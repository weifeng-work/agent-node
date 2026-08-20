"""单元测试: TraeWork 适配器纯逻辑（CDP 依赖惰性，不外连真实 TraeWork）。

覆盖: 回复噪音清洗、运行/停止正则、能力自检三种分支、提交前的 CDP 就绪门控、提示词拼接。
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from executors.base import PluginContext, TaskInput
from executors.adapters.traework import (_RUNNING_RE, _STOPPED_RE, TraeWorkPlugin,
                                         _clean_reply)


def _plugin(tmp: str) -> TraeWorkPlugin:
    ctx = PluginContext("node-x", "traework", Path(tmp), {"cdp_port": 9433})
    return TraeWorkPlugin(ctx)


def _task(tmp: str, task_id: str = "abcd1234efgh") -> TaskInput:
    work = Path(tmp) / "work"
    work.mkdir(parents=True, exist_ok=True)
    return TaskInput(task_id, "整理一下代码", ["C:\\foo\\doc.md"],
                     work / "result.md", 60)


class TestCleanReply(unittest.TestCase):
    def test_strips_chrome_noise_lines(self):
        text = "TraeWork\n内容由你生成的内容\n正在搜索文件\n答案正文第一行\n答案正文第二行"
        cleaned = _clean_reply(text)
        self.assertIn("答案正文第一行", cleaned)
        self.assertIn("答案正文第二行", cleaned)
        self.assertNotIn("TraeWork", cleaned)
        self.assertNotIn("正在搜索文件", cleaned)

    def test_empty_and_blank(self):
        self.assertEqual(_clean_reply(""), "")
        self.assertEqual(_clean_reply("  \n  "), "")


class TestRegexes(unittest.TestCase):
    def test_running(self):
        self.assertTrue(_RUNNING_RE.search("正在思考中"))
        self.assertTrue(_RUNNING_RE.search("生成处理中"))
        self.assertFalse(_RUNNING_RE.search("已经完成，无运行"))

    def test_stopped(self):
        self.assertTrue(_STOPPED_RE.search("已停止"))
        self.assertTrue(_STOPPED_RE.search("执行失败"))
        self.assertFalse(_STOPPED_RE.search("任务完成"))


class TestPromptBuild(unittest.TestCase):
    def test_contains_tag_result_and_attachments(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = _plugin(tmp)
            task = _task(tmp)
            tag = task.task_id[:8]
            result = str(task.result_file.resolve())
            prompt = plugin._build_prompt(task, tag, result)
            self.assertIn(tag, prompt)
            self.assertIn(result, prompt)
            self.assertIn("【附件】", prompt)
            self.assertIn("C:\\foo\\doc.md", prompt)
            self.assertIn(f"任务完成 {tag}", prompt)


class TestCapability(unittest.TestCase):
    def test_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = _plugin(tmp)
            with mock.patch.object(plugin.cdp, "probe_ready",
                                   return_value=(True, None)):
                cap = plugin.check_capability()
        self.assertTrue(cap.available)

    def test_running_but_no_cdp(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = _plugin(tmp)
            with mock.patch.object(plugin.cdp, "probe_ready",
                                   return_value=(False, "no cdp")), \
                    mock.patch.object(TraeWorkPlugin, "_traework_running",
                                      return_value=True):
                cap = plugin.check_capability()
        self.assertFalse(cap.available)
        self.assertIn("CDP", cap.reason)

    def test_not_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = _plugin(tmp)
            with mock.patch.object(plugin.cdp, "probe_ready",
                                   return_value=(False, "no cdp")), \
                    mock.patch.object(TraeWorkPlugin, "_traework_running",
                                      return_value=False):
                cap = plugin.check_capability()
        self.assertFalse(cap.available)
        self.assertIn("启动 TraeWork", cap.reason)


class TestSubmit(unittest.TestCase):
    def test_rejects_when_cdp_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = _plugin(tmp)
            task = _task(tmp)
            with mock.patch.object(plugin.cdp, "probe_ready",
                                   return_value=(False, "no cdp")):
                r = plugin.submit(task)
        self.assertFalse(r.ok)
        self.assertEqual(r.error, "not_installed")

    def test_submit_ok_increments_inflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = _plugin(tmp)
            plugin._inject_prompt = lambda prompt, tag: None      # 不接真实 CDP
            plugin._harvest_loop = lambda task: None              # 不真轮询
            task = _task(tmp)
            with mock.patch.object(plugin.cdp, "probe_ready",
                                   return_value=(True, None)):
                r = plugin.submit(task)
            self.assertTrue(r.ok)
            self.assertEqual(plugin._inflight, 1)


if __name__ == "__main__":
    unittest.main()