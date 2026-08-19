"""集中配置：全部可由环境变量覆盖，方便脚本化与跨机部署。

环境变量:
  BUS_BROKER_HOST  MQTT Broker 地址（默认 127.0.0.1）
  BUS_BROKER_PORT  MQTT 端口（默认 1883）
  BUS_HTTP_BASE    中间架构 HTTP 基址（默认 http://127.0.0.1:8000）
  BUS_AGENT_ID     默认本节点 agent_id
  BUS_MQTT_USER    MQTT 用户名（可选；v2 匿名化后 broker allow_anonymous，一般留空）
  BUS_MQTT_PASS    MQTT 密码（可选；仅恢复认证模式时由 scripts/add_node.py 发放）
  BUS_HTTP_TOKEN   HTTP 令牌（可选；v2 匿名化后面板/API 全匿名，一般留空）
"""
import os


class BusConfig:
    def __init__(self, broker_host=None, broker_port=None, http_base=None, agent_id=None,
                 mqtt_user=None, mqtt_pass=None, http_token=None):
        self.broker_host = broker_host or os.environ.get("BUS_BROKER_HOST", "127.0.0.1")
        self.broker_port = int(broker_port or os.environ.get("BUS_BROKER_PORT", "1883"))
        self.http_base = (http_base or os.environ.get("BUS_HTTP_BASE", "http://127.0.0.1:8000")).rstrip("/")
        self.agent_id = agent_id or os.environ.get("BUS_AGENT_ID", "")
        self.mqtt_user = mqtt_user or os.environ.get("BUS_MQTT_USER", "")
        self.mqtt_pass = mqtt_pass if mqtt_pass is not None else os.environ.get("BUS_MQTT_PASS", "")
        self.http_token = http_token or os.environ.get("BUS_HTTP_TOKEN", "")

    @classmethod
    def load(cls, **overrides) -> "BusConfig":
        clean = {k: v for k, v in overrides.items() if v is not None}
        return cls(**clean)
