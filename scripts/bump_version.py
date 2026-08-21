#!/usr/bin/env python3
"""把 tag 版本号写入 node/version.py 的 VERSION 常量（纯 Python，无 npm/Node 依赖）。

用法:
    python scripts/bump_version.py v0.2.0

在 GitHub Actions 发版（tag push）时调用；源码形态本可用 git describe 自动跟随，
但 codeload 源码 zip 部署形态无 `.git`，只能读 version.py 的 VERSION 常量，故需在
发版时把该常量落成最新 tag。唯一事实来源 = GitHub tag。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_PY = ROOT / "node" / "version.py"


def parse_tag(tag: str) -> str:
    m = re.fullmatch(r"v(\d+\.\d+\.\d+)", tag.strip())
    if not m:
        raise SystemExit(f"❌ tag 格式错误: {tag}（应为 vX.Y.Z）")
    return m.group(1)


def main() -> int:
    tag = sys.argv[1] if len(sys.argv) > 1 else ""
    version = parse_tag(tag)
    if not VERSION_PY.is_file():
        raise SystemExit(f"❌ 未找到 {VERSION_PY}")

    src = VERSION_PY.read_text(encoding="utf-8")
    new = re.sub(r'VERSION\s*=\s*"[^"]*"', f'VERSION = "{version}"', src, count=1)
    if new == src:
        raise SystemExit(f"❌ {VERSION_PY} 中未找到 VERSION = \"x.y.z\" 常量")
    VERSION_PY.write_text(new, encoding="utf-8")
    print(f"✓ 版本号已写入 {VERSION_PY} -> {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())