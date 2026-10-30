# -*- coding: utf-8 -*-
"""像素猫素材：从 images/像素猫-canvas绘制.html 内联的调色板/索引图生成。

191×160 网格、130 色。地图用 zlib + base64 压缩，渲染时再按主题
把中性色映射到 ink / paper；耳朵、项圈、鼻子等暖色保留参考图原色，
也可传入 accent / nose 整体移入主题色系（见 themed_palette）。
外圈背景默认随 paper 着色，render(transparent=True) 可把与画布边缘
连通的那片背景置为透明（BG_MASK），眼白 / 高光不受影响。
"""
from __future__ import annotations

import base64
import colorsys
import zlib
from collections import deque
from functools import lru_cache

from PIL import Image, ImageDraw

GRID_W = 191
GRID_H = 160

PALETTE = (
    (255, 255, 255), (10, 10, 8), (4, 4, 3), (251, 210, 182), (250, 170, 101), (250, 250, 250),
    (245, 245, 245), (33, 33, 32), (209, 209, 209), (234, 234, 234), (168, 168, 168), (21, 21, 21),
    (14, 14, 14), (81, 81, 81), (214, 214, 214), (153, 153, 152), (74, 74, 74), (189, 189, 189),
    (219, 220, 219), (23, 17, 12), (30, 19, 16), (225, 226, 226), (27, 27, 26), (173, 174, 174),
    (95, 95, 94), (54, 54, 53), (48, 48, 47), (194, 194, 194), (128, 128, 128), (239, 239, 239),
    (137, 137, 137), (86, 86, 86), (123, 123, 122), (103, 104, 103), (62, 62, 62), (40, 41, 40),
    (158, 159, 159), (43, 36, 31), (246, 205, 178), (126, 104, 93), (214, 180, 158), (51, 39, 34),
    (141, 142, 142), (114, 114, 114), (198, 199, 199), (40, 26, 21), (19, 10, 7), (245, 195, 152),
    (252, 194, 148), (69, 69, 68), (245, 164, 96), (204, 204, 204), (227, 158, 101), (189, 132, 88),
    (180, 180, 180), (86, 70, 60), (95, 77, 67), (196, 160, 138), (114, 94, 81), (210, 174, 148),
    (238, 199, 170), (72, 59, 50), (167, 139, 123), (141, 117, 101), (152, 124, 106), (98, 82, 73),
    (156, 133, 118), (58, 47, 41), (182, 154, 138), (201, 170, 148), (173, 149, 132), (105, 86, 75),
    (226, 188, 162), (189, 161, 142), (134, 112, 98), (65, 54, 45), (179, 146, 127), (120, 98, 87),
    (231, 193, 167), (187, 153, 131), (249, 214, 190), (109, 109, 108), (47, 29, 24), (58, 33, 28),
    (66, 39, 31), (134, 103, 79), (222, 174, 134), (148, 113, 89), (74, 43, 34), (92, 69, 52),
    (145, 106, 83), (159, 120, 92), (38, 20, 16), (210, 159, 123), (188, 141, 108), (220, 167, 127),
    (120, 87, 67), (79, 57, 44), (148, 147, 147), (166, 151, 140), (203, 135, 80), (124, 82, 52),
    (102, 63, 41), (253, 243, 233), (249, 162, 88), (193, 124, 74), (222, 149, 90), (132, 84, 53),
    (142, 92, 57), (236, 160, 96), (181, 125, 85), (188, 125, 81), (248, 175, 109), (154, 103, 66),
    (106, 73, 48), (238, 166, 108), (88, 60, 43), (202, 136, 88), (235, 170, 117), (133, 90, 62),
    (143, 99, 68), (169, 119, 83), (198, 140, 96), (73, 51, 36), (114, 76, 52), (155, 110, 79),
    (201, 146, 103), (81, 53, 37), (13, 4, 2), (208, 145, 94),
)

MAP_ZLIB_B64 = (
    "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"
)
MAP = zlib.decompress(base64.b64decode(MAP_ZLIB_B64))


def _nearest_index(rgb):
    r, g, b = rgb
    return min(
        range(len(PALETTE)),
        key=lambda i: (PALETTE[i][0] - r) ** 2
        + (PALETTE[i][1] - g) ** 2
        + (PALETTE[i][2] - b) ** 2,
    )


def _quad_curve(x1, x2, y, dip):
    mid = (x1 + x2) / 2
    points = []
    for step in range(17):
        t = step / 16
        x = (1 - t) ** 2 * x1 + 2 * (1 - t) * t * mid + t ** 2 * x2
        yy = (1 - t) ** 2 * y + 2 * (1 - t) * t * (y + dip) + t ** 2 * y
        points.append((round(x), round(yy)))
    return points


def _build_blink_map():
    """把两只眼睛所在区域压平，再画两条闭眼弧线，生成眨眼索引图。"""
    image = Image.frombytes("P", (GRID_W, GRID_H), MAP)
    draw = ImageDraw.Draw(image)
    black = _nearest_index((10, 10, 8))
    white = _nearest_index((255, 255, 255))
    # 眼白实测范围：左 75..94 / 右 110..129，纵向 58..73；向外留 2–3px。
    draw.rectangle((72, 54, 97, 75), fill=black)
    draw.rectangle((107, 54, 132, 75), fill=black)
    for x1, x2 in ((76, 93), (111, 128)):
        draw.line(_quad_curve(x1, x2, 66, 10), fill=white, width=2, joint="curve")
    return image.tobytes()


BLINK_MAP = _build_blink_map()


def _surround_mask() -> bytes:
    """外圈背景掩码：与画布边缘连通的 index-0 像素记 255，其余记 0。

    眼白 / 瞳孔高光也可能取 index 0，但它们是猫脸上与边缘不连通的小块，
    记 0 保留不透明——把外圈置透明时不会把眼睛掏空。眨眼帧的外圈与
    睁眼帧是同一片连通区域，两个帧共用这一份掩码。
    """
    width, height = GRID_W, GRID_H
    mask = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()
    for x in range(width):
        for y in (0, height - 1):
            index = y * width + x
            if MAP[index] == 0 and not mask[index]:
                mask[index] = 255
                queue.append((x, y))
    for y in range(height):
        for x in (0, width - 1):
            index = y * width + x
            if MAP[index] == 0 and not mask[index]:
                mask[index] = 255
                queue.append((x, y))
    while queue:
        x, y = queue.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                       (1, 1), (1, -1), (-1, 1), (-1, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < width and 0 <= ny < height:
                index = ny * width + nx
                if not mask[index] and MAP[index] == 0:
                    mask[index] = 255
                    queue.append((nx, ny))
    return bytes(mask)


BG_MASK = _surround_mask()


def _hex_rgb(color: str):
    value = color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a, b, amount):
    return tuple(
        max(0, min(255, round(a[i] + (b[i] - a[i]) * amount)))
        for i in range(3)
    )


@lru_cache(maxsize=32)
def themed_palette(ink: str, paper: str,
                   accent: str | None = None, nose: str | None = None):
    """按主题重映射调色板。

    中性色按亮度在 paper→ink 之间渐变（暗部近 ink、亮部近 paper）。
    暖色（项圈 / 内耳 / 鼻子 / 腮红）默认保留参考图原色；传入 accent 后
    改按亮度移入主题色系：项圈一档用 accent，鼻子 / 内耳等最亮的暖色用
    nose 向 paper 提亮，暗部暖色混一点 ink——冷暖分档各自连续，参考图的
    深浅结构不会丢。accent 为 None 时暖色原样保留（兼容旧调用）。
    """
    ink_rgb = _hex_rgb(ink)
    paper_rgb = _hex_rgb(paper)
    accent_rgb = _hex_rgb(accent) if accent else None
    nose_rgb = _hex_rgb(nose) if nose else accent_rgb
    warm_dark = _mix(ink_rgb, accent_rgb, 0.30) if accent_rgb else None
    result = []
    for rgb in PALETTE:
        r, g, b = rgb
        _h, sat, _v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
        if sat < 0.15 or max(rgb) - min(rgb) < 12:      # 中性色
            result.append(_mix(paper_rgb, ink_rgb, 1 - lum))
        elif accent_rgb is None:                        # 未传 accent：暖色留原色
            result.append(rgb)
        elif lum >= 0.78:                               # 鼻子 / 内耳 / 暖高光
            result.append(_mix(
                nose_rgb, paper_rgb, min(1.0, (lum - 0.78) / 0.22) * 0.85))
        elif lum >= 0.50:                               # 项圈及其明暗过渡
            result.append(_mix(warm_dark, accent_rgb, (lum - 0.50) / 0.28))
        else:                                           # 暗部暖色：混一点 ink
            result.append(warm_dark)
    return tuple(result)


def render(ink: str, paper: str, size: tuple[int, int], blink: bool = False,
           accent: str | None = None, nose: str | None = None,
           transparent: bool = False):
    """生成指定主题、指定显示尺寸的像素猫图片。

    ink / paper 负责中性色（猫身 / 眼白 / 背景），accent / nose 负责暖色
    （项圈 / 内耳 / 鼻子）；blink=True 换闭眼索引图；transparent=True 时
    外圈背景置为透明（返回 RGBA，眼白 / 高光仍不透明），默认返回 RGB。
    """
    palette = themed_palette(ink, paper, accent, nose)
    image = Image.frombytes(
        "P", (GRID_W, GRID_H), BLINK_MAP if blink else MAP)
    flat = [value for rgb in palette for value in rgb]
    flat.extend([0] * (768 - len(flat)))
    image.putpalette(flat)
    rgb = image.convert("RGB").resize(size, Image.Resampling.NEAREST)
    if not transparent:
        return rgb
    alpha = Image.frombytes("L", (GRID_W, GRID_H), BG_MASK).point(
        lambda v: 255 - v).resize(size, Image.Resampling.NEAREST)
    rgb.putalpha(alpha)
    return rgb
