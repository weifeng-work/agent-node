"""attach_visible 失败模式诊断: 弹窗 + pause 保底（即使 attach 报错窗口也不关）。
窗口保留 120 秒，用户可读到错误内容。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from executors.psmux_transport import PsmuxTransport

mux = PsmuxTransport()
S = "agn_diag_attach"
print("创建会话...", flush=True)
assert mux.new_session(S)
time.sleep(3)
mux.send_text(S, "echo 诊断: pane 内容正常显示")
mux.send_keys(S, "Enter")
time.sleep(1)

# 与 attach_visible 完全相同的命令串，但尾部加 & pause（失败也留窗）
safe = S
cmd = (f"title {safe} & mode con cols=120 lines=40 & "
       f'"{mux.binary}" attach-session -t {S} & echo === attach 退出码 %errorlevel% === & pause')
import subprocess
p = subprocess.Popen(["cmd.exe", "/S", "/c", f'"{cmd}"'],
                     env=mux._env,
                     creationflags=0x00000010)  # CREATE_NEW_CONSOLE
print(f"cmd PID: {p.pid}（窗口已弹，含 pause 保底）", flush=True)
for i in range(120, 0, -30):
    print(f"保留中... 剩余 {i}s", flush=True)
    time.sleep(30)
try:
    p.kill()
except Exception:
    pass
mux.kill_session(S)
print("诊断结束", flush=True)
