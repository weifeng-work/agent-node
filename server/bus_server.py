"""中间架构服务端（单进程）：

  MQTT 桥   —— 订阅总线全部主题，落地注册/心跳/遗嘱/消息追溯到 SQLite
  HTTP API  —— 在线名单、消息时间线、文件上传下载（Claim-Check）
  Web 面板  —— server/static/index.html

运行:  python server/bus_server.py [--host 0.0.0.0] [--port 8000]
                              [--broker-host 127.0.0.1] [--broker-port 1883]
                              [--db data/bus.db] [--files-dir data/files]
"""

import argparse
import json
import logging
import os
import platform as _platform
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import paho.mqtt.client as mqtt
import uvicorn
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_bus import provision

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("bus_server")

OFFLINE_AFTER_SECONDS = 90.0

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent

# 静态目录（web 面板）：默认 BASE_DIR/static，打包后可被 control_app 覆盖
_STATIC_DIR = str(BASE_DIR / "static")

# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------

_db_lock = threading.Lock()


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        # Bug 17: in-memory tombstone（agent_id -> 删除时间戳），TTL 60s
        # 防止 bridge 重连时 MQTT retain register 重新激活已删节点。
        # 进程重启丢失可接受（极少同时发生 bridge 重连 + 节点删除）。
        self._tombstones: dict = {}
        self._init_tables()

    def _init_tables(self):
        with self.conn:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agents(
                    agent_id TEXT PRIMARY KEY,
                    name TEXT, capabilities TEXT, platform TEXT, executor TEXT,
                    online INTEGER DEFAULT 1,
                    last_seen REAL, registered_at REAL
                );
                CREATE TABLE IF NOT EXISTS messages(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL, topic TEXT, msg_type TEXT,
                    sender_id TEXT, target_id TEXT,
                    task_id TEXT, correlation_id TEXT,
                    status TEXT, payload TEXT
                );
                CREATE TABLE IF NOT EXISTS files(
                    file_id TEXT PRIMARY KEY, name TEXT, size INTEGER,
                    uploaded_by TEXT, ts REAL
                );
                CREATE TABLE IF NOT EXISTS team_info(
                    id INTEGER PRIMARY KEY CHECK (id=1),
                    team_id TEXT, team_name TEXT,
                    created_at REAL, updated_at REAL
                );
                """
            )
            # 增量迁移：逐列补齐（列已存在时忽略）
            for col in ("hostname", "health"):
                try:
                    self.conn.execute(f"ALTER TABLE agents ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError:
                    pass

    def execute(self, sql, params=(), fetch=False):
        with _db_lock, self.conn:
            cur = self.conn.execute(sql, params)
            rows = cur.fetchall() if fetch else None
        return rows

    # ---- agents ----

    def upsert_agent(self, msg: dict):
        self.execute(
            """INSERT INTO agents(agent_id,name,capabilities,platform,executor,hostname,health,online,last_seen,registered_at)
               VALUES(?,?,?,?,?,?,?,1,?,?)
               ON CONFLICT(agent_id) DO UPDATE SET
                 name=excluded.name, capabilities=excluded.capabilities,
                 platform=excluded.platform, executor=excluded.executor,
                 hostname=excluded.hostname, health=excluded.health,
                 online=1, last_seen=excluded.last_seen""",
            (
                msg["agent_id"],
                msg.get("name", ""),
                json.dumps(msg.get("capabilities", [])),
                msg.get("platform", ""),
                msg.get("executor", ""),
                msg.get("hostname", ""),
                msg.get("health", "unknown"),
                time.time(),
                msg.get("registered_at", time.time()),
            ),
        )

    def heartbeat(self, agent_id: str, health: str = None):
        self.execute(
            "UPDATE agents SET online=1, last_seen=?, "
            "health=COALESCE(NULLIF(?,''), health) WHERE agent_id=?",
            (time.time(), health, agent_id),
        )

    def mark_offline(self, agent_id: str):
        self.execute("UPDATE agents SET online=0 WHERE agent_id=?", (agent_id,))

    def list_agents(self):
        rows = self.execute(
            "SELECT * FROM agents ORDER BY online DESC, name", fetch=True
        )
        now = time.time()
        result = []
        for r in rows:
            stale = now - (r["last_seen"] or 0) > OFFLINE_AFTER_SECONDS
            result.append(
                {
                    "agent_id": r["agent_id"],
                    "name": r["name"],
                    "capabilities": json.loads(r["capabilities"] or "[]"),
                    "platform": r["platform"],
                    "executor": r["executor"],
                    "hostname": r["hostname"] or "",
                    "health": r["health"] or "unknown",
                    "online": bool(r["online"]) and not stale,
                    "last_seen": r["last_seen"],
                    "registered_at": r["registered_at"],
                }
            )
        return result

    # ---- messages ----

    def log_message(self, topic: str, msg: dict):
        # 仅心跳不入库（纯状态信号、无查看价值，且量极大）
        # register/offline/task_progress 入库，作为"系统事件"由前端聚合展示，
        # 与真实通信消息（task_request/task_result/git_event）分层呈现
        if topic.startswith("bus/heartbeat/"):
            return
        target_id = msg.get("target_id", "")
        if (
            msg.get("type") == "task_result"
            and not target_id
            and msg.get("correlation_id")
        ):
            rows = self.execute(
                "SELECT sender_id FROM messages WHERE correlation_id=? AND msg_type='task_request' LIMIT 1",
                (msg["correlation_id"],),
                fetch=True,
            )
            if rows:
                target_id = rows[0]["sender_id"]
        self.execute(
            """INSERT INTO messages(ts,topic,msg_type,sender_id,target_id,task_id,correlation_id,status,payload)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                time.time(),
                topic,
                msg.get("type", ""),
                msg.get("sender_id", ""),
                target_id,
                msg.get("task_id", ""),
                msg.get("correlation_id", ""),
                msg.get("status", ""),
                json.dumps(msg, ensure_ascii=False),
            ),
        )

    def list_messages(self, limit=100, agent_id=None, keyword=None):
        sql, params = "SELECT * FROM messages", []
        conds = []
        if agent_id:
            conds.append("(sender_id=? OR target_id=?)")
            params += [agent_id, agent_id]
        if keyword:
            conds.append("payload LIKE ?")
            params.append(f"%{keyword}%")
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = self.execute(sql, params, fetch=True)
        return [dict(r) for r in rows]

    # ---- files ----

    def add_file(self, file_id, name, size, uploaded_by):
        self.execute(
            "INSERT INTO files(file_id,name,size,uploaded_by,ts) VALUES(?,?,?,?,?)",
            (file_id, name, size, uploaded_by, time.time()),
        )

    def list_files(self):
        rows = self.execute("SELECT * FROM files ORDER BY ts DESC", fetch=True)
        return [dict(r) for r in rows]

    def get_file(self, file_id):
        rows = self.execute(
            "SELECT * FROM files WHERE file_id=?", (file_id,), fetch=True
        )
        return dict(rows[0]) if rows else None

    # ---- team ----

    def get_team(self):
        rows = self.execute("SELECT * FROM team_info WHERE id=1", fetch=True)
        return dict(rows[0]) if rows else None

    def init_team(self, team_id: str, team_name: str):
        self.execute(
            "INSERT OR REPLACE INTO team_info(id,team_id,team_name,created_at,updated_at)"
            " VALUES(1,?,?,?,?)",
            (team_id, team_name, time.time(), time.time()),
        )

    def delete_agent(self, agent_id: str):
        self.execute("DELETE FROM agents WHERE agent_id=?", (agent_id,))
        # Bug 17: 入 tombstone，60s 内 bridge 收到 retain register 时忽略
        self._tombstones[agent_id] = time.time()

    def is_tombstoned(self, agent_id: str) -> bool:
        """Bug 17: 检查 agent_id 是否在 tombstone TTL（60s）内。"""
        ts = self._tombstones.get(agent_id)
        if not ts:
            return False
        if time.time() - ts < 60.0:
            return True
        # TTL 过期，节点可正常重新 register
        self._tombstones.pop(agent_id, None)
        return False

    def rename_agent(self, agent_id: str, name: str) -> int:
        """Bug 18: 返回受影响行数；调用方据此判断 agent 是否存在。"""
        with _db_lock, self.conn:
            cur = self.conn.execute(
                "UPDATE agents SET name=? WHERE agent_id=?", (name, agent_id)
            )
            return cur.rowcount


# ---------------------------------------------------------------------------
# MQTT 桥
# ---------------------------------------------------------------------------


class MqttBridge:
    def __init__(
        self,
        store: Store,
        broker_host: str,
        broker_port: int,
        username: str = "",
        password: str = "",
    ):
        self.store = store
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id="bus-server-bridge"
        )
        if username:
            self.client.username_pw_set(username, password)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.broker_host, self.broker_port = broker_host, broker_port

    def start(self):
        self.client.connect(self.broker_host, self.broker_port, keepalive=60)
        self.client.loop_start()

    def stop(self):
        try:
            self.client.disconnect()
            self.client.loop_stop()
        except Exception:
            pass

    def publish(self, topic: str, payload: dict, qos: int = 1) -> bool:
        """向总线发布控制消息（面板改名等下发用）。

        Bug 33：返回是否在超时内发布完成；未连接时 publish 入队但 wait_for_publish 超时返 False。
        """
        info = self.client.publish(
            topic, json.dumps(payload, ensure_ascii=False), qos=qos
        )
        ok = info.wait_for_publish(timeout=5)
        if not ok:
            log.warning(
                "MQTT publish 超时/未连 topic=%s rc=%s mid=%s",
                topic, getattr(info, "rc", "?"), getattr(info, "mid", "?"),
            )
        return ok

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            client.subscribe([("bus/#", 1), ("agent/+/inbox", 1)])
            log.info(
                "MQTT 桥已连接 %s:%s，订阅 bus/# 与 agent/+/inbox",
                self.broker_host,
                self.broker_port,
            )
        else:
            log.error("MQTT 连接失败 reason_code=%s", reason_code)

    def _on_disconnect(
        self, client, userdata, disconnect_flags, reason_code, properties=None
    ):
        # Bug 32：补 disconnect 日志，便于排查 broker 抖动
        log.warning("MQTT 桥断开 reason_code=%s（paho 自动重连）", reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        topic = msg.topic
        self.store.log_message(topic, data)
        t = data.get("type")
        if t == "register":
            aid = data.get("agent_id", "")
            # Bug 17: 已删节点的 retain register 在 tombstone TTL 内忽略，
            # 避免 bridge 重连时 retain 缓存把节点"复活"
            if aid and self.store.is_tombstoned(aid):
                log.info("忽略已删节点 retain register（tombstone）: %s", aid)
                return
            self.store.upsert_agent(data)
            log.info("注册: %s (%s)", aid, data.get("name", ""))
        elif topic.startswith("bus/heartbeat/"):
            self.store.heartbeat(data.get("agent_id", ""), health=data.get("health"))
        elif topic.startswith("bus/offline/"):
            self.store.mark_offline(data.get("agent_id", ""))
            log.info("离线(遗嘱): %s", data.get("agent_id"))


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------


def create_app(
    store: Store,
    files_dir: Path,
    bridge: MqttBridge,
    broker_port: int = 1883,
    master_agent_id: str = "",
    on_rename_local=None,
) -> FastAPI:
    app = FastAPI(title="Agent Bus Server")
    files_dir.mkdir(parents=True, exist_ok=True)

    from fastapi import Depends, Header

    def require_token(authorization: str = Header(None), token: str = Query(None)):
        """面板 API（匿名放行）：无需凭据。"""
        return {"agent_id": "", "role": "anonymous"}

    def require_admin(ident: dict = Depends(require_token)):
        return ident

    @app.get("/api/health")
    def health():
        return {"ok": True}

    # ---- 队伍：首次向导 + 加入 ----

    class SetupBody(BaseModel):
        team_name: str

    class JoinBody(BaseModel):
        agent_id: str = ""
        device_name: str = ""
        platform: str = ""

    @app.get("/api/team/status")
    def team_status():
        t = store.get_team()
        return {"initialized": bool(t), "team_name": t["team_name"] if t else ""}

    @app.post("/api/team/setup")
    def team_setup(body: SetupBody):
        if store.get_team():
            raise HTTPException(403, "team already initialized")
        team_name = body.team_name.strip()
        if not (1 <= len(team_name) <= 32):
            raise HTTPException(400, "队伍名称长度需 1-32")
        store.init_team(uuid.uuid4().hex[:12], team_name)
        log.info("队伍已初始化: %s", team_name)
        return {"ok": True, "team_name": team_name}

    @app.post("/api/join")
    def join(body: JoinBody, request: Request):
        team = store.get_team()
        if not team:
            raise HTTPException(403, "team not initialized")
        agent_id = (body.agent_id or "").strip() or f"node-{uuid.uuid4().hex[:6]}"
        if not provision.valid_agent_id(agent_id):
            raise HTTPException(400, "agent_id 非法（限 [A-Za-z0-9_-]，1-64 位）")
        ip = request.client.host if request.client else "?"
        log.info("节点加入(匿名): %s (%s) from %s", agent_id, body.device_name, ip)
        return {
            "ok": True,
            "team_name": team["team_name"],
            "team_id": team["team_id"],
            "agent_id": agent_id,
            "broker_host": provision.get_local_ip(),
            "broker_port": broker_port,
        }

    # ---- 管理（匿名） ----

    @app.delete("/api/admin/nodes/{agent_id}")
    def remove_node(agent_id: str, ident: dict = Depends(require_admin)):
        store.delete_agent(agent_id)
        log.info("节点已移除: %s by %s", agent_id, ident.get("agent_id"))
        return {
            "ok": True,
            "agent_id": agent_id,
            "broker_restarted": False,
            "broker_message": "匿名模式下无需重启 broker，节点已即时移除。",
        }

    class RenameBody(BaseModel):
        agent_id: str
        name: str

    @app.post("/api/admin/rename")
    def rename_node(body: RenameBody, ident: dict = Depends(require_admin)):
        """网页面板改名：立即更新中心名单；本 hub 节点同进程持久化，
        远程节点经 MQTT 下发 rename 指令让目标本地持久化。"""
        agent_id = body.agent_id.strip()
        name = body.name.strip()
        if not agent_id:
            raise HTTPException(400, "缺少 agent_id")
        if not (1 <= len(name) <= 64):
            raise HTTPException(400, "名称长度需 1-64")
        affected = store.rename_agent(agent_id, name)
        if affected == 0:
            # Bug 18: agent_id 不在名单内（可能是从未注册或已被删除）
            raise HTTPException(404, f"agent 不存在: {agent_id}")
        if master_agent_id and agent_id == master_agent_id:
            if on_rename_local:
                try:
                    on_rename_local(name)
                except Exception as e:
                    log.warning("本地改名回调失败: %s", e)
            mode = "local"
        else:
            try:
                from agent_bus.schema import inbox_topic, make_task_request

                req = make_task_request("bus_server", agent_id, "")
                req["payload"] = {"op": "rename", "name": name}
                bridge.publish(inbox_topic(agent_id), req)
            except Exception as e:
                log.warning("改名下发失败: %s", e)
                return {
                    "ok": True,
                    "agent_id": agent_id,
                    "name": name,
                    "mode": "remote-warn",
                    "warning": str(e),
                }
            mode = "remote"
        log.info("节点改名: %s → %s (%s) by %s", agent_id, name, mode, ident.get("agent_id"))
        return {"ok": True, "agent_id": agent_id, "name": name, "mode": mode}

    # ---- 节点列表 ----

    @app.get("/api/agents")
    def agents(ident: dict = Depends(require_token)):
        # 标记本机（运行 bus_server 的主控机）上的节点，供面板置顶主控机
        master_host = _platform.node()
        result = store.list_agents()
        for a in result:
            a["is_master"] = (a.get("hostname") or "") == master_host
        return result

    @app.get("/api/messages")
    def messages(
        limit: int = Query(100, ge=1, le=1000),
        agent_id: str = None,
        keyword: str = None,
        ident: dict = Depends(require_token),
    ):
        return store.list_messages(limit, agent_id, keyword)

    @app.get("/api/files")
    def files_list(ident: dict = Depends(require_token)):
        base = os.environ.get("BUS_HTTP_BASE", "").rstrip("/")
        out = []
        for f in store.list_files():
            f["url"] = (
                f"{base}/api/files/{f['file_id']}"
                if base
                else f"/api/files/{f['file_id']}"
            )
            out.append(f)
        return out

    @app.post("/api/files/upload")
    async def upload(
        file: UploadFile = File(...),
        uploaded_by: str = "",
        ident: dict = Depends(require_token),
    ):
        import anyio

        # Bug 35：filename 可能为 None（异常上传），统一兜底 'unnamed'，
        # 避免写入 SQLite NULL 导致前端展示 "None"
        fname = file.filename or "unnamed"
        file_id = uuid.uuid4().hex[:12]
        dest = files_dir / f"{file_id}_{Path(fname).name}"
        size = 0

        # Bug 24：写盘是同步 IO，async 函数内裸调会阻塞事件循环；
        # 用 anyio.to_thread 把 open + write 丢工作线程，事件循环保持响应
        async def _write_stream():
            nonlocal size
            with open(dest, "wb") as out:
                while True:
                    chunk = await file.read(64 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    await anyio.to_thread.run_sync(out.write, chunk)

        await _write_stream()
        store.add_file(file_id, fname, size, uploaded_by)
        base = os.environ.get("BUS_HTTP_BASE", "").rstrip("/")
        url = f"{base}/api/files/{file_id}" if base else f"/api/files/{file_id}"
        log.info("文件上传: %s (%d bytes) by %s", fname, size, uploaded_by)
        return {"file_id": file_id, "url": url, "size": size}

    @app.get("/api/files/{file_id}")
    def download(file_id: str, ident: dict = Depends(require_token)):
        meta = store.get_file(file_id)
        if not meta:
            raise HTTPException(404, "file not found")
        for f in files_dir.iterdir():
            if f.name.startswith(file_id):
                return FileResponse(
                    str(f), filename=meta["name"], media_type="application/octet-stream"
                )
        raise HTTPException(404, "file not found on disk")

    # ---- 静态 ----

    # 静态目录：核心路径优先，可被 control_app 注入覆盖（打包后指向解出的 index.html）
    static_dir = Path(_STATIC_DIR)
    if not static_dir.is_dir():
        static_dir = BASE_DIR / "static"
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def serve_bus_server(
    host="0.0.0.0",
    port=8000,
    broker_host="127.0.0.1",
    broker_port=1883,
    db=None,
    files_dir=None,
    static_dir=None,
    master_agent_id="",
    on_rename_local=None,
):
    """编程式启动中间架构服务（控制节点 hub 内联用）。

    返回 (store, bridge, app, uvicorn_server)；调用方可 start/stop。
    db/files_dir/static_dir 可注入（打包后指向应用数据目录，而非模块 ROOT）。
    """
    if db is None:
        db = str(ROOT_DIR / "data" / "bus.db")
    if files_dir is None:
        files_dir = str(ROOT_DIR / "data" / "files")

    store = Store(Path(db))
    bridge = MqttBridge(store, broker_host, broker_port)
    bridge.start()

    if static_dir is not None:
        import server.bus_server as _mod

        _mod._STATIC_DIR = str(static_dir)
    app = create_app(
        store,
        Path(files_dir),
        bridge,
        broker_port=broker_port,
        master_agent_id=master_agent_id,
        on_rename_local=on_rename_local,
    )

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    uv_server = uvicorn.Server(config)
    log.info(
        "中间架构服务端启动(编程) %s:%s (broker=%s:%s)",
        host,
        port,
        broker_host,
        broker_port,
    )
    return store, bridge, app, uv_server


def main():
    ap = argparse.ArgumentParser(description="Agent Bus 中间架构服务端")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--broker-host", default="127.0.0.1")
    ap.add_argument("--broker-port", type=int, default=1883)
    ap.add_argument("--db", default=str(ROOT_DIR / "data" / "bus.db"))
    ap.add_argument("--files-dir", default=str(ROOT_DIR / "data" / "files"))
    args = ap.parse_args()

    store, bridge, app, uv_server = serve_bus_server(
        host=args.host,
        port=args.port,
        broker_host=args.broker_host,
        broker_port=args.broker_port,
        db=args.db,
        files_dir=args.files_dir,
    )
    uv_server.run()


if __name__ == "__main__":
    main()
