"""单元测试: comm.db 存储（通信日志/inbox/known peers/聊天）。"""
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

    def test_inbox_consume_semantics(self):
        """2.6.9: 取走即标已读；已读不再出现。"""
        self.store.add_inbox("caller-1", "node-x", "t1", "task_result", {"ok": True})
        self.store.add_inbox("caller-1", "node-x", "t2", "task_result", {"ok": False})
        self.store.add_inbox("caller-2", "node-x", "t3", "task_result", {"ok": True})
        items = self.store.fetch_inbox("caller-1")
        self.assertEqual(len(items), 2)          # caller 隔离（2.6.5）
        items2 = self.store.fetch_inbox("caller-1")
        self.assertEqual(len(items2), 0)         # 已读不再出现
        items3 = self.store.fetch_inbox("caller-2")
        self.assertEqual(len(items3), 1)

    def test_inbox_cleanup(self):
        self.store.add_inbox("c1", "n", "t", "k", {})
        self.store.fetch_inbox("c1")
        self.store.add_inbox("c1", "n", "t2", "k", {})
        n = self.store.cleanup_inbox("consumed")
        self.assertEqual(n, 1)
        self.assertEqual(len(self.store.fetch_inbox("c1")), 1)  # 未读保留

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
