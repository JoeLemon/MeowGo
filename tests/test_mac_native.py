# -*- coding: utf-8 -*-
"""macOS 原生集成：菜单栏托盘与全局热键回调。

只在本机 macOS 上运行；需要「辅助功能」权限的用例在未授权时跳过。
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa import mac_native, platform_support  # noqa: E402

pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(sys.platform != "darwin", reason="仅 macOS 原生集成"),
]


def test_available_and_icon_source():
    assert mac_native.available() is True
    assert isinstance(mac_native.app_is_frontmost(), bool)
    assert os.path.isfile(mac_native.icon_source())


def test_tray_start_stop_and_menu_actions():
    fired: list[str] = []
    icon = mac_native.MacTrayIcon(
        tip="MeowGo 测试",
        menu_items=[("显示主窗口", lambda: fired.append("show")), None,
                    ("退出", lambda: fired.append("quit"))],
        invoke=lambda fn: fn())
    try:
        assert icon.start() is True
        assert icon.running is True
        assert icon.start() is True          # 幂等
        assert len(icon._targets) == 2       # 分隔线不生成 target

        icon.set_tip("MeowGo · 新提示")
        assert icon.tip == "MeowGo · 新提示"
        icon.set_icon(dark=True)
        assert icon._dark is True

        # 运行状态联动：菜单项可动态禁用 / 启用
        icon.set_menu_enabled({"退出": False})
        assert icon._items["退出"].isEnabled() is False
        icon.set_menu_enabled({"退出": True})
        assert icon._items["退出"].isEnabled() is True
        # 未知标签不报错
        icon.set_menu_enabled({"不存在的项": False})

        # 直接触发菜单 target，验证回调接线
        icon._targets[0].fire_(None)
        icon._targets[1].fire_(None)
        assert fired == ["show", "quit"]
    finally:
        assert icon.stop() is True
        assert icon.running is False
        assert icon.stop() is True           # 重复 stop 安全


def test_tray_menu_item_initial_disabled():
    """菜单项第三项为 False 时，创建即为灰色不可点。"""
    icon = mac_native.MacTrayIcon(
        menu_items=[("运行当前组", lambda: None, False),
                    ("停止运行", lambda: None, True)])
    try:
        assert icon.start() is True
        assert icon._items["运行当前组"].isEnabled() is False
        assert icon._items["停止运行"].isEnabled() is True
    finally:
        icon.stop()


def test_hotkey_callback_maps_f5_f9():
    from Quartz import (
        CGEventCreateKeyboardEvent,
        CGEventSetIntegerValueField,
        kCGEventKeyDown,
        kCGEventKeyUp,
        kCGKeyboardEventAutorepeat,
    )

    fired: list[str] = []
    listener = mac_native.GlobalHotkeyListener(
        on_run=lambda: fired.append("run"),
        on_stop=lambda: fired.append("stop"))
    f9 = CGEventCreateKeyboardEvent(None, mac_native.F9_KEYCODE, True)
    listener._callback(None, kCGEventKeyDown, f9, None)
    assert fired == ["stop"]

    f5 = CGEventCreateKeyboardEvent(None, mac_native.F5_KEYCODE, True)
    listener._callback(None, kCGEventKeyDown, f5, None)
    assert fired == ["stop", "run"]

    # 长按自动重复不再触发；其它键忽略；keyUp 忽略
    fired.clear()
    CGEventSetIntegerValueField(f5, kCGKeyboardEventAutorepeat, 1)
    listener._callback(None, kCGEventKeyDown, f5, None)
    other = CGEventCreateKeyboardEvent(None, 0, True)
    listener._callback(None, kCGEventKeyDown, other, None)
    listener._callback(None, kCGEventKeyUp, f5, None)
    assert fired == []


def test_hotkey_listener_start_stop():
    if "accessibility" in platform_support.missing_permissions():
        pytest.skip("需要 macOS 辅助功能权限")
    fired: list[str] = []
    listener = mac_native.GlobalHotkeyListener(
        on_run=lambda: fired.append("run"),
        on_stop=lambda: fired.append("stop"))
    try:
        assert listener.start() is True, listener.error
        assert listener.running is True
        assert listener.start() is True      # 重复启动幂等
        time.sleep(0.1)
    finally:
        assert listener.stop() is True
        assert listener.running is False


class _DummyWidget:
    """Tk 轮询定时器的最小替身。"""

    def after(self, _ms, _fn):
        return "poll-id"

    def after_cancel(self, _id):
        pass


def test_mac_keymap():
    from rpa import recorder

    assert recorder.mac_key_name(0) == "a"
    assert recorder.mac_key_name(55) == "command"
    assert recorder.mac_key_name(53) == "esc"
    assert recorder.mac_key_name(96) == "f5"
    assert recorder.mac_key_name(82) == "num0"
    assert recorder.mac_key_name(999) is None


def test_mac_key_recorder_state_machine():
    from rpa import recorder

    done: list[str] = []
    cancelled: list[int] = []
    states: list[bool] = []
    rec = recorder.KeyRecorder(
        _DummyWidget(), states.append, lambda _names: None,
        done.append, lambda: cancelled.append(1), lambda _text: None)
    rec.active = True
    rec._track(55, 0, True)          # Command
    rec._track(0, 0, True)           # A
    assert rec._ordered() == ["command", "a"]
    rec._track(0, 0, False)
    rec._track(55, 0, False)
    assert done == ["command,a"]
    assert states[-1] is False

    rec.active = True
    rec._track(recorder._MAC_ESC_KEYCODE, 0, True)
    assert cancelled == [1]


def test_mac_macro_recorder_steps(monkeypatch):
    from rpa import recorder

    steps = []
    macro = recorder.MacroRecorder(
        _DummyWidget(), lambda _active: None, steps.extend, lambda: None,
        lambda _text: None)
    monkeypatch.setattr(macro, "_install_mac_hook", lambda: True)
    try:
        assert macro.start() is True
        macro._on_mac_mouse("down", 100, 100, "left")
        macro._on_mac_mouse("up", 100, 100, "left")
        macro._on_mac_mouse("move", 300, 300, "")
        macro._on_mac_mouse("wheel", 300, 300, 120)
    finally:
        macro.stop()
    kinds = [(step.type, step.content) for step in steps]
    assert ("click_xy", "100,100") in kinds
    assert ("move_xy", "300,300") in kinds
    assert ("scroll", "100") in kinds


def _first_titled_control():
    """从前台应用的辅助功能树里取一个带标题的菜单项（只读，不点击）。"""
    import ApplicationServices as AS

    from rpa import uia

    try:
        app = uia._mac_focused_application(AS)
    except Exception:  # noqa: BLE001
        return None
    if app is None:
        return None

    def attr(element, name):
        err, value = AS.AXUIElementCopyAttributeValue(element, name, None)
        return value if err == 0 else None

    stack = [(root, 0) for root in uia._mac_application_roots(AS, app)]
    seen = 0
    while stack and seen < 800:
        element, depth = stack.pop()
        seen += 1
        title = attr(element, AS.kAXTitleAttribute)
        role = str(attr(element, AS.kAXRoleAttribute) or "")
        if isinstance(title, str) and title.strip() and role in (
                "AXMenuItem", "AXButton", "AXMenuBarItem"):
            return title.strip(), role
        if depth >= 6:
            continue
        for child in reversed(list(attr(element, AS.kAXChildrenAttribute) or [])):
            stack.append((child, depth + 1))
    return None


def test_mac_window_close_focuses_target(monkeypatch):
    """关闭窗口的 Cmd+W 兜底必须先把目标进程带到前台，避免关错窗口。"""
    from rpa import engine

    scripts: list[str] = []
    monkeypatch.setattr(engine, "_mac_windows",
                        lambda: [("巡检窗口", "巡检进程")])
    monkeypatch.setattr(engine, "_osascript",
                        lambda script, timeout=10: scripts.append(script) or "")
    assert engine._mac_window_action("close", "巡检") == "已关闭窗口「巡检」"
    assert scripts and "set frontmost to true" in scripts[0]
    assert 'keystroke "w" using command down' in scripts[0]


def test_mac_uia_find_and_click_wiring(monkeypatch):
    import ApplicationServices as AS

    from rpa import uia

    if not uia.available():
        pytest.skip("需要 macOS 辅助功能权限")
    picked = _first_titled_control()
    if not picked:
        pytest.skip("前台应用没有可读控件")
    title, role = picked

    found = uia.find_control(title, timeout=1.0)
    assert found is not None
    assert uia.find_control("不存在的控件名称", timeout=0) is None

    # 只验证点击接线：替换 AXPress，避免真的操作前台应用
    monkeypatch.setattr(AS, "AXUIElementPerformAction", lambda _el, _act: 0)
    assert uia.click_control(title, timeout=0) is True
    assert uia.click_control(title, control_type="Button", timeout=0) \
        is (role == "AXButton")
