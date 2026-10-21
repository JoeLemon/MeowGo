# -*- coding: utf-8 -*-
"""步骤对话框新类型目检：if_else 双组输入 / drag / set / copy 的渲染效果。

用法：C:/Python310/python.exe tests/shot_dialogs.py
产出：tests/ui_dialog_ifelse.png、tests/ui_dialog_drag.png（各停留 2.5 秒抓窗）
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa import gui as gui_module  # noqa: E402
from rpa.engine import Group, Project, Step  # noqa: E402

OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    app = gui_module.App()
    app.project = Project(groups=[
        Group("甲组", [Step("click", "按钮.png", 1)]),
        Group("关弹窗", [Step("wait", "0.5")]),
        Group("等弹窗", [Step("wait", "0.5")]),
    ])
    app.update()

    def shot(name, dialog):
        app.update()
        time.sleep(1.2)                     # 等动画/置顶稳定
        x = dialog.winfo_rootx()
        y = dialog.winfo_rooty()
        w = dialog.winfo_width()
        h = dialog.winfo_height()
        try:
            import pyautogui

            pyautogui.screenshot(
                region=(x, y, w, h)).save(os.path.join(OUT_DIR, name))
            print(f"saved {name} ({w}x{h})")
        except Exception as exc:  # noqa: BLE001
            print(f"skip {name}: {exc}")
        dialog.cancel()

    # 1) if_else：出现 / 没出现两个组名框
    dialog = gui_module.StepDialog(app, app.project,
                                   Step("if_else", "弹窗.png|关弹窗|等弹窗"))
    shot("ui_dialog_ifelse.png", dialog)

    # 2) drag：起点|终点 + 等待超时
    dialog2 = gui_module.StepDialog(app, app.project,
                                    Step("drag", "文件.png|回收站.png",
                                         timeout=3, on_fail="skip"))
    shot("ui_dialog_drag.png", dialog2)

    # 3) set：变量步骤
    dialog3 = gui_module.StepDialog(app, app.project,
                                    Step("set", "订单号={index}"))
    shot("ui_dialog_set.png", dialog3)

    app.destroy()


if __name__ == "__main__":
    main()
