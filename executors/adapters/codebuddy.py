"""CodeBuddy CLI 适配器（2.2.9 双形态：非交互 fresh + 交互式 TUI 常驻）。

非交互（codebuddy-cli，non_interactive_cli，并发 3）:
  每任务在 psmux 独立会话中拉起 `codebuddy -p --output-format json -y`
  （prompt 经 stdin——argv 多行截断坑；stdout 重定向文件——>64KB 管道截断坑），
  attach_visible 弹窗可见执行过程与最终输出；解析 result 事件写 result.md。

交互式（codebuddy-tui，interactive_tui，并发 1 串行）:
  psmux 常驻 TUI 会话（跨任务复用）+ attach_visible 可见窗口，
  send-keys 注入单行任务信封，轮询 result.md（主路）/屏幕稳定（兜底）。

2.2.14 CLI 授权/登录处理:
  - 权限确认: -y/--dangerously-skip-permissions 跳过（安全局域网自用模型）
  - trust 对话框: 启动状态机自动按键通过
  - 登录态: 输出命中认证标记 → 明确 auth_required 指引（窗口已可见，人类可登录）
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

from ..base import (CapabilityResult, ExecutorPlugin, ResultPayload, SubmitResult,
                    TaskInput)
from ..psmux_transport import PsmuxTransport

# 未登录特征（小写匹配；agent-bus 实战清单）
_AUTH_MARKERS = (
    "未登录", "请先登录", "请登录", "登录后重试", "登录已过期",
    "not logged in", "please log in", "please sign in", "sign in required",
    "login required", "unauthorized", "forbidden",
    "authentication required", "not authenticated",
    "token expired", "invalid token", "no valid token",
)

AUTH_HINT = ("CodeBuddy CLI 未登录。请到该机器查看执行器弹窗/终端，运行 codebuddy "
             "（交互模式）完成登录后重试；执行器会自动检测，无需重启节点。")

PREMISES = [
    "codebuddy CLI 已安装且在 PATH 中（npm i -g @codebuddy/cli）",
    "已登录: 首次在该机器终端运行 codebuddy 完成登录",
    "交互式弹窗依赖 psmux（项目 bin/ 或 PATH）",
]


def _looks_like_auth_failure(*texts) -> bool:
    blob = "\n".join(t for t in texts if t).lower()
    return any(m in blob for m in _AUTH_MARKERS)


def find_codebuddy() -> str | None:
    for name in ("codebuddy.cmd", "codebuddy.exe", "codebuddy", "codebuddy.ps1"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _extract_result_text(stdout_text: str) -> tuple[str, str | None]:
    """解析 codebuddy --output-format json 的事件数组 → (结论文本, session_id)。

    结构: [ {type:message,...}, ..., {type:"result", result, session_id} ]
    容错: 单对象 / 前后杂音（括号切片）/ result 为 JSON 字符串。
    """
    text = stdout_text.strip()

    def _load(s: str):
        try:
            return json.loads(s)
        except ValueError:
            return None

    data = _load(text)
    if data is None:
        for opener, closer in (("[", "]"), ("{", "}")):
            i, j = text.find(opener), text.rfind(closer)
            if i >= 0 and j > i:
                data = _load(text[i:j + 1])
                if data is not None:
                    break
    if data is None:
        return "", None
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return "", None

    session_id = None
    for item in data:
        if isinstance(item, dict):
            sid = item.get("session_id") or item.get("sessionId")
            if sid:
                session_id = sid

    def _to_text(val) -> str:
        if val is None:
            return ""
        if isinstance(val, str):
            s = val.strip()
            if s[:1] in "[{":
                inner = _load(s)
                if inner is not None:
                    return _to_text(inner)
            return s
        if isinstance(val, list):
            parts = []
            for item in val:
                if isinstance(item, dict):
                    role = item.get("role", "")
                    etype = item.get("type", "")
                    if role == "user":
                        continue
                    if isinstance(item.get("text"), str) and item["text"]:
                        parts.append(item["text"])
                        continue
                    content = item.get("content")
                    if isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict) and c.get("type") in (
                                    "output_text", "text", "output") and c.get("text"):
                                parts.append(c["text"])
            return "\n".join(p for p in parts if p)
        if isinstance(val, dict):
            content = val.get("content")
            if isinstance(content, list):
                return _to_text([val])
            for k in ("text", "output", "result"):
                if isinstance(val.get(k), str):
                    return val[k]
            return ""
        return str(val)

    result_elem = next((x for x in data if isinstance(x, dict)
                        and x.get("type") == "result"), None)
    if result_elem is not None:
        out = _to_text(result_elem.get("result"))
        if out:
            return out, session_id
    # 兜底: assistant 消息聚合
    out = _to_text(data)
    return out, session_id


# ================= 非交互（fresh，每任务新会话） =================
class CodeBuddyCliPlugin(ExecutorPlugin):
    plugin_id = "codebuddy"
    display_name = "CodeBuddy CLI"
    executor_type = "non_interactive_cli"
    concurrency = 3

    def __init__(self, ctx):
        super().__init__(ctx)
        self._mux = PsmuxTransport()
        self._inflight = 0
        self._lock = threading.Lock()

    def check_capability(self) -> CapabilityResult:
        cb = find_codebuddy()
        if cb:
            ok = self._mux.available
            return CapabilityResult(
                ok, None if ok else "psmux 不可用（弹窗可见性降级）", PREMISES)
        return CapabilityResult(False, "未检测到 codebuddy CLI（which 未命中）",
                                PREMISES)

    def submit(self, task: TaskInput) -> SubmitResult:
        cb = find_codebuddy()
        if not cb:
            return SubmitResult(False, "not_installed", "未检测到 codebuddy CLI")
        work_dir = task.result_file.parent
        work_dir.mkdir(parents=True, exist_ok=True)
        session = f"agn_cli_{task.task_id[:8]}"
        with self._lock:
            self._inflight += 1
        prompt_file = work_dir / "prompt.txt"
        prompt_file.write_text(task.prompt, encoding="utf-8")
        stdout_f = work_dir / "stdout.txt"
        stderr_f = work_dir / "stderr.txt"
        exit_f = work_dir / "exit_code.txt"
        if not self._mux.new_session(session):
            # psmux 不可用 → 后台直跑（可见性降级，headless 语义）
            threading.Thread(target=self._run_headless, args=(cb, task),
                             daemon=True).start()
            return SubmitResult(True)
        # 等 shell 就绪（new-session 后 shell 启动需时，过早注入键入会丢失）
        deadline = time.time() + 10
        while time.time() < deadline:
            if self._mux.capture_pane(session).strip():
                break
            time.sleep(0.5)
        time.sleep(1.0)
        # 长/复杂命令一律走脚本文件（psmux send-keys 注入超长行会截断——
        # 实测 ~600 字符路径被截）；pane 里只敲短命令
        run_ps1 = work_dir / "run.ps1"
        run_ps1.write_text(
            # 中文 Windows 控制台默认 GBK: 三处编码统一 UTF-8（否则管道双向乱码）
            '[Console]::InputEncoding = [Text.Encoding]::UTF8\n'
            '[Console]::OutputEncoding = [Text.Encoding]::UTF8\n'
            '$OutputEncoding = [Text.Encoding]::UTF8\n'
            f'Set-Location "{work_dir}"\n'
            f'Get-Content "{prompt_file}" -Raw | & "{cb}" -p -y '
            f'2> "{stderr_f}" | Tee-Object -Variable o\n'
            f'[IO.File]::WriteAllText("{stdout_f}", [string]($o -join "`n"))\n'
            f'"exitcode=$LASTEXITCODE" | Out-File -Encoding ascii "{exit_f}"\n'
            f'Write-Host ""\n'
            f'Write-Host "========== CodeBuddy 回复（任务完成） =========="\n'
            f'$o\n'
            f'Write-Host "================================================"\n'
            f'Write-Host "窗口将在 15 秒后关闭..."\n'
            f'Start-Sleep -Seconds 15\n',
            encoding="utf-8-sig")
        self._mux.send_text(
            session, f'powershell -NoProfile -ExecutionPolicy Bypass -File "{run_ps1}"')
        self._mux.send_keys(session, "Enter")
        # 注入自检: 命令应出现在屏幕上（否则立即失败，不等满超时）
        time.sleep(1.5)
        echoed = self._mux.capture_pane(session)
        if "run.ps1" not in echoed:
            self._mux.kill_session(session)
            with self._lock:
                self._inflight -= 1
            return SubmitResult(False, "agent_error",
                                "命令注入未生效（shell 未就绪或注入丢失）")
        self._mux.attach_visible(session)      # 弹窗: 人类可见执行与对话流
        threading.Thread(target=self._wait_cli, args=(task, session),
                         daemon=True, name=f"cbcli-{task.task_id[:8]}").start()
        return SubmitResult(True)

    def _run_headless(self, cb: str, task: TaskInput) -> None:
        """无 psmux 降级: 后台进程 + 文件重定向（结果仍回 result.md）。"""
        work_dir = task.result_file.parent
        try:
            with open(work_dir / "stdout.txt", "wb") as so, \
                    open(work_dir / "stderr.txt", "wb") as se:
                subprocess.run([cb, "-p", "-y"],
                               cwd=str(work_dir), stdout=so, stderr=se,
                               input=task.prompt.encode("utf-8"),
                               timeout=max(60, task.timeout),
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass
        finally:
            self._finalize_from_files(task)

    def _wait_cli(self, task: TaskInput, session: str) -> None:
        try:
            deadline = time.time() + max(60, task.timeout) + 30
            exit_file = task.result_file.parent / "exit_code.txt"
            while time.time() < deadline:
                if exit_file.exists():
                    time.sleep(1.5)   # 等 stdout flush 完成
                    break
                time.sleep(1.0)
            time.sleep(1.0)
            self._finalize_from_files(task)
            # 停留窗口: 等 run.ps1 的「回复展示 + 15s dwell」播完再关（2.2.6）
            time.sleep(17)
        finally:
            with self._lock:
                self._inflight -= 1
            try:
                self._mux.kill_session(session)
            except Exception:
                pass

    def _finalize_from_files(self, task: TaskInput) -> None:
        """结果文件契约（2.2.11）: 主路 result.md 由 codebuddy 按提示词指令自写；
        本方法为兜底——result.md 缺失时从 stdout.txt（Tee 捕获的回复文本）写入。"""
        d = task.result_file.parent
        stdout_text = ""
        try:
            stdout_text = (d / "stdout.txt").read_text(encoding="utf-8",
                                                       errors="replace").strip()
        except OSError:
            pass
        stderr_text = ""
        try:
            stderr_text = (d / "stderr.txt").read_text(encoding="utf-8",
                                                       errors="replace")
        except OSError:
            pass
        if task.result_file.exists():
            return                      # 主路已生效，不覆盖
        if _looks_like_auth_failure(stdout_text, stderr_text):
            content = f"⚠️ {AUTH_HINT}\n\n---\n原始输出:\n{stderr_text[:500]}"
        elif stdout_text:
            content = stdout_text
        else:
            content = (f"CodeBuddy 未返回结论（result.md 与 stdout.txt 均为空）。\n\n"
                       f"[stderr]\n{stderr_text[:1000]}")
        tmp = task.result_file.with_suffix(".tmp")
        task.result_file.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(task.result_file)          # 原子写（2.2.11）

    def status(self):
        from ..base import ExecutorStatus
        return ExecutorStatus(
            available=find_codebuddy() is not None,
            state="busy" if self._inflight > 0 else "idle",
            inflight=self._inflight, concurrency=self.concurrency,
            current_task=None, queue_len=0)


def _q(path: str) -> str:
    return f'"{path}"' if " " in path else path


# ================= 交互式 TUI（常驻会话，串行队列） =================
class CodeBuddyTuiPlugin(ExecutorPlugin):
    plugin_id = "codebuddy-tui"
    display_name = "CodeBuddy TUI"
    executor_type = "interactive_tui"
    concurrency = 1

    SESSION = "agn_cb_tui"
    SHELL_READY_WAIT = 3.0
    TUI_BOOT_TIMEOUT = 240.0
    POLL_INTERVAL = 2.0
    STABLE_ROUNDS = 3          # 屏幕连续 N 轮稳定 → 兜底完成

    def __init__(self, ctx):
        super().__init__(ctx)
        self._mux = PsmuxTransport()
        self._session_ready = False
        self._poll_state: dict[str, dict] = {}
        self._state_lock = threading.Lock()

    def check_capability(self) -> CapabilityResult:
        cb = find_codebuddy()
        if cb:
            ok = self._mux.available
            return CapabilityResult(
                ok, None if ok else "psmux 不可用（TUI 弹窗必需）", PREMISES)
        return CapabilityResult(False, "未检测到 codebuddy CLI", PREMISES)

    # ---------- 会话生命周期 ----------
    def _ensure_session(self) -> None:
        """常驻 TUI 会话（跨任务复用；弹窗可见对话流）。"""
        if self._session_ready and self._mux.has_session(self.SESSION):
            return
        if not self._mux.new_session(self.SESSION):
            raise RuntimeError("psmux 会话创建失败（TUI 模式必需 psmux）")
        time.sleep(self.SHELL_READY_WAIT)
        # 交互式 TUI: 默认会话 + 跳过权限确认（安全局域网自用，2.2.14）
        cb = find_codebuddy()
        self._mux.send_text(self.SESSION, f"{_q(cb)} --permission-mode bypassPermissions")
        self._mux.send_keys(self.SESSION, "Enter")
        self._mux.attach_visible(self.SESSION)    # 弹窗: 人类观察 TUI 对话流
        self._wait_tui_ready()
        self._session_ready = True

    def _wait_tui_ready(self) -> None:
        """启动状态机: 处理 trust/权限对话框，等 TUI 主界面渲染完成（2.2.14）。

        就绪判定: TUI 界面含 ASCII 艺术字 █/边框 │（PS 提示符不可能出现）。
        注意欢迎屏有动态内容（时钟/WebUI 提示刷新），不能用"屏幕静止"判就绪。
        """
        deadline = time.time() + self.TUI_BOOT_TIMEOUT
        while time.time() < deadline:
            screen = self._mux.capture_pane(self.SESSION)
            low = screen.lower()
            # trust/权限对话框自动通过（Enter）——关键词须具体，防误触 TUI 输入
            if any(k in low for k in ("do you trust", "trust this folder",
                                      "是否信任", "do you want to proceed",
                                      "press enter to")):
                self._mux.send_keys(self.SESSION, "Enter")
                time.sleep(2.0)
                continue
            # TUI 渲染特征: ASCII 艺术/边框字符 + 足够内容量
            if ("█" in screen or "│" in screen) and len(screen.strip()) > 200:
                time.sleep(2.0)   # 等界面完全稳定
                return
            time.sleep(1.0)
        raise TimeoutError(f"TUI 启动超时（>{self.TUI_BOOT_TIMEOUT:.0f}s）")

    # ---------- 任务 ----------
    def submit(self, task: TaskInput) -> SubmitResult:
        if not find_codebuddy():
            return SubmitResult(False, "not_installed", "未检测到 codebuddy CLI")
        try:
            self._ensure_session()
        except Exception as e:
            return SubmitResult(False, "agent_error", f"TUI 会话拉起失败: {e}")
        task_tag = task.task_id[:8]
        # 单行任务信封（多行在 TUI 注入路径有提前提交风险）+ 结果文件指令
        instruction = " ".join(task.prompt.split())
        result_path = str(task.result_file.resolve())
        line = (f"【跨节点任务 {task_tag}】{instruction} "
                f"【输出要求】完成后把最终结论写入文件 {result_path} "
                f"(UTF-8 Markdown)，并在回复中说明已写入。")
        if not self._inject_and_verify(line, task_tag):
            return SubmitResult(False, "agent_error",
                                "任务注入失败（3 次 Enter 均未提交成功）")
        # 屏幕兜底轮询（主路 result.md 由注册表全局轮询推进）
        threading.Thread(target=self._watch_screen, args=(task,), daemon=True,
                         name=f"cbtui-{task_tag}").start()
        return SubmitResult(True)

    def _inject_and_verify(self, line: str, task_tag: str) -> bool:
        """注入 + 提交 + 验证（实测坑: 文本注入后立即 Enter 会被 TUI 吞掉——
        须延时并验证「文本进入对话区 且 输入框已清空」，失败重发 Enter）。"""
        self._mux.send_text(self.SESSION, line)
        time.sleep(1.5)                    # 等 TUI 输入框处理完文本
        for _ in range(3):
            self._mux.send_keys(self.SESSION, "Enter")
            time.sleep(2.5)
            screen = self._mux.capture_pane(self.SESSION)
            lines = screen.splitlines()
            gt_lines = [l for l in lines if l.strip().startswith(">")]
            in_input = bool(gt_lines) and task_tag in gt_lines[-1]
            if task_tag in screen and not in_input:
                return True                # 已提交: 文本在对话区、输入框空
        return False

    def _watch_screen(self, task: TaskInput) -> None:
        """屏幕稳定检测（result.md 缺失时的兜底回收，同 WorkBuddy 机制）。"""
        task_tag = task.task_id[:8]
        deadline = time.time() + task.timeout
        last, stable = "", 0
        with self._state_lock:
            self._poll_state[task.task_id] = {"done": False, "text": ""}
        while time.time() < deadline:
            time.sleep(self.POLL_INTERVAL)
            if task.result_file.exists():     # 主路生效
                with self._state_lock:
                    self._poll_state[task.task_id]["done"] = True
                return
            screen = self._mux.capture_pane(self.SESSION)
            # 取任务信封之后的屏幕文本（近似: 首次出现 tag 之后）
            idx = screen.find(task_tag)
            seg = screen[idx:] if idx >= 0 else screen
            # 过滤输入行残留（信封自身）
            seg = "\n".join(l for l in seg.splitlines()
                            if task_tag not in l and "输出要求" not in l)
            if seg.strip() and seg == last:
                stable += 1
            else:
                stable = 0
                last = seg
            if stable >= self.STABLE_ROUNDS:
                text = seg.strip()[:20000]
                if _looks_like_auth_failure(text):
                    text = f"⚠️ {AUTH_HINT}"
                with self._state_lock:
                    self._poll_state[task.task_id] = {"done": True, "text": text}
                return
        with self._state_lock:
            self._poll_state[task.task_id] = {"done": True, "text": last.strip()}

    def is_done(self, task_id: str) -> bool:
        if super().is_done(task_id):
            return True
        with self._state_lock:
            st = self._poll_state.get(task_id)
            return bool(st and st.get("done"))

    def get_result(self, task_id: str) -> ResultPayload:
        fr = super().get_result(task_id)
        if fr.ok:
            return fr
        with self._state_lock:
            st = self._poll_state.get(task_id) or {}
        if st.get("text"):
            return ResultPayload(True, None, st["text"])
        return ResultPayload(False, "agent_error",
                             f"结果文件未生成且未回收到回复: {fr.content or ''}")

    def status(self):
        from ..base import ExecutorStatus
        return ExecutorStatus(
            available=find_codebuddy() is not None,
            state="idle", inflight=0, concurrency=self.concurrency,
            current_task=None, queue_len=0)
