"""节点核心（Node Core）。

职责:
- 组装 transport（beacon 发现 + TCP mesh）与 executors（注册表）
- 信封分发（2.16.4 开关管辖；阻塞操作入线程池，不阻塞网络线程）
- 本地能力面 API（供面板/MCP/CLI 调用，能力对等映射 2.5.7）
- 在线检测（beacon 超时 + TCP 探测，2.1.6）
- 任务三模式（sync/async/trigger，2.7）与异步回执邮箱（2.6）
- 通信日志全量落库（2.3）+ node.log（2.9.7）
"""
from __future__ import annotations

import ctypes
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from executors.registry import ExecutorRegistry
from node import a2a
from node.config import NodeConfig
from node.store import Store, utcnow
from transport import protocol as P
from transport.beacon import BeaconService, build_beacon_payload
from transport.mesh import MeshManager

BEACON_ONLINE_SEC = 6.0     # 2.1.6 默认时序参数
PROBE_INTERVAL = 1.0
PROBE_FAILS = 3
MAX_LIST_ENTRIES = 5000
SHELL_DEFAULT_TIMEOUT = 60


def _pid_alive(pid: int) -> bool:
    try:
        if sys.platform == "win32":
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not h:
                return False
            try:
                exit_code = ctypes.c_ulong()
                ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(exit_code))
                return exit_code.value == 259  # STILL_ACTIVE
            finally:
                ctypes.windll.kernel32.CloseHandle(h)
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


class NodeCore:
    def __init__(self, data_dir: Path, panel_port: int | None = None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "inbox").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "sync").mkdir(parents=True, exist_ok=True)
        self.config = NodeConfig(self.data_dir)
        self.node_id = self.config.node_id
        self.store = Store(self.data_dir / "comm.db")
        self._init_logging()
        self.mesh = MeshManager(self, self.node_id,
                                listen_port=self.config.peer_tcp_port)
        self.beacon = BeaconService(
            self.config.discovery_ports(), self.node_id,
            payload_provider=lambda: build_beacon_payload(self),
            on_beacon=self._on_beacon)
        self.registry = ExecutorRegistry(self)
        self.sync = None  # 可选（sync/syncthing.py，延后初始化）
        self.panel_port = panel_port
        self.started_at = time.time()
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="handler")
        self._lock = threading.RLock()
        self._last_beacon: dict[str, float] = {}
        self._probe_fails: dict[str, int] = {}
        self._online: dict[str, bool] = {}
        self._notifications: list[dict] = []
        self._async_pending: dict[str, dict] = {}   # task_id -> {callerId, agentId, nodeId}
        self._remote_results: dict[str, dict] = {}  # 远端推送结果缓存（sync 模式加速）
        self._remote_tasks: dict[str, str] = {}     # task_id -> 远端 node_id
        self._async_retry: list[tuple[str, float]] = []
        self._stopping = False
        self._lock_file = None

    # ---------- 日志（2.9.7 node.log） ----------
    def _init_logging(self) -> None:
        self.log_path = self.data_dir / "node.log"
        self.logger = logging.getLogger(f"agent-node.{self.node_id}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        if not self.logger.handlers:
            fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
            fh = logging.FileHandler(self.log_path, encoding="utf-8")
            fh.setFormatter(fmt)
            self.logger.addHandler(fh)
            sh = logging.StreamHandler()
            sh.setFormatter(fmt)
            self.logger.addHandler(sh)

    def log(self, level: str, msg: str) -> None:
        getattr(self.logger, level, self.logger.info)(msg)

    # ---------- 启停 ----------
    def start(self) -> None:
        self._acquire_single_instance()
        self.mesh.start()
        self.registry.load_plugins()
        self.registry.start_poller()
        self.beacon.start()
        threading.Thread(target=self._online_monitor, daemon=True,
                         name="online-monitor").start()
        threading.Thread(target=self._manual_peer_loop, daemon=True,
                         name="manual-peers").start()
        threading.Thread(target=self._async_retry_loop, daemon=True,
                         name="async-retry").start()
        # 同步引擎（可选，缺失二进制则降级跳过）
        try:
            if self.config.sync_enabled:
                from sync.syncthing import SyncManager
                self.sync = SyncManager(self)
                self.sync.start()
        except Exception as e:
            self.log("warning", f"同步引擎未启用: {e}")
            self.sync = None
        self.log("info", f"节点已启动: {self.node_id} name={self.config.name} "
                         f"team={self.config.team_id!r} "
                         f"peer_tcp={self.mesh.my_listen_port} panel={self.panel_port}")

    def stop(self) -> None:
        self._stopping = True
        try:
            if self.sync:
                self.sync.stop()
        except Exception:
            pass
        try:
            self.beacon.stop()
        except Exception:
            pass
        try:
            self.mesh.stop()
        except Exception:
            pass
        try:
            self.registry.stop()
        except Exception:
            pass
        try:
            self.store.close()
        except Exception:
            pass
        # 关闭日志句柄（Windows 下文件锁）
        try:
            for h in list(self.logger.handlers):
                h.close()
                self.logger.removeHandler(h)
        except Exception:
            pass
        self._release_single_instance(clean=True)

    # ---------- 单实例 + 异常退出检测（2.11.3 / 2.12.2） ----------
    def _acquire_single_instance(self) -> None:
        lock_path = self.data_dir / "node.lock"
        if lock_path.exists():
            stale = True
            try:
                old_pid = int(lock_path.read_text().strip() or 0)
                if old_pid and _pid_alive(old_pid):
                    stale = False
                    raise RuntimeError(f"节点已在运行 pid={old_pid}（2.11.3 单实例保护）")
            except ValueError:
                pass
            if stale:
                # 上次异常退出（锁残留），提示人类（2.12.2）
                self.log("warning", "检测到上次节点异常退出（node.lock 残留），"
                                    "请检查 data/node.log 或让本机 AI 读取日志排查")
                self._notify("warning", "上次节点异常退出——请检查 node.log 排查原因")
                self._crash_alert()
                lock_path.unlink(missing_ok=True)
        self._lock_file = open(lock_path, "w", encoding="utf-8")
        self._lock_file.write(str(os.getpid()))
        self._lock_file.flush()

    def _release_single_instance(self, clean: bool = True) -> None:
        try:
            if self._lock_file:
                self._lock_file.close()
            if clean:
                (self.data_dir / "node.lock").unlink(missing_ok=True)
        except Exception:
            pass

    def _crash_alert(self) -> None:
        """崩溃告警（2.12.2）：模态 MessageBox，后台线程 + 超时防阻塞。"""
        if sys.platform != "win32":
            return

        def _alert():
            try:
                ctypes.windll.user32.MessageBoxTimeoutW(
                    0, "上次 agent-node 节点异常退出，请检查 data/node.log 排查原因。",
                    "agent-node", 0x40, 0, 10000)
            except Exception:
                pass

        threading.Thread(target=_alert, daemon=True).start()

    # ---------- 通知 ----------
    def _notify(self, kind: str, text: str) -> None:
        with self._lock:
            self._notifications.append({"ts": utcnow(), "kind": kind, "text": text})
            if len(self._notifications) > 100:
                self._notifications = self._notifications[-100:]

    def notifications(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return list(self._notifications[-limit:])

    # ================= beacon / 在线检测 =================
    def _on_beacon(self, payload: dict, addr) -> None:
        node_id = payload.get("node_id")
        if not node_id or node_id == self.node_id:
            return
        ports = payload.get("ports") or {}
        peer_tcp = int(ports.get("peer_tcp") or 0)
        caps = payload.get("capabilities") or []
        switches = payload.get("switches") or {}
        self.store.upsert_peer(
            node_id, name=payload.get("name"), team_id=payload.get("team_id"),
            capabilities=caps, switches=switches,
            sync_device_id=payload.get("sync_device_id"),
            host=addr[0], peer_tcp_port=peer_tcp)
        with self._lock:
            self._last_beacon[node_id] = time.time()
            self._probe_fails[node_id] = 0
        # 同步引擎自动互配（2.4.7）
        if self.sync and payload.get("sync_device_id"):
            try:
                self._pool.submit(self.sync.pair_peer, payload)
            except Exception:
                pass
        # 同 team 且未连接 → 主动建连（全连接 mesh，2.1.8）
        if self.config.team_matches(payload.get("team_id")) and peer_tcp:
            if not self.mesh.is_online(node_id):
                self._pool.submit(self.mesh.connect_peer, node_id, addr[0], peer_tcp)

    def on_peer_connected(self, node_id: str, conn) -> None:
        with self._lock:
            self._online[node_id] = True
        self.store.upsert_peer(node_id, host=conn.addr[0] if conn.addr else None,
                               peer_tcp_port=conn.peer_peer_tcp)
        self.log("info", f"对端已连接: {node_id} ({conn.addr})")
        self._notify("info", f"节点已连接: {self._peer_display_name(node_id)}")

    def on_peer_disconnected(self, node_id: str) -> None:
        if node_id:
            self.log("info", f"对端连接断开: {node_id}")
            self._notify("warning", f"节点连接断开: {self._peer_display_name(node_id)}")

    def _peer_display_name(self, node_id: str) -> str:
        p = self.store.peer(node_id)
        return f"{p.get('name') or node_id}" if p else node_id

    def is_peer_online(self, node_id: str) -> bool:
        if self.mesh.is_online(node_id):
            return True
        with self._lock:
            ts = self._last_beacon.get(node_id, 0)
        return (time.time() - ts) < BEACON_ONLINE_SEC

    def _online_monitor(self) -> None:
        """2.1.6: beacon 超时 → TCP 探测；3 次失败 → 离线。"""
        while not self._stopping:
            try:
                peers = self.store.peers()
                for p in peers:
                    node_id = p["node_id"]
                    if node_id == self.node_id:
                        continue
                    if self.mesh.is_online(node_id):
                        with self._lock:
                            self._online[node_id] = True
                            self._probe_fails[node_id] = 0
                        continue
                    with self._lock:
                        last = self._last_beacon.get(node_id, 0)
                    if time.time() - last < BEACON_ONLINE_SEC:
                        continue  # beacon 仍在到达
                    # 超时 → TCP 探测
                    addr = self.mesh.peer_addr(node_id) or (
                        (p.get("host"), p.get("peer_tcp_port"))
                        if p.get("host") and p.get("peer_tcp_port") else None)
                    if not addr:
                        continue
                    ok = False
                    try:
                        with socket.create_connection((addr[0], int(addr[1])), timeout=2):
                            ok = True
                    except OSError:
                        ok = False
                    with self._lock:
                        fails = self._probe_fails.get(node_id, 0) + (0 if ok else 1)
                        self._probe_fails[node_id] = fails
                        was = self._online.get(node_id, False)
                        self._online[node_id] = ok or fails < PROBE_FAILS
                        if was and not self._online[node_id]:
                            self.log("info", f"节点判定离线: {node_id}（连续 {PROBE_FAILS} 次探测失败）")
                    time.sleep(PROBE_INTERVAL)
            except Exception:
                traceback.print_exc()
            time.sleep(2)

    def _manual_peer_loop(self) -> None:
        """2.1.10: manual_peers 保活连接。"""
        while not self._stopping:
            try:
                for mp in self.config.manual_peers:
                    host, port = mp.get("host"), int(mp.get("peer_tcp_port") or 0)
                    if host and port:
                        # 未知 node_id，直接按地址连接（握手后注册）
                        if not any(
                                (c.addr and c.addr[0] == host) or False
                                for c in list(self.mesh.connections.values())):
                            self.mesh.connect(host, port)
            except Exception:
                pass
            time.sleep(30)

    # ================= 信封分发（2.16.4） =================
    def handle_envelope(self, env: dict, conn) -> None:
        t = env.get("type")
        sender = env.get("sender_node_id") or "?"
        try:
            self.store.add_comm_log("inbound", sender, t, env.get("correlation_id"),
                                    None, self._summarize(env))
        except Exception:
            pass
        handler = {
            P.T_TEXT_REQUEST: self._on_text_request,
            P.T_SHELL_EXEC: self._on_shell_exec,
            P.T_FILE_PUSH: self._on_file_push,
            P.T_FILE_PULL: self._on_file_pull,
            P.T_FILE_LIST: self._on_file_list,
            P.T_FILE_RESULT: self._on_file_result,
            P.T_PLUGIN_SYNC: self._on_plugin_sync,
            P.T_EXECUTOR_STATUS: self._on_executor_status,
            P.T_A2A_REQUEST: self._on_a2a_request,
            P.T_A2A_RESPONSE: self._on_a2a_response,
            P.T_ERROR: self._on_error,
        }.get(t)
        if handler is None:
            # 未知 type → error 但不关闭连接（A.3 前向兼容）
            try:
                conn.send_env(P.make_envelope(
                    P.T_ERROR, self.node_id, sender,
                    {"code": P.E_UNSUPPORTED_TYPE, "message": f"unsupported type: {t}"},
                    correlation_id=env.get("msg_id")))
            except Exception:
                pass
            return
        # 阻塞类操作入线程池（不阻塞网络线程）
        self._pool.submit(self._safe_handler, handler, env, conn)

    def _safe_handler(self, handler, env, conn) -> None:
        try:
            handler(env, conn)
        except Exception as e:
            self.log("error", f"信封处理异常 type={env.get('type')}: {e}\n"
                              f"{traceback.format_exc()}")
            try:
                conn.send_env(P.make_envelope(
                    P.T_ERROR, self.node_id, env.get("sender_node_id") or "*",
                    {"code": "agent_error", "message": str(e)},
                    correlation_id=env.get("msg_id")))
            except Exception:
                pass

    @staticmethod
    def _summarize(env: dict) -> str:
        try:
            return str(env.get("payload"))[:300]
        except Exception:
            return ""

    # ---------- 聊天（2.8） ----------
    def _on_text_request(self, env: dict, conn) -> None:
        sender = env.get("sender_node_id")
        pl = env.get("payload") or {}
        text = (pl.get("text") or "").strip()
        if not text:
            return
        self.store.add_chat(sender, "in", text, pl.get("session_id"))
        self._notify("chat", f"收到消息 [{self._peer_display_name(sender)}]: {text[:80]}")
        self.log("info", f"收到文本消息 from {sender}: {text[:50]}")

    # ---------- 远程命令（2.16.4 shell_exec，受 allow_shell） ----------
    def _on_shell_exec(self, env: dict, conn) -> None:
        sender = env.get("sender_node_id")
        pl = env.get("payload") or {}
        if not self.config.switches.get("allow_shell"):
            result = {"ok": False, "error": P.E_DISABLED, "output": "",
                      "detail": "目标节点已关闭 allow_shell"}
        else:
            result = self._run_shell(pl.get("command") or "",
                                     float(pl.get("timeout") or SHELL_DEFAULT_TIMEOUT))
        resp = P.make_envelope(P.T_SHELL_RESULT, self.node_id, sender, result,
                               correlation_id=env.get("msg_id"))
        conn.send_env(resp)
        self.store.add_comm_log("outbound", sender, P.T_SHELL_RESULT,
                                env.get("msg_id"), "ok" if result.get("ok") else "error",
                                str(result.get("output", ""))[:300])

    def _run_shell(self, command: str, timeout: float) -> dict:
        if not command.strip():
            return {"ok": False, "error": "agent_error", "output": "空命令"}
        try:
            r = subprocess.run(
                command, shell=True, capture_output=True, timeout=timeout,
                encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
            return {"ok": r.returncode == 0, "output": out.strip(),
                    "exitCode": r.returncode, "error": None if r.returncode == 0 else "agent_error"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": P.E_TIMEOUT, "output": "", "detail": f"命令超时（>{timeout:.0f}s）"}
        except Exception as e:
            return {"ok": False, "error": "agent_error", "output": str(e)}

    # ---------- 文件（2.4，受 allow_file；附件例外走 allow_ai_task，2.13.1） ----------
    def _resolve_target_path(self, target_path: str, purpose: str,
                             task_ref: dict) -> Path:
        p = Path(target_path)
        if p.is_absolute():
            return p
        if purpose == "task_attachment":
            base = self.data_dir / "executor_work" / (task_ref.get("agentId") or "") \
                / (task_ref.get("taskId") or "")
            base.mkdir(parents=True, exist_ok=True)
            return (base / p.name).resolve()
        # 相对路径 → 相对目标节点 data 目录（插件分发 "plugins/x.py" 等）
        return (self.data_dir / p).resolve()

    def _on_file_push(self, env: dict, conn) -> None:
        import base64 as _b64
        sender = env.get("sender_node_id")
        pl = env.get("payload") or {}
        purpose = pl.get("purpose") or "file"
        allowed = (self.config.switches.get("allow_ai_task")
                   if purpose == "task_attachment"
                   else self.config.switches.get("allow_file"))
        if not allowed:
            conn.send_env(P.make_envelope(
                P.T_FILE_ACK, self.node_id, sender,
                {"accepted": False, "error": P.E_DISABLED,
                 "detail": f"目标节点已关闭 {'allow_ai_task' if purpose == 'task_attachment' else 'allow_file'}"},
                correlation_id=env.get("msg_id")))
            return
        size = int(pl.get("size") or 0)
        if size > 4 * 1024 * 1024 * 1024:
            conn.send_env(P.make_envelope(
                P.T_FILE_ACK, self.node_id, sender,
                {"accepted": False, "error": "agent_error", "detail": "文件过大（>4GiB）"},
                correlation_id=env.get("msg_id")))
            return
        try:
            target = self._resolve_target_path(pl.get("targetPath") or "", purpose,
                                               pl.get("taskRef") or {})
            target.parent.mkdir(parents=True, exist_ok=True)
            ftid = _b64.b64decode(pl["ftid"])
            from transport.mesh import FileReceiver
            recv = FileReceiver(self.mesh, ftid, target, size,
                                pl.get("sha256") or "", correlation_id=env.get("msg_id"),
                                reply_conn=conn)
            self.mesh.register_receiver(ftid, recv)
            conn.send_env(P.make_envelope(
                P.T_FILE_ACK, self.node_id, sender,
                {"accepted": True}, correlation_id=env.get("msg_id")))
            self.log("info", f"接收文件 from {sender}: {target} ({size}B purpose={purpose})")
        except Exception as e:
            conn.send_env(P.make_envelope(
                P.T_FILE_ACK, self.node_id, sender,
                {"accepted": False, "error": "agent_error", "detail": str(e)},
                correlation_id=env.get("msg_id")))

    def _on_file_pull(self, env: dict, conn) -> None:
        import base64 as _b64
        import uuid as _uuid
        sender = env.get("sender_node_id")
        pl = env.get("payload") or {}
        if not self.config.switches.get("allow_file"):
            conn.send_env(P.make_envelope(
                P.T_FILE_ACK, self.node_id, sender,
                {"accepted": False, "error": P.E_DISABLED, "detail": "目标节点已关闭 allow_file"},
                correlation_id=env.get("msg_id")))
            return
        path = Path(pl.get("path") or "")
        if not path.is_absolute():
            path = (self.data_dir / path).resolve()
        if not path.is_file():
            conn.send_env(P.make_envelope(
                P.T_FILE_ACK, self.node_id, sender,
                {"accepted": False, "error": P.E_OFFLINE, "detail": f"文件不存在: {path}"},
                correlation_id=env.get("msg_id")))
            return
        size = path.stat().st_size
        sha = P.sha256_file(path)
        ftid = _uuid.uuid4().bytes
        conn.send_env(P.make_envelope(
            P.T_FILE_ACK, self.node_id, sender,
            {"accepted": True, "size": size, "sha256": sha,
             "ftid": _b64.b64encode(ftid).decode()},
            correlation_id=env.get("msg_id")))
        # 反向推送块帧（A.4 pull）
        def _stream():
            ok = self.mesh.stream_out_file(conn, ftid, path)
            self.log("info", f"pull 推送文件给 {sender}: {path} ({size}B ok={ok})")
        threading.Thread(target=_stream, daemon=True).start()

    def _on_file_result(self, env: dict, conn) -> None:
        # file_result 经 pending 机制匹配；无 pending（如 pull 响应方收到的确认）→ 记日志
        self.log("info", f"file_result: {env.get('payload')}")

    def _on_file_list(self, env: dict, conn) -> None:
        sender = env.get("sender_node_id")
        pl = env.get("payload") or {}
        if not self.config.switches.get("allow_file"):
            result = {"ok": False, "error": P.E_DISABLED, "entries": [],
                      "detail": "目标节点已关闭 allow_file"}
        else:
            result = self._list_dir_local(pl.get("path") or "",
                                          bool(pl.get("recursive")))
        conn.send_env(P.make_envelope(P.T_FILE_LIST_RESULT, self.node_id, sender,
                                      result, correlation_id=env.get("msg_id")))

    def _list_dir_local(self, path: str, recursive: bool = False) -> dict:
        p = Path(path or ("C:\\" if sys.platform == "win32" else "/"))
        if not p.is_absolute():
            p = (self.data_dir / p).resolve()
        if not p.exists():
            return {"ok": False, "error": "agent_error", "entries": [],
                    "detail": f"目录不存在: {p}"}
        entries = []
        try:
            if recursive:
                count = 0
                for root, dirs, files in os.walk(p):
                    for d in dirs:
                        entries.append({"name": d, "path": str(Path(root) / d),
                                        "isDir": True, "size": 0})
                        count += 1
                    for f in files:
                        fp = Path(root) / f
                        try:
                            entries.append({"name": f, "path": str(fp), "isDir": False,
                                            "size": fp.stat().st_size})
                        except OSError:
                            pass
                        count += 1
                    if count >= MAX_LIST_ENTRIES:
                        break
            else:
                for it in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
                    try:
                        entries.append({"name": it.name, "path": str(it),
                                        "isDir": it.is_dir(),
                                        "size": 0 if it.is_dir() else it.stat().st_size})
                    except OSError:
                        pass
        except OSError as e:
            return {"ok": False, "error": "agent_error", "entries": [], "detail": str(e)}
        return {"ok": True, "entries": entries,
                "path": str(p), "isDir": True}

    # ---------- 插件分发重扫（2.2.10，受 allow_file） ----------
    def _on_plugin_sync(self, env: dict, conn) -> None:
        sender = env.get("sender_node_id")
        pl = env.get("payload") or {}
        if not self.config.switches.get("allow_file"):
            result = {"ok": False, "error": P.E_DISABLED, "added": [], "restarted": [],
                      "detail": "目标节点已关闭 allow_file"}
        else:
            result = self.registry.rescan(pl.get("changed_plugins"))
        conn.send_env(P.make_envelope(P.T_PLUGIN_SYNC_RESULT, self.node_id, sender,
                                      result, correlation_id=env.get("msg_id")))

    # ---------- 执行器深态查询（2.16.4，受 allow_ai_task） ----------
    def _on_executor_status(self, env: dict, conn) -> None:
        sender = env.get("sender_node_id")
        pl = env.get("payload") or {}
        if not self.config.switches.get("allow_ai_task"):
            result = {"ok": False, "error": P.E_DISABLED, "executors": [],
                      "detail": "目标节点已关闭 allow_ai_task"}
        else:
            result = {"ok": True, **self.registry.deep_status(pl.get("executorId"))}
        conn.send_env(P.make_envelope(P.T_EXECUTOR_STATUS_RESULT, self.node_id, sender,
                                      result, correlation_id=env.get("msg_id")))

    # ---------- A2A 任务（2.16.3，受 allow_ai_task） ----------
    def _on_a2a_request(self, env: dict, conn) -> None:
        sender = env.get("sender_node_id")
        payload = env.get("payload") or {}
        method = payload.get("method")
        params = payload.get("params") or {}
        req_id = payload.get("id")

        def reply(result: dict, code: int | None = None, message: str | None = None):
            body = (a2a.jsonrpc_error(req_id, code, message) if code
                    else a2a.jsonrpc_result(req_id, result))
            conn.send_env(P.make_envelope(
                P.T_A2A_RESPONSE, self.node_id, sender, body,
                correlation_id=env.get("msg_id")))

        if not self.config.switches.get("allow_ai_task"):
            # 结构化错误码保留在 result 内（调用方按 2.13.5 判读）
            reply({"ok": False, "error": P.E_DISABLED,
                   "detail": "目标节点已关闭 allow_ai_task"})
            return
        if method == "message/send":
            self._pool.submit(self._a2a_message_send, env, conn, params, reply)
            return
        if method == "tasks/get":
            task_id = params.get("taskId")
            info = self.registry.task_info(task_id)
            if info is None:
                reply({"ok": False, "error": P.E_OFFLINE, "detail": "任务不存在"},
                      -32001, "task not found")
            else:
                reply({"ok": True, "task": info})
            return
        reply({"ok": False}, -32601, f"method not found: {method}")

    def _a2a_message_send(self, env: dict, conn, params: dict, reply) -> None:
        task = params.get("task") or {}
        task_id = task.get("id") or a2a.new_task_id()
        agent_id = task.get("agentId") or ""
        message = params.get("message") or {}
        parts = message.get("parts") or []
        prompt = next((p.get("text") for p in parts if p.get("kind") == "text"), "")
        attachments = [p.get("path") for p in parts if p.get("kind") == "data"]
        mode = params.get("mode") or "sync"
        caller_id = params.get("callerId") or "unknown"
        timeout = float(params.get("timeoutSec") or 600)
        work_dir = self.data_dir / "executor_work" / agent_id / task_id
        att_paths = [str(work_dir / Path(a).name) for a in (attachments or [])]
        r = self.registry.submit_local(
            agent_id, task_id, prompt, mode,
            caller_node_id=env.get("sender_node_id"), caller_id=caller_id,
            attachments=att_paths, timeout=timeout)
        # 幂等（2.13.5）: task_id 已存在 → 返回现状
        if not r.ok and r.error == "not_installed":
            existing = self.registry.task_info(task_id)
            if existing:
                r = SubmitResultLike(True, None, "幂等命中：任务已存在")
        reply({"ok": r.ok, "error": r.error, "detail": r.detail, "taskId": task_id})

    def _on_a2a_response(self, env: dict, conn) -> None:
        """异步回执推送（correlation_id = task_id，A.5）与响应匹配兜底。"""
        payload = env.get("payload") or {}
        result = payload.get("result") or {}
        task_id = env.get("correlation_id")
        if result.get("kind") == "task_result" and task_id:
            task = result.get("task") or {}
            with self._lock:
                self._remote_results[task_id] = task
                pending = self._async_pending.pop(task_id, None)
            if pending:
                content = self._extract_task_content(task)
                self.store.add_inbox(
                    pending.get("callerId") or "unknown",
                    env.get("sender_node_id"), task_id, "task_result",
                    {"ok": task.get("status", {}).get("state") == "completed",
                     "taskId": task_id, "agentId": pending.get("agentId"),
                     "state": task.get("status", {}).get("state"),
                     "error": (task.get("metadata") or {}).get("error"),
                     "content": content})
                self._notify("receipt", f"异步回执到达: 任务 {task_id[:8]} "
                                        f"[{pending.get('agentId')}]")
                self.log("info", f"异步回执入邮箱: {task_id[:8]} caller={pending.get('callerId')}")

    def _on_error(self, env: dict, conn) -> None:
        self.log("warning", f"收到对端 error: {env.get('payload')}")

    # ================= 本地能力面 API（面板/MCP/CLI 共用） =================
    # ---------- 概览（2.9.3） ----------
    def overview(self) -> dict:
        local_execs = self.registry.deep_status()["executors"]
        busy = any(e.get("state") == "busy" for e in local_execs)
        return {
            "nodeId": self.node_id,
            "name": self.config.name,
            "teamId": self.config.team_id,
            "host": socket.gethostname(),
            "ips": [ip for ip in self._local_ips()],
            "uptimeSec": int(time.time() - self.started_at),
            "discoveryPorts": self.config.discovery_ports(),
            "peerTcpPort": self.mesh.my_listen_port,
            "panelPort": self.panel_port,
            "panelUrl": self.panel_url(),
            "switches": dict(self.config.switches),
            "busy": busy,
            "status": "busy" if busy else "running",
            "executors": local_execs,
            "notifications": self.notifications(),
            "sync": {"enabled": bool(self.sync),
                     "deviceId": self.sync.device_id if self.sync else None},
        }

    @staticmethod
    def _local_ips() -> list[str]:
        ips = []
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
            s.close()
        except Exception:
            pass
        return ips

    def panel_url(self) -> str:
        return f"http://127.0.0.1:{self.panel_port}/" if self.panel_port else ""

    # ---------- 节点列表（2.1.9 离线不消失） ----------
    def list_nodes(self) -> list[dict]:
        out = []
        for p in self.store.peers():
            node_id = p["node_id"]
            out.append({
                "nodeId": node_id,
                "name": p.get("name"),
                "teamId": p.get("team_id"),
                "capabilities": p.get("capabilities") or [],
                "switches": p.get("switches") or {},
                "host": p.get("host"),
                "peerTcpPort": p.get("peer_tcp_port"),
                "online": self.is_peer_online(node_id),
                "connected": self.mesh.is_online(node_id),
                "lastSeen": p.get("last_seen"),
                "firstSeen": p.get("first_seen"),
            })
        return out

    def forget_node(self, node_id: str) -> bool:
        self.store.delete_peer(node_id)
        return True

    # ---------- 聊天 ----------
    def send_chat(self, target_node_id: str, text: str,
                  session_id: str | None = None) -> dict:
        if not self.mesh.is_online(target_node_id):
            # 已知地址则尝试即时建连后发送
            addr = self.mesh.peer_addr(target_node_id)
            peer = self.store.peer(target_node_id)
            if not addr and peer and peer.get("host") and peer.get("peer_tcp_port"):
                addr = (peer["host"], int(peer["peer_tcp_port"]))
            if addr:
                self.mesh.connect_peer(target_node_id, addr[0], addr[1])
                deadline = time.time() + 4
                while time.time() < deadline and not self.mesh.is_online(target_node_id):
                    time.sleep(0.2)
        if not self.mesh.is_online(target_node_id):
            return {"ok": False, "error": P.E_OFFLINE, "detail": "目标不在线（2.8.5）"}
        env = P.make_envelope(
            P.T_TEXT_REQUEST, self.node_id, target_node_id,
            {"text": text, "session_id": session_id})
        if not self.mesh.send_to(target_node_id, env):
            return {"ok": False, "error": P.E_OFFLINE, "detail": "目标不在线（发送失败）"}
        self.store.add_chat(target_node_id, "out", text, session_id)
        self.store.add_comm_log("outbound", target_node_id, P.T_TEXT_REQUEST,
                                None, "ok", text[:300])
        return {"ok": True}

    def chat_history(self, peer_node_id: str, limit: int = 200) -> list[dict]:
        return self.store.chat_history(peer_node_id, limit)

    def conversations(self) -> list[dict]:
        return self.store.conversations()

    # ---------- 文件 ----------
    def list_dir(self, node_id: str | None, path: str, recursive: bool = False) -> dict:
        if not node_id or node_id == self.node_id:
            return self._list_dir_local(path, recursive)
        env = P.make_envelope(P.T_FILE_LIST, self.node_id, node_id,
                              {"path": path, "recursive": recursive})
        resp = self.mesh.call(node_id, env, timeout=30)
        if resp is None:
            return {"ok": False, "error": P.E_OFFLINE, "entries": [], "detail": "目标不在线"}
        return resp.get("payload") or {}

    def file_push(self, node_id: str | None, local_path: str, target_path: str | None = None,
                  purpose: str = "file", task_ref: dict | None = None) -> dict:
        lp = Path(local_path)
        if not lp.is_file():
            return {"ok": False, "error": "agent_error", "detail": f"本地文件不存在: {lp}"}
        if not node_id or node_id == self.node_id:
            # 本机推送：直接落目标路径（默认统一收件目录，2.4.2）
            dest = Path(target_path) if target_path else (self.config.inbox_dir() / lp.name)
            if not dest.is_absolute():
                dest = (self.data_dir / dest).resolve()
            dest.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(lp, dest)
            self.store.add_comm_log("local", self.node_id, "file_push", None, "ok",
                                    f"{lp} -> {dest}")
            return {"ok": True, "detail": str(dest)}
        if not target_path:
            target_path = f"inbox/{lp.name}"
        result = self.mesh.push_file(node_id, lp, target_path, purpose=purpose,
                                     task_ref=task_ref)
        self.store.add_comm_log("outbound", node_id, "file_push", None,
                                "ok" if result.get("ok") else (result.get("error") or "error"),
                                f"{lp} -> {target_path}")
        return result

    def file_pull(self, node_id: str | None, remote_path: str,
                  dest_dir: str | None = None) -> dict:
        dest_root = Path(dest_dir) if dest_dir else self.config.inbox_dir()
        dest_root.mkdir(parents=True, exist_ok=True)
        if not node_id or node_id == self.node_id:
            src = Path(remote_path)
            if not src.is_absolute():
                src = (self.data_dir / src).resolve()
            if not src.is_file():
                return {"ok": False, "error": "agent_error", "detail": f"文件不存在: {src}"}
            import shutil
            dest = dest_root / src.name
            shutil.copy2(src, dest)
            return {"ok": True, "path": str(dest)}
        # 先按单文件拉取；不存在再按目录整树递归拉取（2.4.6 不经打包）
        single = self.mesh.pull_file(node_id, remote_path, dest_root / Path(remote_path).name)
        if single.get("ok"):
            self.store.add_comm_log("outbound", node_id, "file_pull", None, "ok",
                                    remote_path)
            return single
        if "不存在" not in (single.get("detail") or ""):
            return single
        # 目录整树递归拉取（2.4.6 不经打包）
        listing = self.list_dir(node_id, remote_path, recursive=True)
        if not listing.get("ok"):
            return {"ok": False, "error": listing.get("error") or P.E_OFFLINE,
                    "detail": listing.get("detail")}
        files = [e for e in listing.get("entries", []) if not e.get("isDir")]
        root = listing.get("path") or remote_path
        succeeded, failed = 0, []
        for f in files:
            rel = Path(f["path"]).relative_to(root) if f["path"].startswith(str(root)) \
                else Path(f["name"])
            dest = dest_root / Path(remote_path).name / rel
            r = self.mesh.pull_file(node_id, f["path"], dest)
            if r.get("ok"):
                succeeded += 1
            else:
                failed.append({"path": f["path"], "error": r.get("error")})
        self.store.add_comm_log("outbound", node_id, "file_pull", None,
                                "ok" if not failed else "error",
                                f"{remote_path} files={succeeded} failed={len(failed)}")
        return {"ok": not failed, "files": succeeded, "failed": failed,
                "detail": f"已拉取 {succeeded} 个文件到 {dest_root}"}

    # ---------- shell ----------
    def shell_exec(self, target_node_id: str | None, command: str,
                   timeout: float = SHELL_DEFAULT_TIMEOUT) -> dict:
        if not target_node_id or target_node_id == self.node_id:
            # 本机直接执行（本机 AI 全权，不受开关，2.5.9 ⑧）
            result = self._run_shell(command, timeout)
            self.store.add_comm_log("local", self.node_id, "shell_exec", None,
                                    "ok" if result.get("ok") else "error", command[:300])
            return result
        env = P.make_envelope(P.T_SHELL_EXEC, self.node_id, target_node_id,
                              {"command": command, "timeout": timeout})
        resp = self.mesh.call(target_node_id, env, timeout=timeout + 15)
        if resp is None:
            return {"ok": False, "error": P.E_OFFLINE, "output": "", "detail": "目标不在线"}
        self.store.add_comm_log("outbound", target_node_id, "shell_exec", None,
                                "ok" if (resp.get("payload") or {}).get("ok") else "error",
                                command[:300])
        return resp.get("payload") or {}

    # ---------- 执行器（2.5.9 ⑤） ----------
    def list_executors(self) -> list[dict]:
        out = []
        for e in self.registry.deep_status()["executors"]:
            agent_id = e["agentId"]
            entry = self.registry.entries.get(agent_id, {})
            out.append({
                "executorId": f"{self.node_id}/{agent_id}",
                "nodeId": self.node_id,
                "agentId": agent_id,
                "name": e.get("name") or entry.get("name"),
                "executorType": e.get("executorType"),
                "concurrency": e.get("concurrency"),
                "online": True, "local": True,
                "status": e,
                "premises": entry.get("premises") or [],
                "sessionPolicy": "fresh" if e.get("executorType") == "non_interactive_cli" else "session",
            })
        for p in self.store.peers():
            node_id = p["node_id"]
            online = self.is_peer_online(node_id)
            for cap in p.get("capabilities") or []:
                out.append({
                    "executorId": f"{node_id}/{cap.get('agentId')}",
                    "nodeId": node_id,
                    "agentId": cap.get("agentId"),
                    "name": cap.get("name"),
                    "executorType": cap.get("executorType"),
                    "concurrency": cap.get("concurrency"),
                    "online": online, "local": False,
                    "premises": [],
                    "sessionPolicy": "fresh" if cap.get("executorType") == "non_interactive_cli" else "session",
                })
        return out

    def get_executor_status(self, executor_id: str) -> dict:
        node_id, agent_id = self._parse_executor_id(executor_id)
        if node_id == self.node_id:
            st = self.registry.status(agent_id)
            if st is None:
                return {"ok": False, "error": "not_installed", "detail": "执行器不存在"}
            return {"ok": True, "status": st}
        env = P.make_envelope(P.T_EXECUTOR_STATUS, self.node_id, node_id,
                              {"executorId": agent_id})
        resp = self.mesh.call(node_id, env, timeout=15)
        if resp is None:
            return {"ok": False, "error": P.E_OFFLINE, "detail": "目标不在线"}
        return resp.get("payload") or {}

    def _parse_executor_id(self, executor_id: str) -> tuple[str, str]:
        if "/" in executor_id:
            node_id, agent_id = executor_id.split("/", 1)
            return node_id, agent_id
        return self.node_id, executor_id

    # ---------- 任务提交（2.7 三模式） ----------
    def submit_task(self, executor_id: str, prompt: str, mode: str = "sync",
                    attachments: list[str] | None = None, timeout: float = 600.0,
                    task_id: str | None = None, caller_id: str = "panel") -> dict:
        if mode not in ("sync", "async", "trigger"):
            return {"ok": False, "error": "agent_error", "detail": f"未知 mode: {mode}"}
        node_id, agent_id = self._parse_executor_id(executor_id)
        task_id = task_id or a2a.new_task_id()
        att_names = [Path(a).name for a in (attachments or [])]
        if node_id == self.node_id:
            r = self.registry.submit_local(
                agent_id, task_id, prompt, mode, caller_node_id=None,
                caller_id=caller_id, attachments=attachments, timeout=timeout)
            result = {"ok": r.ok, "error": r.error, "detail": r.detail,
                      "taskId": task_id, "mode": mode, "local": True}
            if r.ok and mode == "sync":
                info = self.registry.wait_task(task_id, timeout=timeout + 30)
                result["task"] = info
                result["content"] = self._extract_task_content(info) if info else None
                result["ok"] = bool(info and info.get("status", {}).get("state") == "completed")
            return result
        # ---- 远程 ----
        if not self.mesh.is_online(node_id) and not self.is_peer_online(node_id):
            return {"ok": False, "error": P.E_OFFLINE, "detail": "目标不在线"}
        # 附件先行（2.13.1: 附件直传到任务工作目录，受 allow_ai_task）
        for a, name in zip(attachments or [], att_names):
            r = self.mesh.push_file(
                node_id, Path(a), f"executor_work/{agent_id}/{task_id}/{name}",
                purpose="task_attachment", task_ref={"agentId": agent_id, "taskId": task_id})
            if not r.get("ok"):
                return {"ok": False, "error": r.get("error") or P.E_AGENT_ERROR,
                        "detail": f"附件直传失败: {r.get('detail')}"}
        params = {
            "task": {"id": task_id, "agentId": agent_id},
            "message": a2a.make_user_message(prompt, att_names),
            "mode": mode, "callerId": caller_id, "callerNodeId": self.node_id,
            "timeoutSec": timeout,
        }
        env = P.make_envelope(
            P.T_A2A_REQUEST, self.node_id, node_id,
            a2a.jsonrpc_request("message/send", params), correlation_id=task_id)
        resp = self.mesh.call(node_id, env, timeout=25)
        if resp is None:
            return {"ok": False, "error": P.E_OFFLINE, "detail": "目标不在线或未应答"}
        body = (resp.get("payload") or {})
        rpc_result = body.get("result") or {}
        rpc_error = body.get("error")
        if rpc_error or not rpc_result.get("ok"):
            err = (rpc_result.get("error")
                   or (rpc_error or {}).get("message") or P.E_AGENT_ERROR)
            detail = rpc_result.get("detail") or str(rpc_error)
            self.store.add_comm_log("outbound", node_id, "task_submit", task_id, err, detail)
            return {"ok": False, "error": err, "detail": detail, "taskId": task_id}
        with self._lock:
            self._remote_tasks[task_id] = node_id
        self.store.add_comm_log("outbound", node_id, "task_submit", task_id, "ok",
                                f"executor={agent_id} mode={mode}")
        result = {"ok": True, "taskId": task_id, "mode": mode, "local": False}
        if mode == "trigger":
            result["triggered"] = True
            return result
        if mode == "async":
            with self._lock:
                self._async_pending[task_id] = {"callerId": caller_id,
                                                "agentId": agent_id, "nodeId": node_id}
            return result
        # sync: 轮询 tasks/get（或等远端推送缓存）
        info = self._wait_remote_task(node_id, task_id, timeout)
        result["task"] = info
        result["content"] = self._extract_task_content(info) if info else None
        result["ok"] = bool(info and info.get("status", {}).get("state") == "completed")
        return result

    def _wait_remote_task(self, node_id: str, task_id: str, timeout: float) -> dict | None:
        deadline = time.time() + timeout + 30
        while time.time() < deadline:
            with self._lock:
                cached = self._remote_results.get(task_id)
            if cached:
                return cached
            env = P.make_envelope(
                P.T_A2A_REQUEST, self.node_id, node_id,
                a2a.jsonrpc_request("tasks/get", {"taskId": task_id}),
                correlation_id=task_id)
            resp = self.mesh.call(node_id, env, timeout=15)
            if resp:
                rpc_result = (resp.get("payload") or {}).get("result") or {}
                task = rpc_result.get("task")
                if task:
                    state = (task.get("status") or {}).get("state")
                    if state in (a2a.TASK_COMPLETED, a2a.TASK_FAILED):
                        return task
            time.sleep(2)
        return {"id": task_id, "status": {"state": a2a.TASK_FAILED,
                                          "message": "等待远端结果超时"},
                "metadata": {"error": P.E_TIMEOUT}}

    @staticmethod
    def _extract_task_content(task: dict | None) -> str | None:
        if not task:
            return None
        arts = task.get("artifacts") or []
        for art in arts:
            for part in art.get("parts") or []:
                if part.get("kind") == "text":
                    return part.get("text")
        return None

    def get_task_result(self, task_id: str) -> dict:
        info = self.registry.task_info(task_id)
        if info:
            return {"ok": True, "task": info, "content": self._extract_task_content(info),
                    "local": True}
        with self._lock:
            node_id = self._remote_tasks.get(task_id)
            cached = self._remote_results.get(task_id)
        if cached:
            return {"ok": True, "task": cached,
                    "content": self._extract_task_content(cached), "local": False}
        if not node_id:
            return {"ok": False, "error": "offline", "detail": "任务不存在"}
        env = P.make_envelope(
            P.T_A2A_REQUEST, self.node_id, node_id,
            a2a.jsonrpc_request("tasks/get", {"taskId": task_id}),
            correlation_id=task_id)
        resp = self.mesh.call(node_id, env, timeout=15)
        if resp is None:
            return {"ok": False, "error": P.E_OFFLINE, "detail": "目标不在线"}
        rpc_result = (resp.get("payload") or {}).get("result") or {}
        task = rpc_result.get("task")
        if not task:
            return {"ok": False, "error": "offline", "detail": "任务不存在"}
        return {"ok": True, "task": task, "content": self._extract_task_content(task),
                "local": False}

    # ---------- 异步回执推送/重试 ----------
    def store_local_async_result(self, record) -> None:
        """本机异步任务完成 → 回执直接进调用者邮箱（2.6.5 caller 归属）。"""
        ok = record.state == a2a.TASK_COMPLETED
        self.store.add_inbox(
            record.caller_id or "unknown", None, record.task_id, "task_result",
            {"ok": ok, "taskId": record.task_id, "agentId": record.agent_id,
             "state": record.state, "error": record.error, "content": record.content})
        self._notify("receipt", f"异步回执到达: 任务 {record.task_id[:8]} "
                                f"[{record.agent_id}]")
        self.log("info", f"本机异步回执入邮箱: {record.task_id[:8]} "
                         f"caller={record.caller_id}")

    def push_async_result(self, record) -> None:
        payload = {"jsonrpc": "2.0", "id": record.task_id,
                   "result": {"kind": "task_result", "task": record.as_dict()}}
        env = P.make_envelope(P.T_A2A_RESPONSE, self.node_id, record.caller_node_id,
                              payload, correlation_id=record.task_id)
        if not self.mesh.send_to(record.caller_node_id, env):
            with self._lock:
                self._async_retry.append((record.task_id, time.time() + 600))
            self.log("warning", f"异步回执推送失败（入重试队列）: {record.task_id[:8]}")

    def _async_retry_loop(self) -> None:
        while not self._stopping:
            time.sleep(20)
            with self._lock:
                due = [(tid, exp) for tid, exp in self._async_retry if time.time() >= exp]
                self._async_retry = [(t, e) for t, e in self._async_retry
                                     if (t, e) not in due]
            for task_id, _ in due:
                rec = self.registry.tasks.get(task_id)
                if rec and rec.caller_node_id:
                    self.push_async_result(rec)

    # ---------- inbox ----------
    def check_inbox(self, caller_id: str) -> list[dict]:
        return self.store.fetch_inbox(caller_id)

    def cleanup_inbox(self, mode: str, before: str | None = None) -> dict:
        n = self.store.cleanup_inbox(mode, before)
        return {"ok": True, "deleted": n}

    # ---------- 设置类（本机全权，远程不可改） ----------
    def rename_node(self, new_name: str) -> dict:
        self.config.name = new_name
        self.config.save()
        return {"ok": True}

    def set_team(self, team_id: str) -> dict:
        old_team = self.config.team_id
        self.config.team_id = team_id
        self.config.save()
        # 2.1.7 切换即断开旧 team 连接
        closed = []
        with self.mesh._conn_lock:
            conns = list(self.mesh.connections.items())
        for pid, conn in conns:
            if not self.config.team_matches(conn.peer_team):
                conn.close("team switch")
                closed.append(pid)
        # 同步引擎联动：移除旧 team 设备共享（2.1.7）
        if self.sync and old_team != team_id:
            try:
                self._pool.submit(self.sync.reteam, team_id)
            except Exception:
                pass
        self.log("info", f"team 切换: {old_team!r} -> {team_id!r} 断开 {closed}")
        return {"ok": True, "disconnected": closed}

    def set_switch(self, name: str, enabled: bool) -> dict:
        self.config.set_switch(name, enabled)
        self.config.save()
        return {"ok": True}

    def add_manual_peer(self, host: str, peer_tcp_port: int) -> dict:
        mp = {"host": host, "peer_tcp_port": int(peer_tcp_port)}
        if mp not in self.config.manual_peers:
            self.config.manual_peers.append(mp)
            self.config.save()
        conn = self.mesh.connect(host, int(peer_tcp_port))
        return {"ok": conn is not None, "detail": "已发起连接（握手结果见节点列表）"}

    def remove_manual_peer(self, host: str) -> dict:
        self.config.manual_peers = [m for m in self.config.manual_peers
                                    if m.get("host") != host]
        self.config.save()
        return {"ok": True}

    # ---------- 日志查询 ----------
    def comm_log(self, **filters) -> list[dict]:
        return self.store.query_comm_log(**filters)

    def node_log(self, lines: int = 200) -> str:
        try:
            content = self.log_path.read_text(encoding="utf-8", errors="replace")
            return "\n".join(content.splitlines()[-int(lines):])
        except Exception:
            return ""

    # ---------- 插件分发（2.2.10 / 2.5.9 ④） ----------
    def distribute_plugin(self, target_node_id: str, plugin_path: str) -> dict:
        p = Path(plugin_path)
        if not p.is_file():
            return {"ok": False, "error": "agent_error", "detail": f"插件文件不存在: {p}"}
        r = self.file_push(target_node_id, str(p), f"plugins/{p.name}")
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error"), "detail": r.get("detail")}
        env = P.make_envelope(P.T_PLUGIN_SYNC, self.node_id, target_node_id,
                              {"changed_plugins": [p.stem]})
        resp = self.mesh.call(target_node_id, env, timeout=30)
        if resp is None:
            return {"ok": False, "error": P.E_TIMEOUT,
                    "detail": "文件已送达但 plugin_sync 未应答"}
        return resp.get("payload") or {}

    def list_plugins(self) -> list[dict]:
        out = []
        if self.registry.plugins_dir.is_dir():
            for py in sorted(self.registry.plugins_dir.glob("*")):
                if py.suffix in (".py",):
                    out.append({"file": py.name, "path": str(py),
                                "size": py.stat().st_size})
        return out

    # ---------- 同步 ----------
    def sync_now(self) -> dict:
        if not self.sync:
            return {"ok": False, "error": "not_installed",
                    "detail": "同步引擎未启用（未装 Syncthing 或已关闭）"}
        return self.sync.sync_now()

    # ---------- 执行器操作 ----------
    def set_executor_suspend(self, executor_id: str, suspend: bool,
                             reason: str | None = None,
                             until: str | None = None) -> dict:
        node_id, agent_id = self._parse_executor_id(executor_id)
        if node_id != self.node_id:
            return {"ok": False, "error": P.E_DISABLED,
                    "detail": "仅本机执行器可挂起（远程不可改，4.3）"}
        ok = self.registry.set_suspend(agent_id, suspend, reason, until)
        return {"ok": ok}

    def restart_plugin(self, executor_id: str) -> dict:
        node_id, agent_id = self._parse_executor_id(executor_id)
        if node_id != self.node_id:
            return {"ok": False, "error": P.E_DISABLED, "detail": "仅本机执行器可重启"}
        return {"ok": self.registry.restart_plugin(agent_id)}

    def rename_executor(self, executor_id: str, new_name: str) -> dict:
        node_id, agent_id = self._parse_executor_id(executor_id)
        if node_id != self.node_id:
            return {"ok": False, "error": P.E_DISABLED, "detail": "仅本机执行器可改名"}
        return {"ok": self.registry.rename_executor(agent_id, new_name)}

    def list_agent_sessions(self, executor_id: str | None = None) -> dict:
        node_id, agent_id = self._parse_executor_id(executor_id or "")
        if node_id == self.node_id or not executor_id:
            return {"ok": True, "sessions": []}  # v1: 交互式会话列表暂空（2.2.16）
        env = P.make_envelope(P.T_EXECUTOR_STATUS, self.node_id, node_id,
                              {"executorId": agent_id, "sessions": True})
        resp = self.mesh.call(node_id, env, timeout=15)
        if resp is None:
            return {"ok": False, "error": P.E_OFFLINE, "detail": "目标不在线"}
        return {"ok": True, "sessions": (resp.get("payload") or {}).get("sessions", [])}

    # ---------- 诊断（2.17.7 cli diag 同源） ----------
    def diag(self) -> dict:
        checks = []
        checks.append({"item": "本地配置可读", "ok": bool(self.config.node_id)})
        checks.append({"item": "comm.db 可写",
                       "ok": bool(self.store.query_comm_log(limit=1) is not None)})
        checks.append({"item": "业务 TCP 监听",
                       "ok": bool(self.mesh.my_listen_port),
                       "detail": f"port={self.mesh.my_listen_port}"})
        checks.append({"item": "发现端口监听",
                       "ok": bool(self.config.discovery_ports())})
        peers = self.list_nodes()
        online = [p for p in peers if p["online"]]
        checks.append({"item": "beacon 收发/已知节点",
                       "ok": True, "detail": f"known={len(peers)} online={len(online)}"})
        execs = self.registry.deep_status()["executors"]
        checks.append({"item": "插件加载",
                       "ok": bool(execs),
                       "detail": ", ".join(f"{e['agentId']}({'ok' if e.get('available') else 'unavailable'})"
                                           for e in execs) or "无可用插件"})
        checks.append({"item": "同步引擎",
                       "ok": True if not self.config.sync_enabled else bool(self.sync),
                       "detail": (f"deviceId={self.sync.device_id[:8]}..." if self.sync
                                  else ("未启用（配置关闭）" if not self.config.sync_enabled
                                        else "未启用（缺二进制）"))})
        return {"ok": all(c["ok"] for c in checks), "checks": checks,
                "nodeId": self.node_id, "ts": utcnow()}


class SubmitResultLike:
    """轻量结果对象（幂等命中场景）。"""

    def __init__(self, ok, error=None, detail=None):
        self.ok = ok
        self.error = error
        self.detail = detail
