"""WorkBuddy UIA 树转储探针: 打印窗口控件树（control_type + name），定位真实输入框类型。"""
import sys
import time

from pywinauto import Application


def main():
    app = Application(backend="uia").connect(title="WorkBuddy", timeout=5)
    dlg = app.window(title="WorkBuddy")
    print("window found:", dlg.exists())
    out = []

    def rec(ctrl, depth):
        if depth > 10:
            return
        try:
            ct = ctrl.element_info.control_type
            name = (ctrl.element_info.name or "")[:60]
            out.append(f"{'  ' * depth}{ct}: {name!r}")
        except Exception:
            return
        try:
            for ch in ctrl.children():
                rec(ch, depth + 1)
        except Exception:
            pass

    rec(dlg, 0)
    text = "\n".join(out[:400])
    with open(r"C:\Users\IKUN\agent-node\scripts\uia_dump.txt", "w",
              encoding="utf-8") as f:
        f.write(text + f"\n--- total lines: {len(out)}\n")
    print(f"dumped {len(out)} lines to uia_dump.txt")


if __name__ == "__main__":
    main()
