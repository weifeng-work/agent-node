"""TCP mesh（2.1.8 全连接 + 附录 A）。

- 每对互通节点一条长连接；断线自动重连（2.13.3）
- 首帧 ping 身份交换 + team 隔离在连接层强制（A.1 / 2.1.7）
- 去双连接：保留"较小 node_id 发起"的连接（A.1）
- ping/pong 保活（A.7，5s 间隔、3 次未回应判断）
- 请求-响应：correlation_id = 请求 msg_id（A.3）
- 文件分帧传输（A.4）：块帧与控制帧同连接交错，多路复用按 file_transfer_id 区分
"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import threading
import time
import uuid
from pathlib import Path

from . import protocol as P

KEEPALIVE_INTERVAL = 5.0
KEEPALIVE_MISS = 3


class _Pending:
    __slots__ = ("event", "env")

    def __init__(self):
        self.event = threading.Event()
        self.env: dict | None = None


class FileReceiver:
    """入站文件接收器：按块落盘，完成时校验大小 + SHA-256（2.17.6）并回 file_result。"""

    def __init__(self, mesh: "MeshManager", ftid: bytes, path: Path, size: int,
                 sha256: str, correlation_id: str, reply_conn: "Connection"):
        self.mesh = mesh
        self.ftid = ftid
        self.path = Path(path)
        self.size = int(size)
        self.sha256 = sha256
        self.correlation_id = correlation_id
        self.reply_conn = reply_conn
        self.received = 0
        self.expected_seq = 0
        self.done = threading.Event()
        self.ok = False
        self.error: str | None = None
        self._io_lock = threading.Lock()   # feed/_finish 互斥（多线程收尾竞态）
        self._fh = open(self.path, "wb")

    def feed(self, seq: int, data: bytes) -> None:
        with self._io_lock:
            if self.done.is_set():
                return
            if seq != self.expected_seq:
                self._finish_locked(
                    False, f"block seq out of order: {seq} != {self.expected_seq}")
                return
            self.expected_seq += 1
            try:
                self._fh.write(data)
            except OSError as e:
                self._finish_locked(False, f"write failed: {e}")
                return
            self.received += len(data)
            if self.received >= self.size:
                try:
                    self._fh.close()
                except Exception:
                    pass
                if self.path.stat().st_size != self.size:
                    self._cleanup_partial()
                    self._finish_locked(False, "size mismatch")
                    return
                actual = P.sha256_file(self.path)
                if actual != self.sha256:
                    self._cleanup_partial()
                    self._finish_locked(False, "sha256 mismatch")
                    return
                self._finish_locked(True, None)

    def _cleanup_partial(self) -> None:
        # 半写清理（2.17.5：要么完整要么不落盘）
        try:
            self._fh.close()
        except Exception:
            pass
        try:
            self.path.unlink()
        except OSError:
            pass

    def _finish_locked(self, ok: bool, error: str | None) -> None:
        """须在 _io_lock 内调用。"""
        self.ok = ok
        self.error = error
        try:
            self._fh.close()
        except Exception:
            pass
        if not ok:
            # 半写清理（2.17.5: 要么完整要么不落盘）
            try:
                self.path.unlink()
            except OSError:
                pass
        self.done.set()
        self.mesh.remove_receiver(self.ftid)
        try:
            env = P.make_envelope(
                P.T_FILE_RESULT, self.mesh.my_node_id, self.reply_conn.peer_node_id or "*",
                {"ok": ok, "error": error}, correlation_id=self.correlation_id)
            self.reply_conn.send_env(env)
        except Exception:
            pass

    def _finish(self, ok: bool, error: str | None) -> None:
        with self._io_lock:
            if self.done.is_set():
                return
            self._finish_locked(ok, error)


class Connection:
    """单条对等长连接：读线程 + 保活线程 + 发送锁。"""

    def __init__(self, mesh: "MeshManager", sock: socket.socket, addr,
                 initiated_by_me: bool):
        self.mesh = mesh
        self.sock = sock
        self.addr = addr
        self.initiated_by_me = initiated_by_me
        self.peer_node_id: str | None = None
        self.peer_team: str | None = None
        self.peer_name: str | None = None
        self.peer_peer_tcp: int | None = None
        self.handshake_done = False
        self.alive = True
        self.created_at = time.time()
        self.last_seen = time.time()
        self._send_lock = threading.Lock()
        self._pending_ponges: set[str] = set()
        self._pong_lock = threading.Lock()
        self._closed_by = None
        self._out_streams = 0            # 在途出站文件流计数（去双连接排空用）
        self._ops_lock = threading.Lock()
        self.retired = False             # 被去双连接淘汰（关闭不算断线）

    def start(self) -> None:
        threading.Thread(target=self._reader, daemon=True,
                         name=f"conn-reader-{self.addr}").start()
        threading.Thread(target=self._keepalive, daemon=True,
                         name=f"conn-keepalive-{self.addr}").start()
        # A.1: 连接建立后立即发送首帧 ping（身份交换）
        try:
            self.send_env(P.make_envelope(
                P.T_PING, self.mesh.my_node_id, "*",
                {"hello": True, "team_id": self.mesh.my_team, "name": self.mesh.my_name,
                 "peer_tcp": self.mesh.my_listen_port}))
        except Exception:
            self.close("hello send failed")

    def send_env(self, env: dict) -> None:
        data = P.envelope_to_bytes(env)
        with self._send_lock:
            P.send_frame(self.sock, P.FRAME_JSON, data)

    def send_block(self, ftid: bytes, seq: int, data: bytes) -> None:
        with self._send_lock:
            P.send_frame(self.sock, P.FRAME_FILE, P.pack_file_block(ftid, seq, data))

    def close(self, reason: str = "closed") -> None:
        if not self.alive:
            return
        self.alive = False
        self._closed_by = reason
        try:
            self.sock.close()
        except Exception:
            pass

    def add_stream(self) -> None:
        with self._ops_lock:
            self._out_streams += 1

    def done_stream(self) -> None:
        with self._ops_lock:
            self._out_streams = max(0, self._out_streams - 1)

    def busy_transferring(self) -> bool:
        with self._ops_lock:
            return self._out_streams > 0

    # ---------- 内部 ----------
    def _reader(self) -> None:
        while self.alive:
            try:
                fr = P.read_frame(self.sock)
            except OSError:
                break
            if fr is None:
                break
            ftype, payload = fr
            self.last_seen = time.time()
            if ftype == P.FRAME_FILE:
                self.mesh._on_file_block(self, payload)
                continue
            try:
                env = P.envelope_from_bytes(payload)
            except Exception:
                self.close("bad json frame")
                break
            try:
                self._on_envelope(env)
            except Exception:
                # 严禁吞噬异常（2.17.4）：记录后继续
                self.mesh.node_core.log("error", f"envelope 处理异常 type={env.get('type')}")
        self.close("reader exit")
        self.mesh._on_conn_closed(self)

    def _on_envelope(self, env: dict) -> None:
        t = env.get("type")
        if not self.handshake_done:
            # A.1: 首帧必须是 ping
            if t != P.T_PING:
                self.send_env(P.make_envelope(
                    P.T_ERROR, self.mesh.my_node_id, "*",
                    {"code": P.E_BAD_FRAME, "message": "first frame must be ping"}))
                self.close("first frame not ping")
                return
            self.peer_node_id = env.get("sender_node_id")
            pl = env.get("payload") or {}
            self.peer_team = pl.get("team_id")
            self.peer_name = pl.get("name")
            self.peer_peer_tcp = pl.get("peer_tcp")
            self.mesh.node_core.log(
                "info", f"收到对端首帧: {self.peer_node_id} team={self.peer_team}")
            if not self.mesh.team_matches(self.peer_team):
                # 2.1.7/A.1: team 不一致 → 回 error 并关闭连接
                self.send_env(P.make_envelope(
                    P.T_ERROR, self.mesh.my_node_id, self.peer_node_id or "*",
                    {"code": P.E_DISABLED, "message": "team mismatch",
                     "detail": f"{self.peer_team!r} vs {self.mesh.my_team!r}"},
                    correlation_id=env.get("msg_id")))
                self.close("team mismatch")
                self.mesh.node_core.log(
                    "info", f"team 不一致，拒绝连接: {self.peer_node_id} "
                            f"({self.peer_team} != {self.mesh.my_team})")
                return
            self.handshake_done = True
            self.mesh._register(self)
            self.send_pong(env)
            return
        if t == P.T_PING:
            self.send_pong(env)
            return
        if t == P.T_PONG:
            with self._pong_lock:
                self._pending_ponges.discard(env.get("correlation_id"))
            return
        self.mesh._dispatch(env, self)

    def send_pong(self, ping_env: dict) -> None:
        # pong 的 correlation_id = ping 的 correlation_id（缺省回退 msg_id），
        # 保证发送方 pending_ponges 能匹配（A.7）
        cid = ping_env.get("correlation_id") or ping_env.get("msg_id")
        self.send_env(P.make_envelope(
            P.T_PONG, self.mesh.my_node_id, ping_env.get("sender_node_id") or "*",
            {}, correlation_id=cid))

    def _keepalive(self) -> None:
        while self.alive:
            time.sleep(KEEPALIVE_INTERVAL)
            if not self.alive:
                break
            pid = P.new_msg_id()
            with self._pong_lock:
                self._pending_ponges.add(pid)
                if len(self._pending_ponges) >= KEEPALIVE_MISS:
                    self.close("keepalive missed")
                    break
            try:
                # msg_id=pid: pong 按此回 correlate，与 pending 精确匹配
                self.send_env(P.make_envelope(
                    P.T_PING, self.mesh.my_node_id, self.peer_node_id or "*",
                    {}, correlation_id=pid, msg_id=pid))
            except Exception:
                self.close("keepalive send failed")
                break


class MeshManager:
    def __init__(self, node_core, my_node_id: str, listen_port: int = 0):
        self.node_core = node_core
        self.my_node_id = my_node_id
        self._listen_port_req = int(listen_port or 0)
        self.connections: dict[str, Connection] = {}
        self._conn_lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._pending_lock = threading.Lock()
        self.file_receivers: dict[bytes, FileReceiver] = {}
        self._orphans: dict[bytes, list] = {}   # ftid -> [(seq, data)] 竞态缓冲
        self._recv_lock = threading.Lock()
        self._known_addrs: dict[str, tuple[str, int]] = {}   # node_id -> (host, port)
        self._connecting: set[str] = set()                   # 并发建连去重
        self._server: socket.socket | None = None
        self._announce_sock: socket.socket | None = None
        self._announce_port: int | None = None
        self.my_listen_port: int | None = None
        self._stopping = False
        self._anchors_provider = None              # () -> list[{host, peer_tcp_port}]
        self._scan_cool: dict[str, float] = {}      # 扫描: ip -> 下次可探测时间
        self._scan_lasts: dict[str, float] = {}     # 扫描: ip -> 探测节流时间戳
        self._scan_interval = 12.0
        self._scan_timeout = 1.5
        self._scan_node_range = range(1, 255)   # 测试可注入收敛范围

    # ---- 基本属性（team/name 可变，经回调取当前值） ----
    @property
    def my_team(self) -> str:
        return self.node_core.config.team_id

    @property
    def my_name(self) -> str:
        return self.node_core.config.name

    def team_matches(self, peer_team: str | None) -> bool:
        return (self.my_team or "") == (peer_team or "")

    # ---------- 启动 ----------
    def start(self, host: str = "0.0.0.0") -> None:
        # 端口在 start 时读配置（构造后仍可改 peer_tcp_port）
        # 2.18: 可预测对等端口 —— 显式端口优先；默认(0)从约定段内顺延分配，
        # 段满才随机。保证被隔离节点能靠「出站扫描全子网×段」猜中并连上本机。
        port = self._listen_port_req
        default_range = []
        try:
            cfg_port = int(self.node_core.config.peer_tcp_port or 0)
        except Exception:
            cfg_port = 0
        try:
            default_range = self.node_core.config.peer_port_range()
        except Exception:
            default_range = []
        # 候选顺序：显式端口 → 约定段 → 0(随机)
        candidates = []
        if cfg_port:
            candidates.append(cfg_port)
        candidates += [p for p in default_range if p not in candidates]
        candidates.append(0)

        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        bound = None
        for cport in candidates:
            try:
                self._server.bind((host, cport))
                bound = cport
                break
            except OSError:
                continue
        if bound is None:  # 理论上不会（含 0）
            self._server.bind((host, 0))
        self._server.listen(16)
        self.my_listen_port = self._server.getsockname()[1]
        self.node_core.log("info", f"mesh 监听端口: {self.my_listen_port} (候选 {candidates})")
        threading.Thread(target=self._accept_loop, daemon=True, name="mesh-accept").start()
        self._start_announce_listener(host)

    def _start_announce_listener(self, host: str) -> None:
        """固定「通告」TCP 端（3.x）：只管应答 whoami，让子网扫描先以每 IP 1 端口命中。

        非 mesh 连接：收到连接即回一行 JSON（含真实对等端口），随即关闭；不做全握手，
        因此不参与 mesh 去双连接，扫描方据此拿到真实端口后再拨 mesh。
        """
        try:
            base = self.node_core.config.announce_tcp_port
        except Exception:
            base = 49700
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # 不用 SO_REUSEADDR：Windows 下它会允许同端口双 LISTEN，破坏「被占则顺延」；
        # 通告监听器短生命周期，正常关闭即可。
        bound = None
        for p in range(base, base + 5):  # 端口被占则顺延，避免与蜂窝冲突
            try:
                sock.bind((host, p))
                bound = p
                break
            except OSError:
                continue
        if bound is None:
            sock.close()
            return
        sock.listen(16)
        self._announce_sock = sock
        self._announce_port = bound
        self.node_core.log("info", f"通告 TCP 端口: {bound}")

        def _loop():
            while not self._stopping:
                try:
                    conn, _addr = sock.accept()
                except OSError:
                    break
                try:
                    conn.settimeout(2.0)
                    cfg = getattr(self.node_core, "config", None) or {}
                    name = getattr(cfg, "name", "") or ""
                    team = getattr(cfg, "team_id", "") or ""
                    hello = {
                        "v": 1, "kind": "agent-node-announce",
                        "node_id": self.my_node_id,
                        "name": name,
                        "peer_tcp_port": self.my_listen_port or 0,
                        "team_id": team,
                    }
                    conn.sendall((json.dumps(hello) + "\n").encode("utf-8"))
                except Exception:
                    pass
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
        threading.Thread(target=_loop, daemon=True, name="mesh-announce").start()

    def stop(self) -> None:
        self._stopping = True
        try:
            if self._server:
                self._server.close()
        except Exception:
            pass
        try:
            if self._announce_sock:
                self._announce_sock.close()
        except Exception:
            pass
        with self._conn_lock:
            conns = list(self.connections.values())
        for c in conns:
            c.close("node stopping")

    def _accept_loop(self) -> None:
        while not self._stopping:
            try:
                sock, addr = self._server.accept()
            except OSError:
                break
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            conn = Connection(self, sock, addr, initiated_by_me=False)
            conn.start()

    # ---------- 连接管理 ----------
    def connect(self, host: str, port: int, timeout: float = 5.0) -> Connection | None:
        """主动连接对端（异步握手；握手成功经 _register 回调）。"""
        try:
            sock = socket.create_connection((host, int(port)), timeout=timeout)
        except OSError:
            return None
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        conn = Connection(self, sock, (host, int(port)), initiated_by_me=True)
        conn.start()
        return conn

    def connect_peer(self, node_id: str, host: str, port: int) -> Connection | None:
        """幂等连接：已连接/正在连接则跳过；记录地址供断线重连。"""
        with self._conn_lock:
            c = self.connections.get(node_id)
            if c and c.alive:
                return c
            if node_id in self._connecting:
                return None
            self._connecting.add(node_id)
        self._known_addrs[node_id] = (host, int(port))
        try:
            return self.connect(host, int(port))
        finally:
            with self._conn_lock:
                self._connecting.discard(node_id)

    # ---------- 锚点出站拨号（被隔离方向通用自愈） ----------
    def start_anchor_polling(self, anchors_provider, interval: float = 30.0) -> None:
        """启动锚点主动出站回连循环。anchors_provider() -> list[{host, peer_tcp_port}]。

        用于「目的节点入站被 AP 隔离」的通用自愈：本机作为被隔离方，主动出站连接
        锚点。出站链路通常不被隔离规则丢弃，连上后照常握手/注册，双向消息可达。
        每次遍历总是尝试补齐对每个锚点的连接；既不依赖广播也无需判断对端可达性。
        """
        self._anchors_provider = anchors_provider
        self._anchor_interval = float(interval)

        def _loop():
            while not self._stopping:
                try:
                    anchors = self._anchors_provider() or []
                except Exception:
                    anchors = []
                for a in anchors:
                    if self._stopping:
                        return
                    try:
                        host, port = a.get("host"), int(a.get("peer_tcp_port") or 0)
                    except (TypeError, ValueError):
                        continue
                    if not host or not port:
                        continue
                    # 已有一致 host 的存活连接则不重复拨号
                    if self._has_live_conn_to(host):
                        continue
                    self.node_core.log("info", f"锚点出站拨号: {host}:{port}")
                    self.connect(host, port)
                for _ in range(max(1, int(self._anchor_interval))):
                    if self._stopping:
                        return
                    time.sleep(1)

        threading.Thread(target=_loop, daemon=True, name="mesh-anchor-dial").start()

    # ---------- 自动子网扫描（2.18 / 3.x: 被 AP 隔离 + 通告端口快速通道） ----------
    def start_subnet_scan(self, interval: float = 12.0, connect_timeout: float = 1.5) -> None:
        """对被隔离节点做「出站扫描」的自动发现。

        原理：AP 隔离挡的是入站，出站单播放行。本机主动对子网内每个 IP 发起短超时
        连接，凡握手成功即注册为常驻双向连接。3.x 增强：
        - **快速通道**：先拨固定「通告」TCP 端口（每 IP 1 端口，读 whoami 拿真实对等
          端口再拨 mesh），免盲扫 20 口对等段；通告不可达才回落到段扫。
        - **不断连也轻量补扫**：即使已有存活连接，也周期性（每 3 轮）作一次仅通告端口
          的轻扫，捕捉后加入的孤立节点；全段扫仅在「零存活连接」时进行。
        带冷却与节流，避免风暴。
        """
        self._scan_interval = float(interval)
        self._scan_timeout = float(connect_timeout)

        def _loop():
            cycle = 0
            while not self._stopping:
                if self._any_live_conn():
                    # 健康时每 3 轮做一次仅通告端口的轻扫，捕捉新生孤立节点
                    if cycle % 3 == 0:
                        try:
                            self._subnet_scan_once(fast_only=True)
                        except Exception:
                            pass
                else:
                    try:
                        self._subnet_scan_once(fast_only=False)
                    except Exception:
                        pass
                cycle += 1
                for _ in range(max(1, int(self._scan_interval))):
                    if self._stopping:
                        return
                    time.sleep(1)

        threading.Thread(target=_loop, daemon=True, name="mesh-subnet-scan").start()

    def _any_live_conn(self) -> bool:
        with self._conn_lock:
            return any(c.alive and c.handshake_done
                       for c in self.connections.values())

    def _scan_subnets(self) -> set[str]:
        """待扫描网段前缀（去重）：本机接口 + 已知对端主机所在网段。

        本机接口仅能给出本机 /24，但 AP 隔离可能让本机与对端不在同一网段也互连；
        把已知对端 IP 的 /24 也纳入扫描，可覆盖「跨段但知道地址」的场景。掩码恒按
        /24 推导为保守假设（无 netmask 的跨平台探测方式受限），重叠前缀自动去重。
        """
        prefixes: set[str] = set()
        from transport.beacon import _all_local_ips
        candidates = []
        try:
            candidates += _all_local_ips()
        except Exception:
            pass
        with self._conn_lock:
            candidates += [a[0] for a in self._known_addrs.values() if a and a[0]]
        for ip in candidates:
            if not ip or ip.startswith("127."):
                continue
            bike = ip.rsplit(".", 1)
            if len(bike) == 2 and bike[0].count(".") == 2:
                prefixes.add(bike[0])
        return prefixes

    def _announce_ports(self) -> list[int]:
        try:
            base = self.node_core.config.announce_tcp_port
        except Exception:
            base = 49700
        return list(range(base, base + 5))

    def _local_ip_set(self) -> set[str]:
        """本机所有接口 IP（含虚拟网卡），用于排除自发现。"""
        try:
            from transport.beacon import _all_local_ips
            return set(_all_local_ips())
        except Exception:
            return set()

    def _announce_probe(self, host: str) -> bool:
        """通告快速通道：拨固定端口读 whoami，拿到真实对等端口后拨 mesh。成功则已建长连。"""
        for ap in self._announce_ports():
            if self._stopping or self._has_live_conn_to(host):
                return self._has_live_conn_to(host)
            try:
                s = socket.create_connection((host, ap), timeout=self._scan_timeout)
            except OSError:
                continue
            peer_port = 0
            try:
                s.settimeout(2.0)
                data = s.recv(2048)
                info = json.loads(data.decode("utf-8", "replace"))
                peer_port = int(info.get("peer_tcp_port") or 0)
            except Exception:
                peer_port = 0
            finally:
                try:
                    s.close()
                except Exception:
                    pass
            if peer_port:
                conn = self.connect(host, peer_port)
                if conn and self._wait_done(conn, 2.0):
                    return True
            return self._has_live_conn_to(host)
        return False

    def _subnet_scan_once(self, fast_only: bool = False) -> None:
        ports = []
        if not fast_only:
            try:
                ports = self.node_core.config.peer_port_range()
            except Exception:
                ports = []
        # 单轮扫描时间预算：fast_only 轻扫预算更小
        budget = 6.0 if fast_only else 10.0
        budget_deadline = time.time() + budget
        prefixes = self._scan_subnets()
        if not prefixes:
            return
        # 本机自身 IP：禁止扫描/拨号（否则会把自己当对端，3.x 自发现 bug）
        local_ips = self._local_ip_set()
        for prefix in sorted(prefixes):
            if self._stopping:
                return
            for node_part in self._scan_node_range:
                if self._stopping or time.time() > budget_deadline:
                    return
                host = f"{prefix}.{node_part}"
                if host in local_ips:
                    continue  # 不扫描/不连自己
                if self._has_live_conn_to(host):
                    continue
                now = time.time()
                if now < self._scan_cool.get(host, 0):
                    continue  # 落空冷却中
                last = self._scan_lasts.get(host, 0)
                if now - last < 2.0:
                    continue  # 探测节流
                self._scan_lasts[host] = now
                if self._announce_probe(host):
                    continue  # 快速通道已建立连接
                if fast_only:
                    continue  # 轻扫只走通告端口
                # 回落：段扫（约定对等端口段）
                for p in ports:
                    if self._stopping:
                        return
                    if self._has_live_conn_to(host):
                        break
                    conn = self.connect(host, p, timeout=self._scan_timeout)
                    if conn is None:
                        continue
                    # 等心跳握手（≤ 段内尝试即返回）
                    if self._wait_done(conn, 2.0):
                        break
                # 若没连上，冷却该 host
                if not self._has_live_conn_to(host):
                    self._scan_cool[host] = time.time() + 60

    def _wait_done(self, conn, secs: float) -> bool:
        deadline = time.time() + secs
        while time.time() < deadline and conn.alive:
            if conn.handshake_done:
                return True
            time.sleep(0.05)
        return bool(conn.handshake_done)

    def _has_live_conn_to(self, host: str) -> bool:
        with self._conn_lock:
            return any(c.alive and c.handshake_done and c.addr and c.addr[0] == host
                       for c in self.connections.values())

    def _register(self, conn: Connection) -> None:
        pid = conn.peer_node_id
        if not pid:
            return
        if pid == self.my_node_id:
            # 自我连接：经本机其它网卡/IP 的子网扫描或回环把"自己"当作对端连上。
            # 必须拒绝登记并关闭，否则节点会在面板上把自己列为在线对端（3.x 自发现 bug）。
            conn.close("self connection")
            return
        with self._conn_lock:
            old = self.connections.get(pid)
            if old is not None and old is not conn and old.alive and old.handshake_done:
                # A.1 去双连接：保留「较小 node_id 发起」的那条；
                # 被淘汰者延迟排空关闭（在途文件传输不中断）
                old_init = self.my_node_id if old.initiated_by_me else pid
                new_init = self.my_node_id if conn.initiated_by_me else pid
                if new_init < old_init:
                    self.connections[pid] = conn
                    self._retire(old)
                else:
                    self._retire(conn)
                    return
            else:
                self.connections[pid] = conn
        if conn.addr:
            self._known_addrs[pid] = (conn.addr[0],
                                      int(conn.peer_peer_tcp or conn.addr[1] or 0))
        try:
            self.node_core.on_peer_connected(pid, conn)
        except Exception:
            pass

    def _retire(self, conn: Connection) -> None:
        """延迟关闭被去双连接淘汰的连接：等在途文件传输排空（≤60s）。"""
        conn.retired = True

        def _drain():
            deadline = time.time() + 60
            while time.time() < deadline and conn.alive:
                if not conn.busy_transferring() and not self._conn_has_receivers(conn):
                    break
                time.sleep(0.5)
            conn.close("duplicate conn drained")
        threading.Thread(target=_drain, daemon=True,
                         name=f"retire-{conn.addr}").start()

    def _conn_has_receivers(self, conn: Connection) -> bool:
        with self._recv_lock:
            return any(r.reply_conn is conn for r in self.file_receivers.values())

    def _on_conn_closed(self, conn: Connection) -> None:
        pid = conn.peer_node_id
        was_registered = False
        with self._conn_lock:
            if pid and self.connections.get(pid) is conn:
                del self.connections[pid]
                was_registered = True
        if conn.retired and not was_registered:
            # 去双连接淘汰关闭：胜者连接仍在，不算断线（不重连/不通知）
            return
        # 该连接上的在途接收器 → 失败收尾（关句柄 + 清半写文件，2.17.5）
        with self._recv_lock:
            stuck = [r for r in self.file_receivers.values() if r.reply_conn is conn]
        for r in stuck:
            try:
                r._finish(False, "connection lost")
            except Exception:
                pass
        if pid and not self._stopping:
            # 2.13.3 断线自动重连（有已知地址才重连）
            addr = self._known_addrs.get(pid)
            if addr:
                threading.Thread(target=self._reconnect_loop, args=(pid, addr),
                                 daemon=True, name=f"reconnect-{pid}").start()
        try:
            if pid:
                self.node_core.on_peer_disconnected(pid)
        except Exception:
            pass

    def _reconnect_loop(self, node_id: str, addr: tuple[str, int]) -> None:
        for _ in range(10):
            if self._stopping:
                return
            time.sleep(3)
            with self._conn_lock:
                c = self.connections.get(node_id)
                if c and c.alive:
                    return
            self.node_core.log("info", f"断线重连中: {node_id} -> {addr}")
            try:
                conn = self.connect(addr[0], addr[1])
            except Exception:
                conn = None
            if conn:
                # 握手成功与否看是否注册（等 6s）
                deadline = time.time() + 6
                while time.time() < deadline:
                    if not conn.alive:
                        break
                    if conn.handshake_done:
                        return
                    time.sleep(0.3)
        self.node_core.log("warning", f"重连失败（已尝试多轮）: {node_id}")

    def is_online(self, node_id: str) -> bool:
        with self._conn_lock:
            c = self.connections.get(node_id)
            return bool(c and c.alive and c.handshake_done)

    def peer_addr(self, node_id: str) -> tuple[str, int] | None:
        return self._known_addrs.get(node_id)

    # ---------- 消息发送与请求-响应 ----------
    def send_to(self, node_id: str, env: dict) -> bool:
        with self._conn_lock:
            c = self.connections.get(node_id)
        if c and c.alive:
            try:
                c.send_env(env)
                return True
            except OSError:
                c.close("send failed")
                self._on_conn_closed(c)
        return False

    def call(self, node_id: str, env: dict, timeout: float = 15.0) -> dict | None:
        """请求-响应：按 correlation_id=请求 msg_id 匹配；超时返回 None。"""
        mid = env["msg_id"]
        p = _Pending()
        with self._pending_lock:
            self._pending[mid] = p
        try:
            if not self.send_to(node_id, env):
                return None
            if not p.event.wait(timeout):
                return None
            return p.env
        finally:
            with self._pending_lock:
                self._pending.pop(mid, None)

    def _dispatch(self, env: dict, conn: Connection) -> None:
        t = env.get("type")
        cid = env.get("correlation_id")
        if t in P.RESPONSE_TYPES and cid:
            with self._pending_lock:
                p = self._pending.get(cid)
            if p is not None:
                p.env = env
                p.event.set()
                return
        # 非响应或无 pending → 交给节点核心处理
        self.node_core.handle_envelope(env, conn)

    # ---------- 文件接收分派（A.4） ----------
    def remove_receiver(self, ftid: bytes) -> None:
        with self._recv_lock:
            self.file_receivers.pop(ftid, None)
            self._orphans.pop(ftid, None)

    def register_receiver(self, ftid: bytes, recv: FileReceiver) -> None:
        """注册接收器；若已有先到的孤儿块（pull 方向竞态）则重放。"""
        with self._recv_lock:
            self.file_receivers[ftid] = recv
            orphans = self._orphans.pop(ftid, [])
        for seq, data in orphans:
            recv.feed(seq, data)
            if recv.done.is_set():
                break

    def _on_file_block(self, conn: Connection, payload: bytes) -> None:
        unpacked = P.unpack_file_block(payload)
        if unpacked is None:
            return
        ftid, seq, data = unpacked
        with self._recv_lock:
            recv = self.file_receivers.get(ftid)
            if recv is None:
                # 块先于接收器注册到达（pull 竞态）→ 孤儿块缓存，注册时重放
                buf = self._orphans.setdefault(ftid, [])
                if len(buf) < 4096:
                    buf.append((seq, data))
                return
        recv.feed(seq, data)

    # ---------- 文件传输 API（A.4） ----------
    def push_file(self, node_id: str, local_path: Path, target_path: str,
                  purpose: str = "file", task_ref: dict | None = None) -> dict:
        """push：file_push → file_ack → 块帧 → file_result。purpose:
        file=普通文件（受 allow_file）；task_attachment=任务附件（受 allow_ai_task，2.13.1）。"""
        local_path = Path(local_path)
        if not local_path.is_file():
            return {"ok": False, "error": "agent_error", "detail": f"本地文件不存在: {local_path}"}
        size = local_path.stat().st_size
        sha = P.sha256_file(local_path)
        ftid = uuid.uuid4().bytes
        req = P.make_envelope(P.T_FILE_PUSH, self.my_node_id, node_id, {
            "targetPath": str(target_path),
            "size": size, "sha256": sha,
            "ftid": base64.b64encode(ftid).decode(),
            "purpose": purpose, "taskRef": task_ref or {},
        })
        ack = self.call(node_id, req, timeout=15)
        if ack is None:
            return {"ok": False, "error": P.E_OFFLINE, "detail": "目标不在线或未应答"}
        pl = ack.get("payload") or {}
        if not pl.get("accepted"):
            return {"ok": False, "error": pl.get("error") or P.E_DISABLED,
                    "detail": pl.get("detail") or "对方拒绝接收文件"}
        with self._conn_lock:
            conn = self.connections.get(node_id)
        if not conn or not conn.alive:
            return {"ok": False, "error": P.E_OFFLINE, "detail": "连接已断开"}
        # 流式发送块帧（空文件也发一个空块，保证接收方能完成）
        seq = 0
        conn.add_stream()
        try:
            with open(local_path, "rb") as f:
                while True:
                    b = f.read(P.FILE_CHUNK)
                    conn.send_block(ftid, seq, b)
                    seq += 1
                    if not b:
                        break
        except OSError as e:
            return {"ok": False, "error": "agent_error", "detail": f"读取本地文件失败: {e}"}
        finally:
            conn.done_stream()
        # 等 file_result（correlation_id = file_push 的 msg_id）
        p = _Pending()
        with self._pending_lock:
            self._pending[req["msg_id"]] = p
        try:
            wait = max(30.0, size / (200 * 1024) + 30)
            if not p.event.wait(wait):
                return {"ok": False, "error": P.E_TIMEOUT, "detail": "等待 file_result 超时"}
            rpl = p.env.get("payload") or {}
            return {"ok": bool(rpl.get("ok")), "error": rpl.get("error"),
                    "detail": rpl.get("error") or "文件已完整送达"}
        finally:
            with self._pending_lock:
                self._pending.pop(req["msg_id"], None)

    def pull_file(self, node_id: str, remote_path: str, dest_path: Path) -> dict:
        """pull：file_pull → file_ack（含元数据）→ 对端推块帧 → 本端校验后回 file_result。"""
        dest_path = Path(dest_path)
        req = P.make_envelope(P.T_FILE_PULL, self.my_node_id, node_id,
                              {"path": str(remote_path)})
        ack = self.call(node_id, req, timeout=15)
        if ack is None:
            return {"ok": False, "error": P.E_OFFLINE, "detail": "目标不在线或未应答"}
        pl = ack.get("payload") or {}
        if not pl.get("accepted"):
            return {"ok": False, "error": pl.get("error") or P.E_DISABLED,
                    "detail": pl.get("detail") or "对方拒绝拉取请求"}
        size = int(pl.get("size") or 0)
        sha = pl.get("sha256") or ""
        ftid = base64.b64decode(pl["ftid"])
        with self._conn_lock:
            conn = self.connections.get(node_id)
        if not conn or not conn.alive:
            return {"ok": False, "error": P.E_OFFLINE, "detail": "连接已断开"}
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        recv = FileReceiver(self, ftid, dest_path, size, sha,
                            correlation_id=req["msg_id"], reply_conn=conn)
        self.register_receiver(ftid, recv)
        wait = max(60.0, size / (200 * 1024) + 60)
        if not recv.done.wait(wait):
            self.remove_receiver(ftid)
            return {"ok": False, "error": P.E_TIMEOUT, "detail": "接收文件数据超时"}
        return {"ok": recv.ok, "error": recv.error,
                "detail": "文件已完整接收" if recv.ok else (recv.error or "接收失败"),
                "path": str(dest_path)}

    def stream_out_file(self, conn: Connection, ftid: bytes, path: Path) -> bool:
        """向对端推送文件块（push 发起方 / pull 响应方共用）。"""
        conn.add_stream()
        try:
            seq = 0
            with open(path, "rb") as f:
                while True:
                    b = f.read(P.FILE_CHUNK)
                    conn.send_block(ftid, seq, b)
                    seq += 1
                    if not b:
                        break
            return True
        except OSError:
            return False
        finally:
            conn.done_stream()
