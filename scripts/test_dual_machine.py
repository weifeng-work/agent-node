"""双机连通验收测试（第五章验收策略——真实双机环境）。

在本机运行（默认面板 http://127.0.0.1:5177），对 IKUN 节点执行全能力面验收:
发现/连接、聊天、远程命令、文件 push/pull/list、执行器深态、任务三模式、
WorkBuddy 实调（--with-workbuddy 开启）。

用法:
    python scripts/test_dual_machine.py [--panel http://127.0.0.1:5177]
                                        [--peer-name IKUN-PC]
                                        [--with-workbuddy]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

PASS, FAIL = 0, 0
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    global PASS, FAIL
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f"  --  {detail}" if detail and not ok else
                                  (f"  --  {detail}" if detail and ok else "")))
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")
    return ok


class Panel:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, body: dict | None = None,
             timeout: float = 400.0) -> dict:
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Caller-Id", "dual-test")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="http://127.0.0.1:5177")
    ap.add_argument("--peer-name", default="IKUN-PC")
    ap.add_argument("--with-workbuddy", action="store_true")
    args = ap.parse_args()
    p = Panel(args.panel)

    print("=" * 62)
    print("agent-node 双机连通验收测试")
    print("=" * 62)

    # 1. 本机健康 + 概览
    health = p.call("GET", "/api/health")
    check("本机面板健康", health.get("ok") is True)
    ov = p.call("GET", "/api/overview")
    my_node_id = ov["nodeId"]
    check("本机概览", ov.get("name") == "Admin-PC" and ov.get("peerTcpPort") == 41840,
          f"name={ov.get('name')} port={ov.get('peerTcpPort')}")

    # 2. 发现 IKUN 节点（beacon 自动发现 + mesh 连接）
    nodes = p.call("GET", "/api/nodes")["nodes"]
    peer = next((n for n in nodes if n.get("name") == args.peer_name), None)
    if not check("发现对端节点（beacon 自动发现）", peer is not None,
                 f"已知节点: {[n.get('name') for n in nodes]}"):
        return report()
    peer_id = peer["nodeId"]
    deadline = time.time() + 30
    while time.time() < deadline:
        nodes = p.call("GET", "/api/nodes")["nodes"]
        peer = next(n for n in nodes if n["nodeId"] == peer_id)
        if peer["online"] and peer.get("connected"):
            break
        time.sleep(1)
    check("对端在线 + mesh 已连接", peer["online"] and peer.get("connected"),
          f"online={peer['online']} connected={peer.get('connected')}")
    check("对端能力广播含 workbuddy",
          any(c.get("agentId") == "workbuddy" for c in peer.get("capabilities") or []),
          str(peer.get("capabilities")))

    # 3. 聊天（2.8）
    r = p.call("POST", "/api/chat/send",
               {"target_node_id": peer_id, "text": "双机验收测试: hello from Admin-PC"})
    check("聊天发送（仅在线可发）", r.get("ok") is True, str(r))
    # 从 IKUN 面板验证收到（经远程 shell 回读其本地面板）
    r = p.call("POST", "/api/shell", {
        "target_node_id": peer_id,
        "command": ("powershell -NoProfile -Command \"(Invoke-RestMethod "
                    "'http://127.0.0.1:5177/api/chat/history?peer="
                    + urllib.parse.quote(my_node_id) + "').messages.Count\""),
        "timeout": 30})
    check("对端已收到聊天并落库",
          r.get("ok") is True and r.get("output", "").strip() not in ("", "0"),
          f"output={r.get('output')!r} err={r.get('error')}")

    # 4. 远程命令（2.16.4 shell_exec）
    r = p.call("POST", "/api/shell", {"target_node_id": peer_id,
                                      "command": "echo dual-shell-ok", "timeout": 20})
    check("远程命令执行", r.get("ok") is True and "dual-shell-ok" in r.get("output", ""),
          str(r))

    # 5. 文件 push（2.4.2 任意路径 + 默认收件目录）
    src = Path("data/dualtest_send.txt")
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("dual machine file push test 12345", encoding="utf-8")
    r = p.call("POST", "/api/files/push", {"node_id": peer_id,
                                           "local_path": str(src),
                                           "target_path": "inbox/dualtest_send.txt"})
    check("文件 push 到对端收件目录", r.get("ok") is True, str(r))
    r = p.call("POST", "/api/shell", {
        "target_node_id": peer_id,
        "command": "type C:\\Users\\IKUN\\agent-node\\data\\inbox\\dualtest_send.txt",
        "timeout": 20})
    check("对端文件内容一致（SHA 由协议校验，此处回读验证）",
          r.get("ok") is True and "dual machine file push test 12345" in r.get("output", ""),
          str(r.get("output"))[:100])

    # 6. list_dir 远程（2.4.4）
    r = p.call("GET", "/api/files/list?node_id=" + urllib.parse.quote(peer_id)
               + "&path=" + urllib.parse.quote("C:\\Users\\IKUN\\agent-node\\data\\inbox"))
    check("远程目录列表", r.get("ok") is True
          and any(e["name"] == "dualtest_send.txt" for e in r.get("entries") or []),
          str(r)[:150])

    # 7. 文件 pull（2.4.3）
    r = p.call("POST", "/api/files/pull", {
        "node_id": peer_id,
        "path": "C:\\Users\\IKUN\\agent-node\\data\\inbox\\dualtest_send.txt"})
    check("文件 pull 回本机收件目录", r.get("ok") is True, str(r))
    pulled = Path("data/inbox/dualtest_send.txt")
    check("pull 文件内容一致",
          pulled.exists() and pulled.read_text(encoding="utf-8") == src.read_text(encoding="utf-8"))

    # 8. 执行器深态（远程 executor_status，受 allow_ai_task）—— 远程返回 {ok, executors:[...]}
    r = p.call("GET", "/api/executors/status?executor_id="
               + urllib.parse.quote(f"{peer_id}/workbuddy"))
    wb = next((e for e in r.get("executors") or []
               if e.get("agentId") == "workbuddy"), None)
    check("远程执行器深态查询（workbuddy）",
          r.get("ok") is True and wb is not None and wb.get("available") is True,
          str(r)[:150])

    # 9. 远程任务 sync 模式（mock）
    r = p.call("POST", "/api/task/submit", {
        "executor_id": f"{peer_id}/mock", "prompt": "双机验收: sync 模式任务",
        "mode": "sync", "timeout": 120})
    check("远程任务 sync（mock）", r.get("ok") is True
          and "Mock 执行结果" in (r.get("content") or ""), str(r)[:200])

    # 10. 远程任务 async 模式 → 本机 caller 邮箱
    r = p.call("POST", "/api/task/submit", {
        "executor_id": f"{peer_id}/mock", "prompt": "双机验收: async 模式任务",
        "mode": "async", "timeout": 120})
    check("远程任务 async 受理", r.get("ok") is True, str(r))
    if r.get("ok"):
        got = []
        deadline = time.time() + 60
        while time.time() < deadline:
            items = p.call("GET", "/api/inbox")["items"]
            got = [i for i in items if i.get("correlation_id") == r.get("taskId")]
            if got:
                break
            time.sleep(2)
        check("异步回执进本机邮箱（caller=dual-test）",
              bool(got) and got[0]["content"].get("ok") is True, str(got)[:200])

    # 11. 触发型
    r = p.call("POST", "/api/task/submit", {
        "executor_id": f"{peer_id}/mock", "prompt": "双机验收: trigger",
        "mode": "trigger", "timeout": 60})
    check("远程任务 trigger（触发确认）",
          r.get("ok") is True and r.get("triggered") is True, str(r))

    # 12. 附件直传（2.13.1）
    att = Path("data/dualtest_att.txt")
    att.write_text("attachment content XYZ-999", encoding="utf-8")
    r = p.call("POST", "/api/task/submit", {
        "executor_id": f"{peer_id}/mock", "prompt": "双机验收: 附件任务",
        "mode": "sync", "timeout": 120, "attachments": [str(att)]})
    check("带附件远程任务（附件直传到工作目录）",
          r.get("ok") is True and "附件: 1 个" in (r.get("content") or ""),
          str(r)[:200])

    # 13. 幂等（2.13.5 应用层 task_id）
    tid = "dual-idempotent-0001"
    r1 = p.call("POST", "/api/task/submit", {
        "executor_id": f"{peer_id}/mock", "prompt": "幂等任务", "mode": "sync",
        "timeout": 120, "task_id": tid})
    r2 = p.call("POST", "/api/task/submit", {
        "executor_id": f"{peer_id}/mock", "prompt": "幂等任务重发", "mode": "sync",
        "timeout": 120, "task_id": tid})
    check("task_id 幂等（重发不重复执行）",
          r1.get("ok") is True and r2.get("taskId") == tid and r2.get("ok") is not None,
          f"r1={r1.get('ok')} r2={r2}")

    # 14. 通信日志（2.3）
    logs = p.call("GET", "/api/logs/comm?limit=50")
    check("通信日志记录双方交互", len(logs.get("entries") or []) >= 5,
          f"entries={len(logs.get('entries') or [])}")

    # 15. diag
    d = p.call("GET", "/api/diag")
    check("本机 diag 自检", d.get("ok") is True, str(d.get("checks"))[:200])

    # 16. WorkBuddy 实调（执行器验收，2.2.8 结果文件契约）
    if args.with_workbuddy:
        print("\n--- WorkBuddy 实调（注入 → 执行 → 结果回收）---")
        r = p.call("POST", "/api/task/submit", {
            "executor_id": f"{peer_id}/workbuddy",
            "prompt": "这是一次跨节点连通性验收测试。请直接回复一句话："
                      "「WorkBuddy 已收到 agent-node 双机任务，链路正常。」"
                      "然后按输出要求写入结果文件。",
            "mode": "sync", "timeout": 300})
        content = r.get("content") or ""
        check("WorkBuddy 实调取得回复", r.get("ok") is True and len(content) > 5,
              f"r={str(r)[:300]}")
        if content:
            print(f"    WorkBuddy 回复（前 500 字）:\n    {content[:500]}")

    return report()


def report() -> int:
    print("\n" + "=" * 62)
    print(f"结果: {PASS} 通过 / {FAIL} 失败")
    if FAILURES:
        print("失败项:")
        for f in FAILURES:
            print(f"  - {f}")
    print("=" * 62)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
