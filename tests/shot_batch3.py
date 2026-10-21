# -*- coding: utf-8 -*-
"""批三界面目检：深色主题 / 单步调试对话框 / 批量多选 / 拖拽落点指示线。"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from rpa import gui as gui_module
from rpa.engine import Group, Step

# 临时设置：默认深色主题，避免动到用户配置
_TMP = tempfile.mkdtemp(prefix="rpa_shot3_")
gui_module.SETTINGS_PATH = os.path.join(_TMP, "settings.json")
with open(gui_module.SETTINGS_PATH, "w", encoding="utf-8") as fh:
    json.dump({"theme": "dark", "window": "1180x800"}, fh)
gui_module.enable_dpi_awareness()

app = gui_module.App()
app.after(200, app._restore_paned)

image_dir = app.image_dir
os.makedirs(image_dir, exist_ok=True)
for name, color in (("登录按钮.png", (59, 111, 245)),):
    path = os.path.join(image_dir, name)
    if not os.path.exists(path):
        Image.new("RGB", (200, 70), color).save(path)

app.project.groups = [
    Group("登录", [Step("click", "登录按钮.png", 1, "首页右上角"),
                   Step("input", "admin"),
                   Step("hotkey", "enter"),
                   Step("wait", "0.5")]),
    Group("巡检", [Step("if_image", "登录按钮.png|登录"),
                   Step("scroll", "-300")]),
]
app.group_index = 0
app._refresh_all(select_group=0)
# 多选两行（批量操作的视觉效果）
app.step_tree.selection_set("1", "2")
app._update_preview()
app.update()

shot = Image


def grab(name):
    from PIL import ImageGrab
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    ImageGrab.grab().save(path)
    print(f"saved tests/{name}")


def show_debug():
    """单步调试对话框 + 拖拽指示线（模拟一次拖拽中途）。"""
    dialog = gui_module.DebugStepDialog(app, "登录", 1,
                                        Step("input", "admin", 1, "账号"))
    app.update()
    grab("ui_batch3_debug.png")
    dialog._run()

    # 模拟拖拽：按下 → 移动（画出落点线）→ 截图 → 松手
    tree = app.step_tree
    bbox = tree.bbox("0")
    x, y = 6, bbox[1] + 2
    tree.event_generate("<ButtonPress-1>", x=x, y=y)
    tree.event_generate("<B1-Motion>", x=x, y=y + 70)
    app.update()
    grab("ui_batch3_drag.png")
    tree.event_generate("<ButtonRelease-1>", x=x, y=y + 70)
    app.update()


app.after(500, lambda: (grab("ui_batch3_dark.png"), show_debug(),
                        app.after(300, app.destroy)))
app.mainloop()
