"""A2A v1.0 数据模型（2.16.1 / 2.16.3）。

- 复用 A2A 的任务语义与数据形状：camelCase 字段、JSON-RPC 2.0、ISO 8601 UTC 时间戳
- 不依赖 a2a-sdk：自研 TCP 传输只需数据类型，引入 SDK 全家桶（httpx/sse 等）反而
  增加 Python 3.14 目标机的安装风险；线格式契约（A.5）不变
- 方法集（自研 TCP binding 内使用）:
    message/send   提交任务（mode 随 params 携带，节点级语义，插件不感知 2.7.5）
    tasks/get      查询任务状态/结果（同步模式轮询）
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

# Task 生命周期状态（A2A TaskState）
TASK_SUBMITTED = "submitted"
TASK_WORKING = "working"
TASK_INPUT_REQUIRED = "input-required"
TASK_COMPLETED = "completed"
TASK_FAILED = "failed"
TASK_CANCELED = "canceled"

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_task_id() -> str:
    return str(uuid.uuid4())


def jsonrpc_request(method: str, params: dict | None = None, req_id: str | None = None) -> dict:
    return {"jsonrpc": "2.0", "id": req_id or str(uuid.uuid4()), "method": method,
            "params": params or {}}


def jsonrpc_result(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def jsonrpc_error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def make_task(task_id: str, agent_id: str, state: str = TASK_SUBMITTED,
              prompt: str | None = None) -> dict:
    task = {
        "id": task_id,
        "contextId": task_id,  # v1: 单任务即单上下文
        "status": {"state": state, "timestamp": now_iso()},
        "metadata": {"agentId": agent_id},
    }
    if prompt is not None:
        task["metadata"]["prompt"] = prompt
    return task


def make_user_message(prompt: str, attachments: list[str] | None = None) -> dict:
    parts = [{"kind": "text", "text": prompt}]
    for a in attachments or []:
        parts.append({"kind": "data", "mimeType": "application/octet-stream",
                      "path": a})  # 本地路径（2.16.3 附件：路径替代 URL）
    return {"role": "user", "parts": parts, "messageId": str(uuid.uuid4())}


def set_task_state(task: dict, state: str) -> dict:
    task["status"] = {"state": state, "timestamp": now_iso()}
    return task


def task_artifact_text(content: str) -> list[dict]:
    """任务结果 Artifacts（配合 2.2.11 结果文件契约）。"""
    return [{"name": "result", "parts": [{"kind": "text", "text": content}]}]
