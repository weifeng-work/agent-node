"""节点配置 data/node_config.json（2.14.3）+ 环境变量覆盖（2.14.4）。

- node_id 首启自动生成 node-<hostname>-<随机短码>（稳定身份，寻址用）
- name 用户可改显示名；team_id 可选隔离域（默认空 = 无 team）
- peer_tcp_port: 0=动态分配（默认），可固定（预期被手动加入的节点应设固定值，2.1.10）
- manual_peers: 手动指定加入的对端地址（跨网段兜底，2.1.10）
- 写盘一律原子（tmp + rename，2.17.5）
"""
from __future__ import annotations

import json
import os
import re
import socket
import tempfile
import uuid
from pathlib import Path

# 2.1.5: 发现端口集分散不同区间，刻意避开 LocalSend 默认端口 53317
DEFAULT_DISCOVERY_PORTS = [41830, 41550, 60420, 31820, 26880]
DEFAULT_PANEL_PORT = 5177
# 2.18: 约定对等 TCP 端口段。被 AP 隔离的节点靠「出站扫描全子网×段内端口」自动发现
# 节点：扫描方必须能猜中对方默认监听端口，故默认对等端口从本段分配（冲突顺延，
# 段满才随机）。避开常见服务端口，降低误连。
DEFAULT_PEER_PORT_START = 49710
DEFAULT_PEER_PORT_END = 49729
# 3.x 发现增强：UDP 组播信道（本地管理范围组；与广播正交，广播被过滤但组播放行时补上）
DEFAULT_MULTICAST_GROUP = "239.255.42.47"
# 固定通告 TCP 端口：节点在动态对等端口之外，另监听一个「通告/握手」端，
# 让子网扫描先以「每 IP 1 端口」命中，免盲扫 20 口对等段。
DEFAULT_ANNOUNCE_TCP_PORT = 49700

_ENV_CONFIG_DIR = "AGENT_NODE_CONFIG_DIR"
_ENV_INBOX_DIR = "AGENT_NODE_INBOX_DIR"
_ENV_DISCOVERY_PORTS = "AGENT_NODE_DISCOVERY_PORTS"
_ENV_ANCHORS = "AGENT_NODE_ANCHORS"

DEFAULT_CONFIG: dict = {
    "node_id": "",
    "name": "",
    "team_id": "",
    "run_as_admin": False,
    "switches": {"allow_shell": True, "allow_file": True, "allow_ai_task": True},
    "peer_tcp_port": 0,
    "manual_peers": [],
    "peer_anchors": [],   # 被隔离方向主动出站回连的锚点（host/port），多锚点通用自愈
    "sync_enabled": True,
    "enable_mock": False,  # 内置 mock 测试桩默认关闭（生产不加载；测试/验收显式开启，2.x）
    "discovery_ports": DEFAULT_DISCOVERY_PORTS,
}


def _sanitize_host(hostname: str) -> str:
    h = re.sub(r"[^A-Za-z0-9\-]", "-", hostname).strip("-")
    return h or "pc"


def generate_node_id() -> str:
    return f"node-{_sanitize_host(socket.gethostname())}-{uuid.uuid4().hex[:6]}"


def atomic_write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class NodeConfig:
    """节点配置（内存态 + 落盘）。"""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "node_config.json"
        cfg = dict(DEFAULT_CONFIG)
        if self.path.exists():
            try:
                # utf-8-sig 兼容 PowerShell Set-Content 写出的 BOM
                loaded = json.loads(self.path.read_text(encoding="utf-8-sig"))
                cfg.update({k: v for k, v in loaded.items() if k in DEFAULT_CONFIG})
            except Exception:
                pass  # 损坏则回退默认重建（2.17.4 异常不吞噬：由调用方日志记录）
        first = not cfg.get("node_id")
        if first:
            cfg["node_id"] = generate_node_id()
        if not cfg.get("name"):
            cfg["name"] = socket.gethostname()
        cfg["switches"] = {**DEFAULT_CONFIG["switches"], **(cfg.get("switches") or {})}
        if not isinstance(cfg.get("manual_peers"), list):
            cfg["manual_peers"] = []
        if not isinstance(cfg.get("peer_anchors"), list):
            cfg["peer_anchors"] = []
        self._cfg = cfg
        if first:
            self.save()

    # ---- 基本字段 ----
    @property
    def node_id(self) -> str:
        return self._cfg["node_id"]

    @property
    def name(self) -> str:
        return self._cfg["name"]

    @name.setter
    def name(self, v: str) -> None:
        self._cfg["name"] = str(v).strip()

    @property
    def team_id(self) -> str:
        return self._cfg.get("team_id") or ""

    @team_id.setter
    def team_id(self, v: str) -> None:
        self._cfg["team_id"] = (v or "").strip()

    @property
    def switches(self) -> dict:
        return self._cfg["switches"]

    @property
    def peer_tcp_port(self) -> int:
        return int(self._cfg.get("peer_tcp_port") or 0)

    @peer_tcp_port.setter
    def peer_tcp_port(self, v: int) -> None:
        self._cfg["peer_tcp_port"] = int(v)

    @property
    def manual_peers(self) -> list:
        return self._cfg["manual_peers"]

    @property
    def peer_anchors(self) -> list:
        """锚点列表（host/peer_tcp_port）。被隔离方向用它主动出站回连；可经 env 覆盖。"""
        env = os.environ.get(_ENV_ANCHORS)
        if env:
            parsed = []
            for item in env.replace(";", ",").split(","):
                item = item.strip()
                if not item:
                    continue
                # 支持 host:port 或 host（端口 0=待协商）
                try:
                    host, port = item.rsplit(":", 1)
                    host, port = host.strip(), int(port)
                except ValueError:
                    host, port = item, 0
                if host:
                    parsed.append({"host": host, "peer_tcp_port": port})
            if parsed:
                return parsed
        return list(self._cfg["peer_anchors"])

    @peer_anchors.setter
    def peer_anchors(self, v: list) -> None:
        self._cfg["peer_anchors"] = [a for a in (v or []) if a.get("host")]

    def peer_port_range(self) -> list[int]:
        """约定对等端口段（默认分配与子网扫描共用的可预测端口集合）。"""
        try:
            lo, hi = int(self._cfg.get("peer_port_range_start") or DEFAULT_PEER_PORT_START), \
                     int(self._cfg.get("peer_port_range_end") or DEFAULT_PEER_PORT_END)
        except ValueError:
            lo, hi = DEFAULT_PEER_PORT_START, DEFAULT_PEER_PORT_END
        lo = max(1, min(lo, 65535))
        hi = max(lo, min(hi, 65535))
        return list(range(lo, hi + 1))

    @property
    def sync_enabled(self) -> bool:
        return bool(self._cfg.get("sync_enabled", True))

    @sync_enabled.setter
    def sync_enabled(self, v: bool) -> None:
        self._cfg["sync_enabled"] = bool(v)

    @property
    def enable_mock(self) -> bool:
        """内置 mock 测试桩开关（第五章 #4；生产默认关，测试/验收时设 true 开启）。"""
        return bool(self._cfg.get("enable_mock", False))

    @property
    def run_as_admin(self) -> bool:
        return bool(self._cfg.get("run_as_admin"))

    @run_as_admin.setter
    def run_as_admin(self, v: bool) -> None:
        self._cfg["run_as_admin"] = bool(v)

    def set_switch(self, name: str, enabled: bool) -> None:
        if name in ("allow_shell", "allow_file", "allow_ai_task"):
            self._cfg["switches"][name] = bool(enabled)

    def team_matches(self, peer_team: str | None) -> bool:
        """2.1.7 隔离规则：仅双方 team_id 一致才连通（空=无 team；空↔非空也隔离）。"""
        return (self.team_id or "") == (peer_team or "")

    # ---- 发现端口（环境变量优先，2.14.4） ----
    def discovery_ports(self) -> list[int]:
        env = os.environ.get(_ENV_DISCOVERY_PORTS)
        if env:
            try:
                ports = [int(p) for p in env.replace(";", ",").split(",") if p.strip()]
                if ports:
                    return ports
            except ValueError:
                pass
        cfg_ports = self._cfg.get("discovery_ports")
        return list(cfg_ports) if cfg_ports else list(DEFAULT_DISCOVERY_PORTS)

    # ---- 发现增强（3.x）：组播组 + 固定通告端口 ----
    @property
    def multicast_group(self) -> str:
        return str(self._cfg.get("multicast_group") or DEFAULT_MULTICAST_GROUP).strip()

    @property
    def announce_tcp_port(self) -> int:
        try:
            return int(self._cfg.get("announce_tcp_port") or DEFAULT_ANNOUNCE_TCP_PORT)
        except (TypeError, ValueError):
            return DEFAULT_ANNOUNCE_TCP_PORT

    def inbox_dir(self) -> Path:
        env = os.environ.get(_ENV_INBOX_DIR)
        if env:
            return Path(env).resolve()
        return self.data_dir / "inbox"

    def save(self) -> None:
        atomic_write_json(self.path, self._cfg)

    def as_dict(self) -> dict:
        return json.loads(json.dumps(self._cfg, ensure_ascii=False))


def resolve_data_dir(default: str | Path) -> Path:
    env = os.environ.get(_ENV_CONFIG_DIR)
    return Path(env).resolve() if env else Path(default).resolve()
