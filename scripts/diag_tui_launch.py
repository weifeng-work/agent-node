"""CodeBuddy TUI 会话拉起诊断: 创建 psmux 会话 → 启动 codebuddy TUI → 抓屏。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from executors.adapters.codebuddy import find_codebuddy, _q
from executors.psmux_transport import PsmuxTransport

mux = PsmuxTransport()
S = "agn_cb_tui"
cb = find_codebuddy()
print("codebuddy:", cb)

print("1. 创建会话...", flush=True)
assert mux.new_session(S)
time.sleep(3)
print("2. 注入启动命令...", flush=True)
mux.send_text(S, f"{_q(cb)} --permission-mode bypassPermissions")
mux.send_keys(S, "Enter")
time.sleep(8)
print("3. 当前屏幕:", flush=True)
screen = mux.capture_pane(S)
print(screen[:1500])
print("---", flush=True)
mux.attach_visible(S)
print("已弹窗 attach，保留 60 秒...", flush=True)
time.sleep(60)
mux.kill_session(S)
print("结束", flush=True)
