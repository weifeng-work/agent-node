"""检查 codebuddy 真实输出结构。"""
import json
import os

path = os.path.join(os.environ["TEMP"], "cb_out.json")
text = open(path, encoding="utf-8-sig").read()
data = json.loads(text)
print("事件数:", len(data))
print("类型:", [e.get("type") for e in data])
res = [e for e in data if e.get("type") == "result"]
if res:
    r = res[-1]
    print("result keys:", list(r.keys()))
    print("session_id:", r.get("session_id"))
    print("result 值类型:", type(r.get("result")).__name__)
    print("result 内容前200:", str(r.get("result"))[:200])
