"""attach_visible 弹窗验证: 创建会话 + 弹窗 + 保留 60 秒（用户肉眼确认）。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from executors.psmux_transport import PsmuxTransport

mux = PsmuxTransport()
S = "agn_visible_test"
print("创建会话...")
assert mux.new_session(S)
time.sleep(3)
mux.send_text(S, "echo 这是 agent-node 弹窗可见性测试，窗口保留 60 秒...")
mux.send_keys(S, "Enter")
print("调用 attach_visible（cmd.exe 新控制台 + CREATE_NEW_CONSOLE）...")
ok = mux.attach_visible(S)
print("attach_visible 返回:", ok)
for i in range(120, 0, -20):
    print(f"窗口保留中... 剩余 {i}s（请确认桌面是否出现标题为 {S} 的控制台窗口）")
    time.sleep(20)
mux.kill_session(S)
print("会话已关闭（窗口应自动消失）")
