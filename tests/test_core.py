"""单元测试: 整目录推送 push_dir（目录遍历/排除 + 本机整树落盘重建）。"""
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()