"""Syncthing 同步引擎托管（2.4.7）。

- Syncthing 作为节点子进程（静态二进制于 data/syncthing/syncthing(.exe)）
- 经 REST API（127.0.0.1:8384，X-API-Key）编程控制：文件夹配置/触发扫描/设备互配
- 设备配对引导: device ID 经 beacon 交换（sync_device_id），同 team 在线对端自动互配
  （双方对称互加设备 + 共享 data/sync/ 文件夹）
- 许可合规: 独立子进程 + REST 控制（MPL-2.0 弱 copyleft，非衍生作品）
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

FOLDER_ID = "agent-node-sync"
REST_BASE = "http://127.0.0.1:8384"
START_TIMEOUT = 60


class SyncManager:
    def __init__(self, node_core):
        self.node_core = node_core
        self.data_dir = Path(node_core.data_dir)
        self.home = self.data_dir / "syncthing"
        self.sync_dir = self.data_dir / "sync"
        self.bin = self.home / ("syncthing.exe" if os.name == "nt" else "syncthing")
        self.api_key = ""
        self.device_id: str | None = None
        self._proc: subprocess.Popen | None = None
        self._paired: set[str] = set()
        self._lock = threading.Lock()

    # ---------- REST ----------
    def _rest(self, method: str, path: str, body: dict | None = None,
              timeout: float = 10.0):
        url = REST_BASE + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("X-API-Key", self.api_key)
        if data:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}

    # ---------- 启停 ----------
    def start(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.sync_dir.mkdir(parents=True, exist_ok=True)
        if not self.bin.is_file():
            self.node_core.log("warning",
                               f"同步引擎未启用: 缺少二进制 {self.bin}（2.4.7 随包分发）")
            return
        # API key（固定生成一次，写 home/.apikey）
        key_file = self.home / ".apikey"
        if key_file.exists():
            self.api_key = key_file.read_text().strip()
        else:
            import uuid
            self.api_key = uuid.uuid4().hex
            key_file.write_text(self.api_key)
        env = dict(os.environ)
        env["STGUIAPIKEY"] = self.api_key
        env["STNORESTART"] = "1"
        self._proc = subprocess.Popen(
            [str(self.bin), "home", str(self.home), "-no-browser", "-no-restart"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=env)
        # 等 REST 就绪 + 读取 device ID
        deadline = time.time() + START_TIMEOUT
        while time.time() < deadline:
            try:
                st = self._rest("GET", "/rest/system/status")
                self.device_id = st.get("myID")
                if self.device_id:
                    break
            except Exception:
                time.sleep(1.5)
        if not self.device_id:
            self.node_core.log("warning", "同步引擎启动超时（REST 未就绪）")
            return
        self._configure()
        self.node_core.log("info", f"同步引擎就绪: device={self.device_id[:8]}... "
                                   f"folder={self.sync_dir}")

    def _configure(self) -> None:
        try:
            opts = self._rest("GET", "/rest/config/options")
            opts.update({
                "globalAnnounceEnabled": False,   # 纯局域网（不做全局发现）
                "localAnnounceEnabled": True,
                "relaysEnabled": False,
                "natEnabled": False,
                "urAccepted": -1,
            })
            self._rest("PUT", "/rest/config/options", opts)
            # 统一同步文件夹 data/sync/（默认开启，2.4.7）
            folders = self._rest("GET", "/rest/config/folders")
            if not any(f.get("id") == FOLDER_ID for f in folders):
                self._rest("PUT", "/rest/config/folders", {
                    "id": FOLDER_ID, "label": "agent-node sync",
                    "path": str(self.sync_dir), "type": "sendreceive",
                    "rescanIntervalS": 3600, "fsWatcherEnabled": True,
                    "devices": [{"deviceID": self.device_id}],
                })
        except Exception as e:
            self.node_core.log("warning", f"同步引擎配置失败: {e}")

    def stop(self) -> None:
        try:
            if self._proc:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except Exception:
                    self._proc.kill()
        except Exception:
            pass

    # ---------- 自动互配（2.4.7 设备配对引导） ----------
    def pair_peer(self, beacon_payload: dict) -> None:
        """收到同 team 对端 beacon → 自动互加设备 + 共享文件夹（对称执行双方互加即连通）。"""
        dev = beacon_payload.get("sync_device_id") or ""
        node_id = beacon_payload.get("node_id") or ""
        if not dev or not self.device_id or dev == self.device_id:
            return
        with self._lock:
            if dev in self._paired:
                return
            self._paired.add(dev)
        try:
            devices = self._rest("GET", "/rest/config/devices")
            if not any(d.get("deviceID") == dev for d in devices):
                self._rest("PUT", "/rest/config/devices", {
                    "deviceID": dev, "name": node_id or dev[:8],
                    "addresses": ["dynamic"], "autoAcceptFolders": False,
                })
                self.node_core.log("info", f"同步互配: 已添加设备 {node_id} ({dev[:8]}...)")
            # 共享文件夹给该设备
            folders = self._rest("GET", "/rest/config/folders")
            for f in folders:
                if f.get("id") == FOLDER_ID:
                    devs = f.get("devices") or []
                    if not any(d.get("deviceID") == dev for d in devs):
                        devs.append({"deviceID": dev})
                        f["devices"] = devs
                        self._rest("PUT", f"/rest/config/folders", f)
                    break
        except Exception as e:
            self.node_core.log("warning", f"同步互配失败 {node_id}: {e}")
            with self._lock:
                self._paired.discard(dev)

    def reteam(self, new_team_id: str) -> None:
        """切换 team 联动（2.1.7）：移除不再是同 team 的设备共享（简化：清空全部互配）。"""
        with self._lock:
            self._paired.clear()
        try:
            folders = self._rest("GET", "/rest/config/folders")
            for f in folders:
                if f.get("id") == FOLDER_ID:
                    f["devices"] = [{"deviceID": self.device_id}]
                    self._rest("PUT", "/rest/config/folders", f)
                    break
        except Exception:
            pass

    # ---------- 手动触发（MCP sync_now / cli.py sync） ----------
    def sync_now(self) -> dict:
        if not self.device_id:
            return {"ok": False, "error": "not_installed", "detail": "同步引擎未运行"}
        try:
            self._rest("POST", f"/rest/db/scan?folder={FOLDER_ID}")
            return {"ok": True, "detail": f"已触发 {self.sync_dir} 扫描同步"}
        except Exception as e:
            return {"ok": False, "error": "agent_error", "detail": str(e)}
