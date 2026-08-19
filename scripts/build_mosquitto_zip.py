"""构建 mosquitto 便携 zip（供 PyInstaller 打进 exe）。

用法：
  python scripts/build_mosquitto_zip.py

说明：
  - 优先复制本机已安装的 mosquitto（C:\\Program Files\\mosquitto 等）
  - 输出 build/mosquitto.zip（约 3-4MB），PyInstaller 用 --add-data 打进 exe
"""
import zipfile
from pathlib import Path
import shutil

PROJ = Path(__file__).resolve().parent.parent
SRC_CANDIDATES = [
    Path(r"C:\Program Files\mosquitto"),
    Path(r"C:\Program Files (x86)\mosquitto"),
    Path(r"C:\mosquitto"),
]


def find_mosquitto_dir() -> Path:
    for base in SRC_CANDIDATES:
        if (base / "mosquitto.exe").exists():
            return base
    # PATH
    exe = shutil.which("mosquitto")
    if exe:
        return Path(exe).parent
    raise FileNotFoundError(
        "未找到本机 mosquitto。请先安装 mosquitto（https://mosquitto.org/download/）")


def main():
    src = find_mosquitto_dir()
    print(f"源 mosquitto 目录: {src}")
    build_dir = PROJ / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    out = build_dir / "mosquitto.zip"
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        n = 0
        for f in src.iterdir():
            if f.is_file() and f.name.lower().endswith((".exe", ".dll", ".conf", ".txt")):
                zf.write(f, f.name)
                n += 1
        print(f"已压缩 {n} 个文件 → {out} ({out.stat().st_size // 1024 // 1024} MB)")


if __name__ == "__main__":
    main()
