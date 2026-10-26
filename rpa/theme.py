# -*- coding: utf-8 -*-
"""MeowGo 设计令牌（Design Tokens v1.1）。

这里是界面颜色、字号、圆角、控件高度和图标的唯一来源：
`gui.py` 与 `uikit.py` 都从这里取值，不要在组件里另写一套色值或字号。

颜色命名沿用《MeowGo 界面视觉规范》：
暖白纸面 + 墨黑文字 + 暖橙点缀；暗色模式整组替换。
"""
from __future__ import annotations

from typing import Mapping

# ---------------------------------------------------------------------------
# 字体
# ---------------------------------------------------------------------------
FONT_FAMILY = "Microsoft YaHei UI"
MONO_FAMILY = "Cascadia Code"

# Tk 的 font size 单位是 pt；96 DPI 下 1pt≈1.333px。
# 这里保留 8/9/10/11/13/14/16/22 的阶梯，分别对应
# caption / note / body / section / title / display / large / hero。
FONT_CAPTION = (FONT_FAMILY, 8)
FONT_CAPTION_BOLD = (FONT_FAMILY, 8, "bold")
FONT_NOTE = (FONT_FAMILY, 9)
FONT_NOTE_BOLD = (FONT_FAMILY, 9, "bold")
FONT_BODY = (FONT_FAMILY, 10)
FONT_BODY_BOLD = (FONT_FAMILY, 10, "bold")
FONT_SECTION = (FONT_FAMILY, 11, "bold")
FONT_TITLE = (FONT_FAMILY, 13, "bold")
FONT_DISPLAY = (FONT_FAMILY, 14, "bold")
FONT_TITLE_LARGE = (FONT_FAMILY, 16, "bold")
FONT_HERO = (FONT_FAMILY, 22, "bold")
FONT_MONO = (MONO_FAMILY, 9)

# 自绘控件（RoundButton / SearchableCombo）传入的整数 pt 字号
FONT_SIZE_CAPTION = 8
FONT_SIZE_NOTE = 9
FONT_SIZE_BODY = 10

# ---------------------------------------------------------------------------
# 间距 / 圆角 / 控件尺寸（逻辑像素，统一乘 App.scale）
# ---------------------------------------------------------------------------
SPACE_2 = 2
SPACE_4 = 4
SPACE_8 = 8
SPACE_12 = 12
SPACE_16 = 16
SPACE_20 = 20
SPACE_24 = 24
SPACE_32 = 32

RADIUS_S = 8          # 输入框 / 小按钮 / 下拉项
RADIUS_M = 12         # 卡片 / 弹窗 / 表格容器
RADIUS_L = 16         # 大卡片 / 面板
RADIUS_XL = 24        # 引导页 / 大容器

# 控件高度四档：紧凑图标 / 常规 / 对话框主按钮 / 空状态主按钮
CONTROL_XS = 24
CONTROL_SM = 30
CONTROL_MD = 36
CONTROL_LG = 40

# 图标尺寸：小图标 / 常规 / 面板标题 / 大图标
ICON_XS = 12
ICON_SM = 14
ICON_MD = 16
ICON_LG = 18
ICON_XL = 20

HEADER_HEIGHT = 56
TABLE_ROW_HEIGHT = 34
DIALOG_PAD = 16
DIALOG_ROW_GAP = 6

# ---------------------------------------------------------------------------
# 色板
# ---------------------------------------------------------------------------
LIGHT_PALETTE = {
    "BG": "#FAF9F7",
    "CARD": "#FFFFFF",
    "ACCENT": "#FDAD63",
    "ACCENT_STRONG": "#E8861F",
    "ACCENT_TINT": "#FFE9D2",
    "ACCENT_MIST": "#FFF6EC",
    "TEXT": "#080806",
    "MUTED": "#6B655E",
    "TEXT_WEAK": "#A8A29A",
    "BORDER": "#E6E2DB",
    "BORDER_STRONG": "#D4CFC6",
    "SUCCESS": "#3E9B6F",
    "WARNING": "#E0A33C",
    "DANGER": "#D9534F",
    "INFO": "#5B8DEF",
    # 语义色「深变体」：用于浅色底（Tag / 徽章 / 报告 pill）上的文字，
    # 保证 12px 小字对比度 ≥ 4.5:1（规范 §8.5）
    "SUCCESS_TEXT": "#2C7755",
    "WARNING_TEXT": "#8F5600",
    "DANGER_TEXT": "#B03B37",
    "LOG_BG": "#12110F",
    "LOG_FG": "#F5F2ED",
    "LOG_HEAD_BG": "#1D1B18",
    "LOG_HEAD_FG": "#F5F2ED",
    "LOG_HEAD_MUTED": "#9A938A",
    "LOG_INFO": "#7ED3A8",
    "LOG_WARN": "#F0C674",
    "LOG_ERROR": "#F28B82",
    "LOG_TIME": "#8C857D",
    "CONSOLE_SCROLL": "#3D3831",
    "ZEBRA": "#F7F5F1",
    "HEADING_BG": "#F3F1ED",
    "HEADING_FG": "#6B655E",
    "SELECT_BG": "#FFE9D2",
    "ENTRY_BG": "#FFFFFF",
    "SCROLL_BG": "#CFC8BE",
    "SCROLL_TROUGH": "#FAF9F7",
    "PROGRESS_TROUGH": "#F0EDE8",
    "PREVIEW_BG": "#FFF6EC",
    "SUMMARY_BG": "#FFF6EC",
    "SUMMARY_BORDER": "#F2C38C",
    "MARK_RUNNING": "#FFF3DF",
    "MARK_DONE": "#EAF5EF",
    "MARK_FAILED": "#FBEAE9",
}

DARK_PALETTE = {
    "BG": "#12110F",
    "CARD": "#1D1B18",
    "ACCENT": "#FDAD63",
    "ACCENT_STRONG": "#FFB86F",
    "ACCENT_TINT": "#3A2D20",
    "ACCENT_MIST": "#2A2118",
    "TEXT": "#F5F2ED",
    "MUTED": "#A8A29A",
    "TEXT_WEAK": "#6B655E",
    "BORDER": "#35312B",
    "BORDER_STRONG": "#4A443C",
    "SUCCESS": "#6BC79A",
    "WARNING": "#F0BC5E",
    "DANGER": "#EE8B88",
    "INFO": "#8FB3F5",
    # 暗色下语义文字直接用提亮后的语义色（深色浅底上对比度已足够）
    "SUCCESS_TEXT": "#6BC79A",
    "WARNING_TEXT": "#F0BC5E",
    "DANGER_TEXT": "#EE8B88",
    "LOG_BG": "#0D0C0A",
    "LOG_FG": "#F7F4EF",
    "LOG_HEAD_BG": "#1E1B18",
    "LOG_HEAD_FG": "#F7F4EF",
    "LOG_HEAD_MUTED": "#B8B1A8",
    "LOG_INFO": "#7ED3A8",
    "LOG_WARN": "#F0C674",
    "LOG_ERROR": "#F28B82",
    "LOG_TIME": "#8C857D",
    "CONSOLE_SCROLL": "#3D3831",
    "ZEBRA": "#25221E",
    "HEADING_BG": "#26231F",
    "HEADING_FG": "#A8A29A",
    "SELECT_BG": "#3A2D20",
    "ENTRY_BG": "#1D1B18",
    "SCROLL_BG": "#3D3831",
    "SCROLL_TROUGH": "#12110F",
    "PROGRESS_TROUGH": "#3B3630",
    "PREVIEW_BG": "#2A2118",
    "SUMMARY_BG": "#2A2118",
    "SUMMARY_BORDER": "#71573B",
    "MARK_RUNNING": "#33291C",
    "MARK_DONE": "#1F2E27",
    "MARK_FAILED": "#352322",
}

# 品牌墨黑：阴影、深色提示条等不随主题变化的“绝对色”
INK = "#080806"

_current = {"dark": False}


def set_dark(on: bool) -> None:
    """切换当前暗色状态（只改令牌，不直接操作控件）。"""
    _current["dark"] = bool(on)


def is_dark() -> bool:
    return _current["dark"]


def palette(dark: bool | None = None) -> Mapping[str, str]:
    """返回当前（或指定）主题的色板。"""
    use_dark = _current["dark"] if dark is None else bool(dark)
    return DARK_PALETTE if use_dark else LIGHT_PALETTE


def token(name: str, dark: bool | None = None) -> str:
    """按名称取当前主题色；不存在时抛 KeyError，避免悄悄出现默认色。"""
    return palette(dark)[name]


def is_dark_color(color: str) -> bool:
    """判断一个 Hex 颜色是否偏暗（用于深色日志条上的 ghost 按钮）。"""
    color = color.lstrip("#")
    if len(color) == 3:
        color = "".join(ch * 2 for ch in color)
    try:
        red, green, blue = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return False
    return (red * 0.299 + green * 0.587 + blue * 0.114) < 128


def button_colors(kind: str, bg: str, *, enabled: bool = True,
                  hover: bool = False, pressed: bool = False,
                  dark: bool | None = None) -> tuple[str, str, str]:
    """圆角按钮的 (填充色, 前景色, 描边色)。

    所有按钮都从这里取色，避免 RoundButton 内部再维护一套。
    """
    use_dark = _current["dark"] if dark is None else bool(dark)
    p = palette(use_dark)
    dark_bg = is_dark_color(bg)

    if not enabled:
        if kind in ("accent", "danger"):
            if use_dark:
                return "#2A2622", p["TEXT_WEAK"], ""
            return p["BORDER"], p["TEXT_WEAK"], ""
        if use_dark or (kind == "ghost" and dark_bg):
            return p["HEADING_BG"], p["TEXT_WEAK"], ""
        return p["BORDER"], p["TEXT_WEAK"], ""

    if kind == "accent":
        if pressed:
            return ("#F2A552" if use_dark else "#D97712"), INK, ""
        if hover:
            return p["ACCENT_STRONG"], INK, ""
        return p["ACCENT"], INK, ""

    if kind == "danger":
        if use_dark:
            if pressed:
                return "#D97B78", INK, ""
            if hover:
                return "#F2A09D", INK, ""
            return p["DANGER"], INK, ""
        if pressed:
            return "#B03B37", "#FFFFFF", ""
        if hover:
            return "#C74642", "#FFFFFF", ""
        return p["DANGER"], "#FFFFFF", ""

    if kind == "outline":
        if use_dark:
            base, hover_bg, active = p["CARD"], p["HEADING_BG"], "#3A2D20"
        else:
            base, hover_bg, active = p["CARD"], p["HEADING_BG"], p["ACCENT_TINT"]
        if pressed:
            return active, p["TEXT"], p["BORDER"]
        if hover:
            return hover_bg, p["TEXT"], p["BORDER"]
        return base, p["TEXT"], p["BORDER"]

    if kind == "ghost":
        if use_dark or dark_bg:
            fg = p["LOG_FG"] if dark_bg else p["TEXT"]
            hover_bg = p["ACCENT_MIST"] if use_dark else "#26231F"
            active = p["ACCENT_TINT"] if use_dark else "#302B26"
        else:
            fg, hover_bg, active = p["TEXT"], p["ACCENT_MIST"], p["ACCENT_TINT"]
        if pressed:
            return active, fg, ""
        if hover:
            return hover_bg, fg, ""
        return bg, fg, ""

    # solid（默认次级按钮）
    if use_dark:
        base, hover_bg, active = p["HEADING_BG"], "#302B26", "#3A342C"
    else:
        base, hover_bg, active = p["HEADING_BG"], "#E9E5DE", "#DFDAD2"
    if pressed:
        return active, p["TEXT"], ""
    if hover:
        return hover_bg, p["TEXT"], ""
    return base, p["TEXT"], ""
