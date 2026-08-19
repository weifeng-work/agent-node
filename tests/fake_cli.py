"""假 CLI（测试桩，设计第五章 #4）—— 模拟非交互 CLI 智能体。

读 stdin 全文为提示词 → 延迟数秒 → 按结果文件契约写 result.md → 退出。
用于在不依赖真实智能体的情况下测试 CLI 执行器基座与完整任务链路。
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    prompt = sys.stdin.read()
    time.sleep(2.0)  # 模拟执行耗时
    content = (
        f"# 假 CLI 执行结果\n\n"
        f"- 时间: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
        f"- 收到提示词 {len(prompt)} 字符:\n\n"
        f"```\n{prompt[:1000]}\n```\n\n"
        f"[fake-cli] 任务已执行完毕，本回复写入 result.md。\n"
    )
    result = Path("result.md")
    tmp = result.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(result)
    print(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
