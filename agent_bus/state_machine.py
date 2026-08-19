"""状态机读写模块 —— 解耦「手动关闭」与「自动拉起」。

文件路径：data/runtime/state.json
状态枚举：
  - active：运行态。无论进程是否存在，Watchdog 都必须将其拉起。
  - disabled：停用态。Watchdog 看到此状态后，停止拉起，如果进程存在则将其杀死。

原子写：临时文件 + rename，防止并发写损坏。
"""
import json
import os
import tempfile
import time
from pathlib import Path


# 状态枚举
STATE_ACTIVE = "active"
STATE_DISABLED = "disabled"

# 默认状态文件路径（相对于安装目录）
STATE_FILE_REL = "data/runtime/state.json"


def get_state_path(install_dir: str) -> Path:
    """返回 state.json 的完整路径。"""
    return Path(install_dir) / STATE_FILE_REL


def read_state(install_dir: str) -> str:
    """读取当前状态，文件不存在或损坏时返回 active（默认运行态）。"""
    path = get_state_path(install_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        state = data.get("state", STATE_ACTIVE)
        if state in (STATE_ACTIVE, STATE_DISABLED):
            return state
        return STATE_ACTIVE
    except Exception:
        return STATE_ACTIVE


def write_state(install_dir: str, state: str) -> bool:
    """原子写状态文件：临时文件 + rename。

    使用 tempfile.mkstemp 在同目录创建临时文件，写入后 rename 覆盖，
    确保无论何时 crash 都不会留下半写文件。
    """
    if state not in (STATE_ACTIVE, STATE_DISABLED):
        raise ValueError(f"非法状态: {state}，仅允许 {STATE_ACTIVE}/{STATE_DISABLED}")

    path = get_state_path(install_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {"state": state, "updated_at": time.time()}
    content = json.dumps(data, ensure_ascii=False, indent=2)

    # 原子写：临时文件 + rename
    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp",
        prefix="state_",
        dir=str(path.parent),
    )
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        fd = None
        os.replace(tmp_path, str(path))
        return True
    except Exception:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return False


def is_active(install_dir: str) -> bool:
    """快捷判断：是否为 active 状态。"""
    return read_state(install_dir) == STATE_ACTIVE


def is_disabled(install_dir: str) -> bool:
    """快捷判断：是否为 disabled 状态。"""
    return read_state(install_dir) == STATE_DISABLED