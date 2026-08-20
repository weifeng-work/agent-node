"""本机 WorkBuddy 执行器实调（用户验证项 #4）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.test_dual_machine import Panel, check, report


def main() -> int:
    p = Panel("http://127.0.0.1:5177")
    # 本机执行器深态（确认 available）
    st = p.call("GET", "/api/executors/status?executor_id=workbuddy")
    status = st.get("status") or {}
    check("本机 workbuddy 执行器可用", st.get("ok") is True
          and status.get("available") is True, str(st)[:200])
    print("提交本机 WorkBuddy 任务（sync, 300s）...")
    r = p.call("POST", "/api/task/submit", {
        "executor_id": "workbuddy",
        "prompt": "这是一次本机执行器链路测试。请直接回复一句话："
                  "「本机 WorkBuddy 执行器链路正常。」然后按输出要求写入结果文件。",
        "mode": "sync", "timeout": 300})
    content = r.get("content") or ""
    check("本机 WorkBuddy 实调取得回复", r.get("ok") is True and len(content) > 5,
          str(r)[:400])
    if content:
        print("=== 本机 WorkBuddy 回复 ===")
        print(content[:600])
    return report()


if __name__ == "__main__":
    sys.exit(main())
