"""单元测试: CodeBuddy stdout 解析器（事件数组/杂音容错/auth 检测）。"""
import json
import unittest

from executors.adapters.codebuddy import (_extract_result_text,
                                          _looks_like_auth_failure)


class TestCodeBuddyParse(unittest.TestCase):
    def test_event_array_with_result(self):
        stdout = json.dumps([
            {"type": "message", "role": "user", "content": [
                {"type": "text", "text": "任务指令"}]},
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "中间回答"}]},
            {"type": "result", "result": "最终结论：一切正常。",
             "session_id": "sess-123"},
        ], ensure_ascii=False)
        out, sid = _extract_result_text(stdout)
        self.assertEqual(out, "最终结论：一切正常。")
        self.assertEqual(sid, "sess-123")

    def test_noise_wrapped_array(self):
        stdout = ('[debug] session start\n'
                  '[{"type":"result","result":"结论 B","session_id":"s2"}]\n'
                  'some trailing log')
        out, sid = _extract_result_text(stdout)
        self.assertEqual(out, "结论 B")
        self.assertEqual(sid, "s2")

    def test_result_is_json_string(self):
        inner = [{"type": "message", "role": "assistant",
                  "content": [{"type": "output_text", "text": "嵌套结论"}]}]
        evt = {"type": "result", "result": json.dumps(inner, ensure_ascii=False),
               "session_id": "s3"}
        out, _ = _extract_result_text(json.dumps([evt]))
        self.assertIn("嵌套结论", out)

    def test_assistant_fallback(self):
        stdout = json.dumps([
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "无 result 事件的回复"}]}])
        out, _ = _extract_result_text(stdout)
        self.assertIn("无 result 事件的回复", out)

    def test_garbage_returns_empty(self):
        out, sid = _extract_result_text("not json at all")
        self.assertEqual(out, "")
        self.assertIsNone(sid)

    def test_auth_detection(self):
        self.assertTrue(_looks_like_auth_failure("CodeBuddy 未登录，请先登录"))
        self.assertTrue(_looks_like_auth_failure("Error: 401 Unauthorized"))
        self.assertTrue(_looks_like_auth_failure("", "login required"))
        self.assertFalse(_looks_like_auth_failure("任务完成，输出正常"))
        # 误报防护: 任务内容含 401 不应触发（单词边界场景由调用方长文本承担）
        self.assertFalse(_looks_like_auth_failure("返回了 3 个结果"))


if __name__ == "__main__":
    unittest.main()
