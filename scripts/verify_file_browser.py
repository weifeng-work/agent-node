"""文件浏览器修复验证（面板 API 层模拟全部前端操作）。"""
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.test_dual_machine import Panel, check, report


def main() -> int:
    p = Panel("http://127.0.0.1:5177")
    nodes = p.call("GET", "/api/nodes")["nodes"]
    peer = next(n for n in nodes if n.get("name") == "IKUN-PC")
    peer_id = peer["nodeId"]
    print(f"对端: {peer_id} online={peer['online']} connected={peer.get('connected')}")

    # 1. 根视图: 枚举 IKUN 盘符（含其他硬盘）
    r = p.call("GET", f"/api/files/list?node_id={urllib.parse.quote(peer_id)}&path=")
    drives = [e["name"] for e in r.get("entries") or []]
    check("IKUN 根视图盘符枚举", r.get("ok") is True and len(drives) >= 1,
          f"drives={drives}")
    print(f"    IKUN 磁盘: {drives}")

    # 2. 打开 IKUN C 盘内文件夹（此前 bug: 路径反斜杠被吃）
    users_path = urllib.parse.quote("C:" + chr(92) + "Users")
    r = p.call("GET", f"/api/files/list?node_id={urllib.parse.quote(peer_id)}&path={users_path}")
    entries = r.get("entries") or []
    check("打开 IKUN C:\\Users 文件夹", r.get("ok") is True and len(entries) > 0,
          f"entries={[e['name'] for e in entries][:8]}")

    # 3. 打开 IKUN 深层文件夹
    deep = urllib.parse.quote("C:" + chr(92) + "Users" + chr(92) + "IKUN"
                              + chr(92) + "agent-node" + chr(92) + "data")
    r = p.call("GET", f"/api/files/list?node_id={urllib.parse.quote(peer_id)}&path={deep}")
    check("打开 IKUN 深层 data 文件夹", r.get("ok") is True
          and any(e["name"] == "inbox" for e in r.get("entries") or []), str(r)[:150])

    # 4. 本机根视图 + 打开本机文件夹（此前同样打不开）
    r = p.call("GET", "/api/files/list?node_id=&path=")
    local_drives = [e["name"] for e in r.get("entries") or []]
    check("本机根视图盘符枚举", r.get("ok") is True and len(local_drives) >= 1,
          f"drives={local_drives}")
    win_path = urllib.parse.quote("C:" + chr(92) + "Windows")
    r = p.call("GET", f"/api/files/list?node_id=&path={win_path}")
    check("打开本机 C:\\Windows 文件夹", r.get("ok") is True
          and len(r.get("entries") or []) > 0, str(r)[:120])

    # 5. 下载 IKUN 单文件（此前 WinError: 文件不存在）
    remote_file = urllib.parse.quote("C:" + chr(92) + "Users" + chr(92) + "IKUN"
                                     + chr(92) + "agent-node" + chr(92) + "data"
                                     + chr(92) + "inbox" + chr(92) + "dualtest_send.txt")
    dl = urllib.request.urlopen(
        f"http://127.0.0.1:5177/api/files/download?node_id={urllib.parse.quote(peer_id)}"
        f"&path={remote_file}", timeout=60)
    content = dl.read().decode("utf-8")
    check("下载 IKUN 单文件（字节流）",
          "dual machine file push test 12345" in content, content[:80])
    pulled = Path("data/inbox/dualtest_send.txt")
    check("下载副本落本机收件目录", pulled.exists())

    # 6. 上传: 模拟文件选择器（单文件，带子目录 rel → 保留结构）
    up_dir = urllib.parse.quote("C:" + chr(92) + "Users" + chr(92) + "IKUN"
                                + chr(92) + "agent-node" + chr(92) + "data"
                                + chr(92) + "inbox")
    req = urllib.request.Request(
        f"http://127.0.0.1:5177/api/files/upload?node_id={urllib.parse.quote(peer_id)}"
        f"&target_dir={up_dir}&rel={urllib.parse.quote('picker_test/hello.txt')}",
        data=b"picker upload test 42", method="POST")
    req.add_header("X-File-Name", urllib.parse.quote("hello.txt"))
    r = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
    check("文件选择器推送（子目录保留结构）", r.get("ok") is True, str(r))
    # 远端验证
    v = p.call("POST", "/api/shell", {
        "target_node_id": peer_id,
        "command": "type C:\\Users\\IKUN\\agent-node\\data\\inbox\\picker_test\\hello.txt",
        "timeout": 30})
    check("远端收到上传文件（内容一致）",
          v.get("ok") is True and "picker upload test 42" in v.get("output", ""),
          str(v.get("output"))[:100])

    # 7. 上传到根目录（无 target_dir → 默认收件目录 2.4.2）
    req = urllib.request.Request(
        f"http://127.0.0.1:5177/api/files/upload?node_id={urllib.parse.quote(peer_id)}"
        f"&rel=default_inbox_test.txt",
        data=b"default inbox bytes", method="POST")
    req.add_header("X-File-Name", urllib.parse.quote("default_inbox_test.txt"))
    r = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
    check("根目录推送 → 默认收件目录", r.get("ok") is True, str(r))

    # 8. 挂起（4.3 弹窗后端路径: 原因+到期时间）
    from datetime import datetime, timedelta
    until = (datetime.now() + timedelta(minutes=2)).isoformat()
    r = p.call("POST", "/api/executors/suspend", {
        "executor_id": "mock", "suspend": True, "reason": "验证挂起", "until": until})
    check("挂起 mock（含原因+到期）", r.get("ok") is True, str(r))
    st = p.call("GET", "/api/executors/status?executor_id=mock")["status"]
    check("挂起状态生效", st.get("state") == "suspended"
          and st.get("suspendReason") == "验证挂起", str(st)[:120])
    r = p.call("POST", "/api/executors/suspend", {"executor_id": "mock", "suspend": False})
    check("恢复 mock", r.get("ok") is True, str(r))

    # 9. 控制字符防御（\x08 路径 → 明确报错而非 WinError 123）
    r = p.call("POST", "/api/files/push", {
        "node_id": peer_id, "local_path": "data/dualtest_send.txt",
        "target_path": "C:\x08stlog"})
    check("控制字符路径防御", r.get("ok") is False
          and "控制字符" in (r.get("detail") or ""), str(r))

    return report()


if __name__ == "__main__":
    sys.exit(main())
