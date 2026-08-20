"""清理双端 known_peers 中的陈旧测试节点（本地测试 beacon 泄漏累积）。"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

REAL_NODES = {"node-PC-20231207KSGE-212c09", "node-DESKTOP-F9P3UKK-952407"}


def clean(base: str, label: str) -> int:
    n = 0
    with urllib.request.urlopen(base + "/api/nodes", timeout=10) as r:
        nodes = json.loads(r.read().decode())["nodes"]
    for node in nodes:
        nid = node["nodeId"]
        if nid not in REAL_NODES:
            req = urllib.request.Request(
                base + "/api/nodes/forget",
                data=json.dumps({"node_id": nid}).encode(), method="POST")
            req.add_header("Content-Type", "application/json")
            urllib.request.urlopen(req, timeout=10).read()
            n += 1
    print(f"{label}: 清理 {n} 个陈旧节点")
    return n


if __name__ == "__main__":
    clean("http://127.0.0.1:5177", "本机")
    # IKUN 侧经远程 shell 调用其本地面板
    peer = "node-DESKTOP-F9P3UKK-952407"
    script = (
        "$nodes = (Invoke-RestMethod http://127.0.0.1:5177/api/nodes -TimeoutSec 5).nodes; "
        "foreach ($n in $nodes) { if ($n.nodeId -ne 'node-PC-20231207KSGE-212c09') { "
        "Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5177/api/nodes/forget "
        "-ContentType 'application/json' -Body ('{\"node_id\":\"' + $n.nodeId + '\"}') | Out-Null } }; "
        "Write-Output done"
    )
    req = urllib.request.Request(
        "http://127.0.0.1:5177/api/shell",
        data=json.dumps({"target_node_id": peer, "command":
                         f"powershell -NoProfile -Command \"{script}\"",
                         "timeout": 120}).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=150) as r:
        result = json.loads(r.read().decode())
    print("IKUN 清理:", result.get("output", "").strip() or result.get("error"))
    # 复核
    for base, label in (("http://127.0.0.1:5177", "本机"),):
        with urllib.request.urlopen(base + "/api/nodes", timeout=10) as r:
            nodes = json.loads(r.read().decode())["nodes"]
        print(f"{label} 剩余节点:", [n["name"] or n["nodeId"] for n in nodes])
