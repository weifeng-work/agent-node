#!/usr/bin/env python3
"""构建 Windows x64 便携运行时 zip —— 零环境一键装的核心产物。

产物 = 一个自包含目录（内置 Python + 已预装本项目依赖 + bin 辅助二进制），
用户无需安装 Node / npm / git / 系统 Python，解压即能 `pythonw -m node.main`。

用法（发布/CI 调用）:
    python scripts/build_runtime.py              # 输出到 dist/
    python scripts/build_runtime.py --out dist --python-ver 3.12
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = REPO_ROOT / "requirements.txt"
BIN_DIR = REPO_ROOT / "bin"

# python-build-standalone 最新 release 的 assets 名单（找 x64+win+install_only+tar.gz）
RT_API = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
RT_TAG_PREFIX = "cpython-"
RUNTIME_ZIP_NAME = "agent-node-runtime-windows-x64.zip"


def _pick_rt_asset(py_ver: str) -> tuple[str, str]:
    """返回 (url, name)：匹配 py_ver.x 的 x86_64 pc-windows-msvc install_only.tar.gz。"""
    with urllib.request.urlopen(RT_API, timeout=60) as r:
        rel = json.load(r)
    for a in rel.get("assets", []):
        n: str = a["name"]
        if (n.startswith(f"{RT_TAG_PREFIX}{py_ver}.")
                and "x86_64-pc-windows-msvc" in n
                and n.endswith("install_only.tar.gz")):
            return a["browser_download_url"], n
    raise SystemExit(f"未找到 python-build-standalone 匹配 py_ver={py_ver} 的资产")


def _wget(url: str, dest: Path) -> None:
    print(f"  下载 {url}")
    urllib.request.urlretrieve(url, dest)


def _extract_incl_pip(root: Path) -> Path:
    """返回真实 python.exe 所在目录（windows 平台 install_only 即解压根）。"""
    pyexe = next(root.rglob("python.exe"), None)
    if pyexe is None:
        raise SystemExit("便携 Python 包缺 python.exe")
    return pyexe.parent


def _ensure_pip(pyroot: Path) -> None:
    py = pyroot / "python.exe"
    if subprocess.run([str(py), "-m", "pip", "--version"],
                      capture_output=True).returncode != 0:
        print("  install_only 不带 pip，改用 get-pip 引导…")
        gp = pyroot.parent / "get-pip.py"
        _wget("https://bootstrap.pypa.io/get-pip.py", gp)
        subprocess.run([str(py), str(gp)], check=True)


def _install_deps(pyroot: Path) -> None:
    py = pyroot / "python.exe"
    print("  安装依赖（pip install -r requirements.txt）…")
    subprocess.run([str(py), "-m", "pip", "install",
                    "-r", str(REQUIREMENTS), "--disable-pip-version-check",
                    "--quiet"], check=True)


def _zip_dir(root: Path, arcbase: Path, zf: zipfile.ZipFile) -> None:
    for p in root.rglob("*"):
        if p.is_file():
            zf.write(p, p.relative_to(arcbase).as_posix())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO_ROOT / "dist"))
    ap.add_argument("--python-ver", default="3.12")
    a = ap.parse_args()

    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / RUNTIME_ZIP_NAME

    if not REQUIREMENTS.is_file():
        print("ERR 缺少 requirements.txt", file=sys.stderr)
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="rt-build-"))
    try:
        url, name = _pick_rt_asset(a.python_ver)
        tarball = tmp / name
        _wget(url, tarball)
        print("  解压便携 Python…")
        with tarfile.open(tarball, "r:gz") as tar:
            tar.extractall(tmp, filter="data")
        pyroot = _extract_incl_pip(tmp)
        _ensure_pip(pyroot)
        _install_deps(pyroot)

        # 把 bin 辅助二进制一并并入（NodeCore data.parent/bin 依赖它们）
        stage_bin = tmp / "bin"
        if BIN_DIR.is_dir():
            shutil.copytree(BIN_DIR, stage_bin)

        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            _zip_dir(tmp, tmp, zf)   # 顶层含 python\ 与 bin\
        print(f"✓ 运行时 zip: {dest} ({dest.stat().st_size//1024//1024} MB)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())