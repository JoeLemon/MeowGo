# -*- coding: utf-8 -*-
"""预览功能目检：对话框按钮行 + 预览窗渲染。

用法：C:/Python310/python.exe tests/shot_preview.py
产出：tests/ui_dialog_preview.png（按钮行）、tests/ui_preview_dialog.png（预览窗）
"""
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa import gui as gui_module  # noqa: E402
from rpa.engine import Project, Step  # noqa: E402

OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    app = gui_module.App()
    app.project = Project(groups=[])
    app.update()

    # 造一张「大截图」尺寸的图（1600×900，会被缩小到上限内），预览效果直观
    from PIL import Image

    image_dir = os.path.join(OUT_DIR, "_shot_preview_dir")
    os.makedirs(image_dir, exist_ok=True)
    path = os.path.join(image_dir, "preview_demo.png")
    image = Image.new("RGB", (1600, 900))
    random.seed(20241020)
    image.putdata([
        (random.randrange(90, 220), random.randrange(120, 220),
         random.randrange(180, 255))
        for _ in range(1600 * 900)])
    image.save(path)
    app.image_dir = image_dir
    app.project.image_dir = image_dir

    def shot(name, window):
        app.update()
        time.sleep(1.2)                     # 等动画/置顶稳定
        try:
            import pyautogui

            pyautogui.screenshot(region=(window.winfo_rootx(),
                                         window.winfo_rooty(),
                                         window.winfo_width(),
                                         window.winfo_height())
                                 ).save(os.path.join(OUT_DIR, name))
            print(f"saved {name} "
                  f"({window.winfo_width()}x{window.winfo_height()})")
        except Exception as exc:  # noqa: BLE001
            print(f"skip {name}: {exc}")

    # 1) 对话框按钮行：截图 / 选图 / 预览 排在一起
    dialog = gui_module.StepDialog(app, app.project, Step("click", ""))
    dialog.var_content.set(os.path.basename(path))
    shot("ui_dialog_preview.png", dialog)

    # 2) 真预览窗
    preview = gui_module.ImagePreviewDialog(app, path)
    shot("ui_preview_dialog.png", preview)
    preview.cancel()
    dialog.cancel()
    app.destroy()


if __name__ == "__main__":
    main()
