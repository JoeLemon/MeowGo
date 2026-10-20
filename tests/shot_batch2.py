# -*- coding: utf-8 -*-
"""批二界面目检：造一个有代表性的状态后截图（搜索 / 斑马纹 / 图片预览）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from rpa import gui as gui_module
from rpa.engine import Group, Step

gui_module.enable_dpi_awareness()

app = gui_module.App()
app.geometry(f"{app.s(1180)}x{app.s(820)}")

# 造几张真实图片，供预览窗格显示
image_dir = app.image_dir
os.makedirs(image_dir, exist_ok=True)
for name, color in (("登录按钮.png", (59, 111, 245)),
                    ("关闭弹窗.png", (226, 104, 60))):
    if not os.path.exists(os.path.join(image_dir, name)):
        Image.new("RGB", (200, 70), color).save(
            os.path.join(image_dir, name))

app.project.groups = [
    Group("登录", [Step("click", "登录按钮.png", 1, "首页右上角"),
                   Step("input", "admin"),
                   Step("hotkey", "enter")]),
    Group("巡检", [Step("if_image", "关闭弹窗.png|登录", 1, "有弹窗就关"),
                   Step("wait", "0.5"),
                   Step("scroll", "-300"),
                   Step("cmd", "start notepad")]),
]
app.group_index = 0
app._refresh_all(select_group=0)
app.step_tree.selection_set("0")       # 选中图片步骤 → 预览窗格弹出
app._update_preview()
app.update()
app.after(150, app._restore_paned)
app.after(600, lambda: (
    app.update(),
    from_pil := __import__("PIL.ImageGrab", fromlist=["grab"]).grab(),
    from_pil.save(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "ui_batch2.png")),
    print("saved tests/ui_batch2.png"),
    app.destroy(),
))
app.mainloop()
