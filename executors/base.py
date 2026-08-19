"""执行器插件统一接口 —— 设计 2.2.15（定稿签名，AI 施工硬契约）。

- 一套接口覆盖 GUI / 交互式 TUI / 非交互 CLI 三种类型
- 绑定（MUST）: 方法名/参数名/字段名/类型/返回语义；自由（施工定）: 内部实现
- is_done 默认实现基于结果文件契约（2.2.11）；插件可覆盖（如 WorkBuddy UIA 信号）
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

ErrorCode = str  # 2.13.5: offline/disabled/not_installed/busy/suspended/timeout/agent_error


class PluginContext:
    def __init__(self, node_id: str, agent_id: str, work_dir: Path, config: dict):
        self.node_id = node_id
        self.agent_id = agent_id       # 工作目录隔离键（2.2.9）
        self.work_dir = Path(work_dir)
        self.config = config or {}     # 插件配置（含授权对话框匹配等启动参数，2.2.14）


class CapabilityResult:
    """启动自检（2.2.12）：以「启动时能否探测到」为准决定是否广播能力。"""

    def __init__(self, available: bool, reason: str | None = None,
                 premises: list[str] | None = None):
        self.available = available
        self.reason = reason
        self.premises = premises or []


class TaskInput:
    def __init__(self, task_id: str, prompt: str, attachments: list[str],
                 result_file: Path, timeout: float):
        self.task_id = task_id
        self.prompt = prompt              # 含「把最终结论写入结果文件」指令（由注册表组装）
        self.attachments = list(attachments or [])  # 已 P2P 直传到 work_dir 的本地路径（2.13.1）
        self.result_file = Path(result_file)
        self.timeout = float(timeout)


class SubmitResult:
    def __init__(self, ok: bool, error: ErrorCode | None = None, detail: str | None = None):
        self.ok = ok
        self.error = error
        self.detail = detail

    def as_dict(self) -> dict:
        return {"ok": self.ok, "error": self.error, "detail": self.detail}


class ExecutorStatus:
    def __init__(self, available: bool, state: str, inflight: int, concurrency: int,
                 current_task: str | None, queue_len: int, until: str | None = None):
        self.available = available
        self.state = state            # 插件只报 idle/busy；suspended 由节点核心叠加（2.9.10）
        self.inflight = inflight
        self.concurrency = concurrency
        self.current_task = current_task
        self.queue_len = queue_len
        self.until = until            # 挂起到期时间点（4.3）

    def as_dict(self) -> dict:
        return {"available": self.available, "state": self.state, "inflight": self.inflight,
                "concurrency": self.concurrency, "currentTask": self.current_task,
                "queueLen": self.queue_len, "until": self.until}


class ResultPayload:
    def __init__(self, ok: bool, error: ErrorCode | None = None, content: str | None = None):
        self.ok = ok
        self.error = error
        self.content = content          # 结果文件内容（成功时）

    def as_dict(self) -> dict:
        return {"ok": self.ok, "error": self.error, "content": self.content}


class ExecutorPlugin(ABC):
    """执行器插件基类。约定（2.2.15）:
    plugin_id / display_name / executor_type / concurrency 为类属性；
    插件语言 = Python；由注册表（executors/registry.py）实例化并回调生命周期。
    """

    plugin_id: str = ""
    display_name: str = ""
    executor_type: str = "non_interactive_cli"  # interactive_tui | non_interactive_cli | gui
    concurrency: int = 3

    def __init__(self, ctx: PluginContext):
        self.ctx = ctx
        self._result_files: dict[str, Path] = {}   # task_id -> result.md 路径（基座记账）

    # ---------- 生命周期 ----------
    def on_load(self) -> None:
        pass

    def on_unload(self) -> None:
        pass

    @abstractmethod
    def check_capability(self) -> CapabilityResult:
        """启动自检：CLI=which；GUI=窗口/进程探测（2.2.12）。"""

    # ---------- 任务 ----------
    @abstractmethod
    def submit(self, task: TaskInput) -> SubmitResult:
        """收到即运行；结果写入 task.result_file（结果文件契约 2.2.11）。"""

    def is_done(self, task_id: str) -> bool:
        """默认：结果文件存在（大小稳定性由注册表轮询叠加，2.2.11）。"""
        rf = self._result_files.get(task_id)
        return rf is not None and rf.exists()

    def get_result(self, task_id: str) -> ResultPayload:
        rf = self._result_files.get(task_id)
        if rf is not None and rf.exists():
            try:
                return ResultPayload(True, None, rf.read_text(encoding="utf-8", errors="replace"))
            except Exception as e:
                return ResultPayload(False, "agent_error", f"读取结果文件失败: {e}")
        return ResultPayload(False, "agent_error", "结果文件不存在")

    # ---------- 状态 ----------
    def status(self) -> ExecutorStatus:
        """插件只报 idle/busy；suspended 由节点核心叠加（2.9.10/4.3）。"""
        return ExecutorStatus(available=True, state="idle", inflight=0,
                              concurrency=self.concurrency, current_task=None, queue_len=0)

    # ---------- 基座辅助 ----------
    def register_result_file(self, task_id: str, result_file: Path) -> None:
        self._result_files[task_id] = Path(result_file)

    def unregister_task(self, task_id: str) -> None:
        self._result_files.pop(task_id, None)
