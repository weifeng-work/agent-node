"""WorkBuddy GUI 适配器（2.2.5 / 2.2.8 / 2.2.11）。

注入/回收核心复用 agent-bus 已实战验证的 workbuddy_executor 逻辑：
  1. deeplink 预填: os.startfile('workbuddy://task?action=start&prompt=...')
     （必须 ShellExecute 直达——过 cmd/bash 会把 URL 里的 & 截断）
  2. UIA 提交: pywinauto 定位输入框 Edit → click_input 聚焦 → ENTER
     （含"覆盖草稿"弹窗自愈重试；提交成功 = 对话区出现含任务短码的用户消息）
  3. 回收: 主路 = 结果文件契约（提示词要求 WorkBuddy 把最终结论写入 result.md，
     is_done 判定文件存在 + 大小稳定）；兜底 = UIA 文本树轮询（完成按钮/文本稳定），
     结果文件缺失时用兜底文本（2.2.11 GUI 必须 + 历史经验）

前提（2.2.13 前提披露）:
  - WorkBuddy 桌面端已安装且已运行（未运行 → 拒绝并提示，不冷启动，2.2.12）
  - WorkBuddy 以普通权限运行（管理员进程会触发 UIPI 输入拦截，err=5）
  - pip install pywinauto
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote

from ..base import (CapabilityResult, ExecutorPlugin, ResultPayload, SubmitResult,
                    TaskInput)

WINDOW_TITLE = "WorkBuddy"
DEEPLINK_PREFIX = "workbuddy://task?action=start&prompt="
POLL_INTERVAL = 3.0
SUBMIT_WAIT = 4.0
MAX_URL_LEN = 4000

# 界面噪音过滤（复用 agent-bus 实战调优的规则）
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
_MODEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,11}$")
_NOISE_PREFIX = ("今天帮你做些什么", "内容由 AI 生成", "引用来源", "对话内搜索")

PREMISES = [
    "WorkBuddy 桌面端已安装且已登录并保持运行（执行器不冷启动 GUI，2.2.12）",
    "WorkBuddy 以普通权限运行（管理员运行会触发 UIPI 输入拦截）",
    "目标机需 pip install pywinauto",
]


def _is_noise(text: str) -> bool:
    t = text.strip()
    if not t or t in ("\ufeff", "WorkBuddy", "默认权限", "暂无内容"):
        return True
    if _TIME_RE.match(t):
        return True
    return any(t.startswith(p) for p in _NOISE_PREFIX)


def _strip_tail_noise(parts: list) -> list:
    while parts:
        t = parts[-1].strip()
        if _TIME_RE.match(t) or _MODEL_RE.match(t):
            parts.pop()
        else:
            break
    return parts


class WorkBuddyPlugin(ExecutorPlugin):
    plugin_id = "workbuddy"
    display_name = "WorkBuddy"
    executor_type = "gui"
    concurrency = 1  # 2.2.9: GUI 固定 1

    def __init__(self, ctx):
        super().__init__(ctx)
        self._poll_state: dict[str, dict] = {}   # task_id -> {done, text}
        self._state_lock = threading.Lock()

    # ---------- 能力自检（2.2.12: GUI 查进程/窗口） ----------
    def check_capability(self) -> CapabilityResult:
        running = self._workbuddy_running()
        if running:
            return CapabilityResult(True, premises=PREMISES)
        installed = self._workbuddy_installed()
        if installed:
            return CapabilityResult(
                False, reason="WorkBuddy 已安装但未运行——请先在目标机启动 WorkBuddy",
                premises=PREMISES)
        return CapabilityResult(False, reason="未检测到 WorkBuddy（未安装）",
                                premises=PREMISES)

    @staticmethod
    def _workbuddy_running() -> bool:
        try:
            out = subprocess.run(
                ["tasklist", "/fi", "imagename eq WorkBuddy.exe", "/fo", "csv", "/nh"],
                capture_output=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            return b"WorkBuddy.exe" in out  # 字节匹配，规避中文系统 GBK 控制台编码
        except Exception:
            return False

    @staticmethod
    def _workbuddy_installed() -> bool:
        return Path(r"C:\Program Files\WorkBuddy\WorkBuddy.exe").is_file()

    # ---------- 任务提交 ----------
    def submit(self, task: TaskInput) -> SubmitResult:
        task_tag = task.task_id[:8]
        # 结果文件契约指令（2.2.11）：要求被调智能体把最终结论写入指定结果文件
        result_path = str(task.result_file.resolve())
        prompt = (
            f"【跨节点任务 {task_tag}】\n"
            f"{task.prompt}\n\n"
            f"【输出要求】任务完成后，请把最终结论（Markdown 格式）写入文件：\n"
            f"{result_path}\n"
            f"（UTF-8 编码；写入成功后在回复中说明\"结果已写入文件\"。）"
        )
        if task.attachments:
            prompt += "\n【附件】" + "; ".join(str(a) for a in task.attachments)
        try:
            self._inject_prompt(prompt, task_tag)
        except Exception as e:
            return SubmitResult(False, "agent_error", f"注入失败: {e}")
        # 启动 UIA 兜底回收轮询（文件缺失时使用）
        threading.Thread(target=self._harvest_loop, args=(task,), daemon=True,
                         name=f"wb-harvest-{task_tag}").start()
        return SubmitResult(True)

    # ---------- is_done / get_result ----------
    def is_done(self, task_id: str) -> bool:
        # 主路: 结果文件（注册表叠加大小稳定性判断）；兜底: UIA 完成信号
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

    # ---------- 注入核心（复用 agent-bus 已验证实现） ----------
    def _connect_window(self, wait: float = 20.0):
        from pywinauto import Application
        deadline = time.time() + wait
        last_err = None
        while time.time() < deadline:
            try:
                app = Application(backend="uia").connect(title=WINDOW_TITLE, timeout=3)
                dlg = app.window(title=WINDOW_TITLE)
                if dlg.exists(timeout=1):
                    return app, dlg
            except Exception as e:
                last_err = e
            time.sleep(1.5)
        raise RuntimeError(f"找不到 WorkBuddy 窗口（{last_err}）；请确认已启动且为普通权限运行")

    def _walk(self, dlg, kinds: tuple, max_depth=12) -> list:
        out = []

        def rec(ctrl, depth):
            if depth > max_depth:
                return
            try:
                ct = ctrl.element_info.control_type
                if ct in kinds:
                    out.append((ct, ctrl.element_info.name or ""))
            except Exception:
                return
            try:
                for ch in ctrl.children():
                    rec(ch, depth + 1)
            except Exception:
                pass

        rec(dlg, 0)
        return out

    def _find_input_edit(self, dlg):
        best, best_w = None, -1
        edits = []

        def rec(ctrl, depth):
            if depth > 12:
                return
            try:
                if ctrl.element_info.control_type == "Edit":
                    edits.append(ctrl)
            except Exception:
                return
            try:
                for ch in ctrl.children():
                    rec(ch, depth + 1)
            except Exception:
                pass

        rec(dlg, 0)
        for e in edits:
            try:
                w = e.rectangle().width()
                if w > best_w:
                    best, best_w = e, w
            except Exception:
                continue
        if best is None:
            raise RuntimeError("UIA 树中未找到输入框 Edit 控件")
        return best

    def _inject_prompt(self, prompt: str, task_tag: str):
        from pywinauto.keyboard import send_keys
        _, dlg = self._connect_window()
        url = DEEPLINK_PREFIX + quote(prompt)
        if len(url) > MAX_URL_LEN:
            # 过长截断保护：保留结果文件指令（尾部靠前段提示词承载任务）
            prompt = prompt[:2000]
            url = DEEPLINK_PREFIX + quote(prompt)
        os.startfile(url)  # ShellExecute 直达；绝不能过 cmd（& 会被截断）
        time.sleep(2.5)
        for attempt in range(3):
            dlg = self._connect_window(wait=10)[1]
            try:
                dlg.set_focus()
            except Exception:
                pass
            edit = self._find_input_edit(dlg)
            edit.click_input()
            time.sleep(0.5)
            send_keys("{ENTER}")
            time.sleep(SUBMIT_WAIT)
            texts_now = [n for _, n in self._walk(dlg, ("Text", "ListItem", "Button"))]
            if any(task_tag in n for n in texts_now):
                return
            # "覆盖当前草稿"弹窗自愈
            for _, n in self._walk(dlg, ("Button",)):
                if n and ("覆盖" in n or n == "确定"):
                    try:
                        dlg.child_window(title=n, control_type="Button").click_input()
                        time.sleep(1.0)
                    except Exception:
                        pass
                    break
        raise RuntimeError("deeplink 预填/ENTER 提交失败（3 次尝试均未在对话区发现任务消息）")

    # ---------- UIA 兜底回收（复用 agent-bus 轮询逻辑） ----------
    def _harvest_loop(self, task: TaskInput):
        task_tag = task.task_id[:8]
        deadline = time.time() + task.timeout
        last_reply, stable = "", 0
        with self._state_lock:
            self._poll_state[task.task_id] = {"done": False, "text": ""}
        while time.time() < deadline:
            time.sleep(POLL_INTERVAL)
            if super().is_done(task.task_id):
                # 结果文件已生成 → 主路生效，结束兜底轮询
                with self._state_lock:
                    self._poll_state[task.task_id]["done"] = True
                return
            try:
                _, dlg = self._connect_window(wait=5)
            except Exception:
                continue
            texts = [n for _, n in self._walk(dlg, ("Text", "ListItem"))]
            btns = {n for _, n in self._walk(dlg, ("Button",))}
            done_btn = any(n.startswith("已完成") or n == "复制"
                           for n in btns - {""})
            reply_parts = []
            env_idx = next((i for i, n in enumerate(texts) if task_tag in n), None)
            if env_idx is not None:
                header_idx = next(
                    (i for i in range(env_idx + 1, len(texts))
                     if texts[i].strip() == WINDOW_TITLE), None)
                if header_idx is not None:
                    seg = texts[header_idx + 1:]
                else:
                    last_tag = max(
                        (i for i, n in enumerate(texts) if task_tag in n),
                        default=env_idx)
                    seg = texts[last_tag:]
                while seg and (_TIME_RE.match(seg[0].strip()) or seg[0].strip() == "\ufeff"):
                    seg = seg[1:]
                for n in seg:
                    if _is_noise(n):
                        break
                    reply_parts.append(n)
            reply = "\n".join(_strip_tail_noise(reply_parts)).strip()
            if reply:
                if reply == last_reply:
                    stable += 1
                else:
                    stable = 0
                last_reply = reply
                if done_btn or stable >= 2:
                    with self._state_lock:
                        self._poll_state[task.task_id] = {"done": True, "text": last_reply}
                    return
        # 超时：保留已观察文本，标记 done（get_result 兜底可用）
        with self._state_lock:
            self._poll_state[task.task_id] = {"done": True, "text": last_reply}
