"""单元测试: comm.db 存储（通信日志/mailbox/known peers/聊天）。"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from node.store import Store


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "comm.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_comm_log_roundtrip_and_filters(self):
        self.store.add_comm_log("outbound", "node-x", "shell_exec", "c1", "ok", "echo")
        self.store.add_comm_log("inbound", "node-x", "shell_result", "c1", "ok", "out")
        self.store.add_comm_log("inbound", "node-y", "text_request", None, None, "hi")
        all_logs = self.store.query_comm_log(limit=10)
        self.assertEqual(len(all_logs), 3)
        only_x = self.store.query_comm_log(peer_node_id="node-x")
        self.assertEqual(len(only_x), 2)
        only_in = self.store.query_comm_log(direction="inbound")
        self.assertEqual(len(only_in), 2)
        corr = self.store.query_comm_log(correlation_id="c1")
        self.assertEqual(len(corr), 2)

    def test_old_inbox_table_migrates_to_mailbox(self):
        """旧版库（inbox 表）初始化后迁移到 mailbox 且数据保留（防混淆重构）。"""
        db = Path(self.tmp.name) / "old_comm.db"
        c = sqlite3.connect(str(db))
        c.execute("CREATE TABLE inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, "
                  "caller_id TEXT, source_node_id TEXT, correlation_id TEXT, kind TEXT, "
                  "content TEXT, consumed INTEGER DEFAULT 0)")
        c.execute("INSERT INTO inbox(ts,caller_id,correlation_id,kind,content) "
                  "VALUES ('2026-01-01','c1','t1','task_result','{\"ok\":true}')")
        c.commit()
        c.close()
        s = Store(db)
        try:
            items = s.list_mail_all()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["correlation_id"], "t1")
            # 旧表已不存在
            rows = s._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='inbox'").fetchall()
            self.assertEqual(len(rows), 0)
        finally:
            s.close()

    def test_dual_inbox_and_mailbox_does_not_crash(self):
        """异常状态：inbox 与 mailbox 双表并存，初始化不崩溃（以新表为准）。"""
        db = Path(self.tmp.name) / "dual_comm.db"
        c = sqlite3.connect(str(db))
        c.execute("CREATE TABLE mailbox (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, "
                  "caller_id TEXT, source_node_id TEXT, correlation_id TEXT, kind TEXT, "
                  "content TEXT, consumed INTEGER DEFAULT 0)")
        c.execute("CREATE TABLE inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, "
                  "caller_id TEXT, source_node_id TEXT, correlation_id TEXT, kind TEXT, "
                  "content TEXT, consumed INTEGER DEFAULT 0)")
        c.commit()
        c.close()
        s = Store(db)   # 不应抛异常
        try:
            self.assertEqual(s.list_mail_all(), [])
            rows = s._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='inbox'").fetchall()
            self.assertEqual(len(rows), 0)
        finally:
            s.close()

    def test_inbox_consume_semantics(self):
        """2.6.9: 取走即标已读；已读不再出现。"""
        self.store.add_mail("caller-1", "node-x", "t1", "task_result", {"ok": True})
        self.store.add_mail("caller-1", "node-x", "t2", "task_result", {"ok": False})
        self.store.add_mail("caller-2", "node-x", "t3", "task_result", {"ok": True})
        items = self.store.fetch_mail("caller-1")
        self.assertEqual(len(items), 2)          # caller 隔离（2.6.5）
        items2 = self.store.fetch_mail("caller-1")
        self.assertEqual(len(items2), 0)         # 已读不再出现
        items3 = self.store.fetch_mail("caller-2")
        self.assertEqual(len(items3), 1)

    def test_inbox_all_human_view(self):
        """方案 A: 人类/面板全量查看（含已读未读、不分 caller、不标记已读）。"""
        self.store.add_mail("caller-1", "node-x", "t1", "task_result", {"ok": True})
        self.store.add_mail("caller-1", "node-x", "t2", "task_result", {"ok": False})
        self.store.add_mail("caller-2", "node-x", "t3", "task_result", {"ok": True})
        all_items = self.store.list_mail_all(limit=10)
        self.assertEqual(len(all_items), 3)      # 全量、不分 caller
        # 未读状态保留（不标记已读）
        still_unread = self.store.list_mail_all(limit=10)
        self.assertEqual(len(still_unread), 3)
        # fetch 后再查全量仍可见（已读的也保留）
        self.store.fetch_mail("caller-1")
        self.assertEqual(len(self.store.list_mail_all(limit=10)), 3)
        # 按时间倒序（最新在前）
        self.assertEqual(all_items[0]["correlation_id"], "t3")

    def test_inbox_cleanup(self):
        self.store.add_mail("c1", "n", "t", "k", {})
        self.store.fetch_mail("c1")
        self.store.add_mail("c1", "n", "t2", "k", {})
        n = self.store.cleanup_mail("consumed")
        self.assertEqual(n, 1)
        self.assertEqual(len(self.store.fetch_mail("c1")), 1)  # 未读保留

    def test_known_peers_upsert_offline_persist(self):
        """2.1.9: 已知节点持久化、离线不消失。"""
        self.store.upsert_peer("node-a", name="A", team_id="", host="1.2.3.4",
                               peer_tcp_port=40001)
        self.store.upsert_peer("node-a", name="A2", team_id="t1")
        peers = self.store.peers()
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0]["name"], "A2")
        self.assertEqual(peers[0]["team_id"], "t1")
        self.store.delete_peer("node-a")
        self.assertEqual(len(self.store.peers()), 0)

    def test_chat_history_and_conversations(self):
        self.store.add_chat("node-b", "out", "hello")
        self.store.add_chat("node-b", "in", "world")
        self.store.add_chat("node-c", "out", "other")
        hist = self.store.chat_history("node-b")
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[0]["direction"], "out")
        convs = self.store.conversations()
        self.assertEqual(len(convs), 2)


if __name__ == "__main__":
    unittest.main()
