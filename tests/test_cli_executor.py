"""集成测试: CLI 执行器基座（fake_cli 假 CLI + 可见窗口降级）。"""
import json
import tempfile
import time
import unittest
from pathlib import Path

from executors.registry import ExecutorRegistry


class _FakeCore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        from node.config import NodeConfig
        self.config = NodeConfig(data_dir)
        from node.store import Store
        self.store = Store(data_dir / "comm.db")

    def log(self, level, msg):
        pass

    def push_async_result(self, record):
        pass


class TestCliExecutor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.data_dir = Path(cls.tmp.name)
        cls.core = _FakeCore(cls.data_dir)
        fake_cli = Path(__file__).parent / "fake_cli.py"
        # executor_config.json: CLI 执行器条目（绝对路径命令）
        (cls.data_dir / "executor_config.json").write_text(json.dumps({
            "cli_executors": [
                {"agent_id": "fakecli", "name": "假CLI",
                 "command": f'"{sys_executable()}" "{fake_cli}"',
                 "concurrency": 2}
            ]}), encoding="utf-8")
        cls.reg = ExecutorRegistry(cls.core)
        cls.reg.load_plugins()
        cls.reg.start_poller()

    @classmethod
    def tearDownClass(cls):
        cls.reg.stop()
        cls.core.store.close()
        cls.tmp.cleanup()

    def test_capability_check(self):
        """2.2.12: which 命中 → 广播能力。"""
        caps = self.reg.capabilities_payload()
        self.assertIn("fakecli", [c["agentId"] for c in caps])

    def test_task_via_fake_cli(self):
        """submit → 子进程跑 fake_cli（stdin 注入提示词）→ result.md 回收。"""
        r = self.reg.submit_local("fakecli", "cli-1", "请回复：CLI 执行器正常",
                                  "sync", None, "tester", timeout=120)
        self.assertTrue(r.ok, r.detail)
        info = self.reg.wait_task("cli-1", timeout=120)
        self.assertEqual(info["status"]["state"], "completed",
                         json.dumps(info, ensure_ascii=False))
        content = info["artifacts"][0]["parts"][0]["text"]
        self.assertIn("假 CLI 执行结果", content)
        self.assertIn("CLI 执行器正常", content)  # stdin 完整送达（多行不截断）


def sys_executable() -> str:
    import sys
    return sys.executable


if __name__ == "__main__":
    unittest.main()
