"""节点软件版本（唯一事实来源 = GitHub tag）。

- 源码形态（目录含 .git）：用 `git describe --tags --abbrev=0` 自动跟随最新 tag，
  避免手工改本文件导致与发版 tag 漂移。
- 部署形态（npm 发布副本，无 .git）：用下方 VERSION 常量。
  release.sh 在发版时按 tag 自动把该常量覆盖为 tag（去前导 v），再随 app/ 一并部署。

面板顶部与命令行启动（python -m node.main）均从本模块取版本号。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

# release.sh 发版时自动覆盖为 tag（去掉前缀 v）；本地源码形态则被 git describe 覆盖
VERSION = "0.1.10"


def _git_describe() -> str | None:
    """源码形态：读取仓库最新 tag（vX.Y.Z → X.Y.Z）。"""
    try:
        root = Path(__file__).resolve().parents[1]  # 仓库根
        if not (root / ".git").exists():
            return None
        r = subprocess.run(["git", "describe", "--tags", "--abbrev=0"],
                           cwd=str(root), capture_output=True, text=True, timeout=5)
        tag = (r.stdout or "").strip()
        if tag.startswith("v"):
            return tag[1:]
    except Exception:
        pass
    return None


def get_version() -> str:
    v = _git_describe()
    return v if v else VERSION


VERSION = get_version()