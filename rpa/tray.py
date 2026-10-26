# -*- coding: utf-8 -*-
"""系统托盘：零第三方依赖实现（ctypes + Shell_NotifyIconW）。

与「开机自启」天然配对：开机自启让 MeowGo 登录后自动出现，托盘让它
安静待在后台——「启动后最小化到托盘」+「关闭窗口时最小化到托盘」
两个开关（见 gui.SettingsDialog）都靠本模块落地。

设计要点：

- 托盘图标跑在**独立守护线程**的消息循环里（自建消息窗口），不占 tk 主线程；
- 图标由 PIL 从 main.png 生成临时 .ico：浅色主题用墨黑猫，深色主题把
  近黑像素反白（与界面品牌 Logo 同款处理），写进系统临时目录并按源文件
  哈希缓存，重复启动不重复生成；
- 右键菜单（显示主窗口 / 退出）与单击/双击激活都通过回调交回调用方，
  调用方用 ``invoke=lambda fn: app.after(0, fn)`` 把动作 marshal 回 tk 线程；
- 附带 :func:`hook_session_end`：注销 / 关机时不再「关窗躲托盘」，
  避免挡系统关机（关机画面一直转圈的那种）。

非 Windows 平台 :meth:`TrayIcon.start` 一律返回 ``False``，导入无副作用。
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import queue
import sys
import tempfile
import threading
import time
from ctypes import wintypes

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NOT_WINDOWS = "系统托盘只支持 Windows。"

# ---------------------------------------------------------------- 常量
WM_APP = 0x8000
WM_TRAY = WM_APP + 1            # 托盘图标回调消息
WM_WAKE = WM_APP + 2            # 唤醒消息循环：执行请求队列

NIM_ADD, NIM_MODIFY, NIM_DELETE, NIM_SETVERSION = 0x0000, 0x0001, 0x0002, 0x0004
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO, NIIF_WARNING, NIIF_ERROR = 0x1, 0x2, 0x3
NOTIFYICON_VERSION_4 = 4
NIN_BALLOONUSERCLICK = 0x0405

WM_COMMAND, WM_QUIT, WM_DESTROY = 0x0111, 0x0012, 0x0002
WM_LBUTTONUP, WM_LBUTTONDBLCLK = 0x0202, 0x0203
WM_RBUTTONDOWN, WM_RBUTTONUP, WM_CONTEXTMENU = 0x0204, 0x0205, 0x007B
WM_QUERYENDSESSION, WM_ENDSESSION = 0x0011, 0x0016

TPM_LEFTBUTTON, TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0000, 0x0002, 0x0100
MF_STRING, MF_SEPARATOR = 0x0000, 0x0800
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
HWND_MESSAGE = -3               # 消息窗口的父窗口
GWLP_WNDPROC = -4

WINDOW_CLASS = "MeowGoTrayWindow"
TIP_MAX = 127                   # szTip 128 个字符（含结尾 \0）
INFO_MAX = 255                  # szInfo 256 个字符
INFO_TITLE_MAX = 63             # szInfoTitle 64 个字符

# ---------------------------------------------------------------- 结构
user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_byte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateFlags", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeout", wintypes.UINT),      # 与 uVersion 共用同一偏移
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", _GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


# POINT 必须用 wintypes 里那一个，不要自己再定义一个同名结构体：
# ctypes 比较 argtypes 时看的是「类对象」而不是字段布局，自定义的 POINT
# 会让 user32.GetCursorPos 的 argtypes 变成 POINTER(tray.POINT)，而
# pyautogui 内部传的是 byref(wintypes.POINT) —— 立刻报
# "expected LP_POINT instance instead of pointer to POINT"，把全局所有
# pyautogui 动作（放大镜 FAILSAFE 每次都要读光标位置）一起打死。
POINT = wintypes.POINT


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD), ("pt", POINT)]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT),
                ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
                ("hIconSm", wintypes.HICON)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)

user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.RegisterClassExW.restype = wintypes.ATOM
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.DefWindowProcW.restype = ctypes.c_long
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.GetMessageW.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.PostMessageW.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.PostQuitMessage.restype = None
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.restype = wintypes.BOOL
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT,
                               ctypes.c_size_t, wintypes.LPCWSTR]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT,
                                  ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wintypes.HWND,
                                  ctypes.POINTER(POINT)]
user32.DestroyMenu.restype = wintypes.BOOL
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.GetCursorPos.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                              wintypes.UINT, ctypes.c_int, ctypes.c_int,
                              wintypes.UINT]
user32.DestroyIcon.restype = wintypes.BOOL
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.SetWindowLongPtrW.restype = ctypes.c_void_p
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int,
                                     ctypes.c_void_p]
user32.CallWindowProcW.restype = ctypes.c_long
user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                   wintypes.UINT, wintypes.WPARAM,
                                   wintypes.LPARAM]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                      ctypes.POINTER(NOTIFYICONDATAW)]


# ---------------------------------------------------------------- 图标
def icon_sources() -> list[str]:
    """托盘图标源文件（与窗口图标 main.png 同源，兼容打包后的目录）。"""
    candidates = [
        os.path.join(getattr(sys, "_MEIPASS", APP_DIR), "main.png"),
        os.path.join(APP_DIR, "main.png"),
    ]
    return [path for path in candidates if os.path.exists(path)]


def _invert_dark(image):
    """深色主题：把近黑像素反白（墨黑猫 → 白猫），保留暖橙点缀。"""
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            r, g, b, a = pixels[x, y]
            if a and max(r, g, b) < 170 and max(r, g, b) - min(r, g, b) < 36:
                pixels[x, y] = (245, 242, 237, a)
    return image


def icon_file(source: str = "", dark: bool = False) -> str:
    """生成（或复用缓存里的）托盘 .ico，返回路径；失败返回空串。"""
    if os.name != "nt":
        return ""
    path = source or (icon_sources() or [""])[0]
    if not path or not os.path.exists(path):
        return ""
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        stamp = 0
    key = hashlib.md5(f"{path}|{stamp}|{dark}".encode("utf-8")).hexdigest()[:12]
    target = os.path.join(tempfile.gettempdir(), f"meowgo_tray_{key}.ico")
    if os.path.exists(target):
        return target
    try:
        from PIL import Image

        image = Image.open(path).convert("RGBA")
        if dark:
            image = _invert_dark(image)
        width, height = image.size
        if width != height:      # 兜底裁成正方（main.png 本身就接近正方）
            side = min(width, height)
            left, top = (width - side) // 2, (height - side) // 2
            image = image.crop((left, top, left + side, top + side))
        image.save(target, format="ICO",
                   sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (48, 48)])
        return target
    except Exception:            # noqa: BLE001 — PIL 缺失/源图损坏：没有托盘图标
        return ""


def _load_hicon(path: str):
    if not path:
        return None
    handle = user32.LoadImageW(None, path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
    return handle or None


# ---------------------------------------------------------------- 会话结束防护
class _SessionHook:
    """会话结束钩子的保活对象：持着 WNDPROC 防 GC，可解除。"""

    def __init__(self, hwnd, proc, state):
        self.hwnd = hwnd
        self.proc = proc
        self._state = state

    def unhook(self) -> None:
        old = self._state.get("old")
        if old is not None:
            user32.SetWindowLongPtrW(self.hwnd, GWLP_WNDPROC, old)
            self._state["old"] = None

    def __call__(self) -> None:
        self.unhook()


def hook_session_end(window, on_query, on_end):
    """给 tk 窗口挂会话结束钩子（注销 / 关机）。

    ``on_query()``：系统询问「能不能关机」时调用——置个标志，别真退出
    （用户可能取消关机）；``on_end(ending)``：``ending=True`` 表示会话
    确定结束，此时应立刻退出，否则会挡系统关机。

    返回 :class:`_SessionHook`（调用它即解除；拿不到窗口句纲时返回 ``None``）。
    """
    if os.name != "nt":
        return None
    try:
        hwnd = int(window.winfo_id())
    except Exception:            # noqa: BLE001 — 拿不到窗口句柄就算了
        return None
    state: dict = {"old": None}

    def wnd_proc(hwnd_, message, wparam, lparam):
        if message == WM_QUERYENDSESSION:
            try:
                on_query()
            except Exception:    # noqa: BLE001
                pass
            return 1             # 允许会话继续
        if message == WM_ENDSESSION:
            try:
                on_end(bool(wparam))
            except Exception:    # noqa: BLE001
                pass
            return 0
        return user32.CallWindowProcW(state["old"], hwnd_, message,
                                      wparam, lparam)

    proc = WNDPROC(wnd_proc)
    old = user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC,
                                   ctypes.cast(proc, ctypes.c_void_p))
    if not old:
        return None
    state["old"] = ctypes.c_void_p(old)
    return _SessionHook(hwnd, proc, state)


# ---------------------------------------------------------------- 托盘图标
class TrayIcon:
    """一个系统托盘图标（Shell 通知区）。

    参数：
        tip:         悬停提示；
        on_activate: 单击 / 双击图标的动作；
        menu_items:  右键菜单，``[(标签, 回调), None, ...]``，None = 分隔线；
        invoke:      把回调 marshal 回主线程（tk 用 ``self.after``）。
    """

    def __init__(self, tip: str = "MeowGo", on_activate=None,
                 menu_items=None, invoke=None):
        self.tip = str(tip or "")
        self.on_activate = on_activate
        self.menu_items = list(menu_items or [])
        self._invoke = invoke
        self._thread: threading.Thread | None = None
        self._hwnd = None
        self._hicon = None
        self._source = ""
        self._dark = False
        self._requests: queue.Queue = queue.Queue()
        self._started = threading.Event()
        self._stopped = threading.Event()
        self._wndproc_ref = None
        self._balloon_click = None

    # ------------------------------------------------------------ 生命周期
    @property
    def running(self) -> bool:
        return self._thread is not None and not self._stopped.is_set()

    def start(self, source: str = "", dark: bool = False) -> bool:
        """弹出托盘图标并启动消息循环。成功返回 True。

        已经在跑就当作成功（幂等），不会起第二个线程。
        """
        if os.name != "nt":
            return False
        if self._thread:
            return True
        self._dark = bool(dark)
        path = icon_file(source, dark)
        icon = _load_hicon(path)
        if not icon:
            return False
        self._source = source
        self._hicon = icon
        self._stopped.clear()
        self._started.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="meowgo-tray")
        self._thread.start()
        if not self._started.wait(2.0) or self._stopped.is_set():
            self._thread = None
            return False
        return True

    def stop(self, wait: bool = True) -> None:
        """移除托盘图标并结束消息循环。"""
        if not self._thread:
            return

        def job():
            self._delete_icon()
            user32.PostQuitMessage(0)

        self._post(job)
        if wait:
            self._stopped.wait(2.0)
        self._thread = None
        self._hicon = None
        self._hwnd = None

    # ------------------------------------------------------------ 图标更新
    def set_tip(self, text: str) -> None:
        self.tip = str(text or "")[:TIP_MAX]

        def job():
            nid = self._base(NIF_TIP)
            nid.szTip = self.tip
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

        self._post(job)

    def set_icon(self, dark: bool) -> None:
        """按主题换图标（浅色 = 墨黑猫，深色 = 反白猫）。"""
        if bool(dark) == self._dark and self._hicon:
            return
        path = icon_file(self._source, dark)
        icon = _load_hicon(path)
        if not icon:
            return

        def job():
            old, self._hicon = self._hicon, icon
            nid = self._base(NIF_ICON)
            nid.hIcon = icon
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
            if old:
                user32.DestroyIcon(old)

        self._post(job)
        self._dark = bool(dark)

    def notify(self, title: str, message: str = "", on_click=None) -> None:
        """弹一个气泡提示（Windows 10/11 约显示 10 秒）。"""
        self._balloon_click = on_click
        title = str(title or "")[:INFO_TITLE_MAX]
        message = str(message or "")[:INFO_MAX]

        def job():
            nid = self._base(NIF_INFO)
            nid.szInfo = message
            nid.szInfoTitle = title
            nid.dwInfoFlags = NIIF_INFO
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

        self._post(job)

    # ------------------------------------------------------------ 内部：线程
    def _run(self) -> None:
        try:
            if not self._create_window():
                return
            if not self._add_icon():
                self._teardown_window()
                return
            self._started.set()
            self._drain_requests()
            msg = MSG()
            while True:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == 0 or result == -1:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:        # noqa: BLE001 — 托盘挂掉不影响主程序
            pass
        finally:
            self._cleanup()

    def _create_window(self) -> bool:
        def wnd_proc(hwnd, message, wparam, lparam):
            return self._on_message(hwnd, message, wparam, lparam)

        proc = WNDPROC(wnd_proc)
        wndclass = WNDCLASSEXW()
        wndclass.cbSize = ctypes.sizeof(wndclass)
        wndclass.lpfnWndProc = ctypes.cast(proc, ctypes.c_void_p)
        wndclass.hInstance = kernel32.GetModuleHandleW(None)
        wndclass.lpszClassName = WINDOW_CLASS
        if not user32.RegisterClassExW(ctypes.byref(wndclass)):
            # 类已注册过（同进程第二次启动托盘）也没关系，继续建窗口
            pass
        hwnd = user32.CreateWindowExW(
            0, WINDOW_CLASS, "MeowGo", 0, 0, 0, 0, 0,
            ctypes.c_void_p(HWND_MESSAGE), None, wndclass.hInstance, None)
        if not hwnd:
            return False
        self._wndproc_ref = proc       # 保活，防回调被 GC
        self._hwnd = hwnd
        return True

    def _teardown_window(self) -> None:
        if self._hwnd:
            user32.DestroyWindow(self._hwnd)
        self._hwnd = None
        self._wndproc_ref = None

    def _cleanup(self) -> None:
        self._delete_icon()
        self._teardown_window()
        self._stopped.set()

    def _on_message(self, hwnd, message, wparam, lparam) -> int:
        if message == WM_TRAY:
            self._on_tray_event(lparam or 0)
            return 0
        if message == WM_WAKE:
            self._drain_requests()
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _on_tray_event(self, lparam: int) -> None:
        event = lparam & 0xFFFF
        if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
            self._fire(self.on_activate)
        elif event in (WM_RBUTTONUP, WM_RBUTTONDOWN, WM_CONTEXTMENU):
            self._show_menu()
        elif event == NIN_BALLOONUSERCLICK:
            self._fire(self._balloon_click)

    def _fire(self, callback) -> None:
        if not callback:
            return
        if self._invoke:
            try:
                self._invoke(callback)
                return
            except Exception:    # noqa: BLE001 — invoke 失败就地执行
                pass
        callback()

    def _show_menu(self) -> None:
        """右键菜单：TrackPopupMenu 同步返回选中项，在本线程执行回调。"""
        commands: dict[int, object] = {}
        menu = user32.CreatePopupMenu()
        try:
            next_id = 1000
            for entry in self.menu_items:
                if entry is None:
                    user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                    continue
                label, callback = entry
                command = next_id
                next_id += 1
                if user32.AppendMenuW(menu, MF_STRING, command, str(label)):
                    commands[command] = callback
            point = POINT()
            user32.GetCursorPos(ctypes.byref(point))
            # 先把托盘窗口推到前台，否则菜单点不中的老问题还在
            user32.SetForegroundWindow(self._hwnd)
            chosen = user32.TrackPopupMenu(
                menu, TPM_RETURNCMD | TPM_RIGHTBUTTON,
                point.x, point.y, 0, self._hwnd, None)
        finally:
            user32.DestroyMenu(menu)
        callback = commands.get(chosen)
        if callback:
            self._fire(callback)

    # ------------------------------------------------------------ 内部：Shell
    def _base(self, flags: int) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(nid)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = WM_TRAY
        return nid

    def _add_icon(self) -> bool:
        nid = self._base(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        nid.hIcon = self._hicon
        nid.szTip = self.tip[:TIP_MAX]
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            return False
        # v4：右键「抬起」才弹菜单（不用按住），气球点击可收到
        version = self._base(0)
        version.uTimeout = NOTIFYICON_VERSION_4
        shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(version))
        return True

    def _delete_icon(self) -> None:
        if not self._hwnd:
            return
        nid = self._base(0)
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))

    # ------------------------------------------------------------ 内部：队列
    def _post(self, job, wait: bool = False, timeout: float = 2.0):
        """把动作排进消息循环线程执行（Shell_NotifyIcon 是线程亲和的）。"""
        if not self._thread:
            return None
        box: dict = {}

        def wrapped():
            try:
                box["result"] = job()
            except BaseException as exc:    # noqa: BLE001 — 托盘错误不外溢
                box["error"] = exc
            finally:
                box["done"] = True

        self._requests.put(wrapped)
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_WAKE, 0, 0)
        if wait:
            deadline = time.monotonic() + timeout
            while not box.get("done") and time.monotonic() < deadline:
                time.sleep(0.005)
        return box.get("result")

    def _drain_requests(self) -> None:
        while True:
            try:
                job = self._requests.get_nowait()
            except queue.Empty:
                return
            job()
