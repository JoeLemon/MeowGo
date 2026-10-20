# -*- coding: utf-8 -*-
"""可视化自检：把图标和圆角按钮画出来截图，便于人工确认外观。"""
import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa import uikit  # noqa: E402
from rpa.gui import enable_dpi_awareness as _enable_dpi_awareness  # noqa: E402

_enable_dpi_awareness()

NAMES = ["run", "loop", "pause", "stop", "add", "copy", "trash", "up", "down",
         "folder", "folder_open", "note", "link", "image", "clock", "calendar",
         "keyboard", "text", "terminal", "scroll", "pencil", "import", "export",
         "save", "open", "table", "chevron_down", "chevron_right", "close",
         "check", "sliders", "info", "gear"]

root = tk.Tk()
root.title("图标 / 按钮 自检")
root.configure(bg="#f5f6f8")
root.attributes("-topmost", True)
root.lift()
scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)


def s(v):
    return int(round(v * scale))


tk.Label(root, text="图标集（16px）", bg="#f5f6f8",
         font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=s(16),
                                                       pady=(s(12), s(4)))
grid = tk.Frame(root, bg="#f5f6f8")
grid.pack(anchor="w", padx=s(16))
for index, name in enumerate(NAMES):
    cell = tk.Frame(grid, bg="#ffffff", padx=s(6), pady=s(6),
                    highlightthickness=1, highlightbackground="#e2e7f0")
    cell.grid(row=index // 11, column=index % 11, padx=s(2), pady=s(2))
    lbl = tk.Label(cell, image=uikit.icon(name, s(18), "#33405a"),
                   bg="#ffffff")
    lbl.pack()
    tk.Label(cell, text=name, bg="#ffffff", fg="#8a93a5",
             font=("Microsoft YaHei UI", 7)).pack()

tk.Label(root, text="圆角按钮", bg="#f5f6f8",
         font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=s(16),
                                                       pady=(s(14), s(4)))
row = tk.Frame(root, bg="#f5f6f8")
row.pack(anchor="w", padx=s(16), pady=(0, s(10)))
uikit.RoundButton(row, "运行", "run", kind="accent", scale=scale).pack(
    side="left", padx=s(4))
uikit.RoundButton(row, "循环", "loop", scale=scale).pack(side="left", padx=s(4))
uikit.RoundButton(row, "暂停", "pause", scale=scale).pack(side="left", padx=s(4))
uikit.RoundButton(row, "停止", "stop", kind="danger", scale=scale).pack(
    side="left", padx=s(4))
uikit.RoundButton(row, "新增组", "add", kind="outline", scale=scale).pack(
    side="left", padx=s(4))
disabled = uikit.RoundButton(row, "禁用态", "trash", kind="outline", scale=scale)
disabled.pack(side="left", padx=s(4))
disabled.set_enabled(False)

row2 = tk.Frame(root, bg="#ffffff")
row2.pack(anchor="w", padx=s(16), pady=(0, s(14)))
for text, name, kind in (("导入 Excel", "import", "outline"),
                         ("导出 Excel", "export", "outline"),
                         ("截图", "image", "outline"),
                         ("保存", "save", "solid"),
                         ("设置", "sliders", "ghost")):
    uikit.RoundButton(row2, text, name, kind=kind, scale=scale,
                      bg="#ffffff").pack(side="left", padx=s(4))


def shot():
    try:
        from PIL import ImageGrab

        root.update()
        path = os.path.join(os.path.dirname(__file__), "uikit_preview.png")
        x, y = root.winfo_rootx(), root.winfo_rooty()
        w, h = root.winfo_width(), root.winfo_height()
        ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(path)
        print("已保存", path)
    finally:
        root.destroy()


root.after(1200, shot)
root.mainloop()
