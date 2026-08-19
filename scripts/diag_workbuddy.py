"""WorkBuddy 进程与窗口诊断（经节点 shell 在交互会话运行）。"""
import json
import sys
import urllib.request


def shell(cmd, node="node-DESKTOP-F9P3UKK-952407"):
    req = urllib.request.Request("http://127.0.0.1:5177/api/shell",
                                 data=json.dumps({"target_node_id": node,
                                                  "command": cmd,
                                                  "timeout": 60}).encode(),
                                 method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=80) as r:
        return json.loads(r.read().decode())


if __name__ == "__main__":
    r = shell('tasklist /fi "imagename eq WorkBuddy.exe" /fo csv /nh')
    out = r.get("output", "")
    print("WorkBuddy processes:", out.count("WorkBuddy.exe"))
    r2 = shell(r"C:\Python3.14\python.exe "
               r"C:\Users\IKUN\agent-node\scripts\probe_wb_windows.py 2>&1")
    print(r2.get("output", "")[:1500])
