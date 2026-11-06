# -*- coding: utf-8 -*-
"""macOS 原生集成：菜单栏托盘图标 + 全局 F5/F9 热键。

只用系统自带的 AppKit / Quartz（pyautogui 在 macOS 上已经依赖 pyobjc），
不引入新的第三方包。所有入口在非 macOS 上安静失败，不影响导入。

- :class:`MacTrayIcon`：用 NSStatusItem 在菜单栏放一个图标，菜单项回调
  通过 ``invoke`` 交回 Tk 主线程；
- :class:`GlobalHotkeyListener`：用 Quartz 事件监听（CGEventTap）在后台
  线程里监听 F5 / F9，到点把动作写进调用方的队列，不阻塞 Tk 主循环。

macOS 上监听全局键盘需要「辅助功能」权限；未授权时 CGEventTap 创建失败，
``start()`` 返回 False 并给出可照做的提示。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import Any, Callable, Optional

IS_MACOS = sys.platform == "darwin"

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# macOS 虚拟键码（与键盘布局无关）
F5_KEYCODE = 96
F9_KEYCODE = 101

_ACCESSIBILITY_HINT = (
    "缺少「辅助功能」权限：系统设置 → 隐私与安全性 → 辅助功能，"
    "勾选运行 MeowGo 的终端 / Python，然后重新打开 MeowGo。")


def available() -> bool:
    """当前环境能否使用原生集成（macOS + pyobjc AppKit / Quartz）。"""
    if not IS_MACOS:
        return False
    try:
        import AppKit  # noqa: F401
        import Quartz  # noqa: F401
    except Exception:  # noqa: BLE001 - 缺 pyobjc 时按不可用处理
        return False
    return True


def app_is_frontmost() -> bool:
    """MeowGo 是否在前台（全局 F5 要跳过，避免和窗口内快捷键跑两次）。"""
    if not IS_MACOS:
        return False
    try:
        import AppKit

        current = AppKit.NSRunningApplication.currentApplication()
        return bool(current and current.isActive())
    except Exception:  # noqa: BLE001
        return False


def icon_source() -> str:
    """菜单栏图标源文件（与窗口图标同源，兼容打包后的资源目录）。"""
    candidates = [
        os.path.join(getattr(sys, "_MEIPASS", APP_DIR), "main.png"),
        os.path.join(APP_DIR, "main.png"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return ""


def _apple_quote(text: str) -> str:
    """AppleScript 字符串字面量（转义反斜杠 / 引号，换行压成空格）。"""
    flat = " ".join(str(text or "").split())
    return '"' + flat.replace("\\", "\\\\").replace('"', '\\"') + '"'


if IS_MACOS:
    import objc
    from Foundation import NSObject

    class _MenuTarget(NSObject):
        """菜单项 target：收到 action 后调用 Python 回调。"""

        def initWithCallback_(self, callback):
            self = objc.super(_MenuTarget, self).init()
            if self is None:
                return None
            self._callback = callback
            return self

        def fire_(self, _sender) -> None:
            callback = getattr(self, "_callback", None)
            if callback:
                callback()
else:                       # pragma: no cover - 非 macOS 不会实例化
    _MenuTarget = None  # type: ignore[assignment, misc]


class MacTrayIcon:
    """菜单栏托盘图标，接口与 :class:`rpa.tray.TrayIcon` 保持一致。"""

    def __init__(self, tip: str = "MeowGo", on_activate=None,
                 menu_items=None, invoke=None):
        self.tip = str(tip or "")
        self.on_activate = on_activate
        self.menu_items = list(menu_items or [])
        self._invoke = invoke
        self._status_item = None
        self._menu = None
        self._targets: list[object] = []
        self._items: dict[str, Any] = {}
        self._source = ""
        self._dark = False
        self.error = ""

    @property
    def running(self) -> bool:
        return self._status_item is not None

    # ------------------------------------------------------------ 生命周期
    def start(self, source: str = "", dark: bool = False) -> bool:
        if not available():
            self.error = "macOS 菜单栏托盘需要 pyobjc（随 pyautogui 安装）"
            return False
        if self._status_item is not None:
            return True
        try:
            from AppKit import NSImage, NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength

            item = NSStatusBar.systemStatusBar().statusItemWithLength_(
                NSVariableStatusItemLength)
            button = item.button()
            path = source or icon_source()
            image = None
            if path and os.path.exists(path):
                image = NSImage.alloc().initWithContentsOfFile_(path)
            if image is not None:
                image.setSize_((18.0, 18.0))
                image.setTemplate_(True)   # 菜单栏自动适配浅色 / 深色
                button.setImage_(image)
            else:
                button.setTitle_("MG")
            button.setToolTip_(self.tip)

            menu = NSMenu.alloc().init()
            self._items.clear()
            for entry in self.menu_items:
                if entry is None:
                    menu.addItem_(NSMenuItem.separatorItem())
                    continue
                label, callback = entry[0], entry[1]
                enabled = entry[2] if len(entry) >= 3 else True
                target = _MenuTarget.alloc().initWithCallback_(
                    lambda cb=callback: self._fire(cb))
                self._targets.append(target)
                menu_item = NSMenuItem.alloc(
                ).initWithTitle_action_keyEquivalent_(
                    str(label), b"fire:", b"")
                menu_item.setTarget_(target)
                menu_item.setEnabled_(bool(enabled))
                menu.addItem_(menu_item)
                self._items[str(label)] = menu_item
            item.setMenu_(menu)
        except Exception as exc:  # noqa: BLE001 - 菜单栏异常不影响主界面
            self.error = f"创建菜单栏图标失败：{exc}"
            self._status_item = None
            return False
        self._source = path
        self._dark = bool(dark)
        self._status_item = item
        self._menu = menu
        return True

    def stop(self, wait: bool = True) -> bool:
        item, menu = self._status_item, self._menu
        self._status_item = None
        self._menu = None
        self._targets.clear()
        self._items.clear()
        if item is None:
            return True
        try:
            from AppKit import NSStatusBar

            item.setMenu_(None)
            NSStatusBar.systemStatusBar().removeStatusItem_(item)
        except Exception:  # noqa: BLE001
            pass
        del menu
        return True

    # ------------------------------------------------------------ 状态更新
    def set_tip(self, text: str) -> None:
        self.tip = str(text or "")
        item = self._status_item
        if item is None:
            return
        try:
            item.button().setToolTip_(self.tip)
        except Exception:  # noqa: BLE001
            pass

    def set_icon(self, dark: bool = False) -> None:
        """模板图标由系统自动适配深浅色，这里只记录状态做幂等判断。"""
        self._dark = bool(dark)

    def set_menu_enabled(self, states: dict[str, bool]) -> None:
        """按菜单标签启用 / 禁用项；找不到的标签忽略。"""
        for label, enabled in states.items():
            item = self._items.get(str(label))
            if item is None:
                continue
            try:
                item.setEnabled_(bool(enabled))
            except Exception:  # noqa: BLE001 - 菜单项失效不影响主流程
                pass

    def notify(self, title: str, message: str = "", on_click=None) -> None:
        """系统通知：走 AppleScript，不依赖已废弃的 NSUserNotification。"""
        del on_click                  # macOS 通知点击回调无法可靠接管
        if not IS_MACOS:
            return
        script = (
            f"display notification {_apple_quote(message)} "
            f"with title {_apple_quote(title)}")
        try:
            subprocess.run(["osascript", "-e", script], check=False,
                           timeout=8, capture_output=True)
        except (OSError, subprocess.SubprocessError):
            pass

    def _fire(self, callback) -> None:
        """菜单回调统一交回主线程（与 Windows 托盘行为一致）。"""
        if callback is None:
            return
        if self._invoke is not None:
            self._invoke(callback)
        else:
            callback()


class GlobalHotkeyListener:
    """后台监听全局 F5 / F9；macOS 上用 CGEventTap（只监听不拦截按键）。"""

    def __init__(self, on_run: Callable[[], None],
                 on_stop: Callable[[], None], log=None):
        self.on_run = on_run
        self.on_stop = on_stop
        self._log = log
        self.error = ""
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._running = False
        self._tap = None
        self._source = None
        self._loop = None

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> bool:
        if not IS_MACOS:
            self.error = "全局热键的原生监听只支持 macOS / Windows"
            return False
        if self._thread is not None and self._thread.is_alive():
            return self._running
        if not available():
            self.error = "macOS 全局热键需要 pyobjc（随 pyautogui 安装）"
            return False
        self.error = ""
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="meowgo-hotkeys")
        self._thread.start()
        self._ready.wait(2.0)
        return self._running

    def stop(self, wait: bool = True) -> bool:
        tap, loop = self._tap, self._loop
        self._tap = None
        self._loop = None
        if tap is not None:
            try:
                import Quartz

                Quartz.CGEventTapEnable(tap, False)
            except Exception:  # noqa: BLE001
                pass
        if loop is not None:
            try:
                import Quartz

                Quartz.CFRunLoopStop(loop)
            except Exception:  # noqa: BLE001
                pass
        thread = self._thread
        if wait and thread is not None:
            thread.join(1.0)
        self._thread = None
        self._running = False
        self._source = None
        return True

    # ------------------------------------------------------------ 内部实现
    def _run(self) -> None:
        tap = None
        source = None
        try:
            import Quartz

            mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionListenOnly,
                mask, self._callback, None)
        except Exception as exc:  # noqa: BLE001
            self.error = f"全局热键初始化失败：{exc}"
            self._ready.set()
            return
        if tap is None:
            self.error = _ACCESSIBILITY_HINT
            self._ready.set()
            return
        try:
            import Quartz

            source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
            self._loop = Quartz.CFRunLoopGetCurrent()
            Quartz.CFRunLoopAddSource(
                self._loop, source, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
        except Exception as exc:  # noqa: BLE001
            self.error = f"全局热键启动失败：{exc}"
            self._ready.set()
            return
        self._tap = tap
        self._source = source
        self._running = True
        self._ready.set()
        try:
            Quartz.CFRunLoopRun()
        finally:
            self._running = False
            for close in (
                    lambda: Quartz.CGEventTapEnable(tap, False),
                    lambda: source is not None
                    and Quartz.CFRunLoopSourceInvalidate(source),
                    lambda: Quartz.CFMachPortInvalidate(tap)):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass
            self._tap = None
            self._source = None
            self._loop = None

    def _callback(self, _proxy, event_type, event, _refcon):
        """事件回调：只做按键判断并把动作放进调用方队列。"""
        try:
            import Quartz

            if event_type in (Quartz.kCGEventTapDisabledByTimeout,
                              Quartz.kCGEventTapDisabledByUserInput):
                if self._tap is not None:
                    Quartz.CGEventTapEnable(self._tap, True)
                return event
            if event_type != Quartz.kCGEventKeyDown:
                return event
            keycode = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode)
            repeat = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventAutorepeat)
            if repeat:
                return event
            if keycode == F9_KEYCODE:
                self.on_stop()
            elif keycode == F5_KEYCODE:
                self.on_run()
        except Exception as exc:  # noqa: BLE001 - 回调里绝不能抛出
            if self._log is not None:
                self._log("warn", f"全局热键回调异常：{exc}")
        return event


# ---------------------------------------------------------------------------
# 录制用事件监听：键盘（可吞事件）+ 鼠标（只监听）
# ---------------------------------------------------------------------------

class _TapLoop:
    """Quartz 事件监听的后台线程 + CFRunLoop 生命周期封装。"""

    def __init__(self, mask: int, option: int, callback: Callable,
                 name: str, log=None):
        self._mask = mask
        self._option = option
        self._callback = callback
        self._name = name
        self._log = log
        self.error = ""
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._running = False
        self._tap = None
        self._source = None
        self._loop = None

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> bool:
        if not IS_MACOS:
            self.error = "事件监听只支持 macOS"
            return False
        if self._thread is not None and self._thread.is_alive():
            return self._running
        if not available():
            self.error = "macOS 事件监听需要 pyobjc（随 pyautogui 安装）"
            return False
        self.error = ""
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name=self._name)
        self._thread.start()
        self._ready.wait(2.0)
        return self._running

    def stop(self, wait: bool = True) -> bool:
        tap, loop = self._tap, self._loop
        self._tap = None
        self._loop = None
        if tap is not None:
            try:
                import Quartz

                Quartz.CGEventTapEnable(tap, False)
            except Exception:  # noqa: BLE001
                pass
        if loop is not None:
            try:
                import Quartz

                Quartz.CFRunLoopStop(loop)
            except Exception:  # noqa: BLE001
                pass
        thread = self._thread
        if wait and thread is not None:
            thread.join(1.0)
        self._thread = None
        self._running = False
        self._source = None
        return True

    def _run(self) -> None:
        tap = None
        source = None
        try:
            import Quartz

            tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                self._option, self._mask, self._on_event, None)
        except Exception as exc:  # noqa: BLE001
            self.error = f"事件监听初始化失败：{exc}"
            self._ready.set()
            return
        if tap is None:
            self.error = _ACCESSIBILITY_HINT
            self._ready.set()
            return
        try:
            import Quartz

            source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
            self._loop = Quartz.CFRunLoopGetCurrent()
            Quartz.CFRunLoopAddSource(
                self._loop, source, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
        except Exception as exc:  # noqa: BLE001
            self.error = f"事件监听启动失败：{exc}"
            self._ready.set()
            return
        self._tap = tap
        self._source = source
        self._running = True
        self._ready.set()
        try:
            Quartz.CFRunLoopRun()
        finally:
            self._running = False
            for close in (
                    lambda: Quartz.CGEventTapEnable(tap, False),
                    lambda: source is not None
                    and Quartz.CFRunLoopSourceInvalidate(source),
                    lambda: Quartz.CFMachPortInvalidate(tap)):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass
            self._tap = None
            self._source = None
            self._loop = None

    def _on_event(self, _proxy, event_type, event, _refcon):
        """把事件交给具体监听器；返回值沿用 Quartz 语义（None = 吞掉）。"""
        try:
            import Quartz

            if event_type in (Quartz.kCGEventTapDisabledByTimeout,
                              Quartz.kCGEventTapDisabledByUserInput):
                if self._tap is not None:
                    Quartz.CGEventTapEnable(self._tap, True)
                return event
            return self._callback(event_type, event)
        except Exception as exc:  # noqa: BLE001 - 回调里绝不能抛出
            if self._log is not None:
                self._log("warn", f"事件监听回调异常：{exc}")
            return event


class KeyboardEventTap:
    """录制按键：主动吞掉键盘事件，避免漏进输入框 / 触发界面快捷键。"""

    def __init__(self, on_key: Callable[[int, bool], None], log=None):
        self.on_key = on_key
        self._log = log
        self.error = ""
        self._loop: Optional[_TapLoop] = None

    @property
    def running(self) -> bool:
        return bool(self._loop and self._loop.running)

    def start(self) -> bool:
        if not IS_MACOS:
            self.error = "按键录制只支持 macOS / Windows"
            return False
        if self.running:
            return True
        try:
            import Quartz

            mask = (Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
                    | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp))
        except Exception as exc:  # noqa: BLE001
            self.error = f"按键录制初始化失败：{exc}"
            return False
        # option 0 = 可修改事件流：回调返回 None 即吞掉按键
        self._loop = _TapLoop(
            mask, 0, self._handle, "meowgo-key-recorder", self._log)
        ok = self._loop.start()
        self.error = self._loop.error
        return ok

    def stop(self, wait: bool = True) -> bool:
        loop = self._loop
        self._loop = None
        return loop.stop(wait) if loop is not None else True

    def _handle(self, event_type, event):
        import Quartz

        if event_type not in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp):
            return event
        keycode = int(Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGKeyboardEventKeycode))
        if event_type == Quartz.kCGEventKeyDown:
            repeat = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventAutorepeat)
            if not repeat:
                self.on_key(keycode, True)
        else:
            self.on_key(keycode, False)
        return None                 # 录制中吞掉所有按键


class MouseEventTap:
    """录制鼠标：只监听，不拦截，用户还能正常点「停止」。"""

    _EVENTS = (
        "kCGEventMouseMoved", "kCGEventLeftMouseDown", "kCGEventLeftMouseUp",
        "kCGEventRightMouseDown", "kCGEventRightMouseUp",
        "kCGEventScrollWheel")

    def __init__(self, on_event: Callable[[str, int, int, object], None],
                 log=None):
        self.on_event = on_event
        self._log = log
        self.error = ""
        self._loop: Optional[_TapLoop] = None

    @property
    def running(self) -> bool:
        return bool(self._loop and self._loop.running)

    def start(self) -> bool:
        if not IS_MACOS:
            self.error = "宏录制只支持 macOS / Windows"
            return False
        if self.running:
            return True
        try:
            import Quartz

            mask = 0
            for name in self._EVENTS:
                mask |= Quartz.CGEventMaskBit(getattr(Quartz, name))
            option = Quartz.kCGEventTapOptionListenOnly
        except Exception as exc:  # noqa: BLE001
            self.error = f"宏录制初始化失败：{exc}"
            return False
        self._loop = _TapLoop(
            mask, option, self._handle, "meowgo-mouse-recorder", self._log)
        ok = self._loop.start()
        self.error = self._loop.error
        return ok

    def stop(self, wait: bool = True) -> bool:
        loop = self._loop
        self._loop = None
        return loop.stop(wait) if loop is not None else True

    def _handle(self, event_type, event):
        import Quartz

        location = Quartz.CGEventGetLocation(event)
        x, y = int(round(location.x)), int(round(location.y))
        if event_type == Quartz.kCGEventScrollWheel:
            delta = int(Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGScrollWheelEventDeltaAxis1))
            # macOS 一行约等于 Windows 的 120 单位，保持录制结果一致
            self.on_event("wheel", x, y, delta * 120)
        elif event_type in (Quartz.kCGEventLeftMouseDown,
                            Quartz.kCGEventRightMouseDown):
            button = ("left" if event_type == Quartz.kCGEventLeftMouseDown
                      else "right")
            self.on_event("down", x, y, button)
        elif event_type in (Quartz.kCGEventLeftMouseUp,
                            Quartz.kCGEventRightMouseUp):
            button = ("left" if event_type == Quartz.kCGEventLeftMouseUp
                      else "right")
            self.on_event("up", x, y, button)
        elif event_type == Quartz.kCGEventMouseMoved:
            self.on_event("move", x, y, "")
        return event                # 鼠标事件不拦截
