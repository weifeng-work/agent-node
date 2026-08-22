"""psmux 终端复用器传输层（2.2.4/2.2.6 可见性）。

psmux = Windows 原生 tmux（ConPTY，Rust），命令语义与 tmux 兼容。
固化经验（来自 agent-bus 实战）:
- 坑B: 受限环境 warm-session 写 ~/.psmux/ 被拦截 → 子进程统一注入 PSMUX_NO_WARM=1
- 坑C: 无会话时 list-sessions 返回 exit=0 且无输出 → 健康检查看输出而非退出码
- kill-session 不杀子进程树 → Windows taskkill /T /F 兜底
- send-keys -l 防键名解释（字面注入）
- attach_visible: cmd.exe 新控制台（CREATE_NEW_CONSOLE）+ attach-session = 独立可见
  弹窗，人类直接观察 TUI 对话流；kill-session 后窗口自动关闭
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

IS_WIN = sys.platform == "win32"
_CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0
_CREATE_NEW_CONSOLE = 0x00000010 if IS_WIN else 0

COLS, ROWS = 120, 40


def find_psmux() -> str | None:
    """优先项目 bin/，其次 PATH。"""
    root_bin = Path(__file__).resolve().parent.parent / "bin"
    for name in ("psmux.exe", "psmux"):
        p = root_bin / name
        if p.is_file():
            return str(p)
    return shutil.which("psmux")


class PsmuxTransport:
    def __init__(self):
        self.binary = find_psmux()
        env = dict(os.environ)
        env["PSMUX_NO_WARM"] = "1"   # 坑B
        self._env = env

    @property
    def available(self) -> bool:
        return self.binary is not None

    def _run(self, args: list[str], timeout: float = 15.0,
             creationflags: int = _CREATE_NO_WINDOW) -> subprocess.CompletedProcess:
        return subprocess.run([self.binary] + args, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout,
                              env=self._env, creationflags=creationflags)

    # ---------- 会话管理 ----------
    def has_session(self, name: str) -> bool:
        """坑C: 看输出而非退出码。"""
        if not self.available:
            return False
        try:
            r = self._run(["list-sessions"])
            return any(line.split(":")[0] == name
                       for line in (r.stdout or "").splitlines())
        except Exception:
            return False

    def new_session(self, name: str) -> bool:
        try:
            self._run(["kill-session", "-t", name])
        except Exception:
            pass
        try:
            r = self._run(["new-session", "-d", "-s", name,
                           "-x", str(COLS), "-y", str(ROWS)])
            return self.has_session(name)
        except Exception:
            return False

    def kill_session(self, name: str) -> None:
        """kill-session + 进程树清理（Windows kill 不连带子进程）。"""
        pid = self.pane_pid(name)
        try:
            self._run(["kill-session", "-t", name])
        except Exception:
            pass
        if pid and IS_WIN:
            try:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=15,
                               creationflags=_CREATE_NO_WINDOW)
            except Exception:
                pass

    def pane_pid(self, name: str) -> int | None:
        try:
            r = self._run(["display-message", "-p", "-t", name, "#{pane_pid}"])
            return int((r.stdout or "").strip())
        except Exception:
            return None

    # ---------- 输入/输出 ----------
    def send_text(self, target: str, text: str) -> bool:
        """字面注入文本（-l 防键名解释）；多行文本按行注入（防提前提交）。"""
        try:
            for line in text.split("\n"):
                r = self._run(["send-keys", "-t", target, "-l", line])
                if r.returncode != 0:
                    return False
                if line is not text.split("\n")[-1]:
                    pass  # 多行场景由调用方控制提交，v1 提示词保持单行
            return True
        except Exception:
            return False

    def send_keys(self, target: str, keys: str) -> bool:
        """注入特殊键（Enter/Up/Down 等，走键名解释）。"""
        try:
            r = self._run(["send-keys", "-t", target, keys])
            return r.returncode == 0
        except Exception:
            return False

    def inject_line(self, target: str, text: str) -> bool:
        """注入单行 + Enter 提交（v1 交互任务统一单行信封）。"""
        ok = self.send_text(target, text.replace("\r", " ").replace("\n", " "))
        if not ok:
            return False
        return self.send_keys(target, "Enter")

    def capture_pane(self, target: str) -> str:
        """抓取 pane 当前屏幕文本。"""
        try:
            r = self._run(["capture-pane", "-p", "-t", target], timeout=10)
            return r.stdout or ""
        except Exception:
            return ""

    # ---------- 可见弹窗（2.2.6 核心） ----------
    def attach_visible(self, name: str) -> bool:
        """打开人类可见的交互附着窗口（独立控制台，观察 TUI 对话流）。

        Windows 实现注意（实测坑）:
        - 命令含嵌套引号（路径带引号）时，Python list2cmdline 的二次转义与
          cmd /S 解析冲突 → cmd 启动即失败、窗口闪退。故用批处理文件中转。
        - 中文字符（UTF-8 输出）在 cmd 默认 GBK 代码页下会被宽字符挤成一团，
          故在控制台内先 `chcp 65001` 显式切到 UTF-8（2.2.6 可见性修复）。
        - 窗口宿主用 PowerShell（pwsh/windows powershell）：提示符、配色与
          中文渲染优于 cmd，且仍是新建独立控制台（CREATE_NEW_CONSOLE）。
        """
        if not self.available:
            return False
        if IS_WIN:
            import tempfile
            safe = "".join(c for c in name if c.isalnum() or c in "-_")
            batch = Path(tempfile.gettempdir()) / f"agn_attach_{safe}.cmd"
            batch.write_text(
                "@echo off\r\n"
                "@chcp 65001 >nul\r\n"
                f"title {safe}\r\n"
                f"mode con cols={COLS} lines={ROWS}\r\n"
                f'"{self.binary}" attach-session -t {name}\r\n',
                encoding="ascii")
            # 宿主 PowerShell：新控制台窗口（CREATE_NEW_CONSOLE）加载 .cmd，
            # 获得 UTF-8 代码页 + PowerShell 风格窗口。
            powershell = next((p for p in ("pwsh.exe", "powershell.exe")
                               if shutil.which(p)), None)
            if not powershell:
                return False
            try:
                subprocess.Popen(
                    [powershell, "-NoLogo", "-NoExit", "-Command",
                     f'& {{ chcp 65001 | Out-Null; cmd /c "{batch}" }}'],
                    env=self._env, creationflags=_CREATE_NEW_CONSOLE)
                return True
            except Exception:
                return False
        # Linux: 图形会话用终端模拟器弹窗；无 DISPLAY 返回 False（headless 降级）
        if not os.environ.get("DISPLAY"):
            return False
        for term in ("xfce4-terminal", "gnome-terminal", "xterm", "konsole"):
            binp = shutil.which(term)
            if binp:
                try:
                    subprocess.Popen([binp, "-e",
                                      f"{self.binary} attach-session -t {name}"])
                    return True
                except Exception:
                    continue
        return False
