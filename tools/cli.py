"""CLI 工具（2.5.1 语义化子命令）—— 供不支持 MCP 但有终端能力的智能体调用。

子命令:
    register                     生成/读取 caller_id 身份文件
    list                         已知节点列表
    send --to <node_id> --text   发文本消息
    task --target <node_id> --executor <agent_id> --prompt "..." [--mode async]
         [--timeout 600] [--attach file1,file2] [--task-id <幂等键>]
    check --task <task_id>       查询任务结果
    inbox                        取异步邮箱回执（旧名，同 mailbox）
    mailbox                      取异步邮箱回执
    mailbox-all [--limit]        邮箱全量（监控视角）
    mailbox-clean --mode consumed|expired [--before]  清理邮箱
    upload --to <node_id> --file <本地路径> [--target <目标路径>]
    download --from <node_id> --path <远程路径>   （拉到本机收件目录）
    ls --node <node_id> --path <路径> [--recursive]
    shell --to <node_id> --cmd "command"
    anchor list | add <host> [port] | remove <host>   # 锚点
    sync                         触发同步
    diag                         一键健康自检（2.17.7）
    info                         本机节点概览
    config                       读本机配置
    rename <name>                节点改名
    team <team_id>               设置/切换 team
    switch <allow_shell|allow_file|allow_ai_task> <on|off>  三开关
    admin <on|off>               管理员权限
    peer add <host> <port>       手动加对端
    peer remove <host>           手动删对端
    conversations                会话列表
    history --peer <node_id> [--limit]  会话历史
    executor status <id>         执行器深态
    executor suspend <id> --on|--off [--reason] [--until]  挂起/恢复
    executor restart <id>        重启插件
    executor rename <id> <name>  执行器改名
    executor sessions [<id>]     交互式会话列表
    comm-log [--peer] [--direction] [--type] [--corr] [--limit]  通信日志
    node-log [--source] [--lines] [--level]  节点运行日志
    plugins                      列出磁盘插件
    plugin push --node <id> --path <file>  分发插件

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


def main(argv: list[str] | None = None) -> int:
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
    sp = sub.add_parser("mailbox-all", help="邮箱全量（监控视角）")
    sp.add_argument("--limit", type=int, default=None, help="限制条数")
    sp = sub.add_parser("mailbox-clean", help="清理邮箱")
    sp.add_argument("--mode", required=True, choices=["consumed", "expired"])
    sp.add_argument("--before", default=None, help="时间戳/ISO 时间")
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
    sp = sub.add_parser("anchor", help="锚点管理（被 AP 隔离节点主动出站回连）")
    asp = sp.add_subparsers(dest="action", required=True)
    asp.add_parser("list", help="查看本机锚点")
    sp_add = asp.add_parser("add")
    sp_add.add_argument("host")
    sp_add.add_argument("port", type=int, default=0, nargs="?")
    sp_rm = asp.add_parser("remove")
    sp_rm.add_argument("host")
    sub.add_parser("sync")
    sub.add_parser("diag")
    sp = sub.add_parser("executors", help="执行器列表")

    # === 新增：本机 ===
    sub.add_parser("info", help="本机节点概览")
    sub.add_parser("config", help="读本机配置")
    sp = sub.add_parser("rename")
    sp.add_argument("name")
    sp = sub.add_parser("team")
    sp.add_argument("team_id")
    sp = sub.add_parser("switch")
    sp.add_argument("name", choices=["allow_shell", "allow_file", "allow_ai_task"])
    sp.add_argument("value", choices=["on", "off"])
    sp = sub.add_parser("admin")
    sp.add_argument("value", choices=["on", "off"])

    # === 新增：节点(peer) ===
    sp = sub.add_parser("peer", help="对端管理")
    psp = sp.add_subparsers(dest="action", required=True)
    sp_add = psp.add_parser("add")
    sp_add.add_argument("host")
    sp_add.add_argument("port", type=int, help="对端 TCP 端口")
    sp_rm = psp.add_parser("remove")
    sp_rm.add_argument("host")

    # === 新增：聊天 ===
    sub.add_parser("conversations", help="会话列表")
    sp = sub.add_parser("history")
    sp.add_argument("--peer", required=True)
    sp.add_argument("--limit", type=int, default=None)

    # === 新增：执行器 ===
    sp = sub.add_parser("executor", help="执行器管理")
    esp = sp.add_subparsers(dest="action", required=True)
    sp_st = esp.add_parser("status")
    sp_st.add_argument("id")
    sp_su = esp.add_parser("suspend")
    sp_su.add_argument("id")
    sp_su.add_argument("--on", dest="suspend_on", action="store_true",
                      help="挂起执行器")
    sp_su.add_argument("--off", dest="suspend_off", action="store_true",
                      help="恢复执行器")
    sp_su.add_argument("--reason", default=None)
    sp_su.add_argument("--until", default=None)
    sp_re = esp.add_parser("restart")
    sp_re.add_argument("id")
    sp_rn = esp.add_parser("rename")
    sp_rn.add_argument("id")
    sp_rn.add_argument("name")
    sp_se = esp.add_parser("sessions")
    sp_se.add_argument("id", nargs="?", default=None)

    # === 新增：日志 ===
    sp = sub.add_parser("comm-log", help="通信日志")
    sp.add_argument("--peer", default=None)
    sp.add_argument("--direction", default=None)
    sp.add_argument("--type", default=None)
    sp.add_argument("--corr", default=None)
    sp.add_argument("--limit", type=int, default=None)
    sp = sub.add_parser("node-log", help="节点运行日志")
    sp.add_argument("--source", default=None, choices=["node", "executor", "syncthing"])
    sp.add_argument("--lines", type=int, default=None)
    sp.add_argument("--level", default=None)

    # === 新增：插件 ===
    sub.add_parser("plugins", help="列出磁盘插件")
    sp = sub.add_parser("plugin", help="插件管理")
    plsp = sp.add_subparsers(dest="action", required=True)
    sp_pp = plsp.add_parser("push")
    sp_pp.add_argument("--node", required=True)
    sp_pp.add_argument("--path", required=True)

    a = p.parse_args(argv if argv is not None else None)

    # ─── 已有命令 ───
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
    elif a.cmd == "mailbox-all":
        q = f"?limit={a.limit}" if a.limit else ""
        out(call("GET", "/api/mailbox/all" + q))
    elif a.cmd == "mailbox-clean":
        body = {"mode": a.mode}
        if a.before:
            body["before"] = a.before
        out(call("POST", "/api/mailbox/cleanup", body))
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
        r = call("POST", "/api/shell", {
            "target_node_id": a.node, "command": "agent-node update",
            "timeout": a.timeout})
        out(r)
        if r.get("ok"):
            print("\n更新命令已送达，目标节点将拉取最新代码并重部署。")
            print("生效需目标节点手动执行: agent-node restart")
        else:
            print("\n更新命令执行失败，请检查目标节点状况。")
    elif a.cmd == "anchor":
        if a.action == "list":
            out(call("GET", "/api/anchors"))
        elif a.action == "add":
            out(call("POST", "/api/anchors/add",
                     {"host": a.host, "peer_tcp_port": a.port}))
        elif a.action == "remove":
            out(call("POST", "/api/anchors/remove", {"host": a.host}))
    elif a.cmd == "sync":
        out(call("POST", "/api/sync/now", {}))
    elif a.cmd == "diag":
        out(call("GET", "/api/diag"))
    elif a.cmd == "executors":
        out(call("GET", "/api/executors"))

    # ─── 新增：本机 ───
    elif a.cmd == "info":
        out(call("GET", "/api/overview"))
    elif a.cmd == "config":
        out(call("GET", "/api/settings"))
    elif a.cmd == "rename":
        out(call("POST", "/api/settings/name", {"name": a.name}))
    elif a.cmd == "team":
        out(call("POST", "/api/settings/team", {"team_id": a.team_id}))
    elif a.cmd == "switch":
        out(call("POST", "/api/settings/switch",
                 {"switch": a.name, "enabled": a.value == "on"}))
    elif a.cmd == "admin":
        out(call("POST", "/api/settings/admin", {"enabled": a.value == "on"}))

    # ─── 新增：节点(peer) ───
    elif a.cmd == "peer":
        if a.action == "add":
            out(call("POST", "/api/peers/add_manual",
                     {"host": a.host, "peer_tcp_port": a.port}))
        elif a.action == "remove":
            out(call("POST", "/api/peers/remove_manual", {"host": a.host}))

    # ─── 新增：聊天 ───
    elif a.cmd == "conversations":
        out(call("GET", "/api/chat/conversations"))
    elif a.cmd == "history":
        q = f"?peer={urllib.parse.quote(a.peer)}"
        if a.limit:
            q += f"&limit={a.limit}"
        out(call("GET", "/api/chat/history" + q))

    # ─── 新增：执行器 ───
    elif a.cmd == "executor":
        if a.action == "status":
            out(call("GET", f"/api/executors/status?executor_id={urllib.parse.quote(a.id)}"))
        elif a.action == "suspend":
            if a.suspend_on and a.suspend_off:
                print("[错误] --on 与 --off 不能同时使用", file=sys.stderr)
                return 1
            if not a.suspend_on and not a.suspend_off:
                print("[错误] 必须指定 --on 或 --off", file=sys.stderr)
                return 1
            body = {"executor_id": a.id, "suspend": a.suspend_on}
            if a.reason:
                body["reason"] = a.reason
            if a.until:
                body["until"] = a.until
            out(call("POST", "/api/executors/suspend", body))
        elif a.action == "restart":
            out(call("POST", "/api/executors/restart", {"executor_id": a.id}))
        elif a.action == "rename":
            out(call("POST", "/api/executors/rename",
                     {"executor_id": a.id, "new_name": a.name}))
        elif a.action == "sessions":
            url = "/api/executors/sessions"
            if a.id:
                url += f"?executor_id={urllib.parse.quote(a.id)}"
            out(call("GET", url))

    # ─── 新增：日志 ───
    elif a.cmd == "comm-log":
        params = {}
        if a.peer: params["peer"] = a.peer
        if a.direction: params["direction"] = a.direction
        if a.type: params["type"] = a.type
        if a.corr: params["correlation_id"] = a.corr
        if a.limit: params["limit"] = a.limit
        q = "?" + urllib.parse.urlencode(params) if params else ""
        out(call("GET", "/api/logs/comm" + q))
    elif a.cmd == "node-log":
        params = {}
        if a.source: params["source"] = a.source
        if a.lines: params["lines"] = a.lines
        if a.level: params["level"] = a.level
        q = "?" + urllib.parse.urlencode(params) if params else ""
        out(call("GET", "/api/logs/node" + q))

    # ─── 新增：插件 ───
    elif a.cmd == "plugins":
        out(call("GET", "/api/plugins"))
    elif a.cmd == "plugin":
        if a.action == "push":
            out(call("POST", "/api/plugins/distribute", {
                "target_node_id": a.node, "plugin_path": a.path}))

    return 0


if __name__ == "__main__":
    sys.exit(main())