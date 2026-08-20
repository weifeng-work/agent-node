"""attach 窗口实时刷新验证: 先弹窗 attach，再往 pane 注入逐秒输出。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from executors.psmux_transport import PsmuxTransport

mux = PsmuxTransport()
S = "agn_live_test"
assert mux.new_session(S)
time.sleep(3)
print("先 attach 弹窗...", flush=True)
assert mux.attach_visible(S)
time.sleep(2)
print("开始逐秒注入数字（窗口应实时出现 1,2,3...）...", flush=True)
for i in range(1, 16):
    mux.send_text(S, f"echo 第{i}秒-如果你能看到这行数字在增加说明窗口实时刷新")
    mux.send_keys(S, "Enter")
    time.sleep(1)
print("注入完成，窗口保留 60 秒...", flush=True)
time.sleep(60)
mux.kill_session(S)
print("结束", flush=True)
