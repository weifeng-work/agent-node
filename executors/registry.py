"""执行器注册表（2.2.15 / 2.2.9 / 2.13.2 / 4.3）。

- 插件加载: 内置 mock + executor_config.json 的 cli_executors 条目 + WorkBuddy 适配器
  + data/plugins/ 外部插件（2.2.10 分发后重扫即纳入）
- 能力自检 → 决定是否广播（2.2.12）
- 任务生命周期: submit → poll(is_done + 结果文件大小稳定) → get_result → 完成/失败
- 并发模型: 非交互 CLI 信号量并发（满 → busy 拒绝不排队）；
  TUI/GUI 单队列串行（忙时排队，2.13.2）
- 挂起: 拒绝任务返回 suspended；到期自动恢复（executor_config.json 持久化，2.14.7）
"""
from __future__ import annotations

import importlib.util
import json
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .base import (CapabilityResult, ExecutorPlugin, PluginContext, SubmitResult,
                   TaskInput)
from .mock_plugin import MockPlugin
from .cli_executor import CliExecutorPlugin
from node import a2a

RESULT_FILE_NAME = "result.md"
POLL_INTERVAL = 1.0
SIZE_STABLE_ROUNDS = 2  # 2.2.11: 大小连续 N 轮不变
PRUNE_KEEP_SEC = 3600   # 已完结任务保留窗口后回收（防内存/磁盘无限增长）


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_until(until: str | None) -> float | None:
    if not until:
        return None
    try:
        return datetime.fromisoformat(until).timestamp()
    except ValueError:
        return None


class TaskRecord:
    def __init__(self, task_id, agent_id, prompt, mode, caller_node_id, caller_id,
                 work_dir, result_file, timeout, attachments=None):
        self.task_id = task_id
        self.agent_id = agent_id
        self.prompt = prompt
        self.mode = mode                    # sync/async/trigger（节点级语义，2.7.5）
        self.caller_node_id = caller_node_id
        self.caller_id = caller_id
        self.work_dir = Path(work_dir)
        self.result_file = Path(result_file)
        self.timeout = float(timeout)
        self.attachments = list(attachments or [])
        self.state = a2a.TASK_SUBMITTED
        self.submitted_at = time.time()
        self.deadline = self.submitted_at + self.timeout
        self.content: str | None = None
        self.error: str | None = None
        self.detail: str | None = None
        self.done_event = threading.Event()
        self.last_size = -1
        self.stable_count = 0
        self.finished_at: float | None = None   # 完结时间点（回收窗口用）

    def as_dict(self) -> dict:
        task = a2a.make_task(self.task_id, self.agent_id, self.state)
        if self.content is not None:
            task["artifacts"] = a2a.task_artifact_text(self.content)
        if self.error:
            task["metadata"]["error"] = self.error
        return task


class ExecutorRegistry:
    def __init__(self, node_core):
        self.node_core = node_core
        self.data_dir = Path(node_core.data_dir)
        self.plugins_dir = self.data_dir / "plugins"
        self.work_root = self.data_dir / "executor_work"
        self.config_path = self.data_dir / "executor_config.json"
        self.plugins: dict[str, ExecutorPlugin] = {}
        self.entries: dict[str, dict] = {}       # agent_id -> 条目配置（name/挂起等）
        self.available: dict[str, bool] = {}
        self.tasks: dict[str, TaskRecord] = {}
        self._lock = threading.RLock()
        self._queues: dict[str, list[str]] = {}  # agent_id -> 排队 task_id（TUI/GUI）
        self._workers: dict[str, threading.Thread] = {}
        self._cli_sems: dict[str, threading.BoundedSemaphore] = {}
        self._stop = threading.Event()
        self._config: dict = self._load_config()

    # ---------- 配置（executor_config.json，2.14.7） ----------
    def _load_config(self) -> dict:
        cfg: dict = {"cli_executors": [], "agents": {}}
        if self.config_path.exists():
            try:
                loaded = json.loads(self.config_path.read_text(encoding="utf-8"))
                cfg.update({k: v for k, v in loaded.items() if k in cfg})
            except Exception:
                pass
        return cfg

    def _save_config(self) -> None:
        from node.config import atomic_write_json
        atomic_write_json(self.config_path, self._config)

    # ---------- 插件加载 ----------
    def load_plugins(self) -> None:
        with self._lock:
            self.plugins.clear()
            self.entries.clear()
            # 1) 内置 mock（测试桩，第五章 #4；node_config.json enable_mock=true 显式开启）
            if self.node_core.config.enable_mock:
                self._add_plugin(MockPlugin, "mock", {})
            # 2) WorkBuddy GUI 适配器（导入失败 = 本机缺依赖，跳过）
            try:
                from .adapters.workbuddy import WorkBuddyPlugin
                self._add_plugin(WorkBuddyPlugin, "workbuddy", {})
            except Exception as e:
                self.node_core.log("info", f"WorkBuddy 适配器不可用: {e}")
            # 2.5) CodeBuddy CLI 适配器（非交互 fresh + 交互式 TUI 常驻）
            try:
                from .adapters.codebuddy import (CodeBuddyCliPlugin,
                                                 CodeBuddyTuiPlugin)
                self._add_plugin(CodeBuddyCliPlugin, "codebuddy-cli", {})
                self._add_plugin(CodeBuddyTuiPlugin, "codebuddy-tui", {})
            except Exception as e:
                self.node_core.log("info", f"CodeBuddy 适配器不可用: {e}")
            # 2.7) TraeWork CN GUI 适配器（CDP 注入/回收；缺 websockets 或未装则跳过）
            try:
                from .adapters.traework import TraeWorkPlugin
                self._add_plugin(TraeWorkPlugin, "traework", {})
            except Exception as e:
                self.node_core.log("info", f"TraeWork 适配器不可用: {e}")
            # 3) 配置的 CLI 执行器条目
            for entry in self._config.get("cli_executors") or []:
                agent_id = entry.get("agent_id")
                command = entry.get("command")
                if not agent_id or not command:
                    continue
                ctx = PluginContext(self.node_core.config.node_id, agent_id,
                                    self.work_root / agent_id, entry)
                plugin = CliExecutorPlugin(ctx, command, entry)
                self._install(agent_id, plugin, entry)
            # 4) data/plugins/ 外部插件（2.2.10）
            if self.plugins_dir.is_dir():
                for py in sorted(self.plugins_dir.glob("*.py")):
                    self._load_external(py)
        self.node_core.log("info", f"插件加载完成: {list(self.plugins.keys())}")

    def _add_plugin(self, plugin_cls, agent_id: str, entry: dict) -> None:
        ctx = PluginContext(self.node_core.config.node_id, agent_id,
                            self.work_root / agent_id, entry)
        self._install(agent_id, plugin_cls(ctx), entry)

    def _install(self, agent_id: str, plugin: ExecutorPlugin, entry: dict) -> None:
        self.plugins[agent_id] = plugin
        merged = dict(entry or {})
        merged.setdefault("name", plugin.display_name or agent_id)
        merged.setdefault("executor_type", plugin.executor_type)
        merged.setdefault("concurrency", plugin.concurrency)
        merged.setdefault("suspend", {"suspended": False})
        # 恢复持久化的 name/挂起（2.14.7）
        saved = (self._config.get("agents") or {}).get(agent_id) or {}
        if saved.get("name"):
            merged["name"] = saved["name"]
        if saved.get("suspend"):
            merged["suspend"] = saved["suspend"]
        self.entries[agent_id] = merged
        try:
            cap = plugin.check_capability()
            self.available[agent_id] = cap.available
            merged["premises"] = cap.premises           # 前提摘要（2.2.13）
            merged["reason"] = cap.reason
            if not cap.available:
                self.node_core.log("info", f"执行器 {agent_id} 不可用: {cap.reason}")
        except Exception:
            self.available[agent_id] = False
            merged["premises"] = []
            merged["reason"] = "能力自检异常"
        try:
            plugin.on_load()
        except Exception:
            pass
        if plugin.executor_type == "non_interactive_cli":
            self._cli_sems[agent_id] = threading.BoundedSemaphore(
                int(merged.get("concurrency") or 3))

    def _load_external(self, py: Path) -> None:
        try:
            spec = importlib.util.spec_from_file_location(f"agn_plugin_{py.stem}", py)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for obj in vars(mod).values():
                if (isinstance(obj, type) and issubclass(obj, ExecutorPlugin)
                        and obj is not ExecutorPlugin and getattr(obj, "plugin_id", "")
                        and not obj.__module__.startswith(("executors", "node"))):
                    agent_id = getattr(obj, "agent_id", None) or obj.plugin_id
                    entry = {"external_file": str(py)}
                    self._install(agent_id, obj(PluginContext(
                        self.node_core.config.node_id, agent_id,
                        self.work_root / agent_id, entry)), entry)
                    return
            self.node_core.log("warning", f"外部插件未找到 ExecutorPlugin 子类: {py.name}")
        except Exception as e:
            self.node_core.log("error", f"外部插件加载失败 {py.name}: {e}\n"
                                       f"{traceback.format_exc()}")

    def rescan(self, changed: list[str] | None = None) -> dict:
        """plugin_sync 重扫（2.2.10）：新增纳入；交互式执行器重启进程生效。"""
        restarted = []
        with self._lock:
            old_keys = set(self.plugins.keys())
            # 保留外部条目配置，重新加载
            self.load_plugins()
            for agent_id in changed or []:
                plugin = self.plugins.get(agent_id)
                if plugin and plugin.executor_type in ("interactive_tui", "gui"):
                    try:
                        plugin.on_unload()
                        plugin.on_load()
                        restarted.append(agent_id)
                    except Exception:
                        pass
            new_keys = [k for k in self.plugins.keys() if k not in old_keys]
        return {"ok": True, "added": new_keys, "restarted": restarted,
                "all": list(self.plugins.keys())}

    # ---------- 能力广播（2.16.2 beacon capabilities） ----------
    def capabilities_payload(self) -> list[dict]:
        out = []
        with self._lock:
            for agent_id, plugin in self.plugins.items():
                if not self.available.get(agent_id):
                    continue
                e = self.entries.get(agent_id, {})
                out.append({
                    "agentId": agent_id,
                    "name": e.get("name") or plugin.display_name,
                    "executorType": plugin.executor_type,
                    "concurrency": int(e.get("concurrency") or plugin.concurrency),
                })
        return out

    def executor_ids(self) -> list[str]:
        with self._lock:
            return list(self.plugins.keys())

    # ---------- 任务提交（2.13.2 并发/忙碌语义） ----------
    def submit_local(self, agent_id: str, task_id: str, prompt: str, mode: str,
                     caller_node_id: str | None, caller_id: str,
                     attachments: list[str] | None = None,
                     timeout: float = 600.0) -> SubmitResult:
        with self._lock:
            plugin = self.plugins.get(agent_id)
            if plugin is None or not self.available.get(agent_id):
                reason = "未检测到该执行器" if plugin is None else "执行器不可用（未运行/未安装）"
                return SubmitResult(False, "not_installed", reason)
            entry = self.entries.get(agent_id, {})
            # 挂起门控（4.3）：到期自动恢复
            susp = entry.get("suspend") or {}
            if susp.get("suspended"):
                until = _parse_until(susp.get("until"))
                if until and time.time() >= until:
                    susp["suspended"] = False
                    self._persist_agent(agent_id)
                else:
                    return SubmitResult(False, "suspended",
                                        f"执行器已挂起（原因: {susp.get('reason') or '手动'}）")
            etype = plugin.executor_type
            if etype == "non_interactive_cli":
                inflight = sum(1 for t in self.tasks.values()
                               if t.agent_id == agent_id and t.state == a2a.TASK_WORKING)
                conc = int(entry.get("concurrency") or plugin.concurrency)
                if inflight >= conc:
                    return SubmitResult(False, "busy",
                                        f"非交互执行器忙（在途 {inflight}/{conc}，不排队）")
        # 工作目录隔离（2.2.9）
        work_dir = self.work_root / agent_id / task_id
        work_dir.mkdir(parents=True, exist_ok=True)
        result_file = work_dir / RESULT_FILE_NAME
        # 组装含结果文件指令的提示词（2.2.11）；GUI 适配器自建指令（workbuddy 自带）
        if plugin.executor_type != "gui":
            full_prompt = (f"{prompt}\n\n"
                           f"【输出要求】完成后请把最终结论写入文件: "
                           f"{result_file.resolve()}（UTF-8 编码，Markdown 格式）。")
        else:
            full_prompt = prompt
        record = TaskRecord(task_id, agent_id, prompt, mode, caller_node_id, caller_id,
                            work_dir, result_file, timeout, attachments)
        with self._lock:
            self.tasks[task_id] = record
        task_input = TaskInput(task_id, full_prompt, attachments or [],
                               result_file, timeout)
        plugin.register_result_file(task_id, result_file)
        record.state = a2a.TASK_WORKING
        if etype == "non_interactive_cli":
            threading.Thread(target=self._run_cli_task, args=(plugin, task_input, record),
                             daemon=True, name=f"cli-task-{task_id[:8]}").start()
        else:
            # TUI/GUI: 排队串行（2.13.2）
            with self._lock:
                self._queues.setdefault(agent_id, []).append(task_id)
                self._ensure_worker(agent_id)
        return SubmitResult(True)

    def _run_cli_task(self, plugin: ExecutorPlugin, task: TaskInput, record: TaskRecord):
        sem = self._cli_sems.get(record.agent_id)
        acquired = sem.acquire(timeout=0.1) if sem else True
        if not acquired:
            self._finalize(record, False, "busy", "并发槽被占（竞态）")
            return
        try:
            r = plugin.submit(task)
            if not r.ok:
                self._finalize(record, False, r.error or "agent_error", r.detail)
        except Exception as e:
            self._finalize(record, False, "agent_error", f"{e}\n{traceback.format_exc()}")
        finally:
            if sem and acquired:
                sem.release()

    def _ensure_worker(self, agent_id: str) -> None:
        t = self._workers.get(agent_id)
        if t and t.is_alive():
            return
        t = threading.Thread(target=self._queued_worker, args=(agent_id,),
                             daemon=True, name=f"exec-worker-{agent_id}")
        self._workers[agent_id] = t
        t.start()

    def _queued_worker(self, agent_id: str) -> None:
        """TUI/GUI 串行队列 worker：处理完一个再接下一个。"""
        while not self._stop.is_set():
            with self._lock:
                q = self._queues.get(agent_id)
                task_id = q.pop(0) if q else None
            if not task_id:
                return
            record = self.tasks.get(task_id)
            plugin = self.plugins.get(agent_id)
            if record is None or plugin is None:
                continue
            task_input = TaskInput(record.task_id, record.prompt, record.attachments,
                                   record.result_file, record.timeout)
            plugin.register_result_file(record.task_id, record.result_file)
            try:
                r = plugin.submit(task_input)
                if not r.ok:
                    self._finalize(record, False, r.error or "agent_error", r.detail)
                    continue
                # 等待完成（轮询由全局 poller 推进 state，这里等待事件）
                record.done_event.wait(timeout=record.timeout + 10)
            except Exception as e:
                self._finalize(record, False, "agent_error",
                               f"{e}\n{traceback.format_exc()}")

    # ---------- 全局轮询（is_done + 文件大小稳定 + 超时，2.2.11） ----------
    def start_poller(self) -> None:
        threading.Thread(target=self._poll_loop, daemon=True, name="registry-poller").start()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            self._prune_old_tasks()
            with self._lock:
                working = [t for t in self.tasks.values() if t.state == a2a.TASK_WORKING]
            for record in working:
                try:
                    self._poll_one(record)
                except Exception:
                    pass
            time.sleep(POLL_INTERVAL)

    def _prune_old_tasks(self) -> None:
        """回收已完结超窗任务：移除任务记录 + 注销结果文件 + 清理工作目录（2.2 内存/磁盘）。"""
        import shutil
        now = time.time()
        with self._lock:
            for tid, rec in list(self.tasks.items()):
                if not rec.finished_at or (now - rec.finished_at) <= PRUNE_KEEP_SEC:
                    continue
                self.tasks.pop(tid, None)
                try:
                    plugin = self.plugins.get(rec.agent_id)
                    if plugin:
                        plugin.unregister_task(tid)
                except Exception:
                    pass
                try:
                    shutil.rmtree(rec.work_dir, ignore_errors=True)
                except Exception:
                    pass

    def _poll_one(self, record: TaskRecord) -> None:
        plugin = self.plugins.get(record.agent_id)
        if plugin is None:
            self._finalize(record, False, "agent_error", "插件已卸载")
            return
        # 超时兜底
        if time.time() > record.deadline:
            rp = plugin.get_result(record.task_id)
            if rp.ok and rp.content:
                self._finalize(record, True, None, None, rp.content)
            else:
                self._finalize(record, False, "timeout",
                               f"任务超时（>{record.timeout:.0f}s），返回已观察部分",
                               rp.content if rp and rp.content else None)
            return
        done_flag = False
        # 结果文件存在 + 大小稳定（2.2.11）
        if record.result_file.exists():
            try:
                size = record.result_file.stat().st_size
            except OSError:
                size = -1
            if size == record.last_size and size >= 0:
                record.stable_count += 1
            else:
                record.stable_count = 0
                record.last_size = size
            if record.stable_count >= SIZE_STABLE_ROUNDS:
                done_flag = True
        # 插件信号（如 WorkBuddy UIA 完成按钮）
        try:
            if plugin.is_done(record.task_id):
                done_flag = True
        except Exception:
            pass
        if done_flag:
            rp = plugin.get_result(record.task_id)
            if rp.ok and rp.content:
                self._finalize(record, True, None, None, rp.content)
            else:
                # 信号完成但无内容 → 再等一轮（写入中/回执尚未落盘）
                record.stable_count = 0
                record.deadline = max(record.deadline, time.time() + 15)

    def _finalize(self, record: TaskRecord, ok: bool, error: str | None,
                  detail: str | None = None, content: str | None = None) -> None:
        with self._lock:
            if record.state in (a2a.TASK_COMPLETED, a2a.TASK_FAILED):
                return
            record.content = content
            record.error = error
            record.detail = detail
            record.state = a2a.TASK_COMPLETED if ok else a2a.TASK_FAILED
            record.finished_at = time.time()
        record.done_event.set()
        # 通信日志（2.3.1）
        peer = record.caller_node_id or "local"
        self.node_core.store.add_comm_log(
            "outbound" if record.caller_node_id else "local",
            peer, "task_result", record.task_id,
            "ok" if ok else (error or "error"), detail)
        # 异步任务回执（2.6.1）: 远程 → 推送给调用方节点；本机 → 直接入邮箱
        if record.mode == "async":
            if record.caller_node_id:
                self.node_core.push_async_result(record)
            else:
                self.node_core.store_local_async_result(record)
        # 触发型不推结果（2.7.1）

    # ---------- 查询 ----------
    def task_info(self, task_id: str) -> dict | None:
        with self._lock:
            rec = self.tasks.get(task_id)
            return rec.as_dict() if rec else None

    def wait_task(self, task_id: str, timeout: float = 30.0) -> dict | None:
        """同步等待任务完成（sync 模式）。"""
        with self._lock:
            rec = self.tasks.get(task_id)
        if rec is None:
            return None
        rec.done_event.wait(timeout=timeout)
        with self._lock:
            return rec.as_dict()

    # ---------- 状态（2.9.10 / executor_status 协议） ----------
    def status(self, agent_id: str) -> dict | None:
        with self._lock:
            plugin = self.plugins.get(agent_id)
            if plugin is None:
                return None
            entry = self.entries.get(agent_id, {})
        try:
            st = plugin.status().as_dict()
        except Exception:
            st = {"available": False, "state": "idle", "inflight": 0,
                  "concurrency": plugin.concurrency, "currentTask": None, "queueLen": 0}
        st["available"] = self.available.get(agent_id, False)
        st["name"] = entry.get("name")
        st["agentId"] = agent_id
        st["executorType"] = plugin.executor_type
        st["pluginId"] = plugin.plugin_id
        st["premises"] = entry.get("premises") or []
        st["reason"] = entry.get("reason")
        st["sessionId"] = ""
        susp = entry.get("suspend") or {}
        if susp.get("suspended"):
            st["state"] = "suspended"       # 节点核心叠加（2.2.15）
            st["suspendReason"] = susp.get("reason")
            st["until"] = susp.get("until")
        with self._lock:
            q = self._queues.get(agent_id) or []
            st["queueLen"] = len(q)
            if plugin.executor_type == "non_interactive_cli":
                st["queueLen"] = 0           # 2.13.2: 非交互忙时拒绝不排队
            working = [t for t in self.tasks.values()
                       if t.agent_id == agent_id and t.state == a2a.TASK_WORKING]
            st["inflight"] = len(working)
            st["currentTask"] = working[0].task_id if working else None
        return st

    def deep_status(self, agent_id: str | None = None) -> dict:
        with self._lock:
            ids = [agent_id] if agent_id else list(self.plugins.keys())
        return {"executors": [self.status(i) for i in ids if self.status(i)]}

    # ---------- 挂起 / 改名 / 重启（4.3 / 2.5.9） ----------
    def set_suspend(self, agent_id: str, suspend: bool, reason: str | None = None,
                    until: str | None = None) -> bool:
        with self._lock:
            if agent_id not in self.plugins:
                return False
            entry = self.entries.setdefault(agent_id, {})
            if suspend:
                entry["suspend"] = {"suspended": True, "reason": reason or "手动",
                                    "until": until}
            else:
                entry["suspend"] = {"suspended": False}
            self._persist_agent(agent_id)
        return True

    def rename_executor(self, agent_id: str, new_name: str) -> bool:
        with self._lock:
            if agent_id not in self.plugins:
                return False
            self.entries.setdefault(agent_id, {})["name"] = new_name
            self._persist_agent(agent_id)
        return True

    def restart_plugin(self, agent_id: str) -> bool:
        with self._lock:
            plugin = self.plugins.get(agent_id)
            if plugin is None:
                return False
        try:
            plugin.on_unload()
            plugin.on_load()
            cap = plugin.check_capability()
            self.available[agent_id] = cap.available
            return True
        except Exception:
            return False

    def _persist_agent(self, agent_id: str) -> None:
        agents = self._config.setdefault("agents", {})
        agents[agent_id] = {
            "name": self.entries.get(agent_id, {}).get("name"),
            "suspend": self.entries.get(agent_id, {}).get("suspend"),
        }
        self._save_config()

    def stop(self) -> None:
        self._stop.set()
