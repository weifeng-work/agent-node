"""UDP beacon 发现（2.1.5 / 2.16.2）。

- 多端口冗余监听 + 向所有发现端口广播；至少一个共同空闲端口即可互通
- 同一 beacon 从多端口到达按 node_id + seq 去重
- 载荷: {v, node_id, name, team_id, ips, ports, switches, capabilities, sync_device_id, seq}
"""
from __future__ import annotations

import json
import socket
import threading
import time
from collections import deque

BEACON_INTERVAL = 2.0  # 2.1.6 默认时序参数
_BROADCAST_HOST = "255.255.255.255"


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


class BeaconService:
    def __init__(self, ports: list[int], my_node_id: str, payload_provider,
                 on_beacon, interval: float = BEACON_INTERVAL):
        """payload_provider() -> dict（不含 seq）；on_beacon(payload_dict, addr) 在监听线程调用。"""
        self.ports = list(ports)
        self.my_node_id = my_node_id
        self.payload_provider = payload_provider
        self.on_beacon = on_beacon
        self.interval = interval
        self._seq = 0
        self._recent: dict[str, deque] = {}
        self._recent_lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._send_sock: socket.socket | None = None

    # ---- 去重（node_id + seq，2.1.5） ----
    def _dedup(self, node_id: str, seq: int) -> bool:
        with self._recent_lock:
            dq = self._recent.setdefault(node_id, deque(maxlen=512))
            if seq in dq:
                return False
            dq.append(seq)
            return True

    def start(self) -> None:
        self._send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
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
            if not self._dedup(node_id, int(seq)):
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
                for p in self.ports:
                    try:
                        self._send_sock.sendto(data, (_BROADCAST_HOST, p))
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
