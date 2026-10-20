# -*- coding: utf-8 -*-
"""界面小组件：矢量图标 + 圆角按钮 + 轻量提示。

图标用 PIL 在 24×24 网格上绘制后按 4 倍超采样缩放，避免 Tk 无矢量图标的短板；
圆角按钮用 Canvas 的 smooth 多边形实现，支持悬停/按下/禁用三种状态。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont

from PIL import Image, ImageDraw, ImageTk

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


class Tooltip:
    """鼠标悬停提示。"""

    def __init__(self, widget: tk.Widget, text: str):
        self.widget = widget
        self.text = text
        self.tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, _event=None) -> None:
        if self.tip or not self.text:
            return
        self.tip = tk.Toplevel(self.widget)
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        label = tk.Label(self.tip, text=self.text, bg="#2b3240", fg="#f2f4f8",
                         padx=8, pady=4, font=("Microsoft YaHei UI", 9),
                         bd=0)
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

MENU_CARD = "#ffffff"
MENU_BORDER = "#dbe2ee"
MENU_HOVER = "#eaf1ff"
MENU_HOVER_DANGER = "#fdeceb"
MENU_TEXT = "#22262e"
MENU_MUTED = "#9aa2b1"
MENU_DANGER = "#e5484d"
MENU_ACCENT = "#3b6ff5"
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

            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

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
        self.font = tkfont.Font(family="Microsoft YaHei UI", size=font_size)

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
                self.font.measure(item.label)
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
                text=item.label, anchor="w", fill=color, font=self.font)
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
                 height: int = 34, pad: tuple[int, int] = (14, 14),
                 icon_size: int = 16, radius: int = 9, tooltip: str = "",
                 font_size: int = 9, bg: str = "#f5f6f8", bold: bool = False):
        if isinstance(pad, (int, float)):
            pad = (pad, pad)
        self.scale = scale
        self.kind = kind
        self.command = command
        self.icon_name = icon_name
        self.text = text
        self.height = int(round(height * scale))
        self.radius = int(round(radius * scale))
        self.pad = (int(round(pad[0] * scale)), int(round(pad[1] * scale)))
        self.icon_size = int(round(icon_size * scale))
        self.gap = int(round(6 * scale)) if (icon_name and text) else 0
        self.font = tkfont.Font(family="Microsoft YaHei UI",
                                size=font_size, weight="bold" if bold else "normal")
        self._enabled = True
        self._hover = False
        self._pressed = False

        text_w = self.font.measure(text) if text else 0
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
        self._tip = Tooltip(self, tooltip or text)

    # -- 配色 -------------------------------------------------------------
    def _palette(self) -> tuple[str, str]:
        """返回 (背景色, 前景色)。"""
        if self.kind == "accent":
            base, fg = "#3b6ff5", "#ffffff"
            hover, active = "#2f5fdc", "#2854c4"
        elif self.kind == "danger":
            base, fg = "#e5484d", "#ffffff"
            hover, active = "#d23c41", "#b83136"
        elif self.kind == "outline":
            base, fg = "#ffffff", "#33405a"
            hover, active = "#eef3ff", "#e0e9ff"
        elif self.kind == "ghost":
            base, fg = self["bg"], "#4a5768"
            hover, active = "#e8ecf3", "#dde3ec"
        else:
            base, fg = "#e9edf4", "#2b3444"
            hover, active = "#dfe4ee", "#d3dae7"
        if not self._enabled:
            if self.kind in ("accent", "danger"):
                return "#c9d2e0", "#ffffff"
            return "#f0f1f4", "#b3b8c0"
        if self._pressed:
            return active, fg
        if self._hover:
            return hover, fg
        return base, fg

    def _rebuild(self) -> None:
        fill, fg = self._palette()
        self.itemconfigure(self._shape, fill=fill,
                           outline="#d8dee9" if (self.kind == "outline" and self._enabled) else "")
        for item in self._content:
            self.delete(item)
        self._content = []

        width = int(self["width"])
        x = self.pad[0]
        if self.icon_name:
            image = icon(self.icon_name, self.icon_size, fg)
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

    def set_text(self, text: str, icon_name: str | None = None) -> None:
        self.text = text
        if icon_name is not None:
            self.icon_name = icon_name
        text_w = self.font.measure(text) if text else 0
        width = self.pad[0] * 2 + text_w
        if self.icon_name:
            self.gap = int(round(6 * self.scale)) if text else 0
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
        if self.kind == "ghost":
            self._rebuild()
