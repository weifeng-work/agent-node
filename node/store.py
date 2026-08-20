"""comm.db —— SQLite 单库多表（2.14.1）:
通信日志（2.3）+ mailbox 异步邮箱（2.6.8）+ known peers（2.1.9）+ 聊天记录（2.8.2）。

命名约定（防混淆）:
- `mailbox`（异步邮箱）: 异步任务回执，按 caller_id 归属 —— 与"文件收件箱"（data/inbox/ 目录）严格区分。
- 文件收件箱 = 目录 data/inbox/（config.inbox_dir()），与 mailbox 无关。

- WAL 模式 + 事务写入（2.17.5 并发写安全）
- mailbox「取走即标已读」（2.6.9）；通信日志保留全量审计
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS comm_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    direction TEXT NOT NULL,
    peer_node_id TEXT NOT NULL,
    msg_type TEXT NOT NULL,
    correlation_id TEXT,
    result TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_comm_peer ON comm_log(peer_node_id);
CREATE INDEX IF NOT EXISTS idx_comm_corr ON comm_log(correlation_id);
CREATE TABLE IF NOT EXISTS mailbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    caller_id TEXT NOT NULL,
    source_node_id TEXT,
    correlation_id TEXT,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mailbox_caller ON mailbox(caller_id, consumed);
CREATE TABLE IF NOT EXISTS known_peers (
    node_id TEXT PRIMARY KEY,
    name TEXT,
    team_id TEXT,
    capabilities TEXT,
    switches TEXT,
    sync_device_id TEXT,
    host TEXT,
    peer_tcp_port INTEGER,
    first_seen TEXT,
    last_seen TEXT
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    peer_node_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    session_id TEXT,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_peer ON chat_messages(peer_node_id, id);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        with self._lock, self._db:
            # 旧版表名 inbox → mailbox（防与文件收件箱混淆；保留既有回执数据）
            # 健壮性：仅当旧表存在且新表不存在时迁移，避免中途异常/双表并存导致崩溃
            old = self._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='inbox'"
            ).fetchone()
            has_new = self._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='mailbox'"
            ).fetchone()
            if old and not has_new:
                self._db.execute("ALTER TABLE inbox RENAME TO mailbox")
                self._db.execute("DROP INDEX IF EXISTS idx_inbox_caller")
            elif old and has_new:
                # 双表并存（异常状态）：丢弃旧表数据，以新表为准，避免启动崩溃
                self._db.execute("DROP TABLE IF EXISTS inbox")
                self._db.execute("DROP INDEX IF EXISTS idx_inbox_caller")
            self._db.executescript(SCHEMA)

    # ---------- 通信日志（2.3） ----------
    def add_comm_log(self, direction: str, peer_node_id: str, msg_type: str,
                     correlation_id: str | None, result: str | None, detail: str | None) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO comm_log(ts, direction, peer_node_id, msg_type, correlation_id, result, detail) "
                "VALUES (?,?,?,?,?,?,?)",
                (utcnow(), direction, peer_node_id or "", msg_type, correlation_id,
                 result, (detail or "")[:500]),
            )

    def query_comm_log(self, peer_node_id: str | None = None, direction: str | None = None,
                       msg_type: str | None = None, correlation_id: str | None = None,
                       limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM comm_log WHERE 1=1"
        args: list = []
        if peer_node_id:
            sql += " AND peer_node_id=?"
            args.append(peer_node_id)
        if direction:
            sql += " AND direction=?"
            args.append(direction)
        if msg_type:
            sql += " AND msg_type=?"
            args.append(msg_type)
        if correlation_id:
            sql += " AND correlation_id=?"
            args.append(correlation_id)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    # ---------- mailbox（2.6） ----------
    def add_mail(self, caller_id: str, source_node_id: str | None, correlation_id: str | None,
                 kind: str, content: dict) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO mailbox(ts, caller_id, source_node_id, correlation_id, kind, content) "
                "VALUES (?,?,?,?,?,?)",
                (utcnow(), caller_id, source_node_id, correlation_id, kind,
                 json.dumps(content, ensure_ascii=False)),
            )

    def fetch_mail(self, caller_id: str, mark_consumed: bool = True) -> list[dict]:
        """未读邮件；取走即标已读（2.6.9）。"""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM mailbox WHERE caller_id=? AND consumed=0 ORDER BY id ASC",
                (caller_id,)).fetchall()
            items = []
            for r in rows:
                item = dict(r)
                try:
                    item["content"] = json.loads(item["content"])
                except Exception:
                    item["content"] = {"raw": item["content"]}
                items.append(item)
            if mark_consumed and items:
                with self._db:
                    self._db.execute(
                        "UPDATE mailbox SET consumed=1 WHERE caller_id=? AND consumed=0",
                        (caller_id,))
            return items

    def list_mail_all(self, limit: int = 200) -> list[dict]:
        """面板/人类视角：异步邮箱全量（不分 caller，含已读/未读；不标记已读）。
        用于「异步回执对人类可见」的方案 A —— 人类有权查看全部任务回执。"""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM mailbox ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        items = []
        for r in rows:
            item = dict(r)
            try:
                item["content"] = json.loads(item["content"])
            except Exception:
                item["content"] = {"raw": item["content"]}
            items.append(item)
        return items

    def cleanup_mail(self, mode: str = "consumed", before: str | None = None) -> int:
        sql = "DELETE FROM mailbox WHERE 1=1"
        args: list = []
        if mode == "consumed":
            sql += " AND consumed=1"
        elif mode == "expired":
            sql += " AND consumed=0"
        else:
            return 0
        if before:
            sql += " AND ts<?"
            args.append(before)
        with self._lock, self._db:
            cur = self._db.execute(sql, args)
            return cur.rowcount

    # ---------- known peers（2.1.9） ----------
    def upsert_peer(self, node_id: str, name: str | None = None, team_id: str | None = None,
                    capabilities: list | None = None, switches: dict | None = None,
                    sync_device_id: str | None = None, host: str | None = None,
                    peer_tcp_port: int | None = None) -> None:
        now = utcnow()
        with self._lock, self._db:
            row = self._db.execute("SELECT first_seen FROM known_peers WHERE node_id=?",
                                   (node_id,)).fetchone()
            first = row["first_seen"] if row else now
            self._db.execute(
                "INSERT INTO known_peers(node_id, name, team_id, capabilities, switches, "
                "sync_device_id, host, peer_tcp_port, first_seen, last_seen) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(node_id) DO UPDATE SET "
                # 空值不覆盖既有值（beacon 与握手两路写入互补）
                "name=COALESCE(NULLIF(excluded.name,''), known_peers.name), "
                "team_id=excluded.team_id, "
                "capabilities=CASE WHEN excluded.capabilities != '[]' "
                "THEN excluded.capabilities ELSE known_peers.capabilities END, "
                "switches=CASE WHEN excluded.switches != '{}' "
                "THEN excluded.switches ELSE known_peers.switches END, "
                "sync_device_id=COALESCE(NULLIF(excluded.sync_device_id,''), "
                "known_peers.sync_device_id), "
                "host=COALESCE(NULLIF(excluded.host,''), known_peers.host), "
                "peer_tcp_port=COALESCE(NULLIF(excluded.peer_tcp_port,0), known_peers.peer_tcp_port), "
                "last_seen=excluded.last_seen",
                (node_id, name or "", team_id or "",
                 json.dumps(capabilities or [], ensure_ascii=False),
                 json.dumps(switches or {}, ensure_ascii=False),
                 sync_device_id or "", host or "", peer_tcp_port, first, now),
            )

    def peers(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM known_peers ORDER BY last_seen DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["capabilities"] = json.loads(d.get("capabilities") or "[]")
            except Exception:
                d["capabilities"] = []
            try:
                d["switches"] = json.loads(d.get("switches") or "{}")
            except Exception:
                d["switches"] = {}
            out.append(d)
        return out

    def peer(self, node_id: str) -> dict | None:
        for p in self.peers():
            if p["node_id"] == node_id:
                return p
        return None

    def delete_peer(self, node_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM known_peers WHERE node_id=?", (node_id,))

    def delete_node_records(self, node_id: str) -> None:
        """彻底删除死节点：known_peers + chat_messages（comm_log 审计保留，2.3）。"""
        with self._lock, self._db:
            self._db.execute("DELETE FROM known_peers WHERE node_id=?", (node_id,))
            self._db.execute("DELETE FROM chat_messages WHERE peer_node_id=?", (node_id,))

    # ---------- 聊天（2.8） ----------
    def add_chat(self, peer_node_id: str, direction: str, text: str,
                 session_id: str | None = None) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO chat_messages(ts, peer_node_id, direction, session_id, text) "
                "VALUES (?,?,?,?,?)",
                (utcnow(), peer_node_id, direction, session_id, text),
            )

    def chat_history(self, peer_node_id: str, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM chat_messages WHERE peer_node_id=? ORDER BY id ASC LIMIT ?",
                (peer_node_id, int(limit))).fetchall()
        return [dict(r) for r in rows]

    def conversations(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT peer_node_id, COUNT(*) AS msg_count, MAX(ts) AS last_ts, "
                "SUM(CASE WHEN direction='in' THEN 1 ELSE 0 END) AS in_count "
                "FROM chat_messages GROUP BY peer_node_id ORDER BY MAX(id) DESC").fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            try:
                self._db.close()
            except Exception:
                pass
