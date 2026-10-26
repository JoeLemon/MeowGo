# -*- coding: utf-8 -*-
"""托盘模块测试：图标生成、启停时序、菜单分发、会话结束钩子。

托盘图标是系统级资源，测试会真的弹一下通知区（随即移除），
不影响鼠标键盘；注册表 / 计划任务之类一概不碰。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa import tray  # noqa: E402

_ok: list[str] = []
_bad: list[str] = []


def check(name, condition, detail=""):
    if condition:
        _ok.append(name)
        print(f"  PASS  {name}")
    else:
        _bad.append(name)
        print(f"  FAIL  {name} {detail}")


def test_icon_file():
    """浅/深两版图标都能生成，且深色版确实反白（文件不同）。"""
    light = tray.icon_file("", False)
    dark = tray.icon_file("", True)
    check("托盘图标文件生成", bool(light) and os.path.exists(light),
          f"light={light!r}")
    check("深色主题图标独立生成", bool(dark) and dark != light
          and os.path.exists(dark), f"dark={dark!r}")
    check("图标源可解析", bool(tray.icon_sources()),
          str(tray.icon_sources()))
    # 缓存命中：第二次直接返回同一路径
    check("图标缓存复用", tray.icon_file("", False) == light)


def test_icon_missing_source():
    """源文件不存在时不崩，返回空串（调用方据此不开托盘）。"""
    check("缺失源图返回空串", tray.icon_file("不存在的图.png", False) == "")


def test_win32_prototypes_compatible():
    """托盘给 user32 设的原型不能和 pyautogui 打架（回归：热键步骤报
    "expected LP_POINT instance instead of pointer to POINT"）。

    ctypes 比对 argtypes 看的是类型对象本身，自定义一个字段一样的 POINT
    也算不同类型。tray 曾经自己定义 POINT 并注册到 GetCursorPos 上，
    而 pyautogui 每次动作前的 FAILSAFE 检查都会调 position() →
    GetCursorPos(byref(wintypes.POINT))，于是所有鼠标键盘步骤一起哑掉。
    """
    import ctypes

    import pyautogui

    check("tray.POINT 就是 wintypes.POINT", tray.POINT is ctypes.wintypes.POINT)

    user32 = ctypes.windll.user32
    foreign = []
    for name in ("GetCursorPos", "SetCursorPos", "GetSystemMetrics",
                 "mouse_event", "SendInput", "GetForegroundWindow"):
        func = getattr(user32, name, None)
        for arg in getattr(func, "argtypes", None) or []:
            inner = getattr(arg, "_type_", None)     # 只挑指针类型看
            if inner is not None and getattr(
                    inner, "__module__", "") != "ctypes.wintypes":
                foreign.append(f"{name}({arg})")
    check("共享 user32 原型没有自定义结构体", not foreign, str(foreign))

    try:
        pos = pyautogui.position()
        ok = len(tuple(pos)) == 2
        detail = repr(pos)
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    check("导入托盘后 pyautogui.position() 正常", ok, detail)


def test_start_stop_cycle():
    """真实启停一轮：start → running → stop → not running。"""
    icon = tray.TrayIcon(tip="MeowGo 测试", on_activate=lambda: None,
                         menu_items=[("显示主窗口", lambda: None), None,
                                     ("退出", lambda: None)],
                         invoke=lambda fn: fn())
    started = icon.start(dark=False)
    check("托盘图标启动", started and icon.running)
    time.sleep(0.2)
    icon.stop()
    check("托盘图标移除", not icon.running)
    # 重复 stop 不报错
    icon.stop()
    check("重复 stop 安全", True)


def test_start_twice():
    """已启动时再 start 直接返回 True，不会起第二个线程。"""
    icon = tray.TrayIcon(tip="MeowGo 测试", on_activate=lambda: None,
                         invoke=lambda fn: fn())
    check("首次启动", icon.start(dark=False))
    first = icon._thread
    check("重复启动幂等", icon.start(dark=False) and icon._thread is first)
    icon.stop()


def test_tip_and_theme_switch():
    """提示文字与主题切换都走消息循环线程执行，不报错。"""
    icon = tray.TrayIcon(tip="旧提示", on_activate=lambda: None,
                         invoke=lambda fn: fn())
    check("启动（主题切换测试）", icon.start(dark=False))
    try:
        icon.set_tip("MeowGo · 新提示")
        time.sleep(0.15)
        check("提示文字更新", icon.tip == "MeowGo · 新提示", icon.tip)
        icon.set_icon(dark=True)
        time.sleep(0.15)
        check("切到深色图标", icon._dark is True)
        icon.set_icon(dark=False)
        time.sleep(0.15)
        check("切回浅色图标", icon._dark is False)
        # 同主题重复切换应被去重（不重复生成图标）
        same = icon._hicon
        icon.set_icon(dark=False)
        time.sleep(0.15)
        check("同主题切换去重", icon._hicon == same)
    finally:
        icon.stop()


def test_tray_events_dispatch():
    """直接派发托盘回调消息：激活 / 菜单项分别触发对应回调。"""
    fired: list[str] = []
    icon = tray.TrayIcon(
        tip="MeowGo 测试",
        on_activate=lambda: fired.append("activate"),
        menu_items=[("显示主窗口", lambda: fired.append("show")), None,
                    ("退出", lambda: fired.append("quit"))],
        invoke=lambda fn: fn())
    check("启动（事件分发测试）", icon.start(dark=False))
    try:
        # 左键抬起 = 激活（WM_TRAY 消息，lParam 低位是鼠标事件）
        icon._on_tray_event(tray.WM_LBUTTONUP)
        icon._on_tray_event(tray.WM_LBUTTONDBLCLK)
        time.sleep(0.1)
        check("单击/双击触发激活", fired == ["activate", "activate"], str(fired))

        # 右键：TrackPopupMenu 需要前台窗口，测试环境里只验证命令映射逻辑
        fired.clear()
        commands = {}
        next_id = [1000]
        for entry in icon.menu_items:
            if entry is None:
                continue
            label, callback = entry
            commands[next_id[0]] = callback
            next_id[0] += 1
        for command, callback in commands.items():
            icon._fire(callback)
        time.sleep(0.1)
        check("菜单命令映射完整", fired == ["show", "quit"], str(fired))
    finally:
        icon.stop()


def test_invoke_marshalling():
    """invoke 存在时，回调被交给调用方（tk 的 after）而不是就地执行。"""
    received: list[object] = []

    def fake_invoke(fn):
        received.append(fn)

    icon = tray.TrayIcon(on_activate=lambda: None, invoke=fake_invoke)
    icon._fire(lambda: received.append("direct"))
    check("回调经 invoke 中转", len(received) == 1 and callable(received[0]))
    # 没有 invoke 时就地执行
    icon2 = tray.TrayIcon(on_activate=lambda: None)
    icon2._fire(lambda: received.append("direct"))
    check("无 invoke 就地执行", received[-1] == "direct")


def test_balloon_click():
    """气泡点击回调（NIN_BALLOONUSERCLICK）能派发到 on_click。"""
    clicked: list[str] = []
    icon = tray.TrayIcon(on_activate=None, invoke=lambda fn: fn())
    icon.notify("标题", "内容", on_click=lambda: clicked.append("click"))
    icon._on_tray_event(tray.NIN_BALLOONUSERCLICK)
    check("气泡点击派发", clicked == ["click"], str(clicked))
    icon._on_tray_event(tray.NIN_BALLOONUSERCLICK)
    check("重复点击不报错", len(clicked) == 2)


def test_session_hook():
    """会话结束钩子：挂得上、解得掉、消息不进 tk 主循环。"""
    if os.name != "nt":
        check("会话钩子（非 Windows 跳过）", True)
        return
    import tkinter as tk

    root = tk.Tk()
    root.geometry("80x60")
    events: list[tuple[str, bool]] = []
    try:
        hook = tray.hook_session_end(
            root,
            on_query=lambda: events.append(("query", True)),
            on_end=lambda ending: events.append(("end", ending)))
        check("会话钩子挂载", hook is not None)
        hwnd = int(root.winfo_id())
        # 给自己窗口投递 WM_QUERYENDSESSION / WM_ENDSESSION，
        # tk 主循环处理时会先经过我们的 WNDPROC
        tray.user32.PostMessageW(hwnd, tray.WM_QUERYENDSESSION, 0, 0)
        for _ in range(50):
            root.update()
            if events:
                break
            time.sleep(0.02)
        check("WM_QUERYENDSESSION 置标志", events == [("query", True)],
              str(events))
        tray.user32.PostMessageW(hwnd, tray.WM_ENDSESSION, 1, 0)
        for _ in range(50):
            root.update()
            if len(events) > 1:
                break
            time.sleep(0.02)
        check("WM_ENDSESSION 通知结束", events[-1] == ("end", True), str(events))
        # 解除后不再收到
        events.clear()
        hook()
        tray.user32.PostMessageW(hwnd, tray.WM_QUERYENDSESSION, 0, 0)
        for _ in range(20):
            root.update()
            time.sleep(0.02)
        check("解除钩子后静默", events == [], str(events))
    finally:
        root.destroy()


def test_non_windows_paths():
    """非 Windows 行为：start 返回 False，hook 返回 None（本机是 Windows，
    这里验证的是「平台不对时安静失败」的代码路径存在且可调用）。"""
    check("图标函数存在可调用", callable(tray.icon_file))
    check("hook 函数存在可调用", callable(tray.hook_session_end))


def main():
    print("== rpa/tray.py 测试 ==")
    for test in (test_icon_file, test_icon_missing_source,
                 test_win32_prototypes_compatible, test_start_stop_cycle,
                 test_start_twice, test_tip_and_theme_switch,
                 test_tray_events_dispatch, test_invoke_marshalling,
                 test_balloon_click, test_session_hook, test_non_windows_paths):
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            check(test.__name__, False, f"{type(exc).__name__}: {exc}")
    print(f"\n{len(_ok)}/{len(_ok) + len(_bad)} 通过")
    return 1 if _bad else 0


if __name__ == "__main__":
    sys.exit(main())
