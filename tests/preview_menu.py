# -*- coding: utf-8 -*-
"""右键菜单外观自检：弹出菜单并截图，便于人工确认风格是否与界面一致。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa import gui as gui_module  # noqa: E402
from rpa.engine import Group, Step  # noqa: E402

gui_module.SETTINGS_PATH = os.path.join(tempfile.mkdtemp(), "settings.json")
gui_module.enable_dpi_awareness()

app = gui_module.App()
app.project.groups = [
    Group("登录", [Step("click", "登录按钮.png", 1, "首页登录"),
                   Step("input", "admin"),
                   Step("hotkey", "enter")]),
    Group("主流程", [Step("call", "登录"), Step("wait", "0.5")]),
]
app._refresh_all(select_group=0)
app.attributes("-topmost", True)
app.lift()


class Event:
    """模拟右键事件。"""

    def __init__(self, y, x_root, y_root):
        self.y = y
        self.x_root = x_root
        self.y_root = y_root


def shot():
    bbox = app.step_tree.bbox("1")
    y = bbox[1] + bbox[3] // 2 if bbox else 40
    app.step_tree.selection_set("1")
    app._step_menu(Event(y, app.step_tree.winfo_rootx() + 240,
                         app.step_tree.winfo_rooty() + y))
    app.update()
    menu = None
    for child in app.winfo_children():
        if isinstance(child, gui_module.uikit.ContextMenu):
            menu = child
    if menu is not None:
        menu._set_hover(1)        # 高亮第二项，展示悬停效果
        app.update()

    from PIL import ImageGrab

    x, y0 = app.winfo_rootx(), app.winfo_rooty()
    w, h = app.winfo_width(), app.winfo_height()
    path = os.path.join(os.path.dirname(__file__), "menu_preview.png")
    ImageGrab.grab(bbox=(x, y0, x + w, y0 + h)).save(path)
    print("已保存", path)
    if menu is not None:
        menu.close()
    app.destroy()


app.after(1200, shot)
app.mainloop()
