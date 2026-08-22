"""发送核验探针: 检查 WorkBuddy 对话区是否出现 KEY、输入框是否已清空。"""
import time
from pywinauto import Application

KEY = "7452"
app = Application(backend="uia").connect(title="WorkBuddy", timeout=5)
dlg = app.window(title="WorkBuddy")


def walk(ctrl, kinds, max_depth=14):
    out = []
    def rec(c, d):
        if d > max_depth:
            return
        try:
            ct = c.element_info.control_type
            if ct in kinds:
                out.append((ct, c.element_info.name or ""))
        except Exception:
            return
        try:
            for ch in c.children():
                rec(ch, d + 1)
        except Exception:
            pass
    rec(ctrl, 0)
    return out


# 1. 对话区（Text/ListItem/Button）是否含 KEY
tx = [n for _, n in walk(dlg, ("Text", "ListItem", "Button"))]
conv_hits = [n for n in tx if KEY in n]

# 2. 输入框（最宽 Edit）当前文本：发送成功理应已清空（不再含 KEY）
best, bw = None, -1
for _ct, _n, e in [(None, None, None)]:
    pass
# 重新定位 Edit 对象
edits = []
def find_edit(ctrl, depth=0):
    if depth > 14:
        return
    try:
        if ctrl.element_info.control_type == "Edit":
            edits.append(ctrl)
    except Exception:
        pass
    try:
        for ch in ctrl.children():
            find_edit(ch, depth + 1)
    except Exception:
        pass
find_edit(dlg)
for ed in edits:
    try:
        w = ed.rectangle().width()
        if w > bw:
            best, bw = ed, w
    except Exception:
        pass

edit_text = ""
if best is not None:
    try:
        edit_text = best.get_value() or ""
    except Exception:
        edit_text = ""
edit_has = KEY in edit_text

print(f"对话区含 KEY 的消息数: {len(conv_hits)}")
print(f"输入框当前文本: {edit_text!r}")
print(f"输入框是否仍含 KEY: {edit_has}")
if conv_hits and not edit_has:
    print("VERDICT: 真实发送成功（对话有 KEY 且输入框已清空）")
elif conv_hits and edit_has:
    print("VERDICT: 对话有 KEY 但输入框仍含 KEY（发送可能未完成/清空有延迟）")
elif not conv_hits:
    print("VERDICT: 对话区未出现 KEY（未发送或仍在渲染）| 输入框含KEY=%s" % edit_has)