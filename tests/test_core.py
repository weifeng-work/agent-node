"""单元测试: 整目录推送 push_dir（目录遍历/排除 + 本机整树落盘重建）+ 节点重启。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from node.core import NodeCore, _iter_dir_proj_files


class TestDirTreeEnum(unittest.TestCase):
    def test_iter_skips_excluded(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "sub").mkdir()
            (root / "a.py").write_text("x", encoding="utf-8")
            (root / "sub" / "b.py").write_text("x", encoding="utf-8")
            (root / "venv").mkdir()
            (root / "venv" / "c.py").write_text("x", encoding="utf-8")
            rels = _iter_dir_proj_files(root, {"venv"})
            self.assertEqual(rels, ["a.py", "sub/b.py"])


class TestPushDir(unittest.TestCase):
    def test_local_rebuilds_tree_and_skips_excluded(self):
        with tempfile.TemporaryDirectory() as t:
            data = Path(t) / "data"
            src = Path(t) / "src"
            (src / "sub").mkdir(parents=True)
            (src / "keep.txt").write_text("k", encoding="utf-8")
            (src / "sub" / "n.py").write_text("n", encoding="utf-8")
            (src / "node_modules").mkdir()
            (src / "node_modules" / "junk.js").write_text("j", encoding="utf-8")
            core = NodeCore(data)
            try:
                r = core.push_dir(None, str(src), "inbox")
            finally:
                core.stop()
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["pushed"], 2, r)
            self.assertTrue((data / "inbox" / "keep.txt").is_file())
            self.assertTrue((data / "inbox" / "sub" / "n.py").is_file())
            self.assertFalse((data / "inbox" / "node_modules" / "junk.js").exists())

    def test_rejects_missing_dir(self):
        with tempfile.TemporaryDirectory() as t:
            core = NodeCore(Path(t) / "data")
            try:
                r = core.push_dir(None, str(Path(t) / "no_such"), "inbox")
            finally:
                core.stop()
            self.assertFalse(r["ok"])


class TestRestartSelf(unittest.TestCase):
    def test_restart_self_spawns_detached_relaunch(self):
        """restart_self 应返回 ok 并发起脱离的重新拉起命令（不真正杀本进程）。"""
        import subprocess
        with tempfile.TemporaryDirectory() as t:
            core = NodeCore(Path(t) / "data")
            try:
                with mock.patch.object(subprocess, "Popen") as m:
                    r = core.restart_self()
                self.assertTrue(r["ok"], r)
                args = m.call_args[0][0]  # 命令 argv
                joined = " ".join(args)
                self.assertIn("-Command", args)
                self.assertIn("Stop-Process", joined)
                # 重启命令嵌入在 PowerShell -Command 字符串内
                self.assertIn("-m", joined)
                self.assertIn("node.main", joined)
            finally:
                core.stop()


if __name__ == "__main__":
    unittest.main()