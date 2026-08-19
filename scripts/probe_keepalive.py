"""mesh keepalive 探针: 手动完成握手 + ping/pong 观测（诊断连接 15s 死亡）。"""
import json
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from transport import protocol as P

HOST, PORT = "192.168.31.186", 41840
MY_ID = "node-probe-000001"

sock = socket.create_connection((HOST, PORT), timeout=5)
sock.settimeout(10)

def send(env):
    sock.sendall(P.encode_frame(P.FRAME_JSON, P.envelope_to_bytes(env)))

def recv():
    fr = P.read_frame(sock)
    if fr is None:
        return None
    ftype, payload = fr
    return json.loads(payload.decode("utf-8")) if ftype == P.FRAME_JSON else ("FILE", payload[:8])

# 握手
hello = P.make_envelope(P.T_PING, MY_ID, "*", {"hello": True, "team_id": "", "name": "probe"})
send(hello)
t0 = time.time()
print("hello sent, waiting first frame...")
first = recv()
print(f"first frame after {time.time()-t0:.2f}s: type={first.get('type') if isinstance(first, dict) else first}")
if isinstance(first, dict):
    print("  ->", json.dumps(first, ensure_ascii=False)[:200])

# 连发 3 个 ping，看 pong
for i in range(3):
    ping = P.make_envelope(P.T_PING, MY_ID, "*", {}, correlation_id=P.new_msg_id())
    send(ping)
    t = time.time()
    try:
        resp = recv()
        print(f"ping{i+1}: {time.time()-t:.2f}s -> type={resp.get('type') if isinstance(resp, dict) else resp} "
              f"corr={resp.get('correlation_id') if isinstance(resp, dict) else ''}")
    except socket.timeout:
        print(f"ping{i+1}: TIMEOUT after {time.time()-t:.2f}s")
    time.sleep(2)

print("probe done, closing")
sock.close()
