"""非交互 CLI 执行器基座（2.2.9 / 2.2.6）。

- 每次任务新进程/新会话（fresh），进程退出即结束
- 提示词经 stdin 注入（经验教训：argv 多行会被截断）
- 可见性：优先 psmux（若安装）；未装 psmux 的 Windows 降级为 CREATE_NEW_CONSOLE
  独立控制台窗口（真实可见窗口，符合 2.2.6「有桌面会话→弹窗可见」边界；无桌面
  会话时控制台不可见但 CLI 照跑、结果文件照写，符合降级语义）
- 任务完成后窗口停留数秒再关闭（cmd /c "... & timeout /t N"）
- 由 executor_config.json 的 cli_executors 条目实例化，命令模板从配置读取
"""
from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path

from .base import CapabilityResult, ExecutorPlugin, SubmitResult, TaskInput

DWELL_SECONDS = 4  # 完成后窗口停留（2.2.6 仅 CLI 非交互执行器适用）


class CliExecutorPlugin(ExecutorPlugin):
    """通用非交互 CLI 执行器（按 executor_config.json 条目实例化）。"""

    executor_type = "non_interactive_cli"

    def __init__(self, ctx, command: str, entry: dict):
        super().__init__(ctx)
        self.command = command
        self.entry = entry or {}
        self.plugin_id = self.entry.get("plugin_id") or "cli"
        self.display_name = self.entry.get("name") or self.plugin_id
        self.concurrency = int(self.entry.get("concurrency") or 3)
        self._inflight = 0
        self._lock = threading.Lock()

    # ---------- 能力自检（2.2.12: CLI 用 which） ----------
    def check_capability(self) -> CapabilityResult:
        first = self.command.strip().split(maxsplit=1)[0] if self.command.strip() else ""
        first = first.strip('"').strip("'")  # 兼容带引号的路径命令
        if not first:
            return CapabilityResult(False, reason="命令模板为空")
        if Path(first).is_file() or shutil.which(first):
            return CapabilityResult(True, premises=[f"命令可用: {first}"])
        return CapabilityResult(False, reason=f"未检测到可执行文件: {first}（which 未命中）")

    # ---------- 任务 ----------
    def submit(self, task: TaskInput) -> SubmitResult:
        work_dir = task.result_file.parent
        work_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._inflight += 1
        try:
            if shutil.which("psmux"):
                # psmux 可见窗口（detached 会话 + attach 由人工/后续版本触发）
                cmd = f'psmux new-session -d -s agn_{task.task_id[:8]} -- cmd /c "{self.command} & timeout /t {DWELL_SECONDS} >nul"'
                creationflags = subprocess.CREATE_NO_WINDOW
                shell = True
            else:
                # Windows 降级：独立可见控制台窗口
                cmd = f"{self.command} & timeout /t {DWELL_SECONDS} >nul"
                creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                shell = True
            try:
                proc = subprocess.Popen(
                    cmd, shell=shell, cwd=str(work_dir),
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
            except OSError as e:
                return SubmitResult(False, "agent_error", f"拉起 CLI 进程失败: {e}")
            # 提示词经 stdin 注入（防 argv 换行截断）
            try:
                proc.stdin.write(task.prompt.encode("utf-8"))
                proc.stdin.close()
            except OSError:
                pass
            threading.Thread(target=self._wait, args=(proc, task.task_id),
                             daemon=True).start()
            return SubmitResult(True)
        finally:
            pass

    def _wait(self, proc, task_id: str) -> None:
        try:
            proc.wait(timeout=max(60, int(self.entry.get("max_run_sec") or 1800)))
        except Exception:
            pass
        finally:
            with self._lock:
                self._inflight -= 1

    def status(self):
        from .base import ExecutorStatus
        return ExecutorStatus(
            available=self.check_capability().available,
            state="busy" if self._inflight > 0 else "idle",
            inflight=self._inflight, concurrency=self.concurrency,
            current_task=None, queue_len=0)
