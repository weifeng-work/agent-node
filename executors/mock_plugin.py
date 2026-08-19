"""Mock 执行器（测试桩，设计第五章 #4）。

不依赖真实智能体即可独立测试执行器链路：submit 后延迟写入 result.md，
模拟「智能体执行任务并按结果文件契约回写结论」。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from .base import (CapabilityResult, ExecutorPlugin, ResultPayload, SubmitResult,
                   TaskInput)


class MockPlugin(ExecutorPlugin):
    plugin_id = "mock"
    display_name = "Mock 执行器"
    executor_type = "non_interactive_cli"
    concurrency = 3

    def __init__(self, ctx):
        super().__init__(ctx)
        self._inflight = 0
        self._lock = threading.Lock()

    def check_capability(self) -> CapabilityResult:
        # 测试桩永远可用
        return CapabilityResult(True, premises=["内置 mock 执行器（测试桩，无外部依赖）"])

    def submit(self, task: TaskInput) -> SubmitResult:
        with self._lock:
            self._inflight += 1
        threading.Thread(target=self._run, args=(task,), daemon=True,
                         name=f"mock-{task.task_id[:8]}").start()
        return SubmitResult(True)

    def _run(self, task: TaskInput) -> None:
        try:
            time.sleep(1.5)  # 模拟智能体执行耗时
            content = (
                f"# Mock 执行结果\n\n"
                f"- 任务: `{task.task_id}`\n"
                f"- 时间: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
                f"- 提示词: {task.prompt[:200]}\n"
                f"- 附件: {len(task.attachments)} 个\n\n"
                f"[mock] 任务已执行完毕。\n"
            )
            task.result_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = task.result_file.with_suffix(".tmp")
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(task.result_file)  # 原子写入（2.2.11）
        finally:
            with self._lock:
                self._inflight -= 1

    def get_result(self, task_id: str) -> ResultPayload:
        return super().get_result(task_id)
