# -*- coding: utf-8 -*-
"""界面小组件：矢量图标 + 圆角按钮 + 轻量提示。

图标用 PIL 在 24×24 网格上绘制后按 4 倍超采样缩放，避免 Tk 无矢量图标的短板；
圆角按钮用 Canvas 的 smooth 多边形实现，支持悬停/按下/禁用三种状态。
"""
from __future__ import annotations

import random
import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageTk

from . import i18n, pixel_cat, theme

GRID = 24          # 图标设计网格
SUPER = 4          # 超采样倍数
_cache: dict[tuple, ImageTk.PhotoImage] = {}


# ---------------------------------------------------------------------------
# 图标绘制
# ---------------------------------------------------------------------------

def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    if len(color) == 3:
        color = "".join(ch * 2 for ch in color)
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(v))) for v in rgb)


def _mix(color: str, other: str, amount: float) -> str:
    """把两种颜色按 amount 混合（0=color，1=other）。"""
    try:
        a, b = _hex_to_rgb(color), _hex_to_rgb(other)
    except (ValueError, IndexError):
        return color        # Tk 颜色名（如 white）无法直接解析时放弃阴影
    return _rgb_to_hex(tuple(a[i] + (b[i] - a[i]) * amount for i in range(3)))


class _ScaledDraw:
    """把 24×24 网格坐标等比放大到超采样画布上。

    这样所有图形只需按 24 网格写坐标，绘制时统一乘系数，线条也保持等比粗细。
    """

    def __init__(self, draw: ImageDraw.ImageDraw, factor: int):
        self._d = draw
        self._k = factor

    def _points(self, points):
        return [(p[0] * self._k, p[1] * self._k) for p in points]

    def _box(self, box):
        return [v * self._k for v in box]

    def _width(self, width):
        return max(1, int(round((width or 1) * self._k)))

    def polygon(self, points, fill=None, outline=None, width=1):
        self._d.polygon(self._points(points), fill=fill, outline=outline,
                        width=self._width(width))

    def line(self, points, fill=None, width=1, joint=None):
        self._d.line(self._points(points), fill=fill, width=self._width(width),
                     joint=joint)

    def ellipse(self, box, fill=None, outline=None, width=1):
        self._d.ellipse(self._box(box), fill=fill, outline=outline,
                        width=self._width(width))

    def arc(self, box, start, end, fill=None, width=1):
        self._d.arc(self._box(box), start, end, fill=fill,
                    width=self._width(width))

    def rounded_rectangle(self, box, radius=0, fill=None, outline=None, width=1):
        self._d.rounded_rectangle(self._box(box), radius=radius * self._k,
                                  fill=fill, outline=outline,
                                  width=self._width(width))

    def pieslice(self, box, start, end, fill=None, outline=None, width=1):
        box = self._box(box)
        if hasattr(self._d, "pieslice"):     # Pillow ≥ 8.2
            self._d.pieslice(box, start, end, fill=fill, outline=outline,
                             width=self._width(width))
        else:                                 # 旧版：chord 只支持填充
            self._d.chord(box, start, end, fill=fill, outline=outline)


def _polyline(draw: "_ScaledDraw", points, color, width):
    draw.line(points, fill=color, width=width, joint="curve")
    for point in points:  # 圆头
        r = width / 2
        draw.ellipse([point[0] - r, point[1] - r, point[0] + r, point[1] + r],
                     fill=color)


def _arrow(draw, tip, direction, size, color):
    x, y = tip
    if direction == "up":
        pts = [(x, y), (x - size, y + size), (x + size, y + size)]
    elif direction == "down":
        pts = [(x, y), (x - size, y - size), (x + size, y - size)]
    elif direction == "left":
        pts = [(x, y), (x + size, y - size), (x + size, y + size)]
    else:
        pts = [(x, y), (x - size, y - size), (x - size, y + size)]
    draw.polygon(pts, fill=color)


def _draw(name: str, draw: "_ScaledDraw", color, lw: int) -> None:
    c = color
    if name == "run":
        draw.polygon([(8, 5), (19, 12), (8, 19)], fill=c)
    elif name == "pause":
        draw.rounded_rectangle([7, 5, 11, 19], radius=1.5, fill=c)
        draw.rounded_rectangle([13, 5, 17, 19], radius=1.5, fill=c)
    elif name == "stop":
        draw.rounded_rectangle([6, 6, 18, 18], radius=2.5, fill=c)
    elif name == "loop":
        draw.arc([5, 5, 19, 19], start=40, end=330, fill=c, width=lw)
        _arrow(draw, (13.4, 5.6), "right", 3.4, c)
    elif name == "add":
        _polyline(draw, [(12, 5.5), (12, 18.5)], c, lw)
        _polyline(draw, [(5.5, 12), (18.5, 12)], c, lw)
    elif name == "copy":
        draw.rounded_rectangle([4, 4, 15, 15], radius=2, outline=c, width=lw)
        draw.rounded_rectangle([9, 9, 20, 20], radius=2, outline=c, width=lw)
    elif name == "trash":
        _polyline(draw, [(4, 7), (20, 7)], c, lw)
        _polyline(draw, [(9.5, 4.5), (14.5, 4.5)], c, lw)
        draw.rounded_rectangle([6, 7, 18, 20], radius=2, outline=c, width=lw)
        _polyline(draw, [(10, 10.5), (10, 16.5)], c, max(1, lw - 1))
        _polyline(draw, [(14, 10.5), (14, 16.5)], c, max(1, lw - 1))
    elif name == "up":
        _polyline(draw, [(6.5, 14.5), (12, 9), (17.5, 14.5)], c, lw)
    elif name == "down":
        _polyline(draw, [(6.5, 9.5), (12, 15), (17.5, 9.5)], c, lw)
    elif name == "folder":
        draw.polygon([(3, 19.5), (3, 6), (10, 6), (12, 8.5), (21, 8.5),
                      (21, 19.5)], fill=c)
    elif name == "folder_open":
        draw.polygon([(3, 19.5), (3, 6), (10, 6), (12, 8.5), (20, 8.5),
                      (20, 13)], fill=c)
        draw.polygon([(3, 19.5), (6, 12), (22, 12), (19, 19.5)], fill=c)
    elif name == "note":
        draw.rounded_rectangle([5.5, 3.5, 18.5, 20.5], radius=2.5,
                               outline=c, width=lw)
        for y in (8.5, 12, 15.5):
            _polyline(draw, [(9, y), (15, y)], c, max(1, lw - 1))
    elif name == "link":
        draw.rounded_rectangle([3.5, 8.5, 11.5, 15.5], radius=2.5,
                               outline=c, width=lw)
        _polyline(draw, [(12.5, 12), (18.5, 12)], c, lw)
        _arrow(draw, (20.5, 12), "right", 3.2, c)
    elif name == "image":
        draw.rounded_rectangle([3.5, 5.5, 20.5, 18.5], radius=2.5,
                               outline=c, width=lw)
        draw.ellipse([7, 8.5, 10.5, 12], fill=c)
        _polyline(draw, [(5.5, 17), (10, 12.5), (13, 15.5), (15.5, 13.5),
                         (19, 17)], c, lw)
    elif name == "clock":
        draw.ellipse([4, 4, 20, 20], outline=c, width=lw)
        _polyline(draw, [(12, 12), (12, 7.5)], c, max(1, lw - 1))
        _polyline(draw, [(12, 12), (16, 14)], c, max(1, lw - 1))
    elif name == "calendar":
        draw.rounded_rectangle([4, 6, 20, 20.5], radius=2.5, outline=c,
                               width=lw)
        _polyline(draw, [(4, 10.5), (20, 10.5)], c, lw)
        _polyline(draw, [(8.5, 4), (8.5, 8)], c, lw)
        _polyline(draw, [(15.5, 4), (15.5, 8)], c, lw)
    elif name == "keyboard":
        draw.rounded_rectangle([2.5, 7, 21.5, 17.5], radius=2.5, outline=c,
                               width=lw)
        for x in (6, 10, 14, 18):
            draw.rounded_rectangle([x - 1, 10.5, x + 1, 12.5], radius=0.8,
                                   fill=c)
        _polyline(draw, [(8, 15), (16, 15)], c, max(1, lw - 1))
    elif name == "list":
        for y in (6.5, 12, 17.5):
            draw.ellipse([3, y - 1.7, 6.4, y + 1.7], fill=c)
            _polyline(draw, [(9.5, y), (20.5, y)], c, lw)
    elif name == "mouse":
        draw.rounded_rectangle([7.5, 3, 16.5, 21], radius=4.25, outline=c,
                               width=lw)
        _polyline(draw, [(12, 7), (12, 11)], c, max(1, lw - 1))
    elif name == "text":
        _polyline(draw, [(5.5, 6), (18.5, 6)], c, lw)
        _polyline(draw, [(12, 6), (12, 19)], c, lw)
    elif name == "terminal":
        draw.rounded_rectangle([3, 5, 21, 19], radius=2.5, outline=c, width=lw)
        _polyline(draw, [(6.5, 9.5), (9.5, 12), (6.5, 14.5)], c, lw)
        _polyline(draw, [(12, 15), (17.5, 15)], c, lw)
    elif name == "scroll":
        draw.rounded_rectangle([8.5, 3.5, 15.5, 20.5], radius=3.5,
                               outline=c, width=lw)
        _polyline(draw, [(12, 7.5), (12, 16.5)], c, max(1, lw - 1))
        _arrow(draw, (12, 6.2), "up", 1.9, c)
        _arrow(draw, (12, 17.8), "down", 1.9, c)
    elif name == "pencil":
        draw.polygon([(4, 16.5), (4, 20), (7.5, 20), (19.5, 8), (16, 4.5)],
                     outline=c, width=1)
        _polyline(draw, [(14.5, 6), (18, 9.5)], c, max(1, lw - 1))
    elif name == "import":
        _polyline(draw, [(12, 4), (12, 14)], c, lw)
        _arrow(draw, (12, 16.5), "down", 3.2, c)
        _polyline(draw, [(4.5, 15), (4.5, 19.5), (19.5, 19.5), (19.5, 15)],
                  c, lw)
    elif name == "export":
        _polyline(draw, [(12, 14), (12, 4)], c, lw)
        _arrow(draw, (12, 2.5), "up", 3.2, c)
        _polyline(draw, [(4.5, 15), (4.5, 19.5), (19.5, 19.5), (19.5, 15)],
                  c, lw)
    elif name == "save":
        draw.rounded_rectangle([3.5, 3.5, 20.5, 20.5], radius=2.5,
                               outline=c, width=lw)
        draw.rounded_rectangle([8, 3.5, 16, 9.5], radius=1, fill=c)
        draw.rounded_rectangle([7, 14, 17, 20.5], radius=1.5, outline=c,
                               width=max(1, lw - 1))
    elif name == "open":
        _polyline(draw, [(4, 19), (4, 5.5), (10, 5.5), (12, 8), (19, 8)],
                  c, lw)
        _polyline(draw, [(4, 19), (22, 19)], c, lw)
    elif name == "table":
        draw.rounded_rectangle([3.5, 4.5, 20.5, 19.5], radius=2, outline=c,
                               width=lw)
        _polyline(draw, [(3.5, 9.5), (20.5, 9.5)], c, max(1, lw - 1))
        _polyline(draw, [(9.5, 9.5), (9.5, 19.5)], c, max(1, lw - 1))
        _polyline(draw, [(3.5, 14.5), (20.5, 14.5)], c, max(1, lw - 1))
    elif name == "chevron_down":
        _polyline(draw, [(7, 9.5), (12, 14.5), (17, 9.5)], c, lw)
    elif name == "chevron_right":
        _polyline(draw, [(9.5, 7), (14.5, 12), (9.5, 17)], c, lw)
    elif name == "chevron_up":
        _polyline(draw, [(7, 14.5), (12, 9.5), (17, 14.5)], c, lw)
    elif name == "close":
        _polyline(draw, [(6.5, 6.5), (17.5, 17.5)], c, lw)
        _polyline(draw, [(17.5, 6.5), (6.5, 17.5)], c, lw)
    elif name == "check":
        _polyline(draw, [(5, 12.5), (10, 17.5), (19, 7)], c, lw)
    elif name == "sliders":
        for y, x in ((7.5, 15), (12, 9), (16.5, 16)):
            _polyline(draw, [(4, y), (20, y)], c, max(1, lw - 1))
            draw.ellipse([x - 2.4, y - 2.4, x + 2.4, y + 2.4], fill=c)
    elif name == "cat":
        # 猫头：两只耳朵，脸用实心，眼睛挖空成留白
        draw.polygon([(3.6, 10.4), (5.4, 2.6), (11.2, 7.2)], fill=c)
        draw.polygon([(20.4, 10.4), (18.6, 2.6), (12.8, 7.2)], fill=c)
        draw.ellipse([3.8, 7.6, 20.2, 21.4], fill=c)
        draw.ellipse([7.9, 11.2, 10.1, 13.9], fill=(0, 0, 0, 0))
        draw.ellipse([13.9, 11.2, 16.1, 13.9], fill=(0, 0, 0, 0))
    elif name == "meow":
        # 声波（喵）
        for index, radius in enumerate((3.2, 6.2, 9.2)):
            draw.arc([12 - radius, 12 - radius, 12 + radius, 12 + radius],
                     start=-42, end=42, fill=c, width=max(1, lw - index))
        draw.ellipse([3.4, 9.6, 8.4, 14.4], fill=c)
    elif name == "info":
        draw.ellipse([4, 4, 20, 20], outline=c, width=lw)
        draw.ellipse([10.8, 7.2, 13.2, 9.6], fill=c)
        _polyline(draw, [(12, 11.5), (12, 16.5)], c, lw)
    elif name == "gear":
        draw.ellipse([8.5, 8.5, 15.5, 15.5], outline=c, width=lw)
        for angle in range(0, 360, 45):
            import math

            rad = math.radians(angle)
            x1, y1 = 12 + 8.6 * math.cos(rad), 12 + 8.6 * math.sin(rad)
            x2, y2 = 12 + 11 * math.cos(rad), 12 + 11 * math.sin(rad)
            _polyline(draw, [(x1, y1), (x2, y2)], c, lw + 1)
    elif name == "undo":
        # 标准回退图标：水平箭头指向左，右端向下弯出尾部
        _polyline(draw, [(6.5, 8.5), (15.5, 8.5)], c, lw)
        draw.arc([10.5, 8.5, 20.5, 18.5], start=270, end=360,
                 fill=c, width=lw)
        _arrow(draw, (3.5, 8.5), "left", 3.2, c)
    elif name == "redo":
        # 撤销的镜像：箭头指向右，左端向下弯出尾部
        _polyline(draw, [(8.5, 8.5), (17.5, 8.5)], c, lw)
        draw.arc([3.5, 8.5, 13.5, 18.5], start=180, end=270,
                 fill=c, width=lw)
        _arrow(draw, (20.5, 8.5), "right", 3.2, c)
    elif name == "search":
        draw.ellipse([4, 4, 15, 15], outline=c, width=lw)
        _polyline(draw, [(13.5, 13.5), (19.5, 19.5)], c, lw + 1)
    elif name == "eye":
        # 眼睛：上下两段半弧拼出杏仁轮廓，中间瞳孔
        draw.arc([3, 5.5, 21, 18.5], start=180, end=360, fill=c, width=lw)
        draw.arc([3, 5.5, 21, 18.5], start=0, end=180, fill=c, width=lw)
        draw.ellipse([9.6, 9.6, 14.4, 14.4], outline=c,
                     width=max(1, lw - 1))
        draw.ellipse([11.1, 11.1, 12.9, 12.9], fill=c)
    elif name == "moon":
        draw.pieslice([4, 4, 20, 20], start=60, end=300, fill=c)
        draw.pieslice([9.5, 1.5, 22.5, 14.5], start=200, end=80, fill=(0, 0, 0, 0))
    elif name == "sun":
        draw.ellipse([7.5, 7.5, 16.5, 16.5], outline=c, width=lw)
        for angle in range(0, 360, 45):
            import math

            rad = math.radians(angle)
            _polyline(draw,
                      [(12 + 6.4 * math.cos(rad), 12 + 6.4 * math.sin(rad)),
                       (12 + 9.6 * math.cos(rad), 12 + 9.6 * math.sin(rad))],
                      c, max(1, lw - 1))
    elif name == "question":
        draw.ellipse([4, 4, 20, 20], outline=c, width=lw)
        draw.arc([8.4, 7, 15.6, 14.2], start=-80, end=190, fill=c, width=lw)
        draw.ellipse([10.9, 14.6, 13.1, 16.8], fill=c)
    elif name == "target":
        draw.ellipse([4, 4, 20, 20], outline=c, width=lw)
        draw.ellipse([9, 9, 15, 15], outline=c, width=max(1, lw - 1))
        draw.ellipse([10.8, 10.8, 13.2, 13.2], fill=c)
    elif name == "debug":
        draw.rounded_rectangle([7, 6, 17, 19], radius=2.5, outline=c, width=lw)
        _polyline(draw, [(12, 9), (12, 12.5)], c, max(1, lw - 1))
        draw.ellipse([10.6, 13.4, 13.4, 16.2], fill=c)
        _polyline(draw, [(7, 8), (4, 6)], c, max(1, lw - 1))
        _polyline(draw, [(17, 8), (20, 6)], c, max(1, lw - 1))
        _polyline(draw, [(9, 19), (7, 21)], c, max(1, lw - 1))
        _polyline(draw, [(15, 19), (17, 21)], c, max(1, lw - 1))
    elif name == "paw":
        # 猫爪：掌垫 + 四个脚趾；和开关滑块使用同一套造型语言
        draw.ellipse([7.1, 11.5, 16.9, 19.2], fill=c)
        draw.ellipse([3.4, 6.9, 8.7, 12.1], fill=c)
        draw.ellipse([8.0, 3.9, 12.5, 9.0], fill=c)
        draw.ellipse([11.5, 3.9, 16.0, 9.0], fill=c)
        draw.ellipse([15.3, 6.9, 20.6, 12.1], fill=c)
    elif name == "more":
        for x in (6.5, 12.0, 17.5):
            draw.ellipse([x - 1.35, 10.65, x + 1.35, 13.35], fill=c)
    else:  # 兜底：方块
        draw.rounded_rectangle([6, 6, 18, 18], radius=2, fill=c)


def icon_image(name: str, size: int, color: str) -> Image.Image:
    """返回指定尺寸的图标 PIL 图像（RGBA）。"""
    canvas = Image.new("RGBA", (GRID * SUPER, GRID * SUPER), (0, 0, 0, 0))
    draw = _ScaledDraw(ImageDraw.Draw(canvas), SUPER)
    _draw(name, draw, _hex_to_rgb(color), 2)
    return canvas.resize((size, size), Image.LANCZOS)


def icon(name: str, size: int, color: str) -> ImageTk.PhotoImage:
    """带缓存的 PhotoImage。需先创建 Tk 根窗口。"""
    key = (name, size, color)
    if key not in _cache:
        _cache[key] = ImageTk.PhotoImage(icon_image(name, size, color))
    return _cache[key]


class CatCanvas(tk.Canvas):
    """空状态猫猫：像素猫图片版。

    像素数据来自 images/像素猫-canvas绘制.html（191×160 网格 / 130 色），
    由 rpa.pixel_cat 内联解码：中性色按主题在 paper→ink 间重映射，暖色
    （项圈 / 内耳 / 鼻子）移入 accent / nose 色系；眨眼切换 BLINK_MAP
    重绘，呼吸浮动整体平移。外圈背景透明，透出 Canvas 底色。接口与旧
    矢量版一致：set_colors() 换主题、redraw() 重画、destroy() 停动画。
    """

    def __init__(self, parent, scale: float = 1.0,
                 bg: str | None = None, ink: str | None = None,
                 accent: str | None = None, nose: str | None = None,
                 **kwargs):
        bg = bg or theme.token("CARD")
        ink = ink or theme.token("TEXT")
        accent = accent or theme.token("ACCENT")
        nose = nose or theme.token("ACCENT_STRONG")
        self.ink = ink
        self.accent = accent
        self.nose = nose
        self.paper = bg
        self._k = max(0.2, float(scale))
        self._photos: dict[bool, ImageTk.PhotoImage] = {}
        self._image_item: Optional[int] = None
        super().__init__(
            parent,
            width=max(1, int(round(pixel_cat.GRID_W * self._k))),
            height=max(1, int(round(pixel_cat.GRID_H * self._k))),
            bg=bg, highlightthickness=0, bd=0, **kwargs)
        # 空状态小动画：轻微呼吸浮动 + 定时眨眼。after 任务在重绘 /
        # 销毁时取消，避免主题切换后叠加多个循环。
        self._bob_after: Optional[str] = None
        self._blink_after: Optional[str] = None
        self._open_after: Optional[str] = None
        self._bob_offset = 0
        self._eyes_hidden = False
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.redraw()

    def set_colors(self, *, bg: Optional[str] = None,
                   ink: Optional[str] = None, accent: Optional[str] = None,
                   nose: Optional[str] = None) -> None:
        """换主题时更新颜色并重绘（像素图按新主题重新量化）。"""
        if bg is not None:
            self.paper = bg
            self.configure(bg=bg)
        if ink is not None:
            self.ink = ink
        if accent is not None:
            self.accent = accent
        if nose is not None:
            self.nose = nose
        self.redraw()

    # ------------------------------------------------------- 空状态小动画
    def _cancel_after(self, attr: str) -> None:
        job = getattr(self, attr, None)
        setattr(self, attr, None)
        if job:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass                    # 窗口已经销毁 / 任务已执行完

    def _stop_animation(self) -> None:
        """取消所有 after 任务，并回到静止状态。"""
        for attr in ("_bob_after", "_blink_after", "_open_after"):
            self._cancel_after(attr)
        self._bob_offset = 0
        self._eyes_hidden = False

    def _start_animation(self) -> None:
        if not self.winfo_exists():
            return
        self._stop_animation()
        self._bob_after = self.after(1400, self._bob)
        self._blink_after = self.after(4200, self._blink)

    def _bob(self) -> None:
        """上下浮动 1–2px，像轻轻呼吸；不追求明显，避免抢内容焦点。"""
        self._bob_after = None
        if not self.winfo_exists():
            return
        step = max(1, int(round(2 * self._k)))
        target = 0 if self._bob_offset else -step
        delta = target - self._bob_offset
        self.move("cat", 0, delta)
        self._bob_offset = target
        self._bob_after = self.after(1500, self._bob)

    def _blink(self) -> None:
        """闭眼：整图换成 BLINK_MAP 帧，130ms 后睁开。"""
        self._cancel_after("_blink_after")
        if not self.winfo_exists() or self._eyes_hidden:
            return
        self._set_frame(blink=True)
        self._eyes_hidden = True
        self._open_after = self.after(130, self._open_eyes)

    def _open_eyes(self) -> None:
        self._cancel_after("_open_after")
        if not self.winfo_exists():
            return
        self._set_frame(blink=False)
        self._eyes_hidden = False
        self._blink_after = self.after(random.randint(3600, 6200), self._blink)

    def _set_frame(self, blink: bool) -> None:
        """把猫图换成睁眼 / 闭眼帧（尺寸不变，只换 image）。"""
        if self._image_item is None:
            return
        try:
            self.itemconfigure(self._image_item, image=self._photo_for(blink))
        except tk.TclError:
            pass                    # 控件已销毁 / 任务已执行完

    def _on_destroy(self, event=None) -> None:
        if event is None or getattr(event, "widget", None) is self:
            self._stop_animation()

    def destroy(self) -> None:  # noqa: D401 - tkinter override
        self._stop_animation()
        super().destroy()

    def _photo_for(self, blink: bool) -> ImageTk.PhotoImage:
        """按当前主题色渲染一帧像素猫（眨眼帧换 BLINK_MAP），带缓存防 GC。

        外圈背景置为透明（RGBA），透出下层 Canvas 底色；眼白 / 高光保留
        不透明，不会把眼睛掏空。
        """
        photo = self._photos.get(blink)
        if photo is None:
            size = (max(1, int(round(pixel_cat.GRID_W * self._k))),
                    max(1, int(round(pixel_cat.GRID_H * self._k))))
            image = pixel_cat.render(self.ink, self.paper, size, blink=blink,
                                     accent=self.accent, nose=self.nose,
                                     transparent=True)
            photo = ImageTk.PhotoImage(image)
            self._photos[blink] = photo
        return photo

    def redraw(self) -> None:
        """按当前主题色重画整只猫：一张像素图，眨眼/浮动动画都在它上面做。"""
        self._stop_animation()
        self.delete("all")
        self._photos = {}                # 颜色变了，旧帧全部作废
        self._image_item = self.create_image(
            0, 0, anchor="nw", image=self._photo_for(False), tags=("cat",))
        self._start_animation()


# ---------------------------------------------------------------------------
# 圆角按钮
# ---------------------------------------------------------------------------

def round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kwargs):
    """在 Canvas 上画圆角矩形（用平滑多边形近似）。"""
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class RoundedCard(tk.Frame):
    """圆角卡片：外层用 Canvas 画圆角底与描边，内容放进 .body。

    卡片尺寸仍由内容撑开（普通 Frame 的几何传播），Canvas 只作为背景，
    因此可以直接替换原来的 tk.Frame，不需要改成固定尺寸。
    """

    def __init__(self, master, radius: int = theme.RADIUS_L,
                 bg: str | None = None, border: str | None = None,
                 outer_bg: str | None = None, padding: int = 6,
                 shadow: bool = True, **kwargs):
        bg = bg or theme.token("CARD")
        border = border or theme.token("BORDER")
        if outer_bg is None:
            try:
                outer_bg = str(master.cget("bg"))
            except tk.TclError:
                outer_bg = theme.token("BG")
        super().__init__(master, bg=bg, bd=0, highlightthickness=0, **kwargs)
        self.radius = int(radius)
        self.card_bg = bg
        self.border = border
        self.outer_bg = outer_bg
        self.padding = int(padding)
        self.shadow = bool(shadow)
        self._canvas = tk.Canvas(self, bg=outer_bg, highlightthickness=0, bd=0)
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg=bg, bd=0, highlightthickness=0)
        self.body.pack(fill="both", expand=True,
                       padx=self.padding, pady=self.padding)
        self._shape = None
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _event=None) -> None:
        width, height = self.winfo_width(), self.winfo_height()
        if width < 4 or height < 4:
            return
        self._canvas.delete("card")
        # 低饱和柔和阴影：Tk 没有 alpha，用外侧底色混一点墨黑模拟 shadow-1。
        if self.shadow:
            shadow = _mix(self.outer_bg, theme.INK, 0.08)
            round_rect(
                self._canvas, 3, 3, width - 1, height - 1,
                min(self.radius, (width - 4) / 2, (height - 4) / 2),
                fill=shadow, outline="", tags="card")
        self._shape = round_rect(
            self._canvas, 1, 1, width - 3, height - 3,
            min(self.radius, (width - 4) / 2, (height - 4) / 2),
            fill=self.card_bg, outline=self.border, width=1, tags="card")

    def set_colors(self, bg: str | None = None, border: str | None = None,
                   outer_bg: str | None = None) -> None:
        """主题切换：同步卡片、描边与圆角外侧的底色。"""
        if bg is not None:
            self.card_bg = bg
            self.body.configure(bg=bg)
            self.configure(bg=bg)
        if border is not None:
            self.border = border
        if outer_bg is not None:
            self.outer_bg = outer_bg
            self._canvas.configure(bg=outer_bg)
        self._redraw()


class TextField(tk.Frame):
    """圆角文本输入框：外层圆角描边 + 内部无边框 Entry。

    这是 v1.3 的统一输入组件：描边只由外层 ``RoundedCard`` 画一次，
    内部 ttk Entry 使用 ``Field.TEntry``（borderwidth=0），因此不会再出现
    「外层圆角框 + 内层方框」的双边框。用法与 Entry 基本兼容，常用方法
    （get / insert / delete / configure / bind / focus_set / select_range）
    都转发到内部 ``entry``。
    """

    def __init__(self, master, textvariable: tk.StringVar | None = None,
                 width: int = 20, scale: float = 1.0, bg: str | None = None,
                 font_size: int = theme.FONT_SIZE_NOTE,
                 padding: int | None = None, radius: int | None = None,
                 show: str | None = None, state: str = "normal"):
        bg = bg or theme.token("CARD")
        super().__init__(master, bg=bg)
        self.scale = scale
        self.bg = bg
        self.var = textvariable if textvariable is not None else tk.StringVar()
        pad = max(2, int(round(2 * scale))) if padding is None else padding
        radius = max(6, int(round(theme.RADIUS_S * scale))) \
            if radius is None else radius
        self._card = RoundedCard(
            self, radius=radius, bg=theme.token("ENTRY_BG"),
            border=theme.token("BORDER"), outer_bg=bg, padding=pad,
            shadow=False)
        self._card.pack(fill="both", expand=True)
        self.body = self._card.body
        entry_opts = {
            "textvariable": self.var, "width": width,
            "style": "Field.TEntry",
            "font": (theme.FONT_FAMILY, font_size), "state": state,
        }
        if show is not None:
            entry_opts["show"] = show
        self.entry = ttk.Entry(self.body, **entry_opts)
        self.entry.pack(side="left", fill="both", expand=True)
        self.entry.bind("<FocusIn>", self._on_focus_in, add="+")
        self.entry.bind("<FocusOut>", self._on_focus_out, add="+")
        # 点在外层圆角留白上时也把光标送进内部 Entry。
        super().bind("<Button-1>", lambda _event: self.entry.focus_set(),
                     add="+")

    def _on_focus_in(self, _event=None) -> None:
        self._card.set_colors(border=theme.token("FIELD_FOCUS"))

    def _on_focus_out(self, _event=None) -> None:
        self._card.set_colors(border=theme.token("BORDER"))

    def set_colors(self, bg: str | None = None, border: str | None = None,
                   outer_bg: str | None = None) -> None:
        if outer_bg is not None:
            self.bg = outer_bg
            super().configure(bg=outer_bg)
        if bg is not None:
            self._card.set_colors(bg=bg)
        if border is not None:
            self._card.set_colors(border=border)
        if outer_bg is not None:
            self._card.set_colors(outer_bg=outer_bg)

    def set_bg(self, color: str) -> None:
        self.bg = color
        super().configure(bg=color)
        self._card.set_colors(bg=theme.token("ENTRY_BG"),
                              border=theme.token("BORDER"),
                              outer_bg=color)

    # -- Entry 兼容接口 -----------------------------------------------------
    def get(self) -> str:
        return self.entry.get()

    def set(self, value: str) -> None:
        self.entry.delete(0, "end")
        self.entry.insert(0, str(value))

    def insert(self, index, text):
        return self.entry.insert(index, text)

    def delete(self, first, last=None):
        return self.entry.delete(first, last)

    def icursor(self, index):
        return self.entry.icursor(index)

    def index(self, index):
        return self.entry.index(index)

    def select_range(self, start, end):
        return self.entry.select_range(start, end)

    def select_clear(self):
        return self.entry.select_clear()

    def focus_set(self):
        return self.entry.focus_set()

    def bind(self, sequence=None, func=None, add=None):
        return self.entry.bind(sequence, func, add)

    def configure(self, **kwargs):
        entry_keys = ("state", "foreground", "show", "font",
                      "textvariable", "justify")
        entry_opts = {key: kwargs.pop(key) for key in entry_keys
                      if key in kwargs}
        if entry_opts:
            if "textvariable" in entry_opts:
                self.var = entry_opts["textvariable"]
            self.entry.configure(**entry_opts)
        if kwargs:
            super().configure(**kwargs)

    config = configure

    def cget(self, key: str):
        if key in ("state", "foreground", "show", "font", "textvariable",
                   "justify"):
            return self.entry.cget(key)
        return super().cget(key)

    def __getattr__(self, name):
        entry = self.__dict__.get("entry")
        if entry is not None:
            return getattr(entry, name)
        raise AttributeError(name)


def style_window(window: tk.Misc, dark: bool | None = None,
                 rounded: bool = True) -> None:
    """Windows 11 原生窗口圆角 + 深色标题栏（老系统静默忽略）。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(window.winfo_id())
        parent = ctypes.windll.user32.GetParent(hwnd)
        if parent:
            hwnd = parent
        dwm = ctypes.windll.dwmapi
        if rounded:
            pref = ctypes.c_int(2)          # DWMWCP_ROUND
            dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref),
                                      ctypes.sizeof(pref))
        if dark is not None:
            flag = ctypes.c_int(1 if dark else 0)
            dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(flag),
                                      ctypes.sizeof(flag))
    except Exception:  # noqa: BLE001 — 非 Win11 / 无 DWM 时保持系统默认
        pass


class SlimScrollbar(tk.Canvas):
    """参考图风格的细滚动条：浅色圆角轨道 + 深墨圆角滑块 + 白色猫爪。

    接口与 ``ttk.Scrollbar`` 保持兼容（``set`` / ``get`` / ``command``），
    因此可以直接替换 Treeview、Text、Canvas、Listbox 的原生滚动条。
    竖向滑块较矮时自动收成纯色药丸，避免猫爪被压扁。
    """

    def __init__(self, master, orient: str = "vertical", command=None,
                 *, width: int = 12, bg: str | None = None,
                 variant: str = "default", scale: float = 1.0):
        self.orient = "horizontal" if str(orient).startswith("h") else "vertical"
        self.command = command
        self.variant = variant
        self.scale = scale
        self.thickness = max(8, int(round(width * scale)))
        if bg is None:
            try:
                bg = str(master.cget("bg"))
            except tk.TclError:
                bg = theme.token("CARD")
        self.bg = bg
        self._first = 0.0
        self._last = 1.0
        self._drag_offset = 0.0
        self._dragging = False
        self._hover = False
        self._paw_photo = None
        if self.orient == "vertical":
            super().__init__(master, width=self.thickness, bg=bg,
                             highlightthickness=0, bd=0, takefocus=0,
                             cursor="arrow")
        else:
            super().__init__(master, height=self.thickness, bg=bg,
                             highlightthickness=0, bd=0, takefocus=0,
                             cursor="arrow")
        self.bind("<Configure>", self._redraw)
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<MouseWheel>", self._on_wheel)

    # -- ttk.Scrollbar 兼容接口 -------------------------------------------
    def set(self, first, last) -> None:
        try:
            first, last = float(first), float(last)
        except (TypeError, ValueError):
            first, last = 0.0, 1.0
        self._first = max(0.0, min(1.0, first))
        self._last = max(self._first, min(1.0, last))
        self._redraw()

    def get(self):
        return self._first, self._last

    def configure(self, **kwargs):
        command = kwargs.pop("command", None)
        if command is not None:
            self.command = command
        bg = kwargs.pop("bg", None)
        if bg is not None:
            self.bg = str(bg)
        return super().configure(bg=self.bg, **kwargs)

    config = configure

    # -- 绘制 -------------------------------------------------------------
    def _extent(self) -> tuple[int, int, int, int]:
        """返回轨道可用区域与圆角。"""
        pad = max(2, int(round(2 * self.scale)))
        if self.orient == "vertical":
            w = max(1, self.winfo_width())
            h = max(1, self.winfo_height())
            return pad, pad, w - pad, h - pad
        w = max(1, self.winfo_width())
        h = max(1, self.winfo_height())
        return pad, pad, w - pad, h - pad

    def _colors(self) -> tuple[str, str, str]:
        if self.variant == "console":
            return (theme.token("LOG_BG"), theme.token("CONSOLE_SCROLL"),
                    theme.token("LOG_HEAD_FG"))
        return (theme.token("SCROLL_TRACK"), theme.token("SCROLL_THUMB"),
                theme.token("SCROLL_THUMB_FG"))

    def _redraw(self, _event=None) -> None:
        self.delete("all")
        self._paw_photo = None
        if self.winfo_width() < 3 or self.winfo_height() < 3:
            return
        track, thumb, paw_fg = self._colors()
        x1, y1, x2, y2 = self._extent()
        radius = max(2, min(self.thickness / 2 - 1,
                            (y2 - y1) / 2 if self.orient == "vertical"
                            else (x2 - x1) / 2))
        if self.orient == "vertical":
            cx = (x1 + x2) / 2
            tx1, tx2 = cx - (self.thickness - 2 * 2) / 2, \
                cx + (self.thickness - 2 * 2) / 2
            round_rect(self, tx1, y1, tx2, y2, radius, fill=track,
                       outline="", tags=("track",))
        else:
            cy = (y1 + y2) / 2
            ty1, ty2 = cy - (self.thickness - 2 * 2) / 2, \
                cy + (self.thickness - 2 * 2) / 2
            round_rect(self, x1, ty1, x2, ty2, radius, fill=track,
                       outline="", tags=("track",))

        visible = self._last - self._first
        if visible >= 0.9995:
            return
        if self.orient == "vertical":
            track_len = max(1.0, y2 - y1)
            min_len = max(20.0, self.thickness * 1.7)
            thumb_len = max(min_len, visible * track_len)
            top = y1 + self._first * track_len
            bottom = top + thumb_len
            if bottom > y2:
                top, bottom = y2 - thumb_len, y2
            cx = (x1 + x2) / 2
            pad = max(1.0, 1.0 * self.scale)
            half = max(2.0, (self.thickness - 2 * 2) / 2 - pad)
            fill = _mix(thumb, "#FFFFFF", 0.10) if self._hover else thumb
            round_rect(self, cx - half, top, cx + half, bottom,
                       min(half, (bottom - top) / 2), fill=fill,
                       outline="", tags=("thumb",))
            paw_size = int(max(7, min(half * 1.55, thumb_len * 0.50)))
            if bottom - top >= paw_size + 8:
                self._paw_photo = icon("paw", paw_size, paw_fg)
                self.create_image(cx, (top + bottom) / 2,
                                  image=self._paw_photo, tags=("thumb",))
        else:
            track_len = max(1.0, x2 - x1)
            min_len = max(20.0, self.thickness * 1.7)
            thumb_len = max(min_len, visible * track_len)
            left = x1 + self._first * track_len
            right = left + thumb_len
            if right > x2:
                left, right = x2 - thumb_len, x2
            cy = (y1 + y2) / 2
            pad = max(1.0, 1.0 * self.scale)
            half = max(2.0, (self.thickness - 2 * 2) / 2 - pad)
            fill = _mix(thumb, "#FFFFFF", 0.10) if self._hover else thumb
            round_rect(self, left, cy - half, right, cy + half,
                       min(half, (right - left) / 2), fill=fill,
                       outline="", tags=("thumb",))

    def _thumb_span(self) -> tuple[float, float, float, float]:
        """返回滑块在当前画布上的起止位置（像素）。"""
        x1, y1, x2, y2 = self._extent()
        visible = max(0.0, self._last - self._first)
        if self.orient == "vertical":
            track_len = max(1.0, y2 - y1)
            thumb_len = max(max(20.0, self.thickness * 1.7),
                            visible * track_len)
            top = y1 + self._first * track_len
            if top + thumb_len > y2:
                top = y2 - thumb_len
            return y1, y2, top, top + thumb_len
        track_len = max(1.0, x2 - x1)
        thumb_len = max(max(20.0, self.thickness * 1.7), visible * track_len)
        left = x1 + self._first * track_len
        if left + thumb_len > x2:
            left = x2 - thumb_len
        return x1, x2, left, left + thumb_len

    # -- 交互 -------------------------------------------------------------
    def _on_enter(self, _event=None) -> None:
        self._hover = True
        self._redraw()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._redraw()

    def _on_press(self, event) -> str:
        if self.command is None or self._last - self._first >= 0.9995:
            return "break"
        start, end, lo, hi = self._thumb_span()
        pos = event.y if self.orient == "vertical" else event.x
        self._dragging = True
        if lo <= pos <= hi:
            self._drag_offset = pos - lo
        else:
            self._drag_offset = (hi - lo) / 2
            self._jump(pos - self._drag_offset)
        return "break"

    def _on_drag(self, event) -> str:
        if not self._dragging or self.command is None:
            return "break"
        pos = event.y if self.orient == "vertical" else event.x
        self._jump(pos - self._drag_offset)
        return "break"

    def _on_release(self, _event=None) -> None:
        self._dragging = False

    def _jump(self, thumb_start: float) -> None:
        start, end, _lo, _hi = self._thumb_span()
        track_len = max(1.0, end - start)
        before = max(0.0, self._first)
        visible = self._last - self._first
        # 滑块中心落在点击位置；命令按标准 scrollbar 的 moveto 协议发送
        target = max(0.0, min(1.0 - visible,
                               (thumb_start - start) / track_len))
        if abs(target - before) > 1e-6:
            self.command("moveto", str(target))

    def _on_wheel(self, event) -> str:
        if self.command is not None:
            self.command("scroll", -1 if event.delta > 0 else 1, "units")
        return "break"

    def set_bg(self, color: str) -> None:
        self.bg = color
        self.configure(bg=color)
        self._redraw()

    def refresh(self) -> None:
        self._redraw()


class Tooltip:
    """鼠标悬停提示。"""

    def __init__(self, widget: tk.Widget, text: str):
        self.widget = widget
        self._source_text = text
        self.text = i18n.tr(text)
        self.tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def refresh_language(self) -> None:
        """语言切换后按源文案重新翻译（提示条可能正显示，先收起再更新）。"""
        self.text = i18n.tr(self._source_text)
        self._hide()

    def set_source(self, text: str) -> None:
        """按钮文字变化时同步提示条源文案。"""
        self._source_text = text
        self.text = i18n.tr(text)
        self._hide()

    def _show(self, _event=None) -> None:
        if self.tip or not self.text:
            return
        self.tip = tk.Toplevel(self.widget)
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        label = tk.Label(self.tip, text=self.text,
                         bg=theme.token("LOG_BG"), fg=theme.token("LOG_FG"),
                         padx=9, pady=5, font=theme.FONT_NOTE, bd=0)
        label.pack()
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        rect = monitor_work_area(x, y)
        if rect is None:            # 非 Windows：退回主屏
            x = max(0, x - 40)
        else:                       # 提示条也要留在鼠标所在的那块屏里
            x, y = max(x - 40, rect[0]), max(y, rect[1])
        self.tip.geometry(f"+{int(x)}+{int(y)}")

    def _hide(self, _event=None) -> None:
        if self.tip:
            self.tip.destroy()
            self.tip = None


# ---------------------------------------------------------------------------
# 自绘右键菜单（与界面同风格：圆角卡片 + 图标 + 悬停高亮）
# ---------------------------------------------------------------------------

MENU_CARD = theme.token("CARD")
MENU_BORDER = theme.token("BORDER")
MENU_HOVER = theme.token("ACCENT_MIST")
MENU_HOVER_DANGER = theme.token("MARK_FAILED")
MENU_TEXT = theme.token("TEXT")
MENU_MUTED = theme.token("TEXT_WEAK")
MENU_DANGER = theme.token("DANGER")
MENU_ACCENT = theme.token("ACCENT_STRONG")


def _refresh_menu_tokens() -> None:
    """把主题令牌同步到菜单模块常量（菜单是即用即建，改常量即可）。"""
    dark = theme.is_dark()
    globals().update(
        MENU_CARD=theme.token("CARD"),
        MENU_BORDER=theme.token("BORDER"),
        MENU_HOVER=theme.token("ACCENT_MIST"),
        MENU_HOVER_DANGER=theme.token("MARK_FAILED"),
        MENU_TEXT=theme.token("TEXT"),
        MENU_MUTED=theme.token("TEXT_WEAK"),
        MENU_DANGER=theme.token("DANGER"),
        MENU_ACCENT=theme.token("ACCENT") if dark
        else theme.token("ACCENT_STRONG"),
    )


def set_dark_mode(on: bool) -> None:
    """切换暗色配色（全局生效；已有控件需各自 refresh 才会重画）。"""
    theme.set_dark(bool(on))
    _refresh_menu_tokens()


TRANSPARENT_KEY = "#ff00fe"   # 用于抠掉圆角外的区域

# 多屏坐标：菜单必须落在鼠标所在的那块屏幕里（副屏在主屏左边时是负坐标，
# 右边时超出主屏尺寸，所以一律用 Windows 的显示器信息而不是 Tk 的主屏尺寸）
_monitor_api = None


def _load_monitor_api():
    """准备 MonitorFromPoint / GetMonitorInfoW，失败返回 None。"""
    global _monitor_api
    if _monitor_api is None:
        try:
            import ctypes

            # 用 wintypes.POINT：自定义同名结构体虽字段一致，但 ctypes 按
            # 类型对象比对 argtypes，会和别处（pyautogui / tray）冲突
            POINT = ctypes.wintypes.POINT

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                            ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

            user32 = ctypes.windll.user32
            user32.MonitorFromPoint.argtypes = [POINT, ctypes.c_ulong]
            user32.MonitorFromPoint.restype = ctypes.c_void_p
            user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p,
                                               ctypes.POINTER(MONITORINFO)]
            user32.GetMonitorInfoW.restype = ctypes.c_bool
            _monitor_api = (user32, POINT, MONITORINFO)
        except Exception:  # noqa: BLE001 — 非 Windows / 调用失败：走兜底
            _monitor_api = False
    return _monitor_api or None


def monitor_work_area(x: int, y: int):
    """包含 (x, y) 的显示器工作区 (left, top, right, bottom)，取不到返回 None。

    多屏时取鼠标所在那块屏，并排除任务栏占用的区域。
    """
    api = _load_monitor_api()
    if not api:
        return None
    user32, POINT, MONITORINFO = api
    try:
        import ctypes

        monitor = user32.MonitorFromPoint(POINT(int(x), int(y)), 2)  # 最近的屏
        if not monitor:
            return None
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        work = info.rcWork
        return (int(work.left), int(work.top), int(work.right),
                int(work.bottom))
    except Exception:  # noqa: BLE001
        return None


def desktop_bounds():
    """所有屏幕合成的虚拟桌面矩形 (left, top, right, bottom)，取不到返回 None。"""
    try:
        import ctypes

        metric = ctypes.windll.user32.GetSystemMetrics
        left, top = metric(76), metric(77)      # SM_X/YVIRTUALSCREEN
        width, height = metric(78), metric(79)  # SM_CX/CYVIRTUALSCREEN
        if width <= 0 or height <= 0:
            return None
        return (int(left), int(top), int(left + width), int(top + height))
    except Exception:  # noqa: BLE001
        return None


class MenuItem:
    """一条菜单项；label 为 "-" 时表示分隔线。"""

    def __init__(self, label: str = "", icon: str | None = None, command=None,
                 shortcut: str = "", enabled: bool = True, danger: bool = False,
                 accent: bool = False):
        self.label = label
        self.icon = icon
        self.command = command
        self.shortcut = shortcut
        self.enabled = enabled
        self.danger = danger
        self.accent = accent

    @property
    def is_separator(self) -> bool:
        return self.label.strip() in ("-", "---")

    def color(self, muted: bool = False) -> str:
        if not self.enabled or muted:
            return MENU_MUTED
        if self.danger:
            return MENU_DANGER
        if self.accent:
            return MENU_ACCENT
        return MENU_TEXT


class ContextMenu(tk.Toplevel):
    """圆角右键菜单，支持图标、快捷键提示、分隔线与禁用项。

    用法：
        ContextMenu(master, scale, [MenuItem(...), "-", MenuItem(...)]).popup(x, y)
    """

    def __init__(self, master, scale: float, items, min_width: int = 168,
                 font_size: int = 9):
        super().__init__(master)
        self.withdraw()
        self.overrideredirect(True)
        self.scale = scale

        def s(value: float) -> int:
            return int(round(value * scale))

        self._s = s
        self.items: list[MenuItem] = []
        for item in items:
            if isinstance(item, MenuItem):
                self.items.append(item)
            elif isinstance(item, str):
                self.items.append(MenuItem(item))
        self.font = tkfont.Font(family=theme.FONT_FAMILY, size=font_size)

        self.row_h = s(31)
        self.pad_y = s(6)
        self.pad_x = s(11)
        self.icon_size = s(15)
        self.gap = s(9)
        self.radius = s(10)

        width = s(min_width)
        for item in self.items:
            if item.is_separator:
                continue
            need = self.pad_x * 2 + self.icon_size + self.gap + \
                self.font.measure(i18n.tr(item.label))
            if item.shortcut:
                need += self.gap * 2 + self.font.measure(item.shortcut)
            width = max(width, need)
        height = self.pad_y * 2 + sum(
            s(9) if item.is_separator else self.row_h for item in self.items)

        self._width, self._height = width, height
        self._rows: list[dict] = []
        self._hover = -1
        self._press = -1
        self._closed = False
        self._armed = False
        self._poll_id = None
        self.on_close = None          # 关闭后的回调（菜单栏用它恢复按钮样式）
        self.on_outside_right = None  # 在菜单外点右键时的回调（x_root, y_root）

        bg = TRANSPARENT_KEY
        try:
            self.configure(bg=bg)
            self.wm_attributes("-transparentcolor", bg)
        except tk.TclError:          # 非 Windows 或不支持：退回白底
            bg = MENU_CARD
            self.configure(bg=bg)

        self.canvas = tk.Canvas(self, width=width, height=height, bg=bg,
                                highlightthickness=0, bd=0)
        self.canvas.pack()
        # 用「外层填充 + 内层白底」做出 1px 边框，避免描边在圆角处留下杂边
        round_rect(self.canvas, 0.5, 0.5, width - 0.5, height - 0.5,
                   self.radius, fill=MENU_BORDER, outline="")
        round_rect(self.canvas, 1.5, 1.5, width - 1.5, height - 1.5,
                   max(1, self.radius - 1), fill=MENU_CARD, outline="")

        y = self.pad_y
        for item in self.items:
            if item.is_separator:
                line_y = y + s(4)
                self.canvas.create_line(self.pad_x, line_y, width - self.pad_x,
                                        line_y, fill=MENU_BORDER)
                y += s(9)
                continue
            y1, y2 = y, y + self.row_h
            middle = (y1 + y2) / 2
            highlight = round_rect(self.canvas, s(5), y1 + s(2),
                                   width - s(5), y2 - s(2), s(8), fill="",
                                   outline="")
            color = item.color()
            image = icon(item.icon, self.icon_size, color) if item.icon else None
            text_id = self.canvas.create_text(
                self.pad_x + self.icon_size + self.gap, middle,
                text=i18n.tr(item.label), anchor="w", fill=color, font=self.font)
            icon_id = None
            if image is not None:
                icon_id = self.canvas.create_image(
                    self.pad_x + self.icon_size / 2, middle, image=image,
                    anchor="center")
            shortcut_id = None
            if item.shortcut:
                shortcut_id = self.canvas.create_text(
                    width - self.pad_x, middle, text=item.shortcut, anchor="e",
                    fill=MENU_MUTED, font=self.font)
            self._rows.append({"y1": y1, "y2": y2, "item": item,
                               "highlight": highlight, "text": text_id,
                               "icon": icon_id, "shortcut": shortcut_id,
                               "image": image})
            y = y2

        # 绑定在 Toplevel 上：抓取状态下点在菜单外的点击会送到窗口本身，
        # 而画布只覆盖窗口内部（画布的 bindtags 里也含 Toplevel，故菜单内事件同样能收到）
        self.bind("<Motion>", self._on_motion)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<ButtonPress-3>", self._on_right_press)
        self.bind("<Escape>", lambda e: self.close())
        self.bind("<FocusOut>", lambda e: self.close())

    # -- 显示 -------------------------------------------------------------
    def popup(self, x: int, y: int) -> "ContextMenu":
        self.update_idletasks()
        x, y = self._clamp(int(x), int(y))
        # 尺寸一起写进 geometry：多屏/负坐标下也不会被系统挪到别处
        self.geometry(f"{self._width}x{self._height}+{x}+{y}")
        self.deiconify()
        self.lift()
        try:
            self.attributes("-topmost", True)
            self.grab_set()
        except tk.TclError:
            pass
        self.focus_force()
        self._watch()
        return self

    def _clamp(self, x: int, y: int) -> tuple[int, int]:
        """把菜单放进鼠标所在的那块屏幕（多屏时允许负坐标、右侧副屏）。"""
        rect = monitor_work_area(x, y) or desktop_bounds()
        if rect is None:            # 非 Windows：退回 Tk 的主屏尺寸
            rect = (0, 0, self.winfo_screenwidth(), self.winfo_screenheight())
        left, top, right, bottom = rect
        margin = self._s(6)
        return (min(max(x, left), max(left, right - self._width - margin)),
                min(max(y, top), max(top, bottom - self._height - margin)))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._poll_id is not None:
            try:
                self.after_cancel(self._poll_id)
            except tk.TclError:
                pass
            self._poll_id = None
        try:
            self.grab_release()
        except tk.TclError:
            pass
        callback, self.on_close = self.on_close, None
        try:
            self.destroy()
        except tk.TclError:
            pass
        if callback:
            callback()

    # -- 点其它地方也能关掉 -------------------------------------------------
    def _watch(self) -> None:
        """轮询鼠标左右键：即使点在别的程序窗口上（本地抓取管不到），也能收起菜单。

        刚弹出时按键可能还按着（右键菜单就是从按下开始弹的），所以先等一次
        「两个键都抬起」再开始判定。用 GetAsyncKeyState 的低位判断「自上次查询
        以来按下过」，快速点击也不会漏。在菜单外按右键时，除了收起本菜单，
        还会把这次右键转交给下面的界面（于是能就地弹出新的菜单）。
        """
        if self._closed:
            return
        try:
            left, right = self._key_state()
            edge = bool(left & 0x0001) or bool(right & 0x0001)
            down = bool(left & 0x8000) or bool(right & 0x8000)
            if self._armed:
                if (edge or down) and not self._inside():
                    if right & 0x8001:          # 右键：收起并把这次右键转交出去
                        self._outside_right(*self._pointer_xy())
                    else:
                        self.close()
                    return
            elif not edge and not down:
                self._armed = True
            self._poll_id = self.after(50, self._watch)
        except tk.TclError:
            return

    @staticmethod
    def _key_state() -> tuple[int, int]:
        """(左键状态, 右键状态)；取不到时返回 (0, 0)。"""
        try:
            import ctypes

            get = ctypes.windll.user32.GetAsyncKeyState
            return (int(get(0x01)), int(get(0x02)))
        except Exception:  # noqa: BLE001
            return (0, 0)

    def _pointer_xy(self) -> tuple[int, int]:
        """鼠标的屏幕坐标，取不到时返回 (0, 0)。"""
        try:
            return self.winfo_pointerxy()
        except tk.TclError:
            return (0, 0)

    def _inside(self) -> bool:
        """鼠标是否还在菜单窗口内。"""
        try:
            x, y = self.winfo_pointerxy()
            left, top = self.winfo_rootx(), self.winfo_rooty()
            return (left <= x < left + self._width
                    and top <= y < top + self._height)
        except tk.TclError:
            return True

    # -- 交互 -------------------------------------------------------------
    def _index_at(self, x: int, y: int):
        if not (0 <= x < self._width and 0 <= y < self._height):
            return None
        for index, row in enumerate(self._rows):
            if row["y1"] <= y < row["y2"]:
                return index
        return None

    def _set_hover(self, index) -> None:
        if index == self._hover:
            return
        self._hover = index
        for position, row in enumerate(self._rows):
            item = row["item"]
            active = position == index and item.enabled
            if item.is_separator:
                continue
            if active:
                fill = MENU_HOVER_DANGER if item.danger else MENU_HOVER
            else:
                fill = ""
            self.canvas.itemconfigure(row["highlight"], fill=fill)
            color = item.color(muted=not item.enabled)
            if not item.enabled:
                color = MENU_MUTED
            elif active and item.danger:
                color = MENU_DANGER
            self.canvas.itemconfigure(row["text"], fill=color)
            if row["icon"] is not None:
                row["image"] = icon(item.icon, self.icon_size, color)
                self.canvas.itemconfigure(row["icon"], image=row["image"])

    def _on_motion(self, event) -> None:
        index = self._index_at(event.x, event.y)
        row = self._rows[index] if index is not None else None
        if row is not None and not row["item"].enabled:
            index = None
        self._set_hover(index)
        self.canvas.configure(cursor="hand2" if index is not None else "arrow")

    def _on_press(self, event) -> None:
        self._press = self._index_at(event.x, event.y)

    def _on_right_press(self, event) -> None:
        """在菜单外按右键：收起本菜单，并让下面的界面就地弹出自己的菜单。"""
        if 0 <= event.x < self._width and 0 <= event.y < self._height:
            return                  # 菜单内部右键：先不动
        self._outside_right(*self._pointer_xy())

    def _outside_right(self, x_root: int, y_root: int) -> None:
        """菜单外右键的收尾：先收起，再把坐标交给回调（可能是空操作）。"""
        if self._closed:
            return                  # 轮询已经先处理过了
        callback, self.on_outside_right = self.on_outside_right, None
        self.close()
        if callback:
            callback(int(x_root), int(y_root))

    def _on_release(self, event) -> None:
        index = self._index_at(event.x, event.y)
        pressed = self._press
        self._press = -1
        if index is None:
            self.close()          # 点在菜单外面 = 取消
            return
        row = self._rows[index]
        if not row["item"].enabled or index != pressed:
            return
        command = row["item"].command
        self.close()
        if command:
            command()


class RoundButton(tk.Canvas):
    """圆角按钮，支持图标 + 文字、悬停、按下、禁用。"""

    SOLID, OUTLINE, GHOST, DANGER, ACCENT = ("solid", "outline", "ghost",
                                             "danger", "accent")

    def __init__(self, master, text: str = "", icon_name: str | None = None,
                 command=None, kind: str = SOLID, scale: float = 1.0,
                 height: int = theme.CONTROL_MD, pad: tuple[int, int] = (14, 14),
                 icon_size: int = theme.ICON_MD, radius: int = theme.RADIUS_M,
                 tooltip: str = "", font_size: int = theme.FONT_SIZE_NOTE,
                 bg: str | None = None, bold: bool = False,
                 icon_color: str | None = None):
        if isinstance(pad, (int, float)):
            pad = (pad, pad)
        bg = bg or theme.token("BG")
        self.scale = scale
        self.kind = kind
        self.command = command
        self.icon_name = icon_name
        self.icon_color = icon_color
        self._mg_i18n_source = text
        self.text = i18n.tr(text)
        self.height = int(round(height * scale))
        self.radius = int(round(radius * scale))
        self.pad = (int(round(pad[0] * scale)), int(round(pad[1] * scale)))
        self.icon_size = int(round(icon_size * scale))
        self.gap = int(round(6 * scale)) if (icon_name and text) else 0
        self.font = tkfont.Font(family=theme.FONT_FAMILY,
                                size=font_size, weight="bold" if bold else "normal")
        self._enabled = True
        self._hover = False
        self._pressed = False

        text_w = self.font.measure(self.text) if self.text else 0
        width = self.pad[0] * 2 + text_w
        if icon_name:
            width += self.icon_size + (self.gap if text else 0)
        super().__init__(master, width=width, height=self.height, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._shape = round_rect(self, 0.5, 0.5, width - 0.5, self.height - 0.5,
                                 self.radius, fill=self._palette()[0], outline="")
        self._content: list[int] = []
        self._rebuild()

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._tip = Tooltip(self, tooltip or self._mg_i18n_source)
        self._tip_auto = not tooltip

    # -- 配色 -------------------------------------------------------------
    def _palette(self) -> tuple[str, str, str]:
        """返回 (背景色, 前景色, 描边色)；颜色全部来自 rpa.theme。"""
        return theme.button_colors(
            self.kind, str(self["bg"]), enabled=self._enabled,
            hover=self._hover, pressed=self._pressed)

    def _rebuild(self) -> None:
        fill, fg, border = self._palette()
        self.itemconfigure(self._shape, fill=fill, outline=border)
        # 纯图标 ghost 按钮静默时用次级文字色，悬停再回到主文字色：
        # 顶栏一排黑色图标会抢标题的注意力，弱化后层级更清楚。
        icon_fg = self.icon_color or fg
        if (self.icon_color is None and self.kind == "ghost" and not self.text
                and self._enabled and not self._hover and not self._pressed):
            icon_fg = (theme.token("LOG_HEAD_MUTED")
                       if theme.is_dark_color(str(self["bg"]))
                       else theme.token("MUTED"))
        for item in self._content:
            self.delete(item)
        self._content = []

        x = self.pad[0]
        if self.icon_name:
            image = icon(self.icon_name, self.icon_size, icon_fg)
            self._content.append(self.create_image(x, self.height // 2,
                                                   image=image, anchor="w"))
            self._icon_ref = image
            x += self.icon_size + self.gap
        if self.text:
            self._content.append(self.create_text(x, self.height // 2, text=self.text,
                                                  anchor="w", fill=fg,
                                                  font=self.font))
        self.configure(cursor="hand2" if self._enabled else "arrow")

    # -- 交互 -------------------------------------------------------------
    def _on_enter(self, _event=None) -> None:
        self._hover = True
        self._rebuild()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._pressed = False
        self._rebuild()

    def _on_press(self, _event=None) -> None:
        if not self._enabled:
            return
        self._pressed = True
        self._rebuild()

    def _on_release(self, event=None) -> None:
        if not self._enabled:
            return
        was = self._pressed
        self._pressed = False
        self._rebuild()
        inside = (event is None or
                  (0 <= event.x <= int(self["width"]) and
                   0 <= event.y <= self.height))
        if was and inside and self.command:
            self.command()

    # -- 外部接口 ---------------------------------------------------------
    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not enabled:
            self._hover = self._pressed = False
        self._rebuild()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_icon_color(self, color: str | None) -> None:
        """覆盖图标颜色（下拉字段的猫爪随焦点/选中态变深）。"""
        self.icon_color = color
        self._rebuild()

    def set_text(self, text: str, icon_name: str | None = None) -> None:
        self._mg_i18n_source = text
        self.text = i18n.tr(text)
        if getattr(self, "_tip_auto", False):
            self._tip.set_source(text)
        if icon_name is not None:
            self.icon_name = icon_name
        text_w = self.font.measure(self.text) if self.text else 0
        width = self.pad[0] * 2 + text_w
        if self.icon_name:
            self.gap = int(round(6 * self.scale)) if self.text else 0
            width += self.icon_size + self.gap
        self.configure(width=width)
        self.coords(self._shape, *self._shape_points(width))
        self._rebuild()

    def set_kind(self, kind: str) -> None:
        """切换按钮样式（菜单栏用它高亮当前展开的菜单）。"""
        if kind == self.kind:
            return
        self.kind = kind
        self._rebuild()

    def _shape_points(self, width: int):
        r = min(self.radius, self.height / 2, width / 2)
        return [0.5 + r, 0.5, width - 0.5 - r, 0.5, width - 0.5, 0.5,
                width - 0.5, 0.5 + r, width - 0.5, self.height - 0.5 - r,
                width - 0.5, self.height - 0.5, width - 0.5 - r,
                self.height - 0.5, 0.5 + r, self.height - 0.5, 0.5,
                self.height - 0.5, 0.5, self.height - 0.5 - r, 0.5, 0.5 + r,
                0.5, 0.5]

    def set_bg(self, color: str) -> None:
        self.configure(bg=color)
        self._rebuild()

    def refresh(self) -> None:
        """主题切换后按新配色重画（ ghost 按钮的底色取自父容器）。"""
        self._rebuild()


# ---------------------------------------------------------------------------
# 开关（switch）：替代 ttk.Checkbutton，视觉更贴近现代设置面板
# ---------------------------------------------------------------------------

class Switch(tk.Canvas):
    """圆角开关：点击切换绑定的 BooleanVar，也可由外部改值自动重画。

    视觉层级：轨道 + 描边 + 滑块 + 焦点环；开 / 关切换带短动画。
    尺寸按逻辑像素计算，DPI 缩放后仍保持一致的圆角和边距。
    """

    def __init__(self, master, variable: tk.BooleanVar, command=None,
                 scale: float = 1.0, bg: str | None = None,
                 tooltip: str = ""):
        bg = bg or theme.token("CARD")
        self.scale = scale
        self.variable = variable
        self.command = command
        self.track_w = max(46, int(round(48 * scale)))
        self.track_h = max(24, int(round(26 * scale)))
        self._margin = max(4, int(round(4 * scale)))
        self._pad = max(2, int(round(2 * scale)))
        super().__init__(master,
                         width=self.track_w + self._margin * 2,
                         height=self.track_h + self._margin * 2,
                         bg=bg, highlightthickness=0, bd=0,
                         cursor="hand2", takefocus=True)
        self._on = bool(variable.get())
        self._hover = False
        self._focused = False
        self._enabled = True
        self._anim_pos = 1.0 if self._on else 0.0
        self._anim_after: str | None = None
        self._track_item: int | None = None
        self._thumb_item: int | None = None
        self._track_key = None
        self._thumb_key = None
        self._track_photo: ImageTk.PhotoImage | None = None
        self._thumb_photo: ImageTk.PhotoImage | None = None
        self._trace = variable.trace_add("write", self._on_variable)
        self.bind("<Button-1>", self._toggle)
        self.bind("<space>", self._toggle)
        self.bind("<Return>", self._toggle)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<Destroy>", self._on_destroy)
        if tooltip:
            self._tip = Tooltip(self, tooltip)
        self._draw()

    # -- 绘制 -------------------------------------------------------------
    def _geometry(self) -> tuple[float, float, float, float, float, float]:
        """返回轨道左上 / 右下坐标与滑块半径、中心 Y。"""
        m = self._margin
        w, h = self.track_w, self.track_h
        left, top = m, m
        right, bottom = m + w, m + h
        knob = max(1.0, (h - self._pad * 2) / 2)
        cy = (top + bottom) / 2
        return left, top, right, bottom, knob, cy

    def _track_spec(self) -> dict:
        """当前状态对应的轨道 / 滑块配色。"""
        dark = theme.is_dark()
        if not self._enabled:
            top = bottom = theme.token("HEADING_BG")
            border = theme.token("BORDER")
            track_shadow = 70 if dark else 22
            thumb = "#F7F5F2" if not dark else "#38332D"
            thumb_border = theme.token("BORDER")
            paw = theme.token("BORDER_STRONG")
            thumb_shadow = 50 if dark else 18
        elif self._on:
            base = (theme.token("ACCENT_STRONG") if self._hover
                    else theme.token("ACCENT"))
            top = _mix(base, "#FFFFFF", 0.05 if dark else 0.12)
            bottom = _mix(base, "#000000", 0.10 if dark else 0.06)
            border = theme.token("ACCENT_STRONG")
            track_shadow = 82 if dark else 36
            thumb = "#F8F5F0" if dark else "#FFFFFF"
            thumb_border = _mix(thumb, "#000000", 0.07 if not dark else 0.0)
            if dark:
                thumb_border = "#4A443C"
            paw = theme.token("ACCENT_STRONG")
            thumb_shadow = 95 if dark else 42
        else:
            heading = theme.token("HEADING_BG")
            top = _mix(heading, "#FFFFFF", 0.04 if dark else 0.36)
            bottom = heading
            border = theme.token("BORDER_STRONG")
            if self._hover:
                top = _mix(top, theme.token("ACCENT_TINT"), 0.45)
                bottom = _mix(bottom, theme.token("ACCENT_TINT"), 0.35)
                border = theme.token("ACCENT")
            track_shadow = 76 if dark else 24
            thumb = "#F5F2ED" if dark else "#FFFFFF"
            thumb_border = "#4A443C" if dark else _mix(thumb, "#000000", 0.06)
            paw = "#7D756A" if dark else "#D8D3CB"
            thumb_shadow = 88 if dark else 38

        return {
            "top": top, "bottom": bottom, "border": border,
            "track_shadow": track_shadow, "thumb": thumb,
            "thumb_border": thumb_border, "paw": paw,
            "thumb_shadow": thumb_shadow,
            "focus": theme.token("ACCENT_STRONG"), "dark": dark,
        }

    def _track_image(self, spec: dict) -> ImageTk.PhotoImage:
        """生成带柔和阴影的轨道图（4 倍超采样后缩放）。"""
        cw = self.track_w + self._margin * 2
        ch = self.track_h + self._margin * 2
        key = (cw, ch, round(self.scale, 3), spec["top"], spec["bottom"],
               spec["border"], spec["track_shadow"], spec["focus"],
               self._focused and self._enabled)
        if key == self._track_key and self._track_photo is not None:
            return self._track_photo

        w, h = cw * SUPER, ch * SUPER
        m = self._margin * SUPER
        box = (m, m, (self._margin + self.track_w) * SUPER,
               (self._margin + self.track_h) * SUPER)
        radius = (self.track_h / 2) * SUPER
        image = Image.new("RGBA", (w, h), (0, 0, 0, 0))

        shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        offset = max(1, int(round(1.3 * self.scale * SUPER)))
        ImageDraw.Draw(shadow).rounded_rectangle(
            (box[0], box[1] + offset, box[2], box[3] + offset),
            radius=radius, fill=(0, 0, 0, spec["track_shadow"]))
        shadow = shadow.filter(ImageFilter.GaussianBlur(
            max(1.0, 1.8 * self.scale * SUPER)))
        image = Image.alpha_composite(image, shadow)

        # 竖向轻微渐变：浅色轨道更接近参考图，开启态保持品牌橙色
        gradient = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(gradient)
        top_rgb = _hex_to_rgb(spec["top"])
        bottom_rgb = _hex_to_rgb(spec["bottom"])
        for y in range(h):
            t = y / max(1, h - 1)
            color = tuple(int(top_rgb[i] + (bottom_rgb[i] - top_rgb[i]) * t)
                          for i in range(3))
            draw.line(((0, y), (w, y)), fill=color + (255,))
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
        gradient.putalpha(mask)
        image = Image.alpha_composite(image, gradient)

        draw = ImageDraw.Draw(image)
        width = max(1, int(round(1.0 * self.scale * SUPER)))
        draw.rounded_rectangle(box, radius=radius,
                               outline=spec["border"], width=width)
        if self._focused and self._enabled:
            gap = max(1, int(round(1.1 * self.scale * SUPER)))
            ring = max(1, int(round(1.2 * self.scale * SUPER)))
            ring_box = (box[0] - gap, box[1] - gap,
                        box[2] + gap, box[3] + gap)
            draw.rounded_rectangle(
                ring_box, radius=radius + gap,
                outline=_hex_to_rgb(spec["focus"]) + (210,), width=ring)

        image = image.resize((cw, ch), Image.LANCZOS)
        self._track_key = key
        self._track_photo = ImageTk.PhotoImage(image)
        return self._track_photo

    def _thumb_image(self, spec: dict, knob: float) -> ImageTk.PhotoImage:
        """生成带投影和猫爪图案的滑块图。"""
        shadow_pad = max(4, int(round(4 * self.scale)))
        size = max(8, int(round((knob + shadow_pad) * 2)))
        key = (size, round(knob, 2), spec["thumb"], spec["thumb_border"],
               spec["paw"], spec["thumb_shadow"], spec["dark"],
               self._enabled, self._on)
        if key == self._thumb_key and self._thumb_photo is not None:
            return self._thumb_photo

        w = h = size * SUPER
        center = w / 2
        r = knob * SUPER
        image = Image.new("RGBA", (w, h), (0, 0, 0, 0))

        shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        offset = max(1, int(round(1.3 * self.scale * SUPER)))
        ImageDraw.Draw(shadow).ellipse(
            (center - r, center - r + offset,
             center + r, center + r + offset),
            fill=(0, 0, 0, spec["thumb_shadow"]))
        shadow = shadow.filter(ImageFilter.GaussianBlur(
            max(1.0, 1.6 * self.scale * SUPER)))
        image = Image.alpha_composite(image, shadow)

        draw = ImageDraw.Draw(image)
        border = max(1, int(round(0.8 * self.scale * SUPER)))
        draw.ellipse((center - r, center - r, center + r, center + r),
                     fill=spec["thumb"], outline=spec["thumb_border"],
                     width=border)
        self._draw_paw(draw, center, center, r, spec["paw"])

        image = image.resize((size, size), Image.LANCZOS)
        self._thumb_key = key
        self._thumb_photo = ImageTk.PhotoImage(image)
        return self._thumb_photo

    @staticmethod
    def _draw_paw(draw: ImageDraw.ImageDraw, cx: float, cy: float,
                  radius: float, color: str) -> None:
        """在滑块中央画一枚简洁的猫爪。"""
        def ellipse(dx: float, dy: float, rx: float, ry: float) -> None:
            draw.ellipse((cx + (dx - rx) * radius, cy + (dy - ry) * radius,
                          cx + (dx + rx) * radius, cy + (dy + ry) * radius),
                         fill=color)

        # 掌垫：三个重叠椭圆形成轻微的爱心轮廓
        ellipse(0, 0.24, 0.34, 0.21)
        ellipse(-0.15, 0.16, 0.19, 0.17)
        ellipse(0.15, 0.16, 0.19, 0.17)
        # 四个脚趾：中间两枚更高，外侧两枚略低，留出细小间隙
        ellipse(-0.35, -0.16, 0.10, 0.14)
        ellipse(-0.12, -0.31, 0.10, 0.15)
        ellipse(0.12, -0.31, 0.10, 0.15)
        ellipse(0.35, -0.16, 0.10, 0.14)

    def _draw(self) -> None:
        left, top, right, bottom, knob, cy = self._geometry()
        spec = self._track_spec()
        track = self._track_image(spec)
        if self._track_item is None:
            self._track_item = self.create_image(
                (self.track_w + self._margin * 2) / 2,
                (self.track_h + self._margin * 2) / 2,
                image=track)
        else:
            self.itemconfigure(self._track_item, image=track)

        thumb = self._thumb_image(spec, knob)
        travel = max(0.0, self.track_w - self._pad * 2 - knob * 2)
        cx = left + self._pad + knob + travel * self._anim_pos
        if self._thumb_item is None:
            self._thumb_item = self.create_image(cx, cy, image=thumb)
        else:
            self.itemconfigure(self._thumb_item, image=thumb)
            self.coords(self._thumb_item, cx, cy)
        self.tag_raise(self._thumb_item)

    # -- 动画 -------------------------------------------------------------
    def _animate_to(self, target: float) -> None:
        if self._anim_after is not None:
            try:
                self.after_cancel(self._anim_after)
            except (tk.TclError, ValueError):
                pass
            self._anim_after = None
        start = self._anim_pos
        steps = 6

        def tick(step: int = 1) -> None:
            self._anim_after = None
            progress = min(1.0, step / steps)
            # ease-out cubic：快速起步，末尾收稳
            eased = 1 - (1 - progress) ** 3
            self._anim_pos = start + (target - start) * eased
            self._draw()
            if step < steps and self.winfo_exists():
                self._anim_after = self.after(14, lambda: tick(step + 1))
            else:
                self._anim_pos = target
                self._draw()

        if abs(target - start) < 0.01:
            self._anim_pos = target
            self._draw()
            return
        tick()

    # -- 交互 -------------------------------------------------------------
    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self._hover = self._focused = False
        self.configure(cursor="hand2" if self._enabled else "arrow")
        self._draw()

    def _toggle(self, _event=None) -> str:
        if not self._enabled:
            return "break"
        self.variable.set(not bool(self.variable.get()))
        if self.command:
            self.command()
        return "break"

    def _on_variable(self, *_args) -> None:
        on = bool(self.variable.get())
        if on == self._on:
            return
        self._on = on
        self._animate_to(1.0 if on else 0.0)

    def _on_enter(self, _event=None) -> None:
        if not self._enabled:
            return
        self._hover = True
        self._draw()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._draw()

    def _on_focus_in(self, _event=None) -> None:
        if not self._enabled:
            return
        self._focused = True
        self._draw()

    def _on_focus_out(self, _event=None) -> None:
        self._focused = False
        self._draw()

    def _on_destroy(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        if self._anim_after is not None:
            try:
                self.after_cancel(self._anim_after)
            except (tk.TclError, ValueError):
                pass
            self._anim_after = None
        try:
            self.variable.trace_remove("write", self._trace)
        except (tk.TclError, ValueError):
            pass

    # -- 外部接口 ---------------------------------------------------------
    def get(self) -> bool:
        return bool(self.variable.get())

    def set(self, value: bool) -> None:
        self.variable.set(bool(value))

    def set_bg(self, color: str) -> None:
        """主题切换：换底色后重画（轨道颜色取自全局暗色开关）。"""
        self.configure(bg=color)
        self._draw()


# ---------------------------------------------------------------------------
# 可搜索的下拉选择器（分组 + 模糊匹配）
# ---------------------------------------------------------------------------

class _ComboOption:
    """下拉选项：value 是内部值，label 是显示文字，group 是分组名。"""

    __slots__ = ("value", "label", "group", "source_label")

    def __init__(self, value: str, label: str, group: str = "",
                 source_label: str | None = None):
        self.value = value
        self.label = label
        self.group = group
        self.source_label = source_label if source_label is not None else label


def _fuzzy_match(query: str, text: str) -> bool:
    """模糊匹配：先看子串，再按顺序逐个字符找（支持跳字）。"""
    q = query.strip().lower()
    if not q:
        return True
    hay = text.lower()
    if q in hay:
        return True
    pos = -1
    for char in q:
        pos = hay.find(char, pos + 1)
        if pos < 0:
            return False
    return True


class _DropdownPanel(tk.Frame):
    """参考图风格的下拉面板：圆角浅灰底、分组标题、选中行灰色药丸。

    单独做成一个类，ReadonlySelect / SearchableCombo 共用同一套视觉；
    面板仍然内嵌在宿主窗口里（不是 Toplevel），方便点击外部自动收起。
    """

    def __init__(self, host, rows, selected_value, *, x: int, y: int,
                 width: int, height: int, scale: float, font: tkfont.Font,
                 outer_bg: str, on_pick, on_hover=None):
        super().__init__(host, bg=outer_bg, bd=0, highlightthickness=0)
        self.rows = list(rows)
        self.font = font
        self.scale = scale
        self.outer_bg = outer_bg
        self.on_pick = on_pick
        self.on_hover = on_hover
        self.pad = max(6, int(round(6 * scale)))
        self.row_h = max(int(round(30 * scale)),
                         int(font.metrics("linespace")) + int(round(14 * scale)))
        self.radius = max(8, int(round(14 * scale)))
        self.bar_w = max(8, int(round(10 * scale))) if rows else 0
        self.visible = max(1, (height - self.pad * 2) // self.row_h)
        self.top = 0
        self.selected_index = self._find(selected_value)
        self.active_index = self.selected_index
        if self.active_index is None:
            self.active_index = next(
                (i for i, (_t, value) in enumerate(self.rows)
                 if value is not None), None)
        self._scrollbar = None
        self._images = []
        self.canvas = tk.Canvas(self, bg=outer_bg, bd=0,
                                highlightthickness=0, width=width,
                                height=height)
        self.canvas.pack(fill="both", expand=True)
        if len(self.rows) > self.visible:
            self._scrollbar = SlimScrollbar(
                self, orient="vertical", command=self._on_scroll,
                width=self.bar_w, bg=theme.token("MENU_BG"), scale=scale)
            self._scrollbar.place(x=width - self.pad - self.bar_w,
                                  y=self.pad, width=self.bar_w,
                                  height=max(1, height - self.pad * 2))
            try:
                tk.Misc.lift(self._scrollbar)
            except tk.TclError:
                pass
        self._ensure_visible(self.active_index)
        # Canvas 内部的点击/悬停由面板自己处理；滚动条是子控件，不会抢错事件。
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Configure>", self._on_configure)
        self.after_idle(self._redraw)

    # -- 数据 -------------------------------------------------------------
    def _find(self, value) -> int | None:
        for index, (_text, row_value) in enumerate(self.rows):
            if row_value == value:
                return index
        return None

    def value_at(self, index: int):
        if 0 <= index < len(self.rows):
            return self.rows[index][1]
        return None

    def move(self, delta: int) -> None:
        if self.active_index is None or not self.rows:
            return
        index = self.active_index
        for _ in range(len(self.rows)):
            index = (index + delta) % len(self.rows)
            if self.value_at(index) is not None:
                break
        self.active_index = index
        self._ensure_visible(index)
        self._redraw()

    def set_active(self, index: int | None) -> None:
        self.active_index = index
        self._redraw()

    def _ensure_visible(self, index: int | None) -> None:
        if index is None:
            return
        if index < self.top:
            self.top = index
        elif index >= self.top + self.visible:
            self.top = index - self.visible + 1
        self.top = max(0, min(self.top, max(0, len(self.rows)
                                              - self.visible)))

    # -- 滚动 -------------------------------------------------------------
    def _on_scroll(self, *args) -> None:
        if not self.rows:
            return
        if args and args[0] == "moveto":
            self.top = int(round(float(args[1]) * len(self.rows)))
        elif args and args[0] == "scroll":
            step = int(args[1])
            self.top += step
        max_top = max(0, len(self.rows) - self.visible)
        self.top = max(0, min(self.top, max_top))
        self._redraw()

    def _on_wheel(self, event) -> str:
        if len(self.rows) > self.visible:
            self.top += -1 if event.delta > 0 else 1
            max_top = max(0, len(self.rows) - self.visible)
            self.top = max(0, min(self.top, max_top))
            self._redraw()
        return "break"

    # -- 交互 -------------------------------------------------------------
    def _index_at(self, event_y: int) -> int | None:
        if event_y < self.pad:
            return None
        index = self.top + (event_y - self.pad) // self.row_h
        if self.top <= index < min(len(self.rows), self.top + self.visible):
            return index
        return None

    def _on_click(self, event) -> str:
        index = self._index_at(event.y)
        value = self.value_at(index) if index is not None else None
        if value is not None:
            self.on_pick(value)
        return "break"

    def _on_motion(self, event) -> None:
        index = self._index_at(event.y)
        if index is None:
            return
        if self.value_at(index) is None:
            return
        if index != self.active_index:
            self.active_index = index
            self._redraw()
            if self.on_hover is not None:
                value = self.value_at(index)
                if value is not None:
                    self.on_hover(value)

    def _on_configure(self, _event=None) -> None:
        # 宽度 / 高度变化都重画；首帧画布很窄时，选中行药丸会只画出一小截。
        height = max(1, self.winfo_height())
        visible = max(1, (height - self.pad * 2) // self.row_h)
        if visible != self.visible:
            self.visible = visible
        self._redraw()

    # -- ?? 绘制 -------------------------------------------------------------
    def _redraw(self) -> None:
        try:
            width = max(3, self.canvas.winfo_width())
            height = max(3, self.canvas.winfo_height())
        except tk.TclError:
            return
        self.canvas.delete("all")
        self._images = []
        shadow = _mix(self.outer_bg, "#000000", 0.10)
        round_rect(self.canvas, 2, 3, width - 2, height - 1,
                   self.radius, fill=shadow, outline="")
        round_rect(self.canvas, 1, 1, width - 3, height - 3,
                   self.radius, fill=theme.token("MENU_BG"),
                   outline=theme.token("MENU_BORDER"), width=1)
        row_right = width - self.pad
        if self._scrollbar is not None:
            row_right -= self.bar_w + self.pad
        for index in range(self.top, min(len(self.rows),
                                         self.top + self.visible)):
            text, value = self.rows[index]
            y = self.pad + (index - self.top) * self.row_h
            cy = y + self.row_h / 2
            if value is None:
                self.canvas.create_text(
                    self.pad + int(round(8 * self.scale)), cy,
                    text=text, anchor="w", fill=theme.token("TEXT_WEAK"),
                    font=self.font)
                continue
            selected = index == self.selected_index
            active = index == self.active_index
            if selected or active:
                fill = (theme.token("MENU_ROW_SELECTED") if selected
                        else theme.token("MENU_ROW_HOVER"))
                round_rect(self.canvas, self.pad, y + 2, row_right,
                           y + self.row_h - 2,
                           max(4, int(round(8 * self.scale))),
                           fill=fill, outline="")
            text_x = self.pad + int(round(12 * self.scale))
            if selected:
                size = max(14, int(round(18 * self.scale)))
                left = self.pad + int(round(8 * self.scale))
                check = icon("check", max(9, int(round(size * 0.78))),
                             theme.token("TEXT"))
                self._images.append(check)
                self.canvas.create_image(left + size / 2, cy, image=check)
                text_x = left + size + int(round(6 * self.scale))
            color = theme.token("TEXT")
            self.canvas.create_text(text_x, cy, text=text, anchor="w",
                                    fill=color, font=self.font)
        if self._scrollbar is not None:
            self._scrollbar.set(
                self.top / max(1, len(self.rows)),
                min(1.0, (self.top + self.visible) / max(1, len(self.rows))))


class SearchableCombo(tk.Frame):
    """既可下拉选择、又能输入关键词过滤的输入框（支持分组标题）。

    用法：
        combo = SearchableCombo(master, [(key, label, "基础"), ...],
                                variable=var, command=on_select)
        combo.get()        # 当前显示文字
        combo.value        # 当前选中项的内部值（未选过为空）
        combo.set(key)     # 按内部值 / 显示文字选中
    """

    def __init__(self, master, options, variable: tk.StringVar | None = None,
                 command=None, scale: float = 1.0, bg: str | None = None,
                 font_size: int = theme.FONT_SIZE_NOTE, width: int = 22,
                 readonly: bool = False, shell: bool = False):
        bg = bg or theme.token("CARD")
        super().__init__(master, bg=bg)
        self.scale = scale
        self.bg = bg
        self.command = command
        self.readonly = bool(readonly)
        self.font = tkfont.Font(family=theme.FONT_FAMILY, size=font_size)
        self.font_size = font_size
        self._items = []
        for item in options:
            option = _ComboOption(*item) if isinstance(item, tuple) else item
            self._items.append(_ComboOption(
                option.value, i18n.tr(option.label), i18n.tr(option.group),
                source_label=option.label))
        self._group_order: list[str] = []
        for option in self._items:
            if option.group and option.group not in self._group_order:
                self._group_order.append(option.group)
        if any(not option.group for option in self._items):
            self._group_order.insert(0, "")
        self.var = variable if variable is not None else tk.StringVar()
        self._value = ""
        self._popup: tk.Frame | None = None
        self._listbox: tk.Listbox | None = None
        self._panel: _DropdownPanel | None = None
        self._rows: list[tuple[str, str | None]] = []
        self._enabled = True
        self._height = max(theme.CONTROL_XS,
                           int(round(theme.CONTROL_SM * scale)))
        self._suppress_focus = False
        self._outside_bind: str | None = None

        # shell=True 时控件自带参考图同款圆角字段，供只读下拉和独立使用；
        # 步骤对话框保留原来的外层容器，避免重复描边。
        self._shell = None
        field_parent = self
        field_bg = bg
        field_pad = max(1, int(round(2 * scale)))
        if shell:
            self._shell = RoundedCard(
                self, radius=max(6, int(round(theme.RADIUS_S * scale))),
                bg=theme.token("ENTRY_BG"), border=theme.token("BORDER"),
                outer_bg=bg, padding=field_pad, shadow=False)
            self._shell.pack(fill="both", expand=True)
            field_parent = self._shell.body
            field_bg = theme.token("ENTRY_BG")

        self.entry = ttk.Entry(
            field_parent, textvariable=self.var, width=width,
            style="Field.TEntry", font=(theme.FONT_FAMILY, font_size),
            state="readonly" if self.readonly else "normal")
        self.entry.pack(side="left", fill="x", expand=True)
        self.btn = RoundButton(field_parent, "", "paw", self._toggle_popup,
                               kind="ghost", scale=scale, bg=field_bg,
                               height=self._height, pad=theme.SPACE_4,
                               icon_size=theme.ICON_SM,
                               tooltip=i18n.tr("展开 / 收起选项"))
        self.btn.pack(side="right", padx=(2, 0))
        self.entry.bind("<KeyRelease>", self._on_key)
        self.entry.bind("<Down>", self._on_down)
        self.entry.bind("<Up>", self._on_up)
        self.entry.bind("<Return>", self._on_return)
        self.entry.bind("<KP_Enter>", self._on_return)
        self.entry.bind("<Escape>", self._on_escape)
        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)
        if self._shell is not None:
            self.entry.bind(
                "<FocusIn>",
                lambda e: self._shell.set_colors(
                    border=theme.token("FIELD_FOCUS")), add="+")
            self.entry.bind(
                "<FocusOut>",
                lambda e: self._shell.set_colors(
                    border=theme.token("BORDER")), add="+")
        self.bind("<Destroy>", self._on_destroy)
        self._sync_value_from_var()
        self._sync_paw()

    # -- 兼容 Combobox 的常用接口 ------------------------------------------
    def set(self, value: str) -> None:
        """按内部值或显示文字选中；带「1 · 」旧编号前缀也能识别。"""
        text = str(value).strip()
        if "·" in text:
            text = text.split("·", 1)[1].strip()
        for option in self._items:
            if text in (option.value, option.label, option.source_label):
                self._value = option.value
                self.var.set(option.label)
                self._sync_paw()
                return
        self.var.set(text)

    def get(self) -> str:
        return self.var.get()

    def _sync_value_from_var(self) -> None:
        text = str(self.var.get()).strip()
        for option in self._items:
            if text in (option.value, option.label, option.source_label):
                self._value = option.value
                return
        self._value = ""

    def _sync_paw(self, focused: bool = False) -> None:
        if not hasattr(self, "btn"):
            return
        if not self._enabled:
            color = theme.token("TEXT_WEAK")
        elif focused or self._value:
            color = theme.token("TEXT")
        else:
            color = theme.token("MUTED")
        self.btn.set_icon_color(color)

    @property
    def value(self) -> str:
        return self._value

    def cget(self, key: str):
        if key == "values":
            return [option.label for option in self._items]
        if key == "state":
            return "normal" if self._enabled else "disabled"
        return super().cget(key)

    def configure(self, **kwargs):
        state = kwargs.pop("state", None)
        if state is not None:
            self._enabled = str(state) != "disabled"
            entry_state = ("readonly" if self.readonly else "normal") \
                if self._enabled else "disabled"
            self.entry.configure(state=entry_state)
            self.btn.set_enabled(self._enabled)
            if not self._enabled:
                self._hide_popup()
            self._sync_paw()
        if kwargs:
            super().configure(**kwargs)

    config = configure

    def focus_set(self) -> None:  # type: ignore[override]
        self.entry.focus_set()

    def set_bg(self, color: str) -> None:
        """主题切换：同步外层字段与 paw 按钮的底色。"""
        self.bg = color
        self.configure(bg=color)
        if self._shell is not None:
            self._shell.set_colors(
                bg=theme.token("ENTRY_BG"), border=theme.token("BORDER"),
                outer_bg=color)
            self.btn.set_bg(theme.token("ENTRY_BG"))
        else:
            self.btn.set_bg(color)
        self._hide_popup()
        self._sync_paw()

    def refresh(self) -> None:
        self.set_bg(self.bg)

    # -- 下拉列表 ----------------------------------------------------------
    def _label_of(self, value: str) -> str:
        for option in self._items:
            if option.value == value:
                return option.label
        return ""

    def _rows_for(self, query: str) -> list[tuple[str, str | None]]:
        """按分组生成行；分组标题的 value 为 None（不可选）。"""
        rows: list[tuple[str, str | None]] = []
        for group in self._group_order:
            matched = [option for option in self._items
                       if option.group == group
                       and _fuzzy_match(query,
                                        f"{option.label} {option.group} "
                                        f"{option.value}")]
            if not matched:
                continue
            if group:
                rows.append((group, None))
            rows.extend((option.label, option.value) for option in matched)
        if not rows:                    # 没有分组的兜底 / 无匹配提示
            rows.append((i18n.tr("没有匹配的指令类型"), None))
        return rows

    def _toggle_popup(self) -> None:
        if self._popup is not None:
            self._hide_popup()
        else:
            # 点下拉箭头后把焦点放到输入框：接着打字就能模糊搜索
            self._suppress_focus = True
            try:
                self.entry.focus_set()
                self.entry.select_range(0, "end")
            except tk.TclError:
                pass
            self._show_popup("")
            self.after(150, self._release_focus_guard)

    def _popup_geometry(self) -> tuple[int, int, int, int]:
        """计算下拉面板在宿主窗口内的位置与高度（放不下就向上展开）。"""
        host = self.winfo_toplevel()
        line = self.font.metrics("linespace")
        row_h = max(int(round(30 * self.scale)),
                    line + int(round(14 * self.scale)))
        pad = max(6, int(round(6 * self.scale)))
        width = max(self.winfo_width(), int(round(240 * self.scale)))
        # 最多显示 6 行，其余滚动：保持参考图的轻量面板，也避免在较矮的
        # 步骤对话框里盖住下方备注输入框。
        desired = min(len(self._rows), 6) * row_h + pad * 2
        x = self.winfo_rootx() - host.winfo_rootx()
        below = self.winfo_rooty() - host.winfo_rooty() \
            + self.winfo_height() + 2
        above = self.winfo_rooty() - host.winfo_rooty() - 2
        host_h = host.winfo_height()
        if below + desired <= host_h:
            y, height = below, desired
        elif above - desired >= 0:
            y, height = above - desired, desired
        else:
            y = below
            height = max(row_h + pad * 2, host_h - y - 4)
        return x, y, width, height

    def _show_popup(self, query: str) -> None:
        if not self._enabled:
            return
        self._rows = self._rows_for(query)
        self._destroy_popup()
        # 面板内嵌在宿主窗口里（不是 Toplevel）：不会抢 grab，点其它地方
        # 能照常触发点击并顺手收起。
        host = self.winfo_toplevel()
        x, y, width, height = self._popup_geometry()
        panel = _DropdownPanel(
            host, self._rows, self._value, x=x, y=y, width=width,
            height=height, scale=self.scale, font=self.font,
            outer_bg=self.bg, on_pick=self._choose)
        self._popup = panel
        self._panel = panel
        self._listbox = None
        panel.place(x=x, y=y, width=width, height=height)
        panel.lift()
        # 点面板之外的地方：先让点击照常落到目标控件，再收起下拉
        self._outside_bind = host.bind("<Button-1>", self._on_any_click,
                                       add="+")

    def _destroy_popup(self) -> None:
        bind_id = self._outside_bind
        self._outside_bind = None
        if bind_id:
            try:
                self.winfo_toplevel().unbind("<Button-1>", bind_id)
            except tk.TclError:
                pass
        popup, self._popup = self._popup, None
        self._panel = None
        self._listbox = None
        if popup is None:
            return
        try:
            popup.destroy()
        except tk.TclError:
            pass

    def _hide_popup(self) -> None:
        """收起下拉并恢复成上次真正选中的类型名。"""
        if self._popup is None:
            return
        self._destroy_popup()
        label = self._label_of(self._value)
        if label:
            self.var.set(label)

    def _on_any_click(self, event) -> None:
        """点击下拉面板以外的地方时自动收起。"""
        popup = self._popup
        if popup is None:
            return
        # 点自己（输入框 / 箭头）或面板内部都不算「点外面」：箭头要留给
        # 自己的 toggle 处理，否则按下先收起、松开又立刻弹开。
        widget = event.widget
        while widget is not None:
            if widget is self or widget is popup:
                return
            widget = getattr(widget, "master", None)
        self._hide_popup()

    def _on_wheel(self, event) -> str:
        if self._panel is not None:
            return self._panel._on_wheel(event)
        return "break"

    def _index_of(self, value: str) -> int | None:
        for index, (_text, row_value) in enumerate(self._rows):
            if row_value == value:
                return index
        return None

    def _row_value(self, index: int) -> str | None:
        if 0 <= index < len(self._rows):
            return self._rows[index][1]
        return None

    # -- 交互 -------------------------------------------------------------
    def _on_key(self, event) -> None:
        if self.readonly:
            return
        if event.keysym in ("Down", "Up", "Return", "KP_Enter", "Escape",
                            "Tab", "Left", "Right", "Home", "End",
                            "Prior", "Next", "Shift_L", "Shift_R",
                            "Control_L", "Control_R", "Alt_L", "Alt_R",
                            "Caps_Lock", "Num_Lock"):
            return
        self._show_popup(self.var.get())

    def _on_down(self, _event=None) -> str:
        self._step(1)
        return "break"

    def _on_up(self, _event=None) -> str:
        self._step(-1)
        return "break"

    def _step(self, delta: int) -> None:
        if self._popup is None:
            self._show_popup("")
        if self._panel is None or not self._rows:
            return
        self._panel.move(delta)

    def _current_index(self) -> int:
        if self._panel is None or self._panel.active_index is None:
            return -1
        return int(self._panel.active_index)

    def _on_return(self, _event=None) -> str:
        if self._popup is not None:
            index = self._current_index()
            value = self._row_value(index)
            if value is not None:
                self._choose(value)
                return "break"
        return "break"

    def _on_escape(self, _event=None) -> str:
        self._hide_popup()
        return "break"

    def _on_click(self, event) -> str:
        if self._panel is None:
            return "break"
        index = self._panel._index_at(event.y)
        value = self._row_value(index) if index is not None else None
        if value is not None:
            self._choose(value)
        return "break"

    def _on_motion(self, event) -> None:
        if self._panel is not None:
            self._panel._on_motion(event)

    def _choose(self, value: str) -> None:
        self._value = value
        self.var.set(self._label_of(value))
        self._sync_paw()
        self._hide_popup()
        self._suppress_focus = True
        try:
            self.entry.focus_set()
        except tk.TclError:
            pass
        self.after(150, self._release_focus_guard)
        if self.command:
            self.command()

    def _release_focus_guard(self) -> None:
        self._suppress_focus = False

    def _on_focus_in(self, _event=None) -> None:
        self._sync_paw(True)
        try:
            self.entry.select_range(0, "end")
        except tk.TclError:
            pass
        if not self._suppress_focus:
            self._show_popup("")

    def _on_focus_out(self, _event=None) -> None:
        self.after(0, lambda: self._sync_paw(False))
        self.after(80, self._close_if_focus_left)

    def _close_if_focus_left(self) -> None:
        if self._popup is None:
            return
        try:
            focus = self.focus_get()
        except KeyError:                # 焦点控件刚好被销毁
            focus = None
        widget = focus
        while widget is not None:
            if widget is self or widget is self._popup:
                return
            widget = getattr(widget, "master", None)
        self._hide_popup()

    def _on_destroy(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        self._destroy_popup()

    def destroy(self) -> None:  # noqa: D401 - tkinter override
        self._destroy_popup()
        super().destroy()


class LocalizedCombobox(SearchableCombo):
    """只读下拉框：显示文字随语言变化，变量中始终保存内部源值。

    业务代码继续读写 ``variable``（例如 ``var_on_fail``）里的中文源值；
    界面上看到的是当前语言的译文。这样切换语言不会破坏保存逻辑。
    视觉上和 SearchableCombo 一致：圆角字段、右侧猫爪、圆角下拉面板。
    """

    def __init__(self, master=None, *, values=(), variable=None, **kwargs):
        self._source_values = [str(value) for value in values]
        self._source_var = variable if variable is not None else tk.StringVar()
        self._display_var = tk.StringVar()
        self._syncing = False
        font = kwargs.pop("font", theme.FONT_NOTE)
        font_size = font[1] if isinstance(font, (tuple, list)) and \
            len(font) > 1 else theme.FONT_SIZE_NOTE
        width = kwargs.pop("width", 18)
        kwargs.pop("state", None)
        kwargs.pop("textvariable", None)
        options = [(source, i18n.tr(source), "", source)
                   for source in self._source_values]
        super().__init__(master, options, variable=self._display_var,
                         readonly=True, shell=True, font_size=font_size,
                         width=width, **kwargs)
        self._display_trace = self._display_var.trace_add(
            "write", self._on_display_change)
        self._source_trace = self._source_var.trace_add(
            "write", self._on_source_change)
        self._sync_display()
        if self._source_var.get():
            self.set(self._source_var.get())

    def _rebuild_options(self) -> None:
        self._items = [_ComboOption(source, i18n.tr(source), "",
                                    source_label=source)
                       for source in self._source_values]
        self._group_order = [""]
        self._sync_value_from_var()

    def _on_source_change(self, *_args) -> None:
        if self._syncing:
            return
        self._sync_display()

    def _on_display_change(self, *_args) -> None:
        if self._syncing:
            return
        display = self._display_var.get()
        for source in self._source_values:
            if display in (source, i18n.tr(source)):
                self._value = source
                if self._source_var.get() != source:
                    self._syncing = True
                    try:
                        self._source_var.set(source)
                    finally:
                        self._syncing = False
                return
        self._value = ""

    def _sync_display(self) -> None:
        display = i18n.tr(self._source_var.get())
        self._value = self._source_var.get()
        if hasattr(self, "btn"):
            self._sync_paw()
        if self._display_var.get() == display:
            return
        self._syncing = True
        try:
            self._display_var.set(display)
        finally:
            self._syncing = False

    def cget(self, key: str):
        if key == "values":
            return tuple(i18n.tr(value) for value in self._source_values)
        return super().cget(key)

    def configure(self, **kwargs):
        values = kwargs.pop("values", None)
        if values is not None:
            self._source_values = [str(value) for value in values]
            self._rebuild_options()
            self._sync_display()
        if kwargs:
            super().configure(**kwargs)

    config = configure

    def refresh_language(self) -> None:
        self._rebuild_options()
        self._sync_display()
        self._hide_popup()

    def set(self, value: str) -> None:
        """按源值或当前译文选中。"""
        text = str(value)
        for source in self._source_values:
            if text in (source, i18n.tr(source)):
                self._value = source
                self._source_var.set(source)
                self._sync_display()
                return
        self._source_var.set(text)

    def _on_destroy(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        for var, trace in ((self._display_var,
                            getattr(self, "_display_trace", None)),
                           (self._source_var,
                            getattr(self, "_source_trace", None))):
            if trace:
                try:
                    var.trace_remove("write", trace)
                except (tk.TclError, ValueError):
                    pass
        super()._on_destroy(event)


class ReadonlySelect(SearchableCombo):
    """普通只读下拉框：值就是显示文字，用于语言 / 执行组等场景。"""

    def __init__(self, master, values, variable: tk.StringVar | None = None,
                 command=None, scale: float = 1.0, bg: str | None = None,
                 font_size: int = theme.FONT_SIZE_NOTE, width: int = 18,
                 **kwargs):
        options = [(str(value), str(value), "") for value in values]
        super().__init__(master, options, variable=variable,
                         command=command, scale=scale, bg=bg,
                         font_size=font_size, width=width, readonly=True,
                         shell=True, **kwargs)

    def cget(self, key: str):
        if key == "values":
            return tuple(option.label for option in self._items)
        return super().cget(key)

    def configure(self, **kwargs):
        values = kwargs.pop("values", None)
        if values is not None:
            current = self.get()
            self._items = [_ComboOption(str(value), str(value), "",
                                        source_label=str(value))
                           for value in values]
            self._group_order = [""]
            if current:
                self.set(current)
        if kwargs:
            super().configure(**kwargs)

    config = configure

    def refresh_language(self) -> None:
        self._hide_popup()
