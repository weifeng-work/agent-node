"""TraeWork CN GUI 执行器适配器（2.2.5 / 2.2.11 / 2.2.12）。

注入/回收核心复用 agent-bus（weifeng-work/agent-bus）已验证的 traework_executor：
  通过 CDP 把任务注入 TraeWork 桌面端并回收结果（无可用 CLI 的桌面智能体）。
  注入: 聚焦 composer(.chat-input-v2-input-box-editable) → Input.insertText 注入全文
        → 点发送按钮 button.chat-input-v2-send-button（回退 ENTER）→
        轮询到对话区出现含任务短码的 .user-message（防误判未提交）
  回收: 结果文件契约为主路（.tmp→rename 原子写，is_done=大小稳定）；
        DOM 观察为兜底（.turn__agent-message 实质回复连续稳定且无运行指示，
        或回复含"任务完成 <短码>"）。

前提（能力自检 + 前提披露，2.2.13）:
  - TraeWork CN 已安装、已登录、正在运行（执行器不冷启动 GUI，2.2.12）
  - ~/.trae-cn/argv.json 加入字符串值 "remote-debugging-port": "9433" 后重启
    （CDP 仅监听 127.0.0.1）
  - 依赖 websockets（TraeWork CDP 注入需要；缺失时本适配器不加载）

限制:
  - v1 任务进入 TraeWork 窗口当前打开的会话，不做会话切换
  - 会话正文在云端，本执行器不读其内部存储，只经 DOM/结果文件交互

节点接口不变：executor_type="gui"（concurrency=1 串行），结果经 result.md 契约 + DOM 兜底。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from ..base import (CapabilityResult, ExecutorPlugin, ResultPayload, SubmitResult,
                    TaskInput)

DEFAULT_CDP_PORT = 9433
POLL_INTERVAL = 3.0          # 回收轮询间隔（秒）
WINDOW_TITLE = "TraeWork CN"

# DOM 锚点（Trae SOLO-lite 聊天界面）
COMPOSER_SEL = ".chat-input-v2-input-box-editable"
SEND_BTN_SEL = "button.chat-input-v2-send-button"
USER_MSG_SEL = ".user-message"
AGENT_MSG_SEL = ".turn__agent-message"

PREMISES = [
    "TraeWork CN 已安装、已登录且正在运行（执行器不冷启动 GUI，2.2.12）",
    "~/.trae-cn/argv.json 已加入字符串值 \"remote-debugging-port\": \"9433\" 并重启 TraeWork（CDP 仅监听 127.0.0.1）",
    "目标机需 pip install websockets",
]

# DOM innerText 里的界面噪音行（agent 消息头部/工具摘要）
_CHROME_RE = re.compile(
    r"^(TraeWork|耗时.*|思考过程|正在.*|已执行\s*\d+.*|已创建.*|已修改.*|已发送.*|等待.*)$"
)
# 状态条文案 → 运行中（阻止"稳定即完成"判定）
_RUNNING_RE = re.compile(r"思考|生成|执行|搜索|读取|写入|分析|恢复|规划|调用|浏览|整理|正在")
# 状态条/图标 → 已停止或失败（且无正文时提前标记完成）
_STOPPED_RE = re.compile(r"手动停止|已停止|停止生成|失败|出错|中断|报错")


def _clean_reply(text: str) -> str:
    lines = [ln for ln in (text or "").splitlines()
             if ln.strip() and not _CHROME_RE.match(ln.strip())]
    return "\n".join(lines).strip()


class CdpClient:
    """最小化 CDP 客户端：后台 asyncio 线程 + 同步 facade（节点主循环是同步的）。

    目标由 /json 发现（workbench page target）；仅本机 127.0.0.1。
    断线自动重连一次。websockets 惰性导入——适配器加载时无需 web上依赖。
    """

    def __init__(self, port: int = DEFAULT_CDP_PORT):
        self.port = int(port)
        self.http_base = f"http://127.0.0.1:{self.port}"
        self._loop = None
        self._thread = None
        self._ws = None
        self._ws_url = None
        self._next_id = 0
        self._lock = threading.Lock()

    # ---- 生命周期 ----
    def start(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True,
                                        name="trae-cdp-loop")
        self._thread.start()

    def _ensure_loop(self) -> None:
        if self._loop is None:
            self.start()

    def close(self) -> None:
        if self._loop:
            try:
                asyncio.run_coroutine_threadsafe(self._close_ws(), self._loop).result(timeout=5)
            except Exception:
                pass
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass

    async def _close_ws(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None

    # ---- target 发现 / 健康 ----
    def find_ws_url(self) -> str:
        with urllib.request.urlopen(self.http_base + "/json", timeout=5) as r:
            targets = json.load(r)
        pages = [t for t in targets if t.get("type") == "page"]
        pick = None
        for t in pages:
            url = t.get("url", "")
            title = t.get("title", "")
            if url.startswith("vscode-file://") or WINDOW_TITLE.lower() in title.lower():
                pick = t
                break
        if pick is None and pages:
            pick = pages[0]
        if pick is None:
            raise RuntimeError("CDP 无可用 page target（TraeWork 是否已启动？）")
        return pick["webSocketDebuggerUrl"]

    def check_alive(self) -> bool:
        try:
            with urllib.request.urlopen(self.http_base + "/json/version", timeout=3) as r:
                json.load(r)
            return True
        except Exception:
            return False

    def probe_ready(self):
        """能力自检：CDP 可达且有可用 page target。(ok, reason)"""
        if not self.check_alive():
            return False, "TraeWork CDP 未就绪（未运行或未开 remote-debugging-port 9433）"
        try:
            self.find_ws_url()
            return True, None
        except Exception as e:
            return False, str(e)

    # ---- 同步 facade ----
    def call(self, method: str, params: dict | None = None, timeout: float = 30.0):
        with self._lock:
            self._ensure_loop()
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._call(method, params or {}), self._loop)
                return fut.result(timeout=timeout)
            except Exception:
                # 断线重连一次再试
                try:
                    asyncio.run_coroutine_threadsafe(self._close_ws(), self._loop).result(timeout=5)
                except Exception:
                    pass
                fut = asyncio.run_coroutine_threadsafe(
                    self._call(method, params or {}), self._loop)
                return fut.result(timeout=timeout)

    async def _call(self, method: str, params: dict):
        import websockets
        if self._ws is None:
            self._ws_url = self.find_ws_url()
            self._ws = await websockets.connect(self._ws_url, max_size=64 * 1024 * 1024)
        self._next_id += 1
        mid = self._next_id
        await self._ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            raw = await self._ws.recv()
            msg = json.loads(raw)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method} 错误: {msg['error']}")
                return msg.get("result", {})

    def evaluate(self, expr: str, timeout: float = 30.0):
        r = self.call("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True}, timeout=timeout)
        return r.get("result", {}).get("value")


class TraeWorkPlugin(ExecutorPlugin):
    plugin_id = "traework"
    display_name = "TraeWork CN"
    executor_type = "gui"
    concurrency = 1

    def __init__(self, ctx):
        super().__init__(ctx)
        port = int(self.ctx.config.get("cdp_port")
                   or os.environ.get("AGENT_NODE_TRAEWORK_CDP_PORT")
                   or DEFAULT_CDP_PORT)
        self.cdp = CdpClient(port)
        self._poll_state: dict[str, dict] = {}   # task_id -> {done, text}
        self._state_lock = threading.Lock()
        self._ops_lock = threading.Lock()        # CDP 注入/轮询互斥（防跨任务穿插）
        self._inflight = 0
        self._lock = threading.Lock()

    # ---------- 能力自检（2.2.12: GUI 以 CDP/DOM 探测） ----------
    def check_capability(self) -> CapabilityResult:
        ok, _ = self.cdp.probe_ready()
        if ok:
            return CapabilityResult(True, premises=PREMISES)
        if self._traework_running():
            return CapabilityResult(
                False, reason="TraeWork CN 已运行但 CDP 未启用——请确认 ~/.trae-cn/argv.json "
                              "含 remote-debugging-port 9433 并重启 TraeWork（CDP 监听 127.0.0.1）",
                premises=PREMISES)
        return CapabilityResult(False, reason="未检测到 TraeWork CN 运行/CDP（请先启动 TraeWork CN）",
                                premises=PREMISES)

    @staticmethod
    def _traework_running() -> bool:
        try:
            out = subprocess.run(
                ["tasklist", "/fo", "csv", "/nh"],
                capture_output=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            return any(b"trae" in ln.lower() for ln in out.splitlines())
        except Exception:
            return False

    # ---------- 任务提交 ----------
    def submit(self, task: TaskInput) -> SubmitResult:
        self.cdp._ensure_loop()
        # 运行期防御：装了对端但没开 CDP / 没运行 → 直接拒绝（2.2.12 语义）
        ok, _ = self.cdp.probe_ready()
        if not ok:
            return SubmitResult(
                False, "not_installed",
                "TraeWork CDP 未就绪（请启动 TraeWork CN 并开启 remote-debugging-port 9433）")
        task_tag = task.task_id[:8]
        result_path = str(task.result_file.resolve())
        prompt = self._build_prompt(task, task_tag, result_path)
        try:
            with self._ops_lock:
                self._inject_prompt(prompt, task_tag)
        except Exception as e:
            return SubmitResult(False, "agent_error", f"注入失败: {e}")
        with self._lock:
            self._inflight += 1
        threading.Thread(target=self._harvest_loop, args=(task,), daemon=True,
                         name=f"trae-harvest-{task_tag}").start()
        return SubmitResult(True)

    def _build_prompt(self, task: TaskInput, task_tag: str, result_path: str) -> str:
        prompt = (f"【跨智能体任务 {task_tag}】\n"
                  f"{task.prompt}\n\n"
                  f"【输出要求】任务完成后，请把最终结论（Markdown 格式）写入文件：\n"
                  f"{result_path}\n"
                  f"（UTF-8 编码；写入成功后在回复末尾写上\"任务完成 {task_tag}\"。）")
        if task.attachments:
            prompt += "\n【附件】" + "; ".join(str(a) for a in task.attachments)
        return prompt

    # ---------- CDP 注入三步 ----------
    def _inject_prompt(self, prompt: str, task_tag: str) -> None:
        cdp = self.cdp
        focused = cdp.evaluate(f"""
            (() => {{
                const el = document.querySelector({json.dumps(COMPOSER_SEL)});
                if (!el) return false;
                el.focus();
                return document.activeElement === el;
            }})()
        """)
        if not focused:
            raise RuntimeError(f"未找到/无法聚焦输入框 {COMPOSER_SEL}（TraeWork 窗口是否正常？）")
        # 全文注入（Input.insertText 走真实输入管线，无 argv 长度/换行截断问题）
        cdp.call("Input.insertText", {"text": prompt})
        time.sleep(0.8)
        # 提交：优先点发送按钮，回退 ENTER；带重试。
        # 安全铁律：点击后按钮会变成"停止生成"——绝不在已提交/生成中时再次点击。
        for attempt in range(3):
            # 若输入框已空，说明上一次点击其实已提交成功（用户消息 DOM 可能延迟出现）
            if not self._composer_text():
                if self._wait_tag_visible(task_tag, timeout=15):
                    return
                raise RuntimeError("输入框已空但对话区始终未出现任务消息（渲染异常？）")
            btn = cdp.evaluate(f"""
                (() => {{
                    const b = document.querySelector({json.dumps(SEND_BTN_SEL)});
                    if (!b) return null;
                    const r = b.getBoundingClientRect();
                    return {{x: r.left + r.width / 2, y: r.top + r.height / 2,
                             cls: String(b.className), disabled: b.disabled === true,
                             aria: b.getAttribute('aria-label') || ''}};
                }})()
            """)
            clicked = False
            if btn and not btn.get("disabled") and "voice-call-mode" not in btn.get("cls", ""):
                x, y = btn["x"], btn["y"]
                cdp.call("Input.dispatchMouseEvent",
                         {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
                cdp.call("Input.dispatchMouseEvent",
                         {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
                clicked = True
            else:
                for t in ("keyDown", "keyUp"):
                    cdp.call("Input.dispatchKeyEvent",
                             {"type": t, "key": "Enter", "code": "Enter",
                              "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
            if self._wait_tag_visible(task_tag, timeout=12):
                return
            # 重试前重新聚焦（文本可能已清空或仍在）
            cdp.evaluate(f"(() => {{ const el = document.querySelector({json.dumps(COMPOSER_SEL)});"
                         f" if (el) el.focus(); return true; }})()")
        raise RuntimeError("注入失败：3 次尝试均未在对话区发现含短码的用户消息")

    def _composer_text(self) -> str:
        return self.cdp.evaluate(f"""
            (() => {{
                const el = document.querySelector({json.dumps(COMPOSER_SEL)});
                return el ? (el.innerText || '').trim() : '';
            }})()
        """) or ""

    def _wait_tag_visible(self, task_tag: str, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._user_message_visible(task_tag):
                return True
            time.sleep(1.0)
        return False

    def _user_message_visible(self, task_tag: str) -> bool:
        return bool(self.cdp.evaluate(f"""
            Array.from(document.querySelectorAll({json.dumps(USER_MSG_SEL)}))
                 .some(u => (u.innerText || '').includes({json.dumps(task_tag)}))
        """))

    # ---------- DOM 兜底回收 ----------
    def _poll_state(self, task_tag: str) -> dict:
        raw = self.cdp.evaluate(f"""
            (() => {{
                const TAG = {json.dumps(task_tag)};
                const nodes = Array.from(document.querySelectorAll(
                    {json.dumps(USER_MSG_SEL + "," + AGENT_MSG_SEL)}));
                let anchor = -1;
                for (let i = 0; i < nodes.length; i++) {{
                    if (nodes[i].classList.contains('user-message')
                        && (nodes[i].innerText || '').includes(TAG)) anchor = i;
                }}
                const replies = [];
                if (anchor >= 0) {{
                    for (let i = anchor + 1; i < nodes.length; i++) {{
                        if (nodes[i].classList.contains('turn__agent-message'))
                            replies.push(nodes[i].innerText || '');
                    }}
                }}
                const send = document.querySelector({json.dumps(SEND_BTN_SEL)});
                let bar_text = '', icon_cls = '';
                const turns = Array.from(document.querySelectorAll({json.dumps(AGENT_MSG_SEL)}));
                if (turns.length) {{
                    const bar = turns[turns.length-1].querySelector('[class*="latest-assistant-bar"]');
                    if (bar) bar_text = (bar.innerText || '').trim();
                    const icon = turns[turns.length-1].querySelector('[class*="status-icon"]');
                    if (icon) icon_cls = String(icon.className);
                }}
                const indicators = Array.from(document.querySelectorAll(
                    '[class*="stop" i],[class*="generating" i],[class*="thinking" i],'
                    '[class*="loading" i]'
                )).slice(0, 6).map(e => String(e.className).slice(0, 80));
                return {{anchor: anchor >= 0, replies: replies,
                         send_cls: send ? String(send.className) : '',
                         bar_text: bar_text, icon_cls: icon_cls, indicators: indicators}};
            }})()
        """)
        return raw if isinstance(raw, dict) else {}

    def _run_harvest(self, task: TaskInput) -> None:
        task_tag = task.task_id[:8]
        deadline = time.time() + task.timeout
        last_reply, stable = "", 0
        last_fsize, fstable = -1, 0
        with self._state_lock:
            self._poll_state[task.task_id] = {"done": False, "text": ""}
        while time.time() < deadline:
            time.sleep(POLL_INTERVAL)
            # A) 结果文件契约（主路，最可靠）
            try:
                if task.result_file.exists():
                    sz = task.result_file.stat().st_size
                    if sz == last_fsize and sz > 0:
                        fstable += 1
                    else:
                        fstable = 0
                    last_fsize = sz
                    if fstable >= 2:
                        with self._state_lock:
                            self._poll_state[task.task_id]["done"] = True
                        return
            except OSError:
                pass
            # B) DOM 观察（兜底）
            with self._ops_lock:
                try:
                    st = self._poll_state(task_tag)
                except Exception:
                    continue
            if not st.get("anchor"):
                continue
            replies = _clean_reply("\n".join(st.get("replies") or []))
            done_marker = f"任务完成 {task_tag}" in replies
            running = bool(_RUNNING_RE.search(st.get("bar_text") or ""))
            stopped = bool(_STOPPED_RE.search(st.get("bar_text") or "")) or \
                      bool(_STOPPED_RE.search(st.get("icon_cls") or ""))
            if replies:
                if replies == last_reply:
                    stable += 1
                else:
                    stable = 0
                last_reply = replies
                if done_marker or (len(replies) > 20 and stable >= 2 and not running):
                    with self._state_lock:
                        self._poll_state[task.task_id] = {"done": True, "text": replies}
                    return
            elif stopped:
                # 已停止且无正文 → 提前标记完成（get_result 兜底给提示）
                with self._state_lock:
                    self._poll_state[task.task_id] = {"done": True, "text": ""}
                return
        # 超时：保留已观察文本（get_result 兜底可用）
        with self._state_lock:
            self._poll_state[task.task_id] = {"done": True, "text": last_reply}

    def _harvest_loop(self, task: TaskInput) -> None:
        try:
            self._run_harvest(task)
        finally:
            with self._lock:
                self._inflight = max(0, self._inflight - 1)

    # ---------- is_done / get_result ----------
    def is_done(self, task_id: str) -> bool:
        # 主路: 结果文件（注册表叠加大小稳定性）；兜底: DOM 完成信号
        if super().is_done(task_id):
            return True
        with self._state_lock:
            st = self._poll_state.get(task_id)
            return bool(st and st.get("done"))

    def get_result(self, task_id: str) -> ResultPayload:
        file_result = super().get_result(task_id)
        if file_result.ok:
            return file_result
        with self._state_lock:
            st = self._poll_state.get(task_id) or {}
        if st.get("text"):
            return ResultPayload(True, None, st["text"])
        return ResultPayload(False, "agent_error",
                             f"结果文件未生成且未回收到回复文本: {file_result.content or ''}")

    # ---------- 状态 ----------
    def status(self):
        from ..base import ExecutorStatus
        busy = self._inflight > 0
        return ExecutorStatus(available=True, state="busy" if busy else "idle",
                              inflight=self._inflight, concurrency=self.concurrency,
                              current_task=None, queue_len=0)