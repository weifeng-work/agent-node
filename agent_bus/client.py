"""AgentBus 客户端：MQTT 收发 + 注册/心跳/遗嘱 + 同步等待结果。

状态文件上报（供托盘壳/通信节点判定真实 bus 状态，v0.4）:
  通过 status_file 参数或环境变量 BUS_STATUS_FILE 指定路径；
  在真实 MQTT 事件写入 {"status","agent_id","health","ts"}:
    connected    连接成功
    reconnecting 连接断开（on_disconnect）
    stopped      主动断开（disconnect()）
  心跳循环每 30s 刷新 ts（新鲜度信号）。
  执行器零改动：通信节点拉起子进程时设置 BUS_STATUS_FILE 即可。
"""

import json
import logging
import os
import platform as _platform
import queue
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt

from .config import BusConfig
from .schema import (
    inbox_topic,
    make_register,
    make_task_request,
    make_task_result,
    validate,
)
from . import files as _files

log = logging.getLogger("agent_bus")

HEARTBEAT_INTERVAL = 30.0


class AgentBus:
    """一个 Agent 节点的总线连接。

    用法:
        bus = AgentBus("agent_alpha", name="Alpha@PC")
        bus.connect()                      # 注册 + 心跳 + 订阅自己收件箱
        result = bus.send_task("codebuddy_pc1", "分析 a.py 的 bug", wait=True)
        for msg in bus.poll_inbox(timeout=5):
            ...
    """

    def __init__(
        self,
        agent_id: str,
        name: str = "",
        capabilities=None,
        executor: str = "",
        config: BusConfig = None,
        status_file: str = "",
    ):
        self.agent_id = agent_id
        self.name = name or agent_id
        self.capabilities = capabilities or []
        self.executor = executor
        self.health = "unknown"  # ok | auth_required | unknown（CLI 登录态）
        self.cfg = config or BusConfig.load()
        self.status_file = status_file or os.environ.get("BUS_STATUS_FILE", "")
        self.on_message = None  # 可选回调: fn(msg_dict) -> bool（True=已消费，不入 inbox）
        self._inbox: "queue.Queue[dict]" = queue.Queue()
        self._pending: dict = {}  # correlation_id -> {"event", "result"}
        self._pending_lock = threading.Lock()
        self._hb_thread = None
        self._disconnected = False  # B3：标记已主动断开，心跳线程停止刷状态
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"bus-{agent_id}-{int(time.time())}",
        )
        if self.cfg.mqtt_user:
            self._client.username_pw_set(self.cfg.mqtt_user, self.cfg.mqtt_pass)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._connected = threading.Event()
        # 遗嘱：异常掉线时由 Broker 代发，服务端标记离线
        self._client.will_set(
            f"bus/offline/{self.agent_id}",
            json.dumps(
                {"type": "offline", "agent_id": self.agent_id, "ts": time.time()}
            ),
            qos=1,
        )

    # ---------- 状态文件上报（托盘壳/通信节点读） ----------

    def _write_status(self, status: str):
        """写真实 bus 状态到状态文件；原子写（临时文件+rename），失败静默。"""
        if not self.status_file:
            return
        try:
            payload = {
                "status": status,
                "agent_id": self.agent_id,
                "health": self.health,
                "ts": time.time(),
            }
            path = Path(self.status_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            content = json.dumps(payload, ensure_ascii=False)
            # 原子写：临时文件 + rename（B5）
            import tempfile

            fd, tmp_path = tempfile.mkstemp(
                suffix=".tmp", prefix="status_", dir=str(path.parent)
            )
            try:
                os.write(fd, content.encode("utf-8"))
                os.close(fd)
                fd = None
                os.replace(tmp_path, str(path))
            except Exception:
                if fd is not None:
                    os.close(fd)
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
        except Exception:
            log.debug("状态文件写入失败: %s", self.status_file)

    # ---------- 连接管理 ----------

    def connect(self, register: bool = True, timeout: float = 10.0):
        self._client.connect(self.cfg.broker_host, self.cfg.broker_port, keepalive=60)
        self._client.loop_start()
        if not self._connected.wait(timeout):
            raise ConnectionError(
                f"无法连接 MQTT Broker {self.cfg.broker_host}:{self.cfg.broker_port}"
            )
        if register:
            self.register()
            self._hb_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
            self._hb_thread.start()
        return self

    def disconnect(self):
        self._disconnected = True  # B3：标记已断开，心跳线程不再刷状态
        # B7：先 join 心跳线程（带超时），避免其在 stopped 之后补写 connected 造成闪回
        hb = self._hb_thread
        if hb and hb is not threading.current_thread():
            hb.join(timeout=2.0)
        self._write_status("stopped")
        try:
            self._client.disconnect()
            self._client.loop_stop()
        except Exception:
            pass

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            client.subscribe(inbox_topic(self.agent_id), qos=1)
            self._connected.set()
            self._write_status("connected")
            log.info(
                "[%s] 已连接总线, 订阅 %s", self.agent_id, inbox_topic(self.agent_id)
            )
        else:
            log.error("[%s] 连接失败: %s", self.agent_id, reason_code)

    def _on_disconnect(
        self, client, userdata, disconnect_flags, reason_code, properties=None
    ):
        # B7：主动断开（disconnect() 已置标志并写 stopped）时，
        # 不覆盖为 reconnecting，避免状态文件闪回
        if self._disconnected:
            return
        self._write_status("reconnecting")
        log.warning("[%s] 连接断开 reason=%s，重连中", self.agent_id, reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            log.warning("丢弃无法解析的消息: topic=%s", msg.topic)
            return
        errors = validate(data)
        if errors:
            log.warning("丢弃非法报文 %s: %s", msg.topic, errors)
            return
        # 结果报文优先匹配等待者
        if data["type"] == "task_result":
            corr = data.get("correlation_id")
            with self._pending_lock:
                slot = self._pending.get(corr)
            if slot:
                slot["result"] = data
                slot["event"].set()
                return
        # B8：on_message 返回 True 表示该消息已被消费，不再入 _inbox，
        # 避免 MCP check_inbox 重复返回已处理消息
        if self.on_message:
            consumed = False
            try:
                consumed = bool(self.on_message(data))
            except Exception:
                log.exception("on_message 回调异常")
            if consumed:
                return
        self._inbox.put(data)

    def _heartbeat_loop(self):
        topic = f"bus/heartbeat/{self.agent_id}"
        while not self._disconnected:
            self._client.publish(
                topic,
                json.dumps(
                    {
                        "agent_id": self.agent_id,
                        "ts": time.time(),
                        "health": self.health,
                    }
                ),
                qos=1,
            )
            # B7：publish 可能阻塞，期间 disconnect() 可能已置标志——
            # 写 connected 前二次检查，杜绝 stopped 之后闪回 connected
            if self._disconnected:
                break
            self._write_status("connected")  # 刷新状态文件 ts（新鲜度信号）
            time.sleep(HEARTBEAT_INTERVAL)

    # ---------- 注册 ----------

    def register(self):
        msg = make_register(
            self.agent_id,
            self.name,
            capabilities=self.capabilities,
            platform=_platform.system().lower(),
            executor=self.executor,
            hostname=_platform.node(),
            health=self.health,
        )
        self._client.publish("bus/register", json.dumps(msg), qos=1, retain=True)
        log.info("[%s] 已注册: %s", self.agent_id, self.name)

    def set_health(self, state: str):
        """更新 CLI 健康态并立即推送心跳（服务端据此刷新面板徽章）。"""
        if state not in ("ok", "auth_required", "unknown"):
            log.warning("[%s] 忽略非法 health 状态: %s", self.agent_id, state)
            return
        if state == self.health:
            return
        self.health = state
        topic = f"bus/heartbeat/{self.agent_id}"
        self._client.publish(
            topic,
            json.dumps({"agent_id": self.agent_id, "ts": time.time(), "health": state}),
            qos=1,
        )
        log.info("[%s] CLI 健康态 -> %s", self.agent_id, state)

    # ---------- 任务 ----------

    def send_task(
        self,
        target_id: str,
        instruction: str,
        context_data=None,
        attachments: list = None,
        session_id: str = None,
        timeout_seconds: int = 600,
        wait: bool = True,
        wait_timeout: float = None,
    ):
        """发送任务。wait=True 时阻塞等待对方回传的 task_result（dict），超时返回 None。"""
        req = make_task_request(
            self.agent_id,
            target_id,
            instruction,
            context_data=context_data,
            attachment_urls=attachments or [],
            session_id=session_id,
            timeout_seconds=timeout_seconds,
        )
        if not wait:
            self._client.publish(inbox_topic(target_id), json.dumps(req), qos=1)
            return req

        slot = {"event": threading.Event(), "result": None}
        with self._pending_lock:
            self._pending[req["correlation_id"]] = slot
        self._client.publish(inbox_topic(target_id), json.dumps(req), qos=1)
        log.info(
            "[%s] 任务已发送 %s -> %s", self.agent_id, req["task_id"][:8], target_id
        )
        ok = slot["event"].wait(wait_timeout or timeout_seconds + 30)
        with self._pending_lock:
            self._pending.pop(req["correlation_id"], None)
        if not ok:
            log.warning("[%s] 等待结果超时 task=%s", self.agent_id, req["task_id"])
            return None
        return slot["result"]

    def send_msg(
        self, target_id: str, msg: dict, wait: bool = True, wait_timeout: float = None
    ):
        """发送自定义消息到目标 inbox（控制面/特殊 op 用）。

        msg 须为合法报文（含 protocol_version/type/sender_id/correlation_id）。
        wait=True 时阻塞等待匹配 correlation_id 的 task_result。
        """
        if not wait:
            self._client.publish(inbox_topic(target_id), json.dumps(msg), qos=1)
            return msg
        corr = msg.get("correlation_id")
        if not corr:
            raise ValueError("wait=True 的消息必须带 correlation_id")
        slot = {"event": threading.Event(), "result": None}
        with self._pending_lock:
            self._pending[corr] = slot
        self._client.publish(inbox_topic(target_id), json.dumps(msg), qos=1)
        log.info(
            "[%s] 消息已发送 %s -> %s",
            self.agent_id,
            msg.get("task_id", "")[:8],
            target_id,
        )
        ok = slot["event"].wait(wait_timeout or 120)
        with self._pending_lock:
            self._pending.pop(corr, None)
        if not ok:
            log.warning(
                "[%s] 等待回执超时 task=%s", self.agent_id, msg.get("task_id", "")
            )
            return None
        return slot["result"]

    def reply_task(
        self,
        request: dict,
        output_text: str,
        status: str = "success",
        error=None,
        artifacts=None,
        session_id: str = None,
    ) -> bool:
        """把执行结果回传给任务发起方。"""
        res = make_task_result(
            self.agent_id,
            request,
            output_text=output_text,
            status=status,
            error=error,
            artifacts=artifacts or [],
            session_id=session_id,
        )
        reply_to = request.get("reply_to") or inbox_topic(request.get("sender_id", ""))
        info = self._client.publish(reply_to, json.dumps(res), qos=1)
        info.wait_for_publish(timeout=10)
        log.info(
            "[%s] 结果已回传 %s status=%s", self.agent_id, res["task_id"][:8], status
        )
        return True

    def poll_inbox(self, timeout: float = 0.0) -> list:
        """取出当前收件箱中的消息（非阻塞或阻塞 timeout 秒取到第一条）。"""
        first = _queue_get(self._inbox, timeout)
        if first is None:
            return []
        msgs = [first]
        while True:
            m = _queue_get(self._inbox, 0)
            if m is None:
                break
            msgs.append(m)
        return msgs

    # ---------- 便捷封装（HTTP） ----------

    def list_agents(self) -> list:
        return _files.list_agents_http(self.cfg.http_base, token=self.cfg.http_token)

    def upload(self, path: str) -> dict:
        return _files.upload_file(
            path,
            self.cfg.http_base,
            uploaded_by=self.agent_id,
            token=self.cfg.http_token,
        )

    def download(self, url: str, dest: str) -> str:
        return _files.download_file(
            url, dest, self.cfg.http_base, token=self.cfg.http_token
        )


def _queue_get(q: queue.Queue, timeout: float):
    if timeout and timeout > 0:
        try:
            return q.get(timeout=timeout)
        except queue.Empty:
            return None
    try:
        return q.get_nowait()
    except queue.Empty:
        return None
