"""单元测试: CLI 工具（tools/cli.py）。

验证所有子命令的 argparse 参数解析、call() 调用结构、JSON 输出。
不依赖运行中的面板——mock cli.call() 返回固定响应。
"""
import json
import unittest
from unittest.mock import patch, ANY
from tools import cli


class TestCliArgparse(unittest.TestCase):
    """验证每个子命令能正确解析参数并调 call()。"""

    def test_list(self):
        with patch.object(cli, "call", return_value={"ok": True}) as m:
            self.assertEqual(cli.main(["list"]), 0)
            m.assert_called_once_with("GET", "/api/nodes")

    def test_send(self):
        with patch.object(cli, "call", return_value={"ok": True}) as m:
            self.assertEqual(cli.main(["send", "--to", "node-abc",
                                       "--text", "hello"]), 0)
            m.assert_called_once_with("POST", "/api/chat/send",
                                      {"target_node_id": "node-abc",
                                       "text": "hello"})

    def test_task(self):
        with patch.object(cli, "call", return_value={"ok": True}) as m:
            self.assertEqual(cli.main(["task", "--target", "node-x",
                                       "--executor", "wb",
                                       "--prompt", "do it"]), 0)
            m.assert_called_once()

    def test_info(self):
        with patch.object(cli, "call", return_value={"ok": True}) as m:
            self.assertEqual(cli.main(["info"]), 0)
            m.assert_called_once_with("GET", "/api/overview")

    def test_config(self):
        with patch.object(cli, "call", return_value={"ok": True}) as m:
            self.assertEqual(cli.main(["config"]), 0)
            m.assert_called_once_with("GET", "/api/settings")

    def test_rename(self):
        with patch.object(cli, "call", return_value={"ok": True}) as m:
            self.assertEqual(cli.main(["rename", "new-name"]), 0)
            m.assert_called_once_with("POST", "/api/settings/name",
                                      {"name": "new-name"})

    def test_team(self):
        with patch.object(cli, "call", return_value={"ok": True}) as m:
            self.assertEqual(cli.main(["team", "team-a"]), 0)
            m.assert_called_once_with("POST", "/api/settings/team",
                                      {"team_id": "team-a"})

    def test_switch(self):
        with patch.object(cli, "call", return_value={"ok": True}) as m:
            self.assertEqual(cli.main(["switch", "allow_shell", "on"]), 0)
            m.assert_called_once_with("POST", "/api/settings/switch",
                                      {"switch": "allow_shell", "enabled": True})

    def test_admin(self):
        with patch.object(cli, "call", return_value={"ok": True}) as m:
            self.assertEqual(cli.main(["admin", "on"]), 0)
            m.assert_called_once_with("POST", "/api/settings/admin",
                                      {"enabled": True})

    def test_peer_add(self):
        with patch.object(cli, "call", return_value={"ok": True}) as m:
            self.assertEqual(cli.main(["peer", "add", "192.168.1.10",
                                       "49715"]), 0)
            m.assert_called_once_with("POST", "/api/peers/add_manual",
                                      {"host": "192.168.1.10",
                                       "peer_tcp_port": 49715})

    def test_peer_remove(self):
        with patch.object(cli, "call", return_value={"ok": True}) as m:
            self.assertEqual(cli.main(["peer", "remove", "192.168.1.10"]), 0)
            m.assert_called_once_with("POST", "/api/peers/remove_manual",
                                      {"host": "192.168.1.10"})

    def test_conversations(self):
        with patch.object(cli, "call", return_value={"ok": True}) as m:
            self.assertEqual(cli.main(["conversations"]), 0)
            m.assert_called_once_with("GET", "/api/chat/conversations")

    def test_history(self):
        with patch.object(cli, "call", return_value={"ok": True}) as m:
            self.assertEqual(cli.main(["history", "--peer", "node-abc"]), 0)
            m.assert_called_with("GET", "/api/chat/history?peer=node-abc")

    def test_mailbox_all(self):
        with patch.object(cli, "call", return_value={"ok": True}) as m:
            self.assertEqual(cli.main(["mailbox-all"]), 0)
            m.assert_called_once_with("GET", "/api/mailbox/all")

    def test_mailbox_clean(self):
        with patch.object(cli, "call", return_value={"ok": True}) as m:
            self.assertEqual(cli.main(["mailbox-clean", "--mode",
                                       "consumed"]), 0)
            m.assert_called_once_with("POST", "/api/mailbox/cleanup",
                                      {"mode": "consumed"})

    def test_executor_status(self):
        with patch.object(cli, "call", return_value={"ok": True}) as m:
            self.assertEqual(cli.main(["executor", "status", "wb"]), 0)
            m.assert_called_once_with("GET", "/api/executors/status?executor_id=wb")

    def test_executor_restart(self):
        with patch.object(cli, "call", return_value={"ok": True}) as m:
            self.assertEqual(cli.main(["executor", "restart", "wb"]), 0)
            m.assert_called_once_with("POST", "/api/executors/restart",
                                      {"executor_id": "wb"})

    def test_executor_sessions(self):
        with patch.object(cli, "call", return_value={"ok": True}) as m:
            self.assertEqual(cli.main(["executor", "sessions"]), 0)
            m.assert_called_once_with("GET", "/api/executors/sessions")

    def test_comm_log(self):
        with patch.object(cli, "call", return_value={"ok": True}) as m:
            self.assertEqual(cli.main(["comm-log", "--peer", "node-abc"]), 0)
            m.assert_called_once_with("GET",
                                      "/api/logs/comm?peer=node-abc")

    def test_node_log(self):
        with patch.object(cli, "call", return_value={"ok": True}) as m:
            self.assertEqual(cli.main(["node-log", "--lines", "50"]), 0)
            m.assert_called_once_with("GET", "/api/logs/node?lines=50")

    def test_plugins(self):
        with patch.object(cli, "call", return_value={"ok": True}) as m:
            self.assertEqual(cli.main(["plugins"]), 0)
            m.assert_called_once_with("GET", "/api/plugins")

    def test_plugin_push(self):
        with patch.object(cli, "call", return_value={"ok": True}) as m:
            self.assertEqual(cli.main(["plugin", "push", "--node",
                                       "node-abc", "--path",
                                       "plugin.zip"]), 0)
            m.assert_called_once_with("POST", "/api/plugins/distribute",
                                      {"target_node_id": "node-abc",
                                       "plugin_path": "plugin.zip"})


class TestCliCallerId(unittest.TestCase):
    """验证 caller_id 注入逻辑。"""

    @patch.dict(cli.os.environ, {"AGENT_NODE_CALLER_ID": "test-caller"})
    def test_env_var_priority(self):
        self.assertEqual(cli.caller_id(), "test-caller")

    def test_missing_caller_id(self):
        with patch.object(cli, "CALLER_FILE",
                          new=cli.Path("/nonexistent/caller.json")):
            with self.assertRaises(SystemExit) as cm:
                cli.caller_id()
            self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()