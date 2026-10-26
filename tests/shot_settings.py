# -*- coding: utf-8 -*-
"""运行设置对话框目检：开机自启行与整体布局。

用法：C:/Python310/python.exe tests/shot_settings.py
产出：reports/_ui_review/14_运行设置_开机自启.png

用临时 settings，不动用户真实配置。
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa import gui as gui_module  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "reports", "_ui_review")

_TMP = tempfile.mkdtemp(prefix="rpa_shot_settings_")
gui_module.SETTINGS_PATH = os.path.join(_TMP, "settings.json")
with open(gui_module.SETTINGS_PATH, "w", encoding="utf-8") as fh:
    json.dump({"theme": "light", "window": "1180x800"}, fh)
gui_module.enable_dpi_awareness()

app = gui_module.App()
app.update()

dialog = gui_module.SettingsDialog(app, app.settings, app.image_dir)
app.update()


def grab(name):
    app.update()
    time.sleep(0.8)
    try:
        import pyautogui

        os.makedirs(OUT_DIR, exist_ok=True)
        pyautogui.screenshot(region=(dialog.winfo_rootx(),
                                     dialog.winfo_rooty(),
                                     dialog.winfo_width(),
                                     dialog.winfo_height())
                             ).save(os.path.join(OUT_DIR, name))
        print(f"saved reports/_ui_review/{name} "
              f"({dialog.winfo_width()}x{dialog.winfo_height()})")
    except Exception as exc:  # noqa: BLE001
        print(f"skip {name}: {exc}")


app.after(400, lambda: (grab("14_运行设置_开机自启.png"),
                        dialog.cancel(),
                        app.after(200, app.destroy)))
app.mainloop()
