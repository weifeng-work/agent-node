"""单元测试: 多锚点出站拨号（被 AP 隔离节点通用自愈）。

覆盖:
- config.peer_anchors 读写/落盘/环境变量覆盖
- core.add_anchor / remove_anchor
- mesh 锚点拨号循环: 对锚点出站建连（能 accept 即证明主动拨号）
"""
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from node.core import NodeCore
from node.config import (DEFAULT_PEER_PORT_START, DEFAULT_PEER_PORT_END,
                         DEFAULT_ANNOUNCE_TCP_PORT, DEFAULT_MULTICAST_GROUP)
from transport.mesh import MeshManager


class TestAnchorConfig(unittest.TestCase):
    def test_default_empty(self):
        with tempfile.TemporaryDirectory() as t:
            core = NodeCore(Path(t) / "data")
            try:
                self.assertEqual(core.config.peer_anchors, [])
                self.assertIn("peer_anchors", core.config.as_dict())
            finally:
                core.stop()

    def test_add_remove_persist(self):
        with tempfile.TemporaryDirectory() as t:
            core = NodeCore(Path(t) / "data")
            try:
                r = core.add_anchor("127.0.0.1", 51999)
                self.assertTrue(r["ok"])
                self.assertEqual(len(core.config.peer_anchors), 1)
                # 重复添加去重
                core.add_anchor("127.0.0.1", 51999)
                self.assertEqual(len(core.config.peer_anchors), 1)
                # 落盘
                saved = json.loads((core.config.path).read_text(encoding="utf-8-sig"))
                self.assertEqual(saved["peer_anchors"], [{"host": "127.0.0.1",
                                                          "peer_tcp_port": 51999}])
                core.remove_anchor("127.0.0.1")
                self.assertEqual(core.config.peer_anchors, [])
            finally:
                core.stop()

    def test_empty_host_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            core = NodeCore(Path(t) / "data")
            try:
                self.assertFalse(core.add_anchor("  ", 1)["ok"])
            finally:
                core.stop()

    def test_env_override(self):
        import os
        with tempfile.TemporaryDirectory() as t:
            core = NodeCore(Path(t) / "data")
            try:
                os.environ["AGENT_NODE_ANCHORS"] = "10.0.0.1:5555,10.0.0.2:5556"
                try:
                    self.assertEqual(len(core.config.peer_anchors), 2)
                    self.assertEqual(core.config.peer_anchors[0],
                                     {"host": "10.0.0.1", "peer_tcp_port": 5555})
                finally:
                    del os.environ["AGENT_NODE_ANCHORS"]
            finally:
                core.stop()


class _StubCore:
    """MeshManager 依赖的最小节点核心桩（仅锚点/扫描循环用到的接口）。"""
    def __init__(self):
        self._logs = []
        self.config = _StubConfig()

    def log(self, level, msg):
        self._logs.append((level, msg))


class _StubConfig:
    def __init__(self, ptcp: int = 0, pstart=DEFAULT_PEER_PORT_START,
                 pend=DEFAULT_PEER_PORT_END):
        self.peer_tcp_port = ptcp
        self.name = "stub"
        self.team_id = ""
        self._pstart, self._pend = pstart, pend

    def peer_port_range(self) -> list[int]:
        return list(range(self._pstart, self._pend + 1))


class TestAnchorDial(unittest.TestCase):
    def test_dials_anchor_as_outbound(self):
        """被隔离方配置锚点后，主动出站连上锚点（accept 成功 = 拨号成立）。"""
        accepted = threading.Event()
        got = {}

        # 模拟「锚点」侧：一个可被出站连接的 TCP 服务器
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def _accept():
            conn, addr = srv.accept()
            got["addr"] = addr
            accepted.set()
            try:
                conn.close()
            except Exception:
                pass

        threading.Thread(target=_accept, daemon=True).start()

        mesh = MeshManager(_StubCore(), "node-isolated-aaa", listen_port=0)
        with tempfile.TemporaryDirectory():
            mesh.start()
            try:
                mesh.start_anchor_polling(
                    lambda: [{"host": "127.0.0.1", "peer_tcp_port": port}],
                    interval=1.0)
                # 握手不成功（对端非节点）但 TCP 建连/accept 应发生 —— 证明出站拨号
                self.assertTrue(accepted.wait(10), "锚点未被主动出站连接")
                self.assertEqual(got["addr"][0], "127.0.0.1")
            finally:
                mesh.stop()


class TestPeerPortSegment(unittest.TestCase):
    def test_default_binds_within_segment(self):
        """默认(端口0)应绑定在约定段内。"""
        mesh = MeshManager(_StubCore(), "node-seg-aaa", listen_port=0)
        try:
            mesh.start()
            self.assertGreaterEqual(mesh.my_listen_port, DEFAULT_PEER_PORT_START)
            self.assertLessEqual(mesh.my_listen_port, DEFAULT_PEER_PORT_END)
        finally:
            mesh.stop()

    def test_explicit_port_is_honored(self):
        """显式配置的 peer_tcp_port 应被优先采用（而非强制回退段内）。"""
        # 临时占一个空闲端口作为显式端口目标（先绑定再关闭拿到号，随后释放）
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        explicit_port = probe.getsockname()[1]
        probe.close()
        # 显式端口不在段内，用以确认「显式优先」不被段内候选干扰
        core = _StubCore()
        core.config.peer_tcp_port = explicit_port
        mesh = MeshManager(core, "node-seg-bbb", listen_port=0)
        try:
            mesh.start()
            self.assertEqual(mesh.my_listen_port, explicit_port)
        finally:
            mesh.stop()


class TestSubnetScan(unittest.TestCase):
    def test_scan_dials_reporting_peer(self):
        """扫描对目标子网、目标段端口出站建连（accept 即证明拨号成立）。"""
        from transport import mesh as mesh_mod
        from transport.beacon import _local_ips
        local_ip = next((ip for ip in _local_ips() if not ip.startswith("127.")
                         and ip.count(".") == 3), None)
        if not local_ip:
            self.skipTest("无本地非回环 IPv4，跳过")
        old = mesh_mod.MeshManager._scan_subnets
        mesh_mod.MeshManager._scan_subnets = lambda self: {local_ip.rsplit(".", 1)[0]}
        accepted = threading.Event()
        got = {}

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def _accept():
            conn, addr = srv.accept()
            got["addr"] = addr
            accepted.set()
            try:
                conn.close()
            except Exception:
                pass

        threading.Thread(target=_accept, daemon=True).start()

        cfg = _StubConfig(ptcp=0, pstart=port, pend=port)
        mesh = MeshManager(_StubCore(), "node-scan-ccc")
        mesh.node_core.config = cfg
        # 收敛扫描范围：只扫本机末段（避免整 /24 × 1.5s 超时烧掉预算）
        mesh._scan_node_range = range(int(local_ip.rsplit(".", 1)[1]),
                                      int(local_ip.rsplit(".", 1)[1]) + 1)
        try:
            mesh._subnet_scan_once()   # 扫 <本机IP网段>.1..254 × 端口=port
        finally:
            srv.close()
            mesh_mod.MeshManager._scan_subnets = old
        self.assertTrue(accepted.wait(10),
                        "扫描未对段端口发起出站连接")


class TestDiscoverEnhancements(unittest.TestCase):
    def test_defaults(self):
        """3.x 发现增强默认值：组播组 + 固定通告端口可读。"""
        with tempfile.TemporaryDirectory() as t:
            core = NodeCore(Path(t) / "data")
            try:
                self.assertTrue(core.config.multicast_group)
                self.assertIsInstance(core.config.announce_tcp_port, int)
                self.assertEqual(core.config.announce_tcp_port, DEFAULT_ANNOUNCE_TCP_PORT)
            finally:
                core.stop()

    def test_announce_listener_replies_whoami(self):
        """固定通告端口应回一行含真实对等端口的 JSON。"""
        mesh = MeshManager(_StubCore(), "node-ann-aaa", listen_port=0)
        try:
            mesh.start()
            self.assertIsNotNone(mesh._announce_port, "通告端口未启动")
            s = socket.create_connection(("127.0.0.1", mesh._announce_port), timeout=3)
            try:
                data = s.recv(4096)
                info = json.loads(data.decode("utf-8"))
                self.assertEqual(info["node_id"], "node-ann-aaa")
                self.assertEqual(info["peer_tcp_port"], mesh.my_listen_port)
            finally:
                s.close()
        finally:
            mesh.stop()

    def test_subnet_scan_uses_announce_fastpath(self):
        """扫描 '' 号：_announce_probe 应通过通告端口拿到对端真实端口并建连。"""
        # 起一个真正支持通告端口的节点
        srv_core = _StubCore()
        srv_core.config = _StubConfig()
        server = MeshManager(srv_core, "node-ann-target", listen_port=0)
        try:
            server.start()
            # 扫描方克隆一个 _StubConfig，扫 127 所在子网；收敛只扫 127.0.0.1
            mesh = MeshManager(_StubCore(), "node-ann-scan", listen_port=0)
            mesh._scan_timeout = 1.0
            mesh._scan_node_range = range(1, 2)   # 只扫 .1 => 127.0.0.1
            try:
                ok = mesh._announce_probe("127.0.0.1")
                self.assertTrue(ok, "通告快速通道未建立到 server 的连接")
            finally:
                mesh.stop()
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()