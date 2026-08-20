"""单元测试: 执行器注册表（任务生命周期/结果文件契约/并发/挂起/队列语义）。"""
import tempfile
import time
import unittest
from pathlib import Path

from executors.base import (CapabilityResult, ExecutorPlugin, PluginContext,
                            SubmitResult, TaskInput)
from executors.mock_plugin import MockPlugin
from executors.registry import ExecutorRegistry


class _FakeCore:
    """为 registry 提供最小 node_core 接口（单测桩）。"""

    def __init__(self, data_dir: Path, enable_mock: bool = True):
        self.data_dir = data_dir
        # 测试需 mock 执行器，预写 node_config.json 显式开启 enable_mock
        # （生产默认关，见 node/config.py DEFAULT_CONFIG，2.x）
        cfg = data_dir / "node_config.json"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('{"enable_mock": ' + ("true" if enable_mock else "false") +
                       '}', encoding="utf-8")
        from node.config import NodeConfig
        self.config = NodeConfig(data_dir)
        from node.store import Store
        self.store = Store(data_dir / "comm.db")
        self.logs = []

    def log(self, level, msg):
        self.logs.append(f"[{level}] {msg}")

    def push_async_result(self, record):
        pass  # 单测不测推送（集成测试覆盖）


class _BlockingGuiPlugin(ExecutorPlugin):
    """并发=1 的 GUI 桩: 忙时排队（2.13.2 排队型）。"""
    plugin_id = "fakegui"
    display_name = "FakeGUI"
    executor_type = "gui"
    concurrency = 1

    def check_capability(self):
        return CapabilityResult(True)

    def submit(self, task: TaskInput) -> SubmitResult:
        import threading
        def _run():
            time.sleep(0.8)
            task.result_file.parent.mkdir(parents=True, exist_ok=True)
            task.result_file.write_text(f"gui result for {task.task_id[:6]}",
                                        encoding="utf-8")
        threading.Thread(target=_run, daemon=True).start()
        return SubmitResult(True)


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.core = _FakeCore(self.data_dir)
        self.reg = ExecutorRegistry(self.core)
        self.reg.load_plugins()
        self.reg.start_poller()

    def tearDown(self):
        self.reg.stop()
        self.core.store.close()
        self.tmp.cleanup()

    def test_mock_plugin_lifecycle(self):
        """submit → 轮询 is_done → get_result（结果文件契约 2.2.11）。"""
        r = self.reg.submit_local("mock", "task-1", "你好，执行任务", "sync",
                                  None, "test-caller")
        self.assertTrue(r.ok, r.detail)
        info = self.reg.wait_task("task-1", timeout=15)
        self.assertEqual(info["status"]["state"], "completed")
        self.assertIn("Mock 执行结果", info["artifacts"][0]["parts"][0]["text"])
        # 结果文件确实存在
        rf = self.data_dir / "executor_work" / "mock" / "task-1" / "result.md"
        self.assertTrue(rf.exists())

    def test_busy_reject_non_interactive(self):
        """2.13.2: 非交互 CLI 满并发 → busy 拒绝不排队。"""
        # mock 并发 3：提交 3 个（都在途）→ 第 4 个应 busy
        for i in range(3):
            r = self.reg.submit_local("mock", f"busy-{i}", "p", "async", None, "c")
            self.assertTrue(r.ok)
        r4 = self.reg.submit_local("mock", "busy-4", "p", "async", None, "c")
        self.assertFalse(r4.ok)
        self.assertEqual(r4.error, "busy")
        self.assertIn("不排队", r4.detail)

    def test_queue_for_gui_type(self):
        """2.13.2: GUI 并发 1 → 第二个任务排队而非拒绝。"""
        self.reg._add_plugin(_BlockingGuiPlugin, "fakegui", {})
        r1 = self.reg.submit_local("fakegui", "g1", "p1", "sync", None, "c")
        r2 = self.reg.submit_local("fakegui", "g2", "p2", "sync", None, "c")
        self.assertTrue(r1.ok)
        self.assertTrue(r2.ok, "GUI 忙时应排队而非拒绝")
        i1 = self.reg.wait_task("g1", timeout=20)
        i2 = self.reg.wait_task("g2", timeout=20)
        self.assertEqual(i1["status"]["state"], "completed")
        self.assertEqual(i2["status"]["state"], "completed")
        # 串行：g2 完成时间晚于 g1（排队等待）
        self.assertGreaterEqual(i2["status"]["timestamp"], i1["status"]["timestamp"])

    def test_suspend_gate(self):
        """4.3: 挂起拒绝任务（suspended 错误码）。"""
        self.assertTrue(self.reg.set_suspend("mock", True, reason="超额"))
        st = self.reg.status("mock")
        self.assertEqual(st["state"], "suspended")
        self.assertEqual(st["suspendReason"], "超额")
        r = self.reg.submit_local("mock", "s1", "p", "async", None, "c")
        self.assertFalse(r.ok)
        self.assertEqual(r.error, "suspended")
        # 恢复
        self.assertTrue(self.reg.set_suspend("mock", False))
        r2 = self.reg.submit_local("mock", "s2", "p", "async", None, "c")
        self.assertTrue(r2.ok)

    def test_suspend_persisted(self):
        """2.14.7: 挂起状态落盘重启后仍生效。"""
        self.reg.set_suspend("mock", True, reason="手动",
                             until="2099-01-01T00:00:00")
        reg2 = ExecutorRegistry(self.core)
        reg2.load_plugins()
        st = reg2.status("mock")
        self.assertEqual(st["state"], "suspended")
        reg2.stop()

    def test_not_installed(self):
        r = self.reg.submit_local("no-such-agent", "t", "p", "async", None, "c")
        self.assertFalse(r.ok)
        self.assertEqual(r.error, "not_installed")

    def test_timeout(self):
        """超时兜底（2.13.5 timeout 错误码）。"""
        r = self.reg.submit_local("mock", "to-1", "p", "sync", None, "c",
                                  timeout=0.2)
        self.assertTrue(r.ok)
        info = self.reg.wait_task("to-1", timeout=10)
        self.assertEqual(info["status"]["state"], "failed")
        self.assertEqual(info["metadata"].get("error"), "timeout")

    def test_capabilities_payload_only_available(self):
        """2.2.12: 探测不到的执行器不广播。"""
        import subprocess
        try:
            out = subprocess.run(["tasklist", "/fi", "imagename eq WorkBuddy.exe",
                                  "/fo", "csv", "/nh"], capture_output=True,
                                 timeout=10).stdout
            wb_running = b"WorkBuddy.exe" in out
        except Exception:
            wb_running = False
        caps = self.reg.capabilities_payload()
        agents = [c["agentId"] for c in caps]
        self.assertIn("mock", agents)
        # WorkBuddy 仅在本机实际运行时才广播（2.2.12 以启动时探测为准）
        if wb_running:
            self.assertIn("workbuddy", agents)
        else:
            self.assertNotIn("workbuddy", agents)

    def test_external_plugin_load(self):
        """2.2.10: data/plugins/ 外部插件加载。"""
        plugins_dir = self.data_dir / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)
        (plugins_dir / "ext_echo.py").write_text(
            "from executors.base import CapabilityResult, ExecutorPlugin, SubmitResult\n"
            "class ExtEchoPlugin(ExecutorPlugin):\n"
            "    plugin_id = 'ext_echo'\n"
            "    display_name = 'ExtEcho'\n"
            "    executor_type = 'non_interactive_cli'\n"
            "    concurrency = 2\n"
            "    def check_capability(self):\n"
            "        return CapabilityResult(True)\n"
            "    def submit(self, task):\n"
            "        import threading, time\n"
            "        def _r():\n"
            "            time.sleep(0.3)\n"
            "            task.result_file.parent.mkdir(parents=True, exist_ok=True)\n"
            "            task.result_file.write_text('ext ok', encoding='utf-8')\n"
            "        threading.Thread(target=_r, daemon=True).start()\n"
            "        return SubmitResult(True)\n", encoding="utf-8")
        result = self.reg.rescan()
        self.assertIn("ext_echo", result["all"])
        r = self.reg.submit_local("ext_echo", "e1", "p", "sync", None, "c")
        self.assertTrue(r.ok)
        info = self.reg.wait_task("e1", timeout=10)
        self.assertEqual(info["status"]["state"], "completed")


if __name__ == "__main__":
    unittest.main()
