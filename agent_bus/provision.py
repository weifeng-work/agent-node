"""（遗留）凭据开通逻辑 —— v2 匿名化（git 4c805e7）后，bus_server 的 /api/join
与 join_team.py 均改为匿名直连，不再发放 MQTT 密码 / HTTP 令牌。

本模块当前仅由两类场景使用：
  - scripts/add_node.py：历史凭据的增删查（如日后恢复认证模式时）
  - 工具函数：get_local_ips / get_local_ip / valid_agent_id（discovery、server 共用）

历史职责（不再生效）：
  - 生成 MQTT 密码 / HTTP 令牌
  - 写 mosquitto passwd（PBKDF2 哈希，由 mosquitto_passwd 工具完成）
  - HTTP 令牌写入 bus.db（http_tokens 表），重置节点时先删旧令牌（防幽灵令牌）
  - broker 侧留档 data/credentials.json（已 gitignore）
  - pattern ACL 骨架
  - 用户态 broker 重启（服务模式返回需人工处理的提示）

auth 目录解析优先级（必须与 broker conf 一致）：
  1. 环境变量 BUS_AUTH_DIR（显式指定）
  2. 遗留部署目录存在：C:\\mosquitto-auth（Windows 服务）/ /etc/agent-bus-auth（Linux 服务）
  3. 新部署默认：仓库 data/auth（用户态便携 broker）
"""
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import string
import subprocess
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
BRIDGE_USER = "bus-server-bridge"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_LEGACY_DIRS = [Path("C:/mosquitto-auth"), Path("/etc/agent-bus-auth")]


def auth_dir() -> Path:
    """解析凭据目录（见模块 docstring 的优先级说明）。"""
    env = os.environ.get("BUS_AUTH_DIR")
    if env:
        d = Path(env)
        d.mkdir(parents=True, exist_ok=True)
        return d
    for d in _LEGACY_DIRS:
        if d.is_dir():
            return d
    d = ROOT_DIR / "data" / "auth"
    # Windows mosquitto 以 ANSI 打开 conf 内的 passwd/acl 路径，
    # 仓库路径含非 ASCII（中文目录）时打不开 → 落到盘根纯 ASCII 目录
    if sys.platform == "win32" and any(ord(c) > 127 for c in str(d)):
        d = Path("C:/agent-bus-auth")
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_mosquitto_passwd() -> str:
    name = shutil.which("mosquitto_passwd")
    if name:
        return name
    candidates = [
        # 便携版（setup_host.py 解压目录）
        str(ROOT_DIR / "data" / "runtime" / "mosquitto" / "mosquitto_passwd.exe"),
        r"C:\Program Files\mosquitto\mosquitto_passwd.exe",
        "/usr/bin/mosquitto_passwd", "/usr/sbin/mosquitto_passwd",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    raise FileNotFoundError("找不到 mosquitto_passwd（安装 mosquitto 或先运行 setup_host.py）")


def gen_password(n=24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def gen_token() -> str:
    return secrets.token_urlsafe(24)


def valid_agent_id(agent_id: str) -> bool:
    return bool(ID_RE.match(agent_id or ""))


def set_mqtt_password(passwd_file: Path, user: str, password: str):
    passwd_file.parent.mkdir(parents=True, exist_ok=True)
    if not passwd_file.exists():
        passwd_file.touch()
    r = subprocess.run([find_mosquitto_passwd(), "-b", str(passwd_file), user, password],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"mosquitto_passwd 失败: {r.stderr.strip()}")


def remove_mqtt_user(passwd_file: Path, user: str):
    """从 passwd 文件删除用户（不存在时静默成功）。"""
    if not passwd_file.exists():
        return
    r = subprocess.run([find_mosquitto_passwd(), "-D", str(passwd_file), user],
                       capture_output=True, text=True)
    # 用户不存在时 mosquitto_passwd -D 返回非 0，属预期
    if r.returncode != 0 and "not found" not in (r.stderr or "").lower():
        raise RuntimeError(f"mosquitto_passwd -D 失败: {r.stderr.strip()}")


def write_acl(acl_file: Path):
    """pattern ACL：按 MQTT 用户名（%u = agent_id）授权，新增节点零维护。

    bus-server-bridge 全量读写（消息追溯）；节点可写任意 agent/+/inbox 是
    P2P 通信的前提。队伍隔离（bus/{team}/ 前缀）留待联邦版本。
    """
    acl = f"""# agent-bus ACL (pattern %u = MQTT username = agent_id)
user {BRIDGE_USER}
topic readwrite #

pattern write bus/register
pattern write bus/heartbeat/%u
pattern write bus/offline/%u
pattern read agent/%u/inbox
pattern write agent/+/inbox
"""
    acl_file.write_text(acl, encoding="utf-8")


class CredStore:
    """broker 侧留档（data/credentials.json）+ bus.db 令牌表。"""

    def __init__(self, cred_file: Path = None, db_path: Path = None):
        self.db_path = db_path or (ROOT_DIR / "data" / "bus.db")
        # 凭据留档默认与 db 同目录（测试/E2E 用独立 db 时自动隔离，不污染生产留档）
        self.cred_file = cred_file or (self.db_path.parent / "credentials.json")
        if self.cred_file.exists():
            try:
                self.data = json.loads(self.cred_file.read_text(encoding="utf-8"))
            except ValueError:
                self.data = {"nodes": {}}
        else:
            self.data = {"nodes": {}}
        self.cred_file.parent.mkdir(parents=True, exist_ok=True)

    def save_node(self, agent_id, mqtt_pass, http_token, role="node"):
        self.data["nodes"][agent_id] = {
            "mqtt_user": agent_id, "mqtt_pass": mqtt_pass,
            "http_token": http_token, "role": role,
            "issued_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.cred_file.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._upsert_token(http_token, agent_id, role)

    def _upsert_token(self, token: str, agent_id: str, role: str):
        conn = sqlite3.connect(str(self.db_path))
        try:
            with conn:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS http_tokens(
                           token TEXT PRIMARY KEY, agent_id TEXT,
                           role TEXT DEFAULT 'node', created_at REAL)"""
                )
                # 幽灵令牌修复：重置节点先撤销该节点全部旧令牌（审核 A.1）
                conn.execute("DELETE FROM http_tokens WHERE agent_id=?", (agent_id,))
                conn.execute(
                    "INSERT INTO http_tokens(token,agent_id,role,created_at) VALUES(?,?,?,?)",
                    (token, agent_id, role, time.time()),
                )
        finally:
            conn.close()

    def revoke_tokens(self, agent_id: str):
        """撤销节点全部 HTTP 令牌（节点移除/重置时调用）。"""
        conn = sqlite3.connect(str(self.db_path))
        try:
            with conn:
                conn.execute("DELETE FROM http_tokens WHERE agent_id=?", (agent_id,))
        finally:
            conn.close()
        self.data["nodes"].pop(agent_id, None)
        self.cred_file.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def provision(self, agent_id: str, role: str = "node") -> dict:
        """一站式开通：生成并落盘 MQTT 密码 + HTTP 令牌。返回凭据 dict。"""
        if not valid_agent_id(agent_id):
            raise ValueError(f"agent_id 非法（限 [A-Za-z0-9_-]，1-64 位）: {agent_id}")
        d = auth_dir()
        mqtt_pass, http_token = gen_password(), gen_token()
        set_mqtt_password(d / "passwd", agent_id, mqtt_pass)
        acl = d / "acl"
        if not acl.exists():
            write_acl(acl)
        self.save_node(agent_id, mqtt_pass, http_token, role=role)
        return {"agent_id": agent_id, "mqtt_user": agent_id, "mqtt_pass": mqtt_pass,
                "http_token": http_token, "role": role}


# ---------------------------------------------------------------------------
# 口令哈希（PBKDF2-SHA256，标准库实现）
# ---------------------------------------------------------------------------

def hash_passphrase(passphrase: str, salt: bytes = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, 200_000)
    return salt.hex() + "$" + dk.hex()


def verify_passphrase(passphrase: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$", 1)
        dk = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"),
                                 bytes.fromhex(salt_hex), 200_000)
        return secrets.compare_digest(dk.hex(), dk_hex)
    except (ValueError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# broker 进程管理（用户态便携模式）
# ---------------------------------------------------------------------------

def _is_private_ip(ip: str) -> bool:
    try:
        a, b = (int(x) for x in ip.split(".")[:2])
    except ValueError:
        return False
    return a == 10 or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31)


def get_local_ips() -> list:
    """本机候选 IP（有序去重）：默认路由出口私网 IP 优先，其余私网地址次之。

    多网卡/代理 TUN 场景（出口是 198.18.x.x fake-ip、另有 VMware 等虚拟网卡）
    无法给出唯一正确答案——由 beacon 全量携带、join 侧做连通性自检选择
    （设计原则：发现 ≠ 连通）。
    """
    out = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        if _is_private_ip(ip):
            out.append(ip)
    except OSError:
        pass
    finally:
        s.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if _is_private_ip(ip) and ip not in out:
                out.append(ip)
    except socket.gaierror:
        pass
    if sys.platform != "win32":
        try:  # Debian /etc/hosts 常把 hostname 映射为 127.0.1.1，hostname -I 直接给网卡 IP
            ips = subprocess.run(["hostname", "-I"], capture_output=True,
                                 text=True, timeout=5).stdout.split()
            for ip in ips:
                if _is_private_ip(ip) and ip not in out:
                    out.append(ip)
        except (OSError, ValueError):
            pass
    return out or ["127.0.0.1"]


def get_local_ip() -> str:
    """首选本机局域网 IP（展示/join 返回用；多候选场景见 get_local_ips）。"""
    return get_local_ips()[0]


def restart_user_broker() -> tuple:
    """重启用户态 broker（setup_host 以 Popen 启动、记录在 data/broker.pid + broker.cmd.json）。

    返回 (restarted: bool, message: str)。服务模式（无 pid 文件）返回 False，
    由调用方提示人工重启。
    """
    pid_file = ROOT_DIR / "data" / "broker.pid"
    cmd_file = ROOT_DIR / "data" / "broker.cmd.json"
    if not pid_file.exists() or not cmd_file.exists():
        return False, "service-mode broker，需人工重启使 passwd 生效"

    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, 9)  # Windows 上 os.kill(pid, 9) 即强制终止
    except (OSError, ProcessLookupError):
        pass
    time.sleep(0.6)

    spec = json.loads(cmd_file.read_text(encoding="utf-8"))
    proc = subprocess.Popen(
        spec["args"], cwd=spec.get("cwd") or None,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=(0x08000000 if sys.platform == "win32" else 0),  # CREATE_NO_WINDOW
    )
    pid_file.write_text(str(proc.pid))
    # 等端口起来（最多 5s）
    host, port = spec.get("probe_host", "127.0.0.1"), int(spec.get("probe_port", 1883))
    for _ in range(25):
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True, f"broker 已重启 pid={proc.pid}"
        except OSError:
            time.sleep(0.2)
    return False, "broker 重启后端口未就绪"
