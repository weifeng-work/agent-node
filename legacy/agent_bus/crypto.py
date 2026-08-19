"""控制面轻量身份检查（简化版，适用于高安全局域网 + 可信设备环境）。

在局域网全可信的前提下，控制消息不做 HMAC 验签，仅做 sender 身份检查：
- 控制消息只接受来自 hub-* 身份的 sender
- 消息明文走 MQTT，不做加密（局域网信任边界内）

本模块保留签名函数作为兼容 stub，实际验签改为 sender 身份白名单检查。
"""
import json


def canonical(obj) -> str:
    """确定性 JSON 序列化：键递归排序，去除空白。

    保留以兼容旧代码调用，但不再用于签名验签。
    """
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def is_hub_message(sender_id: str) -> bool:
    """检查控制消息是否来自 hub 身份。

    在可信局域网环境下，仅通过 sender_id 前缀判断。
    控制消息只接受 hub-* 发送的，防止非 hub 节点误操作。
    """
    return sender_id and sender_id.startswith("hub-")


def is_control_op(op: str) -> bool:
    """检查是否为控制面操作。"""
    return op in ("shell_exec", "executor_activate", "executor_deactivate", "upgrade")