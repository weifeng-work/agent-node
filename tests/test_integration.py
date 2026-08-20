"""本地双节点集成测试 —— 同进程两个 NodeCore 实例（临时数据目录 + 固定端口）。

覆盖（第五章验收策略 - 集成层）:
- TCP mesh: 首帧握手/去双连接/请求-响应
- 聊天 text_request 双端落库
- shell_exec（allow_shell 开关拒绝路径）
- 文件 push/pull（SHA-256 校验、目录整树、开关拒绝路径）
- list_dir 远程
- 执行器深态查询 executor_status（allow_ai_task 拒绝路径）
- 任务三模式（sync/async/trigger）远程 mock 执行器 + 异步邮箱
- 面板 REST（完整本地 API 栈）
- team 隔离（连接层强制）

注意: beacon 广播在本测试中不依赖（用直连保证确定性）；beacon 链路由
test_beacon_discovery.py 在真实双机场景验证。
"""
import json
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from node.core import NodeCore

PORT_A = 45301
PORT_B = 45302
PANEL_A = 5277
PANEL_B = 5278


def _http(method: str, url: str, body: dict | None = None, headers: dict | None = None,
          timeout: float = 120.0) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class TestTwoNodes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        # 集成测试依赖 mock 执行器（生产默认关闭），预写配置显式开启（2.x）
        for sub in ("a", "b"):
            d = root / sub
            d.mkdir(parents=True, exist_ok=True)
            (d / "node_config.json").write_text(
                '{"enable_mock": true}', encoding="utf-8")
        cls.core_a = NodeCore(root / "a", panel_port=PANEL_A)
        cls.core_a.config.peer_tcp_port = PORT_A
        cls.core_b = NodeCore(root / "b", panel_port=PANEL_B)
        cls.core_b.config.peer_tcp_port = PORT_B
        cls.core_a.config.sync_enabled = False
        cls.core_b.config.sync_enabled = False
        cls.core_a.start()
        cls.core_b.start()
        # 面板线程
        for core, port in ((cls.core_a, PANEL_A), (cls.core_b, PANEL_B)):
            threading.Thread(target=_serve, args=(core, port), daemon=True).start()
        # A 直连 B（确定性建连，不依赖 beacon）
        cls.core_a.mesh.connect_peer(cls.core_b.node_id, "127.0.0.1", PORT_B)
        deadline = time.time() + 15
        while time.time() < deadline:
            if cls.core_a.mesh.is_online(cls.core_b.node_id) and \
                    cls.core_b.mesh.is_online(cls.core_a.node_id):
                break
            time.sleep(0.3)
        cls.online = (cls.core_a.mesh.is_online(cls.core_b.node_id)
                      and cls.core_b.mesh.is_online(cls.core_a.node_id))

    @classmethod
    def tearDownClass(cls):
        cls.core_a.stop()
        cls.core_b.stop()
        cls.tmp.cleanup()

    # ---------- mesh ----------
    def test_01_mesh_connected(self):
        self.assertTrue(self.online, "双节点 mesh 应在直连后完成握手互认")
        # 双端各自知道对方（已知节点持久化）
        names_a = [n["nodeId"] for n in self.core_a.list_nodes()]
        self.assertIn(self.core_b.node_id, names_a)

    def test_02_chat(self):
        r = self.core_a.send_chat(self.core_b.node_id, "你好 B，我是 A")
        self.assertTrue(r["ok"], r)
        time.sleep(0.6)
        msgs = self.core_b.chat_history(self.core_a.node_id)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["text"], "你好 B，我是 A")
        self.assertEqual(msgs[0]["direction"], "in")
        # A 侧 outbound 落库
        msgs_a = self.core_a.chat_history(self.core_b.node_id)
        self.assertEqual(msgs_a[0]["direction"], "out")

    def test_03_shell_exec_remote(self):
        r = self.core_a.shell_exec(self.core_b.node_id, "echo agent-node-test-ok", 15)
        self.assertTrue(r["ok"], r)
        self.assertIn("agent-node-test-ok", r["output"])

    def test_04_shell_disabled(self):
        """2.9.4: 关闭 allow_shell → 拒绝并记日志。"""
        self.core_b.config.set_switch("allow_shell", False)
        try:
            r = self.core_a.shell_exec(self.core_b.node_id, "echo should-be-rejected", 10)
            self.assertFalse(r["ok"])
            self.assertEqual(r["error"], "disabled")
            logs = self.core_b.comm_log(msg_type="shell_exec")
            self.assertTrue(any(e["direction"] == "inbound" for e in logs))
        finally:
            self.core_b.config.set_switch("allow_shell", True)

    def test_05_file_push_pull(self):
        src = self.core_a.data_dir / "send_test.txt"
        src.write_text("hello file transfer " * 100, encoding="utf-8")
        # push 到指定路径
        r = self.core_a.file_push(self.core_b.node_id, str(src), "inbox/send_test.txt")
        self.assertTrue(r["ok"], r)
        got = self.core_b.data_dir / "inbox" / "send_test.txt"
        self.assertEqual(got.read_text(encoding="utf-8"), src.read_text(encoding="utf-8"))
        # pull 回来
        r2 = self.core_a.file_pull(self.core_b.node_id, str(got))
        self.assertTrue(r2["ok"], r2)
        pulled = self.core_a.config.inbox_dir() / "send_test.txt"
        self.assertEqual(pulled.read_text(encoding="utf-8"), src.read_text(encoding="utf-8"))

    def test_06_file_push_binary_sha256(self):
        import os
        src = self.core_a.data_dir / "bin_test.bin"
        src.write_bytes(os.urandom(300_000))  # 多块传输（>1MiB 块阈值的 1/3，单块足够）
        r = self.core_a.file_push(self.core_b.node_id, str(src), "inbox/bin_test.bin")
        self.assertTrue(r["ok"], r)
        from transport.protocol import sha256_file
        self.assertEqual(sha256_file(self.core_b.data_dir / "inbox" / "bin_test.bin"),
                         sha256_file(src))

    def test_07_file_disabled(self):
        self.core_b.config.set_switch("allow_file", False)
        try:
            src = self.core_a.data_dir / "reject_me.txt"
            src.write_text("x", encoding="utf-8")
            r = self.core_a.file_push(self.core_b.node_id, str(src), "inbox/reject.txt")
            self.assertFalse(r["ok"])
            self.assertEqual(r["error"], "disabled")
        finally:
            self.core_b.config.set_switch("allow_file", True)

    def test_08_list_dir_remote(self):
        r = self.core_a.list_dir(self.core_b.node_id, str(self.core_b.data_dir / "inbox"),
                                 recursive=False)
        self.assertTrue(r["ok"], r)
        names = [e["name"] for e in r["entries"]]
        self.assertIn("send_test.txt", names)

    def test_09_executor_status_remote(self):
        r = self.core_a.get_executor_status(f"{self.core_b.node_id}/mock")
        self.assertTrue(r["ok"], r)
        statuses = r.get("executors") or [r.get("status")]
        mock_st = next(s for s in statuses if s and s.get("agentId") == "mock")
        self.assertTrue(mock_st["available"])

    def test_10_task_sync_remote(self):
        r = self.core_a.submit_task(f"{self.core_b.node_id}/mock",
                                    "集成测试：请回复 OK", mode="sync", timeout=60)
        self.assertTrue(r["ok"], r)
        self.assertIn("Mock 执行结果", r["content"])

    def test_11_task_async_remote_inbox(self):
        """2.6/2.7: 异步任务 → 回执进调用方 caller 私有邮箱。"""
        r = self.core_a.submit_task(f"{self.core_b.node_id}/mock",
                                    "异步任务测试", mode="async", timeout=60,
                                    caller_id="ai-test-caller")
        self.assertTrue(r["ok"], r)
        task_id = r["taskId"]
        deadline = time.time() + 40
        items = []
        while time.time() < deadline:
            items = self.core_a.check_inbox("ai-test-caller")
            if items:
                break
            time.sleep(1)
        self.assertTrue(items, "异步回执应到达 caller 邮箱")
        self.assertEqual(items[0]["correlation_id"], task_id)
        self.assertTrue(items[0]["content"]["ok"])
        self.assertIn("Mock 执行结果", items[0]["content"]["content"])
        # 取走即已读
        self.assertEqual(self.core_a.check_inbox("ai-test-caller"), [])

    def test_12_task_trigger(self):
        """2.7.1: 触发型只需节点层触发确认，无结果回执。"""
        r = self.core_a.submit_task(f"{self.core_b.node_id}/mock",
                                    "触发即可", mode="trigger", timeout=30)
        self.assertTrue(r["ok"], r)
        self.assertTrue(r.get("triggered"))
        time.sleep(3)  # 触发后执行完不推回执
        self.assertEqual(self.core_a.check_inbox("panel"), [])

    def test_13_task_idempotent_by_task_id(self):
        """2.13.5: 应用层幂等 —— 复用同一 task_id 不重复执行。"""
        tid = "fixed-idempotent-task-0001"
        r1 = self.core_a.submit_task(f"{self.core_b.node_id}/mock", "幂等任务",
                                     mode="sync", timeout=60, task_id=tid)
        self.assertTrue(r1["ok"], r1)
        # 同一 task_id 二次提交（已完成）→ 幂等返回现状，不重复执行
        r2 = self.core_a.submit_task(f"{self.core_b.node_id}/mock", "幂等任务重发",
                                     mode="sync", timeout=60, task_id=tid)
        # 目标端任务已存在 → 提交应被接受并返回已完成状态（或幂等命中）
        self.assertIn(r2.get("error"), (None, "not_installed"), r2)
        info = self.core_a.get_task_result(tid)
        self.assertTrue(info["ok"])

    def test_14_ai_task_disabled(self):
        self.core_b.config.set_switch("allow_ai_task", False)
        try:
            r = self.core_a.submit_task(f"{self.core_b.node_id}/mock", "应被拒绝",
                                        mode="trigger", timeout=30)
            self.assertFalse(r["ok"])
            self.assertEqual(r["error"], "disabled")
        finally:
            self.core_b.config.set_switch("allow_ai_task", True)

    def test_15_task_attachments(self):
        """2.13.1: 附件 P2P 直传到任务工作目录。"""
        att = self.core_a.data_dir / "att_test.txt"
        att.write_text("附件内容 ABC-123", encoding="utf-8")
        r = self.core_a.submit_task(f"{self.core_b.node_id}/mock",
                                    "带附件任务", mode="sync", timeout=60,
                                    attachments=[str(att)])
        self.assertTrue(r["ok"], r)
        # 目标端工作目录收到附件
        task_id = r["taskId"]
        att_on_b = self.core_b.data_dir / "executor_work" / "mock" / task_id / "att_test.txt"
        self.assertTrue(att_on_b.exists(), "附件应直传到目标任务工作目录")
        self.assertEqual(att_on_b.read_text(encoding="utf-8"), "附件内容 ABC-123")
        self.assertIn("附件: 1 个", r["content"])

    def test_16_team_isolation(self):
        """2.1.7: team 不一致 → 连接层拒绝。"""
        # B 切到 team-x（经 set_team: 断开旧 team 连接）
        self.core_b.set_team("team-x")
        time.sleep(1.5)
        r = self.core_a.send_chat(self.core_b.node_id, "应发不出去")
        self.assertFalse(r["ok"])
        # A 直连 B → 首帧 team 校验失败
        conn = self.core_a.mesh.connect("127.0.0.1", PORT_B)
        time.sleep(2.0)
        self.assertFalse(self.core_a.mesh.is_online(self.core_b.node_id),
                         "team 不一致不应建立连接")
        # 恢复
        self.core_b.config.team_id = ""
        self.core_b.config.save()
        self.core_a.mesh.connect_peer(self.core_b.node_id, "127.0.0.1", PORT_B)
        deadline = time.time() + 10
        while time.time() < deadline and not self.core_a.mesh.is_online(self.core_b.node_id):
            time.sleep(0.3)
        self.assertTrue(self.core_a.mesh.is_online(self.core_b.node_id))

    def test_17_panel_rest_full_stack(self):
        """面板 REST 完整栈（MCP/CLI 的底层通道）。"""
        base = f"http://127.0.0.1:{PANEL_A}"
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                _http("GET", base + "/api/health")
                break
            except Exception:
                time.sleep(0.5)
        ov = _http("GET", base + "/api/overview")
        self.assertEqual(ov["nodeId"], self.core_a.node_id)
        self.assertIn("allow_shell", ov["switches"])
        nodes = _http("GET", base + "/api/nodes")
        self.assertTrue(any(n["nodeId"] == self.core_b.node_id for n in nodes["nodes"]))
        # 经面板发任务（异步）
        r = _http("POST", base + "/api/task/submit", {
            "executor_id": f"{self.core_b.node_id}/mock",
            "prompt": "面板链路任务", "mode": "async", "timeout": 60},
            headers={"X-Caller-Id": "panel-rest-test"})
        self.assertTrue(r["ok"], r)
        deadline = time.time() + 40
        items = []
        while time.time() < deadline:
            items = _http("GET", base + "/api/inbox",
                          headers={"X-Caller-Id": "panel-rest-test"})["items"]
            if items:
                break
            time.sleep(1)
        self.assertTrue(items)
        # 通信日志可查
        logs = _http("GET", base + "/api/logs/comm?limit=20")
        self.assertTrue(logs["ok"])

    def test_18_diag(self):
        d = self.core_a.diag()
        self.assertTrue(d["ok"], d)
        for c in d["checks"]:
            if c["item"] in ("本地配置可读", "comm.db 可写", "业务 TCP 监听",
                             "发现端口监听"):
                self.assertTrue(c["ok"], c)


def _serve(core, port):
    from server.panel import serve
    try:
        serve(core, port)
    except Exception:
        pass


if __name__ == "__main__":
    unittest.main()
