"""Agent Bus 控制节点（对称、托盘可见性、内嵌 MCP Server）—— 单 exe MVP 入口。

三种模式（命令行可选，MCP 可独立启动）：
  默认 / 无参数        托盘 GUI：局域网节点全局可见性 + 受控开关 + 节点命名
  --mcp                独立启动内嵌 MCP Server（供 Claude/Cursor 等作为 MCP Client
                       通过 JSON 配置连接；软件把自身能力封装为标准工具）
  --headless           不起托盘，后台运行（测试/服务用）

用途：
  - 连总线，具备受控能力（被遥控执行本机命令）+ 遥控能力（对他人执行命令）
  - 内嵌 MCP Server，把 发任务/查收件箱/文件读写/执行命令 封装成工具给 AI 用
  - 中心角色（--role hub）额外起 broker + bus_server，成为团队中心
"""

import argparse
import json
import logging
import os
import platform as _platform
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from agent_bus import discovery  # noqa: E402
from agent_bus.client import AgentBus, BusConfig  # noqa: E402
from agent_bus import provision  # noqa: E402

log = logging.getLogger("control_app")

# ---------------------------------------------------------------------------
# 路径 / 数据目录（兼容 PyInstaller 打包 & 源码运行）
# ---------------------------------------------------------------------------


def app_exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return ROOT_DIR


def app_data_dir() -> Path:
    d = app_exe_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def local_ips():
    try:
        return provision.get_local_ips()
    except Exception:
        return []


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    import tempfile

    c = json.dumps(data, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(
        suffix=".tmp", prefix=path.stem + "_", dir=str(path.parent)
    )
    try:
        os.write(fd, c.encode("utf-8"))
        os.close(fd)
        fd = None
        os.replace(tmp, str(path))
    except Exception:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# 文件收件箱（Downloads/agent-node/inbox）—— 其他节点 post_file 的落盘位置
# ---------------------------------------------------------------------------


def downloads_dir() -> Path:
    """Windows 用户 Downloads 目录（Known Folder），失败回退 ~/Downloads。"""
    try:
        import ctypes
        from ctypes import wintypes

        if hasattr(ctypes, "windll"):

            class _GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", wintypes.DWORD),
                    ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            # FOLDERID_Downloads = {374DE290-123F-4565-9164-39C4925E467B}
            fid = _GUID(
                0x374DE290,
                0x123F,
                0x4565,
                (0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B),
            )
            ole32 = ctypes.windll.ole32
            # Bug 28：SHGetKnownFolderPath 返回的 PWSTR 由 CoTaskMem 分配，
            # 调用方必须 CoTaskMemFree 释放，否则每次初始化 inbox 都泄漏一次缓冲。
            ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
            ole32.CoTaskMemFree.restype = None
            fn = ctypes.windll.shell32.SHGetKnownFolderPath
            fn.argtypes = [
                ctypes.POINTER(_GUID),
                wintypes.DWORD,
                wintypes.HANDLE,
                ctypes.POINTER(ctypes.c_wchar_p),
            ]
            fn.restype = wintypes.HRESULT
            p = ctypes.c_wchar_p()
            hr = fn(ctypes.byref(fid), 0, None, ctypes.byref(p))
            if hr == 0 and p.value:
                value = p.value
                # 必须 free 原始指针（p.value 是新 str，原指针已分离）
                ole32.CoTaskMemFree(p)
                return Path(value)
    except Exception:
        pass
    return Path.home() / "Downloads"


def ensure_inbox_dir() -> Path:
    """幂等创建收件箱目录 Downloads/agent-node/inbox。

    只补建缺失目录，绝不删除/覆盖/移动用户已有文件（mkdir exist_ok）。
    """
    inbox = downloads_dir() / "agent-node" / "inbox"
    try:
        inbox.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log.warning("收件箱目录初始化失败 %s: %s", inbox, e)
    return inbox


def zip_dir(src: Path, tmp_dir: Path) -> Path:
    """把目录打包为 zip（保留顶层目录名），返回 zip 路径。"""
    import zipfile

    zpath = tmp_dir / f"{src.name}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src.rglob("*")):
            if p.is_dir():
                continue
            zf.write(p, p.relative_to(src.parent))
    return zpath


def safe_unzip(zpath: Path, dest_dir: Path) -> list:
    """安全解压 zip 到 dest_dir，防 zip-slip，返回解压出的条目名列表。

    Bug 22：chunked 写盘，避免大文件整读到内存爆 OOM。
    """
    import zipfile

    dest_dir = dest_dir.resolve()
    out = []
    with zipfile.ZipFile(zpath) as zf:
        for m in zf.infolist():
            target = (dest_dir / m.filename).resolve()
            if not target.is_relative_to(dest_dir):
                raise ValueError(f"非法解压路径: {m.filename}")
            if m.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                # chunked 读写，避免大文件整读爆内存
                with zf.open(m) as s, open(target, "wb") as d:
                    while True:
                        chunk = s.read(64 * 1024)
                        if not chunk:
                            break
                        d.write(chunk)
            out.append(m.filename)
    return out


# ---------------------------------------------------------------------------
# 控制节点
# ---------------------------------------------------------------------------


class ControlNode:
    """对称控制节点：总线连接 + 受控能力 + 发现 + 配置。"""

    def __init__(self, args):
        self.args = args
        self.role = args.role
        self.data_dir = app_data_dir()
        self.config_file = self.data_dir / "node_config.json"
        self.cfg = load_json(self.config_file, {})

        self.agent_id = args.agent_id or self.cfg.get("agent_id") or self._default_id()
        self.name = args.name or self.cfg.get("name") or self._default_id()
        self.shell_control = bool(self.cfg.get("shell_control", True))
        if args.no_control:
            self.shell_control = False
        self.master_flag = self.role == "hub"

        self.mqtt_port = args.broker_port or 1883
        self.http_port = args.http_port or 8000
        self.discovery_port = args.discovery_port or discovery.DISCOVERY_PORT

        self.broker_proc = None
        self.server_proc = None
        self.bus = None
        self._stop = threading.Event()
        self._found_nodes = []
        self._advertiser = None
        self._rebooted_center = False
        # 收件箱：其他节点 post_file 的落盘位置（幂等创建，不碰已有文件）
        self.inbox_dir = ensure_inbox_dir()

    def _default_id(self):
        host = os.getenv("COMPUTERNAME") or socket.gethostname() or "node"
        return f"ctl-{host.lower()}"

    # ---- 配置持久化 ----
    def _save_config(self):
        save_json(
            self.config_file,
            {
                "agent_id": self.agent_id,
                "name": self.name,
                "shell_control": self.shell_control,
                "updated_at": time.time(),
            },
        )

    def set_name(self, new_name: str):
        if not new_name or not new_name.strip():
            return
        self.name = new_name.strip()
        if self.bus:
            try:
                self.bus.name = self.name
                self.bus.register()  # 立即重发注册，刷新中心名单（改名是重大状态变更）
            except Exception:
                pass
        self._save_config()

    def set_shell_control(self, on: bool):
        self.shell_control = bool(on)
        self._save_config()

    # ---- 中心角色端口兜底 ----
    def _select_ports(self):
        if self.role != "hub":
            return
        if not self.args.broker_port:
            self.mqtt_port = discovery.pick_mqtt_port()
        if not self.args.http_port:
            self.http_port = discovery.pick_http_port()
        if not self.args.discovery_port:
            self.discovery_port = discovery.pick_discovery_port()
        log.info(
            "中心端口 mqtt=%s http=%s discovery=%s",
            self.mqtt_port,
            self.http_port,
            self.discovery_port,
        )

    # ---- 发现 ----
    def _beacon_host(self) -> str:
        """beacon 里 mqtt_host 字段：hub 广播自己的局域网 IP（worker 需据此可达），
        worker 广播自己连接的中心地址。"""
        if self.role == "hub":
            ips = local_ips()
            for ip in ips:
                if ip and ip != "127.0.0.1":
                    return ip
            return "127.0.0.1"
        return getattr(self, "_broker_host", "127.0.0.1")

    def _beacon(self) -> dict:
        return {
            "proto": discovery.PROTO,
            "ver": discovery.PROTO_VER,
            "type": "sym_ctl",
            "agent_id": self.agent_id,
            "name": self.name,
            "host_name": _platform.node(),
            "ips": local_ips(),
            "mqtt_host": self._beacon_host(),
            "mqtt_port": self.mqtt_port,
            "http_port": self.http_port,
            "controlled": self.shell_control,
            "discovery_port": self.discovery_port,
            "is_master": self.master_flag,
        }

    def start_advertising(self):
        self._advertiser = discovery.ControlAdvertiser(
            self._beacon, self.discovery_port
        )
        self._advertiser.start()

    def refresh_peers(self):
        self._found_nodes = discovery.scan_control_nodes(
            timeout=1.2, discovery_port=self.discovery_port
        )

    def peers_loop(self):
        while not self._stop.wait(4.0):
            try:
                self.refresh_peers()
                if getattr(self, "on_peers", None):
                    self.on_peers(self._found_nodes)
            except Exception:
                pass

    # ---- 总线 ----
    def _discover_center(self) -> tuple:
        """Bug 30：扫描局域网中心（is_master）节点，返回 (broker_host, mqtt_port, http_port)。

        用 host_ip（收到广播的源地址，天然可达）而非 hub 自报 mqtt_host，
        避免 hub 广播 127.0.0.1 时 worker 连到自己。扫不到返回 None。
        """
        try:
            nodes = discovery.scan_control_nodes(
                timeout=2.0, discovery_port=self.discovery_port
            )
            for n in nodes:
                if not n.get("is_master"):
                    continue
                host = n.get("host_ip") or n.get("mqtt_host") or ""
                if not host:
                    continue
                return (
                    host,
                    int(n.get("mqtt_port") or 1883),
                    int(n.get("http_port") or 8000),
                )
        except Exception:
            pass
        return None

    def _center_http_port(self, host: str) -> int:
        """worker 连远程中心时，文件服务端口取自 beacon 扫描到的中心节点。

        中心（is_master）广播自己的 http_port；扫不到时回退 8000。
        """
        try:
            nodes = discovery.scan_control_nodes(
                timeout=1.0, discovery_port=self.discovery_port
            )
            for n in nodes:
                if n.get("is_master") and n.get("mqtt_host", "") == host:
                    return int(n.get("http_port") or 8000)
            for n in nodes:
                if n.get("is_master"):
                    return int(n.get("http_port") or 8000)
        except Exception:
            pass
        return 8000

    def connect(self, broker_host=None):
        """建立总线连接。

        地址解析优先级：
          hub    → 本地 127.0.0.1 + 自身端口（自己起 broker）
          worker + 显式 --broker-host → 用该 host + 自身 mqtt_port + 扫中心 http_port
          worker + 无显式 host → 自动扫描 is_master 取真实 host/端口；
                                  扫不到回退本地（连接会失败，log 提示）
        """
        if self.role == "hub":
            host, mqtt_port, http_port = "127.0.0.1", self.mqtt_port, self.http_port
        else:
            explicit = broker_host or self.args.broker_host or ""
            if explicit:
                host = explicit
                mqtt_port = self.mqtt_port
                http_port = self._center_http_port(host)
            else:
                center = self._discover_center()
                if center:
                    host, mqtt_port, http_port = center
                    log.info(
                        "自动发现中心 %s (mqtt=%s http=%s)",
                        host, mqtt_port, http_port,
                    )
                else:
                    host, mqtt_port, http_port = "127.0.0.1", self.mqtt_port, 8000
                    log.warning(
                        "未发现中心节点，回退本地 %s:%s（若无本地 broker 将连接失败）",
                        host, mqtt_port,
                    )

        self._broker_host = host
        # worker 语境下 mqtt_port/http_port 即"我连的中心地址"，写回使 beacon 一致
        self.mqtt_port = mqtt_port
        self.http_port = http_port
        # 文件服务（upload/download/名单）由中心 bus_server 提供：
        cfg = BusConfig.load(
            broker_host=host,
            broker_port=mqtt_port,
            http_base=f"http://{host}:{http_port}",
            agent_id=self.agent_id,
        )
        caps = ["supervise", "shell"] + (
            ["route", "master"] if self.role == "hub" else []
        )
        self.bus = AgentBus(
            self.agent_id,
            name=self.name,
            capabilities=caps,
            executor="control",
            config=cfg,
        )
        self.bus.on_message = self._on_message
        self.bus.connect(register=True, timeout=8)

    # ---- 消息分发：受控能力（shell_exec / rename / fetch_file / post_file） ----
    def _on_message(self, msg: dict) -> bool:
        """处理总线消息，返回 True 表示已消费（不再入 inbox 供 MCP 拉取）。"""
        op = (msg.get("payload") or {}).get("op") if isinstance(msg, dict) else None
        try:
            if op == "shell_exec":
                self._handle_shell_exec(msg)
            elif op == "rename":
                self._handle_rename(msg)
            elif op == "fetch_file":
                self._handle_fetch_file(msg)
            elif op == "post_file":
                self._handle_post_file(msg)
        except Exception as e:
            log.warning("消息处理异常: %s", e)
        # 已识别的 control op 无论处理成败都视为已消费，避免 MCP 重复处理同一条
        return op in ("shell_exec", "rename", "fetch_file", "post_file")

    def _handle_rename(self, msg: dict):
        """网页面板下发的改名指令：持久化到本地 config 并回执。"""
        payload = dict(msg.get("payload") or {})
        new_name = (payload.get("name") or "").strip()
        sender = msg.get("sender_id", "")
        if not new_name:
            self._reply(msg, status="error", error="缺少 name")
            return
        self.set_name(new_name)
        log.info("[%s] 按 %s 指令改名为: %s", self.agent_id, sender, self.name)
        self._reply(msg, status="success", output_text=f"节点已改名为 {self.name}")

    def _handle_shell_exec(self, msg: dict):
        payload = dict(msg.get("payload") or {})
        cmd = payload.get("cmd", "")
        sender = msg.get("sender_id", "")
        # 受控能力开关（托盘可关闭）
        if not self.shell_control:
            self._reply(msg, status="error", error="shell_control_disabled")
            return
        if not sender:
            self._reply(msg, status="error", error="缺少 sender_id")
            return
        cwd = payload.get("cwd") or None
        timeout = float(payload.get("timeout_seconds", 60))
        try:
            r = subprocess.run(
                cmd,
                shell=True,
                cwd=cwd,
                capture_output=True,
                timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            out = self._decode(r.stdout)
            if r.stderr:
                out += "\n" + self._decode(r.stderr)
            self._reply(
                msg,
                status="success" if r.returncode == 0 else "error",
                output_text=out[:20000],
                error=None if r.returncode == 0 else f"exit={r.returncode}",
            )
        except subprocess.TimeoutExpired:
            self._reply(msg, status="timeout", error="命令超时")
        except Exception as e:
            self._reply(msg, status="error", error=str(e))

    def _handle_fetch_file(self, msg: dict):
        """对方主动取本机文件/目录：打包上传 → 回执 URL（请求方自行 download）。"""
        payload = dict(msg.get("payload") or {})
        path = (payload.get("path") or "").strip()
        if not self.shell_control:
            self._reply(msg, status="error", error="shell_control_disabled")
            return
        p = Path(path)
        # Bug 36：拒绝 symlink 越权读取。Path.exists() 跟随符号链接，
        # 攻击者可用 symlink 指向目录外敏感文件（如 ~/.ssh/id_rsa）。
        # 用 lstat + is_symlink 判断，symlink 一律拒绝。
        if p.is_symlink():
            self._reply(msg, status="error", error=f"拒绝读取符号链接: {path}")
            return
        if not p.exists():
            self._reply(msg, status="error", error=f"不存在: {path}")
            return
        import shutil
        import tempfile

        tmp = None
        try:
            if p.is_dir():
                tmp = Path(tempfile.mkdtemp(prefix="agentnode_fetch_"))
                up_path = zip_dir(p, tmp)
            else:
                up_path = p
            up = self.bus.upload(str(up_path))
            out = json.dumps(
                {
                    "url": up["url"],
                    "name": up.get("name") or Path(up_path).name,
                    "size": up.get("size", 0),
                    "is_dir": p.is_dir(),
                },
                ensure_ascii=False,
            )
            self._reply(msg, status="success", output_text=out)
        except Exception as e:
            self._reply(msg, status="error", error=str(e))
        finally:
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)

    def _handle_post_file(self, msg: dict):
        """接收对方推送的文件/目录：落盘到本机收件箱，zip 安全解压。"""
        payload = dict(msg.get("payload") or {})
        urls = payload.get("urls") or []
        names = payload.get("names") or []
        unpack = payload.get("unpack") or []
        if not self.shell_control:
            self._reply(msg, status="error", error="shell_control_disabled")
            return
        saved, unpacked = [], []
        try:
            for i, url in enumerate(urls):
                raw = names[i] if i < len(names) else f"file_{i}"
                # basename 防路径穿越（兼容反斜杠）
                name = os.path.basename(str(raw).replace("\\", "/")) or f"file_{i}"
                dest = self.inbox_dir / name
                # Bug 23：files.download_file 已用临时文件+rename，失败不残留
                self.bus.download(url, str(dest))
                saved.append(str(dest))
                if i < len(unpack) and unpack[i] and name.lower().endswith(".zip"):
                    unpacked.extend(safe_unzip(dest, self.inbox_dir))
                    dest.unlink(missing_ok=True)  # 解压完成删掉 zip 本体
            out = json.dumps({"saved": saved, "unpacked": unpacked}, ensure_ascii=False)
            self._reply(msg, status="success", output_text=out)
        except Exception as e:
            # Bug 20：异常时把已成功落盘的 saved 列表也带回执，
            # 让发起方知道哪些已下载、哪些失败，避免误以为全失败而重发导致 inbox 重复
            partial = json.dumps(
                {"saved": saved, "unpacked": unpacked, "error": str(e)},
                ensure_ascii=False,
            )
            self._reply(msg, status="error", output_text=partial, error=str(e))

    @staticmethod
    def _decode(b: bytes) -> str:
        if b is None:
            return ""
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                return b.decode(enc)
            except Exception:
                continue
        return b.decode("utf-8", errors="replace")

    def _reply(self, msg, status, output_text="", error=None):
        if self.bus:
            try:
                self.bus.reply_task(
                    msg, output_text=output_text, status=status, error=error
                )
            except Exception as e:
                log.warning("回执失败: %s", e)

    # ---- 中心：broker + server ----
    def _broker_exe(self) -> str:
        """找到可用 mosquitto：系统已装优先，否则从内置便携包解压到数据目录。"""
        import shutil

        # 1. 系统装有
        exe = shutil.which("mosquitto")
        if exe:
            return exe
        for base in (r"C:\Program Files\mosquitto", r"C:\mosquitto"):
            p = Path(base) / "mosquitto.exe"
            if p.exists():
                return str(p)
        # 2. 内置便携包（打包后从 sys._MEIPASS 解压到 app_data_dir/runtime/mosquitto）
        mosq_dir = app_data_dir() / "runtime" / "mosquitto"
        exe = mosq_dir / "mosquitto.exe"
        if exe.exists():
            return str(exe)
        # 3. 首启：从内置 zip 解压便携 mosquitto
        self._unpack_mosquitto(mosq_dir)
        return str(exe) if exe.exists() else ""

    def _unpack_mosquitto(self, dest: Path):
        import zipfile

        dest.mkdir(parents=True, exist_ok=True)
        # 打包内嵌路径：sys._MEIPASS/mosquitto/mosquitto.zip；源码路径 build/mosquitto.zip
        candidates = []
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            candidates.append(Path(bundle) / "mosquitto" / "mosquitto.zip")
        candidates.append(ROOT_DIR / "build" / "mosquitto.zip")
        for z in candidates:
            if z and z.exists():
                log.info("解压内置 mosquitto: %s → %s", z, dest)
                with zipfile.ZipFile(z) as zf:
                    zf.extractall(dest)
                return
        raise RuntimeError(
            "未找到内置 mosquitto.zip（打包失败或源码缺 build/mosquitto.zip）"
        )

    def start_center(self):
        if self.role != "hub":
            return
        # ---- broker ----
        exe = self._broker_exe()
        if not exe:
            raise RuntimeError("mosquitto 不可用（本机未装且内置包缺失）")
        if self._tcp_open(self.mqtt_port):
            log.info("mqtt %s 已有 broker（复用）", self.mqtt_port)
        else:
            conf_dir = app_data_dir() / "runtime"
            conf_dir.mkdir(parents=True, exist_ok=True)
            conf = conf_dir / "mosquitto.conf"
            conf.write_text(
                f"listener {self.mqtt_port} 0.0.0.0\nallow_anonymous true\n",
                encoding="utf-8",
            )
            self.broker_proc = subprocess.Popen(
                [exe, "-c", str(conf)],
                cwd=str(conf.parent),
                stdout=open(app_data_dir() / "broker.log", "ab"),
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            log.info("broker 已启动 pid=%s :%s", self.broker_proc.pid, self.mqtt_port)
            # 等待 broker 端口就绪，避免下游 MqttBridge/connect 抢跑失败
            if not self._wait_port_ready(self.mqtt_port, timeout=5.0):
                log.warning(
                    "broker 端口 %s 5s 内未就绪，下游可能首次连接失败（将靠自动重连恢复）",
                    self.mqtt_port,
                )

        # ---- bus_server（编程式线程内联启动） ----
        try:
            from server.bus_server import serve_bus_server

            db = str(app_data_dir() / "bus.db")
            files_dir = str(app_data_dir() / "files")
            static_dir = self._static_dir()
            store, bridge, app_, uv_server = serve_bus_server(
                host="0.0.0.0",
                port=self.http_port,
                broker_host="127.0.0.1",
                broker_port=self.mqtt_port,
                db=db,
                files_dir=files_dir,
                static_dir=static_dir,
                master_agent_id=self.agent_id,
                on_rename_local=self.set_name,
            )
            self.server_store = store
            self._bridge = bridge
            self._uv_server = uv_server
            threading.Thread(target=uv_server.run, daemon=True).start()
            log.info("bus_server 内联已启动 :%s", self.http_port)
        except Exception as e:
            log.error("bus_server 内联启动失败: %s", e)

    @staticmethod
    def _static_dir() -> str:
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            p = Path(bundle) / "server" / "static"
            if p.is_dir():
                return str(p)
        p = ROOT_DIR / "server" / "static"
        return str(p)

    @staticmethod
    def _tcp_open(port: int, host="127.0.0.1") -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.4):
                return True
        except OSError:
            return False

    @staticmethod
    def _wait_port_ready(port: int, host="127.0.0.1", timeout=5.0) -> bool:
        """轮询等待 TCP 端口可连接，超时返回 False。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if ControlNode._tcp_open(port, host):
                return True
            time.sleep(0.2)
        return False

    # ---- 运行 ----
    def run(self):
        self._select_ports()
        if self.role == "hub":
            self.start_center()
        self.connect(self.args.broker_host or None)
        self.start_advertising()
        threading.Thread(target=self.peers_loop, daemon=True).start()

    def shutdown(self):
        self._stop.set()
        if self._advertiser:
            try:
                self._advertiser.stop()
            except Exception:
                pass
        if self.bus:
            try:
                self.bus.disconnect()
            except Exception:
                pass
        # Bug 16：停 bus_server（uvicorn daemon 线程）
        uv = getattr(self, "_uv_server", None)
        if uv is not None:
            try:
                uv.should_exit = True
            except Exception:
                pass
        bridge = getattr(self, "_bridge", None)
        if bridge is not None:
            try:
                bridge.stop()
            except Exception:
                pass
        # Bug 16：终止 mosquitto 子进程，避免 hub 退出后孤儿进程残留
        if self.broker_proc is not None:
            try:
                self.broker_proc.terminate()
                self.broker_proc.wait(timeout=2)
            except Exception:
                try:
                    self.broker_proc.kill()
                except Exception:
                    pass
        log.info("控制节点已退出")


# ---------------------------------------------------------------------------
# 内嵌 MCP Server：把控制节点能力封装成 MCP 工具（可独立 --mcp 启动）
# ---------------------------------------------------------------------------


class EmbeddedMCPServer:
    """把控制节点能力暴露为 MCP 工具，供 Claude Desktop/Cursor 等作为 MCP Client
    通过 JSON 配置连接。FastMCP 走 stdin/stdout 传输，天然适配 MCP 客户端。"""

    def __init__(self, node: ControlNode):
        self.node = node

    def tools(self, mcp):
        @mcp.tool()
        def list_online_agents() -> str:
            """查看总线上在线智能体及其在线状态。"""
            try:
                return json.dumps(self.node.bus.list_agents(), ensure_ascii=False)
            except Exception as e:
                return json.dumps({"error": str(e)})

        @mcp.tool()
        def send_task(
            target_id: str,
            instruction: str,
            file_paths: str = "",
            wait_seconds: int = 300,
        ) -> str:
            """给另一个智能体发任务。wait_seconds>0 阻塞等结果；0 异步发完即回。"""
            try:
                atts = []
                for p in filter(None, [x.strip() for x in file_paths.split(",")]):
                    atts.append(self.node.bus.upload(p)["url"])
                result = self.node.bus.send_task(
                    target_id,
                    instruction,
                    attachments=atts,
                    wait=wait_seconds > 0,
                    wait_timeout=wait_seconds or None,
                )
                return json.dumps(result, ensure_ascii=False)
            except Exception as e:
                return json.dumps({"error": str(e)})

        @mcp.tool()
        def check_inbox(timeout_seconds: float = 3.0) -> str:
            """拉取发给我（本控制节点 agent_id）的新消息。"""
            try:
                msgs = self.node.bus.poll_inbox(timeout=timeout_seconds)
                return json.dumps(msgs, ensure_ascii=False)
            except Exception as e:
                return json.dumps({"error": str(e)})

        @mcp.tool()
        def run_command(command: str, cwd: str = "", timeout_seconds: int = 60) -> str:
            """在本机执行命令行命令并返回输出（受控能力）。

            Bug 25：与远程 shell_exec 一致，受 shell_control 开关控制。
            托盘"关闭受控能力"后此工具也拒绝执行，避免开关形同虚设。
            """
            try:
                if not self.node.shell_control:
                    return json.dumps({"error": "shell_control_disabled"})
                r = subprocess.run(
                    command,
                    shell=True,
                    cwd=cwd or None,
                    capture_output=True,
                    timeout=timeout_seconds,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                out = ControlNode._decode(r.stdout)
                if r.stderr:
                    out += "\n" + ControlNode._decode(r.stderr)
                return json.dumps(
                    {"exit": r.returncode, "output": out[:20000]}, ensure_ascii=False
                )
            except subprocess.TimeoutExpired:
                return json.dumps({"error": "timeout"})
            except Exception as e:
                return json.dumps({"error": str(e)})

        @mcp.tool()
        def read_file(path: str) -> str:
            """读取本机文本文件内容（用于把代码文件内容交给智能体）。"""
            try:
                p = Path(path)
                if not p.exists():
                    return json.dumps({"error": f"不存在: {path}"})
                data = p.read_bytes()
                text = data.decode("utf-8", errors="replace")[:50000]
                return json.dumps({"path": str(p), "content": text}, ensure_ascii=False)
            except Exception as e:
                return json.dumps({"error": str(e)})

        @mcp.tool()
        def upload_file(path: str) -> str:
            """上传本地文件到总线文件服务，返回可放入消息的 URL。"""
            try:
                return json.dumps(self.node.bus.upload(path), ensure_ascii=False)
            except Exception as e:
                return json.dumps({"error": str(e)})

        @mcp.tool()
        def download_file(url: str, dest: str) -> str:
            """下载总线上的文件到本机路径。"""
            try:
                return json.dumps({"saved_to": self.node.bus.download(url, dest)})
            except Exception as e:
                return json.dumps({"error": str(e)})

        @mcp.tool()
        def fetch_file(target_id: str, path: str, dest: str = "") -> str:
            """从远程节点主动拉取文件/目录到本机。

            path 为远程绝对路径（目录会自动打包）。dest 缺省存到本机
            Downloads/agent-node/inbox 下。返回落盘路径。
            """
            from agent_bus.schema import make_task_request

            try:
                node = self.node
                req = make_task_request(node.agent_id, target_id, "fetch_file")
                req["payload"] = {"op": "fetch_file", "path": path}
                res = node.bus.send_msg(
                    target_id, req, wait=True, wait_timeout=300
                )
                if not res:
                    return json.dumps({"error": "无回执/超时"})
                r = res.get("result") or {}
                if res.get("status") != "success":
                    return json.dumps(
                        {"error": r.get("error") or r.get("output_text", "")}
                    )
                info = json.loads(r.get("output_text") or "{}")
                if not info.get("url"):
                    return json.dumps({"error": "回执缺少 url"})
                name = info.get("name") or "fetched.bin"
                dest_path = dest or str(node.inbox_dir / name)
                saved = node.bus.download(info["url"], dest_path)
                return json.dumps(
                    {"saved_to": saved, "size": info.get("size", 0)},
                    ensure_ascii=False,
                )
            except Exception as e:
                return json.dumps({"error": str(e)})

        @mcp.tool()
        def post_file(target_id: str, paths: str) -> str:
            """向远程节点推送文件/文件夹，落入其 Downloads/agent-node/inbox。

            paths 逗号分隔（文件直接传；文件夹自动打包 zip，对方解压到 inbox）。
            """
            from agent_bus.schema import make_task_request

            import shutil
            import tempfile

            tmp = None
            try:
                node = self.node
                tmp = Path(tempfile.mkdtemp(prefix="agentnode_post_"))
                entries = []
                for raw in filter(None, [x.strip() for x in paths.split(",")]):
                    p = Path(raw)
                    if not p.exists():
                        return json.dumps({"error": f"不存在: {raw}"})
                    if p.is_dir():
                        zp = zip_dir(p, tmp)
                        entries.append(
                            {"path": str(zp), "name": zp.name, "unpack": True}
                        )
                    else:
                        entries.append(
                            {"path": str(p), "name": p.name, "unpack": False}
                        )
                urls, names, unpack = [], [], []
                for e in entries:
                    up = node.bus.upload(e["path"])
                    urls.append(up["url"])
                    names.append(e["name"])
                    unpack.append(e["unpack"])
                req = make_task_request(node.agent_id, target_id, "post_file")
                req["payload"] = {
                    "op": "post_file",
                    "urls": urls,
                    "names": names,
                    "unpack": unpack,
                }
                res = node.bus.send_msg(target_id, req, wait=True, wait_timeout=600)
                if not res:
                    return json.dumps({"error": "无回执/超时"})
                # Bug 19：检查 status；error 时也回传 saved/partial 列表（_handle_post_file 已带）
                if res.get("status") != "success":
                    r = res.get("result") or {}
                    err = res.get("error") or r.get("output_text", "")
                    return json.dumps(
                        {"error": err, "status": res.get("status")},
                        ensure_ascii=False,
                    )
                return json.dumps(res.get("result") or {}, ensure_ascii=False)
            except Exception as e:
                return json.dumps({"error": str(e)})
            finally:
                if tmp is not None:
                    shutil.rmtree(tmp, ignore_errors=True)

        @mcp.tool()
        def reply_task(
            request_json: str, output_text: str, status: str = "success"
        ) -> str:
            """回传任务结果。request_json 传 check_inbox 收到的原始请求。"""
            try:
                req = json.loads(request_json)
                self.node.bus.reply_task(req, output_text=output_text, status=status)
                return json.dumps({"ok": True})
            except Exception as e:
                return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# 托盘
# ---------------------------------------------------------------------------


def run_tray(node: "ControlNode"):
    import pystray
    from PIL import Image, ImageDraw

    def _make_icon(on_count: int) -> "Image":
        img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        color = (29, 201, 129, 255) if on_count > 0 else (200, 200, 200, 255)
        d.ellipse([4, 4, 28, 28], fill=color)
        return img

    # ---- 回调（定义在 build_menu 之前，闭包运行时解析） ----
    def _menu_rename(icon=None, item=None):
        # 改名改到网页面板进行（原生弹窗在 pystray 线程下选中/按钮异常，弃用弹窗方案）
        _open_center(icon, item)

    def _menu_toggle_shell(icon=None, item=None):
        node.set_shell_control(not node.shell_control)

    def _menu_exec(peer):
        cmd = _ask_string(
            "远程执行", f"在 {peer.get('name')} 执行命令:", initial="echo hello"
        )
        if cmd:
            threading.Thread(
                target=_exec_worker, args=(node, peer, cmd), daemon=True
            ).start()

    def _open_http(peer):
        import webbrowser

        webbrowser.open(f"http://{peer.get('host_ip')}:{peer.get('http_port')}/")

    def _open_center(icon=None, item=None):
        import webbrowser

        webbrowser.open(f"http://127.0.0.1:{node.http_port}/")

    def _quit(icon, item, *a):
        icon.stop()

    def _build_menu():
        items = [
            pystray.MenuItem(
                f"Agent Bus [{node.role}] {node.name}", None, enabled=False
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("设置节点名…", _menu_rename, enabled=True),
            pystray.MenuItem(
                "受控能力（允许被遥控执行命令）",
                _menu_toggle_shell,
                checked=lambda it: node.shell_control,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "局域网节点数: %d" % len(node._found_nodes), None, enabled=False
            ),
        ]
        for n in node._found_nodes[:20]:
            sub = pystray.Menu(
                pystray.MenuItem(
                    "在此电脑执行命令", lambda i, it, nn=n: _menu_exec(nn)
                ),
                pystray.MenuItem("打开面板", lambda i, it, nn=n: _open_http(nn)),
            )
            items.append(
                pystray.MenuItem(
                    f"{n.get('name')} @ {n.get('host_name', '')}", submenu=sub
                )
            )
        items.append(pystray.Menu.SEPARATOR)
        if node.role == "hub":
            items.append(pystray.MenuItem("打开中心面板", _open_center))
        items.append(pystray.MenuItem("退出", _quit))
        return pystray.Menu(*items)

    icon = pystray.Icon("agent-bus-ctl", _make_icon(0), "Agent Bus", None)

    def _refresh(peers):
        icon.icon = _make_icon(len(peers))
        icon.title = f"Agent Bus [{node.role}] {node.name} · 发现 {len(peers)} 节点"
        try:
            icon.menu = _build_menu()
            icon.update_menu()
        except Exception:
            pass

    node.on_peers = _refresh
    icon.menu = _build_menu()
    icon.run()


def _ask_string(title, prompt, initial=""):
    """Windows 原生模态输入框，替代 tkinter simpledialog。

    使用 CreateWindowEx 创建对话框，包含静态文本提示、编辑框、确定/取消按钮。
    通过模态消息循环阻塞等待用户输入，返回输入文本或 None。

    优势：
    - 不依赖 tkinter，无 grab 冲突，彻底解决 pystray 菜单崩溃问题
    - Windows 原生外观，无需 withdraw / _keep_focus 等 hack
    - 线程安全，可直接从 pystray 回调线程调用
    """
    import ctypes
    import ctypes.wintypes as w

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # ---- ctypes 类型别名（Python 3.14 wintypes 移除了部分结构体，手动补齐） ----
    HWND = w.HWND
    UINT = w.UINT
    WPARAM = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint
    LPARAM = ctypes.c_int64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
    LRESULT = LPARAM
    HINSTANCE = w.HINSTANCE
    HICON = w.HICON
    HCURSOR = w.HCURSOR
    HBRUSH = w.HBRUSH
    LPCWSTR = w.LPCWSTR

    # WNDPROC 函数指针类型（需在 WNDCLASSEXW 之前定义）
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM)

    # 手动定义 WNDCLASSEXW（Python 3.14 wintypes 已移除）
    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", UINT),
            ("style", UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", HINSTANCE),
            ("hIcon", HICON),
            ("hCursor", HCURSOR),
            ("hbrBackground", HBRUSH),
            ("lpszMenuName", LPCWSTR),
            ("lpszClassName", LPCWSTR),
            ("hIconSm", HICON),
        ]

    # 手动定义 MSG + POINT（同上版本兼容）
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", HWND),
            ("message", UINT),
            ("wParam", WPARAM),
            ("lParam", LPARAM),
            ("time", w.DWORD),
            ("pt", POINT),
        ]

    # ---- constants ----
    IDC_EDIT = 100
    IDOK, IDCANCEL = 1, 2
    WS_OVERLAPPED = 0x00000000
    WS_CAPTION = 0x00C00000
    WS_SYSMENU = 0x00080000
    WS_VISIBLE = 0x10000000
    WS_CHILD = 0x40000000
    WS_BORDER = 0x00800000
    WS_TABSTOP = 0x00010000
    WS_EX_TOPMOST = 0x00000008
    WS_EX_DLGMODALFRAME = 0x00000001
    ES_AUTOHSCROLL = 0x00000080
    ES_LEFT = 0x00000000
    BS_PUSHBUTTON = 0x00000000
    BS_DEFPUSHBUTTON = 0x00000001
    SS_LEFT = 0x00000000
    SM_CXSCREEN, SM_CYSCREEN = 0, 1
    WM_CLOSE = 0x0010
    WM_DESTROY = 0x0002
    WM_COMMAND = 0x0111
    EM_SETSEL = 0x00B1

    # ---- Bug 11/29：补全 user32/kernel32 argtypes/restype
    #      64 位下未设类型会让 HWND/HANDLE 指针被截断到 c_int，造成句柄错位崩溃。
    kernel32.GetModuleHandleW.argtypes = [LPCWSTR]
    kernel32.GetModuleHandleW.restype = HINSTANCE
    kernel32.GetLastError.argtypes = []
    kernel32.GetLastError.restype = w.DWORD

    user32.RegisterClassExW.argtypes = [ctypes.c_void_p]  # CONST WNDCLASSEXW*
    user32.RegisterClassExW.restype = w.ATOM  # 16-bit atom；失败返回 0
    user32.CreateWindowExW.argtypes = [
        w.DWORD, LPCWSTR, LPCWSTR, w.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        HWND, w.HMENU, HINSTANCE, ctypes.c_void_p,
    ]
    user32.CreateWindowExW.restype = HWND
    user32.DestroyWindow.argtypes = [HWND]
    user32.DestroyWindow.restype = w.BOOL
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.PostQuitMessage.restype = None
    user32.DefWindowProcW.argtypes = [HWND, UINT, WPARAM, LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.GetDlgItemTextW.argtypes = [HWND, ctypes.c_int, w.LPWSTR, ctypes.c_int]
    user32.GetDlgItemTextW.restype = ctypes.c_int  # 返回字符数（不含 null）
    user32.LoadCursorW.argtypes = [HINSTANCE, w.LPCWSTR]  # 第二参数可传 MAKEINTRESOURCE/id
    user32.LoadCursorW.restype = HCURSOR
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.SetFocus.argtypes = [HWND]
    user32.SetFocus.restype = HWND
    user32.SendMessageW.argtypes = [HWND, UINT, WPARAM, LPARAM]
    user32.SendMessageW.restype = LRESULT
    user32.GetMessageW.argtypes = [ctypes.c_void_p, HWND, UINT, UINT]  # LPMSG, HWND, UINT, UINT
    user32.GetMessageW.restype = w.BOOL
    user32.IsDialogMessageW.argtypes = [HWND, ctypes.c_void_p]  # HWND, LPMSG
    user32.IsDialogMessageW.restype = w.BOOL
    user32.TranslateMessage.argtypes = [ctypes.c_void_p]  # LPMSG
    user32.TranslateMessage.restype = w.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.c_void_p]  # LPMSG
    user32.DispatchMessageW.restype = LRESULT

    # ---- state (list for closure mutability) ----
    result = [None]

    # ---- window procedure ----
    @WNDPROC
    def dlg_proc(hwnd, msg, wp, lp):
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        if msg == WM_COMMAND:
            cmd_id = wp & 0xFFFF
            if cmd_id == IDOK:
                buf = ctypes.create_unicode_buffer(512)
                user32.GetDlgItemTextW(hwnd, IDC_EDIT, buf, 512)
                result[0] = buf.value
                user32.DestroyWindow(hwnd)
                return 0
            if cmd_id == IDCANCEL:
                user32.DestroyWindow(hwnd)
                return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    # ---- register window class ----
    hinst = kernel32.GetModuleHandleW(None)
    cls_name = "LoomyInputDlg_" + str(ctypes.addressof(dlg_proc))

    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.style = 0
    wc.lpfnWndProc = dlg_proc
    wc.cbClsExtra = 0
    wc.cbWndExtra = 0
    wc.hInstance = hinst
    wc.hIcon = None
    wc.hCursor = user32.LoadCursorW(None, 32512)  # IDC_ARROW
    wc.hbrBackground = 16  # COLOR_3DFACE + 1
    wc.lpszMenuName = None
    wc.lpszClassName = cls_name
    wc.hIconSm = None

    if not user32.RegisterClassExW(ctypes.byref(wc)):
        if kernel32.GetLastError() != 1410:  # ERROR_CLASS_ALREADY_EXISTS
            return None

    # ---- screen-centered position ----
    dlg_w, dlg_h = 400, 135
    x = (user32.GetSystemMetrics(SM_CXSCREEN) - dlg_w) // 2
    y = (user32.GetSystemMetrics(SM_CYSCREEN) - dlg_h) // 2

    # ---- create dialog window ----
    hwnd = user32.CreateWindowExW(
        WS_EX_TOPMOST | WS_EX_DLGMODALFRAME, cls_name, title,
        WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_VISIBLE,
        x, y, dlg_w, dlg_h, None, None, hinst, None,
    )
    if not hwnd:
        return None

    # ---- child controls ----
    user32.CreateWindowExW(
        0, "STATIC", prompt,
        WS_CHILD | WS_VISIBLE | SS_LEFT, 15, 12, 370, 20,
        hwnd, None, hinst, None,
    )
    edit_hwnd = user32.CreateWindowExW(
        0, "EDIT", initial,
        WS_CHILD | WS_VISIBLE | WS_BORDER | WS_TABSTOP | ES_AUTOHSCROLL | ES_LEFT,
        15, 36, 370, 22, hwnd, IDC_EDIT, hinst, None,
    )
    user32.CreateWindowExW(
        0, "BUTTON", "确定",
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_DEFPUSHBUTTON,
        230, 70, 75, 24, hwnd, IDOK, hinst, None,
    )
    user32.CreateWindowExW(
        0, "BUTTON", "取消",
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
        310, 70, 75, 24, hwnd, IDCANCEL, hinst, None,
    )

    # ---- focus & select all ----
    user32.SetFocus(edit_hwnd)
    user32.SendMessageW(edit_hwnd, EM_SETSEL, 0, -1)

    # ---- modal message loop ----
    msg = MSG()
    while True:
        ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if ret <= 0:  # WM_QUIT or error
            break
        if not user32.IsDialogMessageW(hwnd, ctypes.byref(msg)):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    return result[0]


def _exec_worker(node, peer, cmd):
    if not node.bus or not peer.get("agent_id"):
        return
    try:
        from agent_bus.schema import make_task_request

        req = make_task_request(
            node.agent_id, peer["agent_id"], instruction="", timeout_seconds=120
        )
        req["payload"] = {"op": "shell_exec", "cmd": cmd, "timeout_seconds": 120}
        result = node.bus.send_msg(peer["agent_id"], req, wait=True, wait_timeout=150)
        out = ""
        if result:
            out = result.get("result", {}).get("output_text", "") or result.get(
                "error", ""
            )
        _toast(node, f"远程 {peer.get('name')}", out[:300] or "(无输出)")
    except Exception as e:
        _toast(node, "远程命令失败", str(e))


def _toast(node, title, msg):
    """Windows 原生消息框，替代 tkinter messagebox。

    从后台线程调用也无问题，无需 tkinter 初始化。
    """
    import ctypes

    try:
        ctypes.windll.user32.MessageBoxW(
            None, msg or "(空)", title, 0x00000040,  # MB_OK | MB_ICONINFORMATION
        )
    except Exception:
        log.info("[%s] %s", title, msg)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(
        description="Agent Bus 控制节点（对称 + 托盘 + 内嵌 MCP）"
    )
    ap.add_argument("--role", choices=("worker", "hub"), default="worker")
    ap.add_argument("--agent-id", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--broker-port", type=int, default=0)
    ap.add_argument("--http-port", type=int, default=0)
    ap.add_argument("--discovery-port", type=int, default=0)
    ap.add_argument(
        "--broker-host", default="", help="连外部中心 broker 的地址（worker 用）"
    )
    ap.add_argument("--no-control", action="store_true", help="启动时关闭受控能力")
    ap.add_argument("--headless", action="store_true", help="不起托盘（后台/测试）")
    ap.add_argument(
        "--mcp", action="store_true", help="以 MCP Server 模式启动（stdin/stdout）"
    )
    args = ap.parse_args()

    # windowed exe（console=False）双击启动时无控制台，sys.stdout/stderr 为 None：
    # 不处理的话 logging 会崩。stderr 兜底写入 data/node.log 便于排查。
    # 注意：--mcp 由 MCP 客户端以管道启动时 stdout 是有效管道，此处不会动它。
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        try:
            sys.stderr = open(
                app_data_dir() / "node.log", "a", encoding="utf-8", buffering=1
            )
        except Exception:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )

    node = ControlNode(args)

    # ----- MCP 模式：独立启动，供智能体 JSON 配置调用 -----
    if args.mcp:
        node._select_ports()
        if node.role == "hub":
            node.start_center()  # 先起本地 broker/bus_server，再连总线
        node.connect(args.broker_host or None)
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("agent-bus-control")
        EmbeddedMCPServer(node).tools(mcp)
        log.info("MCP Server 就绪 agent=%s 传输=stdio", node.agent_id)
        mcp.run()
        node.shutdown()
        return

    # ----- GUI / headless 模式 -----
    node.run()
    if args.headless:
        log.info("headless 模式运行中")
        try:
            while not node._stop.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
    else:
        try:
            run_tray(node)
        except Exception as e:
            log.error("托盘异常: %s", e)
        except KeyboardInterrupt:
            pass
    node.shutdown()


if __name__ == "__main__":
    main()
