"""集成测试: MCP Server（stdio JSON-RPC 薄桥 → 面板 REST → 节点核心）。

以子进程拉起 `python -m mcp.server`（模拟 AI 客户端 stdio 方式，2.6.3），
验证 initialize 握手 / tools/list / tools/call / caller_id 收件箱归属（caller_id 由父进程名自动派生，无需配置）。
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from mcp.server import get_caller_id
from node.core import NodeCore

PORT = 5291


class TestMcpServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        # MCP 任务链路依赖 mock 执行器（生产默认关闭），预写配置显式开启（2.x）
        d = Path(cls.tmp.name) / "m"
        d.mkdir(parents=True, exist_ok=True)
        (d / "node_config.json").write_text('{"enable_mock": true}',
                                            encoding="utf-8")
        cls.core = NodeCore(d, panel_port=PORT)
        cls.core.config.sync_enabled = False
        cls.core.start()
        threading.Thread(target=_serve, args=(cls.core, PORT), daemon=True).start()
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                import urllib.request
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health",
                                            timeout=2) as r:
                    json.loads(r.read())
                break
            except Exception:
                time.sleep(0.4)
        env = dict(os.environ)
        env["AGENT_NODE_PANEL"] = f"http://127.0.0.1:{PORT}"
        env["PYTHONIOENCODING"] = "utf-8"
        # 不设 AGENT_NODE_CALLER_ID：caller_id 由系统按父进程名自动派生（2.6.3/2.6.4）
        cls.proc = subprocess.Popen(
            [sys.executable, "-m", "mcp.server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env, cwd=str(Path(__file__).parent.parent),
            text=True, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.proc.stdin.close()
            cls.proc.wait(timeout=5)
        except Exception:
            cls.proc.kill()
        cls.core.stop()
        cls.tmp.cleanup()

    def _rpc(self, msg: dict) -> dict:
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        return json.loads(line)

    def test_01_initialize(self):
        r = self._rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {}})
        self.assertEqual(r["id"], 1)
        self.assertIn("tools", r["result"]["capabilities"])
        self.assertTrue(r["result"]["serverInfo"]["name"])

    def test_02_tools_list(self):
        r = self._rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = r["result"]["tools"]
        names = {t["name"] for t in tools}
        # 2.5.9 工具面关键工具齐备
        for expected in ("get_skill", "get_node_info", "list_nodes", "send_text",
                         "forget_node", "purge_node", "list_dir", "file_push",
                         "file_pull", "list_executors", "submit_task",
                         "get_task_result", "check_inbox", "get_executor_status",
                         "set_executor_suspend", "restart_plugin", "get_comm_log",
                         "get_node_log", "get_config", "rename_node", "set_team",
                         "set_control_state", "shell_exec", "sync_now",
                         "distribute_plugin"):
            self.assertIn(expected, names, f"缺少 MCP 工具: {expected}")
        # 每个工具有 inputSchema
        for t in tools:
            self.assertIn("inputSchema", t)

    def test_03_get_node_info(self):
        r = self._rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "get_node_info", "arguments": {}}})
        inner = json.loads(r["result"]["content"][0]["text"])
        self.assertEqual(inner["nodeId"], self.core.node_id)

    def test_04_submit_task_and_inbox(self):
        """AI 经 MCP 提交异步任务 → 回执进该 caller_id 私有邮箱（2.6.5）。"""
        r = self._rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                       "params": {"name": "submit_task", "arguments": {
                           "executor_id": "mock", "prompt": "MCP 链路任务",
                           "mode": "async", "timeout": 60}}})
        inner = json.loads(r["result"]["content"][0]["text"])
        self.assertTrue(inner["ok"], inner)
        # 轮询 inbox（经 MCP 工具，caller_id 自动注入）
        deadline = time.time() + 40
        items = []
        while time.time() < deadline:
            r2 = self._rpc({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                            "params": {"name": "check_inbox", "arguments": {}}})
            items = json.loads(r2["result"]["content"][0]["text"])["items"]
            if items:
                break
            time.sleep(1)
        self.assertTrue(items, "MCP caller 应收到自己的异步回执")
        self.assertIn("Mock 执行结果", items[0]["content"]["content"])
        # 面板 caller（panel）看不到 MCP 进程的异步回执（caller 隔离）
        panel_items = self.core.check_inbox("panel")
        self.assertFalse(any(i["correlation_id"] == inner["taskId"]
                             for i in panel_items))

    def test_06_caller_id_auto_derived(self):
        """caller_id 无需配置：进程内自动派生且缓存稳定（2.6.4）。"""
        a = get_caller_id()
        b = get_caller_id()
        self.assertEqual(a, b, "caller_id 应在进程生命周期内保持稳定")
        self.assertTrue(a, "caller_id 不应为空")

    def test_05_shell_exec_local(self):
        r = self._rpc({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                       "params": {"name": "shell_exec", "arguments": {
                           "command": "echo mcp-shell-ok"}}})
        inner = json.loads(r["result"]["content"][0]["text"])
        self.assertTrue(inner["ok"], inner)
        self.assertIn("mcp-shell-ok", inner["output"])


def _serve(core, port):
    from server.panel import serve
    try:
        serve(core, port)
    except Exception:
        pass


if __name__ == "__main__":
    unittest.main()
