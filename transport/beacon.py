"""UDP beacon 发现（2.1.5 / 2.16.2）。

- 多端口冗余监听 + 向所有发现端口广播；至少一个共同空闲端口即可互通
- 同一 beacon 从多端口到达按 node_id + seq 去重
- 载荷: {v, node_id, name, team_id, ips, ports, switches, capabilities, sync_device_id, seq}
"""
from __future__ import annotations

import json
import socket
import struct
import threading
import time
from collections import deque

BEACON_INTERVAL = 2.0  # 2.1.6 默认时序参数
_BROADCAST_HOST = "255.255.255.255"


def _join_multicast(sock: socket.socket, group: str, ttl: int = 8) -> None:
    """加入组播组（接收）并设置 TTL/回环用于发送。组播与广播正交，互为兜底。"""
    try:
        mreq = struct.pack("4s4s", socket.inet_aton(group), socket.inet_aton("0.0.0.0"))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        # 允许组播回环（本机多实例），并跳数克制避免泄漏出网段
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    except OSError:
        pass


def _local_ips() -> list[str]:
    ips: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        s.connect(("8.8.8.8", 80))  # 不实际发包，仅取本机出口 IP
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    return ips


def _all_local_ips() -> list[str]:
    ips: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    return ips


def _subnet_broadcasts() -> list[str]:
    """按 /24 推导各接口的子网定向广播地址（实测部分 Windows 不收 255.255.255.255）。"""
    outs = []
    for ip in _all_local_ips():
        try:
            bcast = ip.rsplit(".", 1)[0] + ".255"
            if bcast not in outs:
                outs.append(bcast)
        except Exception:
            pass
    return outs


class BeaconService:
    def __init__(self, ports: list[int], my_node_id: str, payload_provider,
                 on_beacon, interval: float = BEACON_INTERVAL,
                 known_hosts_provider=None, multicast_group: str = ""):
        """payload_provider() -> dict（不含 seq）；on_beacon(payload_dict, addr) 在监听线程调用。
        known_hosts_provider() -> set[str]: 已知对端 host IP（beacon 定向单播兜底）。
        multicast_group: 非空则加入该组播组并同时向组播地址收发（与广播正交，3.x）。"""
        self.ports = list(ports)
        self.my_node_id = my_node_id
        self.payload_provider = payload_provider
        self.on_beacon = on_beacon
        self.interval = interval
        self.known_hosts_provider = known_hosts_provider or (lambda: set())
        self.multicast_group = (multicast_group or "").strip()
        self._seq = 0
        self._recent: dict[str, deque] = {}
        self._recent_lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._send_sock: socket.socket | None = None

    # ---- 去重（node_id + 代次 + seq，2.1.5） ----
    # gen（节点本次启动时间戳）参与键：节点重启后 seq 重新计数，用新 gen 隔离，
    # 避免与重启前遗留 seq 窗口冲突而被误丢。
    def _dedup(self, node_id: str, gen: int, seq: int) -> bool:
        key = f"{node_id}@{gen}"
        with self._recent_lock:
            dq = self._recent.setdefault(key, deque(maxlen=512))
            if seq in dq:
                return False
            dq.append(seq)
            return True

    def start(self) -> None:
        self._send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if self.multicast_group:
            _join_multicast(self._send_sock, self.multicast_group)
        for p in self.ports:
            t = threading.Thread(target=self._listen_loop, args=(p,), daemon=True,
                                 name=f"beacon-listen-{p}")
            t.start()
            self._threads.append(t)
        t = threading.Thread(target=self._broadcast_loop, daemon=True, name="beacon-broadcast")
        t.start()
        self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._send_sock:
                self._send_sock.close()
        except Exception:
            pass

    def _listen_loop(self, port: int) -> None:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # Windows 下 SO_REUSEADDR 允许多实例/多进程同端口监听（本地集成测试需要）
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", port))
            if self.multicast_group:
                _join_multicast(sock, self.multicast_group)
            sock.settimeout(1.0)
        except OSError:
            return  # 该端口被占 → 跳过该端口（2.1.5 端口容错）
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                payload = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            node_id = payload.get("node_id")
            seq = payload.get("seq")
            if not node_id or seq is None or node_id == self.my_node_id:
                continue  # 自身 beacon 丢弃
            gen = int(payload.get("gen") or 0)
            if not self._dedup(node_id, gen, int(seq)):
                continue
            try:
                self.on_beacon(payload, addr)
            except Exception:
                pass
        try:
            sock.close()
        except Exception:
            pass

    def _broadcast_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._seq += 1
                base = dict(self.payload_provider())
                base["v"] = 1
                base["seq"] = self._seq
                data = json.dumps(base, ensure_ascii=False).encode("utf-8")
                # 多路并发送达: 全网广播 + 子网定向广播（部分环境不收全网广播）+
                # 已知对端单播（最可靠，已知节点状态更新不受广播环境影响）+ 组播（3.x 正交信道）
                targets = {_BROADCAST_HOST}
                targets.update(_subnet_broadcasts())
                if self.multicast_group:
                    targets.add(self.multicast_group)
                try:
                    targets.update(self.known_hosts_provider())
                except Exception:
                    pass
                for host in targets:
                    for p in self.ports:
                        try:
                            self._send_sock.sendto(data, (host, p))
                        except OSError:
                            continue
            except Exception:
                pass
            self._stop.wait(self.interval)


def build_beacon_payload(node_core) -> dict:
    """节点核心 → beacon 载荷（2.16.2）。"""
    cfg = node_core.config
    return {
        "node_id": cfg.node_id,
        "gen": int(node_core.started_at),   # 代次：节点本次启动时间戳（去重启歧义）
        "name": cfg.name,
        "team_id": cfg.team_id,
        "ips": _local_ips(),
        "ports": {
            "peer_tcp": node_core.mesh.my_listen_port or 0,
            "discovery": cfg.discovery_ports(),
        },
        "switches": dict(cfg.switches),
        "capabilities": node_core.registry.capabilities_payload(),
        "sync_device_id": node_core.sync.device_id if node_core.sync else "",
    }
