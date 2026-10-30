# -*- coding: utf-8 -*-
"""全局输入录制：键盘热键（WH_KEYBOARD_LL）与鼠标宏（WH_MOUSE_LL）。

从 gui.py 拆出，行为与公开名称保持不变；界面仍通过 gui 模块引用
``KeyRecorder`` / ``MacroRecorder`` / ``vk_to_key_name``。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
import time
import tkinter as tk
from typing import Optional

from .engine import Step

# ---------------------------------------------------------------------------
# 按键录制：底层键盘钩子（WH_KEYBOARD_LL）
# ---------------------------------------------------------------------------

class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", ctypes.wintypes.DWORD),
                ("scanCode", ctypes.wintypes.DWORD),
                ("flags", ctypes.wintypes.DWORD),
                ("time", ctypes.wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p)]


_HOOKPROC = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_int,
                             ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)

_WH_KEYBOARD_LL = 13
_WM_KEYDOWN, _WM_KEYUP = 0x100, 0x101
_WM_SYSKEYDOWN, _WM_SYSKEYUP = 0x104, 0x105
_MAPVK_VK_TO_CHAR = 2

# VK → 引擎键名（这些键不随键盘布局变化）
_VK_KEYMAP = {
    0x08: "backspace", 0x09: "tab", 0x0D: "enter", 0x1B: "esc",
    0x20: "space", 0x2D: "insert", 0x2E: "delete", 0x24: "home",
    0x23: "end", 0x21: "pageup", 0x22: "pagedown", 0x25: "left",
    0x26: "up", 0x27: "right", 0x28: "down", 0x14: "capslock",
    0x90: "numlock", 0x91: "scrolllock", 0x2C: "printscreen",
    0x13: "pause", 0x5D: "apps",
    0x10: "shift", 0xA0: "shift", 0xA1: "shift",
    0x11: "ctrl", 0xA2: "ctrl", 0xA3: "ctrl",
    0x12: "alt", 0xA4: "alt", 0xA5: "alt",
    0x5B: "win", 0x5C: "win",
    0x6B: "add", 0x6D: "subtract", 0x6A: "multiply", 0x6F: "divide",
    0x6E: "decimal",
}

# 美式布局的 OEM 标点兜底（按当前布局取不到字符时使用）
_VK_OEM_US = {0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".",
              0xBF: "/", 0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]",
              0xDE: "'", 0xE2: "\\"}

# 定稿时修饰键统一排在前头的顺序
_MODIFIER_ORDER = ("ctrl", "alt", "shift", "win")

_user32: Optional[ctypes.WinDLL] = None


def _win_user32() -> ctypes.WinDLL:
    """user32 的私有 ctypes 句柄（单独设 argtypes，不影响其它调用方）。"""
    global _user32
    if _user32 is None:
        user32 = ctypes.WinDLL("user32")
        user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [
            ctypes.wintypes.HWND, ctypes.wintypes.LPDWORD]
        user32.GetKeyboardLayout.argtypes = [ctypes.wintypes.DWORD]
        user32.GetKeyboardLayout.restype = ctypes.wintypes.HKL
        user32.MapVirtualKeyExW.argtypes = [ctypes.wintypes.UINT,
                                            ctypes.wintypes.UINT,
                                            ctypes.wintypes.HKL]
        user32.MapVirtualKeyExW.restype = ctypes.wintypes.UINT
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, _HOOKPROC, ctypes.wintypes.HINSTANCE,
            ctypes.wintypes.DWORD]
        user32.SetWindowsHookExW.restype = ctypes.wintypes.HHOOK
        user32.UnhookWindowsHookEx.argtypes = [ctypes.wintypes.HHOOK]
        user32.UnhookWindowsHookEx.restype = ctypes.wintypes.BOOL
        user32.CallNextHookEx.argtypes = [
            ctypes.wintypes.HHOOK, ctypes.c_int, ctypes.wintypes.WPARAM,
            ctypes.wintypes.LPARAM]
        user32.CallNextHookEx.restype = ctypes.c_long
        _user32 = user32
    return _user32


def vk_to_key_name(vk: int, scan: int = 0) -> Optional[str]:
    """把 Windows 虚拟键码翻译成引擎（pyautogui）认识的键名。

    - 修饰键 / 功能键 / 编辑键 / 小键盘：查表，与键盘布局无关
    - 字母与标点：按当前键盘布局取字符，非 ASCII 时退回美式布局，
      保证任何输入法下都能录到正确的键
    - 顶层数字键返回 None：引擎旧版限制不识别，由调用方提示改小键盘
    """
    if vk in _VK_KEYMAP:
        return _VK_KEYMAP[vk]
    if 0x70 <= vk <= 0x87:          # F1 ~ F24
        return f"f{vk - 0x6F}"
    if 0x60 <= vk <= 0x69:          # 小键盘 0 ~ 9
        return f"num{vk - 0x60}"
    if 0x30 <= vk <= 0x39:          # 顶层数字键：引擎不识别
        return None
    return _layout_key_char(vk) or _VK_OEM_US.get(vk)


def _layout_key_char(vk: int) -> Optional[str]:
    """按当前键盘布局取按键字符；失败或非 ASCII 时返回 None（兜底表接管）。"""
    try:
        user32 = _win_user32()
        hkl = user32.GetKeyboardLayout(user32.GetWindowThreadProcessId(
            user32.GetForegroundWindow(), None))
        code = user32.MapVirtualKeyExW(vk, _MAPVK_VK_TO_CHAR, hkl)
    except Exception:
        return None
    if not code:
        return None
    char = chr(code & 0xFFFF)
    if char.isascii() and (char.isalpha() or not char.isalnum()):
        return char.lower() if char.isalpha() else char
    return None


class KeyRecorder:
    """把用户直接按下的组合键录成引擎的热键格式（英文逗号分隔）。

    录制期间安装全局底层键盘钩子（WH_KEYBOARD_LL）：
    - 取的是原始虚拟键码，不受输入法/键盘布局影响——中文输入法下也能录
    - 按键被钩子吞掉，不会漏进输入框、也不会触发界面快捷键
    - 全部松开自动定稿；单独按 Esc 取消并恢复原内容

    线程模型（关键）：钩子回调里只做纯 Python 的「攒事件」——绝不调外部
    函数、不碰 Tk（那会释放 GIL，可能触发 PyEval_RestoreThread 致命错误），
    原始事件进 _pending 队列，由 Tk 侧的 _poll 定时取出并驱动状态机。

    回调（都在 Tk 主线程的 _poll 里同步触发）：
      on_state(active)   录制开始/结束：禁用或恢复其它控件
      on_preview(names)  实时预览：已按下的键（修饰键排在前）
      on_done(text)      定稿：引擎格式，如 "ctrl,shift,a"
      on_cancel()        取消：调用方负责恢复原内容
      on_hint(text)      提示文字
    """

    _POLL_MS = 15                      # 轮询原始按键事件的间隔

    def __init__(self, widget, on_state, on_preview, on_done, on_cancel,
                 on_hint):
        self.widget = widget            # 用来挂轮询定时器的 Tk 控件
        self.on_state = on_state
        self.on_preview = on_preview
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.on_hint = on_hint
        self.active = False
        self._held: list[str] = []      # 按住中的键（按下顺序、去重）
        self._last: list[str] = []      # 最近一次完整按下序列（定稿用）
        self._pending: list[tuple] = []  # 钩子攒下的原始事件
        self._poll_id = None
        self._hook = None
        self._proc = None               # 持有 CFUNCTYPE 引用，防止被回收

    # ------------------------------------------------------------ 安装/卸载
    def start(self, previous: str = "") -> bool:
        """开始录制。previous 是取消时要恢复的内容；装不上钩子返回 False。"""
        if self.active:
            return True
        if sys.platform != "win32":
            self.on_hint("按键录入只支持 Windows")
            return False
        if not self._install_hook():
            return False
        self._held = []
        self._last = []
        self._pending = []
        self.active = True
        self.on_state(True)
        self.on_hint("录制中：直接按组合键，全部松开自动录入；单独按 Esc 取消")
        self.on_preview([])
        self._poll_id = self.widget.after(self._POLL_MS, self._poll)
        return True

    def _install_hook(self) -> bool:
        """安装全局键盘钩子；失败时清理回调引用并返回 False。"""
        user32 = _win_user32()
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, _HOOKPROC, ctypes.wintypes.HINSTANCE,
            ctypes.wintypes.DWORD]
        self._proc = _HOOKPROC(self._on_hook)
        hook = user32.SetWindowsHookExW(_WH_KEYBOARD_LL, self._proc, None, 0)
        if not hook:
            self._proc = None
            return False
        self._hook = hook
        return True

    def stop(self) -> None:
        """无条件结束录制并卸载钩子（幂等）。"""
        self.active = False
        if self._poll_id is not None:
            try:
                self.widget.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None
        self._pending = []
        self._uninstall()

    def _uninstall(self) -> None:
        if self._hook is not None:
            try:
                _win_user32().UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
            self._hook = None
            self._proc = None

    # ------------------------------------------------------------ 钩子回调
    def _on_hook(self, nCode, wParam, lParam):
        """钩子回调：只攒原始事件，不做任何外部调用/UI 调用（见类文档）。"""
        try:
            if not self.active:
                return _win_user32().CallNextHookEx(None, nCode, wParam,
                                                    lParam)
            if nCode >= 0 and wParam in (_WM_KEYDOWN, _WM_SYSKEYDOWN,
                                         _WM_KEYUP, _WM_SYSKEYUP):
                info = ctypes.cast(lParam,
                                   ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                self._pending.append(
                    (info.vkCode, info.scanCode,
                     wParam in (_WM_KEYDOWN, _WM_SYSKEYDOWN)))
            return 1                     # 录制中吞掉所有按键
        except Exception:
            return 1

    def _poll(self) -> None:
        """Tk 侧轮询：取出钩子攒下的事件，逐条驱动状态机。"""
        if not self.active:
            return
        events, self._pending = self._pending, []
        for vk, scan, down in events:
            self._track(vk, scan, down)
            if not self.active:          # 处理途中定稿/取消了
                break
        if self.active:
            self._poll_id = self.widget.after(self._POLL_MS, self._poll)

    # ------------------------------------------------------------ 状态机
    def _track(self, vk: int, scan: int, down: bool) -> None:
        if not self.active:
            return
        if down:
            if vk == 0x1B and not self._held:
                self._cancel()           # 单独 Esc = 取消
                return
            name = vk_to_key_name(vk, scan)
            if name is None:
                if 0x30 <= vk <= 0x39:   # 顶层数字键：引擎不识别，给提示
                    self.on_hint("⚠ 引擎不识别顶层数字键（旧版限制），"
                                 "请改按小键盘数字 num0~num9")
                return
            if name not in self._held:
                self._held.append(name)
            self._last = self._ordered()
            self.on_preview(self._ordered())
        else:
            name = vk_to_key_name(vk, scan)
            if name is not None and name in self._held:
                self._held.remove(name)
            if not self._held:
                self._finish()

    def _finish(self) -> None:
        if not self.active or self._held:
            return                     # 又来新按键了，等下一次全部松开
        names = self._last
        self._teardown()
        if names:
            self.on_done(",".join(names))
        else:
            self.on_cancel()           # 什么都没录到（只按了不可用的键）

    def _cancel(self) -> None:
        if not self.active:
            return
        self._teardown()
        self.on_cancel()

    def _teardown(self) -> None:
        self.stop()
        self.on_state(False)

    def _ordered(self) -> list[str]:
        """修饰键排在前（ctrl/alt/shift/win），其余保持按下顺序。"""
        mods = [k for k in _MODIFIER_ORDER if k in self._held]
        rest = [k for k in self._held if k not in _MODIFIER_ORDER]
        return mods + rest


# ---------------------------------------------------------------------------
# 对话框
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 宏录制：底层鼠标钩子（WH_MOUSE_LL）
# ---------------------------------------------------------------------------

_WH_MOUSE_LL = 14
_WM_MOUSEMOVE = 0x0200
_WM_LBUTTONDOWN, _WM_LBUTTONUP = 0x0201, 0x0202
_WM_RBUTTONDOWN, _WM_RBUTTONUP = 0x0204, 0x0205
_WM_MOUSEWHEEL = 0x020A

# 位移小于这个像素不生成「移动鼠标」步骤（避免满屏都是移动）
_MOVE_MIN_DISTANCE = 60
# 两次点击间隔小于这个毫秒、且位置很近 → 合并成双击
_DOUBLE_CLICK_MS = 450


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", ctypes.wintypes.POINT),
                ("mouseData", ctypes.wintypes.DWORD),
                ("flags", ctypes.wintypes.DWORD),
                ("time", ctypes.wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p)]


_MOUSEHOOKPROC = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_int,
                                  ctypes.wintypes.WPARAM,
                                  ctypes.wintypes.LPARAM)


class MacroRecorder:
    """把真实鼠标操作录成一组步骤（click_xy / move_xy / scroll）。

    与 KeyRecorder 同样的安全模型：钩子回调只攒原始事件（纯 Python），
    由 Tk 侧轮询驱动状态机——绝不在回调里调外部函数。
    鼠标事件**不吞掉**：录制期间用户还能正常点界面（比如点「停止」）。
    """

    def __init__(self, widget, on_state, on_done, on_cancel, on_hint):
        self.widget = widget
        self.on_state = on_state
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.on_hint = on_hint
        self.active = False
        self._hook = None
        self._proc = None
        self._events: list[tuple] = []
        self._steps: list[Step] = []
        self._down: Optional[tuple] = None     # 按下中的 (x, y, ms, button)
        self._last_click: Optional[tuple] = None
        self._last_pos = (0, 0)
        self._ignore_rect: Optional[tuple] = None
        self._wheel_rest = 0                    # 不足一格的滚轮增量余量

    # ------------------------------------------------------------ 安装/卸载
    def start(self, ignore_rect: Optional[tuple] = None) -> bool:
        """开始录制；ignore_rect 内的事件忽略（控制条自己的按钮）。"""
        if self.active:
            return True
        if sys.platform != "win32":
            self.on_hint("宏录制只支持 Windows")
            return False
        if not self._install_hook():
            return False
        self._ignore_rect = ignore_rect
        self._events = []
        self._steps = []
        self._down = None
        self._last_click = None
        self._last_pos = (0, 0)
        self._wheel_rest = 0
        self.active = True
        self.on_state(True)
        self.on_hint("录制中：正常操作鼠标，点本窗口的「停止」结束")
        self._schedule_poll()
        return True

    def _install_hook(self) -> bool:
        """安装全局鼠标钩子；失败时清理回调引用并返回 False。"""
        user32 = _win_user32()
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, _MOUSEHOOKPROC, ctypes.wintypes.HINSTANCE,
            ctypes.wintypes.DWORD]
        self._proc = _MOUSEHOOKPROC(self._on_hook)
        hook = user32.SetWindowsHookExW(_WH_MOUSE_LL, self._proc, None, 0)
        if not hook:
            self._proc = None
            return False
        self._hook = hook
        return True

    def stop(self) -> None:
        """停止录制并生成步骤（幂等）。"""
        if not self.active:
            return
        self.active = False
        self._uninstall()
        self._drain()
        self.on_state(False)
        if self._steps:
            self.on_done(list(self._steps))
        else:
            self.on_cancel()

    def cancel(self) -> None:
        """放弃本次录制。"""
        if not self.active:
            return
        self.active = False
        self._uninstall()
        self.on_state(False)
        self.on_cancel()

    def _uninstall(self) -> None:
        if self._hook is not None:
            try:
                _win_user32().UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
            self._hook = None
            self._proc = None

    # ------------------------------------------------------------ 钩子回调
    def _on_hook(self, nCode, wParam, lParam):
        try:
            if not self.active:
                return _win_user32().CallNextHookEx(None, nCode, wParam,
                                                    lParam)
            if nCode >= 0:
                info = ctypes.cast(lParam,
                                   ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                x, y = info.pt.x, info.pt.y
                rect = self._ignore_rect
                if rect and rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]:
                    return 1          # 控制条区域内的操作不录
                message = int(wParam)
                if message == _WM_MOUSEWHEEL:
                    delta = ctypes.c_short(info.mouseData >> 16).value
                    self._events.append(("wheel", x, y, delta))
                elif message == _WM_LBUTTONDOWN:
                    self._events.append(("down", x, y, "left"))
                elif message == _WM_LBUTTONUP:
                    self._events.append(("up", x, y, "left"))
                elif message == _WM_RBUTTONDOWN:
                    self._events.append(("down", x, y, "right"))
                elif message == _WM_RBUTTONUP:
                    self._events.append(("up", x, y, "right"))
                elif message == _WM_MOUSEMOVE:
                    self._events.append(("move", x, y, ""))
            # 鼠标事件不吞：用户还要能点界面上的「停止」
            return _win_user32().CallNextHookEx(None, nCode, wParam, lParam)
        except Exception:
            return _win_user32().CallNextHookEx(None, nCode, wParam, lParam)

    # ------------------------------------------------------------ 状态机
    def _schedule_poll(self) -> None:
        if self.active:
            try:
                self.widget.after(15, self._poll)
            except tk.TclError:
                pass

    def _poll(self) -> None:
        if not self.active:
            return
        self._drain()
        self._schedule_poll()

    def _drain(self) -> None:
        now = time.monotonic() * 1000
        for kind, x, y, extra in self._events:
            if kind == "down":
                self._down = (x, y, now, extra)
            elif kind == "up":
                if self._down is None:
                    continue
                dx, dy, _t, button = self._down
                self._down = None
                if abs(x - dx) > 8 or abs(y - dy) > 8:
                    # 拖拽：记 起点点击 + 终点移动
                    self._steps.append(Step("click_xy", f"{dx},{dy}"))
                    self._steps.append(Step("move_xy", f"{x},{y}"))
                else:
                    self._add_click(x, y, now, button)
                self._last_pos = (x, y)
            elif kind == "move":
                if self._down is not None:
                    continue          # 按住拖动：交给 up 时统一记
                lx, ly = self._last_pos
                if (x - lx) ** 2 + (y - ly) ** 2 > _MOVE_MIN_DISTANCE ** 2:
                    self._steps.append(Step("move_xy", f"{x},{y}"))
                    self._last_pos = (x, y)
            elif kind == "wheel":
                # Windows 以 120 为 1 格；普通鼠标每格正好 ±120，但平滑滚轮 /
                # 触摸板会发一堆小增量——用余量累加，凑满一格才记一步，
                # 剩下的留给下一次，避免亚格滚动被白白丢掉。
                self._wheel_rest += int(extra)
                notches = 0
                while abs(self._wheel_rest) >= 120:
                    step_dir = 1 if self._wheel_rest > 0 else -1
                    notches += step_dir
                    self._wheel_rest -= step_dir * 120
                if notches:
                    self._steps.append(Step("scroll", str(notches * 100)))
        self._events.clear()

    def _add_click(self, x: int, y: int, now: float, button: str) -> None:
        content = f"{x},{y}" + (f",{button}" if button != "left" else "")
        # 与上一次点击足够近 → 合并成双击
        last = self._last_click
        if (last is not None and now - last[2] < _DOUBLE_CLICK_MS
                and abs(x - last[0]) < 8 and abs(y - last[1]) < 8
                and self._steps and self._steps[-1].type == "click_xy"
                and self._steps[-1].content == content
                and self._steps[-1].repeat < 2):
            self._steps[-1].repeat = 2
            self._last_click = None
            return
        self._steps.append(Step("click_xy", content))
        self._last_click = (x, y, now)

