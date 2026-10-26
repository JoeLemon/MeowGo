# -*- coding: utf-8 -*-
"""设计令牌的一致性测试：色板、控件尺寸、按钮配色。"""
from __future__ import annotations

from rpa import theme


CORE_TOKENS = {
    "BG", "CARD", "ACCENT", "ACCENT_STRONG", "ACCENT_TINT", "ACCENT_MIST",
    "TEXT", "MUTED", "TEXT_WEAK", "BORDER", "BORDER_STRONG",
    "SUCCESS", "WARNING", "DANGER", "INFO",
    "SUCCESS_TEXT", "WARNING_TEXT", "DANGER_TEXT",
    "ENTRY_BG", "SELECT_BG", "ZEBRA",
    "HEADING_BG", "HEADING_FG",
    "LOG_BG", "LOG_FG", "LOG_INFO", "LOG_WARN", "LOG_ERROR", "LOG_TIME",
    "SCROLL_BG", "SCROLL_TROUGH",
}


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    assert len(value) == 6, value
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _luminance(value: str) -> float:
    def channel(raw: int) -> float:
        raw /= 255.0
        return raw / 12.92 if raw <= 0.04045 else ((raw + 0.055) / 1.055) ** 2.4

    red, green, blue = _rgb(value)
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def _contrast(foreground: str, background: str) -> float:
    first, second = sorted((_luminance(foreground), _luminance(background)),
                           reverse=True)
    return (first + 0.05) / (second + 0.05)


def test_light_and_dark_palettes_have_the_same_keys():
    assert set(theme.LIGHT_PALETTE) == set(theme.DARK_PALETTE)
    assert CORE_TOKENS <= set(theme.LIGHT_PALETTE)


def test_palette_values_are_hex_colors():
    for palette in (theme.LIGHT_PALETTE, theme.DARK_PALETTE):
        for name, value in palette.items():
            assert value.startswith("#"), (name, value)
            assert len(value) == 7, (name, value)
            _rgb(value)


def test_text_contrast_is_readable_in_both_themes():
    for dark in (False, True):
        p = theme.palette(dark)
        assert _contrast(p["TEXT"], p["BG"]) >= 10.0
        assert _contrast(p["TEXT"], p["CARD"]) >= 10.0
        assert _contrast(p["MUTED"], p["CARD"]) >= 4.5
        assert _contrast(p["HEADING_FG"], p["HEADING_BG"]) >= 4.5
        assert _contrast(p["LOG_FG"], p["LOG_BG"]) >= 7.0
        assert _contrast(p["LOG_TIME"], p["LOG_BG"]) >= 4.5
        assert _contrast(p["TEXT"], p["SELECT_BG"]) >= 7.0


def test_selection_and_entry_surfaces_stay_distinct():
    for dark in (False, True):
        p = theme.palette(dark)
        assert p["ENTRY_BG"] != p["BG"] or dark
        assert p["SELECT_BG"] != p["CARD"]


def test_control_icon_and_radius_steps_are_ordered():
    assert theme.CONTROL_XS < theme.CONTROL_SM < theme.CONTROL_MD < theme.CONTROL_LG
    assert theme.ICON_XS < theme.ICON_SM < theme.ICON_MD < theme.ICON_LG < theme.ICON_XL
    assert theme.RADIUS_S < theme.RADIUS_M < theme.RADIUS_L < theme.RADIUS_XL
    assert theme.SPACE_2 < theme.SPACE_4 < theme.SPACE_8 < theme.SPACE_12
    assert theme.SPACE_12 < theme.SPACE_16 < theme.SPACE_24 < theme.SPACE_32
    assert theme.HEADER_HEIGHT >= theme.CONTROL_LG
    assert theme.TABLE_ROW_HEIGHT >= theme.CONTROL_SM


def test_semantic_text_colors_are_readable_on_tinted_backgrounds():
    """语义色深变体（Tag / 徽章 / 报告 pill 文字）在对应浅底上要可读。"""
    for dark in (False, True):
        p = theme.palette(dark)
        assert _contrast(p["SUCCESS_TEXT"], p["MARK_DONE"]) >= 4.5
        assert _contrast(p["WARNING_TEXT"], p["MARK_RUNNING"]) >= 4.5
        assert _contrast(p["DANGER_TEXT"], p["MARK_FAILED"]) >= 4.5


def test_token_follows_current_theme_and_explicit_override():
    previous = theme.is_dark()
    try:
        theme.set_dark(False)
        assert theme.token("BG") == theme.LIGHT_PALETTE["BG"]
        assert theme.token("BG", True) == theme.DARK_PALETTE["BG"]
        theme.set_dark(True)
        assert theme.is_dark() is True
        assert theme.token("CARD") == theme.DARK_PALETTE["CARD"]
        assert theme.token("CARD", False) == theme.LIGHT_PALETTE["CARD"]
    finally:
        theme.set_dark(previous)


def test_button_colors_cover_every_kind_in_both_themes():
    for dark, bg in ((False, theme.LIGHT_PALETTE["CARD"]),
                     (True, theme.DARK_PALETTE["CARD"])):
        for kind in ("solid", "outline", "ghost", "accent", "danger"):
            fill, foreground, border = theme.button_colors(
                kind, bg, dark=dark)
            assert fill.startswith("#") and foreground.startswith("#")
            assert not border or border.startswith("#")
        accent = theme.button_colors("accent", bg, dark=dark)
        assert accent[1] == theme.INK
        disabled = theme.button_colors("accent", bg, dark=dark, enabled=False)
        assert disabled[1] == theme.palette(dark)["TEXT_WEAK"]


def test_is_dark_color_handles_short_and_long_hex():
    assert theme.is_dark_color("#000000")
    assert theme.is_dark_color("#123")
    assert not theme.is_dark_color("#FFFFFF")
    assert not theme.is_dark_color("#FDAD63")
    assert theme.is_dark_color("not-a-color") is False
