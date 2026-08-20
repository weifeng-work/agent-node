"""CLI 工具（2.5.1 语义化子命令）—— 供不支持 MCP 但有终端能力的智能体调用。

子命令:
    register                     生成/读取 caller_id 身份文件
    list                         已知节点列表
    send --to <node_id> --text   发文本消息
    task --target <node_id> --executor <agent_id> --prompt "..." [--mode async]
         [--timeout 600] [--attach file1,file2] [--task-id <幂等键>]
    check --task <task_id>       查询任务结果
    inbox                        取异步邮箱回执（旧名；已更名 mailbox）
    mailbox                      取异步邮箱回执
    upload --to <node_id> --file <本地路径> [--target <目标路径>]
    download --from <node_id> --path <远程路径>   （拉到本机收件目录）
    ls --node <node_id> --path <路径> [--recursive]
    shell --to <node_id> --cmd "command"
    sync                         触发同步
    diag                         一键健康自检（2.17.7）

环境变量:
    AGENT_NODE_PANEL   面板地址（默认 http://127.0.0.1:5177）
    AGENT_NODE_CALLER_ID  caller_id（缺失时用 ~/.config/agent-node/caller.json）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PANEL = os.environ.get("AGENT_NODE_PANEL", "http://127.0.0.1:5177")
CALLER_FILE = Path.home() / ".config" / "agent-node" / "caller.json"


def caller_id() -> str:
    env = os.environ.get("AGENT_NODE_CALLER_ID")
    if env:
        return env
    if CALLER_FILE.exists():
        try:
            return json.loads(CALLER_FILE.read_text(encoding="utf-8"))["caller_id"]
        except Exception:
            pass
    print("caller_id 未配置。请先运行: cli.py register", file=sys.stderr)
    sys.exit(2)


def call(method: str, path: str, body: dict | None = None,
         timeout: float = 700.0) -> dict:
    url = PANEL.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Caller-Id", caller_id())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        print(f"[错误] 节点面板不可达（{PANEL}）: {e}", file=sys.stderr)
        sys.exit(1)


def out(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main() -> int:
    p = argparse.ArgumentParser(prog="cli.py", description="agent-node CLI 接入工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("register", help="生成/读取 caller_id 身份")
    sub.add_parser("list", help="已知节点列表")
    sp = sub.add_parser("send")
    sp.add_argument("--to", required=True)
    sp.add_argument("--text", required=True)
    sp = sub.add_parser("task")
    sp.add_argument("--target", default="", help="目标 node_id（空=本机）")
    sp.add_argument("--executor", required=True, help="agent_id 或 executor_id")
    sp.add_argument("--prompt", required=True)
    sp.add_argument("--mode", default="async", choices=["sync", "async", "trigger"])
    sp.add_argument("--timeout", type=float, default=600)
    sp.add_argument("--attach", default="", help="附件本地路径，逗号分隔")
    sp.add_argument("--task-id", default=None, help="幂等键（重试复用）")
    sp = sub.add_parser("check")
    sp.add_argument("--task", required=True)
    sp = sub.add_parser("inbox", help="取异步邮箱回执（旧名，同 mailbox）")
    sub.add_parser("mailbox", help="取异步邮箱回执")
    sp = sub.add_parser("upload")
    sp.add_argument("--to", default="")
    sp.add_argument("--file", required=True)
    sp.add_argument("--target", default=None)
    sp = sub.add_parser("download")
    sp.add_argument("--from", dest="frm", required=True)
    sp.add_argument("--path", required=True)
    sp = sub.add_parser("push-dir", help="整目录树推送（逐文件保留相对路径，免打包）")
    sp.add_argument("--root", required=True, help="本机要发送的目录")
    sp.add_argument("--to", default="", help="目标 node_id（空=本机）")
    sp.add_argument("--target", default="inbox", help="远端基础路径（默认 inbox）")
    sp = sub.add_parser("ls")
    sp.add_argument("--node", default="")
    sp.add_argument("--path", default="")
    sp.add_argument("--recursive", action="store_true")
    sp = sub.add_parser("shell")
    sp.add_argument("--to", default="")
    sp.add_argument("--command", "--cmd", dest="command", required=True)
    sp.add_argument("--timeout", type=float, default=60)
    sp = sub.add_parser("node-update", help="远程更新目标节点的代码（发 agent-node update）")
    sp.add_argument("--node", required=True, help="目标节点 node_id")
    sp.add_argument("--timeout", type=float, default=300)
    sub.add_parser("sync")
    sub.add_parser("diag")
    sp = sub.add_parser("executors", help="执行器列表")

    a = p.parse_args()
    if a.cmd == "register":
        import uuid
        CALLER_FILE.parent.mkdir(parents=True, exist_ok=True)
        if CALLER_FILE.exists():
            cid = json.loads(CALLER_FILE.read_text(encoding="utf-8"))["caller_id"]
            print(f"已有 caller_id: {cid}")
        else:
            cid = str(uuid.uuid4())
            CALLER_FILE.write_text(json.dumps({"caller_id": cid}), encoding="utf-8")
            print(f"已生成 caller_id: {cid}\n文件: {CALLER_FILE}")
        return 0
    if a.cmd == "list":
        out(call("GET", "/api/nodes"))
    elif a.cmd == "send":
        out(call("POST", "/api/chat/send",
                 {"target_node_id": a.to, "text": a.text}))
    elif a.cmd == "task":
        executor_id = a.executor
        if a.target and "/" not in executor_id:
            executor_id = f"{a.target}/{a.executor}"
        atts = [s.strip() for s in a.attach.split(",") if s.strip()]
        out(call("POST", "/api/task/submit", {
            "executor_id": executor_id, "prompt": a.prompt, "mode": a.mode,
            "timeout": a.timeout, "attachments": atts or None, "task_id": a.task_id}))
    elif a.cmd == "check":
        out(call("GET", f"/api/task/result?task_id={urllib.parse.quote(a.task)}"))
    elif a.cmd in ("inbox", "mailbox"):
        out(call("GET", "/api/mailbox"))
    elif a.cmd == "upload":
        out(call("POST", "/api/files/push", {
            "node_id": a.to or None, "local_path": a.file, "target_path": a.target}))
    elif a.cmd == "download":
        out(call("POST", "/api/files/pull",
                 {"node_id": a.frm or None, "path": a.path}))
    elif a.cmd == "push-dir":
        out(call("POST", "/api/files/push_dir", {
            "node_id": a.to or None, "local_root": a.root,
            "target_base": a.target}))
    elif a.cmd == "ls":
        q = (f"?node_id={urllib.parse.quote(a.node)}&path={urllib.parse.quote(a.path)}"
             f"&recursive={'true' if a.recursive else 'false'}")
        out(call("GET", "/api/files/list" + q))
    elif a.cmd == "shell":
        out(call("POST", "/api/shell", {
            "target_node_id": a.to or None, "command": a.command,
            "timeout": a.timeout}))
    elif a.cmd == "node-update":
        # 远程更新目标节点代码：经 shell_exec 调对端 agent-node update
        r = call("POST", "/api/shell", {
            "target_node_id": a.node, "command": "agent-node update",
            "timeout": a.timeout})
        out(r)
        if r.get("ok") or (r.get("output") or "").strip():
            print("\n更新命令已送达，目标节点将拉取 npm 最新代码并重部署。")
            print("生效需目标节点手动执行: agent-node restart")
    elif a.cmd == "sync":
        out(call("POST", "/api/sync/now", {}))
    elif a.cmd == "diag":
        out(call("GET", "/api/diag"))
    elif a.cmd == "executors":
        out(call("GET", "/api/executors"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
