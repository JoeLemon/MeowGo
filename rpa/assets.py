# -*- coding: utf-8 -*-
"""图片素材辅助：区域截图、缩略图、路径解析。"""
from __future__ import annotations

import os
import time

SUPPORTED_EXT = (".png", ".jpg", ".jpeg", ".bmp")


def resolve_image(name: str, base_dir: str | None = None) -> str:
    """把步骤里写的图片名解析成可用的绝对路径（找不到就原样返回）。

    会依次尝试：图片目录下的原名 → 相对当前工作目录 → 图片目录的上级目录
    → 图片目录下只取文件名。这样新旧两种写法都能识别：
    新写入的是相对图片目录的名字（`shot_1.png`），旧配置里可能带子目录（`images/shot_1.png`）。
    """
    if not name:
        return name
    if os.path.isabs(name):
        return name
    base = os.path.abspath(base_dir or os.getcwd())
    candidates = [
        os.path.join(base, name),
        os.path.abspath(name),
        os.path.join(os.path.dirname(base), name),
        os.path.join(base, os.path.basename(name)),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return os.path.normpath(candidate)
    return os.path.normpath(candidates[0])


def grab_region(x1: int, y1: int, x2: int, y2: int, path: str) -> str:
    """截取屏幕矩形并保存为 png。"""
    from PIL import ImageGrab

    box = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    image = ImageGrab.grab(bbox=box, all_screens=True)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    image.save(path)
    return path


def is_blank_image(path: str) -> bool:
    """图片是否只有一种颜色（纯色截图多半是没框到内容，匹配会到处命中）。"""
    from PIL import Image

    try:
        with Image.open(path) as image:
            colors = image.convert("RGB").getcolors(maxcolors=2)
    except Exception:  # noqa: BLE001 - 读不了就不下结论
        return False
    if not colors:
        return False
    return len(colors) == 1


def unique_name(directory: str, prefix: str = "shot") -> str:
    """在目录下生成不重复的图片文件名。"""
    os.makedirs(directory, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    name = f"{prefix}_{stamp}.png"
    index = 1
    while os.path.exists(os.path.join(directory, name)):
        name = f"{prefix}_{stamp}_{index}.png"
        index += 1
    return os.path.join(directory, name)
