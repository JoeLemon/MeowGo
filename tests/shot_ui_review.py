# -*- coding: utf-8 -*-
"""UI 规范统一后的界面目检：浅色 / 深色主界面各一张。

用法：C:/Python310/python.exe tests/shot_ui_review.py
产出：reports/_ui_review/12_主界面_浅色_v1.2.png、13_主界面_深色_v1.2.png

用临时 settings，不动用户真实配置；窗口按逻辑尺寸 1180x800 恢复。
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa import gui as gui_module  # noqa: E402
from rpa.engine import Group, Step  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "reports", "_ui_review")

# 临时设置：默认浅色主题，避免动到用户配置
_TMP = tempfile.mkdtemp(prefix="rpa_shot_review_")
gui_module.SETTINGS_PATH = os.path.join(_TMP, "settings.json")
with open(gui_module.SETTINGS_PATH, "w", encoding="utf-8") as fh:
    json.dump({"theme": "light", "window": "1180x800"}, fh)
gui_module.enable_dpi_awareness()

app = gui_module.App()
app.after(200, app._restore_paned)

app.project.groups = [
    Group("登录流程", [Step("click", "登录按钮.png", 1, "首页右上角"),
                     Step("input", "admin"),
                     Step("hotkey", "enter"),
                     Step("wait", "0.5")]),
    Group("主流程", [Step("call", "登录流程"),
                   Step("click", "提交.png"),
                   Step("input", "你好{名字}"),
                   Step("if_image", "弹窗.png|关闭弹窗"),
                   Step("wait", "1")]),
]
app.group_index = 1
app._refresh_all(select_group=1)
app.update()


def grab(name):
    app.update()
    time.sleep(1.0)                     # 等布局/动画稳定
    try:
        import pyautogui

        os.makedirs(OUT_DIR, exist_ok=True)
        pyautogui.screenshot(region=(app.winfo_rootx(), app.winfo_rooty(),
                                     app.winfo_width(), app.winfo_height())
                             ).save(os.path.join(OUT_DIR, name))
        print(f"saved reports/_ui_review/{name}")
    except Exception as exc:  # noqa: BLE001
        print(f"skip {name}: {exc}")


app.after(400, lambda: (grab("12_主界面_浅色_v1.2.png"),
                        app.toggle_theme(),
                        grab("13_主界面_深色_v1.2.png"),
                        app.after(300, app.destroy)))
app.mainloop()
