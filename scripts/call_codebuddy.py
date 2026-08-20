"""CodeBuddy 执行器实测（非交互 + 交互式，本机弹窗可见）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.test_dual_machine import Panel, check, report


def main() -> int:
    p = Panel("http://127.0.0.1:5177")
    mode = sys.argv[1] if len(sys.argv) > 1 else "cli"
    agent = "codebuddy-cli" if mode == "cli" else "codebuddy-tui"
    st = p.call("GET", f"/api/executors/status?executor_id={agent}")
    status = st.get("status") or {}
    check(f"{agent} 执行器可用", st.get("ok") is True
          and status.get("available") is True, str(st)[:200])
    print(f"提交 {agent} 任务（sync, 300s）——注意观察桌面 psmux 弹窗...")
    r = p.call("POST", "/api/task/submit", {
        "executor_id": agent,
        "prompt": ("这是一次跨节点执行器链路验收测试。请直接回复一句话："
                   f"「{'非交互 CLI' if mode == 'cli' else '交互式 TUI'} "
                   "CodeBuddy 链路正常。」然后按输出要求把最终结论写入结果文件。"),
        "mode": "sync", "timeout": 300})
    content = r.get("content") or ""
    check(f"{agent} 实调取得回复", r.get("ok") is True and len(content) > 5,
          str(r)[:400])
    if content:
        print(f"=== {agent} 回复 ===")
        print(content[:600])
    return report()


if __name__ == "__main__":
    sys.exit(main())
