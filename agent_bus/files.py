"""HTTP 文件服务客户端（Claim-Check 上传/下载）与在线名单查询。

v2 匿名化（git 4c805e7）后服务端全匿名，无需认证头；_auth_kwargs 保留
token 参数仅为兼容恢复认证模式或携带环境变量令牌的场景（token 为空即不带）。"""

import os
import urllib.parse

import requests


def _auth_kwargs(token: str) -> dict:
    return {"headers": {"Authorization": f"Bearer {token}"}} if token else {}


def upload_file(
    path: str, http_base: str, uploaded_by: str = "", token: str = ""
) -> dict:
    """上传文件，返回 {file_id, url, size}（name 需自行用 os.path.basename(path)）。"""
    url = f"{http_base}/api/files/upload"
    with open(path, "rb") as f:
        resp = requests.post(
            url,
            files={"file": (os.path.basename(path), f)},
            params={"uploaded_by": uploaded_by},
            timeout=60,
            **_auth_kwargs(token),
        )
    resp.raise_for_status()
    return resp.json()


def download_file(
    url_or_id: str, dest: str, http_base: str = "", token: str = ""
) -> str:
    """下载文件到 dest。url_or_id 可以是完整 URL、相对路径(/api/files/xxx)或 file_id。

    Bug 23：失败时清理半下载文件，避免 inbox/磁盘残留残破文件。
    """
    if url_or_id.startswith("http://") or url_or_id.startswith("https://"):
        url = url_or_id
    elif url_or_id.startswith("/"):
        # 相对路径（服务端 BUS_HTTP_BASE 未设置时返回 /api/files/xxx）
        base = http_base or os.environ.get("BUS_HTTP_BASE", "http://127.0.0.1:8000")
        url = f"{base.rstrip('/')}{url_or_id}"
    else:
        base = http_base or os.environ.get("BUS_HTTP_BASE", "http://127.0.0.1:8000")
        url = f"{base.rstrip('/')}/api/files/{urllib.parse.quote(url_or_id)}"
    # Bug 23：先写临时文件，成功才 rename 到 dest，失败清理临时文件
    dest_path = os.path.abspath(dest)
    tmp_path = dest_path + ".part"
    try:
        with requests.get(url, stream=True, timeout=120, **_auth_kwargs(token)) as r:
            r.raise_for_status()
            with open(tmp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    f.write(chunk)
        os.replace(tmp_path, dest_path)
        return dest
    except Exception:
        # 失败清理半下载临时文件
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def list_agents_http(http_base: str, token: str = "") -> list:
    """查询已注册 Agent 名单（含在线状态）。"""
    resp = requests.get(f"{http_base}/api/agents", timeout=10, **_auth_kwargs(token))
    resp.raise_for_status()
    return resp.json()
