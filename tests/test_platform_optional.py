# -*- coding: utf-8 -*-
"""可选依赖与平台能力：OCR / UIA 缺失提示、macOS 权限说明。"""
import sys
import types

import numpy as np
import pytest

from rpa import ocr, platform_support, uia

pytestmark = pytest.mark.engine


# --------------------------------------------------------------------- OCR
def test_ocr_unavailable_and_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "pytesseract", None)
    assert ocr.available() is False
    with pytest.raises(ocr.OcrUnavailable) as exc:
        ocr._engine()
    assert "pytesseract" in str(exc.value)


def _fake_pil(monkeypatch):
    image = types.SimpleNamespace(fromarray=lambda array: array)
    package = types.ModuleType("PIL")
    package.Image = image
    monkeypatch.setitem(sys.modules, "PIL", package)
    monkeypatch.setitem(sys.modules, "PIL.Image", image)


def _fake_tesseract(monkeypatch, image_to_data):
    module = types.ModuleType("pytesseract")
    module.Output = types.SimpleNamespace(DICT="dict")
    module.image_to_data = image_to_data
    monkeypatch.setitem(sys.modules, "pytesseract", module)


def test_ocr_find_text_joins_words_and_ignores_spaces(monkeypatch):
    _fake_pil(monkeypatch)

    def image_to_data(_image, **_kwargs):
        return {
            "text": ["Hello", "World", ""],
            "conf": ["90", "88", "-1"],
            "block_num": [1, 1, 1], "par_num": [1, 1, 1],
            "line_num": [1, 1, 1],
            "left": [10, 20, 0], "top": [10, 10, 0],
            "width": [10, 10, 0], "height": [4, 4, 0],
        }

    _fake_tesseract(monkeypatch, image_to_data)
    assert ocr.find_text(np.zeros((8, 40, 3), dtype=np.uint8),
                         "hello world") == (20.0, 12.0)


def test_ocr_find_text_confidence_and_errors(monkeypatch):
    _fake_pil(monkeypatch)
    _fake_tesseract(monkeypatch, lambda *_a, **_k: {
        "text": ["low"], "conf": ["10"],
        "block_num": [1], "par_num": [1], "line_num": [1],
        "left": [0], "top": [0], "width": [10], "height": [10],
    })
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    assert ocr.find_text(image, "low") is None
    assert ocr.find_text(image, "") is None

    def boom(*_args, **_kwargs):
        raise RuntimeError("tesseract missing")

    _fake_tesseract(monkeypatch, boom)
    with pytest.raises(ocr.OcrUnavailable) as exc:
        ocr.find_text(image, "anything")
    assert "tesseract missing" in str(exc.value)


# --------------------------------------------------------------------- UIA
def test_uia_unavailable_and_control_lookup(monkeypatch):
    monkeypatch.setitem(sys.modules, "uiautomation", None)
    assert uia.available() is False
    with pytest.raises(uia.UiaUnavailable):
        uia.find_control("确定")

    class Control:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.timeout = None
            self.clicked = False

        def Exists(self, maxSearchSeconds=0):
            self.timeout = maxSearchSeconds
            return True

        def Click(self):
            self.clicked = True

    module = types.ModuleType("uiautomation")
    module.Control = Control
    monkeypatch.setitem(sys.modules, "uiautomation", module)
    assert uia.available() is True

    control = uia.find_control(" 确定 ", "Button", 0.2)
    assert control.kwargs == {"Name": "确定", "ControlTypeName": "Button"}
    assert control.timeout == 0.2
    assert uia.find_control("") is None
    assert uia.click_control("确定", timeout=0) is True


def test_uia_lookup_error_wrapped(monkeypatch):
    class Broken:
        def __init__(self, **_kwargs):
            raise ValueError("COM failed")

    module = types.ModuleType("uiautomation")
    module.Control = Broken
    monkeypatch.setitem(sys.modules, "uiautomation", module)
    with pytest.raises(uia.UiaUnavailable) as exc:
        uia.find_control("确定")
    assert "COM failed" in str(exc.value)


# ---------------------------------------------------------- 平台能力 / 权限
def test_platform_capabilities_and_labels(monkeypatch):
    assert platform_support.available("system_tray") is platform_support.IS_WINDOWS
    assert platform_support.available("no-such-capability") is False
    text = platform_support.unavailable_text("window_ops")
    assert "Windows" in text and "AppleScript" in text
    assert platform_support.unavailable_text("unknown") == "当前系统不支持该功能。"

    assert platform_support.hotkey_label("Ctrl+Z") == "Ctrl+Z"
    assert platform_support.normalize_hotkey("Ctrl,Shift,A") == "Ctrl,Shift,A"
    monkeypatch.setattr(platform_support, "IS_MACOS", True)
    assert platform_support.hotkey_label("Ctrl+Z") == "Cmd+Z"
    assert platform_support.normalize_hotkey("Ctrl, Shift, A") == \
        "command,shift,a"


def test_platform_permissions(monkeypatch):
    assert platform_support.permission_status() == {
        "screen_recording": True, "accessibility": True}
    assert platform_support.missing_permissions() == []
    assert platform_support.open_privacy_settings("bogus") is False

    monkeypatch.setattr(platform_support, "IS_MACOS", True)
    monkeypatch.setattr(platform_support, "_framework", lambda _path: None)
    assert platform_support.permission_status() == {
        "screen_recording": True, "accessibility": True}

    monkeypatch.setattr(
        platform_support, "permission_status",
        lambda: {"screen_recording": False, "accessibility": True})
    assert platform_support.missing_permissions() == ["screen_recording"]
    message = platform_support.explain_permissions(["screen_recording"])
    assert "屏幕录制" in message and "系统设置" in message
    assert platform_support._framework("Z:/definitely/missing") is None
