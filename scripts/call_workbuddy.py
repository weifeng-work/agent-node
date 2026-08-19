"""WorkBuddy 单项实调（双机验收最后一环）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.test_dual_machine import Panel, check, report


def main() -> int:
    p = Panel("http://127.0.0.1:5177")
    nodes = p.call("GET", "/api/nodes")["nodes"]
    peer = next(n for n in nodes if n.get("name") == "IKUN-PC")
    print("提交 WorkBuddy 任务（sync, 300s 超时）...")
    r = p.call("POST", "/api/task/submit", {
        "executor_id": f"{peer['nodeId']}/workbuddy",
        "prompt": "这是一次跨节点连通性验收测试。请直接回复一句话："
                  "「WorkBuddy 已收到 agent-node 双机任务，链路正常。」"
                  "然后按输出要求写入结果文件。",
        "mode": "sync", "timeout": 300})
    content = r.get("content") or ""
    check("WorkBuddy 实调取得回复", r.get("ok") is True and len(content) > 5,
          str(r)[:400])
    if content:
        print("=== WorkBuddy 回复 ===")
        print(content[:800])
    return report()


if __name__ == "__main__":
    sys.exit(main())
