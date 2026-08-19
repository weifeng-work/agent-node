"""单元测试: 附录 A 线级协议（帧格式/信封/文件块）。"""
import socket
import unittest

from transport import protocol as P


class TestProtocol(unittest.TestCase):
    def test_frame_roundtrip(self):
        a, b = socket.socketpair()
        try:
            P.send_frame(a, P.FRAME_JSON, b'{"hello":"world"}')
            ftype, payload = P.read_frame(b)
            self.assertEqual(ftype, P.FRAME_JSON)
            self.assertEqual(payload, b'{"hello":"world"}')
        finally:
            a.close()
            b.close()

    def test_empty_payload_frame(self):
        a, b = socket.socketpair()
        try:
            P.send_frame(a, P.FRAME_JSON, b"")
            ftype, payload = P.read_frame(b)
            self.assertEqual(ftype, P.FRAME_JSON)
            self.assertEqual(payload, b"")
        finally:
            a.close()
            b.close()

    def test_frame_too_large_rejected(self):
        with self.assertRaises(ValueError):
            P.encode_frame(P.FRAME_JSON, b"x" * (P.MAX_FRAME + 1))

    def test_read_frame_oversize_header_returns_none(self):
        a, b = socket.socketpair()
        try:
            # 伪造超长长度头
            a.sendall(P.struct_ if False else __import__("struct").pack(">BI", 1, P.MAX_FRAME + 1))
            self.assertIsNone(P.read_frame(b))
        finally:
            a.close()
            b.close()

    def test_file_block_pack_unpack(self):
        ftid = b"\x01" * 16
        frame = P.pack_file_block(ftid, 42, b"abc")
        got_ftid, seq, data = P.unpack_file_block(frame)
        self.assertEqual(got_ftid, ftid)
        self.assertEqual(seq, 42)
        self.assertEqual(data, b"abc")

    def test_envelope_shape(self):
        env = P.make_envelope(P.T_PING, "node-a", "node-b", {"k": 1},
                              correlation_id="corr-1")
        self.assertEqual(env["v"], 1)
        self.assertEqual(env["type"], P.T_PING)
        self.assertEqual(env["sender_node_id"], "node-a")
        self.assertEqual(env["target_node_id"], "node-b")
        self.assertEqual(env["correlation_id"], "corr-1")
        self.assertTrue(env["msg_id"])
        # JSON 往返
        env2 = P.envelope_from_bytes(P.envelope_to_bytes(env))
        self.assertEqual(env2, env)

    def test_multiple_frames_stream(self):
        a, b = socket.socketpair()
        try:
            for i in range(5):
                P.send_frame(a, P.FRAME_FILE, P.pack_file_block(b"\x02" * 16, i, bytes([i]) * 10))
            for i in range(5):
                ftype, payload = P.read_frame(b)
                self.assertEqual(ftype, P.FRAME_FILE)
                ftid, seq, data = P.unpack_file_block(payload)
                self.assertEqual(seq, i)
                self.assertEqual(data, bytes([i]) * 10)
        finally:
            a.close()
            b.close()

    def test_sha256_file(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "f.bin"
            f.write_bytes(b"12345")
            self.assertEqual(P.sha256_file(f),
                             "5994471abb01112afcc18159f6cc74b4f511b99806da59b3caf5a9c173cacfc5")


if __name__ == "__main__":
    unittest.main()
