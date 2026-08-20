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

_ENV_CONFIG_DIR = "AGENT_NODE_CONFIG_DIR"
_ENV_INBOX_DIR = "AGENT_NODE_INBOX_DIR"
_ENV_DISCOVERY_PORTS = "AGENT_NODE_DISCOVERY_PORTS"

DEFAULT_CONFIG: dict = {
    "node_id": "",
    "name": "",
    "team_id": "",
    "run_as_admin": False,
    "switches": {"allow_shell": True, "allow_file": True, "allow_ai_task": True},
    "peer_tcp_port": 0,
    "manual_peers": [],
    "sync_enabled": True,
    "enable_mock": True,
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
    def sync_enabled(self) -> bool:
        return bool(self._cfg.get("sync_enabled", True))

    @sync_enabled.setter
    def sync_enabled(self, v: bool) -> None:
        self._cfg["sync_enabled"] = bool(v)

    @property
    def enable_mock(self) -> bool:
        """内置 mock 测试桩开关（第五章 #4；验收完成后可关，改 false 重启生效）。"""
        return bool(self._cfg.get("enable_mock", True))

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
