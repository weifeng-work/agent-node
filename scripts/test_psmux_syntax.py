"""psmux 注入语法验证: cmd /c '...' 在 PS pane 中的执行链路。"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from executors.psmux_transport import PsmuxTransport

mux = PsmuxTransport()
S = "agn_syntax_test"
test_dir = Path("data/executor_work/_syntax_test").resolve()
test_dir.mkdir(parents=True, exist_ok=True)
out = test_dir / "out.txt"
ec = test_dir / "ec.txt"
for f in (out, ec):
    f.unlink(missing_ok=True)

assert mux.new_session(S), "会话创建失败"
time.sleep(3)  # shell 就绪

inner = (f'cd /d "{test_dir}" & echo hello> "{out}" '
         f'& echo exitcode=%errorlevel%> "{ec}" & timeout /t 2 >nul')
shell_cmd = f"cmd /c '{inner}'"
print("注入:", shell_cmd[:120], "...")
assert mux.send_text(S, shell_cmd)
assert mux.send_keys(S, "Enter")
time.sleep(5)

print("--- files ---")
for f in (out, ec):
    print(f.name, "存在" if f.exists() else "缺失",
          f.read_text().strip() if f.exists() else "")
print("--- pane 尾部 ---")
print("\n".join(mux.capture_pane(S).splitlines()[-6:]))
mux.kill_session(S)
ok = out.exists() and "hello" in out.read_text() and ec.exists()
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
