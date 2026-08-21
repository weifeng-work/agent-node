"""Web 面板（2.9 / 第四章）—— FastAPI REST + 静态 SPA，绑定 127.0.0.1（2.1.5）。

- 默认端口 5177，被占则顺序 +1 兜底（2.1.5）；实际 URL 写 data/panel.url
- REST = 节点完整本地 API（MCP 薄桥与 CLI 共用，2.5.1/2.10.4）
- caller_id 经 X-Caller-Id 头传递（2.6.3）
"""
from __future__ import annotations

import socket
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

import node.config as cfg_mod

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(core) -> FastAPI:
    app = FastAPI(title="agent-node panel", docs_url=None, redoc_url=None)

    def caller_id(request: Request) -> str:
        return request.headers.get("X-Caller-Id") or "panel"

    # ---------- 静态 SPA ----------
    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    def health():
        return {"ok": True, "nodeId": core.node_id}

    # ---------- 概览（2.9.3） ----------
    @app.get("/api/overview")
    def overview():
        return core.overview()

    # ---------- 节点（2.1.9 / 2.1.10） ----------
    @app.get("/api/nodes")
    def nodes():
        return {"ok": True, "nodes": core.list_nodes()}

    @app.post("/api/nodes/forget")
    async def forget_node(body: dict):
        return core.forget_node(body.get("node_id") or "")

    @app.post("/api/nodes/purge")
    async def purge_node(body: dict):
        return core.purge_node(body.get("node_id") or "")

    @app.post("/api/peers/add_manual")
    async def add_manual(body: dict):
        return core.add_manual_peer(body.get("host") or "",
                                    int(body.get("peer_tcp_port") or 0))

    @app.post("/api/peers/remove_manual")
    async def remove_manual(body: dict):
        return core.remove_manual_peer(body.get("host") or "")

    # ---------- 锚点（2.18: 被隔离方向主动出站回连） ----------
    @app.get("/api/anchors")
    def anchors():
        return {"ok": True, "anchors": core.config.peer_anchors}

    @app.post("/api/anchors/add")
    async def anchor_add(body: dict):
        return core.add_anchor(body.get("host") or "",
                               int(body.get("peer_tcp_port") or 0))

    @app.post("/api/anchors/remove")
    async def anchor_remove(body: dict):
        return core.remove_anchor(body.get("host") or "")

    # ---------- 聊天（2.8） ----------
    @app.get("/api/chat/conversations")
    def conversations():
        return {"ok": True, "conversations": core.conversations()}

    @app.get("/api/chat/history")
    def chat_history(peer: str, limit: int = 200):
        return {"ok": True, "messages": core.chat_history(peer, limit)}

    @app.post("/api/chat/send")
    async def chat_send(body: dict):
        return core.send_chat(body.get("target_node_id") or "",
                              body.get("text") or "", body.get("session_id"))

    # ---------- 文件（2.4 / 2.4.6） ----------
    @app.get("/api/files/list")
    def files_list(node_id: str = "", path: str = "", recursive: bool = False):
        return core.list_dir(node_id or None, path, recursive)

    @app.post("/api/files/push")
    async def files_push(body: dict):
        return core.file_push(body.get("node_id") or None,
                              body.get("local_path") or "",
                              body.get("target_path"))

    @app.post("/api/files/pull")
    async def files_pull(body: dict):
        return core.file_pull(body.get("node_id") or None,
                              body.get("path") or "")

    @app.post("/api/files/push_dir")
    async def files_push_dir(body: dict):
        return core.push_dir(body.get("node_id") or None,
                             body.get("local_root") or "",
                             body.get("target_base") or "inbox")

    @app.get("/api/files/download")
    def files_download(node_id: str, path: str):
        """面板下载: pull 到本机收件目录后返回内容（单文件流式）。"""
        import urllib.parse
        result = core.file_pull(node_id, path)
        if not result.get("ok"):
            return JSONResponse(result, status_code=200)
        return FileResponse(result["path"], filename=Path(path).name)

    @app.post("/api/files/upload")
    async def files_upload(request: Request, node_id: str = "", target_dir: str = "",
                           rel: str = ""):
        """面板推送（2.4.6）: 浏览器文件/文件夹选择器 → 当前浏览目录。
        rel = 文件相对路径（文件夹选择器带 webkitRelativePath，保留目录结构）。"""
        name = request.headers.get("X-File-Name") or "upload.bin"
        import urllib.parse
        name = urllib.parse.unquote(name)
        if any(ord(c) < 0x20 for c in name + rel + target_dir):
            return {"ok": False, "error": "agent_error",
                    "detail": "路径含非法控制字符"}
        data = await request.body()
        if not data:
            return {"ok": False, "error": "agent_error", "detail": "空文件"}
        import uuid as _uuid
        core.config.inbox_dir().mkdir(parents=True, exist_ok=True)
        tmp = core.config.inbox_dir() / f"_upload_{_uuid.uuid4().hex[:8]}_{name}"
        tmp.write_bytes(data)
        base = (target_dir or "").strip().rstrip("/\\")
        rel_clean = (rel or name).replace("\\", "/").strip("/")
        # 浏览在根目录（盘符视图）时落到统一收件目录（2.4.2 默认值）
        target = f"{base}/{rel_clean}" if base else f"inbox/{rel_clean}"
        try:
            return core.file_push(node_id or None, str(tmp), target)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    # ---------- 执行器（2.9.10 / 4.3） ----------
    @app.get("/api/executors")
    def executors():
        return {"ok": True, "executors": core.list_executors()}

    @app.get("/api/executors/status")
    def executor_status(executor_id: str):
        return core.get_executor_status(executor_id)

    @app.post("/api/executors/suspend")
    async def suspend(body: dict):
        return core.set_executor_suspend(body.get("executor_id") or "",
                                         bool(body.get("suspend")),
                                         body.get("reason"), body.get("until"))

    @app.post("/api/executors/restart")
    async def restart(body: dict):
        return core.restart_plugin(body.get("executor_id") or "")

    @app.post("/api/executors/rename")
    async def rename_exec(body: dict):
        return core.rename_executor(body.get("executor_id") or "",
                                    body.get("new_name") or "")

    @app.get("/api/executors/sessions")
    def sessions(executor_id: str = ""):
        return core.list_agent_sessions(executor_id or None)

    # ---------- 任务（2.7） ----------
    @app.post("/api/task/submit")
    async def task_submit(request: Request):
        body = await request.json()
        return core.submit_task(
            body.get("executor_id") or "", body.get("prompt") or "",
            mode=body.get("mode") or "async",
            attachments=body.get("attachments"),
            timeout=float(body.get("timeout") or 600),
            task_id=body.get("task_id"),
            caller_id=caller_id(request))

    @app.get("/api/task/result")
    def task_result(task_id: str):
        return core.get_task_result(task_id)

    # ---------- mailbox（异步邮箱；与文件收件箱 data/inbox/ 严格区分） ----------
    @app.get("/api/mailbox")
    def mailbox(request: Request):
        return {"ok": True, "items": core.check_mail(caller_id(request))}

    @app.get("/api/mailbox/all")
    def mailbox_all(limit: int = 200):
        """面板/人类视角：异步邮箱全量（含已读/未读，不标记已读）。"""
        return {"ok": True, "items": core.mail_all(limit)}

    @app.post("/api/mailbox/cleanup")
    async def mailbox_cleanup(body: dict):
        return core.cleanup_mail(body.get("mode") or "consumed", body.get("before"))

    # ---------- 日志（2.3 / 2.9.7） ----------
    @app.get("/api/logs/comm")
    def logs_comm(peer: str = "", direction: str = "", type: str = "",
                  correlation_id: str = "", limit: int = 200):
        return {"ok": True, "entries": core.comm_log(
            peer_node_id=peer or None, direction=direction or None,
            msg_type=type or None, correlation_id=correlation_id or None,
            limit=limit)}

    @app.get("/api/logs/node")
    def logs_node(lines: int = 200, source: str = "", level: str = ""):
        log = core.node_log(lines)
        # source/level 过滤：level 匹配行内 [LEVEL] 标记；source 匹配行内关键字
        src = str(source).strip().lower()
        lv = str(level).strip().upper()
        if src or lv:
            filtered = []
            for ln in log.splitlines():
                if src and src not in ln.lower():
                    continue
                if lv and f"[{lv}]" not in ln.upper():
                    continue
                filtered.append(ln)
            log = "\n".join(filtered)
        return {"ok": True, "log": log}

    # ---------- 设置（2.9.9 / 2.14.3） ----------
    @app.get("/api/settings")
    def settings():
        c = core.config
        return {"ok": True, "config": {
            "nodeId": c.node_id, "name": c.name, "teamId": c.team_id,
            "switches": dict(c.switches), "runAsAdmin": c.run_as_admin,
            "peerTcpPort": c.peer_tcp_port, "manualPeers": list(c.manual_peers),
            "syncEnabled": c.sync_enabled,
            "discoveryPorts": c.discovery_ports(),
        }}

    @app.post("/api/settings/name")
    async def set_name(body: dict):
        return core.rename_node(body.get("name") or "")

    @app.post("/api/settings/team")
    async def set_team(body: dict):
        return core.set_team(body.get("team_id") or "")

    @app.post("/api/settings/switch")
    async def set_switch(body: dict):
        return core.set_switch(body.get("switch") or "", bool(body.get("enabled")))

    @app.post("/api/settings/admin")
    async def set_admin(body: dict):
        core.config.run_as_admin = bool(body.get("enabled"))
        core.config.save()
        return {"ok": True, "detail": "已写入配置，需重启节点生效"}

    @app.post("/api/settings/sync")
    async def set_sync(body: dict):
        core.config.sync_enabled = bool(body.get("enabled"))
        core.config.save()
        return {"ok": True, "detail": "需重启节点生效"}

    # ---------- 节点生命周期：重启本机（面板/AI，2.11.3 已放宽；仅本机，保持驻留） ----------
    @app.post("/api/node/restart")
    async def node_restart():
        return core.restart_self()

    # ---------- shell（2.5.9 ⑧） ----------
    @app.post("/api/shell")
    async def shell(request: Request):
        body = await request.json()
        return core.shell_exec(body.get("target_node_id") or None,
                               body.get("command") or "",
                               float(body.get("timeout") or 60))

    # ---------- 插件（2.2.10） ----------
    @app.get("/api/plugins")
    def plugins():
        return {"ok": True, "plugins": core.list_plugins()}

    @app.post("/api/plugins/distribute")
    async def distribute(body: dict):
        return core.distribute_plugin(body.get("target_node_id") or "",
                                      body.get("plugin_path") or "")

    # ---------- 同步 ----------
    @app.post("/api/sync/now")
    def sync_now():
        return core.sync_now()

    @app.get("/api/sync/status")
    def sync_status():
        """每个已配对节点的同步完成度（人类确认"同步到位"的反馈，2.4.7）。"""
        if not core.sync:
            return {"ok": False, "error": "not_installed", "devices": []}
        return core.sync.status()

    @app.post("/api/sync/add")
    async def sync_add(request: Request):
        """把本机选择的文件加入同步目录 data/sync（文件选择器 → 浏览器原始字节）。"""
        name = request.headers.get("X-File-Name") or "sync_add.bin"
        import urllib.parse
        name = urllib.parse.unquote(name)
        if any(ord(c) < 0x20 for c in name):
            return {"ok": False, "error": "agent_error", "detail": "文件名含非法字符"}
        data = await request.body()
        if not data:
            return {"ok": False, "error": "agent_error", "detail": "空文件"}
        rel = request.query_params.get("rel") or name
        rel = urllib.parse.unquote(rel).replace("\\", "/").strip("/")
        dest = core.data_dir / "sync" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        if core.sync:
            core.sync.sync_now()
        return {"ok": True, "detail": f"已加入同步目录: {dest}"}

    # ---------- 收件目录 ----------
    @app.get("/api/files/inbox")
    def files_inbox():
        """统一收件目录内容 + 收件历史（来自哪个节点，2.4.2/4.2 文件视图）。"""
        import time as _time
        inbox = core.config.inbox_dir()
        entries = []
        if inbox.is_dir():
            for it in sorted(inbox.iterdir(),
                             key=lambda x: x.stat().st_mtime if x.exists() else 0,
                             reverse=True):
                try:
                    st = it.stat()
                    entries.append({"name": it.name, "path": str(it),
                                    "isDir": it.is_dir(),
                                    "size": 0 if it.is_dir() else st.st_size,
                                    "mtime": int(st.st_mtime)})
                except OSError:
                    pass
        # 收件历史: inbound file_push 通信日志（含来源节点）
        history = []
        for e in core.comm_log(msg_type="file_push", direction="inbound",
                               limit=100):
            history.append({"ts": e.get("ts"), "peer": e.get("peer_node_id"),
                            "detail": (e.get("detail") or "")[:200]})
        return {"ok": True, "entries": entries, "history": history[:50]}

    # ---------- 诊断（2.17.7） ----------
    @app.get("/api/diag")
    def diag():
        return core.diag()

    return app


def pick_panel_port(preferred: int) -> int:
    """2.1.5: 默认固定端口，被占则顺序 +1 尝试。"""
    port = preferred
    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                port += 1
    raise RuntimeError("面板端口 5177+20 均被占用")


def serve(core, preferred_port: int | None = None) -> int:
    """启动面板（阻塞运行于调用线程）；返回实际端口并写 data/panel.url。"""
    import uvicorn
    port = pick_panel_port(preferred_port or cfg_mod.DEFAULT_PANEL_PORT)
    if port != (preferred_port or cfg_mod.DEFAULT_PANEL_PORT):
        # 2.1.5: 非默认端口 → 非模态提示（由 core.notify 落面板通知）
        core._notify("warning", f"默认面板端口被占用，已改用 {port}")
        core.log("warning", f"默认面板端口被占用，已改用 {port}")
    core.panel_port = port
    (core.data_dir / "panel.url").write_text(
        f"http://127.0.0.1:{port}/", encoding="utf-8")
    app = create_app(core)
    # log_config=None: 规避部分 Python 版本上 uvicorn 默认日志配置器
    # （"Unable to configure formatter 'default'"）的兼容性问题
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning",
                log_config=None)
    return port
