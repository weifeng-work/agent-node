"""自研 P2P TCP 线级协议 —— 《重构设计方案.md》附录 A（施工硬契约）。

帧格式（A.2，全部 TCP 数据统一二进制帧）:
    [1 字节 frame_type][4 字节大端长度 N][N 字节载荷]
    0x01  JSON 控制帧: UTF-8 JSON 信封（A.3）
    0x02  文件块帧:    16 字节 file_transfer_id + 4 字节大端 seq + 原始文件字节

单帧载荷上限 8 MiB；文件数据按块（默认 1 MiB）分帧。
"""
from __future__ import annotations

import hashlib
import json
import struct
import uuid

PROTOCOL_VERSION = 1

FRAME_JSON = 0x01
FRAME_FILE = 0x02

MAX_FRAME = 8 * 1024 * 1024      # A.2: 单帧载荷上限 8 MiB
FILE_CHUNK = 1024 * 1024         # 默认块 1 MiB

# ---- 信封 type 常量（A.3） ----
T_TEXT_REQUEST = "text_request"
T_SHELL_EXEC = "shell_exec"
T_SHELL_RESULT = "shell_result"
T_FILE_PUSH = "file_push"
T_FILE_PULL = "file_pull"
T_FILE_LIST = "file_list"
T_FILE_LIST_RESULT = "file_list_result"
T_FILE_ACK = "file_ack"
T_FILE_RESULT = "file_result"
T_PLUGIN_SYNC = "plugin_sync"
T_PLUGIN_SYNC_RESULT = "plugin_sync_result"
T_EXECUTOR_STATUS = "executor_status"
T_EXECUTOR_STATUS_RESULT = "executor_status_result"
T_A2A_REQUEST = "a2a_request"
T_A2A_RESPONSE = "a2a_response"
T_PING = "ping"
T_PONG = "pong"
T_ERROR = "error"

# 响应类信封（按 correlation_id 匹配 pending 调用）
RESPONSE_TYPES = {
    T_SHELL_RESULT, T_FILE_ACK, T_FILE_RESULT, T_FILE_LIST_RESULT,
    T_PLUGIN_SYNC_RESULT, T_EXECUTOR_STATUS_RESULT, T_A2A_RESPONSE,
    T_PONG, T_ERROR,
}

# ---- 错误码（2.13.5 + A.6） ----
E_OFFLINE = "offline"
E_DISABLED = "disabled"
E_NOT_INSTALLED = "not_installed"
E_BUSY = "busy"
E_SUSPENDED = "suspended"
E_TIMEOUT = "timeout"
E_AGENT_ERROR = "agent_error"
E_UNSUPPORTED_TYPE = "unsupported_type"
E_BAD_FRAME = "bad_frame"

_HEADER = struct.Struct(">BI")        # frame_type + payload 长度（大端）
_BLOCK_HEAD = struct.Struct(">16sI")  # file_transfer_id + seq（大端）


def new_msg_id() -> str:
    return str(uuid.uuid4())


def make_envelope(msg_type: str, sender_node_id: str, target_node_id: str,
                  payload: dict | None = None, correlation_id: str | None = None,
                  msg_id: str | None = None) -> dict:
    """统一 JSON 信封（A.3）。correlation_id = 请求方 msg_id（响应）或 A2A task_id（异步回执）。"""
    return {
        "v": PROTOCOL_VERSION,
        "msg_id": msg_id or new_msg_id(),
        "type": msg_type,
        "sender_node_id": sender_node_id,
        "target_node_id": target_node_id,
        "correlation_id": correlation_id,
        "payload": payload or {},
    }


def encode_frame(frame_type: int, payload: bytes) -> bytes:
    if len(payload) > MAX_FRAME:
        raise ValueError(f"frame too large: {len(payload)} > {MAX_FRAME}")
    return _HEADER.pack(frame_type, len(payload)) + payload


def send_frame(sock, frame_type: int, payload: bytes) -> None:
    sock.sendall(encode_frame(frame_type, payload))


def recv_exact(sock, n: int) -> bytes | None:
    """读满 n 字节；连接关闭返回 None。"""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def read_frame(sock):
    """读一帧；返回 (frame_type, payload)；连接关闭或长度非法返回 None。"""
    head = recv_exact(sock, _HEADER.size)
    if head is None:
        return None
    ftype, n = _HEADER.unpack(head)
    if n > MAX_FRAME:
        return None
    payload = recv_exact(sock, n) if n else b""
    if payload is None:
        return None
    return ftype, payload


def pack_file_block(ftid: bytes, seq: int, data: bytes) -> bytes:
    return _BLOCK_HEAD.pack(ftid, seq) + data


def unpack_file_block(payload: bytes):
    """返回 (ftid, seq, data)；载荷过短返回 None。"""
    if len(payload) < _BLOCK_HEAD.size:
        return None
    ftid, seq = _BLOCK_HEAD.unpack_from(payload, 0)
    return ftid, seq, payload[_BLOCK_HEAD.size:]


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def envelope_to_bytes(env: dict) -> bytes:
    return json.dumps(env, ensure_ascii=False).encode("utf-8")


def envelope_from_bytes(data: bytes) -> dict:
    return json.loads(data.decode("utf-8"))
