"""弹窗可见性根因诊断：
1. 当前进程的窗口站/桌面名（决定窗口能否出现在用户桌面）
2. 完整性级别（管理员提权与否）
3. 对照实验: 直接 spawn cmd 新控制台 + notepad（各保留 90 秒）
"""
import ctypes
import os
import subprocess
import sys
import time

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32


def window_station_name() -> str:
    hws = u32.GetProcessWindowStation()
    if not hws:
        return "<GetProcessWindowStation 失败>"
    buf = ctypes.create_unicode_buffer(256)
    length = ctypes.c_ulong(256)
    if not u32.GetUserObjectInformationW(hws, 2, buf, 256, ctypes.byref(length)):
        return "<查询失败>"
    return buf.value


def desktop_name() -> str:
    hd = u32.GetThreadDesktop(k32.GetCurrentThreadId())
    if not hd:
        return "<GetThreadDesktop 失败>"
    buf = ctypes.create_unicode_buffer(256)
    length = ctypes.c_ulong(256)
    if not u32.GetUserObjectInformationW(hd, 2, buf, 256, ctypes.byref(length)):
        return "<查询失败>"
    return buf.value


print("== 窗口站诊断 ==")
print("窗口站:", window_station_name(), "（用户桌面应为 WinSta0）")
print("桌面  :", desktop_name(), "（应为 Default）")
print("SessionId:", os.environ.get("SESSIONNAME", "?"))

print("\n== 完整性级别 ==")
whoami = subprocess.run(["whoami", "/groups"], capture_output=True, text=True)
for line in whoami.stdout.splitlines():
    if "Mandatory" in line:
        print(line.strip())
print("用户:", subprocess.run(["whoami"], capture_output=True,
                              text=True).stdout.strip())

print("\n== 对照实验: 弹 cmd 新控制台 + notepad（各 90 秒）==")
# cmd /c pause: 窗口停留等按键
p1 = subprocess.Popen(
    ["cmd.exe", "/c", "title agent-node-DIAG-cmd & echo 这是诊断窗口1-cmd & pause"],
    creationflags=subprocess.CREATE_NEW_CONSOLE)
print("cmd 进程 PID:", p1.pid)
p2 = subprocess.Popen(["notepad.exe"])
print("notepad 进程 PID:", p2.pid)
time.sleep(3)
alive1, alive2 = p1.poll() is None, p2.poll() is None
print("cmd 存活:", alive1, "| notepad 存活:", alive2)

for i in range(90, 0, -15):
    print(f"保留中... 剩余 {i}s")
    time.sleep(15)
for p in (p1, p2):
    try:
        p.kill()
    except Exception:
        pass
print("诊断窗口已关闭")
