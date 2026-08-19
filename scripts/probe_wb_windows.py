"""WorkBuddy 多窗口诊断: 枚举所有顶层窗口，检查每个窗口的 Edit 控件情况。"""
from pywinauto import Application
from pywinauto.findwindows import find_windows
from pywinauto import Desktop


def walk_kinds(ctrl, kinds, max_depth=12):
    out = []

    def rec(c, depth):
        if depth > max_depth:
            return
        try:
            ct = c.element_info.control_type
            if ct in kinds:
                out.append((ct, c.element_info.name or "", c))
        except Exception:
            return
        try:
            for ch in c.children():
                rec(ch, depth + 1)
        except Exception:
            pass

    rec(ctrl, 0)
    return out


def main():
    desktop = Desktop(backend="uia")
    wins = [w for w in desktop.windows() if "workbuddy" in (w.window_text() or "").lower()]
    print(f"top-level WorkBuddy windows: {len(wins)}")
    for i, w in enumerate(wins):
        try:
            texts = w.window_text()
        except Exception:
            texts = "?"
        edits = walk_kinds(w, ("Edit",))
        print(f"--- window[{i}] title={texts!r} edits={len(edits)}")
        for ct, name, c in edits:
            try:
                rect = c.rectangle()
                size = f"{rect.width()}x{rect.height()}"
            except Exception:
                size = "?"
            inner = walk_kinds(c, ("Text",))[:3]
            inner_txt = " | ".join(t[1][:40] for t in inner)
            print(f"    Edit name={name!r} size={size} inner={inner_txt!r}")
    # 另测: 按 title connect 的方式（适配器同款）
    try:
        app = Application(backend="uia").connect(title="WorkBuddy", timeout=3)
        dlg = app.window(title="WorkBuddy")
        edits = walk_kinds(dlg, ("Edit",))
        print(f"adapter-style connect: edits={len(edits)}")
    except Exception as e:
        print(f"adapter-style connect failed: {e}")


if __name__ == "__main__":
    main()
