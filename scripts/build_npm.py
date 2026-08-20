"""打包脚本: 从项目源码 → npm-dist/app/（npm 包内嵌的 Python 项目）。

用法: python scripts/build_npm.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "npm-dist"
APP = DIST / "app"

# 需要复制的目录/文件（运行时代码 + 二进制）
COPY_DIRS = ["node", "transport", "executors", "server", "mcp", "tools", "sync", "skill", "docs"]
COPY_FILES = ["requirements.txt", "CHANGELOG.md"]
COPY_BIN = ["psmux.exe", "pmux.exe", "tmux.exe", "syncthing.exe"]  # bin/ 下的二进制

EXCLUDE_DIRS = {"__pycache__", ".git", "node_modules", "data", "legacy", "npm-dist", "tests", "scripts", ".trae"}


def copy_tree(src: Path, dest: Path) -> None:
    """递归复制，跳过排除目录。"""
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in EXCLUDE_DIRS and item.is_dir():
            continue
        if item.is_dir():
            copy_tree(item, dest / item.name)
        elif item.suffix not in (".pyc", ".pyo", ".log", ".tgz", ".lock"):
            shutil.copy2(item, dest / item.name)


def main() -> int:
    # 清理旧构建
    if APP.exists():
        shutil.rmtree(APP)
    APP.mkdir(parents=True)

    print("1. 复制源码目录...")
    for d in COPY_DIRS:
        src = ROOT / d
        if src.is_dir():
            copy_tree(src, APP / d)
            print(f"   ✓ {d}/")
        else:
            print(f"   ⚠ {d}/ 不存在，跳过")

    print("2. 复制文件...")
    for f in COPY_FILES:
        src = ROOT / f
        if src.is_file():
            shutil.copy2(src, APP / f)
            print(f"   ✓ {f}")

    print("3. 复制二进制...")
    bin_src = ROOT / "bin"
    bin_dest = APP / "bin"
    bin_dest.mkdir(parents=True, exist_ok=True)
    for exe in COPY_BIN:
        src = bin_src / exe
        if src.is_file():
            # syncthing 放 data 模板位置（首启由节点复制到实际 data 目录）
            if exe == "syncthing.exe":
                data_tpl = APP / "data_synth" / "syncthing"
                data_tpl.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, data_tpl / exe)
            else:
                shutil.copy2(src, bin_dest / exe)
            size_mb = src.stat().st_size / 1024 / 1024
            print(f"   ✓ {exe} ({size_mb:.1f} MB)")
        else:
            print(f"   ⚠ {exe} 不存在，跳过")

    # 统计
    total = sum(f.stat().st_size for f in APP.rglob("*") if f.is_file())
    file_count = sum(1 for f in APP.rglob("*") if f.is_file())
    print(f"\n构建完成: {file_count} 个文件, {total / 1024 / 1024:.1f} MB")
    print(f"输出目录: {APP}")

    # npm pack 预览
    print("\n4. npm pack 预览...")
    r = subprocess.run(["npm", "pack", "--dry-run"], cwd=DIST, capture_output=True,
                       text=True, shell=True)
    if r.returncode == 0:
        # 只显示关键行
        for line in r.stdout.splitlines():
            if "package size" in line.lower() or "total files" in line.lower() or "unpacked" in line.lower():
                print(f"   {line.strip()}")
    else:
        print(f"   ⚠ npm pack 预览失败: {r.stderr[:200]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
