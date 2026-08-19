"""局域网队伍发现协议（UDP 广播 beacon）。

主机侧 BeaconBroadcaster：队伍初始化后每 3 秒向 255.255.255.255:41830 广播：
  {"proto": "agent-bus", "ver": 1, "team_id", "team_name", "host_name",
   "mqtt_port", "http_port"}

子设备侧 scan_teams()：绑定 41830 收集 timeout 秒内的 beacon，按 team_id 去重。

安全：beacon 不含任何凭据；入队即匿名登记于 HTTP /api/join（v2 匿名化后无口令）。
选广播而非组播：零配置（组播需 join group，跨 AP/网段行为更不可控），
代价是部分路由器 AP 隔离下不可达——保留手动输 IP 的回退路径。
"""
import json
import socket
import threading
import time

DISCOVERY_PORT = 41830
# 发现端口池：默认 41830 被占用/winNAT 干扰时逐个回退（UDP）
DISCOVERY_PORT_POOL = [41830, 41831, 41832, 41840, 41850]
# MQTT 端口池（中心 broker 用，防止 1883 被 winNAT/WSL 等占用）
MQTT_PORT_POOL = [1883, 1884, 1885, 8883, 18830]
# HTTP 端口池（bus_server 用）
HTTP_PORT_POOL = [8000, 8001, 8002, 8010, 8088]

BEACON_INTERVAL = 3.0
PROTO = "agent-bus"
PROTO_VER = 1


def udp_port_free(port: int) -> bool:
    """探测本进程能否占用一个 UDP 端口（发现用）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", port))
        s.close()
        return True
    except OSError:
        try:
            s.close()
        except Exception:
            pass
        return False


def tcp_port_free(port: int, host="127.0.0.1") -> bool:
    """探测 TCP 端口是否空闲（broker/server 子进程将 bind 它）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        s.listen(1)
        s.close()
        return True
    except OSError:
        try:
            s.close()
        except Exception:
            pass
        return False


def pick_mqtt_port() -> int:
    """选空闲 MQTT 端口（探测不占用，broker 子进程 bind）。"""
    for p in MQTT_PORT_POOL:
        if tcp_port_free(p):
            return p
    return MQTT_PORT_POOL[0]  # 全忙则退回默认（broker 会尝试）


def pick_http_port() -> int:
    """选空闲 HTTP 端口（探测不占用，bus_server 子进程 bind）。"""
    for p in HTTP_PORT_POOL:
        if tcp_port_free(p):
            return p
    return HTTP_PORT_POOL[0]


def pick_discovery_port() -> int:
    """选空闲 UDP 发现端口（本进程占用）。"""
    for p in DISCOVERY_PORT_POOL:
        if udp_port_free(p):
            return p
    return DISCOVERY_PORT_POOL[0]


class BeaconBroadcaster:
    """主机侧广播线程（team 未初始化时不广播）。"""

    def __init__(self, get_beacon):
        """get_beacon: () -> dict | None。返回 None 时不广播（队伍未初始化）。"""
        self.get_beacon = get_beacon
        self._stop = threading.Event()
        self._thread = None
        self._sock = None

    def start(self):
        # Bug 26：sock 在 start() 同步创建，stop() 可安全 close，避免竞态下泄漏
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._thread = threading.Thread(target=self._run, daemon=True, name="beacon")
        self._thread.start()

    def stop(self):
        self._stop.set()
        # B9：关闭 socket 并 join 线程，避免 UDP socket 泄漏至进程结束
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self):
        # sock 已在 start() 中创建并配置好 SO_BROADCAST
        while not self._stop.wait(BEACON_INTERVAL):
            try:
                b = self.get_beacon()
                if b:
                    payload = json.dumps(b, ensure_ascii=False).encode("utf-8")
                    # 255.255.255.255 走默认路由（可能被代理 TUN 劫持），
                    # 另按候选 IP 补发各网段定向广播（192.168.x.255 等），
                    # 确保物理网卡所在网段必达
                    targets = [("255.255.255.255", DISCOVERY_PORT)]
                    for ip in b.get("ips") or []:
                        if ip.count(".") == 3 and not ip.startswith("127."):
                            targets.append((ip.rsplit(".", 1)[0] + ".255",
                                            DISCOVERY_PORT))
                    for t in dict.fromkeys(targets):  # 去重保序
                        # Bug 26：sock 可能已被 stop() close，捕获 OSError
                        try:
                            self._sock.sendto(payload, t)
                        except OSError:
                            break  # 已停止，退出循环
            except OSError:
                pass  # 网卡切换等瞬时错误：下个周期重试


def scan_teams(timeout: float = 5.0) -> list:
    """扫描局域网内的队伍，返回按发现顺序去重后的列表。

    返回项: {team_id, team_name, host_name, host_ip, mqtt_port, http_port, ver}
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", DISCOVERY_PORT))
    sock.settimeout(0.5)
    teams, order = {}, []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data, (ip, _port) = sock.recvfrom(4096)
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            b = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if b.get("proto") != PROTO:
            continue
        tid = b.get("team_id") or ""
        if not tid:
            continue
        if tid not in teams:
            # host_ip = 收包来源地址（对该子设备而言天然可达）；
            # host_ips = 主机自报全部候选（join 侧连通性自检用，发现≠连通）
            ips = [x for x in ([ip] + (b.get("ips") or [])) if x]
            teams[tid] = {
                "team_id": tid,
                "team_name": b.get("team_name", ""),
                "host_name": b.get("host_name", ""),
                "host_ip": ip,
                "host_ips": list(dict.fromkeys(ips)),
                "mqtt_port": int(b.get("mqtt_port", 1883)),
                "http_port": int(b.get("http_port", 8000)),
                "ver": b.get("ver", 1),
            }
            order.append(tid)
        # 同队多网卡/重复 beacon：保留首个（host_ips 并集补充）
        elif b.get("ips"):
            cur = teams[tid]["host_ips"]
            cur.extend(x for x in b["ips"] if x and x not in cur)
    sock.close()
    return [teams[t] for t in order]


# ---------------------------------------------------------------------------
# 对称控制节点发现：每个节点既是广播者也是扫描者（无主从）
# ---------------------------------------------------------------------------


class ControlAdvertiser:
    """控制节点自广播：把本节点的身份、总线地址、受控能力广播到局域网。

    与 BeaconBroadcaster（主机/队伍向心广播）不同，这是控制节点对等广播，
    用于让局域网内其他控制节点“像 LocalSend 一样”发现自己。
    beacon 内容（get_beacon 返回 dict）:
      proto, ver, type="sym_ctl", agent_id, name, host_name, ips,
      mqtt_host, mqtt_port, http_port, controlled, discovery_port
    """

    def __init__(self, get_beacon, discovery_port: int = DISCOVERY_PORT):
        self.get_beacon = get_beacon
        self.discovery_port = discovery_port
        self._stop = threading.Event()
        self._thread = None
        self._sock = None

    def start(self):
        # Bug 26：sock 在 start() 同步创建
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._thread = threading.Thread(target=self._run, daemon=True, name="ctl-beacon")
        self._thread.start()

    def stop(self):
        self._stop.set()
        # B9：关闭 socket 并 join 线程，避免 UDP socket 泄漏至进程结束
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self):
        # sock 已在 start() 中创建并配置好 SO_BROADCAST
        while not self._stop.wait(BEACON_INTERVAL):
            try:
                b = self.get_beacon()
                if not b:
                    continue
                payload = json.dumps(b, ensure_ascii=False).encode("utf-8")
                targets = [("255.255.255.255", self.discovery_port)]
                for ip in b.get("ips") or []:
                    if ip.count(".") == 3 and not ip.startswith("127."):
                        targets.append((ip.rsplit(".", 1)[0] + ".255", self.discovery_port))
                for t in dict.fromkeys(targets):
                    # Bug 26：sock 可能已被 stop() close
                    try:
                        self._sock.sendto(payload, t)
                    except OSError:
                        break
            except OSError:
                pass


def scan_control_nodes(timeout: float = 3.0,
                       discovery_port: int = DISCOVERY_PORT) -> list:
    """扫描局域网内所有控制节点（对等发现）。

    返回按发现顺序去重后的列表（按 agent_id 去重）:
      {agent_id, name, host_name, host_ip, mqtt_host, mqtt_port,
       http_port, controlled, discovery_port, is_master}
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # Bug 27：原 fallback bind(("",0)) 落到随机端口，广播发往 discovery_port
    # 收不到。改为遍历端口池找一个可绑端口；都不可用才放弃。
    bound_port = None
    for p in [discovery_port] + DISCOVERY_PORT_POOL:
        try:
            sock.bind(("", p))
            bound_port = p
            break
        except OSError:
            continue
    if bound_port is None:
        sock.close()
        return []
    sock.settimeout(0.4)
    nodes, order = {}, []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data, (ip, _p) = sock.recvfrom(4096)
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            b = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if b.get("proto") != PROTO or b.get("type") != "sym_ctl":
            continue
        aid = b.get("agent_id") or ""
        if not aid:
            continue
        if aid not in nodes:
            nodes[aid] = {
                "agent_id": aid,
                "name": b.get("name", aid),
                "host_name": b.get("host_name", ""),
                "host_ip": ip,
                "mqtt_host": b.get("mqtt_host", ip),
                "mqtt_port": int(b.get("mqtt_port", 1883)),
                "http_port": int(b.get("http_port", 8000)),
                "controlled": bool(b.get("controlled", True)),
                "discovery_port": int(b.get("discovery_port", discovery_port)),
                "is_master": bool(b.get("is_master", False)),
                "last_seen": time.time(),
            }
            order.append(aid)
        else:
            nodes[aid]["last_seen"] = time.time()
    sock.close()
    return [nodes[a] for a in order]
