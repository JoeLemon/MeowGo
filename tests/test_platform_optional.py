# -*- coding: utf-8 -*-
"""可选依赖与平台能力：OCR / UIA 缺失提示、macOS 权限说明。"""
import sys
import types

import numpy as np
import pytest

from rpa import ocr, platform_support, uia
from rpa.engine import Group, Project, Step

pytestmark = pytest.mark.engine


# --------------------------------------------------------------------- OCR
def test_ocr_unavailable_and_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "pytesseract", None)
    assert ocr.available() is False
    with pytest.raises(ocr.OcrUnavailable) as exc:
        ocr._engine()
    assert "pytesseract" in str(exc.value)
    if platform_support.IS_MACOS:
        assert "brew install tesseract" in ocr.OCR_INSTALL_HINT


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
    if platform_support.IS_MACOS:
        pytest.skip("macOS 走 AXUIElement，不使用 uiautomation")
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
    if platform_support.IS_MACOS:
        pytest.skip("macOS 走 AXUIElement，不使用 uiautomation")
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
def test_mac_permission_preflight(monkeypatch):
    """缺辅助功能 / 屏幕录制时，运行前校验要提前给出提示。"""
    if not platform_support.IS_MACOS:
        pytest.skip("仅 macOS 有这两项权限")
    from rpa import engine

    monkeypatch.setattr(platform_support, "missing_permissions",
                        lambda: ["accessibility", "screen_recording"])
    problems = engine.validate_environment(
        [Group("主", [Step("input", "你好")])])
    assert any("辅助功能" in p for p in problems), problems
    problems = engine.validate_environment(
        [Group("主", [Step("click", "按钮.png")])])
    assert any("屏幕录制" in p for p in problems), problems
    assert not any("权限" in p for p in engine.validate_environment(
        [Group("主", [Step("wait", "1")])]))

    monkeypatch.setattr(platform_support, "missing_permissions", lambda: [])
    assert not any("权限" in p for p in engine.validate_environment(
        [Group("主", [Step("input", "你好")])]))


def test_mac_session_lock_detection(monkeypatch):
    """macOS 锁屏标记要能驱动引擎暂停；取不到状态时按未锁屏处理。"""
    if not platform_support.IS_MACOS:
        pytest.skip("仅 macOS 有 CGSession 锁屏标记")
    import Quartz

    from rpa import engine

    monkeypatch.setattr(
        Quartz, "CGSessionCopyCurrentDictionary",
        lambda: {"CGSSessionScreenIsLocked": True})
    assert engine.session_locked() is True
    monkeypatch.setattr(Quartz, "CGSessionCopyCurrentDictionary", lambda: {})
    assert engine.session_locked() is False


def test_mac_virtual_screen_rect_union(monkeypatch):
    """多显示器：取所有活跃显示器的并集，副屏在左侧时 left 为负。"""
    if not platform_support.IS_MACOS:
        pytest.skip("仅 macOS 走 Quartz 显示器枚举")
    import types

    import Quartz

    from rpa import engine

    monkeypatch.setattr(
        Quartz, "CGGetActiveDisplayList",
        lambda _max, _ids, _count: (0, (1, 2), 2))

    def bounds(display):
        if display == 1:
            return types.SimpleNamespace(
                origin=types.SimpleNamespace(x=0.0, y=0.0),
                size=types.SimpleNamespace(width=1000.0, height=800.0))
        return types.SimpleNamespace(
            origin=types.SimpleNamespace(x=-500.0, y=0.0),
            size=types.SimpleNamespace(width=500.0, height=800.0))

    monkeypatch.setattr(Quartz, "CGDisplayBounds", bounds)
    assert engine.virtual_screen_rect() == (-500, 0, 1500, 800)


def test_platform_capabilities_and_labels(monkeypatch):
    # 托盘与全局热键在 Windows / macOS 都支持
    assert platform_support.available("system_tray") is (
        platform_support.IS_WINDOWS or platform_support.IS_MACOS)
    assert platform_support.available("global_hotkey") is (
        platform_support.IS_WINDOWS or platform_support.IS_MACOS)
    assert platform_support.available("keyboard_hook") is (
        platform_support.IS_WINDOWS or platform_support.IS_MACOS)
    assert platform_support.available("ui_automation") is (
        platform_support.IS_WINDOWS or platform_support.IS_MACOS)
    assert platform_support.available("no-such-capability") is False
    text = platform_support.unavailable_text("window_ops")
    assert "Windows" in text and "macOS" in text
    assert platform_support.unavailable_text("unknown") == "当前系统不支持该功能。"

    # 先按非 macOS 断言 Ctrl 写法，再切到 macOS 验证换算
    monkeypatch.setattr(platform_support, "IS_MACOS", False)
    assert platform_support.hotkey_label("Ctrl+Z") == "Ctrl+Z"
    assert platform_support.normalize_hotkey("Ctrl,Shift,A") == "Ctrl,Shift,A"
    monkeypatch.setattr(platform_support, "IS_MACOS", True)
    assert platform_support.hotkey_label("Ctrl+Z") == "Cmd+Z"
    assert platform_support.normalize_hotkey("Ctrl, Shift, A") == \
        "command,shift,a"


def test_default_groups_follow_platform(monkeypatch):
    """预置模板按平台生成；旧 Windows 模板升级时只换启动命令。"""
    from rpa import gui

    windows = {g.name: g for g in gui.build_default_groups("windows")}
    mac = {g.name: g for g in gui.build_default_groups("macos")}
    assert windows["打开浏览器"].steps[0].content == "浏览器=chrome"
    assert windows["打开浏览器"].steps[3].content == 'start "" "{浏览器}"'
    assert mac["打开浏览器"].steps[0].content == "浏览器=Google Chrome"
    assert mac["打开浏览器"].steps[3].content == 'open -a "{浏览器}"'
    assert mac["批量打开网址"].steps[2].content == \
        'open -a "{浏览器}" "{网址}"'
    assert mac["上传文件"].steps[3].content == "command,shift,g"

    project = Project(groups=[Group("打开浏览器", [
        Step("set", "浏览器=Google Chrome"),
        Step("set", "网址=https://example.com"),
        Step("cmd", 'start "" "{浏览器}"'),
    ])])
    monkeypatch.setattr(gui, "template_platform", lambda: "windows")
    assert gui._upgrade_template_groups(project) is False
    monkeypatch.setattr(gui, "template_platform", lambda: "macos")
    assert gui._upgrade_template_groups(project) is True
    steps = project.get("打开浏览器").steps
    assert [step.content for step in steps] == [
        "浏览器=Google Chrome", "网址=https://example.com",
        'open -a "{浏览器}"']

    # 原封不动的旧模板：变量和启动命令一起升级，用户改过的网址保留
    old = gui.build_default_groups("windows")[0]
    old.steps[2].content = "网址=https://example.com"
    migrated = Project(groups=[old])
    assert gui._upgrade_template_groups(migrated) is True
    steps = migrated.get("打开浏览器").steps
    assert steps[0].content == "浏览器=Google Chrome"
    assert steps[2].content == "网址=https://example.com"
    assert steps[3].content == 'open -a "{浏览器}"'


def test_mac_command_and_permission_hints(monkeypatch):
    """macOS 上的 start 命令和缺失权限都要给出可照做的提示。"""
    from rpa import engine

    monkeypatch.setattr(platform_support, "IS_MACOS", True)
    assert "open -a" in engine._command_error_hint('start "" "chrome"')
    assert engine._command_error_hint("echo hi") == ""
    monkeypatch.setattr(platform_support, "missing_permissions",
                        lambda: ["accessibility"])
    assert "辅助功能" in engine._mac_permission_hint("accessibility")
    assert engine._mac_permission_hint("screen_recording") == ""

    monkeypatch.setattr(platform_support, "IS_MACOS", False)
    assert engine._command_error_hint('start "" "chrome"') == ""
    assert engine._mac_permission_hint("accessibility") == ""


def test_platform_permissions(monkeypatch):
    # 非 macOS 分支一律视为已授权，不能受本机真实权限状态影响
    monkeypatch.setattr(platform_support, "IS_MACOS", False)
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
