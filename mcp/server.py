"""MCP Server —— stdio 薄转发桥（2.5.1 / 2.6.3 / 2.10.4）。

- 由 AI 客户端以 stdio 子进程拉起；仅参数校验/语义组装/结果回传
- 实际执行在节点核心进程（经面板 REST 127.0.0.1 回环转发）
- caller_id = MCP server 进程身份（2.6.3/2.6.4）:
    首选环境变量 AGENT_NODE_CALLER_ID；缺失时首启生成 UUID 写入
    ~/.config/agent-node/caller.json 并复用
- 工具面 = 2.5.9 ①~⑧（能力对等映射 2.5.7；语义化，不拼裸报文 2.5.8）

用法（MCP 客户端 JSON 配置）:
    {"command": "python", "args": ["-m", "mcp.server"],
     "env": {"AGENT_NODE_CALLER_ID": "my-ai-1", "AGENT_NODE_PANEL": "http://127.0.0.1:5177"}}
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

PANEL_BASE = os.environ.get("AGENT_NODE_PANEL", "http://127.0.0.1:5177")
CALLER_ID_FILE = Path.home() / ".config" / "agent-node" / "caller.json"
PROTOCOL_VERSION = "2024-11-05"


def get_caller_id() -> str:
    env = os.environ.get("AGENT_NODE_CALLER_ID")
    if env:
        return env
    try:
        if CALLER_ID_FILE.exists():
            data = json.loads(CALLER_ID_FILE.read_text(encoding="utf-8"))
            if data.get("caller_id"):
                return data["caller_id"]
        CALLER_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        cid = str(uuid.uuid4())
        CALLER_ID_FILE.write_text(json.dumps({"caller_id": cid}, ensure_ascii=False),
                                  encoding="utf-8")
        return cid
    except Exception:
        return str(uuid.uuid4())


def panel_call(method: str, path: str, body: dict | None = None,
               timeout: float = 700.0) -> dict:
    """面板 REST 调用（X-Caller-Id 自动注入）。"""
    url = PANEL_BASE.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Caller-Id", get_caller_id())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "error": "agent_error", "detail": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": "offline",
                "detail": f"节点面板不可达（{PANEL_BASE}）: {e.reason}"}


# ================= 工具定义（2.5.9 ①~⑧） =================
def _s(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props,
            "required": required or []}


TOOLS = [
    # ① 本机状态
    {"name": "get_node_info", "description": "本机节点概览: node_id/名称/team/IP/端口/面板/三开关/在线状态",
     "inputSchema": _s({})},
    # ② 节点
    {"name": "list_nodes", "description": "已知节点列表（含离线不消失: node_id+名称+team+能力+在线状态+last_seen）",
     "inputSchema": _s({})},
    {"name": "forget_node", "description": "忘记节点（从已知列表移除）",
     "inputSchema": _s({"node_id": {"type": "string"}}, ["node_id"])},
    {"name": "add_manual_peer", "description": "手动指定节点加入（跨网段兜底，被加方需固定 peer_tcp_port）",
     "inputSchema": _s({"host": {"type": "string"}, "peer_tcp_port": {"type": "integer"}},
                       ["host", "peer_tcp_port"])},
    {"name": "remove_manual_peer", "description": "移除手动加入的对端地址",
     "inputSchema": _s({"host": {"type": "string"}}, ["host"])},
    # ③ 聊天
    {"name": "send_text", "description": "向目标节点发文本消息（仅在线可发；不受开关管辖）",
     "inputSchema": _s({"target_node": {"type": "string", "description": "目标 node_id"},
                        "text": {"type": "string"}, "session_id": {"type": "string"}},
                       ["target_node", "text"])},
    {"name": "list_conversations", "description": "聊天会话列表", "inputSchema": _s({})},
    {"name": "get_chat_history", "description": "聊天记录（仅本节点为一方的会话）",
     "inputSchema": _s({"target_node": {"type": "string"}, "limit": {"type": "integer"}},
                       ["target_node"])},
    # ④ 文件
    {"name": "list_dir", "description": "列目录（node_id 为目标节点；本机可传空）",
     "inputSchema": _s({"node_id": {"type": "string"}, "path": {"type": "string"},
                        "recursive": {"type": "boolean"}}, ["path"])},
    {"name": "file_push", "description": "推送文件到目标节点任意路径（默认统一收件目录）",
     "inputSchema": _s({"node_id": {"type": "string"}, "local_path": {"type": "string"},
                        "target_path": {"type": "string"}}, ["node_id", "local_path"])},
    {"name": "file_pull", "description": "从目标节点拉取文件/目录到本机收件目录（目录整树递归）",
     "inputSchema": _s({"node_id": {"type": "string"}, "path": {"type": "string"}},
                       ["node_id", "path"])},
    {"name": "sync_now", "description": "触发同步目录扫描（Syncthing）", "inputSchema": _s({})},
    {"name": "list_plugins", "description": "列出 data/plugins/ 磁盘插件文件",
     "inputSchema": _s({})},
    {"name": "distribute_plugin", "description": "分发/更新执行器插件到目标节点（推送文件+重扫+回执）",
     "inputSchema": _s({"target_node": {"type": "string"}, "plugin_path": {"type": "string"}},
                       ["target_node", "plugin_path"])},
    # ⑤ 执行器
    {"name": "list_executors", "description": "本机+远程执行器统一枚举（executor_id=<node_id>/<agent_id> 全局寻址）",
     "inputSchema": _s({})},
    {"name": "submit_task", "description": "统一调用执行器（透明路由本地/远程; task_id 可选幂等键，重试时复用）",
     "inputSchema": _s({"executor_id": {"type": "string"},
                        "prompt": {"type": "string"},
                        "mode": {"type": "string", "enum": ["sync", "async", "trigger"]},
                        "attachments": {"type": "array", "items": {"type": "string"}},
                        "timeout": {"type": "number"},
                        "task_id": {"type": "string"}},
                       ["executor_id", "prompt"])},
    {"name": "get_task_result", "description": "查询任务结果（含未完成的进行中状态）",
     "inputSchema": _s({"task_id": {"type": "string"}}, ["task_id"])},
    {"name": "check_inbox", "description": "取异步邮箱回执（caller_id 自动注入，只返回自己的）",
     "inputSchema": _s({})},
    {"name": "get_executor_status", "description": "读执行器深态（本机直接/远程深查，受 allow_ai_task）",
     "inputSchema": _s({"executor_id": {"type": "string"}}, ["executor_id"])},
    {"name": "set_executor_suspend", "description": "挂起/恢复本机执行器（可设原因与到期时间点）",
     "inputSchema": _s({"executor_id": {"type": "string"}, "suspend": {"type": "boolean"},
                        "reason": {"type": "string"}, "until": {"type": "string"}},
                       ["executor_id", "suspend"])},
    {"name": "restart_plugin", "description": "重启本机执行器插件",
     "inputSchema": _s({"executor_id": {"type": "string"}}, ["executor_id"])},
    {"name": "list_agent_sessions", "description": "交互式会话列表（v1 暂空）",
     "inputSchema": _s({"executor_id": {"type": "string"}})},
    # ⑥ 日志
    {"name": "get_comm_log", "description": "通信日志（可按对端/方向/类型/correlation 过滤）",
     "inputSchema": _s({"target_node": {"type": "string"}, "direction": {"type": "string"},
                        "type": {"type": "string"}, "correlation_id": {"type": "string"},
                        "limit": {"type": "integer"}})},
    {"name": "get_node_log", "description": "节点运行日志 node.log（source 可选 node/mcp/executor/syncthing）",
     "inputSchema": _s({"source": {"type": "string"}, "lines": {"type": "integer"},
                        "level": {"type": "string"}})},
    # ⑦ 设置（仅本机）
    {"name": "get_config", "description": "读本机配置", "inputSchema": _s({})},
    {"name": "rename_node", "description": "节点改名（仅本机可改）",
     "inputSchema": _s({"new_name": {"type": "string"}}, ["new_name"])},
    {"name": "rename_executor", "description": "执行器条目改名（仅本机）",
     "inputSchema": _s({"executor_id": {"type": "string"}, "new_name": {"type": "string"}},
                       ["executor_id", "new_name"])},
    {"name": "set_team", "description": "设置/切换 team（危险操作: 断开旧 team 连接）",
     "inputSchema": _s({"team_id": {"type": "string"}}, ["team_id"])},
    {"name": "set_control_state", "description": "三开关 allow_shell/allow_file/allow_ai_task",
     "inputSchema": _s({"switch": {"type": "string",
                                   "enum": ["allow_shell", "allow_file", "allow_ai_task"]},
                        "enabled": {"type": "boolean"}}, ["switch", "enabled"])},
    {"name": "set_run_as_admin", "description": "权限设置（需重启生效）",
     "inputSchema": _s({"enabled": {"type": "boolean"}}, ["enabled"])},
    {"name": "cleanup_inbox", "description": "清理收件箱（consumed=已读 / expired=未读）",
     "inputSchema": _s({"mode": {"type": "string", "enum": ["consumed", "expired"]},
                        "before": {"type": "string"}}, ["mode"])},
    # ⑧ 远程命令
    {"name": "shell_exec", "description": "执行命令（target_node=空/本机→直接执行; 远程→受目标 allow_shell 管辖）",
     "inputSchema": _s({"target_node": {"type": "string"}, "command": {"type": "string"},
                        "timeout": {"type": "number"}}, ["command"])},
]


def dispatch_tool(name: str, args: dict) -> str:
    a = args or {}
    if name == "get_node_info":
        r = panel_call("GET", "/api/overview")
    elif name == "list_nodes":
        r = panel_call("GET", "/api/nodes")
    elif name == "forget_node":
        r = panel_call("POST", "/api/nodes/forget", {"node_id": a["node_id"]})
    elif name == "add_manual_peer":
        r = panel_call("POST", "/api/peers/add_manual",
                       {"host": a["host"], "peer_tcp_port": a["peer_tcp_port"]})
    elif name == "remove_manual_peer":
        r = panel_call("POST", "/api/peers/remove_manual", {"host": a["host"]})
    elif name == "send_text":
        r = panel_call("POST", "/api/chat/send",
                       {"target_node_id": a["target_node"], "text": a["text"],
                        "session_id": a.get("session_id")})
    elif name == "list_conversations":
        r = panel_call("GET", "/api/chat/conversations")
    elif name == "get_chat_history":
        q = f"?peer={urllib.parse.quote(a['target_node'])}&limit={a.get('limit') or 200}"
        r = panel_call("GET", "/api/chat/history" + q)
    elif name == "list_dir":
        import urllib.parse as _u
        q = (f"?node_id={_u.quote(a.get('node_id') or '')}"
             f"&path={_u.quote(a.get('path') or '')}"
             f"&recursive={'true' if a.get('recursive') else 'false'}")
        r = panel_call("GET", "/api/files/list" + q)
    elif name == "file_push":
        r = panel_call("POST", "/api/files/push",
                       {"node_id": a.get("node_id") or None,
                        "local_path": a["local_path"],
                        "target_path": a.get("target_path")})
    elif name == "file_pull":
        r = _file_pull(a)
    elif name == "sync_now":
        r = panel_call("POST", "/api/sync/now", {})
    elif name == "list_plugins":
        r = panel_call("GET", "/api/plugins")
    elif name == "distribute_plugin":
        r = panel_call("POST", "/api/plugins/distribute",
                       {"target_node_id": a["target_node"], "plugin_path": a["plugin_path"]})
    elif name == "list_executors":
        r = panel_call("GET", "/api/executors")
    elif name == "submit_task":
        r = panel_call("POST", "/api/task/submit", {
            "executor_id": a["executor_id"], "prompt": a["prompt"],
            "mode": a.get("mode") or "async", "attachments": a.get("attachments"),
            "timeout": a.get("timeout") or 600, "task_id": a.get("task_id")})
    elif name == "get_task_result":
        r = panel_call("GET", f"/api/task/result?task_id={a['task_id']}")
    elif name == "check_inbox":
        r = panel_call("GET", "/api/inbox")
    elif name == "get_executor_status":
        r = panel_call("GET", f"/api/executors/status?executor_id="
                              f"{urllib.parse.quote(a['executor_id'])}")
    elif name == "set_executor_suspend":
        r = panel_call("POST", "/api/executors/suspend", {
            "executor_id": a["executor_id"], "suspend": a["suspend"],
            "reason": a.get("reason"), "until": a.get("until")})
    elif name == "restart_plugin":
        r = panel_call("POST", "/api/executors/restart", {"executor_id": a["executor_id"]})
    elif name == "list_agent_sessions":
        r = panel_call("GET", "/api/executors/sessions?executor_id="
                              f"{urllib.parse.quote(a.get('executor_id') or '')}")
    elif name == "get_comm_log":
        import urllib.parse as _u
        q = (f"?peer={_u.quote(a.get('target_node') or '')}"
             f"&direction={_u.quote(a.get('direction') or '')}"
             f"&type={_u.quote(a.get('type') or '')}"
             f"&correlation_id={_u.quote(a.get('correlation_id') or '')}"
             f"&limit={a.get('limit') or 200}")
        r = panel_call("GET", "/api/logs/comm" + q)
    elif name == "get_node_log":
        r = panel_call("GET", f"/api/logs/node?lines={a.get('lines') or 200}")
    elif name == "get_config":
        r = panel_call("GET", "/api/settings")
    elif name == "rename_node":
        r = panel_call("POST", "/api/settings/name", {"name": a["new_name"]})
    elif name == "rename_executor":
        r = panel_call("POST", "/api/executors/rename",
                       {"executor_id": a["executor_id"], "new_name": a["new_name"]})
    elif name == "set_team":
        r = panel_call("POST", "/api/settings/team", {"team_id": a["team_id"]})
    elif name == "set_control_state":
        r = panel_call("POST", "/api/settings/switch",
                       {"switch": a["switch"], "enabled": a["enabled"]})
    elif name == "set_run_as_admin":
        r = panel_call("GET", "/api/settings")  # admin 经配置文件（文档约束）
        r = {"ok": True, "detail": "run_as_admin 请人工修改 node_config.json 后重启（2.14.6）"}
    elif name == "cleanup_inbox":
        r = panel_call("POST", "/api/inbox/cleanup", {"mode": a["mode"],
                                                      "before": a.get("before")})
    elif name == "shell_exec":
        r = panel_call("POST", "/api/shell", {
            "target_node_id": a.get("target_node") or None,
            "command": a["command"], "timeout": a.get("timeout") or 60})
    else:
        r = {"ok": False, "error": "agent_error", "detail": f"unknown tool: {name}"}
    return json.dumps(r, ensure_ascii=False)


# file_pull: 拉取到本机统一收件目录（2.4.3；目录整树递归不经打包）
def _file_pull(a):
    return panel_call("POST", "/api/files/pull",
                      {"node_id": a.get("node_id") or None, "path": a["path"]})


# ================= MCP stdio JSON-RPC 主循环 =================
def handle_message(msg: dict) -> dict | None:
    method = msg.get("method")
    msg_id = msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "agent-node", "version": "1.0.0"}}}
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name") or ""
        args = params.get("arguments") or {}
        try:
            if name == "file_pull":
                out = _file_pull(args)
            else:
                out = dispatch_tool(name, args)
            return {"jsonrpc": "2.0", "id": msg_id, "result": {
                "content": [{"type": "text", "text": out}],
                "isError": False}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": msg_id, "result": {
                "content": [{"type": "text", "text": json.dumps(
                    {"ok": False, "error": "agent_error", "detail": str(e)},
                    ensure_ascii=False)}],
                "isError": True}}
    if msg_id is not None:
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None


def main() -> int:
    # MCP stdio: 每行一个 JSON-RPC 消息
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle_message(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
