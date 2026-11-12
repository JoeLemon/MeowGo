# -*- coding: utf-8 -*-
"""图形界面：左侧执行组（可收放），右侧组步骤编辑，日志可收放。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import types
from collections import deque
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Optional

from . import __version__ as APP_VERSION
from . import (
    assets,
    autostart,
    dialogs,
    file_assoc,
    i18n,
    launchd,
    logs,
    notify,
    platform_support,
    project_io,
    report_dialogs,
    script_runner,
    secrets,
    theme,
    tray,
    uikit,
    updater,
)
from .dialogs import BaseDialog, GroupDialog, ShortcutsDialog
from .engine import (
    CONDITION_TYPES,
    FAIL_ACTION_STEP_TYPES,
    IMAGE_STEP_TYPES,
    LOOP_TYPES,
    REGION_STEP_TYPES,
    STEP_TYPES,
    TEXT_STEP_TYPES,
    TIMEOUT_STEP_TYPES,
    WAIT_IMAGE_TYPES,
    Executor,
    Group,
    Project,
    Step,
    estimate_steps,
    export_excel,
    find_images,
    import_excel,
    opencv_available,
    parse_region,
    validate_environment,
    validate_project,
    window_title,
)
from .lock import InstanceLock, make_lock_name
from .recorder import (
    KeyRecorder,
    MacroRecorder,
    vk_to_key_name,  # noqa: F401 - 保持旧的 gui.vk_to_key_name 导入路径
)
from .report_dialogs import (
    BackupDialog,
    RecentProjectsDialog,
    RunHistoryDialog,
    RunReportDialog,  # noqa: F401 - 保持旧的 gui.RunReportDialog 导入路径
)
from .schedule import (
    _valid_schedule_time,
    make_schedule_task,
    normalize_schedule_task,  # noqa: F401 - 兼容旧的 gui 导入路径
    schedule_countdown_text,
    schedule_day_text,
    schedule_due,
    schedule_next_run,
    schedule_task_label,
    schedule_tasks,
)
from .theme import (
    CONTROL_LG,
    CONTROL_MD,
    CONTROL_SM,
    CONTROL_XS,
    DARK_PALETTE,
    FONT_BODY,
    FONT_BODY_BOLD,
    FONT_CAPTION,
    FONT_CAPTION_BOLD,
    FONT_DISPLAY,
    FONT_HERO,
    FONT_MONO,
    FONT_NOTE,
    FONT_NOTE_BOLD,
    FONT_SECTION,
    FONT_SIZE_BODY,
    FONT_SIZE_CAPTION,
    FONT_SIZE_NOTE,
    FONT_TITLE,
    FONT_TITLE_LARGE,
    ICON_MD,
    ICON_SM,
    ICON_XS,
    LIGHT_PALETTE,
    TABLE_ROW_HEIGHT,
)

APP_TITLE = "MeowGo"
APP_LOGO_FILE = "meowgo_logo_transparent.png"
CARD_RADIUS = theme.RADIUS_L      # 大卡片 / 面板（--mg-radius-l）
INNER_RADIUS = theme.RADIUS_M     # 小卡片 / 弹窗（--mg-radius-m）
FIELD_RADIUS = theme.RADIUS_S     # 输入框 / 小按钮（--mg-radius-s）
HEADER_HEIGHT = theme.HEADER_HEIGHT  # 顶栏（--mg header）
UI_FONT = theme.FONT_FAMILY
MONO_FONT = theme.MONO_FAMILY


def _app_dir() -> str:
    """程序目录：打包后取 exe 所在目录，便于读写配置与图片。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


APP_DIR = _app_dir()
SETTINGS_PATH = os.path.join(APP_DIR, "rpa_settings.json")
DEFAULT_IMAGE_DIR = os.path.join(APP_DIR, "images")
REPORTS_DIR = os.path.join(APP_DIR, "reports")
SHOTS_DIR = os.path.join(REPORTS_DIR, "shots")
BACKUPS_DIR = os.path.join(APP_DIR, "backups")
MAX_CONFIG_BACKUPS = 10
MAX_LOG_LINES = 2000              # 日志上限：超出后从最早的行开始丢弃
DEFAULT_GROUPS_KEY = "default_groups_version"   # 状态文件里的预置组版本
DEFAULT_GROUPS_VERSION = 3


def image_dir_path(value: object) -> str:
    """把图片目录规范成绝对路径。

    旧配置 / 项目文件里可能存的是相对路径；GUI 从 Finder 启动、定时任务
    由 launchd 启动时工作目录都不是程序目录，相对路径会解析到错误位置。
    """
    path = str(value or "").strip() or DEFAULT_IMAGE_DIR
    return path if os.path.isabs(path) else os.path.join(APP_DIR, path)


def _post_webhook(url: str, text: str, timeout: float = 10) -> None:
    """向企业微信 / 钉钉机器人 Webhook 发送一条文本消息。

    失败时抛异常，由调用方决定是写日志还是提示用户。
    """
    import json
    from urllib.request import Request, urlopen

    payload = {"msgtype": "text", "text": {"content": text}}
    request = Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310
        response.read()
# 步骤表列宽随界面状态保存；#0 在配置里用 icon 作为键，避免特殊字符。
STEP_COLUMN_STATE_KEYS = (
    ("#0", "icon"),
    ("no", "no"),
    ("type", "type"),
    ("content", "content"),
    ("repeat", "repeat"),
    ("note", "note"),
)
# v1 只预置了这两个组。升级到 v2 时只“原地升级”未改过的浏览器组，
# 不把用户已经删掉的旧组加回来。
DEFAULT_GROUPS_V1_NAMES = {"打开浏览器", "打开软件"}
LEGACY_BROWSER_NOTE = "预置模板：改前两步变量即可运行；浏览器启动慢时把等待秒数改大。"
LEGACY_BROWSER_SIGNATURE = (
    ("set", "浏览器=chrome", 0, "chrome / msedge / 完整 exe 路径"),
    ("set", "网址=https://www.baidu.com", 0, "要打开的链接"),
    ("cmd", 'start "" "{浏览器}"', 0, "按变量启动浏览器"),
    ("wait", "2", 0, "等浏览器启动；慢就改大"),
    ("hotkey", "ctrl,l", 0, "聚焦地址栏"),
    ("input", "{网址}", 0, "输入网址"),
    ("hotkey", "enter", 0, "打开网址"),
)

# 定时任务：settings 里的 schedules 是任务列表，schedule 是旧版单任务字段，
# 现在仍作为第一项镜像保留（老配置 / 老测试都还能读）。
SCHEDULE_HISTORY_LIMIT = 60
SCHEDULE_RESULT_TEXT = {
    "running": "运行中",
    "finished": "成功",
    "stopped": "已停止",
    "error": "出错",
    "skipped": "已跳过",
}


# 配色唯一来源是 rpa.theme；这里把用到的浅色令牌点名为模块常量，既有代码
# 可直接写 BG / CARD / ACCENT ...。_apply_theme 切主题时由 _apply_palette
# 整组替换。显式赋值（而不是 globals().update）是为了让 linter / IDE 能看见
# 这些名字，拼错色板名时能在静态检查阶段报出来。
BG = LIGHT_PALETTE["BG"]
CARD = LIGHT_PALETTE["CARD"]
ACCENT = LIGHT_PALETTE["ACCENT"]
ACCENT_STRONG = LIGHT_PALETTE["ACCENT_STRONG"]
ACCENT_MIST = LIGHT_PALETTE["ACCENT_MIST"]
TEXT = LIGHT_PALETTE["TEXT"]
MUTED = LIGHT_PALETTE["MUTED"]
TEXT_WEAK = LIGHT_PALETTE["TEXT_WEAK"]
BORDER = LIGHT_PALETTE["BORDER"]
SUCCESS = LIGHT_PALETTE["SUCCESS"]
WARNING = LIGHT_PALETTE["WARNING"]
DANGER = LIGHT_PALETTE["DANGER"]
INFO = LIGHT_PALETTE["INFO"]
SUCCESS_TEXT = LIGHT_PALETTE["SUCCESS_TEXT"]
DANGER_TEXT = LIGHT_PALETTE["DANGER_TEXT"]
LOG_BG = LIGHT_PALETTE["LOG_BG"]
LOG_FG = LIGHT_PALETTE["LOG_FG"]
LOG_HEAD_BG = LIGHT_PALETTE["LOG_HEAD_BG"]
LOG_HEAD_MUTED = LIGHT_PALETTE["LOG_HEAD_MUTED"]
LOG_INFO = LIGHT_PALETTE["LOG_INFO"]
LOG_WARN = LIGHT_PALETTE["LOG_WARN"]
LOG_ERROR = LIGHT_PALETTE["LOG_ERROR"]
LOG_TIME = LIGHT_PALETTE["LOG_TIME"]
CONSOLE_SCROLL = LIGHT_PALETTE["CONSOLE_SCROLL"]
ZEBRA = LIGHT_PALETTE["ZEBRA"]
HEADING_BG = LIGHT_PALETTE["HEADING_BG"]
SELECT_BG = LIGHT_PALETTE["SELECT_BG"]
TABLE_HEAD_BG = LIGHT_PALETTE["TABLE_HEAD_BG"]
TABLE_HEAD_FG = LIGHT_PALETTE["TABLE_HEAD_FG"]
TABLE_BORDER = LIGHT_PALETTE["TABLE_BORDER"]
TABLE_ROW_SELECTED = LIGHT_PALETTE["TABLE_ROW_SELECTED"]
FIELD_FOCUS = LIGHT_PALETTE["FIELD_FOCUS"]
ENTRY_BG = LIGHT_PALETTE["ENTRY_BG"]
SCROLL_THUMB = LIGHT_PALETTE["SCROLL_THUMB"]
SCROLL_TRACK = LIGHT_PALETTE["SCROLL_TRACK"]
PROGRESS_TROUGH = LIGHT_PALETTE["PROGRESS_TROUGH"]
PREVIEW_BG = LIGHT_PALETTE["PREVIEW_BG"]
MARK_PENDING = LIGHT_PALETTE["MARK_PENDING"]
MARK_RUNNING = LIGHT_PALETTE["MARK_RUNNING"]
MARK_DONE = LIGHT_PALETTE["MARK_DONE"]
MARK_FAILED = LIGHT_PALETTE["MARK_FAILED"]


def _apply_palette(palette: dict) -> None:
    """把色板写回上面的模块级常量（切主题时调用）。"""
    global BG, CARD, ACCENT, ACCENT_STRONG, ACCENT_MIST, TEXT, MUTED
    global TEXT_WEAK, BORDER, SUCCESS, WARNING, DANGER, INFO
    global SUCCESS_TEXT, DANGER_TEXT
    global LOG_BG, LOG_FG, LOG_HEAD_BG, LOG_HEAD_MUTED
    global LOG_INFO, LOG_WARN, LOG_ERROR, LOG_TIME, CONSOLE_SCROLL
    global ZEBRA, HEADING_BG, SELECT_BG
    global TABLE_HEAD_BG, TABLE_HEAD_FG, TABLE_BORDER, TABLE_ROW_SELECTED
    global FIELD_FOCUS, ENTRY_BG, SCROLL_THUMB, SCROLL_TRACK
    global PROGRESS_TROUGH, PREVIEW_BG
    global MARK_PENDING, MARK_RUNNING, MARK_DONE, MARK_FAILED
    BG = palette["BG"]
    CARD = palette["CARD"]
    ACCENT = palette["ACCENT"]
    ACCENT_STRONG = palette["ACCENT_STRONG"]
    ACCENT_MIST = palette["ACCENT_MIST"]
    TEXT = palette["TEXT"]
    MUTED = palette["MUTED"]
    TEXT_WEAK = palette["TEXT_WEAK"]
    BORDER = palette["BORDER"]
    SUCCESS = palette["SUCCESS"]
    WARNING = palette["WARNING"]
    DANGER = palette["DANGER"]
    INFO = palette["INFO"]
    SUCCESS_TEXT = palette["SUCCESS_TEXT"]
    DANGER_TEXT = palette["DANGER_TEXT"]
    LOG_BG = palette["LOG_BG"]
    LOG_FG = palette["LOG_FG"]
    LOG_HEAD_BG = palette["LOG_HEAD_BG"]
    LOG_HEAD_MUTED = palette["LOG_HEAD_MUTED"]
    LOG_INFO = palette["LOG_INFO"]
    LOG_WARN = palette["LOG_WARN"]
    LOG_ERROR = palette["LOG_ERROR"]
    LOG_TIME = palette["LOG_TIME"]
    CONSOLE_SCROLL = palette["CONSOLE_SCROLL"]
    ZEBRA = palette["ZEBRA"]
    HEADING_BG = palette["HEADING_BG"]
    SELECT_BG = palette["SELECT_BG"]
    TABLE_HEAD_BG = palette["TABLE_HEAD_BG"]
    TABLE_HEAD_FG = palette["TABLE_HEAD_FG"]
    TABLE_BORDER = palette["TABLE_BORDER"]
    TABLE_ROW_SELECTED = palette["TABLE_ROW_SELECTED"]
    FIELD_FOCUS = palette["FIELD_FOCUS"]
    ENTRY_BG = palette["ENTRY_BG"]
    SCROLL_THUMB = palette["SCROLL_THUMB"]
    SCROLL_TRACK = palette["SCROLL_TRACK"]
    PROGRESS_TROUGH = palette["PROGRESS_TROUGH"]
    PREVIEW_BG = palette["PREVIEW_BG"]
    MARK_PENDING = palette["MARK_PENDING"]
    MARK_RUNNING = palette["MARK_RUNNING"]
    MARK_DONE = palette["MARK_DONE"]
    MARK_FAILED = palette["MARK_FAILED"]

TYPE_ICONS = {
    "click": "image", "dblclick": "image", "rightclick": "image",
    "input": "text", "wait": "clock", "scroll": "scroll",
    "hotkey": "keyboard", "time": "calendar", "cmd": "terminal",
    "call": "link", "if_image": "check", "window": "open",
    "click_xy": "mouse", "move_xy": "mouse",
    "if_not_image": "check", "if_else": "check", "if_window": "open",
    "set": "text", "copy": "copy", "drag": "mouse",
    "click_text": "text", "wait_text": "clock", "assert_text": "check",
    "ui_click": "mouse", "if_var": "check", "http": "link",
    "for_each": "redo", "while_var": "redo", "break": "close",
    "continue": "next", "file": "folder_open", "json": "table",
    "regex": "search", "date": "calendar", "clipboard": "copy",
    "random_wait": "clock", "section": "note",
    "wait_image": "eye", "wait_gone": "eye", "ocr_read": "text",
    "script": "terminal", "notify": "meow",
}

# 条件步骤里「图片 | 组名」两段式的类型（对话框用图形化双输入框）
PIPE_CONDITION_TYPES = ("if_image", "if_not_image", "if_window", "if_var")

# 新增步骤对话框里「执行组」分类：这些类型会调用 / 进入其它执行组。
# 其余类型统一归入「基础」，方便在下拉里按分组浏览。
TYPE_GROUP_EXEC = {"call", "if_image", "if_not_image", "if_else",
                   "if_window", "if_var", "for_each", "while_var"}

# 每种步骤类型的图标色调：遵循「墨黑为主、暖橙点睛、语义色克制」。
# 鼠标 / 操作类用暖橙，输入类用信息蓝，等待类用警告黄，键盘类用成功绿，
# 条件类用错误红；其余使用暖中性灰，避免界面出现彩虹色。
TYPE_COLORS = {
    "click": ACCENT_STRONG, "dblclick": ACCENT_STRONG,
    "rightclick": ACCENT_STRONG, "click_xy": ACCENT_STRONG,
    "move_xy": ACCENT_STRONG, "drag": ACCENT_STRONG,
    "input": INFO, "time": INFO,
    "set": MUTED, "copy": MUTED,
    "wait": WARNING, "scroll": WARNING,
    "hotkey": SUCCESS, "cmd": SUCCESS,
    "call": TEXT_WEAK,
    "if_image": DANGER, "if_not_image": DANGER, "if_else": DANGER,
    "if_window": DANGER, "window": DANGER,
    "click_text": INFO, "wait_text": WARNING, "assert_text": DANGER,
    "ui_click": ACCENT_STRONG, "if_var": DANGER, "http": SUCCESS,
    "for_each": TEXT_WEAK, "while_var": TEXT_WEAK,
    "break": TEXT_WEAK, "continue": TEXT_WEAK,
    "file": MUTED, "json": INFO, "regex": INFO, "date": INFO,
    "clipboard": MUTED, "random_wait": WARNING, "section": TEXT_WEAK,
    "wait_image": WARNING, "wait_gone": WARNING, "ocr_read": INFO,
    "script": SUCCESS, "notify": INFO,
}
TYPE_ICON_COLOR = MUTED
# 深色下把类型图标提亮一档，避免深底看不清（_apply_theme 时整组替换）
TYPE_COLORS_DARK = dict(TYPE_COLORS)
LIGHT_TYPE_COLORS = dict(TYPE_COLORS)   # 留底：切回浅色时整组还原
TYPE_COLORS_DARK.update({
    "click": DARK_PALETTE["ACCENT"], "dblclick": DARK_PALETTE["ACCENT"],
    "rightclick": DARK_PALETTE["ACCENT"], "click_xy": DARK_PALETTE["ACCENT"],
    "move_xy": DARK_PALETTE["ACCENT"], "drag": DARK_PALETTE["ACCENT"],
    "input": DARK_PALETTE["INFO"], "time": DARK_PALETTE["INFO"],
    "set": DARK_PALETTE["MUTED"], "copy": DARK_PALETTE["MUTED"],
    "wait": DARK_PALETTE["WARNING"], "scroll": DARK_PALETTE["WARNING"],
    "hotkey": DARK_PALETTE["SUCCESS"], "cmd": DARK_PALETTE["SUCCESS"],
    "call": DARK_PALETTE["MUTED"],
    "if_image": DARK_PALETTE["DANGER"], "if_not_image": DARK_PALETTE["DANGER"],
    "if_else": DARK_PALETTE["DANGER"], "if_window": DARK_PALETTE["DANGER"],
    "window": DARK_PALETTE["DANGER"],
    "click_text": DARK_PALETTE["INFO"], "wait_text": DARK_PALETTE["WARNING"],
    "assert_text": DARK_PALETTE["DANGER"],
    "ui_click": DARK_PALETTE["ACCENT"], "if_var": DARK_PALETTE["DANGER"],
    "http": DARK_PALETTE["SUCCESS"],
    "for_each": DARK_PALETTE["TEXT_WEAK"],
    "while_var": DARK_PALETTE["TEXT_WEAK"],
    "break": DARK_PALETTE["TEXT_WEAK"],
    "continue": DARK_PALETTE["TEXT_WEAK"],
    "file": DARK_PALETTE["MUTED"], "json": DARK_PALETTE["INFO"],
    "regex": DARK_PALETTE["INFO"], "date": DARK_PALETTE["INFO"],
    "clipboard": DARK_PALETTE["MUTED"],
    "random_wait": DARK_PALETTE["WARNING"],
    "section": DARK_PALETTE["TEXT_WEAK"],
    "wait_image": DARK_PALETTE["WARNING"],
    "wait_gone": DARK_PALETTE["WARNING"],
    "ocr_read": DARK_PALETTE["INFO"],
    "script": DARK_PALETTE["SUCCESS"],
    "notify": DARK_PALETTE["INFO"],
})

# 步骤搜索框的占位文字（失焦为空时显示，不计入过滤条件）
SEARCH_PLACEHOLDER = "搜索步骤…"


def _search_placeholder() -> str:
    """搜索框占位符的当前语言写法。"""
    return i18n.tr(SEARCH_PLACEHOLDER)


def _is_search_placeholder(text: str) -> bool:
    return text in i18n.variants(SEARCH_PLACEHOLDER)


def _make_no_activate(window) -> None:
    """让无边框窗口不抢焦点（拖拽落点指示线用；失败也只是普通置顶窗）。"""
    try:
        hwnd = ctypes.wintypes.HWND(int(window.winfo_id()))
        user32 = ctypes.windll.user32
        user32.GetWindowLongW.argtypes = [ctypes.wintypes.HWND,
                                          ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [ctypes.wintypes.HWND,
                                          ctypes.c_int, ctypes.c_long]
        user32.SetWindowLongW.restype = ctypes.c_long
        gwl_exstyle, no_activate = -20, 0x08000000
        user32.SetWindowLongW(hwnd, gwl_exstyle,
                              user32.GetWindowLongW(hwnd, gwl_exstyle)
                              | no_activate)
    except Exception:  # noqa: BLE001 — 非 Windows / 老系统无所谓
        pass


def _win_foreground_window() -> Optional[int]:
    """当前前台窗口的句柄；非 Windows / 取不到时返回 None。"""
    if not platform_support.IS_WINDOWS:
        return None
    try:
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
        hwnd = user32.GetForegroundWindow()
        return int(hwnd) if hwnd else None
    except Exception:  # noqa: BLE001 — 取不到就当没有目标窗口
        return None


def _win_window_pid(hwnd: int) -> int:
    """窗口属于哪个进程；取不到返回 0。"""
    try:
        user32 = ctypes.windll.user32
        user32.GetWindowThreadProcessId.argtypes = [
            ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(ctypes.wintypes.HWND(hwnd),
                                        ctypes.byref(pid))
        return int(pid.value)
    except Exception:  # noqa: BLE001
        return 0


def _target_foreground_window() -> Optional[int]:
    """当前前台窗口里属于其它程序的句柄（单步调试结束后要还给它的窗口）。"""
    hwnd = _win_foreground_window()
    if hwnd and _win_window_pid(hwnd) != os.getpid():
        return hwnd
    return None


def _focus_window(hwnd: Optional[int]) -> None:
    """把前台焦点还给目标窗口（单步确认框关掉后调用）。"""
    if not platform_support.IS_WINDOWS or not hwnd:
        return
    try:
        user32 = ctypes.windll.user32
        user32.IsWindow.argtypes = [ctypes.wintypes.HWND]
        user32.IsWindow.restype = ctypes.wintypes.BOOL
        user32.IsIconic.argtypes = [ctypes.wintypes.HWND]
        user32.IsIconic.restype = ctypes.wintypes.BOOL
        user32.ShowWindow.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = ctypes.wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [ctypes.wintypes.HWND]
        user32.SetForegroundWindow.restype = ctypes.wintypes.BOOL
        user32.BringWindowToTop.argtypes = [ctypes.wintypes.HWND]
        user32.BringWindowToTop.restype = ctypes.wintypes.BOOL
        handle = ctypes.wintypes.HWND(hwnd)
        if not user32.IsWindow(handle):
            return
        if user32.IsIconic(handle):
            user32.ShowWindow(handle, 9)          # SW_RESTORE
        user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
        current = user32.GetForegroundWindow()
        if current and int(current) == hwnd:
            return
        user32.BringWindowToTop(handle)
        if user32.SetForegroundWindow(handle):
            return
        # 前台锁生效时再模拟一次 Alt（与 engine._force_foreground 相同），
        # 只在必要时才按，避免无谓地触发目标程序的菜单。
        user32.keybd_event(0x12, 0, 0, 0)          # VK_MENU down
        user32.keybd_event(0x12, 0, 2, 0)          # VK_MENU up
        user32.SetForegroundWindow(handle)
        user32.BringWindowToTop(handle)
    except Exception:  # noqa: BLE001 — 焦点恢复失败不该影响运行
        pass


def _insert_y(tree, y: int) -> Optional[int]:
    """拖拽落点的屏幕 Y 坐标：悬停行的上半段→行顶，下半段→行底。"""
    children = tree.get_children()
    if not children:
        return None
    row = tree.identify_row(y)
    if row:
        bbox = tree.bbox(row)
        if not bbox:
            return None
        _x, top, _width, height = bbox
        return tree.winfo_rooty() + (top if y < top + height / 2
                                     else top + height)
    # 指针在行外的空白处：贴到最近一端
    first = tree.bbox(children[0])
    if first and y < first[1]:
        return tree.winfo_rooty() + first[1]
    last = tree.bbox(children[-1])
    if last:
        return tree.winfo_rooty() + last[1] + last[3]
    return None


def enable_tree_drag(tree, on_drop, show_line: bool = True) -> None:
    """给 Treeview 加拖拽换行（组树 / 步骤树共用）。

    按住并移动超过 8 px 才算拖拽，因此单击选中、双击编辑都不受影响；
    拖拽过程中画一条「插入到这里」的横线作为落点提示（比整行选中更直观），
    落到最后一行下方时直接排到末尾。
    on_drop(source_iid, target_iid) 在松开时调用。
    """
    state: dict[str, Any] = {"iid": None, "y": 0, "dragging": False,
                             "line": None}

    def hide_line() -> None:
        line, state["line"] = state["line"], None
        if line is not None:
            try:
                line.destroy()
            except tk.TclError:
                pass

    def move_line(event) -> None:
        line = state["line"]
        if line is None:
            return
        y = _insert_y(tree, event.y)
        if y is None:
            try:
                line.withdraw()
            except tk.TclError:
                hide_line()
            return
        try:
            line.deiconify()
            line.geometry(f"{tree.winfo_width()}x3"
                          f"+{tree.winfo_rootx()}+{int(y)}")
        except tk.TclError:
            hide_line()

    def on_press(event):
        state["iid"] = tree.identify_row(event.y)
        state["y"] = event.y
        state["dragging"] = False

    def on_motion(event):
        if state["iid"] is None:
            return
        if not state["dragging"] and abs(event.y - state["y"]) < 8:
            return                      # 还没超过阈值，算单击
        if not state["dragging"] and show_line:
            try:
                line = tk.Toplevel(tree)
                line.overrideredirect(True)
                line.attributes("-topmost", True)
                line.configure(bg=ACCENT)
                _make_no_activate(line)
                state["line"] = line
            except tk.TclError:
                state["line"] = None
        state["dragging"] = True
        move_line(event)

    def on_release(event):
        iid, dragging = state["iid"], state["dragging"]
        state["iid"] = None
        state["dragging"] = False
        hide_line()
        if not (iid and dragging):
            return
        target = tree.identify_row(event.y)
        if not target:                  # 落到最后一行下方 → 排到末尾
            children = tree.get_children()
            if children:
                bbox = tree.bbox(children[-1])
                if bbox and event.y > bbox[1] + bbox[3]:
                    target = children[-1]
        if target and target != iid:
            on_drop(iid, target)

    tree.bind("<ButtonPress-1>", on_press, add="+")
    tree.bind("<B1-Motion>", on_motion, add="+")
    tree.bind("<ButtonRelease-1>", on_release, add="+")


def enable_dpi_awareness() -> None:
    """声明 DPI 感知，使 Tk 坐标与屏幕物理像素一致（截图/点击才准确）。

    必须在创建 Tk 根窗口之前调用。优先 per-monitor v2：多屏各自缩放时
    坐标不会互相漂移；老系统逐级退回 per-monitor → system aware。
    """
    if sys.platform != "win32":
        return
    import ctypes

    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4（Win10 1703+）
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(
                ctypes.c_void_p(-4)):
            pass
        else:
            raise OSError("SetProcessDpiAwarenessContext 失败")
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
        except Exception:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)  # system
            except Exception:
                try:
                    ctypes.windll.user32.SetProcessDPIAware()  # 旧系统
                except Exception:
                    pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MeowGo.GUI")
    except Exception:
        pass


# 预置模板里的启动命令 / 默认程序按平台生成：Windows 用 start，macOS 用
# open -a。以前把 start 写死在模板里，macOS 上必然报 127（start 不存在），
# 浏览器起不来，后面的等窗口步骤就只剩超时。
_TEMPLATE_VALUES = {
    "windows": {
        "browser": "chrome",
        "browser_note": "chrome / msedge / 完整 exe 路径",
        "browser_cmd": 'start "" "{浏览器}"',
        "browser_url_cmd": 'start "" "{浏览器}" "{网址}"',
        "software": "notepad",
        "software_note": "exe 名称或完整路径，如 notepad / calc。",
        "software_first_note": "首先打开的程序，如 notepad / calc / 完整路径。",
        "software_cmd": 'start "" "{软件}"',
        "software_group_note": "预置模板：请将「软件」变量修改为要打开的程序"
                               "名称或完整 exe 路径。",
        "file_path": "C:\\Users\\Public\\Documents\\示例.txt",
        "file_hotkey": "alt,n",
        "file_hotkey_note": "将焦点切换至文件名输入框。",
    },
    "macos": {
        "browser": "Google Chrome",
        "browser_note": "Google Chrome / Safari / Firefox，或完整 .app 名称",
        "browser_cmd": 'open -a "{浏览器}"',
        "browser_url_cmd": 'open -a "{浏览器}" "{网址}"',
        "software": "TextEdit",
        "software_note": "应用名称或完整 .app 路径，如 TextEdit / Safari。",
        "software_first_note": "首先打开的应用，如 TextEdit / Safari / 完整 .app 路径。",
        "software_cmd": 'open -a "{软件}"',
        "software_group_note": "预置模板：请将「软件」变量修改为要打开的应用"
                               "名称或完整 .app 路径。",
        "file_path": "/Users/Shared/示例.txt",
        "file_hotkey": "command,shift,g",
        "file_hotkey_note": "Cmd+Shift+G 打开「前往文件夹」，输入完整路径。",
    },
}
# 旧版 Windows 模板里的启动命令 → 当前平台写法。升级时只替换这些 cmd 步骤，
# 用户改过的变量 / 备注 / 步骤顺序都不动。
_LAUNCH_COMMANDS = {
    "windows": {},
    "macos": {
        'start "" "{浏览器}"': 'open -a "{浏览器}"',
        'start "" "{软件}"': 'open -a "{软件}"',
        'start "" "{浏览器}" "{网址}"': 'open -a "{浏览器}" "{网址}"',
    },
}


def template_platform() -> str:
    """当前系统的预置模板分支（模板只区分 Windows / macOS）。"""
    return "macos" if platform_support.IS_MACOS else "windows"


def build_default_groups(platform: str = "") -> list[Group]:
    """首次启动时预置的常用执行组（每次返回新对象，避免共享状态）。"""
    v = _TEMPLATE_VALUES.get(platform or template_platform(),
                             _TEMPLATE_VALUES["windows"])
    browser = Group(
        "打开浏览器",
        [
            Step("set", f"浏览器={v['browser']}", note=v["browser_note"]),
            Step("set", "窗口标题=Chrome",
                 note="窗口标题关键词；Edge 填写 Edge，Firefox 填写 Firefox。"),
            Step("set", "网址=https://www.baidu.com",
                 note="要打开的链接。"),
            Step("cmd", v["browser_cmd"],
                 note="按变量启动浏览器。"),
            Step("window", "wait:{窗口标题}|10",
                 note="等待窗口出现并激活；若等待时间不足，请适当增大。"),
            Step("hotkey", "ctrl,l", note="聚焦地址栏。"),
            Step("input", "{网址}", note="输入网址。"),
            Step("hotkey", "enter", note="打开网址。"),
        ],
        note="预置模板：请修改「浏览器」「窗口标题」「网址」变量；"
             "窗口标题填写浏览器窗口标题中的关键词。",
    )
    software = Group(
        "打开软件",
        [
            Step("set", f"软件={v['software']}", note=v["software_note"]),
            Step("cmd", v["software_cmd"],
                 note="按变量启动软件；若无法找到，请填写完整路径。"),
        ],
        note=v["software_group_note"],
    )
    batch_urls = Group(
        "批量打开网址",
        [
            Step("set", f"浏览器={v['browser']}", note=v["browser_note"]),
            Step("set", "网址=https://www.baidu.com",
                 note="默认网址；带数据运行时由「网址」列覆盖。"),
            Step("cmd", v["browser_url_cmd"],
                 note="每个数据行打开一个链接。"),
            Step("wait", "1",
                 note="间隔 1 秒，避免一次打开过多页面。"),
        ],
        note="预置模板：配合 Excel / CSV 的「网址」列进行数据驱动运行；"
             "无数据时打开默认网址。",
    )
    upload = Group(
        "上传文件",
        [
            Step("set", f"文件路径={v['file_path']}",
                 note="要上传文件的完整路径。"),
            Step("click", "上传按钮.png",
                 note="请先将页面上的上传按钮截图保存为该文件名。"),
            Step("wait", "0.8",
                 note="等待文件选择框出现。"),
            Step("hotkey", v["file_hotkey"],
                 note=v["file_hotkey_note"]),
            Step("input", "{文件路径}",
                 note="粘贴完整路径；也可在数据中提供「文件路径」列。"),
            Step("hotkey", "enter", note="确认上传。"),
        ],
        note="预置模板：先将上传按钮截图保存为 images/上传按钮.png，"
             "再修改「文件路径」变量。",
    )
    workspace = Group(
        "打开工作环境",
        [
            Step("set", f"软件={v['software']}",
                 note=v["software_first_note"]),
            Step("set", f"浏览器={v['browser']}", note=v["browser_note"]),
            Step("set", "网址=https://www.baidu.com",
                 note="要打开的链接。"),
            Step("cmd", v["software_cmd"],
                 note="启动常用软件。"),
            Step("wait", "0.5",
                 note="为软件预留启动时间。"),
            Step("cmd", v["browser_url_cmd"],
                 note="启动浏览器并打开网址。"),
        ],
        note="预置模板：修改「软件」「浏览器」「网址」变量，一键进入工作状态；"
             "可复制 cmd 步骤添加更多程序。",
    )
    return [browser, software, batch_urls, upload, workspace]


def _is_default_placeholder(group: Group) -> bool:
    """早期版本自动建的空「默认组」，只是占位符，没有用户内容。"""
    return (group.name == "默认组" and not group.steps
            and not group.note.strip())


def _matches_legacy_browser(group: Group) -> bool:
    """判断组是否仍是 v1 原封不动的浏览器模板（用户改过就不动）。"""
    if group.note != LEGACY_BROWSER_NOTE:
        return False
    signature = tuple(
        (step.type, step.content, step.repeat, step.note)
        for step in group.steps)
    return signature == LEGACY_BROWSER_SIGNATURE


def _upgrade_template_groups(project: Project) -> bool:
    """v2 → v3：把仍是旧 Windows 模板的步骤换成当前平台的写法。

    只替换与旧模板完全一致的步骤，用户改过的变量值、备注、步骤顺序都不动；
    自定义组里照抄的 Windows 启动命令也一并换成 open -a。
    """
    platform = template_platform()
    if platform == "windows":
        return False
    old = {g.name: g for g in build_default_groups("windows")}
    new = {g.name: g for g in build_default_groups(platform)}
    changed = False
    for name, old_group in old.items():
        group = project.get(name)
        new_group = new.get(name)
        if group is None or new_group is None:
            continue
        if len(group.steps) == len(old_group.steps):
            for step, old_step, new_step in zip(
                    group.steps, old_group.steps, new_group.steps):
                if (step.type, step.content, step.repeat) != (
                        old_step.type, old_step.content, old_step.repeat):
                    continue
                if step.content != new_step.content:
                    step.content = new_step.content
                    changed = True
                if step.note == old_step.note and step.note != new_step.note:
                    step.note = new_step.note
                    changed = True
        if group.note == old_group.note and group.note != new_group.note:
            group.note = new_group.note
            changed = True

    for group in project.groups:
        for step in group.steps:
            if step.type != "cmd":
                continue
            replacement = _LAUNCH_COMMANDS[platform].get(
                (step.content or "").strip())
            if replacement:
                step.content = replacement
                changed = True
    return changed


def seed_default_groups(project: Project, settings: dict) -> bool:
    """按版本补充预置执行组，返回是否改动了组。

    只在首次启动（或预置版本升级）时执行；按组名去重，不覆盖用户改过的
    组。补完后写回版本号，用户以后删掉预置组也不会再次出现。
    """
    try:
        seeded = int(settings.get(DEFAULT_GROUPS_KEY, 0) or 0)
    except (TypeError, ValueError):
        seeded = 0
    if seeded >= DEFAULT_GROUPS_VERSION:
        return False

    # 早期版本在空项目里自动建过一个空「默认组」，它只是占位符；
    # 仅当它是唯一的空组时才清掉，避免把用户自己建的空组误删。
    if len(project.groups) == 1 and _is_default_placeholder(project.groups[0]):
        project.groups.clear()

    templates = build_default_groups()
    changed = _upgrade_template_groups(project)
    if seeded == 1:
        # v1 → v2：只升级没改过的浏览器模板，并补 v2 新增的模板；
        # 不把用户已删掉的「打开浏览器 / 打开软件」加回来。
        browser = project.get("打开浏览器")
        if browser is not None and _matches_legacy_browser(browser):
            template = next(g for g in templates if g.name == "打开浏览器")
            browser.steps = [step.copy() for step in template.steps]
            browser.note = template.note
            changed = True
        templates = [g for g in templates
                     if g.name not in DEFAULT_GROUPS_V1_NAMES]

    existing = set(project.names())
    for group in templates:
        if group.name in existing:
            continue
        project.groups.append(group)
        existing.add(group.name)
        changed = True
    settings[DEFAULT_GROUPS_KEY] = DEFAULT_GROUPS_VERSION
    return changed


def _native_accelerator(text: str) -> str:
    """把 Ctrl+Z / F5 这类提示转成 macOS 菜单栏右侧的快捷键。

    像「从当前行继续」「CSV/Excel」这样的说明文字不是快捷键，返回空串，
    避免系统菜单里出现一长串伪快捷键。
    """
    shortcut = platform_support.hotkey_label(str(text or "").strip())
    if not shortcut:
        return ""
    if "+" in shortcut or re.fullmatch(r"F\d+|\?", shortcut):
        return shortcut.replace("Enter", "Return")
    return ""


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        # 测试 / 重复建根窗口时，旧的 PhotoImage 属于已销毁的根，必须清掉
        uikit.clear_icon_cache()
        self.title(APP_TITLE)
        # 先读设置：界面缩放（ui_scale）要在计算 scale / 字体前生效
        self.settings = self._load_settings()
        self.scale = self._init_scaling()
        self.geometry(f"{self.s(1180)}x{self.s(760)}")
        self.minsize(self.s(1000), self.s(640))
        self.configure(bg=BG)
        # 界面语言：在创建任何控件之前安装翻译层，后面新建的对话框也会自动生效
        i18n.install()
        i18n.set_language(str(self.settings.get("language", "zh")))
        # 执行组默认保存在状态文件；打开 .mgproj 时以项目文件为准
        self.project = Project.from_dict(self.settings)
        self.image_dir = image_dir_path(
            self.settings.get("image_dir", DEFAULT_IMAGE_DIR))
        self.project.image_dir = self.image_dir
        # 项目文件：启动时恢复上次打开的项目；文件丢失就退回状态文件里的流程
        self.project_path = ""
        saved_project = str(self.settings.get("project_path") or "")
        if saved_project:
            try:
                project, meta = project_io.load_project(saved_project)
            except ValueError:
                pass
            else:
                self.project = project
                self.image_dir = image_dir_path(
                    meta.get("image_dir") or self.image_dir)
                self.project.image_dir = self.image_dir
                self.project_path = saved_project
        self.group_index = 0          # 当前编辑的组
        self.log_visible = bool(self.settings.get("log_visible", True))
        # 主题：light / dark（_apply_theme 在界面建好后整组换色）
        self.dark = str(self.settings.get(
            "theme",
            "dark" if platform_support.system_dark_mode() else "light")) == "dark"
        self._last_palette = dict(LIGHT_PALETTE)
        # 单步调试：每步执行前弹窗确认（运行 / 跳过 / 停止）
        self.debug_mode = bool(self.settings.get("debug_mode", False))
        self._debug_once = False       # 一次性调试：只作用下一程运行
        self._debug_continue = False   # 本程是否已选择「继续运行」
        self._debug_dialog: Optional[DebugStepDialog] = None
        self._pending_step: Optional[tuple[str, int]] = None
        # 单步调试时被操作的目标窗口：确认框关掉后把焦点还给它
        self._debug_target: Optional[int] = None
        # 「运行到这一步」的断点：到这一步才开始单步确认
        self._debug_breakpoint: Optional[tuple[str, int]] = None
        self._dirty = False
        self._last_saved_payload: Optional[str] = None
        self._last_save_error = ""
        self._icons: dict[tuple, object] = {}
        # 运行辅助：失败步骤定位、日志行→步骤映射、上次报告路径
        self._failed_step: Optional[tuple[str, int]] = None
        self._current_run_step: Optional[tuple[str, int]] = None

        self._step_marks: dict[str, dict[int, str]] = {}
        self._last_report_path = ""
        self._last_result_path = ""
        self._last_run_log_path = ""
        self._run_log: Optional[logs.RunLog] = None
        self._data_path = ""
        self._run_lock: Optional[InstanceLock] = None
        # 运行进度：estimate_steps 的总步数（-1 表示不可估）、迷你置顶条
        self._progress_total = -1
        self._progress_pulse = False
        self._mini_bar: Optional[RunMiniBar] = None
        # 系统托盘：设置里开了任一托盘开关才创建图标（见 _init_tray）
        self.tray: Any = None
        self._tray_window_visible = True
        self._session_ending = False       # 注销 / 关机中：不再躲托盘
        self._session_hook = None
        self._quitting = False
        # 步骤搜索：当前过滤词（空 = 不过滤）
        self._step_filter = ""
        # 图片预览：当前缩略图的 PhotoImage 与文件路径（防 GC / 打开目录用）
        self._preview_photo: Any = None
        self._preview_path = ""

        # 日志筛选：原始条目留一份，切换级别 / 关键词时整体重绘
        self._log_entries: deque = deque(maxlen=MAX_LOG_LINES)
        # 可见列表项与跳转目标一一对应（过滤 / 裁剪时同步）
        self._visible_targets: deque = deque(maxlen=MAX_LOG_LINES)
        self._log_level = ""
        self._log_search = ""
        self.log_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.ui_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.executor = Executor(base_dir=APP_DIR, log=self._enqueue_log)
        # 持久变量：跨运行保留，随配置文件一起保存（步骤里用 {持久:键} 读）
        self.executor.persist = self.settings.setdefault("persistent_vars", {})
        self.executor.on_persist = self._persist_var_from_worker
        self.worker: Optional[threading.Thread] = None
        self.running = False
        self.paused = False
        self._countdown_token = 0
        # 撤销 / 重做：存 (操作名, 变更前的 project 快照)
        self._undo_stack: list[tuple[str, dict]] = []
        self._redo_stack: list[tuple[str, dict]] = []
        self._editor: Optional[uikit.ReadonlySelect | uikit.TextField] = None
        # 定时运行：记录今天已经触发过的安排，避免同一天重复跑
        self._schedule_fired: set[str] = set()
        # 最近一次由定时任务启动的运行记录（结束时补结果）
        self._active_schedule: Optional[dict] = None
        self._schedule_pending = False
        self._schedule_popover: Optional[SchedulePopover] = None
        # 行内编辑时的按键录入（双击热键步骤的内容列）
        self._inline_target: Optional[uikit.ReadonlySelect | uikit.TextField] = None
        self._inline_record_btn: Optional[uikit.RoundButton] = None
        self._inline_previous = ""
        self._win_hwnd: Optional[int] = None
        self._global_hotkeys: Any = None   # macOS 全局 F5/F9 监听器
        self._capture_callback: Optional[Callable[[str], None]] = None
        self._capture_coords_callback: Optional[Callable[[int, int, int, int], None]] = None
        self._inline_recording = False
        self._inline_recorder = KeyRecorder(
            self,
            on_state=self._inline_record_state,
            on_preview=lambda names: self._inline_set_text("+".join(names)),
            on_done=self._inline_record_done,
            on_cancel=self._inline_record_cancelled,
            on_hint=self._set_status)

        self._build_style()
        self._set_icon()
        self._build_header()
        self._build_statusbar()
        self._build_log()
        self._build_body()
        self._register_right_click()
        self._bind_shortcuts()
        self._restore_ui_state()
        self._apply_theme(self.dark)

        # 托盘：设置里开了任一托盘开关才弹图标；会话结束（注销/关机）时
        # 不再「关窗躲托盘」，否则会挡系统关机
        self._init_tray()
        self._session_hook = tray.hook_session_end(
            self, self._on_session_query, self._on_session_end)
        self.after(400, self._maybe_start_hidden)

        seed_default_groups(self.project, self.settings)
        if (not self.settings.get("onboarded")
                and not os.environ.get("MEOWGO_NO_ONBOARD")):
            self.after(1500, self._show_onboarding)
        if not self.project.groups:
            self.project.groups.append(Group(self.project.unique_name("默认组")))
        self._refresh_all(select_group=0)
        self._start_global_hotkey()
        self._warm_imports()
        self.after(120, self._drain_queue)
        self.after(2000, self._autosave_tick)
        self.after(1000, self._schedule_tick)
        self._check_dpi_setup()
        if not os.environ.get("MEOWGO_NO_PERMISSION_PROMPT"):
            self.after(800, lambda: self._check_platform_permissions())
        self._log("info", i18n.tr("欢迎使用 MeowGo"))
        self._log("muted", i18n.tr(
            "左侧维护执行组，右侧编辑步骤；「调用执行组」指令可复用其它组。"))
        if not opencv_available():
            # 图片步骤全都会失败，启动时就说清楚，别等跑到第 N 步才报错
            self._log("warn", i18n.tr(
                "未安装 OpenCV（opencv-python），图片识别步骤无法运行。"
                "请在命令行执行：pip install opencv-python"))

    def _warm_imports(self) -> None:
        """后台预热重依赖。

        pyautogui 首次导入会连带加载 cv2 / numpy，冷启动要十几秒；
        放到后台线程提前拉起来，避免第一步执行时界面像卡死。
        """

        def worker():
            try:
                import pyautogui  # noqa: F401
                import pyperclip  # noqa: F401
            except Exception:  # noqa: BLE001 — 缺依赖留给运行时报错提示
                pass

        threading.Thread(target=worker, daemon=True).start()

    # --------------------------------------------------------------- 基础
    def _init_scaling(self) -> float:
        try:
            dpi = float(self.winfo_fpixels("1i"))
        except tk.TclError:
            dpi = 96.0
        # 用户可选的界面缩放（设置里 90%–150%），叠加在系统 DPI 之上
        try:
            pref = float(self.settings.get("ui_scale", 1.0))
        except (TypeError, ValueError):
            pref = 1.0
        pref = max(0.8, min(1.6, pref))
        scale = max(0.8, min(3.0, dpi / 96.0 * pref))
        try:
            self.tk.call("tk", "scaling", dpi / 72.0 * pref)
        except tk.TclError:
            pass
        return scale

    def s(self, value: float) -> int:
        """逻辑像素转屏幕像素。"""
        return int(round(value * self.scale))

    def place_window(self, window, x: int, y: int, width: int = 0,
                     height: int = 0) -> None:
        """把弹出的窗口收进主窗口所在的那块屏幕（多屏：允许负坐标）。"""
        rect = uikit.monitor_work_area(
            self.winfo_rootx() + self.winfo_width() // 2,
            self.winfo_rooty() + self.winfo_height() // 2)
        if rect is None:
            x, y = max(0, x), max(0, y)
        else:
            left, top, right, bottom = rect
            x = min(max(x, left), max(left, right - width))
            y = min(max(y, top), max(top, bottom - height))
        window.geometry(f"+{int(x)}+{int(y)}")

    def pic(self, name: str, size: int = 16, color: str = TEXT_WEAK):
        key = (name, self.s(size), color)
        if key not in self._icons:
            self._icons[key] = uikit.icon(name, self.s(size), color)
        return self._icons[key]

    def step_pic(self, step_type: str, size: int = 14):
        """行首「类型色条 + 图标」：让 TYPE_COLORS 真正参与扫行。

        Treeview 不能给单个单元格上色，所以把颜色做成 #0 列的窄色条；
        图标与色条同色，浅色 / 深色主题共用同一套 TYPE_COLORS。
        """
        color = TYPE_COLORS.get(step_type, TYPE_ICON_COLOR)
        key = ("step_type", step_type, self.s(size), color)
        if key not in self._icons:
            from PIL import Image, ImageDraw, ImageTk

            px = self.s(size)
            bar = max(2, self.s(3))
            gap = max(1, self.s(2))
            canvas = Image.new("RGBA", (px + bar + gap, px), (0, 0, 0, 0))
            ImageDraw.Draw(canvas).rounded_rectangle(
                [0, 0, bar - 1, px - 1], radius=max(1, bar / 2), fill=color)
            glyph = uikit.icon_image(
                TYPE_ICONS.get(step_type, "note"), px, color)
            canvas.paste(glyph, (bar + gap, 0), glyph)
            self._icons[key] = ImageTk.PhotoImage(canvas)
        return self._icons[key]

    @staticmethod
    def _icon_paths() -> list[str]:
        """应用默认图标的位置（打包后在 _internal 里，开发时在项目根目录）。"""
        return [os.path.join(getattr(sys, "_MEIPASS", APP_DIR), "main.png"),
                os.path.join(APP_DIR, "main.png")]

    def app_icon(self, size: int):
        """应用默认图标（main.png），按界面缩放后缓存；没有图标时返回 None。"""
        key = ("app_icon", size)
        if key not in self._icons:
            photo = None
            for path in self._icon_paths():
                if not os.path.exists(path):
                    continue
                try:
                    from PIL import Image, ImageTk

                    image = Image.open(path).convert("RGBA")
                    image.thumbnail((size, size), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(image)
                    break
                except Exception:  # noqa: BLE001
                    continue
            self._icons[key] = photo
        return self._icons[key]

    @staticmethod
    def _logo_paths() -> list[str]:
        """品牌 Logo 的位置（与 main.png 一样兼容打包后的目录）。"""
        return [os.path.join(getattr(sys, "_MEIPASS", APP_DIR), APP_LOGO_FILE),
                os.path.join(APP_DIR, APP_LOGO_FILE)]

    def brand_logo(self, height: int, dark: bool):
        """加载品牌 Logo；深色主题下把黑色字标反白，保留橙色圆点。"""
        key = ("brand_logo", self.s(height), bool(dark))
        if key not in self._icons:
            photo = None
            for path in self._logo_paths():
                if not os.path.exists(path):
                    continue
                try:
                    from PIL import Image, ImageTk

                    image = Image.open(path).convert("RGBA")
                    width, src_height = image.size
                    target_h = max(1, self.s(height))
                    target_w = max(1, round(width * target_h / src_height))
                    image = image.resize((target_w, target_h), Image.Resampling.LANCZOS)
                    if dark:
                        pixels = image.load()
                        assert pixels is not None
                        for y in range(target_h):
                            for x in range(target_w):
                                pixel = pixels[x, y]
                                if not isinstance(pixel, tuple):
                                    continue
                                r, g, b, alpha = pixel
                                if (alpha and max(r, g, b) < 170
                                        and max(r, g, b) - min(r, g, b) < 36):
                                    pixels[x, y] = (245, 242, 237, alpha)
                    photo = ImageTk.PhotoImage(image)
                    break
                except Exception:  # noqa: BLE001 — 读不到就用文字兜底
                    continue
            self._icons[key] = photo
        return self._icons[key]

    def _set_icon(self) -> None:
        for icon in self._icon_paths():
            if not os.path.exists(icon):
                continue
            try:
                from PIL import Image, ImageTk

                self._icon_image = ImageTk.PhotoImage(Image.open(icon))
                self.iconphoto(True, self._icon_image)  # type: ignore[arg-type]
                return
            except Exception:  # noqa: BLE001
                continue

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=TEXT,
                        font=FONT_BODY)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Card.TLabel", background=CARD, foreground=TEXT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("Treeview", background=CARD, fieldbackground=CARD,
                        rowheight=self.s(TABLE_ROW_HEIGHT), borderwidth=0,
                        bordercolor=TABLE_BORDER, lightcolor=TABLE_BORDER,
                        darkcolor=TABLE_BORDER, font=FONT_BODY)
        style.configure("Treeview.Heading", background=TABLE_HEAD_BG,
                        foreground=TABLE_HEAD_FG, bordercolor=TABLE_BORDER,
                        lightcolor=TABLE_BORDER, darkcolor=TABLE_BORDER,
                        relief="flat", font=FONT_NOTE_BOLD,
                        padding=(self.s(9), self.s(7)))
        style.map("Treeview",
                  background=[("selected", TABLE_ROW_SELECTED)],
                  foreground=[("selected", TEXT)])
        style.map("Treeview.Heading",
                  background=[("active", TABLE_HEAD_BG),
                              ("pressed", TABLE_HEAD_BG)])
        style.configure("TEntry", fieldbackground=ENTRY_BG,
                        foreground=TEXT, padding=self.s(5),
                        bordercolor=BORDER, lightcolor=ENTRY_BG,
                        darkcolor=ENTRY_BG, borderwidth=1)
        style.map("TEntry",
                  bordercolor=[("focus", FIELD_FOCUS)],
                  lightcolor=[("focus", FIELD_FOCUS)],
                  darkcolor=[("focus", FIELD_FOCUS)])
        # 搜索框：外层用圆角卡片，Entry 自身去掉边框融入卡片
        style.configure("Search.TEntry", fieldbackground=ENTRY_BG,
                        foreground=TEXT, bordercolor=ENTRY_BG,
                        lightcolor=ENTRY_BG, darkcolor=ENTRY_BG,
                        borderwidth=0, relief="flat", padding=self.s(2))
        # 可搜索下拉框：同样由外层圆角卡片提供描边
        # 统一输入框：外层 RoundedCard 负责唯一描边，Entry 自身完全不画边框。
        style.configure("Field.TEntry", fieldbackground=ENTRY_BG,
                        foreground=TEXT, bordercolor=ENTRY_BG,
                        lightcolor=ENTRY_BG, darkcolor=ENTRY_BG,
                        borderwidth=0, relief="flat", padding=self.s(5))
        # 显式覆盖 TEntry 的 focus 描边映射，否则 ttk 仍会在圆角外壳内
        # 再画一圈黑色方框，形成双边框。
        style.map("Field.TEntry",
                  bordercolor=[("focus", ENTRY_BG), ("!focus", ENTRY_BG),
                               ("disabled", ENTRY_BG)],
                  lightcolor=[("focus", ENTRY_BG), ("!focus", ENTRY_BG),
                              ("disabled", ENTRY_BG)],
                  darkcolor=[("focus", ENTRY_BG), ("!focus", ENTRY_BG),
                             ("disabled", ENTRY_BG)],
                  fieldbackground=[("disabled", HEADING_BG),
                                   ("readonly", ENTRY_BG)],
                  foreground=[("disabled", TEXT_WEAK), ("readonly", TEXT)])
        style.configure("Combo.TEntry", fieldbackground=ENTRY_BG,
                        foreground=TEXT, bordercolor=ENTRY_BG,
                        lightcolor=ENTRY_BG, darkcolor=ENTRY_BG,
                        borderwidth=0, relief="flat", padding=self.s(5))
        style.map("Combo.TEntry",
                  bordercolor=[("focus", ENTRY_BG), ("!focus", ENTRY_BG),
                               ("disabled", ENTRY_BG)],
                  lightcolor=[("focus", ENTRY_BG), ("!focus", ENTRY_BG),
                              ("disabled", ENTRY_BG)],
                  darkcolor=[("focus", ENTRY_BG), ("!focus", ENTRY_BG),
                             ("disabled", ENTRY_BG)])
        style.configure("TCombobox", padding=self.s(5),
                        fieldbackground=ENTRY_BG, foreground=TEXT,
                        bordercolor=BORDER, arrowcolor=MUTED)
        style.map("TCombobox",
                  bordercolor=[("focus", FIELD_FOCUS)],
                  fieldbackground=[("readonly", ENTRY_BG)],
                  foreground=[("readonly", TEXT)])
        style.configure("TSpinbox", padding=self.s(5),
                        fieldbackground=ENTRY_BG, foreground=TEXT,
                        bordercolor=BORDER, arrowcolor=MUTED)
        style.map("TSpinbox", bordercolor=[("focus", FIELD_FOCUS)])
        # 原生滚动条仅作兜底；实际界面使用 uikit.SlimScrollbar。
        style.configure("Vertical.TScrollbar", background=SCROLL_THUMB,
                        troughcolor=SCROLL_TRACK, bordercolor=SCROLL_TRACK,
                        arrowcolor=MUTED, width=self.s(12),
                        lightcolor=SCROLL_THUMB, darkcolor=SCROLL_THUMB)
        style.configure("Horizontal.TScrollbar", background=SCROLL_THUMB,
                        troughcolor=SCROLL_TRACK, bordercolor=SCROLL_TRACK,
                        arrowcolor=MUTED, width=self.s(12),
                        lightcolor=SCROLL_THUMB, darkcolor=SCROLL_THUMB)
        # 日志控制台专用：深色滚动条 / 进度条，避免浅色主题里出现亮补丁
        style.configure("Console.Vertical.TScrollbar", background=CONSOLE_SCROLL,
                        troughcolor=LOG_BG, bordercolor=LOG_BG,
                        arrowcolor=LOG_HEAD_MUTED, width=self.s(12),
                        lightcolor=CONSOLE_SCROLL, darkcolor=CONSOLE_SCROLL)
        style.configure("TPanedwindow", background=BG)
        style.configure("Sash", background=BG, bordercolor=BG,
                        lightcolor=BG, darkcolor=BG, sashthickness=self.s(10))
        # 进度条：主窗口细条 + 迷你置顶条里的深色款
        style.configure("Accent.Horizontal.TProgressbar",
                        background=ACCENT, troughcolor=PROGRESS_TROUGH,
                        bordercolor=PROGRESS_TROUGH,
                        lightcolor=PROGRESS_TROUGH,
                        darkcolor=PROGRESS_TROUGH,
                        borderwidth=0, thickness=self.s(6))
        style.configure("Console.Horizontal.TProgressbar",
                        background=ACCENT, troughcolor=LOG_HEAD_BG,
                        bordercolor=LOG_HEAD_BG, lightcolor=LOG_HEAD_BG,
                        darkcolor=LOG_HEAD_BG,
                        borderwidth=0, thickness=self.s(6))
        style.configure("Mini.Horizontal.TProgressbar",
                        background=ACCENT, troughcolor=LOG_HEAD_BG,
                        bordercolor=LOG_HEAD_BG, lightcolor=LOG_HEAD_BG,
                        darkcolor=LOG_HEAD_BG,
                        borderwidth=0, thickness=self.s(4))
        # 分组树的展开 / 收起指示器换成猫爪：派生布局去掉系统三角，
        # 组行图标由「猫爪 + 文件夹」合成（见 App._group_row_pic），
        # 步骤表与其它 Treeview 仍沿用系统布局，不受影响。
        style.layout("Paw.Treeview.Item", [
            ("Treeitem.padding", {"sticky": "nswe", "children": [
                ("Treeitem.image", {"side": "left", "sticky": ""}),
                ("Treeitem.text", {"sticky": "nswe"}),
            ]}),
        ])

    # --------------------------------------------------------------- 菜单
    def _menu_defs(self):
        """顶部菜单栏：左到右的按钮 + 各自的下拉项。"""
        return [("文件", self._file_items), ("编辑", self._edit_items),
                ("运行", self._run_items), ("视图", self._view_items),
                ("帮助", self._help_items)]

    def _file_items(self) -> list:
        MI = uikit.MenuItem
        return [
            MI("新建项目…", "add", self.new_project),
            MI("打开项目…", "open", self.open_project_dialog),
            MI("另存为项目…", "save", self.save_project_as),
            MI("关联 .mgproj 文件…", "link",
               self.register_project_file_assoc,
               "双击项目文件直接打开"),
            MI("最近项目…", "folder", self.open_recent_projects,
               project_io.project_name(self.project_path)
               if self.project_path else ""),
            "-",
            MI("导入 Excel（单个组）…", "import",
               lambda: self.import_excel(single=True), "第一个工作表"),
            MI("导入 Excel（全部工作表）…", "table",
               lambda: self.import_excel(single=False), "每表一个组"),
            "-",
            MI("导出当前组为 Excel…", "export",
               lambda: self.export_excel(single=True),
               self._current_group_name() or "—"),
            MI("导出所有组为 Excel…", "table",
               lambda: self.export_excel(single=False),
               f"{len(self.project.groups)} 个组"),
            "-",
            MI("导出运行日志…", "save", self.export_log),
            MI("打开报告索引", "table", self.open_report_index),
            MI("打开运行报告目录", "folder", self.open_reports_dir),
            "-",
            MI("恢复配置备份…", "open", self.restore_backup),
            "-",
            MI("打开图片目录", "folder_open", self.open_image_dir,
               os.path.basename(self.image_dir)),
            MI("退出", "close", self.on_close),
        ]

    def _edit_items(self) -> list:
        MI = uikit.MenuItem
        group = self._current_group()
        rows = self._selected_steps()
        index = rows[0] if rows else None
        total = len(group.steps) if group else 0
        has = index is not None
        multi = len(rows) > 1
        can_capture = (has and group is not None and index is not None
                       and 0 <= index < len(group.steps)
                       and group.steps[index].type in IMAGE_STEP_TYPES)
        all_disabled = bool(rows) and group is not None and all(
            0 <= row < len(group.steps) and not group.steps[row].enabled
            for row in rows)
        return [
            MI(self._undo_label(), "undo", self.undo, "Ctrl+Z",
               enabled=bool(self._undo_stack)),
            MI(self._redo_label(), "redo", self.redo, "Ctrl+Y",
               enabled=bool(self._redo_stack)),
            "-",
            MI("新建执行组", "add", self.add_group, "Ctrl+G", accent=True),
            MI("恢复预置执行组…", "add", self.restore_default_groups,
               "补回缺失模板"),
            MI("重命名执行组…", "pencil", self.rename_group),
            MI("组级重试…", "redo", self.edit_group_retry, "整组失败后重跑"),
            MI("复制为副本", "copy", self.duplicate_group),
            MI("删除执行组", "trash", self.delete_group, danger=True),
            "-",
            MI("新增步骤", "add", self.add_step, "Ctrl+Enter"),
            MI("全选步骤", "check", self._select_all_steps, "Ctrl+A",
               enabled=total > 0),
            MI("编辑步骤…", "pencil", self.edit_step, "F2", enabled=has),
            MI(f"复制选中步骤（{len(rows)}）" if multi else "复制步骤", "copy",
               self.duplicate_step, enabled=has),
            MI("启用步骤" if all_disabled else "禁用步骤", "check",
               self.toggle_steps_enabled, "Ctrl+E", enabled=has),
            MI(f"删除选中步骤（{len(rows)}）" if multi else "删除步骤", "trash",
               self.delete_step, "Delete", enabled=has, danger=True),
            "-",
            MI("截取屏幕并替换图片…", "image",
               lambda: self.capture_region(self._apply_capture),
               enabled=can_capture),
            "-",
            MI("宏录制鼠标操作…", "mouse", self.open_macro_recorder,
               i18n.tr("仅 Windows") if not platform_support.available(
                   "keyboard_hook") else "录成坐标步骤",
               enabled=platform_support.available("keyboard_hook")),
        ]

    def _run_items(self) -> list:
        MI = uikit.MenuItem
        return [
            MI("运行当前组", "run", self.run_group, "F5", accent=True),
            MI("从选中步骤开始运行", "run", self.run_from_step, "从当前行继续"),
            MI("运行到这一步", "debug", self.run_to_step, "到它才单步确认",
               enabled=self._selected_steps() != []),
            MI("循环运行当前组", "loop", self.run_loop),
            MI("运行全部组", "table", self.run_all),
            MI("带数据运行…", "table", self.run_with_data, "CSV/Excel"),
            "-",
            MI("单步调试运行这个组", "debug", self.run_group_debug,
               "每步确认"),
            MI("单步调试开关：" + ("开" if self.debug_mode else "关"), "debug",
               self.toggle_debug, "F8", accent=self.debug_mode),
            "-",
            MI("定时运行…", "calendar", self.open_schedule,
               self._schedule_summary()),
            "-",
            MI("暂停 / 继续", "pause", self.toggle_pause, enabled=self.running),
            MI("停止", "stop", self.stop_run,
               "F9 全局" if platform_support.available("global_hotkey") else ""),
        ]

    def _view_items(self) -> list:
        MI = uikit.MenuItem
        return [
            MI("收起运行日志" if self.log_visible else "展开运行日志",
               "chevron_down" if self.log_visible else "chevron_right",
               self.toggle_log),
            MI("切换到深色主题" if not self.dark else "切换到浅色主题",
               "moon" if not self.dark else "sun",
               self.toggle_theme, "Ctrl+D"),
            MI("运行设置…", "sliders", self.open_settings),
            MI("图片管家…", "image", self.open_image_manager,
               f"{self._image_count()} 张"),
        ]

    def _help_items(self) -> list:
        MI = uikit.MenuItem
        items = [MI("使用说明", "info", self.show_help),
                 MI("快捷键速查", "question", self.show_shortcuts, "?")]
        items.append(MI("导出诊断信息…", "save", self.export_diagnostics))
        items.append(MI("检查更新…", "refresh", self.check_updates))
        items.append(MI("打开日志目录", "folder", self.open_logs_dir))
        if platform_support.IS_MACOS:
            items.append(MI("检查 macOS 权限…", "check",
                            lambda: self._check_platform_permissions(True)))
        items.append(MI("关于 " + APP_TITLE, "cat", self.show_about))
        return items

    def _build_menubar(self, parent) -> None:
        """自绘菜单栏：一排扁平按钮，点开后是与右键同款的下拉菜单。

        macOS 上改用系统菜单栏：菜单显示在屏幕顶部，释放窗口内的横向空间，
        也更符合 macOS 的操作习惯；Windows / Linux 保留自绘菜单栏。
        """
        self._top_buttons: dict[str, uikit.RoundButton] = {}
        if platform_support.IS_MACOS:
            self._build_native_menubar()
            return
        for name, _builder in self._menu_defs():
            button = uikit.RoundButton(parent, name, None,
                                       lambda n=name: self._open_top_menu(n),
                                       kind="ghost", scale=self.scale, bg=CARD,
                                       height=CONTROL_SM, pad=self.s(10),
                                       font_size=FONT_SIZE_BODY)
            button.pack(side="left", padx=self.s(1))
            self._top_buttons[name] = button

    def _build_native_menubar(self) -> None:
        """macOS 系统菜单栏；状态变化后按需重建，不打断正在打开的菜单。"""
        self._native_menubar = tk.Menu(self)
        self.configure(menu=self._native_menubar)
        self._native_menu_job: Optional[str] = None
        # 菜单项依赖步骤表 / 选中状态，等界面建完再生成第一版
        self.after_idle(self._refresh_native_menubar)
        self.bind("<ButtonRelease-1>", self._schedule_native_menubar, add="+")

    def _schedule_native_menubar(self, _event=None) -> None:
        job = getattr(self, "_native_menu_job", None)
        if job:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
        self._native_menu_job = self.after(120, self._refresh_native_menubar)

    def _refresh_native_menubar(self) -> None:
        self._native_menu_job = None
        menu = getattr(self, "_native_menubar", None)
        if menu is None:
            return
        try:
            menu.delete(0, "end")
        except tk.TclError:
            return
        app_menu = tk.Menu(menu, tearoff=0)
        app_menu.add_command(label=i18n.tr("关于 ") + APP_TITLE,
                             command=self.show_about)
        app_menu.add_command(label=i18n.tr("运行设置…"),
                             command=self.open_settings,
                             accelerator="Command+,")
        app_menu.add_separator()
        app_menu.add_command(label=i18n.tr("退出 ") + APP_TITLE,
                             command=self.on_close, accelerator="Command+Q")
        menu.add_cascade(label=APP_TITLE, menu=app_menu)
        for name, builder in self._menu_defs():
            sub = tk.Menu(menu, tearoff=0)
            for item in builder():
                if isinstance(item, str):
                    if item.strip() in ("-", "---"):
                        sub.add_separator()
                    continue
                if item.is_separator:
                    sub.add_separator()
                    continue
                if not item.label:
                    continue
                sub.add_command(
                    label=i18n.tr(item.label), command=item.command,
                    accelerator=_native_accelerator(item.shortcut),
                    state="normal" if item.enabled else "disabled")
            menu.add_cascade(label=i18n.tr(name), menu=sub)

    def _open_top_menu(self, name: str) -> None:
        builder = dict(self._menu_defs())[name]
        button = self._top_buttons[name]
        button.set_kind("solid")
        menu = self._menu(builder())
        menu.on_close = lambda: button.set_kind("ghost")
        menu.popup(button.winfo_rootx(),
                   button.winfo_rooty() + button.winfo_height() + self.s(2))

    # --------------------------------------------------------------- 顶部
    def _build_header(self) -> None:
        header = tk.Frame(self, bg=CARD, height=self.s(HEADER_HEIGHT))
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Frame(header, bg=BORDER, height=1).pack(side="bottom", fill="x")

        left = tk.Frame(header, bg=CARD)
        left.pack(side="left", padx=(self.s(24), 0))
        self.brand_logo_label = tk.Label(left, bg=CARD, cursor="hand2")
        logo = self.brand_logo(28, self.dark)
        if logo is not None:
            self.brand_logo_label.configure(image=logo)
        else:                            # 没有 Logo 文件时退回文字标题
            self.brand_logo_label.configure(
                text=APP_TITLE, fg=TEXT,
                font=FONT_TITLE)
        self.brand_logo_label.pack(side="left", pady=self.s(14),
                                   padx=(0, self.s(16)))
        self.brand_logo_label.bind("<Button-1>", lambda e: self.show_about())
        uikit.Tooltip(self.brand_logo_label, f"关于 {APP_TITLE}")

        self._build_menubar(left)

        # 右侧操作区：设置 / 撤销重做 / 单步 / 运行控制（原工具栏合并到菜单栏右侧）
        actions = tk.Frame(header, bg=CARD)
        actions.pack(side="right", padx=(0, self.s(16)))
        self._build_header_actions(actions)

    def _refresh_brand_logo(self) -> None:
        """主题切换后重新取 Logo（深色主题用暖白反白版本）。"""
        label = getattr(self, "brand_logo_label", None)
        if label is None:
            return
        logo = self.brand_logo(28, self.dark)
        if logo is not None:
            label.configure(image=logo, text="")
        else:
            label.configure(image="", text=APP_TITLE, fg=TEXT,
                            font=FONT_TITLE)

    def _build_header_actions(self, bar) -> None:
        """顶栏右侧的操作按钮：设置 / 撤销重做 / 单步 / 运行控制。"""

        def add(text, icon, cmd, kind="ghost", pad=self.s(8), tooltip="",
                icon_size=ICON_MD, height=CONTROL_SM):
            button = uikit.RoundButton(bar, text, icon, cmd, kind=kind,
                                       scale=self.scale, bg=CARD, height=height,
                                       pad=pad, icon_size=icon_size,
                                       tooltip=tooltip)
            button.pack(side="left", padx=self.s(1))
            return button

        def separator() -> None:
            tk.Frame(bar, bg=BORDER, width=1, height=self.s(20)).pack(
                side="left", padx=self.s(8))

        self.btn_schedule = add("", "clock", self.open_schedule_popover,
                                pad=self.s(5),
                                tooltip="定时任务：查看即将执行与倒计时")
        self.btn_settings = add("", "sliders", self.open_settings,
                                pad=self.s(5), tooltip="运行设置")
        self.btn_undo = add("", "undo", self.undo, pad=self.s(5),
                            tooltip="撤销（Ctrl+Z）")
        self.btn_redo = add("", "redo", self.redo, pad=self.s(5),
                            tooltip="重做（Ctrl+Y）")
        self.btn_undo.set_enabled(False)
        self.btn_redo.set_enabled(False)
        separator()
        self.btn_debug = add("单步", "debug", self.toggle_debug,
                             pad=self.s(8),
                             tooltip="单步调试：每步执行前确认（F8）")
        if self.debug_mode:
            self.btn_debug.set_kind("accent")
        separator()
        self.btn_run = add("运行组", "run", self.run_group, kind="accent",
                           pad=self.s(10), tooltip="运行当前组 F5")
        self.btn_loop = add("循环", "loop", self.run_loop, pad=self.s(8),
                            tooltip="循环运行当前组")
        self.btn_pause = add("暂停", "pause", self.toggle_pause, pad=self.s(8),
                             tooltip="暂停 / 继续")
        self.btn_stop = add("停止", "stop", self.stop_run, kind="danger",
                            pad=self.s(9), tooltip="停止（全局热键 F9）")
        self.btn_stop.set_enabled(False)
        self.btn_pause.set_enabled(False)

    def _build_body(self) -> None:
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=self.s(24))

        self.paned = ttk.PanedWindow(body, orient="horizontal")
        self.paned.pack(fill="both", expand=True)

        self._build_group_panel(self.paned)
        self._build_step_panel(self.paned)

    def _build_group_panel(self, parent) -> None:
        card = uikit.RoundedCard(
            parent, radius=self.s(CARD_RADIUS), bg=CARD, border=BORDER,
            outer_bg=BG, padding=self.s(6))
        card.configure(width=self.s(240))
        parent.add(card, weight=0)
        panel = card.body

        head = tk.Frame(panel, bg=CARD)
        head.pack(fill="x", padx=self.s(16), pady=(self.s(14), self.s(6)))
        tk.Label(head, image=self.pic("folder", 18, ACCENT_STRONG),
                 bg=CARD).pack(side="left")
        tk.Label(head, text="执行组", bg=CARD, fg=TEXT,
                 font=FONT_SECTION).pack(
            side="left", padx=(self.s(8), self.s(4)))
        uikit.RoundButton(head, "", "add", self.add_group, kind="ghost",
                          scale=self.scale, bg=CARD, height=CONTROL_XS, pad=self.s(4),
                          icon_size=ICON_SM, tooltip="新建执行组（Ctrl+G）"
                          ).pack(side="left")
        self.group_count = tk.Label(head, text="", bg=ACCENT_MIST,
                                    fg=ACCENT_STRONG, padx=self.s(8),
                                    pady=self.s(2),
                                    font=FONT_CAPTION_BOLD)
        self.group_count.pack(side="right")

        hint = tk.Label(panel, text="双击重命名 · 拖拽排序 · 右键查看更多操作",
                        bg=CARD, fg=MUTED, font=FONT_NOTE)
        hint.pack(side="bottom", pady=self.s(12))

        wrap = tk.Frame(panel, bg=CARD)
        wrap.pack(fill="both", expand=True, padx=(self.s(12), 0))
        self.group_tree = ttk.Treeview(wrap, show="tree", selectmode="browse")
        self.group_tree.pack(side="left", fill="both", expand=True)
        scroll = uikit.SlimScrollbar(
            wrap, orient="vertical", command=self.group_tree.yview,
            scale=self.scale, bg=CARD)
        scroll.pack(side="right", fill="y", padx=(self.s(2), 0))
        self.group_tree.configure(yscrollcommand=scroll.set)
        # 展开 / 收起改用猫爪：派生样式去掉系统三角，点组行最左侧的
        # 猫爪切换展开状态（见 _on_group_click / _group_row_pic）。
        self.group_tree.configure(style="Paw.Treeview")
        self.group_tree.bind("<Button-1>", self._on_group_click, add="+")
        self.group_tree.bind("<<TreeviewOpen>>", self._sync_group_paw, add="+")
        self.group_tree.bind("<<TreeviewClose>>", self._sync_group_paw, add="+")
        self.group_tree.tag_configure("running", foreground=WARNING)
        self.group_tree.bind("<<TreeviewSelect>>", self._on_group_select)
        self.group_tree.bind("<Double-1>", self._on_group_double)
        self.group_tree.bind("<Button-3>", self._group_menu)
        enable_tree_drag(self.group_tree, self._on_group_drop)

        # 空状态：像素猫 + 说明 + 主按钮（不用先右键就能新建）
        self.group_empty_box = tk.Frame(panel, bg=CARD)
        self.group_empty_icon = uikit.CatCanvas(
            self.group_empty_box, scale=self.scale * 0.60, bg=CARD, ink=TEXT,
            accent=ACCENT, nose=ACCENT_STRONG)
        self.group_empty_icon.pack(pady=(0, self.s(10)))
        self.group_empty = tk.Label(
            self.group_empty_box, text="尚未创建执行组", bg=CARD, fg=TEXT,
            font=FONT_DISPLAY)
        self.group_empty.pack()
        tk.Label(self.group_empty_box,
                 text="执行组由一组可重复执行的步骤组成。\n请先创建执行组，再添加步骤。",
                 bg=CARD, fg=MUTED, justify="center",
                 font=FONT_BODY).pack(pady=(self.s(6), self.s(14)))
        uikit.RoundButton(
            self.group_empty_box, "新建执行组", "add", self.add_group,
            kind="accent", scale=self.scale, bg=CARD, height=CONTROL_LG,
            pad=self.s(16), font_size=FONT_SIZE_BODY).pack()

    def _build_step_panel(self, parent) -> None:
        card = uikit.RoundedCard(
            parent, radius=self.s(CARD_RADIUS), bg=CARD, border=BORDER,
            outer_bg=BG, padding=self.s(6))
        parent.add(card, weight=1)
        panel = card.body

        head = tk.Frame(panel, bg=CARD)
        head.pack(fill="x", padx=self.s(16), pady=(self.s(14), self.s(6)))
        tk.Label(head, image=self.pic("note", 18, ACCENT_STRONG),
                 bg=CARD).pack(side="left")
        self.step_title = tk.Label(head, text="步骤", bg=CARD, fg=TEXT,
                                   font=FONT_SECTION)
        self.step_title.pack(side="left", padx=(self.s(8), 0))
        tk.Label(head, text="双击单元格可直接编辑", bg=CARD,
                 fg=MUTED, font=FONT_NOTE).pack(
            side="left", padx=(self.s(12), 0))
        self.btn_add_step = uikit.RoundButton(
            head, "新增步骤", "add", self.add_step, kind="ghost",
            scale=self.scale, bg=CARD, height=CONTROL_SM, pad=self.s(8),
            font_size=FONT_SIZE_NOTE, tooltip="新增步骤（Ctrl+Enter）")
        self.btn_add_step.pack(side="right")
        # 步骤搜索：按类型 / 内容 / 备注过滤，右侧显示「匹配数/总数」
        self.search_count = tk.Label(head, text="", bg=CARD, fg=MUTED,
                                     padx=0, pady=0,
                                     font=FONT_CAPTION_BOLD)
        self.search_count.pack(side="right", padx=(0, self.s(8)))
        # 搜索框：统一圆角输入组件 + 有内容时出现的 ×
        self.search_field = uikit.TextField(
            head, width=self.s(14), scale=self.scale, bg=CARD,
            font_size=FONT_SIZE_NOTE, padding=self.s(3))
        self.search_field.pack(side="right", padx=(0, self.s(2)))
        self.search_box = self.search_field.body
        self.step_search = self.search_field
        self.step_search.entry.pack_configure(padx=(self.s(5), 0))
        # × 只在有搜索词时出现；用固定宽度占位，避免标题栏左右跳动
        self.search_clear_slot = tk.Frame(
            self.search_box, bg=ENTRY_BG, width=self.s(22),
            height=self.s(22))
        self.search_clear_slot.pack(side="left", padx=(self.s(2), 0))
        self.search_clear_slot.pack_propagate(False)
        self.btn_search_clear = uikit.RoundButton(
            self.search_clear_slot, "", "close", self._clear_search_and_focus,
            kind="ghost", scale=self.scale, bg=ENTRY_BG, height=CONTROL_XS,
            pad=self.s(4), icon_size=ICON_XS, tooltip="清除搜索（Esc）")
        self.step_search.insert(0, _search_placeholder())
        self.step_search.configure(foreground=MUTED)
        self.step_search.bind("<FocusIn>", self._on_search_focus_in)
        self.step_search.bind("<FocusOut>", self._on_search_focus_out)
        self.step_search.bind("<KeyRelease>", self._on_search_key)
        self.step_search.bind("<Escape>", self._on_search_escape)

        # 表格容器：参考图的圆角浅灰底 + 细描边，表头和行都贴着容器内缘。
        self.table_frame = uikit.RoundedCard(
            panel, radius=self.s(INNER_RADIUS), bg=CARD,
            border=TABLE_BORDER, outer_bg=CARD,
            padding=self.s(2), shadow=False)
        self.table_frame.pack(fill="both", expand=True,
                              padx=(self.s(16), 0), pady=(0, self.s(16)))
        self.step_wrap = self.table_frame
        wrap = tk.Frame(self.table_frame.body, bg=CARD)
        wrap.pack(fill="both", expand=True)
        columns = ("no", "type", "content", "repeat", "note")
        self.step_tree = ttk.Treeview(wrap, columns=columns,
                                      show="tree headings",
                                      selectmode="extended")
        self.step_tree.heading("#0", text="", anchor="w")
        self.step_tree.column("#0", width=self.s(40), anchor="w",
                              stretch=False)
        for key, (text, width, stretch) in {
                "no": ("#", 56, False), "type": ("类型", 140, False),
                "content": ("内容", 340, False), "repeat": ("重复", 64, False),
                "note": ("备注", 200, False)}.items():
            self.step_tree.heading(key, text=text, anchor="w")
            self.step_tree.column(key, width=self.s(width), anchor="w",
                                  minwidth=self.s(40), stretch=stretch)
        # 列宽固定后，窗口变窄时用横向滚动条查看被挡住的列
        scroll_x = uikit.SlimScrollbar(
            wrap, orient="horizontal", command=self.step_tree.xview,
            scale=self.scale, bg=CARD)
        scroll_x.pack(side="bottom", fill="x", pady=(self.s(2), 0))
        scroll2 = uikit.SlimScrollbar(
            wrap, orient="vertical", command=self.step_tree.yview,
            scale=self.scale, bg=CARD)
        scroll2.pack(side="right", fill="y", padx=(self.s(2), 0))
        self.step_tree.pack(side="left", fill="both", expand=True)
        self.step_tree.configure(yscrollcommand=scroll2.set,
                                 xscrollcommand=scroll_x.set)
        # 标签创建顺序即优先级（先建的赢）：运行染色必须建在斑马纹之前，
        # 否则 executing/done/failed 会被斑马纹盖掉。
        self.step_tree.tag_configure("running", background=MARK_RUNNING)
        self.step_tree.tag_configure("pending", background=MARK_PENDING)
        self.step_tree.tag_configure("done", background=MARK_DONE)
        self.step_tree.tag_configure("failed", background=MARK_FAILED)
        self.step_tree.tag_configure("even", background=CARD)
        self.step_tree.tag_configure("odd", background=ZEBRA)
        # 只换前景色，背景仍由斑马纹 / 运行染色决定（禁用行灰显）
        self.step_tree.tag_configure("disabled", foreground=MUTED)
        self.step_tree.bind("<Double-1>", self._on_step_double)
        self.step_tree.bind("<Button-3>", self._step_menu)
        self.step_tree.bind("<Delete>", self._on_step_key_delete)
        self.step_tree.bind("<F2>", self._on_step_key_edit)
        self.step_tree.bind("<Control-e>", self._on_step_key_toggle)
        mod = "Command" if platform_support.IS_MACOS else "Control"
        self.step_tree.bind(f"<{mod}-a>", self._select_all_steps)
        self.step_tree.bind("<<TreeviewSelect>>", self._on_step_select)
        enable_tree_drag(self.step_tree, self._on_step_drop)
        # 用户拖动表头分隔线后立即保存列宽；普通点击因宽度未变而直接返回。
        self.step_tree.bind("<ButtonRelease-1>",
                            self._on_step_columns_released, add="+")

        # 空状态：像素猫 + 标题 + 说明 + 主按钮
        self.step_empty_box = tk.Frame(wrap, bg=CARD)
        self.step_empty_icon = uikit.CatCanvas(
            self.step_empty_box, scale=self.scale * 0.7, bg=CARD, ink=TEXT,
            accent=ACCENT, nose=ACCENT_STRONG)
        self.step_empty_icon.pack(pady=(0, self.s(8)))
        self.step_empty = tk.Label(
            self.step_empty_box, text="此执行组尚未添加步骤", bg=CARD, fg=TEXT,
            font=FONT_DISPLAY)
        self.step_empty.pack()
        self.step_empty_hint = tk.Label(
            self.step_empty_box, text="", bg=CARD, fg=MUTED, justify="center",
            font=FONT_BODY)
        self.step_empty_hint.pack(pady=(self.s(6), self.s(14)))
        self.step_empty_btn = uikit.RoundButton(
            self.step_empty_box, "新增步骤", "add", self.add_step,
            kind="accent", scale=self.scale, bg=CARD, height=CONTROL_LG,
            pad=self.s(16), font_size=FONT_SIZE_BODY)
        self.step_empty_btn.pack()

        # 图片预览窗格：选中图片类步骤时出现，显示缩略图与文件信息
        self.preview_frame = uikit.RoundedCard(
            panel, radius=self.s(INNER_RADIUS), bg=PREVIEW_BG, border=BORDER,
            outer_bg=CARD, padding=self.s(3), shadow=False)
        preview = self.preview_frame.body
        self.preview_image = tk.Label(preview, bg=PREVIEW_BG)
        self.preview_image.pack(side="left", padx=self.s(8), pady=self.s(6))
        info = tk.Frame(preview, bg=PREVIEW_BG)
        info.pack(side="left", fill="x", expand=True, pady=self.s(6))
        self.preview_name = tk.Label(info, text="", bg=PREVIEW_BG, fg=TEXT,
                                     font=FONT_NOTE_BOLD)
        self.preview_name.pack(anchor="w")
        self.preview_meta = tk.Label(info, text="", bg=PREVIEW_BG, fg=MUTED,
                                     font=FONT_CAPTION)
        self.preview_meta.pack(anchor="w")
        uikit.RoundButton(preview, "在文件夹中显示", "folder_open",
                          self._open_preview_image_dir, kind="ghost",
                          scale=self.scale, bg=PREVIEW_BG, height=CONTROL_XS,
                          pad=self.s(6), font_size=FONT_SIZE_NOTE).pack(side="right",
                                                           padx=self.s(6))

    def _build_log(self) -> None:
        self.log_card = uikit.RoundedCard(
            self, radius=self.s(CARD_RADIUS), bg=CARD, border=BORDER,
            outer_bg=BG, padding=self.s(5))
        self.log_card.pack(fill="x", side="bottom", padx=self.s(24),
                           pady=(self.s(8), 0))
        card = self.log_card.body

        head = tk.Frame(card, bg=LOG_HEAD_BG)
        head.pack(fill="x", padx=1, pady=(1, 0))
        self.btn_log_toggle = uikit.RoundButton(
            head, "运行日志", "chevron_down", self.toggle_log, kind="ghost",
            scale=self.scale, bg=LOG_HEAD_BG, height=CONTROL_SM, pad=self.s(8),
            font_size=FONT_SIZE_NOTE, tooltip="收起 / 展开日志")
        self.btn_log_toggle.pack(side="left")
        self.log_state = tk.Label(head, text="就绪", bg=LOG_HEAD_BG,
                                  fg=LOG_HEAD_MUTED,
                                  font=FONT_NOTE)
        self.log_state.pack(side="left", padx=(self.s(10), 0))
        self.btn_log_clear = uikit.RoundButton(
            head, "清空", "trash", self.clear_log, kind="ghost",
            scale=self.scale, bg=LOG_HEAD_BG, height=CONTROL_SM, pad=self.s(8),
            font_size=FONT_SIZE_NOTE)
        self.btn_log_clear.pack(side="right")
        self.btn_log_copy = uikit.RoundButton(
            head, "复制", "copy", self.copy_log, kind="ghost",
            scale=self.scale, bg=LOG_HEAD_BG, height=CONTROL_SM, pad=self.s(8),
            font_size=FONT_SIZE_NOTE, tooltip="复制全部日志到剪贴板")
        self.btn_log_copy.pack(side="right", padx=(0, self.s(2)))
        # 日志筛选：级别 + 关键词（原始条目保留，切换时整块重绘）
        self.log_search_var = tk.StringVar()
        self.log_search_entry = tk.Entry(
            head, textvariable=self.log_search_var, width=10,
            bg=LOG_HEAD_BG, fg=theme.token("LOG_HEAD_FG"),
            insertbackground=theme.token("LOG_HEAD_FG"),
            relief="flat", font=FONT_CAPTION)
        self.log_search_entry.pack(side="right", padx=(0, self.s(6)))
        self.log_search_entry.bind("<KeyRelease>", self._on_log_search)
        self.log_level_var = tk.StringVar(value="全部")
        self.combo_log_level = uikit.LocalizedCombobox(
            head, values=["全部", "信息", "警告", "错误"],
            variable=self.log_level_var, width=6, font_size=FONT_SIZE_CAPTION,
            bg=LOG_HEAD_BG, command=lambda _value=None: self._on_log_filter())
        self.combo_log_level.pack(side="right", padx=(0, self.s(4)))
        self.log_search_entry.bind(
            "<FocusIn>",
            lambda e: self.log_search_entry.configure(
                bg=theme.token("LOG_HEAD_HOVER")))
        self.log_search_entry.bind(
            "<FocusOut>",
            lambda e: self.log_search_entry.configure(bg=LOG_HEAD_BG))
        # 「打开报告」改成运行历史入口：弹窗里可以查看 / 清空历次报告
        self.btn_run_history = uikit.RoundButton(
            head, "历史", "clock", self.open_run_history, kind="ghost",
            scale=self.scale, bg=LOG_HEAD_BG, height=CONTROL_SM, pad=self.s(8),
            font_size=FONT_SIZE_NOTE, tooltip="运行历史：查看 / 清空历次报告")
        self.btn_run_history.pack(side="right", padx=(0, self.s(2)))
        # 点标题栏空白处也能收放
        for widget in (head, self.log_state):
            widget.bind("<Button-1>", lambda e: self.toggle_log())

        # 运行进度条：已知总步数→确定进度；循环 / 无限重复→脉冲滚动
        # 和标题栏共用深色底，让「控制台」从上到下是一整块
        self.progress_frame = tk.Frame(card, bg=LOG_HEAD_BG)
        self.progress_frame.pack(fill="x", padx=1)
        self.progress = ttk.Progressbar(
            self.progress_frame, style="Console.Horizontal.TProgressbar",
            mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True,
                           padx=(self.s(10), 0), pady=(0, self.s(6)))
        self.progress_label = tk.Label(self.progress_frame, text="",
                                       bg=LOG_HEAD_BG, fg=LOG_HEAD_MUTED,
                                       font=FONT_CAPTION)
        self.progress_label.pack(side="left", padx=(self.s(8), self.s(10)),
                                 pady=(0, self.s(6)))

        # 正文 + 滚动条：能回看历史日志；超过 MAX_LOG_LINES 自动丢弃最早行
        self.log_body = tk.Frame(card, bg=LOG_BG)
        self.log_body.pack(fill="x", padx=1, pady=(0, 1))
        # macOS 的 Tk 8.5 上可见 tk.Text 会持续触发整窗重绘（100% CPU），
        # 日志改用 Listbox：同样支持逐行着色、滚动和点击跳转。
        self.log_list = tk.Listbox(
            self.log_body, height=7, bg=LOG_BG, fg=LOG_FG,
            selectbackground=theme.token("SELECT_BG"),
            selectforeground=theme.token("TEXT"),
            relief="flat", borderwidth=0, highlightthickness=0,
            activestyle="none", exportselection=False, takefocus=0,
            font=FONT_MONO)
        self.log_scroll = uikit.SlimScrollbar(
            self.log_body, orient="vertical", command=self.log_list.yview,
            scale=self.scale, bg=LOG_BG, variant="console")
        # 横向滚动条先占底部：长路径 / 长内容不再被 Listbox 直接裁掉
        self.log_xscroll = uikit.SlimScrollbar(
            self.log_body, orient="horizontal", command=self.log_list.xview,
            scale=self.scale, bg=LOG_BG, variant="console")
        self.log_xscroll.pack(side="bottom", fill="x", padx=self.s(12),
                              pady=(0, self.s(4)))
        self.log_scroll.pack(side="right", fill="y", padx=(self.s(2), 0))
        self.log_list.pack(side="left", fill="both", expand=True,
                           padx=self.s(12), pady=self.s(8))
        self.log_list.configure(yscrollcommand=self.log_scroll.set,
                                xscrollcommand=self.log_xscroll.set)
        # 点击带跳转目标的日志行：跳到步骤表对应行
        self.log_list.bind("<Button-1>", self._on_log_link_click)
        self.log_list.bind("<Motion>", self._on_log_hover)

    def toggle_log(self) -> None:
        if self.log_visible:
            self.log_body.pack_forget()
            self.btn_log_toggle.set_text("运行日志", "chevron_right")
        else:
            self.log_body.pack(fill="x", padx=1, pady=(0, 1))
            self.btn_log_toggle.set_text("运行日志", "chevron_down")
        self.log_visible = not self.log_visible

    def _build_statusbar(self) -> None:
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", side="bottom", padx=self.s(24),
                 pady=(self.s(2), self.s(8)))
        self.status_var = tk.StringVar(value=i18n.tr("就绪"))
        self._status_source = "就绪"
        self.status_label = tk.Label(bar, textvariable=self.status_var, bg=BG,
                                     fg=MUTED, font=FONT_NOTE, anchor="w")
        self.status_label.pack(side="left")
        self.btn_shortcuts = uikit.RoundButton(
            bar, "", "question", self.show_shortcuts, kind="ghost",
            scale=self.scale, bg=BG, height=CONTROL_XS, pad=self.s(4),
            icon_size=ICON_SM, tooltip="快捷键速查（?）")
        self.btn_shortcuts.pack(side="right")
        self.image_label = tk.Label(bar, text="", bg=BG, fg=MUTED,
                                    font=FONT_NOTE)
        self.image_label.pack(side="right", padx=(0, self.s(6)))
        # 保存状态放在状态栏：顶栏空间留给运行控制，避免窗口变窄时被裁切
        self.autosave_label = tk.Label(
            bar, text="执行组自动保存", bg=BG, fg=MUTED, font=FONT_NOTE)
        self.autosave_label.pack(side="right", padx=(0, self.s(12)))
        # 状态文案会变成「未保存… / 已保存 · 时间」，前面保留一个固定的功能提示
        tk.Label(bar, text="自动保存", bg=BG, fg=MUTED,
                 font=FONT_CAPTION).pack(side="right",
                                         padx=(0, self.s(4)))
        self.btn_locate = uikit.RoundButton(
            bar, "定位到失败步骤", "target", self._locate_failed_step,
            kind="outline", scale=self.scale, bg=BG, height=CONTROL_XS,
            pad=self.s(7), font_size=FONT_SIZE_NOTE, tooltip="在步骤表里标出失败的那一步")
        self.btn_resume = uikit.RoundButton(
            bar, "从失败步骤继续", "continue", self._resume_from_failed,
            kind="ghost", scale=self.scale, bg=BG, height=CONTROL_XS,
            pad=self.s(7), font_size=FONT_SIZE_NOTE,
            tooltip="跳过前面的步骤，从失败处接着跑")
        # 默认隐藏：运行出现失败步时才出现

    def _bind_shortcuts(self) -> None:
        mod = "Command" if platform_support.IS_MACOS else "Control"
        self.bind(f"<{mod}-g>", lambda e: self.add_group())
        self.bind(f"<{mod}-Return>", lambda e: self.add_step())
        self.bind("<F5>", lambda e: self.run_group())
        self.bind("<F2>", lambda e: self.edit_step())
        self.bind("<Delete>", self._on_delete_key)
        self.bind(f"<{mod}-z>", lambda e: self.undo())
        self.bind(f"<{mod}-y>", lambda e: self.redo())
        self.bind(f"<{mod}-d>", lambda e: self.toggle_theme())
        self.bind("<F8>", lambda e: self.toggle_debug())
        self.bind("<question>", lambda e: self.show_shortcuts())
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # --------------------------------------------------------------- 状态
    def _load_settings(self) -> dict:
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
                return secrets.restore_settings(json.load(fh))
        except (OSError, ValueError):
            return {}

    def _state_dict(self) -> dict:
        data = secrets.protect_settings(dict(self.settings))
        data.pop("last_path", None)        # 旧版遗留键，界面上已无“项目”
        data.update(self._ui_state())
        data.update({"version": 2, "image_dir": self.image_dir,
                     "groups": [group.to_dict() for group in self.project.groups]})
        data["project_path"] = self.project_path
        return data

    def _ui_state(self) -> dict:
        """收集界面状态（窗口大小、分隔条、列宽、日志展开），随配置一起保存。"""
        state: dict[str, Any] = {"log_visible": self.log_visible}
        try:
            state["window"] = self.geometry()
        except tk.TclError:
            pass
        try:
            if self.paned.winfo_ismapped():
                sash = self.paned.sashpos(0)
                if sash > 0:
                    state["sash"] = int(round(sash / self.scale))
        except (tk.TclError, AttributeError):
            pass
        try:
            state["columns"] = self._step_column_widths()
        except tk.TclError:
            pass
        return state

    def _step_column_widths(self) -> dict:
        """按配置键收集步骤表各列宽度（逻辑像素，不含 DPI 缩放）。"""
        return {
            saved_key: int(round(
                self.step_tree.column(tree_key, "width") / self.scale))
            for tree_key, saved_key in STEP_COLUMN_STATE_KEYS}

    def _on_step_columns_released(self, _event=None) -> None:
        """拖动列宽后立即持久化；只在宽度真正变化时写配置。"""
        widths = self._step_column_widths()
        if widths == getattr(self, "_step_columns_snapshot", None):
            return
        self._step_columns_snapshot = widths
        self.settings["columns"] = dict(widths)
        self._save_state()

    def _restore_ui_state(self) -> None:
        """启动时恢复上次的窗口大小、分隔条位置、列宽与日志展开状态。"""
        geometry = str(self.settings.get("window") or "")
        if geometry:
            try:
                self.geometry(geometry)
            except tk.TclError:
                pass
        columns = self.settings.get("columns") or {}
        for tree_key, saved_key in STEP_COLUMN_STATE_KEYS:
            width = columns.get(saved_key)
            if isinstance(width, (int, float)) and width > 0:
                # 防止旧配置或损坏值把列压到不可见/撑爆窗口。
                self.step_tree.column(
                    tree_key, width=self.s(max(20, min(1600, int(width)))))
        self._step_columns_snapshot = self._step_column_widths()
        self.settings["columns"] = dict(self._step_columns_snapshot)
        if not self.log_visible:
            self.log_body.pack_forget()
            self.btn_log_toggle.set_text("运行日志", "chevron_right")
        self.after(150, self._restore_paned)

    def _restore_paned(self) -> None:
        sash = self.settings.get("sash")
        if isinstance(sash, int) and sash > 0:
            try:
                self.paned.sashpos(0, self.s(sash))
            except tk.TclError:
                pass

    @property
    def reports_dir(self) -> str:
        """运行报告目录；跟随模块级 REPORTS_DIR（测试里可替换）。"""
        return REPORTS_DIR

    # ------------------------------------------------------------- 主题
    def _apply_theme(self, dark: bool) -> None:
        """切换明/暗主题：换色板、重建 ttk 样式、重染控件树、重画图标。

        模块级颜色常量整组换血——之后新建的对话框自动跟上；
        已存在的控件靠 _recolor_widgets 按「旧色 → 新色」映射逐个改。
        """
        palette = DARK_PALETTE if dark else LIGHT_PALETTE
        previous = self._last_palette
        self.dark = bool(dark)
        theme.set_dark(dark)
        _apply_palette(palette)
        dialogs.apply_palette(palette)
        report_dialogs.apply_palette(palette)
        TYPE_COLORS.clear()
        TYPE_COLORS.update(TYPE_COLORS_DARK if dark else LIGHT_TYPE_COLORS)
        uikit.set_dark_mode(dark)
        self._build_style()
        self._recolor_widgets(self, previous, palette)
        # 树标签（斑马纹 / 运行染色）跟着色板走
        self.group_tree.tag_configure("running", foreground=WARNING)
        self.step_tree.tag_configure("running", background=MARK_RUNNING)
        self.step_tree.tag_configure("pending", background=MARK_PENDING)
        self.step_tree.tag_configure("done", background=MARK_DONE)
        self.step_tree.tag_configure("failed", background=MARK_FAILED)
        self.step_tree.tag_configure("even", background=CARD)
        self.step_tree.tag_configure("odd", background=ZEBRA)
        # 日志文字色跟随色板（LOG_* 令牌），切主题时重建列表项颜色
        if hasattr(self, "log_list"):
            self.log_list.configure(
                bg=LOG_BG, fg=LOG_FG,
                selectbackground=theme.token("SELECT_BG"),
                selectforeground=theme.token("TEXT"))
            self._render_log()
        self._icons.clear()            # 图标按新色调重画
        self._refresh_brand_logo()
        self._refresh_tray()           # 托盘图标跟着主题反白
        self._refresh_groups()
        self._refresh_steps()
        if _is_search_placeholder(self.step_search.get()):
            self.step_search.configure(foreground=MUTED)
        self._last_palette = dict(palette)
        self.after(0, lambda: uikit.style_window(self, self.dark))

    def _recolor_widgets(self, widget, old: dict, new: dict) -> None:
        """递归重染控件树：Label/Frame/Text/Canvas 按颜色映射换 bg/fg。

        ttk 控件不在这里处理（它们吃 style，已由 _build_style 重建）；
        RoundButton 是自绘 Canvas，换完底色还要重画一遍。
        """
        bg_map = {old[key]: new[key] for key in new if key in old}
        # 同一色值可能承担多个语义（例如浅色下 CARD / ENTRY_BG / 滚动条
        # 猫爪前景都为 #FFFFFF）。按核心表面令牌覆盖，避免映射到无关的新色。
        for key in ("BG", "CARD", "ZEBRA", "HEADING_BG", "SELECT_BG",
                    "TABLE_HEAD_BG", "TABLE_ROW_HOVER", "MENU_BG",
                    "MENU_ROW_HOVER", "ENTRY_BG", "PREVIEW_BG"):
            if key in old and key in new:
                bg_map[old[key]] = new[key]
        fg_map = {old[key]: new[key]
                  for key in ("TEXT", "MUTED", "TEXT_WEAK", "ACCENT",
                              "ACCENT_STRONG", "SUCCESS", "WARNING",
                              "DANGER", "INFO", "LOG_FG", "LOG_HEAD_FG",
                              "LOG_HEAD_MUTED")
                  if key in old and key in new}

        def _get(widget, option):
            try:
                return str(widget.cget(option))
            except tk.TclError:
                return ""          # Frame / 根窗口没有 -fg，无所谓

        if isinstance(widget, uikit.CatCanvas):
            bg = _get(widget, "bg")
            widget.set_colors(
                bg=bg_map.get(bg, bg),
                ink=fg_map.get(widget.ink, widget.ink),
                accent=fg_map.get(widget.accent, widget.accent),
                nose=fg_map.get(widget.nose, widget.nose))
        elif isinstance(widget, uikit.RoundButton):
            bg = _get(widget, "bg")
            widget.set_bg(bg_map.get(bg, bg))
        elif isinstance(widget, uikit.Switch):
            bg = _get(widget, "bg")
            widget.set_bg(bg_map.get(bg, bg))
        elif isinstance(widget, uikit.SlimScrollbar):
            bg = _get(widget, "bg")
            widget.set_bg(bg_map.get(bg, bg))
        elif isinstance(widget, uikit.TextField):
            bg = _get(widget, "bg")
            widget.set_bg(bg_map.get(bg, bg))
        elif isinstance(widget, uikit.SearchableCombo):
            bg = _get(widget, "bg")
            widget.set_bg(bg_map.get(bg, bg))
        elif isinstance(widget, uikit.RoundedCard):
            widget.set_colors(
                bg=bg_map.get(widget.card_bg, widget.card_bg),
                border=bg_map.get(widget.border, widget.border),
                outer_bg=bg_map.get(widget.outer_bg, widget.outer_bg))
        elif not isinstance(widget, ttk.Widget):
            bg, fg = _get(widget, "bg"), _get(widget, "fg")
            if bg in bg_map:
                try:
                    widget.configure(bg=bg_map[bg])
                except tk.TclError:
                    pass
            if fg in fg_map:
                try:
                    widget.configure(fg=fg_map[fg])
                except tk.TclError:
                    pass
        for child in widget.winfo_children():
            self._recolor_widgets(child, old, new)

    def toggle_theme(self) -> None:
        """明暗主题切换（视图菜单 / Ctrl+D）。"""
        self.dark = not self.dark
        self.settings["theme"] = "dark" if self.dark else "light"
        self._apply_theme(self.dark)
        self._save_state()
        self._log("info", f"已切换到{'深色' if self.dark else '浅色'}主题")

    def _save_state(self) -> bool:
        """把执行组与设置写回程序目录的状态文件（没有“项目”概念）。

        先用临时文件写完整内容，再 os.replace 原子替换：写到一半断电 / 磁盘
        满时，旧文件仍然完好。写之前把旧文件备份到 backups/（只留最近几份）。
        返回 False 表示这次没写成功，调用方可据此决定是否提示。
        """
        try:
            payload = json.dumps(self._state_dict(), ensure_ascii=False,
                                 indent=2)
        except (TypeError, ValueError) as exc:
            self._on_save_failed(f"配置无法序列化：{exc}")
            return False
        if payload == self._last_saved_payload and os.path.exists(
                SETTINGS_PATH):
            # 内容没变就别重写，也别再备份一份同样的文件
            self._dirty = False
            self._set_saved_label()
            return True

        self._backup_settings()
        directory = os.path.dirname(SETTINGS_PATH) or "."
        tmp_path = ""
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                prefix=".rpa_settings_", suffix=".tmp", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, SETTINGS_PATH)
        except OSError as exc:
            self._on_save_failed(exc.strerror or str(exc))
            return False
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        self._last_saved_payload = payload
        self._last_save_error = ""
        self._dirty = False
        self._set_saved_label()
        self._save_project_file()
        return True

    def _save_project_file(self) -> bool:
        """把当前流程同步写回已打开的 .mgproj；没有项目文件就跳过。"""
        if not self.project_path:
            return True
        try:
            project_io.save_project(
                self.project_path, self.project, self.image_dir,
                project_io.project_name(self.project_path))
        except (OSError, ValueError) as exc:
            self._log("warn", f"项目文件保存失败：{exc}")
            return False
        return True

    def _on_save_failed(self, reason: str) -> None:
        """保存失败：状态栏显示原因，保留 dirty 让自动保存继续重试。"""
        self._dirty = True
        text = f"保存失败：{reason}"
        if getattr(self, "autosave_label", None) is not None:
            self.autosave_label.configure(text=text, fg=DANGER)
        # 自动保存每 2 秒重试一次，同一个错误只记一行，避免刷屏
        if reason != self._last_save_error:
            self._last_save_error = reason
            self._log("error", f"配置保存失败：{reason}（会自动重试）")

    def _set_saved_label(self) -> None:
        """状态栏保存指示：已保存 · 时间。"""
        if getattr(self, "autosave_label", None) is not None:
            self.autosave_label.configure(
                text=f"已保存 · {time.strftime('%H:%M')}", fg=MUTED)

    def _backup_settings(self) -> None:
        if not os.path.exists(SETTINGS_PATH):
            return
        try:
            os.makedirs(BACKUPS_DIR, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            shutil.copy2(
                SETTINGS_PATH,
                os.path.join(BACKUPS_DIR, f"rpa_settings_{stamp}.json"))
            backups = sorted(
                name for name in os.listdir(BACKUPS_DIR)
                if name.startswith("rpa_settings_") and name.endswith(".json"))
            for name in backups[:-MAX_CONFIG_BACKUPS]:
                os.remove(os.path.join(BACKUPS_DIR, name))
        except OSError:
            pass                       # 备份失败不影响正常保存

    def restore_backup(self) -> None:
        """从 backups/ 里挑一份配置恢复。"""
        try:
            names = sorted(
                (name for name in os.listdir(BACKUPS_DIR)
                 if name.startswith("rpa_settings_") and name.endswith(".json")),
                reverse=True)
        except OSError:
            names = []
        if not names:
            messagebox.showinfo("提示", "还没有配置备份", parent=self)
            return
        dialog = BackupDialog(self, names)
        choice = dialog.wait()
        if not choice:
            return
        path = os.path.join(BACKUPS_DIR, choice)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            messagebox.showerror("恢复失败", str(exc), parent=self)
            return
        self._push_undo("恢复配置备份")
        self.settings = data
        self.project = Project.from_dict(data)
        self.image_dir = image_dir_path(
            data.get("image_dir", DEFAULT_IMAGE_DIR))
        self.project.image_dir = self.image_dir
        self.group_index = 0
        self._refresh_all(select_group=0)
        self._save_state()
        self._log("info", f"已恢复配置备份：{choice}")

    # ------------------------------------------------------------- 项目文件
    def new_project(self) -> None:
        """清空当前流程，开始一个新的空项目。"""
        if not messagebox.askyesno(
                "新建项目",
                "将清空当前执行组并开始一个新项目。\n"
                "当前流程仍会保存在程序目录的状态文件里。",
                parent=self):
            return
        self._push_undo("新建项目")
        self.project = Project(groups=[], image_dir=self.image_dir)
        self.project_path = ""
        self.group_index = 0
        self._refresh_all(select_group=0)
        self._mark_dirty()
        self._save_state()
        self._log("info", "已新建空项目")
        self._set_status("已新建空项目")

    def register_project_file_assoc(self) -> None:
        """把 .mgproj 关联到 MeowGo（只写当前用户，不动系统默认）。"""
        if not platform_support.IS_WINDOWS:
            messagebox.showinfo(
                "文件关联",
                "当前系统不支持自动关联。\n"
                "macOS 需要打包成 .app，Linux 请在桌面环境的"
                "「默认应用」里设置。", parent=self)
            return
        if file_assoc.is_registered():
            if not messagebox.askyesno(
                    "取消文件关联",
                    "现在双击 .mgproj 会用 MeowGo 打开。\n\n"
                    "要取消这个关联吗？", parent=self):
                return
            ok, error = file_assoc.unregister()
            if ok:
                self._log("info", "已取消 .mgproj 文件关联")
                self._set_status("已取消 .mgproj 文件关联")
            else:
                messagebox.showerror("取消失败", error, parent=self)
            return
        if not messagebox.askyesno(
                "关联 .mgproj 文件",
                "把 .mgproj 项目文件关联到 MeowGo？\n\n"
                "关联后双击项目文件就能直接打开，"
                "只写入当前用户，不需要管理员权限。", parent=self):
            return
        ok, error = file_assoc.register()
        if ok:
            self._log("info", "已关联 .mgproj 文件")
            self._set_status("已关联 .mgproj 文件：双击即可打开")
        else:
            messagebox.showerror("关联失败", error, parent=self)

    def open_project_dialog(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="打开项目",
            filetypes=list(project_io.FILE_TYPES))
        if path:
            self.load_project_file(path, remember=True)

    def load_project_file(self, path: str, remember: bool = True) -> bool:
        """打开 .mgproj：用文件里的执行组替换当前流程。"""
        try:
            project, meta = project_io.load_project(path)
        except ValueError as exc:
            messagebox.showerror("打开项目失败", str(exc), parent=self)
            return False
        self._push_undo("打开项目")
        self.project = project
        self.image_dir = image_dir_path(
            meta.get("image_dir") or self.image_dir)
        self.project.image_dir = self.image_dir
        self.project_path = os.path.abspath(path)
        if remember:
            project_io.remember_project(self.settings, self.project_path)
        self.settings["project_path"] = self.project_path
        self.group_index = 0
        self._refresh_all(select_group=0)
        self._dirty = True
        self._save_state()
        self._log("info", f"已打开项目：{project_io.project_name(path)}")
        self._set_status(f"已打开项目：{project_io.project_name(path)}")
        return True

    def save_project_as(self) -> None:
        initial = (project_io.project_name(self.project_path)
                   if self.project_path else "MeowGo 项目")
        path = filedialog.asksaveasfilename(
            parent=self, title="另存为项目",
            defaultextension=project_io.PROJECT_EXT,
            initialfile=initial,
            filetypes=list(project_io.FILE_TYPES))
        if not path:
            return
        try:
            saved = project_io.save_project(path, self.project, self.image_dir)
        except (OSError, ValueError) as exc:
            messagebox.showerror("保存项目失败", str(exc), parent=self)
            return
        self.project_path = saved
        self.settings["project_path"] = saved
        project_io.remember_project(self.settings, saved)
        self._save_state()
        self._log("info", f"项目已保存：{saved}")
        self._set_status(f"项目已保存：{os.path.basename(saved)}")

    def open_recent_projects(self) -> None:
        paths = project_io.recent_projects(self.settings)
        if not paths:
            messagebox.showinfo("最近项目", "还没有打开过项目文件。", parent=self)
            return
        dialog = RecentProjectsDialog(self, paths)
        choice = dialog.wait()
        if choice:
            self.load_project_file(choice, remember=False)

    def _autosave_tick(self) -> None:
        if self._dirty:
            self._save_state()
        self.after(2000, self._autosave_tick)

    def _mark_dirty(self) -> None:
        self._dirty = True
        if getattr(self, "autosave_label", None) is not None:
            self.autosave_label.configure(text="未保存…", fg=WARNING)

    def open_image_dir(self) -> None:
        """在文件管理器里打开图片目录。"""
        try:
            os.makedirs(self.image_dir, exist_ok=True)
            platform_support.open_path(self.image_dir)
        except (OSError, AttributeError) as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)

    def _set_status(self, text: str, color: Optional[str] = None) -> None:
        """状态栏文案。日志标题只显示运行阶段，避免同一句话出现两遍。"""
        self._status_source = text
        self.status_var.set(i18n.tr(text))
        label = getattr(self, "status_label", None)
        if label is not None:
            label.configure(fg=color or MUTED)

    def _set_log_state(self, text: str) -> None:
        """日志标题的运行阶段：就绪 / 运行中 / 暂停 / 完成 / 失败。"""
        color = {
            "运行中": ACCENT,
            "已暂停": WARNING,
            "执行完成": SUCCESS,
            "执行出错": DANGER,
        }.get(text, LOG_HEAD_MUTED)
        self.log_state.configure(text=text, fg=color)

    def _current_group(self) -> Optional[Group]:
        if 0 <= self.group_index < len(self.project.groups):
            return self.project.groups[self.group_index]
        return None

    def _current_group_name(self) -> str:
        group = self._current_group()
        return group.name if group else ""

    # ---------------------------------------------------------- 列表渲染
    def _refresh_all(self, select_group: Optional[int] = None,
                     select_step: Optional[int] = None) -> None:
        if select_group is not None:
            self.group_index = select_group
        if not self.project.groups:
            self.project.groups.append(Group(self.project.unique_name("默认组")))
        self.group_index = max(0, min(self.group_index,
                                      len(self.project.groups) - 1))
        self._refresh_groups()
        self._refresh_steps(select_step)
        self._mark_dirty()

    def _refresh_groups(self) -> None:
        self.group_tree.delete(*self.group_tree.get_children())
        for index, group in enumerate(self.project.groups):
            iid = f"g{index}"
            label = f"  {group.name}   ({len(group.steps)})"
            if self._step_filter:
                # 跨组搜索：每个组后面显示命中数，方便直接切过去
                hits = sum(1 for step in group.steps
                           if self._step_matches(step))
                label += (f"  · {i18n.tr('匹配')} {hits}" if hits
                          else f"  · {i18n.tr('无匹配')}")
            self.group_tree.insert(
                "", "end", iid=iid,
                text=label,
                image=self._group_row_pic(index == self.group_index),
                open=(index == self.group_index))
            for step_index, step in enumerate(group.steps):
                self.group_tree.insert(
                    iid, "end", iid=f"{iid}_{step_index}",
                    text="   " + self._step_summary(step, step_index),
                    image=self.step_pic(step.type, 13))
        self.group_count.configure(text=f"{len(self.project.groups)} 个组")
        if self.project.groups:
            iid = f"g{self.group_index}"
            if self.group_tree.exists(iid):
                self.group_tree.selection_set(iid)
            self.group_empty_box.place_forget()
        else:
            self.group_empty_box.place(relx=0.5, rely=0.45, anchor="center")

    def _group_row_pic(self, is_open: bool):
        """组行图标：猫爪（展开 / 收起）+ 文件夹，合成为一张图。

        系统三角被 Paw.Treeview 布局去掉后，猫爪必须和文件夹一起画进
        #0 列图标槽；展开时猫爪用品牌橙，收起时用次级文字色。
        """
        key = ("group_row", bool(is_open))
        if key not in self._icons:
            from PIL import Image, ImageTk

            paw = uikit.icon_image(
                "paw", self.s(13), ACCENT_STRONG if is_open else MUTED)
            folder = uikit.icon_image("folder", self.s(16), ACCENT_STRONG)
            gap = self.s(5)
            height = max(paw.height, folder.height)
            canvas = Image.new(
                "RGBA", (paw.width + gap + folder.width, height),
                (0, 0, 0, 0))
            canvas.alpha_composite(paw, (0, (height - paw.height) // 2))
            canvas.alpha_composite(
                folder, (paw.width + gap, (height - folder.height) // 2))
            self._icons[key] = ImageTk.PhotoImage(canvas)
        return self._icons[key]

    def _on_group_click(self, event):
        """点组行最左侧的猫爪：展开 / 收起该组（替代系统三角）。"""
        iid = self.group_tree.identify_row(event.y)
        if not iid or "_" in iid:          # 只处理顶层组节点
            return None
        bbox = self.group_tree.bbox(iid, "#0")
        if not bbox or event.x > bbox[0] + self.s(17):
            return None                     # 点在文字 / 文件夹上：保持默认行为
        is_open = not bool(self.group_tree.item(iid, "open"))
        self.group_tree.item(
            iid, open=is_open, image=self._group_row_pic(is_open))
        self.group_tree.selection_set(iid)
        self.group_tree.focus(iid)
        return "break"

    def _sync_group_paw(self, _event=None) -> None:
        """展开 / 收起（双击、键盘）后同步猫爪状态。"""
        for index in range(len(self.project.groups)):
            iid = f"g{index}"
            if not self.group_tree.exists(iid):
                continue
            is_open = bool(self.group_tree.item(iid, "open"))
            self.group_tree.item(iid, image=self._group_row_pic(is_open))

    def _step_summary(self, step: Step, index: int) -> str:
        text = step.content.strip().replace("\n", " ")
        if step.type == "call":
            text = f"{i18n.tr('调用组：')}{step.content}"
        if len(text) > 22:
            text = text[:22] + "…"
        repeat = f"  ×{step.repeat:g}" if step.repeat else ""
        prefix = "⏸ " if not step.enabled else ""
        return f"{prefix}{index + 1}. {i18n.tr(step.label())} {text}{repeat}"

    def _refresh_steps(self, select: Optional[int] = None) -> None:
        self.step_tree.delete(*self.step_tree.get_children())
        group = self._current_group()
        if group is None:
            return
        self.step_title.configure(text=f"步骤 · {group.name}")
        matched = 0
        for index, step in enumerate(group.steps):
            if not self._step_matches(step):
                continue                      # 搜索过滤：不匹配的行不出现
            matched += 1
            repeat = "" if not step.repeat else f"{step.repeat:g}"
            iid = str(index)
            label = ("⏸ " if not step.enabled else "") + step.label()
            self.step_tree.insert(
                "", "end", iid=iid,
                image=self.step_pic(step.type, 14),
                values=(index + 1, label, step.content, repeat,
                        step.note),
                tags=self._step_tags(index))
        filtered = self._step_filter_active()
        if matched:
            self.step_empty_box.place_forget()
        else:
            if filtered:
                self.step_empty.configure(
                    text=f"未找到与“{self._step_filter}”匹配的步骤")
                self.step_empty_hint.configure(
                    text="请尝试其他关键词，或清除搜索以显示全部步骤。")
                self.step_empty_btn.set_text("清除搜索", "close")
                self.step_empty_btn.command = self._clear_search_and_focus
            else:
                self.step_empty.configure(text="此执行组尚未添加步骤")
                self.step_empty_hint.configure(
                    text="请添加第一个步骤，或通过右键菜单批量导入。")
                self.step_empty_btn.set_text("新增步骤", "add")
                self.step_empty_btn.command = self.add_step
            self.step_empty_box.place(relx=0.5, rely=0.45, anchor="center")
        if filtered:
            self.search_count.configure(
                text=f"{matched}/{len(group.steps)}", bg=ACCENT_MIST,
                fg=ACCENT_STRONG, padx=self.s(8), pady=self.s(2))
        else:
            self.search_count.configure(text="", bg=CARD, fg=MUTED,
                                        padx=0, pady=0)
        if select is not None and 0 <= select < len(group.steps):
            if self.step_tree.exists(str(select)):
                self.step_tree.selection_set(str(select))
                self.step_tree.see(str(select))
        self._repaint_step_marks()
        self._update_preview()
        self._set_status(f"当前组：{group.name}（{len(group.steps)} 步）"
                         if not self.running else self.status_var.get())

    @staticmethod
    def _zebra_tag(index: int) -> str:
        """斑马纹：奇数行浅蓝底（与运行染色共存，后者优先级更高）。"""
        return "odd" if index % 2 else "even"

    def _step_tags(self, index: int) -> tuple[str, ...]:
        """步骤行的基础标签：禁用灰显 + 斑马纹。"""
        group = self._current_group()
        tags: list[str] = []
        if (group is not None and 0 <= index < len(group.steps)
                and not group.steps[index].enabled):
            tags.append("disabled")
        tags.append(self._zebra_tag(index))
        return tuple(tags)

    def _selected_step(self) -> Optional[int]:
        selection = self.step_tree.selection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except ValueError:
            return None

    def _selected_steps(self) -> list[int]:
        """多选（extended）时返回全部选中行号，升序；单选时只有一个。"""
        rows = []
        for iid in self.step_tree.selection():
            try:
                rows.append(int(iid))
            except ValueError:
                continue
        return sorted(rows)

    def _select_all_steps(self, _event=None) -> str:
        """Ctrl+A：选中当前组所有可见步骤（搜索过滤时只选可见的）。"""
        rows = self.step_tree.get_children()
        if rows:
            self.step_tree.selection_set(*rows)
        return "break"

    def _refresh_step_row(self, index: int) -> None:
        group = self._current_group()
        if group is None or not self.step_tree.exists(str(index)):
            return
        step = group.steps[index]
        repeat = "" if not step.repeat else f"{step.repeat:g}"
        label = ("⏸ " if not step.enabled else "") + step.label()
        self.step_tree.item(str(index),
                            image=self.step_pic(step.type, 14),
                            values=(index + 1, label, step.content,
                                    repeat, step.note),
                            tags=self._step_tags(index))
        self._refresh_groups()
        self._mark_dirty()

    # --------------------------------------------------------- 步骤搜索
    def _step_filter_active(self) -> bool:
        return bool(self._step_filter)

    def _step_matches(self, step: Step) -> bool:
        """类型名 / 中文标签 / 内容 / 备注，任一包含过滤词即匹配。"""
        text = self._step_filter.lower()
        if not text:
            return True
        hay = f"{step.label()} {step.type} {step.content} {step.note}".lower()
        return text in hay

    def _on_search_focus_in(self, _event=None) -> None:
        if _is_search_placeholder(self.step_search.get()):
            self.step_search.delete(0, "end")
            self.step_search.configure(foreground=TEXT)
        if hasattr(self, "search_field"):
            self.search_field.set_colors(border=FIELD_FOCUS)
        self._sync_search_clear()

    def _on_search_focus_out(self, _event=None) -> None:
        if not self.step_search.get():
            self.step_search.insert(0, _search_placeholder())
            self.step_search.configure(foreground=MUTED)
        if hasattr(self, "search_field"):
            self.search_field.set_colors(border=BORDER)
        self._apply_step_filter()

    def _on_search_key(self, event) -> None:
        if event.keysym == "Escape":
            return                      # Escape 走 _on_search_escape
        self._apply_step_filter()

    def _on_search_escape(self, _event=None) -> str:
        self._clear_step_filter()
        self.step_search.focus_set()
        return "break"

    def _sync_search_clear(self) -> None:
        """有实际搜索词时才显示 ×；占位符不算。"""
        text = self.step_search.get()
        if text in ("", SEARCH_PLACEHOLDER) or _is_search_placeholder(text):
            self.btn_search_clear.pack_forget()
        else:
            self.btn_search_clear.pack()

    def _clear_search_and_focus(self) -> None:
        """空状态主按钮 / × 按钮共用：清空过滤并把焦点放回搜索框。"""
        self._clear_step_filter()
        self.step_search.focus_set()

    def _apply_step_filter(self) -> None:
        text = self.step_search.get()
        self._step_filter = ("" if _is_search_placeholder(text)
                             else text.strip())
        self._sync_search_clear()
        self._refresh_steps()
        self._refresh_groups()

    def _clear_step_filter(self) -> None:
        self._step_filter = ""
        self.step_search.delete(0, "end")
        self.step_search.insert(0, _search_placeholder())
        self.step_search.configure(foreground=MUTED)
        self._sync_search_clear()
        self._refresh_steps()
        self._refresh_groups()

    # ------------------------------------------------------- 图片预览
    def _step_image_path(self, step: Step) -> str:
        """这一步用到的图片文件路径；不是图片步骤或文件不存在时返回空串。"""
        if step.type not in IMAGE_STEP_TYPES:
            return ""
        name = (step.content or "").split("|", 1)[0].strip()
        if not name:
            return ""
        path = (name if os.path.isabs(name)
                else os.path.join(self.image_dir, name))
        return path if os.path.isfile(path) else ""

    def _on_step_select(self, _event=None) -> None:
        self._update_preview()

    def _update_preview(self) -> None:
        """选中图片类步骤→显示缩略图 / 文件名 / 尺寸；否则收起窗格。"""
        path = ""
        index = self._selected_step()
        group = self._current_group()
        if (index is not None and group is not None
                and 0 <= index < len(group.steps)):
            path = self._step_image_path(group.steps[index])
        if not path:
            self._preview_photo = None
            self._preview_path = ""
            self.preview_frame.pack_forget()
            return
        try:
            from PIL import Image, ImageTk

            image = Image.open(path).convert("RGBA")
            width, height = image.size
            image.thumbnail((self.s(132), self.s(60)), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
        except Exception:  # noqa: BLE001 — 读不动的图就当没有预览
            self.preview_frame.pack_forget()
            return
        self._preview_photo = photo      # 保住引用，否则会被 GC 掉
        self._preview_path = path
        self.preview_image.configure(image=photo)
        self.preview_name.configure(text=os.path.basename(path))
        self.preview_meta.configure(
            text=f"{width} × {height} 像素 · "
                 f"{os.path.getsize(path) / 1024:.0f} KB")
        if not self.preview_frame.winfo_ismapped():
            self.preview_frame.pack(fill="x", side="bottom",
                                    before=self.step_wrap)

    def _open_preview_image_dir(self) -> None:
        """在资源管理器里定位当前预览的图片（Windows 带选中效果）。"""
        path = self._preview_path
        if path and os.path.isfile(path):
            try:
                os.system(f'explorer /select,"{os.path.normpath(path)}"')
                return
            except Exception:  # noqa: BLE001 — 退回打开目录
                pass
        self.open_image_dir()

    # ------------------------------------------------------- 组的增删改
    def _on_group_select(self, _event=None) -> None:
        selection = self.group_tree.selection()
        if not selection:
            return
        iid = selection[0]
        if "_" in iid:  # 选中的是步骤子节点 → 跳转到该步骤
            group_part, step_part = iid.split("_", 1)
            index = int(group_part[1:])
            if index != self.group_index:
                self.group_index = index
                self._refresh_groups()
                self._refresh_steps(select=int(step_part))
            else:
                self.step_tree.selection_set(step_part)
                self.step_tree.see(step_part)
            self.group_tree.selection_set(f"g{index}")
            return
        index = int(iid[1:])
        if index != self.group_index:
            self.group_index = index
            self._refresh_groups()
            self._refresh_steps()

    def _on_group_double(self, _event=None) -> None:
        if self.step_tree.selection():
            self.edit_step()

    def _on_group_drop(self, source: str, target: str) -> None:
        """组树拖拽：只重排顶层组节点（拖到子步骤上时落到该组）。"""
        if "_" in source:               # 源是步骤子节点：不处理
            return
        if "_" in target:               # 目标是子节点：落到它所属的组
            target = target.split("_")[0]
        try:
            self.move_group_to(int(source[1:]), int(target[1:]))
        except (ValueError, IndexError):
            pass

    def _on_step_drop(self, source: str, target: str) -> None:
        """步骤树拖拽换行。"""
        if self._step_filter_active():
            # 过滤后行号是「跳号」的，拖动会把步骤送到错误的位置
            self._set_status("搜索过滤中不能拖动排序，请先清空搜索框")
            return
        try:
            self.move_step_to(int(source), int(target))
        except (ValueError, IndexError):
            pass

    def add_group(self) -> None:
        dialog = GroupDialog(self, "新建执行组",
                             self.project.unique_name("新组"))
        name = dialog.wait()
        if not name:
            return
        if name in self.project.names():
            messagebox.showwarning("重名", f"执行组「{name}」已存在", parent=self)
            return
        self._push_undo("新建执行组")
        self.project.groups.append(Group(name))
        self._refresh_all(select_group=len(self.project.groups) - 1)
        self._log("info", f"已新建执行组：{name}")

    def restore_default_groups(self) -> None:
        """把缺失的预置执行组补回来；同名组保留，不覆盖用户内容。"""
        existing = set(self.project.names())
        missing = [group for group in build_default_groups()
                   if group.name not in existing]
        if not missing:
            messagebox.showinfo("预置执行组",
                                "所有预置执行组都已存在。", parent=self)
            return
        names = "、".join(group.name for group in missing)
        if not messagebox.askyesno(
                "恢复预置执行组",
                f"将补回 {len(missing)} 个预置执行组：\n{names}\n\n"
                "已有同名组会保留，不会覆盖。是否继续？",
                parent=self):
            return
        self._push_undo("恢复预置执行组")
        if (len(self.project.groups) == 1
                and _is_default_placeholder(self.project.groups[0])):
            self.project.groups.clear()
        start = len(self.project.groups)
        self.project.groups.extend(missing)
        self._refresh_all(select_group=start)
        self._log("info", f"已恢复预置执行组：{names}")
        self._set_status(f"已恢复 {len(missing)} 个预置执行组")

    def rename_group(self) -> None:
        group = self._current_group()
        if group is None:
            return
        dialog = GroupDialog(self, "重命名执行组", group.name)
        name = dialog.wait()
        if not name or name == group.name:
            return
        if name in self.project.names():
            messagebox.showwarning("重名", f"执行组「{name}」已存在", parent=self)
            return
        old = group.name
        self._push_undo("重命名执行组")
        group.name = name
        # 同步更新所有引用该组的调用步骤
        for other in self.project.groups:
            for step in other.steps:
                if step.type == "call" and step.content.strip() == old:
                    step.content = name
        self._refresh_all()
        self._log("info", f"执行组「{old}」已重命名为「{name}」")

    def edit_group_retry(self) -> None:
        """设置整组重试：整组跑完仍有失败步骤时重跑几次、间隔多久。"""
        group = self._current_group()
        if group is None:
            return
        dialog = GroupDialog(self, "组级重试", group.name,
                             retry=group.retry,
                             retry_delay=group.retry_delay,
                             with_retry=True, show_name=False)
        if dialog.wait() is None:
            return
        if (dialog.retry, dialog.retry_delay) == (group.retry,
                                                  group.retry_delay):
            return
        self._push_undo("组级重试设置")
        group.retry = dialog.retry
        group.retry_delay = dialog.retry_delay
        self._refresh_all()
        if group.retry:
            self._log("info", f"执行组「{group.name}」整组重试："
                              f"{group.retry} 次，间隔 "
                              f"{group.retry_delay:g} 秒")
        else:
            self._log("info", f"执行组「{group.name}」已关闭整组重试")

    def duplicate_group(self) -> None:
        group = self._current_group()
        if group is None:
            return
        self._push_undo("复制执行组")
        clone = Group(self.project.unique_name(group.name + "副本"),
                      [step.copy() for step in group.steps], group.note)
        self.project.groups.insert(self.group_index + 1, clone)
        self._refresh_all(select_group=self.group_index + 1)
        self._log("info", f"已复制为执行组：{clone.name}")

    def delete_group(self) -> None:
        group = self._current_group()
        if group is None:
            return
        if not messagebox.askyesno("确认删除",
                                   f"确定删除执行组「{group.name}」及其 "
                                   f"{len(group.steps)} 个步骤？", parent=self):
            return
        self._push_undo("删除执行组")
        self.project.groups.pop(self.group_index)
        if not self.project.groups:
            self.project.groups.append(Group(self.project.unique_name("默认组")))
        self._refresh_all(select_group=min(self.group_index,
                                           len(self.project.groups) - 1))
        self._log("info", f"已删除执行组：{group.name}")

    def move_group(self, delta: int) -> None:
        target = self.group_index + delta
        if not (0 <= target < len(self.project.groups)):
            return
        self._push_undo("移动执行组")
        groups = self.project.groups
        groups[self.group_index], groups[target] = \
            groups[target], groups[self.group_index]
        self._refresh_all(select_group=target)

    def move_group_to(self, source: int, target: int) -> None:
        """把执行组从 source 拖到 target（拖拽排序用）。"""
        groups = self.project.groups
        if not (0 <= source < len(groups)
                and 0 <= target < len(groups)) or source == target:
            return
        self._push_undo("移动执行组")
        group = groups.pop(source)
        groups.insert(target, group)
        self._refresh_all(select_group=target)

    # ----------------------------------------------------- 步骤的增删改
    def add_step(self) -> None:
        group = self._current_group()
        if group is None:
            return
        dialog = StepDialog(self, self.project)
        if dialog.wait() is None:
            return
        self._push_undo("新增步骤")
        rows = self._selected_steps()
        position = len(group.steps) if not rows else rows[-1] + 1
        group.steps.insert(position, dialog.result)
        self._refresh_all(select_step=position)
        self._log("info", f"已新增步骤：{dialog.result.label()}")

    def edit_step(self) -> None:
        group = self._current_group()
        index = self._selected_step()
        if group is None or index is None:
            messagebox.showinfo("提示", "请先在右侧选择一个步骤", parent=self)
            return
        dialog = StepDialog(self, self.project, group.steps[index])
        if dialog.wait() is None:
            return
        self._push_undo("编辑步骤")
        group.steps[index] = dialog.result
        self._refresh_step_row(index)
        self.step_tree.selection_set(str(index))

    def duplicate_step(self) -> None:
        group = self._current_group()
        rows = self._selected_steps()
        if group is None or not rows:
            return
        self._push_undo("复制步骤")
        copies = [group.steps[index].copy() for index in rows]
        position = rows[-1] + 1
        for offset, step in enumerate(copies):
            group.steps.insert(position + offset, step)
        self._refresh_all(select_step=position + len(copies) - 1)
        count = len(copies)
        self._set_status(f"已复制 {count} 个步骤" if count > 1 else "已复制步骤")

    def toggle_steps_enabled(self) -> None:
        """启用 / 禁用选中的步骤（多选时整块切换）。

        有任意一步启用时，整体改为禁用；全部已禁用时整体恢复启用。
        """
        group = self._current_group()
        rows = self._selected_steps()
        if group is None or not rows:
            return
        enabled = not all(group.steps[index].enabled for index in rows)
        self._push_undo("启用步骤" if enabled else "禁用步骤")
        for index in rows:
            group.steps[index].enabled = enabled
        self._refresh_groups()
        self._refresh_steps()
        ids = [str(index) for index in rows
               if self.step_tree.exists(str(index))]
        if ids:
            self.step_tree.selection_set(ids)
            self.step_tree.see(ids[0])
        self._mark_dirty()
        action = "已启用" if enabled else "已禁用"
        count = len(rows)
        self._set_status(f"{action} {count} 个步骤" if count > 1
                         else f"{action}第 {rows[0] + 1} 步")

    def delete_step(self) -> None:
        group = self._current_group()
        rows = self._selected_steps()
        if group is None or not rows:
            return
        if len(rows) == 1:
            index = rows[0]
            step = group.steps[index]
            if step.content and not messagebox.askyesno(
                    "确认删除", f"确定删除第 {index + 1} 步（{step.label()}）？",
                    parent=self):
                return
        elif not messagebox.askyesno(
                "确认删除", f"确定删除选中的 {len(rows)} 个步骤？", parent=self):
            return
        self._push_undo("删除步骤")
        for index in reversed(rows):        # 从后往前删，行号才不会错位
            group.steps.pop(index)
        self._refresh_all(select_step=min(rows[0], len(group.steps) - 1))
        if len(rows) > 1:
            self._set_status(f"已删除 {len(rows)} 个步骤")

    def move_step(self, delta: int) -> None:
        """上移/下移：多选时整块平移（块顶/块底触界则不动）。"""
        group = self._current_group()
        rows = self._selected_steps()
        if group is None or not rows:
            return
        if len(rows) == 1:
            index = rows[0]
            target = index + delta
            if not (0 <= target < len(group.steps)):
                return
            self._push_undo("移动步骤")
            group.steps[index], group.steps[target] = \
                group.steps[target], group.steps[index]
            self._refresh_all(select_step=target)
            return
        if delta < 0 and rows[0] <= 0:
            return
        if delta > 0 and rows[-1] >= len(group.steps) - 1:
            return
        self._push_undo("移动步骤")
        block = [group.steps.pop(index) for index in reversed(rows)]
        block.reverse()
        insert_at = rows[0] + delta
        for offset, step in enumerate(block):
            group.steps.insert(insert_at + offset, step)
        self._refresh_all(select_step=insert_at)
        self._set_status(f"已移动 {len(block)} 个步骤")

    def move_step_to(self, source: int, target: int) -> None:
        """把步骤从 source 行拖到 target 行（拖拽排序用）。"""
        group = self._current_group()
        if group is None:
            return
        if not (0 <= source < len(group.steps)
                and 0 <= target < len(group.steps)) or source == target:
            return
        self._push_undo("移动步骤")
        step = group.steps.pop(source)
        group.steps.insert(target, step)
        self._refresh_all(select_step=target)

    def _on_delete_key(self, _event=None) -> None:
        if self.focus_get() is self.step_tree:
            self.delete_step()

    def _on_step_key_delete(self, _event=None) -> str:
        """列表内的 Delete：返回 break，避免再触发窗口级绑定（会删两行）。"""
        self.delete_step()
        return "break"

    def _on_step_key_edit(self, _event=None) -> str:
        self.edit_step()
        return "break"

    def _on_step_key_toggle(self, _event=None) -> str:
        self.toggle_steps_enabled()
        return "break"

    # ------------------------------------------------------- 列表内编辑
    def _on_step_double(self, event) -> None:
        row = self.step_tree.identify_row(event.y)
        column = self.step_tree.identify_column(event.x)
        if not row:
            return
        self.step_tree.selection_set(row)
        if column in ("#0", ""):
            self.edit_step()
            return
        self._inline_edit(row, column)

    def _inline_edit(self, row: str, column: str) -> None:
        """在列表单元格上就地编辑。"""
        self._close_inline()
        index = int(row)
        group = self._current_group()
        if group is None or index >= len(group.steps):
            return
        step = group.steps[index]
        bbox = self.step_tree.bbox(row, column)
        if not bbox:
            return
        x, y, width, height = bbox
        field = {"#1": "no", "#2": "type", "#3": "content", "#4": "repeat",
                 "#5": "note"}.get(column, "content")
        if field == "no":
            return

        if field == "type":
            widget: uikit.ReadonlySelect | uikit.TextField = uikit.ReadonlySelect(
                self.step_tree,
                values=[f"{i + 1} · {i18n.tr(info[0])}"
                        for i, info in enumerate(STEP_TYPES.values())],
                scale=self.scale, bg=CARD,
                font_size=FONT_SIZE_NOTE, width=18)
            widget.set(f"{list(STEP_TYPES).index(step.type) + 1} · "
                       f"{i18n.tr(step.label())}")
        else:
            widget = uikit.TextField(
                self.step_tree, scale=self.scale, bg=CARD,
                font_size=FONT_SIZE_NOTE)
            widget.set({"content": step.content,
                        "repeat": "" if not step.repeat else f"{step.repeat:g}",
                        "note": step.note}[field])
        can_record = field == "content" and step.type == "hotkey"
        entry_width = max(width - self.s(40), self.s(80)) if can_record \
            else max(width, self.s(120))
        widget.place(x=x, y=y, width=entry_width, height=height)
        widget.focus_set()
        if isinstance(widget, uikit.TextField):
            widget.select_range(0, "end")
        self._editor = widget

        if can_record:
            # 热键内容列右侧放一个键盘按钮：点击后直接按组合键录入
            self._inline_target = widget
            button = uikit.RoundButton(
                self.step_tree, "", "keyboard", self._inline_record,
                kind="outline", scale=self.scale, bg=CARD,
                height=max(1, round(height / self.scale)),
                pad=self.s(6), icon_size=ICON_SM,
                tooltip="按键录入：直接按组合键，全部松开自动录入")
            button.place(x=x + entry_width + self.s(4), y=y, height=height)
            self._inline_record_btn = button

        def commit(_event=None):
            if self._editor is not widget:   # 已被别的操作关闭/替换
                return
            if self._inline_recording:       # 按键录制中：先不提交
                return
            value = widget.get()
            self._close_inline()
            self._apply_inline(index, field, value)

        def cancel(_event=None):
            self._close_inline()

        widget.bind("<Return>", commit)
        widget.bind("<Escape>", cancel)
        if isinstance(widget, uikit.ReadonlySelect):
            # 自绘下拉选择后立即提交；失焦时延后一点，避开弹出面板的焦点切换
            widget.command = commit
            widget.entry.bind("<FocusOut>",
                              lambda e: self.after(150, commit))
        elif isinstance(widget, uikit.TextField):
            widget.bind("<FocusOut>", commit)

    def _close_inline(self) -> None:
        if self._inline_recorder.active:
            self._inline_recorder.stop()
            self._inline_recording = False
        self._inline_target = None
        if self._inline_record_btn is not None:
            try:
                self._inline_record_btn.place_forget()
                self._inline_record_btn.destroy()
            except tk.TclError:
                pass
            self._inline_record_btn = None
        if self._editor is not None:
            editor, self._editor = self._editor, None
            try:
                editor.place_forget()
                editor.destroy()
            except tk.TclError:
                pass

    # -- 行内按键录入 -------------------------------------------------------
    def _inline_record(self) -> None:
        widget = self._inline_target
        if widget is None or self._inline_recorder.active:
            return
        self._inline_previous = widget.get()
        if not self._inline_recorder.start(self._inline_previous):
            self._set_status("无法启动键盘录制（键盘钩子安装失败），请手动输入")

    def _inline_record_state(self, active: bool) -> None:
        self._inline_recording = active
        widget = self._inline_target
        if widget is not None:
            widget.configure(state="disabled" if active else "normal")
        if self._inline_record_btn is not None:
            self._inline_record_btn.set_enabled(not active)

    def _inline_set_text(self, text: str) -> None:
        widget = self._inline_target
        if not isinstance(widget, uikit.TextField):
            return
        try:
            restore = str(widget.cget("state")) == "disabled"
            if restore:
                widget.configure(state="normal")
            widget.delete(0, "end")
            widget.insert(0, text)
            if restore:
                widget.configure(state="disabled")
        except tk.TclError:
            pass

    def _inline_record_done(self, text: str) -> None:
        self._inline_set_text(text)
        widget = self._inline_target
        if isinstance(widget, uikit.TextField):
            widget.focus_set()
            widget.select_range(0, "end")
        self._set_status(f"已录入热键：{text.replace(',', ' + ')}（回车确认）")

    def _inline_record_cancelled(self) -> None:
        self._inline_set_text(self._inline_previous)
        self._set_status("已取消按键录入")

    def _apply_inline(self, index: int, field: str, value: str) -> None:
        group = self._current_group()
        if group is None or index >= len(group.steps):
            return
        step = group.steps[index]
        if field == "type":
            try:
                key = list(STEP_TYPES)[int(value.split("·")[0].strip()) - 1]
            except (ValueError, IndexError):
                return
        elif field == "repeat":
            try:
                float(value) if value.strip() else 0.0
            except ValueError:
                messagebox.showwarning("格式错误", "重复次数必须是数字", parent=self)
                return
        self._push_undo("编辑步骤")
        if field == "type":
            step.type = key
        elif field == "content":
            step.content = value
        elif field == "repeat":
            step.repeat = float(value) if value.strip() else 0.0
        elif field == "note":
            step.note = value
        self._refresh_step_row(index)
        self.step_tree.selection_set(str(index))
        self._set_status(f"已更新第 {index + 1} 步")

    # ----------------------------------------------------------- 撤销重做
    def _push_undo(self, action: str) -> None:
        """变更前调用：记下当前 project 快照，供 Ctrl+Z 恢复。"""
        self._undo_stack.append(
            (action, {"project": self.project.to_dict(),
                      "group_index": self.group_index,
                      "project_path": self.project_path}))
        if len(self._undo_stack) > 50:      # 防止无限增长
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._sync_undo_buttons()

    def _sync_undo_buttons(self) -> None:
        """工具栏撤销/重做按钮的启用态跟随栈深度。"""
        if getattr(self, "btn_undo", None) is not None:
            self.btn_undo.set_enabled(bool(self._undo_stack))
            self.btn_redo.set_enabled(bool(self._redo_stack))

    def _restore(self, snapshot: dict) -> None:
        self.project = Project.from_dict(snapshot["project"])
        self.project.image_dir = self.image_dir
        self.project_path = snapshot.get("project_path", self.project_path)
        self.group_index = min(snapshot.get("group_index", 0),
                               max(0, len(self.project.groups) - 1))
        self._refresh_all(select_group=self.group_index)
        self._mark_dirty()

    def undo(self) -> None:
        if not self._undo_stack:
            self._set_status("没有可撤销的操作")
            return
        action, snapshot = self._undo_stack.pop()
        self._redo_stack.append(
            (action, {"project": self.project.to_dict(),
                      "group_index": self.group_index,
                      "project_path": self.project_path}))
        self._restore(snapshot)
        self._sync_undo_buttons()
        self._set_status(f"已撤销：{action}")

    def redo(self) -> None:
        if not self._redo_stack:
            self._set_status("没有可重做的操作")
            return
        action, snapshot = self._redo_stack.pop()
        self._undo_stack.append(
            (action, {"project": self.project.to_dict(),
                      "group_index": self.group_index,
                      "project_path": self.project_path}))
        self._restore(snapshot)
        self._sync_undo_buttons()
        self._set_status(f"已重做：{action}")

    def _undo_label(self) -> str:
        return f"撤销 {self._undo_stack[-1][0]}" if self._undo_stack \
            else "撤销（无）"

    def _redo_label(self) -> str:
        return f"重做 {self._redo_stack[-1][0]}" if self._redo_stack \
            else "重做（无）"

    # ----------------------------------------------------------- 右键菜单
    def _menu(self, items) -> uikit.ContextMenu:
        """统一风格的自绘圆角菜单（在菜单外点右键会顺势弹出新的菜单）。"""
        menu = uikit.ContextMenu(self, self.scale, items)
        menu.on_outside_right = self._dispatch_right_click
        return menu

    def _register_right_click(self) -> None:
        """登记「控件 → 它的右键菜单」，供菜单外右键时就地换一份菜单。"""
        self._right_click_targets = {self.step_tree: self._step_menu,
                                     self.group_tree: self._group_menu}

    def _dispatch_right_click(self, x_root: int, y_root: int) -> None:
        """把菜单外的这次右键交给指针下面的控件（找不到就只是收起菜单）。"""
        try:
            widget = self.winfo_containing(x_root, y_root)
        except tk.TclError:
            widget = None
        while widget is not None:
            handler = getattr(self, "_right_click_targets", {}).get(widget)
            if handler is not None:
                event = types.SimpleNamespace(
                    x_root=x_root, y_root=y_root,
                    x=x_root - widget.winfo_rootx(),
                    y=y_root - widget.winfo_rooty())
                handler(event)
                return
            widget = getattr(widget, "master", None)

    def _step_menu(self, event) -> None:
        row = self.step_tree.identify_row(event.y)
        if row:
            # 点在已选中的行上 → 保留多选；否则只选中这一行
            if row not in self.step_tree.selection():
                self.step_tree.selection_set(row)
        group = self._current_group()
        rows = self._selected_steps()
        index = rows[0] if rows else None
        total = len(group.steps) if group else 0
        has = index is not None
        multi = len(rows) > 1
        is_image = (has and group is not None and index is not None
                    and 0 <= index < len(group.steps)
                    and group.steps[index].type in IMAGE_STEP_TYPES)
        all_disabled = bool(rows) and group is not None and all(
            0 <= row < len(group.steps) and not group.steps[row].enabled
            for row in rows)
        toggle_item = uikit.MenuItem(
            "启用步骤" if all_disabled else "禁用步骤", "check",
            self.toggle_steps_enabled, "Ctrl+E", enabled=has)
        if multi:
            items = [
                uikit.MenuItem(f"复制选中步骤（{len(rows)}）", "copy",
                               self.duplicate_step, accent=True),
                toggle_item,
                uikit.MenuItem(f"删除选中步骤（{len(rows)}）", "trash",
                               self.delete_step, "Delete", danger=True),
                "-",
                uikit.MenuItem("整块上移", "up", lambda: self.move_step(-1),
                               enabled=rows[0] > 0),
                uikit.MenuItem("整块下移", "down",
                               lambda: self.move_step(1),
                               enabled=rows[-1] < total - 1),
            ]
        else:
            items = [
                uikit.MenuItem("新增步骤…", "add", self.add_step,
                               "Ctrl+Enter", accent=True),
                uikit.MenuItem("编辑步骤…", "pencil", self.edit_step, "F2",
                               enabled=has),
                uikit.MenuItem("复制步骤", "copy", self.duplicate_step,
                               enabled=has),
                toggle_item,
                uikit.MenuItem("截取屏幕并替换图片…", "image",
                               lambda: self.capture_region(self._apply_capture),
                               enabled=is_image),
                "-",
                uikit.MenuItem("运行到这一步", "debug", self.run_to_step,
                               "到它才单步确认", enabled=has),
                "-",
                uikit.MenuItem("上移", "up", lambda: self.move_step(-1),
                               enabled=index is not None and index > 0),
                uikit.MenuItem("下移", "down", lambda: self.move_step(1),
                               enabled=index is not None
                               and index < total - 1),
                "-",
                uikit.MenuItem("删除步骤", "trash", self.delete_step,
                               "Delete", enabled=has, danger=True),
            ]
        self._menu(items).popup(event.x_root, event.y_root)

    def _group_menu(self, event) -> None:
        row = self.group_tree.identify_row(event.y)
        if row and "_" not in row:      # 点在步骤子节点上时不切换当前组
            self.group_tree.selection_set(row)
            self.group_index = int(row[1:])
            self._refresh_groups()
            self._refresh_steps()
        index = self.group_index
        total = len(self.project.groups)
        items = [
            uikit.MenuItem("运行这个组", "run", self.run_group, "F5",
                           accent=True),
            uikit.MenuItem("单步调试运行这个组", "debug",
                           self.run_group_debug, "每步确认"),
            uikit.MenuItem("循环运行这个组", "loop", self.run_loop),
            "-",
            uikit.MenuItem("新建执行组…", "add", self.add_group, "Ctrl+G"),
            uikit.MenuItem("恢复预置执行组…", "add",
                           self.restore_default_groups, "补回缺失模板"),
            uikit.MenuItem("重命名执行组…", "pencil", self.rename_group),
            uikit.MenuItem("组级重试…", "redo", self.edit_group_retry,
                           "整组失败后重跑"),
            uikit.MenuItem("复制为副本", "copy", self.duplicate_group),
            "-",
            uikit.MenuItem("上移", "up", lambda: self.move_group(-1),
                           enabled=index > 0),
            uikit.MenuItem("下移", "down", lambda: self.move_group(1),
                           enabled=index < total - 1),
            "-",
            uikit.MenuItem("删除执行组", "trash", self.delete_group,
                           danger=True),
        ]
        self._menu(items).popup(event.x_root, event.y_root)

    # ---------------------------------------------------------- Excel 互转
    def import_excel(self, single: bool) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="导入 Excel 配置",
            filetypes=[("Excel 文件", "*.xls *.xlsx"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            groups = import_excel(path, single=single)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("导入失败", str(exc), parent=self)
            return
        if not groups:
            messagebox.showinfo("提示", "该文件没有可导入的数据", parent=self)
            return
        # 名字冲突时自动重命名（同一批导入内部也要去重）
        taken = set(self.project.names())
        for group in groups:
            base, name, index = group.name, group.name, 2
            while name in taken:
                name = f"{base}{index}"
                index += 1
            taken.add(name)
            group.name = name
        self._push_undo("导入 Excel")
        self.project.groups.extend(groups)
        self._refresh_all(select_group=len(self.project.groups) - len(groups))
        self._log("info", f"已导入 {len(groups)} 个执行组："
                          f"{'、'.join(g.name for g in groups)}")

    def export_excel(self, single: bool) -> None:
        groups = self.project.groups
        if single:
            current = self._current_group()
            if current is None:
                return
            groups = [current]
        path = filedialog.asksaveasfilename(
            parent=self, title="导出为 Excel", defaultextension=".xlsx",
            initialfile=(f"{groups[0].name}.xlsx" if single else "所有执行组.xlsx"),
            filetypes=[("Excel 工作簿", "*.xlsx"), ("Excel 97-2003", "*.xls")])
        if not path:
            return
        try:
            export_excel(path, groups, single=single)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self._log("info", f"已导出 {len(groups)} 个执行组到 {path}")
        self._set_status(f"已导出到 {os.path.basename(path)}")

    # --------------------------------------------------------------- 截图
    def capture_region(self, on_done: Optional[Callable[[str], None]] = None) -> None:
        self._capture_callback = on_done
        self._capture_coords_callback = None
        self.withdraw()
        self.after(320, self._open_capture_overlay)

    def pick_region(self, on_done: Callable[[int, int, int, int], None]) -> None:
        """只框选坐标（不截图），用于步骤的「识别范围」。"""
        self._capture_callback = None
        self._capture_coords_callback = on_done
        self.withdraw()
        self.after(320, self._open_capture_overlay)

    def _open_capture_overlay(self) -> None:
        try:
            import ctypes

            user32 = ctypes.windll.user32
            left = user32.GetSystemMetrics(76)
            top = user32.GetSystemMetrics(77)
            width = user32.GetSystemMetrics(78)
            height = user32.GetSystemMetrics(79)
        except Exception:
            left, top = 0, 0
            width, height = self.winfo_screenwidth(), self.winfo_screenheight()

        overlay = tk.Toplevel(self)
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        overlay.geometry(f"{width}x{height}+{left}+{top}")
        overlay.configure(bg="#000000")
        try:
            overlay.attributes("-alpha", 0.28)
        except tk.TclError:
            pass
        canvas = tk.Canvas(overlay, bg="#000000", highlightthickness=0,
                           cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        coords_cb = getattr(self, "_capture_coords_callback", None)
        canvas.create_text(
            width // 2, self.s(60),
            text=("拖拽框选识别范围（只记坐标，不截图），Esc 取消"
                  if coords_cb else
                  "按住鼠标拖拽框选要识别的区域，Esc 取消"),
            fill="white", font=FONT_TITLE_LARGE)
        state = {"x": 0, "y": 0, "rect": None}

        def on_press(event):
            state["x"], state["y"] = event.x, event.y
            state["rect"] = canvas.create_rectangle(event.x, event.y, event.x,
                                                    event.y,
                                                    outline=ACCENT_STRONG,
                                                    width=2)

        def on_drag(event):
            if state["rect"]:
                canvas.coords(state["rect"], state["x"], state["y"], event.x,
                              event.y)

        def on_release(event):
            x1, y1 = state["x"] + left, state["y"] + top
            x2, y2 = event.x + left, event.y + top
            overlay.destroy()
            restore_grab()
            coords_callback = getattr(self, "_capture_coords_callback", None)
            self._capture_coords_callback = None

            def restore_window() -> None:
                self.deiconify()
                self.lift()

            if abs(x2 - x1) < 8 or abs(y2 - y1) < 8:
                restore_window()
                self._set_status("框选区域太小，已取消")
                return
            if coords_callback:
                restore_window()
                coords_callback(min(x1, x2), min(y1, y2),
                                abs(x2 - x1), abs(y2 - y1))
                return
            path = assets.unique_name(self.image_dir)
            try:
                # 必须先截图、后恢复主窗口：反过来的话主窗口会盖住选区，
                # 截到的是 MeowGo 自己的白底（表现为「截图与实际不符」）。
                # 遮罩刚销毁，稍等一会儿让桌面重绘完再拍。
                time.sleep(0.15)
                assets.grab_region(x1, y1, x2, y2, path)
            except Exception as exc:  # noqa: BLE001
                restore_window()
                messagebox.showerror("截图失败", str(exc), parent=self)
                return
            restore_window()
            if assets.is_blank_image(path):
                # 纯色截图会到处命中（点击位置随机），当场提醒比跑挂了好
                self._log("warn", i18n.tr(
                    "截图几乎是纯色，可能没框到内容；请确认目标已显示后重新截图"))
            name = self._relative_image(path)
            self._log("info", f"已保存截图：{name}")
            callback = getattr(self, "_capture_callback", None)
            if callback:
                callback(name)

        def on_cancel(_event=None):
            self._capture_coords_callback = None
            self._capture_callback = None
            overlay.destroy()
            restore_grab()
            self.deiconify()
            self.lift()

        # 从模态对话框（步骤编辑、数据预览等）里点「截图」时，对话框的
        # local grab 会把整个程序内的鼠标/键盘事件全部截走，遮罩作为
        # 另一个 Toplevel 收不到任何输入——表现为「截图没反应、Esc 也
        # 没用」。这里先把 grab 抢过来给遮罩，截图结束（保存或取消）
        # 后再还给原窗口，恢复它的模态语义。
        previous_grab = self.grab_current()

        def restore_grab() -> None:
            try:
                if previous_grab is not None and previous_grab.winfo_exists():
                    previous_grab.grab_set()
            except tk.TclError:
                pass                     # 原窗口已没了：无所谓

        try:
            overlay.grab_set()
        except tk.TclError:
            previous_grab = None

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        overlay.bind("<Escape>", on_cancel)
        overlay.focus_force()

    def _relative_image(self, path: str) -> str:
        try:
            rel = os.path.relpath(path, self.image_dir)
            return path if rel.startswith("..") else rel.replace("\\", "/")
        except ValueError:
            return path

    def _apply_capture(self, name: str) -> None:
        """把截好的图片应用到当前选中的图片类步骤。"""
        group = self._current_group()
        index = self._selected_step()
        if group is None or index is None:
            return
        step = group.steps[index]
        if step.type not in IMAGE_STEP_TYPES:
            messagebox.showinfo(
                "提示",
                f"第 {index + 1} 步是「{step.label()}」，不能使用图片。\n"
                "请选中一个图片类步骤，或在「新增步骤」里选择图片类指令。",
                parent=self)
            return
        step.content = name
        self._refresh_step_row(index)

    # --------------------------------------------------------------- 日志
    def _enqueue_log(self, level: str, message: str) -> None:
        run_log = self._run_log
        if run_log is not None:
            run_log.write(level, message, self._current_run_step)
        self.log_queue.put((level, message))

    def _log(self, level: str, message: str) -> None:
        self._enqueue_log(level, message)

    def _drain_queue(self) -> None:
        try:
            while True:
                level, message = self.log_queue.get_nowait()
                display = i18n.tr(message)
                stamp = time.strftime("%H:%M:%S")
                target = None
                # 出错 / 超时的日志行关联到当前执行的步骤，可点击跳转
                if (self._current_run_step is not None
                        and (level == "error" or "出错" in message
                             or "超时" in message)):
                    target = self._current_run_step
                entry = (level, display, stamp, target)
                # deque(maxlen=MAX_LOG_LINES) 自动丢弃最早条目：
                # 文本区与原始条目都保持上限，长跑 / 循环运行不会无限占内存
                self._log_entries.append(entry)
                if self._log_entry_matches(entry):
                    self._insert_log_entry(entry)
                    self._trim_log()
                    self.log_list.see("end")
        except queue.Empty:
            pass
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "step":
                    self._highlight_step(*payload)  # type: ignore[misc]
                elif kind == "step_done":
                    self._apply_step_done(*payload)  # type: ignore[misc]
                elif kind == "pending":
                    self._set_pending_step(*payload)  # type: ignore[misc]
                elif kind == "progress":
                    self._apply_progress(*payload)  # type: ignore[misc]
                elif kind == "confirm":
                    self._ask_debug(*payload)  # type: ignore[misc]
                elif kind == "persist":
                    self._apply_persist_var(*payload)  # type: ignore[misc]
                elif kind == "end":
                    self._on_run_end(str(payload))
                elif kind == "stop":
                    self.stop_run()
                elif kind == "run":
                    # 确认框打开时 F5 = 继续运行（对话框内绑定），别再起一程；
                    # 全局热键和窗口内按键会同时到达，这里挡掉后者。
                    if (self._debug_dialog is None
                            and not self._app_is_frontmost()):
                        self.run_group()
        except queue.Empty:
            pass
        self.after(120, self._drain_queue)

    def _persist_var_from_worker(self, key: str, value: str) -> None:
        """工作线程里写的持久变量：交回主线程落盘（Tk 不能跨线程）。"""
        self.ui_queue.put(("persist", (key, value)))

    def _apply_persist_var(self, key: str, value: str) -> None:
        """主线程：把持久变量写进设置并保存（失败只影响下次运行时的初值）。"""
        store = self.settings.setdefault("persistent_vars", {})
        if not isinstance(store, dict):
            store = {}
            self.settings["persistent_vars"] = store
        store[str(key)] = str(value)
        self._save_state()

    def _insert_log_entry(self, entry) -> None:
        level, display, stamp, target = entry
        colors = {"info": LOG_INFO, "warn": LOG_WARN, "error": LOG_ERROR,
                  "muted": LOG_HEAD_MUTED, "time": LOG_TIME}
        index = self.log_list.size()
        self.log_list.insert("end", f"[{stamp}] {display}")
        self.log_list.itemconfigure(
            index, foreground=colors.get(level, LOG_HEAD_MUTED))
        self._visible_targets.append(target)

    def _log_entry_matches(self, entry) -> bool:
        level, display, _stamp, _target = entry
        if self._log_level == "info" and level not in ("info", "muted", "time"):
            return False
        if self._log_level == "warn" and level != "warn":
            return False
        if self._log_level == "error" and level != "error":
            return False
        return not (self._log_search
                    and self._log_search not in display.lower())

    def _render_log(self) -> None:
        self.log_list.delete(0, "end")
        self._visible_targets.clear()
        for entry in self._log_entries:
            if self._log_entry_matches(entry):
                self._insert_log_entry(entry)
        self._trim_log()
        self.log_list.see("end")

    def _on_log_filter(self) -> None:
        mapping = {"全部": "", "信息": "info", "警告": "warn", "错误": "error"}
        self._log_level = mapping.get(self.log_level_var.get(), "")
        self._render_log()

    def _on_log_search(self, _event=None) -> None:
        self._log_search = self.log_search_var.get().strip().lower()
        self._render_log()

    def _trim_log(self) -> None:
        """日志行数上限：超出的最早行连同跳转目标一起丢掉。"""
        while self.log_list.size() > MAX_LOG_LINES:
            self.log_list.delete(0)
            self._visible_targets.popleft()

    def _log_target_at(self, y: int):
        index = self.log_list.nearest(y)
        if 0 <= index < len(self._visible_targets):
            return self._visible_targets[index]
        return None

    def _on_log_link_click(self, event) -> None:
        """点日志行：如果该行关联了失败步骤，就跳过去。"""
        target = self._log_target_at(event.y)
        if target:
            self._jump_to_step(*target)

    def _on_log_hover(self, event) -> None:
        cursor = "hand2" if self._log_target_at(event.y) else ""
        try:
            self.log_list.configure(cursor=cursor)
        except tk.TclError:
            pass

    def clear_log(self) -> None:
        self._log_entries.clear()
        self.log_list.delete(0, "end")
        self._visible_targets.clear()

    def copy_log(self) -> None:
        """把当前可见日志（最近 MAX_LOG_LINES 行）复制到剪贴板。"""
        text = "\n".join(str(line) for line in self.log_list.get(0, "end"))
        if not text.strip():
            self._set_status("日志为空，没有可复制的内容")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        lines = self.log_list.size()
        self._set_status(f"已复制 {lines} 行日志到剪贴板")

    # --------------------------------------------------------------- 执行
    def _validate(self, names: Optional[list[str]] = None,
                  check_images: bool = True) -> bool:
        groups = self.project.groups
        if names is not None:
            wanted = set(names)
            index = {g.name: g for g in groups}
            keep = set(names)
            for name in names:
                group = index.get(name)
                if group is None:
                    continue
                for step in group.steps:
                    if step.type == "call":
                        keep.add(step.content.strip())
            groups = [g for g in groups if g.name in keep or g.name in wanted]
        problems = validate_environment(groups) + validate_project(
            groups, base_dir=self.image_dir, check_images=check_images)
        if problems:
            messagebox.showwarning("配置有误", "\n".join(problems[:12]), parent=self)
            for text in problems[:12]:
                self._log("error", text)
            return False
        return True

    def _snapshot(self, names: Optional[list[str]]) -> dict[str, list[Step]]:
        groups = self.project.groups
        if names is None:
            names = [g.name for g in groups]
        index = {g.name: g for g in groups}
        wanted = set(names)
        pending = list(names)
        while pending:
            name = pending.pop()
            group = index.get(name)
            if group is None:
                continue
            for step in group.steps:
                if step.type == "call":
                    target = step.content.strip()
                    if target not in wanted:
                        wanted.add(target)
                        pending.append(target)
        return {name: [step.copy() for step in index[name].steps]
                for name in wanted if name in index}

    def run_group(self) -> bool:
        return self._start(self._current_group_name())

    def run_group_for(self, name: str) -> bool:
        """运行指定组（定时运行用；不切换左侧选中）。"""
        return self._start(name)

    def run_loop(self) -> bool:
        return self._start(self._current_group_name(), loop=True)

    def run_from_step(self) -> None:
        """从当前组选中的那一行开始运行（前面的步骤视为已完成）。"""
        group = self._current_group()
        rows = self._selected_steps()
        if group is None or not rows:
            messagebox.showinfo("提示", "请先在步骤表里选中一行", parent=self)
            return
        index = rows[0]
        self._log("info", f"从「{group.name}」第 {index + 1} 步开始运行")
        self._start(group.name, start_step=index)

    def _resume_from_failed(self) -> None:
        """运行结束后：从失败的那一步继续。"""
        if self._failed_step is None:
            return
        group, index = self._failed_step
        self._log("info", f"从失败步骤继续：{group} 第 {index + 1} 步")
        self._start(group, start_step=index)

    def run_group_debug(self) -> None:
        """单步调试运行当前组：只这一程每步确认，不动全局开关。"""
        if self.running:
            messagebox.showinfo("提示", "正在运行中，请先停止", parent=self)
            return
        self._debug_once = True
        self._start(self._current_group_name())

    def run_to_step(self) -> None:
        """运行到选中步骤：到它才开始单步确认（相当于设个断点）。

        断点之前的步骤照常执行；到达选中的那一步时弹出第一张确认框，
        之后的每步都需要确认，方便长流程里只盯着后半段调试。
        """
        if self.running:
            messagebox.showinfo("提示", "正在运行中，请先停止", parent=self)
            return
        group = self._current_group()
        rows = self._selected_steps()
        if group is None or not rows:
            messagebox.showinfo("提示", "请先在步骤表里选中一行", parent=self)
            return
        index = rows[0]
        self._log("info", f"运行到「{group.name}」第 {index + 1} 步时暂停确认")
        self._debug_once = True
        self._start(group.name, debug_breakpoint=(group.name, index))

    def run_all(self) -> bool:
        return self._start(None)

    def run_with_data(self) -> None:
        """选一个 CSV/Excel，按行带数据跑当前组。"""
        if self.running:
            messagebox.showinfo("提示", "正在运行中，请先停止", parent=self)
            return
        path = filedialog.askopenfilename(
            parent=self, title="选择数据文件（第一行是表头）",
            filetypes=[("表格文件", "*.csv;*.xls;*.xlsx;*.xlsm"),
                       ("所有文件", "*.*")])
        if not path:
            return
        try:
            from rpa.engine import load_data
            data = load_data(path)
        except Exception as exc:  # noqa: BLE001 — 读表失败要提示，不崩
            messagebox.showerror("读取失败", f"读不出数据：{exc}", parent=self)
            return
        if not data:
            messagebox.showwarning("提示", "这个文件里没有数据行", parent=self)
            return
        # 先预览再跑：看清列名和数据行数，避免选错文件
        preview = DataPreviewDialog(self, path, data)
        if preview.wait() is not True:
            return

        # 上次跑过就询问是否接着跑：结果文件里记了每行的 __行号
        start_row = 0
        try:
            from rpa.engine import read_results, result_path

            old_rows = read_results(result_path(path))

            def number(row: dict) -> int:
                try:
                    return int(float(row.get("__行号", 0) or 0))
                except (TypeError, ValueError):
                    return 0

            numbers = [number(row) for row in old_rows]
            last = max(numbers, default=0)
            # 最后一段失败（常见于致命错误中止）时，从最后一个成功行的下一行
            # 继续，重试失败的那一行，而不是直接跳过它。
            last_ok = max((n for n, row in zip(numbers, old_rows)
                           if str(row.get("__结果", "")).strip() == "成功"),
                          default=0)
            resume = last_ok if last > last_ok else last
            if resume and resume < len(data):
                if last > last_ok:
                    question = (f"上次在第 {last_ok + 1} 行失败，"
                                f"是否从第 {resume + 1} 行继续重试？\n\n"
                                "选择「否」将从第一行开始。")
                else:
                    question = (f"上次已执行至第 {last} 行，"
                                f"是否从第 {last + 1} 行继续？\n\n"
                                "选择「否」将从第一行开始。")
                if messagebox.askyesno("继续上次？", question, parent=self):
                    start_row = resume
            elif last >= len(data) and len(data):
                if not messagebox.askyesno(
                        "执行已完成",
                        f"上次已完成全部 {len(data)} 行，是否重新从头执行？",
                        parent=self):
                    return
        except Exception:      # noqa: BLE001 - 旧结果文件读不了就从头跑
            start_row = 0
        self._start(self._current_group_name(), data=data,
                    data_path=path, row_offset=start_row)

    def _start(self, entry: Optional[str], loop: bool = False,
               data: Optional[list] = None,
               data_path: Optional[str] = None,
               row_offset: int = 0, start_step: int = 0,
               debug_breakpoint: Optional[tuple[str, int]] = None) -> bool:
        if self.running:
            messagebox.showinfo("提示", "正在运行中，请先停止", parent=self)
            return False
        names = None if entry is None else [entry]
        if entry is not None and not entry:
            return False
        if sum(len(g.steps) for g in self.project.groups) == 0:
            messagebox.showinfo("提示", "还没有可执行的步骤", parent=self)
            return False
        if not self._validate(names):
            return False
        self.executor.base_dir = self.image_dir    # 步骤里的图片名相对图片目录
        self.executor.groups = self._snapshot(names)
        self.executor.order = ([entry] if entry else self.project.names())
        # 组级重试：整组跑完还有失败步骤就整组再来一次
        self.executor.group_retry = {
            group.name: (int(group.retry or 0), float(group.retry_delay or 0.5))
            for group in self.project.groups}
        self.executor.confidence = float(self.settings.get("confidence", 0.9))
        # 显式设置（普通运行为 None）：避免上一次带数据运行影响这次
        self.executor.data = data  # type: ignore[assignment]  # None 表示清空
        self.executor.row_offset = int(row_offset or 0) if data else 0
        self._data_path = (data_path or "") if data else ""
        # 失败截图：默认开；设置里可以关掉（截图可能含敏感信息）
        self.executor.shot_dir = (SHOTS_DIR if self.settings.get(
            "shot_on_fail", True) else "")
        self.executor.on_step = self._mark_running_step
        self.executor.on_step_done = self._mark_step_done
        self.executor.on_progress = self._on_progress
        # 单步调试：每步执行前向界面问一句（None = 不启用）
        # 一次性调试（右键「单步调试运行」）不落盘，随这次运行生效
        debug = self.debug_mode or self._debug_once
        delay = int(self.settings.get("delay", 3))
        # 单步调试也最小化主窗：确认框关掉时如果主窗是前台，Windows 会把
        # 焦点交给它，下一步就丢了目标窗口。只留确认框和迷你条在前台。
        self.iconify()
        self._countdown(delay,
                        lambda: self._launch(entry, loop, debug=debug,
                                             start_step=start_step,
                                             breakpoint=debug_breakpoint),
                        debug=debug)
        return True

    def _countdown(self, seconds: int, callback, debug: bool = False) -> None:
        self._countdown_token += 1
        token = self._countdown_token
        if seconds <= 0:
            callback()
            return
        overlay = tk.Toplevel(self)
        overlay.withdraw()            # 先按文字量好尺寸再显示，避免闪一下
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        overlay.configure(bg=LOG_BG)
        label = tk.Label(overlay, text="", bg=LOG_BG, fg=LOG_FG,
                         font=FONT_HERO)
        label.pack(expand=True)
        hint = tk.Label(overlay,
                        text=("即将开始（单步调试），请切换到目标窗口（Esc 取消）"
                              if debug else
                              "即将开始，请切换到目标窗口（Esc 取消）"),
                        bg=LOG_BG, fg=LOG_HEAD_MUTED, font=FONT_NOTE,
                        wraplength=self.s(360), justify="center")
        hint.pack(padx=self.s(16), pady=(0, self.s(12)))
        # 固定 280 宽会把中文提示裁掉：按文字实际请求尺寸撑开窗口
        overlay.update_idletasks()
        width = max(self.s(280), overlay.winfo_reqwidth())
        height = max(self.s(126), overlay.winfo_reqheight())
        x = (self.winfo_screenwidth() - width) // 2
        y = (self.winfo_screenheight() - height) // 2
        overlay.geometry(f"{width}x{height}+{x}+{y}")
        overlay.deiconify()
        state = {"left": seconds, "cancelled": False}

        def cancel(_event=None):
            state["cancelled"] = True
            self._countdown_token += 1
            self._schedule_pending = False
            self._cancel_schedule_record("skipped", "启动倒计时被取消")
            overlay.destroy()
            self.deiconify()

        overlay.bind("<Escape>", cancel)

        def tick():
            if state["cancelled"]:
                return
            if token != self._countdown_token:
                # 被新的倒计时取代：关掉自己的遮罩，别留在屏幕中间
                overlay.destroy()
                if self._active_schedule is not None:
                    self._cancel_schedule_record("skipped", "启动被新的运行取代")
                return
            if state["left"] <= 0:
                overlay.destroy()
                callback()
                return
            label.configure(text=str(state["left"]))
            state["left"] -= 1
            overlay.after(1000, tick)

        tick()

    def _launch(self, entry: Optional[str], loop: bool, debug: bool = False,
                start_step: int = 0,
                breakpoint: Optional[tuple[str, int]] = None) -> None:
        self._schedule_pending = False
        # 防止界面和计划任务 / 另一个副本同时抢鼠标
        lock = InstanceLock(make_lock_name(APP_DIR, "RUN"))
        if not lock.acquire():
            self._cancel_schedule_record("skipped", "已有其它运行在进行")
            messagebox.showinfo(
                "已有运行在进行",
                "另一个 MeowGo 正在运行（可能是计划任务），\n"
                "等它结束后再试。", parent=self)
            self.deiconify()
            return
        self._run_lock = lock
        self.running = True
        self._refresh_tray_menu()
        self.paused = False
        self._debug_once = False        # 一次性开关跑完即弃
        self._debug_continue = False    # 每次运行都重新逐步骤确认
        self._pending_step = None
        # 倒计时期间用户已经切到目标窗口：记下来，之后每步确认完把焦点还它
        self._debug_target = _target_foreground_window()
        # 每次运行单独落一份过程日志，事后能按时间/步骤回查
        self._run_log = logs.RunLog(REPORTS_DIR,
                                    entry or "全部执行组")
        self._last_run_log_path = self._run_log.path
        if self._run_log.opened:
            self._log("info", f"运行日志：{self._run_log.path}")
        for button in (self.btn_run, self.btn_loop):
            button.set_enabled(False)
        self.btn_pause.set_enabled(True)
        self.btn_stop.set_enabled(True)
        # 清掉上一次运行的染色与定位信息
        self._step_marks = {}
        self._failed_step = None
        self._current_run_step = None
        self.btn_locate.pack_forget()
        self.btn_resume.pack_forget()
        self._set_status("运行中…" + ("（循环）" if loop else "")
                         + ("（单步调试）" if debug else ""))
        self._set_log_state("运行中")
        if not self.log_visible:      # 收起时自动展开，方便看进度
            self.toggle_log()
        # 估算总步数：循环 / 无限重复时返回 -1，进度条改脉冲模式
        rows = max(1, len(self.executor.data) if self.executor.data else 1)
        rows = max(1, rows - int(self.executor.row_offset or 0))
        total = estimate_steps(self.executor.groups, self.executor.order, rows)
        self._reset_progress(total, pulse=loop)
        self._show_mini_bar()
        # 单步调试：引擎每步执行前会调 confirm（None = 不启用）
        self.executor.confirm = self._confirm_step if debug else None
        # 「运行到这一步」的断点只在这程用；非调试运行清空
        self._debug_breakpoint = breakpoint if debug else None
        if debug:
            self._log("info", "单步调试：每一步执行前都会确认")

        def worker():
            result = self.executor.run(entry, loop=loop,
                                       start_step=start_step)
            self.ui_queue.put(("end", result))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _on_run_end(self, result: str) -> None:
        # 先把报告和结果落盘，再放开按钮：否则用户马上再跑会重置 executor，
        # 正在写的报告 / 结果可能串到下一次运行。
        try:
            self._write_run_report(result)
            self._write_result_file()
            self._show_summary(result)
            self._finish_schedule_history(result)
            self._notify_finish(result)
        finally:
            self._close_debug_dialog("stop")   # 运行结束时别把确认框留在屏幕上
            self.running = False
            self._refresh_tray_menu()
            self.paused = False
            self._schedule_pending = False
            self._debug_continue = False
            self._debug_breakpoint = None
            self._debug_target = None
            self._pending_step = None
            if self._run_lock is not None:
                self._run_lock.release()
                self._run_lock = None
            for button in (self.btn_run, self.btn_loop, self.btn_pause):
                button.set_enabled(True)
            self.btn_pause.set_text("暂停", "pause")
            self.btn_stop.set_enabled(False)
            if self.step_tree.exists("_running"):
                self.step_tree.delete("_running")
            self._finish_progress()
            self._hide_mini_bar()
            if self._tray_window_visible:
                # 藏在托盘时不强行弹窗，改由 _notify_finish 发气泡
                self.deiconify()
                self.lift()
            self._refresh_all()
            result_text = {"finished": "执行完成", "stopped": "已停止",
                           "error": "执行出错"}.get(result, result)
            self._set_log_state(result_text)
            if self._failed_step is not None:
                self.btn_locate.pack(side="right", padx=(0, self.s(6)))
                self.btn_resume.pack(side="right", padx=(0, self.s(4)))
            try:
                report = self.executor.report()
                if report.get("vars"):
                    preview = "，".join(
                        f"{key}={report['vars'][key]}"
                        for key in list(sorted(report["vars"]))[:6])
                    self._log("info", "运行变量：" + preview +
                              ("…" if len(report["vars"]) > 6 else ""))
            except Exception:  # noqa: BLE001 — 变量快照失败不影响收尾
                pass
            run_log = self._run_log
            if run_log is not None:
                try:
                    report = self.executor.report()
                    summary = (f"运行结束：{result_text} · "
                               f"{report['steps']} 步，失败 {report['failed']}，"
                               f"耗时 {report['seconds']:g} 秒")
                except Exception:  # noqa: BLE001 — 统计失败不影响收尾
                    summary = f"运行结束：{result_text}"
                run_log.close(summary)
                self._run_log = None

    def _show_summary(self, result: str) -> None:
        """运行结束摘要：写到软件最底行状态栏（不再占用日志上方空间）。"""
        title = {"finished": "执行完成", "stopped": "已停止",
                 "error": "执行出错"}.get(result, result)
        color = {"finished": SUCCESS, "stopped": WARNING,
                 "error": DANGER}.get(result, MUTED)
        try:
            report = self.executor.report()
        except Exception:  # noqa: BLE001 — 报告失败不影响收尾
            self._set_status(i18n.tr(title), color)
            return
        parts = [i18n.tr(title)]
        if report["rows"] > 1:
            parts.append(f"{report['rows']} {i18n.tr('行数据')}")
        parts.append(f"{report['steps']} {i18n.tr('步')}")
        parts.append(f"{i18n.tr('失败')} {report['failed']}")
        parts.append(f"{i18n.tr('耗时')} {report['seconds']:g} {i18n.tr('秒')}")
        self._set_status(" · ".join(parts), color)

    # ------------------------------------------------------- 运行进度
    def _reset_progress(self, total: int, pulse: bool) -> None:
        """开跑前重置进度条：total<0 或循环运行时改用脉冲（不确定进度）。"""
        was_pulse = self._progress_pulse
        self._progress_total = total
        self._progress_pulse = bool(pulse or total < 0)
        if self._progress_pulse:
            self.progress.configure(mode="indeterminate")
            self.progress.start(self.s(8))
            self.progress_label.configure(
                text="循环运行中…" if pulse else "步数不可估…")
        else:
            if was_pulse:               # 只有脉冲模式才需要停
                try:
                    self.progress.stop()
                except tk.TclError:
                    pass
            self.progress.configure(mode="determinate",
                                    maximum=max(total, 1))
            self.progress["value"] = 0
            self.progress_label.configure(text=f"0/{total}")

    def _on_progress(self, done: int, total: int) -> None:
        """工作线程调用：只入队，交给主线程刷新。"""
        self.ui_queue.put(("progress", (done, total)))

    def _apply_progress(self, done: int, total: int) -> None:
        """主线程：更新进度条与文字；脉冲模式下只显示已执行步数。"""
        if not self._progress_pulse:
            self.progress.configure(maximum=max(total, 1))
            self.progress["value"] = min(done, total)
            self.progress_label.configure(text=f"{done}/{total}")
        else:
            self.progress_label.configure(text=f"已执行 {done} 步")
        if self._mini_bar is not None:
            self._mini_bar.set_counts(done, total)

    def _finish_progress(self) -> None:
        self._progress_pulse = False
        try:
            self.progress.stop()
        except tk.TclError:
            pass
        self.progress.configure(mode="determinate")
        self.progress_label.configure(text="")

    def _show_mini_bar(self) -> None:
        """运行中的迷你置顶条：主窗口最小化时也能看到进度。"""
        if not self.settings.get("show_mini_bar", True):
            return
        if self._mini_bar is None:
            self._mini_bar = RunMiniBar(self)
        else:
            try:
                self._mini_bar.win.deiconify()
            except tk.TclError:
                self._mini_bar = RunMiniBar(self)

    def _hide_mini_bar(self) -> None:
        if self._mini_bar is not None:
            self._mini_bar.destroy()
            self._mini_bar = None

    # ------------------------------------------------------- 单步调试
    def toggle_debug(self) -> None:
        """开/关单步调试（工具栏 / F8）：下次运行每步前都会问一句。"""
        self.debug_mode = not self.debug_mode
        self.settings["debug_mode"] = self.debug_mode
        self.btn_debug.set_kind("accent" if self.debug_mode else "ghost")
        self._save_state()
        state = "已开启" if self.debug_mode else "已关闭"
        self._set_status(f"单步调试{state}"
                         + ("" if self.running else "（下次运行生效）"))
        self._log("info", f"单步调试{state}")

    def _confirm_step(self, name: str, index: int, step: Step) -> str:
        """工作线程调用：把确认请求抛给主线程，阻塞等用户选择。

        等待期间每 0.1 秒看一眼停止标志，急停不用等对话框超时；
        超过 3 分钟无应答按「停止」处理，运行不会永远挂住。
        """
        if self._debug_continue:      # 已点「继续运行」：剩余步骤不再询问
            return "run"
        # 「运行到这一步」：断点之前照常执行；到了断点才开始逐步确认
        if self._debug_breakpoint is not None:
            if (name, index) != self._debug_breakpoint:
                return "run"
            self._debug_breakpoint = None
            self._log("info", f"单步调试：已到达「{name}」第 {index + 1} 步，"
                              "从这里开始逐步确认")
        event = threading.Event()
        box = {"answer": "stop"}
        self.ui_queue.put(("pending", (name, index)))
        self.ui_queue.put(("confirm", (name, index, step, event, box)))
        deadline = time.time() + 180
        while not event.wait(0.1):
            if self.executor.stop_event.is_set() or time.time() >= deadline:
                box["answer"] = "stop"
                break
        return box["answer"]

    def _set_pending_step(self, group_name: str, index: int) -> None:
        """主线程：等待确认的那一步先染成蓝底，明确告诉用户停在哪。"""
        self._pending_step = (group_name, index)
        names = self.project.names()
        if group_name in names:
            position = names.index(group_name)
            if position != self.group_index:
                self.group_index = position
                self._refresh_groups()
                self._refresh_steps()
        self._repaint_step_marks()
        if self.step_tree.exists(str(index)):
            self.step_tree.see(str(index))
        self._set_status(f"单步调试：等待确认 · 第 {index + 1} 步")

    def _clear_pending_step(self) -> None:
        if self._pending_step is None:
            return
        self._pending_step = None
        self._repaint_step_marks()

    def _close_debug_dialog(self, answer: str) -> None:
        """关掉正在等待的调试对话框（急停 / 运行结束时用）。"""
        dialog = self._debug_dialog
        self._debug_dialog = None
        if dialog is None:
            return
        try:
            dialog.result = answer
            dialog.destroy()
        except tk.TclError:
            pass

    def _ask_debug(self, name: str, index: int, step: Step,
                   event: threading.Event, box: dict) -> None:
        """主线程：弹出单步调试对话框，把用户选择交回工作线程。

        单步调试期间主窗一直保持最小化（见 _start）：否则对话框一关，
        焦点会落到主窗上，下一步就找不到刚才的目标窗口了。对话框独立
        置顶显示；用户点完按钮后把前台焦点还给目标窗口。
        """
        if not self.running:
            box["answer"] = "stop"
            event.set()
            return
        if self.state() == "normal":
            self.iconify()            # 用户中途把主窗点回来了：继续最小化
        foreground = _target_foreground_window()
        if foreground:                # 记住用户当前操作的目标窗口
            self._debug_target = foreground
        answer = "stop"
        try:
            dialog = DebugStepDialog(
                self, name, index, step,
                snapshot=self.executor.debug_snapshot(),
                target_title=window_title(foreground or self._debug_target))
            self._debug_dialog = dialog
            answer = dialog.wait()
        except tk.TclError:
            answer = "stop"
        finally:
            self._debug_dialog = None
            self._clear_pending_step()
            # 对话框拿过焦点，关掉后还给目标窗口：下一步直接在原窗口执行。
            # 停止 / 出错时不用还，免得打断用户正在做的事。
            if answer != "stop":
                _focus_window(self._debug_target)
        if answer == "continue":
            # 「继续运行」= 本步照常执行，之后不再询问
            self._debug_continue = True
            self._log("info", "单步调试：已选择继续运行剩余步骤")
            self._set_status("单步调试：继续运行剩余步骤")
            answer = "run"
        box["answer"] = answer or "stop"
        event.set()

    def stop_run(self) -> None:
        if not self.running:
            return
        self.executor.stop()
        self._clear_pending_step()
        self._close_debug_dialog("stop")   # 对话框正等确认时立刻关掉
        self._set_status("正在停止…")
        self._set_log_state("正在停止…")

    # --------------------------------------------------------------- 定时运行
    def _schedule_tick(self) -> None:
        """每秒对一次表：到点就自动跑设定的执行组（界面开着才有效）。"""
        try:
            tasks = schedule_tasks(self.settings)
            today = time.strftime("%Y-%m-%d")
            current = time.strftime("%H:%M")
            if not self.running and not self._schedule_pending:
                for task in tasks:
                    if not task.get("enabled") or not schedule_due(task):
                        continue
                    key = f"{today}|{task.get('id', '')}|{current}"
                    if key in self._schedule_fired:
                        continue
                    self._schedule_fired.add(key)
                    self._fire_schedule(task)
                    break
            # 清理前一天的火星记录，避免集合无限增长
            if len(self._schedule_fired) > 32:
                self._schedule_fired = {
                    k for k in self._schedule_fired if k.startswith(today)}
        finally:
            self.after(1000, self._schedule_tick)

    def _fire_schedule(self, task: dict) -> None:
        """执行一条到点的任务，并把这次触发写进历史。"""
        group = task.get("group") or ""
        if group and group not in self.project.names():
            self._log("warn", f"定时运行跳过：找不到执行组「{group}」")
            self._record_schedule(task, group, "skipped",
                                  f"找不到执行组「{group}」")
            return
        if not group:
            self._log("info", "定时运行触发：当前组")
            group = self._current_group_name()
        else:
            self._log("info", f"定时运行触发：{group}")
        entry = self._record_schedule(task, group, "running")
        self._active_schedule = entry
        self._schedule_pending = True
        started = self.run_group_for(group) if task.get("group") else self.run_group()
        if started is False:
            self._schedule_pending = False
            self._active_schedule = None
            entry["status"] = "skipped"
            entry["detail"] = "没有可运行的步骤或被校验拦下"
            self._mark_dirty()

    def _cancel_schedule_record(self, status: str = "skipped",
                                detail: str = "") -> None:
        """定时启动被取消 / 抢锁失败时，把记录收尾，别一直停在运行中。"""
        entry = self._active_schedule
        if entry is None:
            return
        self._active_schedule = None
        self._schedule_pending = False
        entry["status"] = status
        if detail:
            entry["detail"] = detail
        entry["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._mark_dirty()

    def _record_schedule(self, task: dict, group: str, status: str,
                         detail: str = "") -> dict:
        """追加一条定时执行记录（最新在前，最多保留 60 条）。"""
        entry = {
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "time": task.get("time", ""),
            "group": group or "当前组",
            "status": status,
            "detail": detail,
            "seconds": 0,
            "steps": 0,
            "failed": 0,
        }
        history = [item for item in
                   (self.settings.get("schedule_history") or [])
                   if isinstance(item, dict)]
        history.insert(0, entry)
        self.settings["schedule_history"] = history[:SCHEDULE_HISTORY_LIMIT]
        self._mark_dirty()
        return entry

    def _finish_schedule_history(self, result: str) -> None:
        """运行结束后，把正在跑的那条定时记录补上结果和耗时。"""
        entry = self._active_schedule
        if entry is None:
            return
        self._active_schedule = None
        self._schedule_pending = False
        entry["status"] = result if result in SCHEDULE_RESULT_TEXT else "error"
        entry["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            report = self.executor.report()
            entry["steps"] = int(report.get("steps", 0))
            entry["failed"] = int(report.get("failed", 0))
            entry["seconds"] = float(report.get("seconds", 0))
        except Exception:  # noqa: BLE001 — 历史记录失败不影响运行收尾
            pass
        self._mark_dirty()

    def _schedule_summary(self) -> str:
        """状态栏 / 菜单里显示的定时运行概况。"""
        enabled = [task for task in schedule_tasks(self.settings)
                   if task.get("enabled")]
        if not enabled:
            return "定时运行未启用"
        enabled.sort(key=lambda task: task.get("time", ""))
        first = enabled[0]
        group = first.get("group") or "当前组"
        if len(enabled) == 1:
            return f"定时 {first.get('time', '--:--')} → {group}"
        return (f"{len(enabled)} 个定时任务 · "
                f"最近 {first.get('time', '--:--')} → {group}")

    # --------------------------------------------------------------- 报告通知
    def _write_run_report(self, result: str) -> None:
        """把这次运行写成 markdown 报告，并刷新 reports/index.html。"""
        try:
            from rpa.engine import report_markdown, write_report_index

            os.makedirs(REPORTS_DIR, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            path = os.path.join(REPORTS_DIR, f"run_{stamp}.md")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(report_markdown(self.executor.report(), result,
                                         report_dir=REPORTS_DIR))
            if self._last_run_log_path:
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(f"\n运行日志：`{self._last_run_log_path}`\n")
            self._last_report_path = path
            write_report_index(REPORTS_DIR)
            self._log("info", f"运行报告已保存：{path}")
        except (OSError, ValueError) as exc:
            self._log("warn", f"运行报告保存失败：{exc}")

    def _write_result_file(self) -> None:
        """数据驱动跑完后，把每行结果写到数据文件旁边（可断点续跑）。"""
        if (not self._data_path or not self.executor.row_results
                or not self.settings.get("save_results", True)):
            return
        try:
            from rpa.engine import result_path, write_results

            path = result_path(self._data_path)
            write_results(self.executor.data, self.executor.row_results, path)
            self._last_result_path = path
            self._log("info", f"结果已写入：{path}")
        except (OSError, ValueError) as exc:
            self._log("warn", f"结果写入失败：{exc}")

    def open_run_history(self) -> None:
        """运行历史：查看 / 清空历次运行报告（替代原来的「打开报告」）。"""
        dialog = getattr(self, "_run_history_dialog", None)
        if dialog is not None and dialog.winfo_exists():
            dialog.lift()
            dialog.focus_force()
            return
        dialog = RunHistoryDialog(self, REPORTS_DIR)
        self._run_history_dialog = dialog
        dialog.wait()

    def _notify_finish(self, result: str) -> None:
        """运行结束通知：声音 + 托盘气泡 / 系统通知 + webhook。

        窗口藏在托盘时优先用气泡（点气泡能直接打开窗口）；其它情况
        （窗口可见、macOS 菜单栏、没有托盘）改发系统通知，避免用户
        切到别的应用后错过结果。
        """
        if self.settings.get("sound"):
            try:
                import winsound

                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:  # noqa: BLE001
                pass
        title = {"finished": "执行完成", "stopped": "已停止",
                 "error": "执行出错"}.get(result, "运行结束")
        report = None
        try:
            report = self.executor.report()
            text = (f"{report['steps']} 步，失败 {report['failed']}，"
                    f"耗时 {report['seconds']:g} 秒")
        except Exception:  # noqa: BLE001 — 报告失败不影响通知
            text = "点击打开 MeowGo 查看详情"
        balloon = bool(self.tray and not self._tray_window_visible)
        if balloon:
            self.tray.notify(title, text, on_click=self._tray_show_window)
        elif self.settings.get("system_notify", True):
            # Windows Toast 要启动 PowerShell，放后台线程避免卡住界面
            threading.Thread(
                target=lambda: notify.send(f"{APP_TITLE} · {title}", text),
                daemon=True).start()
        url = (self.settings.get("webhook") or "").strip()
        if not url or report is None:
            return
        text = (f"MeowGo 运行结束：{result}，"
                f"{report['steps']} 步，失败 {report['failed']}，"
                f"耗时 {report['seconds']:g} 秒")

        def post():
            try:
                _post_webhook(url, text)
                self._enqueue_log("info", "结束通知已发送")
            except Exception as exc:  # noqa: BLE001
                self._enqueue_log("warn", f"结束通知发送失败：{exc}")

        threading.Thread(target=post, daemon=True).start()

    def export_log(self) -> None:
        """把当前运行日志存成文本文件。"""
        path = filedialog.asksaveasfilename(
            parent=self, title="导出运行日志", defaultextension=".txt",
            initialfile=f"meowgo_log_{time.strftime('%Y%m%d_%H%M%S')}.txt",
            filetypes=[("文本", "*.txt"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(str(line)
                                   for line in self.log_list.get(0, "end")))
            self._set_status(f"日志已导出：{os.path.basename(path)}")
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)

    def _open_folder(self, path: str) -> None:
        """跨平台打开文件夹（资源管理器 / Finder / xdg-open）。"""
        try:
            platform_support.open_path(path)
        except (OSError, AttributeError) as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)

    def open_logs_dir(self) -> None:
        path = logs.log_dir()
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)
            return
        self._open_folder(path)

    def export_diagnostics(self) -> None:
        """导出环境、依赖、配置摘要和最近日志（敏感项已打码）。"""
        path = filedialog.asksaveasfilename(
            parent=self, title="导出诊断信息", defaultextension=".zip",
            initialfile=("meowgo_diagnostics_"
                         f"{time.strftime('%Y%m%d_%H%M%S')}.zip"),
            filetypes=[("ZIP 压缩包", "*.zip"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            from . import diagnostics

            diagnostics.write_zip(
                path, self.settings, app_dir=APP_DIR,
                image_dir=self.image_dir, report_dir=REPORTS_DIR,
                version=APP_VERSION)
        except Exception as exc:  # noqa: BLE001 - 导出失败要明确告知
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self._set_status(f"诊断信息已导出：{os.path.basename(path)}")
        if messagebox.askyesno(
                "诊断信息已导出",
                i18n.tr("已保存到：") + f"\n{path}\n\n"
                + i18n.tr("是否打开所在文件夹？"),
                parent=self):
            self._open_folder(os.path.dirname(path) or ".")

    def check_updates(self) -> None:
        """查询最新版本并提示下载页（不自动安装）。"""
        try:
            info = updater.fetch_latest(
                str(self.settings.get("update_url") or ""))
        except Exception as exc:  # noqa: BLE001 - 网络 / 接口异常统一提示
            messagebox.showwarning(
                "检查更新失败", f"无法获取版本信息：{exc}", parent=self)
            return
        latest = info["version"]
        if not updater.is_newer(APP_VERSION, latest):
            messagebox.showinfo(
                "已是最新版本", f"当前版本 {APP_VERSION}。", parent=self)
            return
        if messagebox.askyesno(
                "发现新版本",
                f"当前版本 {APP_VERSION}，最新版本 {latest}。\n\n"
                "是否打开下载页面？",
                parent=self):
            if info["url"]:
                try:
                    platform_support.open_path(info["url"])
                except OSError as exc:
                    messagebox.showerror("打开失败", str(exc), parent=self)

    def open_reports_dir(self) -> None:
        """打开运行报告所在目录。"""
        try:
            os.makedirs(REPORTS_DIR, exist_ok=True)
            platform_support.open_path(REPORTS_DIR)
        except (OSError, AttributeError) as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)

    def open_report_index(self) -> None:
        """打开/刷新报告索引页（reports/index.html）。"""
        try:
            from rpa.engine import write_report_index

            path = write_report_index(REPORTS_DIR)
            platform_support.open_path(path)
        except (OSError, AttributeError, ValueError) as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)

    def _check_dpi_setup(self) -> None:
        """多显示器时说明当前的 DPI 感知级别（per-monitor 下负坐标可直接用）。"""
        try:
            import ctypes

            screens = ctypes.windll.user32.GetSystemMetrics(80)  # SM_CMONITORS
        except Exception:  # noqa: BLE001
            return
        if screens <= 1:
            return
        awareness = ctypes.c_int(0)
        try:
            ctypes.windll.shcore.GetProcessDpiAwareness(
                None, ctypes.byref(awareness))
        except Exception:  # noqa: BLE001 - 老系统没有 shcore
            awareness.value = 0
        if awareness.value >= 2:
            self._log("info", "检测到多显示器：已启用 per-monitor DPI，"
                              "副屏坐标（含负数）可直接使用")
        else:
            self._log("warn", "检测到多显示器但未启用 per-monitor DPI，"
                              "副屏缩放与主屏不同时坐标可能偏移")

    def _show_onboarding(self) -> None:
        """首次启动的三步引导；勾选「不再显示」后写入设置。"""
        if self.settings.get("onboarded"):
            return
        dialog = OnboardingDialog(self)
        result = dialog.wait()
        if result and result.get("hide"):
            self.settings["onboarded"] = True
            self._save_state()

    def _check_platform_permissions(self, interactive: bool = False) -> bool:
        """macOS 权限自检：缺屏幕录制 / 辅助功能时引导打开系统设置。"""
        if not platform_support.IS_MACOS:
            if interactive:
                self._set_status("当前系统无需额外权限")
            return True
        missing = platform_support.missing_permissions()
        if not missing:
            if interactive:
                messagebox.showinfo(
                    "权限检查", "屏幕录制与辅助功能权限均已开启。", parent=self)
            return True
        if not interactive:
            # 启动时不要弹原生模态框：macOS 上它会让主窗口看起来「未响应」，
            # 只在日志和状态栏提示，用户可从「帮助 → 检查 macOS 权限…」主动打开。
            self._log("warn", "macOS 权限不完整：请在系统设置中开启"
                              "屏幕录制 / 辅助功能")
            self._set_status("macOS 权限不完整（帮助 → 检查 macOS 权限…）",
                             WARNING)
            return False
        text = platform_support.explain_permissions(missing)
        self._log("warn", "macOS 权限不完整：截图或鼠标操作可能失效")
        if messagebox.askyesno("需要 macOS 权限",
                               text + "\n\n现在打开系统设置？", parent=self):
            for kind in missing:
                platform_support.open_privacy_settings(kind)
        return False

    def toggle_pause(self) -> None:
        if not self.running:
            return
        self.paused = self.executor.toggle_pause()
        self.btn_pause.set_text("继续" if self.paused else "暂停",
                                "run" if self.paused else "pause")
        self._set_status("已暂停" if self.paused else "运行中…")
        self._set_log_state("已暂停" if self.paused else "运行中")

    def _mark_running_step(self, group_name: str, index: int, step: Step) -> None:
        """工作线程调用，只入队。"""
        self._current_run_step = (group_name, index)
        if self._run_log is not None:
            self._run_log.step_start(group_name, index, step.label(),
                                     (step.content or "").strip())
        self.ui_queue.put(("step", (group_name, index)))

    def _mark_step_done(self, group_name: str, index: int, step: Step,
                        ok: bool) -> None:
        """工作线程调用：单步结束，只入队。"""
        self.ui_queue.put(("step_done", (group_name, index, ok)))

    def _highlight_step(self, group_name: str, index: int) -> None:
        self._pending_step = None     # 已开始执行，等待高亮让位给运行高亮
        names = self.project.names()
        if group_name in names:
            position = names.index(group_name)
            if position != self.group_index:
                self.group_index = position
                self._refresh_groups()
                self._refresh_steps()
        self._repaint_step_marks()
        if self.step_tree.exists(str(index)):
            self.step_tree.item(
                str(index), tags=self._step_tags(index) + ("running",))
            self.step_tree.see(str(index))
        self._set_status(f"正在执行：{group_name} · 第 {index + 1} 步")
        if self._mini_bar is not None and group_name in names:
            group = self.project.groups[names.index(group_name)]
            if 0 <= index < len(group.steps):
                step = group.steps[index]
                summary = (step.content or "").strip().replace("\n", " ")
                if len(summary) > 120:      # 迷你条只做提示，超长内容给省略号
                    summary = summary[:120] + "…"
                self._mini_bar.set_step(
                    f"{index + 1}. {step.label()} {summary}".strip())

    def _apply_step_done(self, group_name: str, index: int, ok: bool) -> None:
        """主线程：把单步结果染成绿（成功）/ 红（失败），供结束后定位。"""
        if not ok and self._failed_step is None:
            self._failed_step = (group_name, index)
        self._step_marks.setdefault(group_name, {})[index] = \
            "done" if ok else "failed"
        self._repaint_step_marks()

    def _repaint_step_marks(self) -> None:
        """按 _step_marks 重染当前组的行（刷新列表后调用，标记不丢）。"""
        marks = self._step_marks.get(self._current_group_name(), {})
        pending = self._pending_step
        pending_index = (pending[1] if pending is not None
                         and pending[0] == self._current_group_name() else None)
        for item in self.step_tree.get_children():
            index = int(item)
            # 先回到斑马纹/禁用灰显，再按需叠运行染色（标记标签优先级更高）
            tags = list(self._step_tags(index))
            mark = marks.get(index)
            if mark:
                tags.append(mark)
            elif index == pending_index:
                tags.append("pending")
            self.step_tree.item(item, tags=tuple(tags))

    def _jump_to_step(self, group_name: str, index: int) -> None:
        """跳到指定组的某一步（日志点击 / 定位失败步用）。"""
        self._clear_step_filter()      # 过滤会藏起目标行，先清掉
        names = self.project.names()
        if group_name not in names or index < 0:
            return
        position = names.index(group_name)
        if position != self.group_index:
            self.group_index = position
            self._refresh_groups()
        self._refresh_steps(select=index)
        if self.step_tree.exists(str(index)):
            self.step_tree.selection_set(str(index))
            self.step_tree.see(str(index))
            self.step_tree.focus_set()
        self._set_status(f"已定位：{group_name} · 第 {index + 1} 步")

    def _locate_failed_step(self) -> None:
        if not self._failed_step:
            self._set_status("这次运行没有失败的步骤")
            return
        self._jump_to_step(*self._failed_step)

    # ------------------------------------------------------- 全局停止热键
    def _start_global_hotkey(self) -> None:
        if platform_support.IS_MACOS:
            self._start_macos_hotkeys()
            return
        if os.name != "nt":
            return
        # 主窗的 Win32 句柄：全局 F5 触发时判断主窗是否在前台——在前台就
        # 交给窗口内的 <F5> 绑定处理，避免一次按键跑两次
        try:
            self._win_hwnd = self.winfo_id()
        except tk.TclError:
            self._win_hwnd = None

        def loop():
            try:
                import ctypes
                from ctypes import wintypes

                user32 = ctypes.windll.user32
                user32.RegisterHotKey.argtypes = [
                    wintypes.HWND, ctypes.c_int, wintypes.UINT,
                    wintypes.UINT]
                user32.RegisterHotKey.restype = wintypes.BOOL
                user32.UnregisterHotKey.argtypes = [
                    wintypes.HWND, ctypes.c_int]
                user32.UnregisterHotKey.restype = wintypes.BOOL
                user32.GetMessageW.argtypes = [
                    ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                    wintypes.UINT, wintypes.UINT]
                user32.GetMessageW.restype = ctypes.c_int
                user32.GetForegroundWindow.argtypes = []
                user32.GetForegroundWindow.restype = wintypes.HWND
                ok_stop = user32.RegisterHotKey(None, 1, 0, 0x78)   # VK_F9
                ok_run = user32.RegisterHotKey(None, 2, 0, 0x74)    # VK_F5
                if not ok_stop:
                    self._log("warn", "全局停止热键 F9 注册失败（可能被占用）")
                if not ok_run:
                    self._log("warn", "全局运行热键 F5 注册失败（可能被占用）")
                if not ok_stop and not ok_run:
                    return
                msg = wintypes.MSG()
                while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    if msg.message == 0x0312:  # WM_HOTKEY
                        if msg.wParam == 1 and ok_stop:
                            self.ui_queue.put(("stop", None))
                        elif msg.wParam == 2 and ok_run:
                            fg = user32.GetForegroundWindow()
                            if fg and fg == self._win_hwnd:
                                continue     # 主窗在前台：窗口内 F5 已响应
                            self.ui_queue.put(("run", None))
                if ok_stop:
                    user32.UnregisterHotKey(None, 1)
                if ok_run:
                    user32.UnregisterHotKey(None, 2)
            except Exception:
                pass

        threading.Thread(target=loop, daemon=True).start()

    def _start_macos_hotkeys(self) -> None:
        """macOS：用 Quartz 事件监听实现全局 F5 运行 / F9 停止。"""
        from . import mac_native

        listener = mac_native.GlobalHotkeyListener(
            on_run=lambda: self.ui_queue.put(("run", None)),
            on_stop=lambda: self.ui_queue.put(("stop", None)),
            log=self._log)
        if listener.start():
            self._global_hotkeys = listener
            self._log("info", "全局热键已启用：F5 运行 / F9 停止")
        else:
            self._log("warn", "全局热键未启用")
            if listener.error:
                self._log("muted", listener.error)

    def _app_is_frontmost(self) -> bool:
        """主窗口是否在前台（全局 F5 与窗口内快捷键只响应一次）。"""
        if platform_support.IS_MACOS:
            from . import mac_native

            return mac_native.app_is_frontmost()
        if platform_support.IS_WINDOWS:
            return bool(self._win_hwnd
                        and _win_foreground_window() == self._win_hwnd)
        return False

    # --------------------------------------------------------- 设置 / 帮助
    def open_settings(self) -> None:
        dialog = SettingsDialog(self, self.settings, self.image_dir)
        if dialog.wait() is None:
            return
        old_scale = self.settings.get("ui_scale", 1.0)
        self.settings = dialog.result
        self.image_dir = image_dir_path(dialog.result["image_dir"])
        secrets.clear_marker(self.settings)
        if self.settings.get("ui_scale", 1.0) != old_scale:
            self._log("info", "界面缩放将在下次启动后生效")
        self.project.image_dir = self.image_dir
        language = str(self.settings.get("language", i18n.get_language()))
        if language != i18n.get_language():
            self.set_language(language, save=False)
        self._save_state()
        self._apply_tray_settings()
        self.image_label.configure(text=i18n.tr("图片目录：") + self.image_dir)

    def set_language(self, language: str, save: bool = True) -> None:
        """切换界面语言并即时刷新已存在的控件（不重启客户端）。"""
        language = i18n.set_language(language)
        self.settings["language"] = language
        i18n.refresh(self)
        self._refresh_groups()
        self._refresh_steps()
        if hasattr(self, "_status_source"):
            self.status_var.set(i18n.tr(self._status_source))
        if _is_search_placeholder(self.step_search.get()):
            self.step_search.delete(0, "end")
            self.step_search.insert(0, _search_placeholder())
            self.step_search.configure(foreground=MUTED)
        self.image_label.configure(text=f"图片目录：{self.image_dir}")
        self._log("info", (
            f"界面语言已切换为 {i18n.language_label(language)}"
            if language == "zh" else
            f"Interface language changed to {i18n.language_label(language)}"))
        if save:
            self._save_state()

    def open_schedule(self) -> None:
        dialog = ScheduleDialog(self, self.settings, self.project.names())
        if dialog.wait() is None:
            return
        self.settings.update(dialog.result)
        self._mark_dirty()
        self._save_state()
        self._log("info", f"定时任务已保存（{self._schedule_summary()}）")

    def open_schedule_popover(self) -> None:
        """顶栏时钟：浮窗显示即将执行的定时任务与倒计时。"""
        popover = getattr(self, "_schedule_popover", None)
        if popover is not None and popover.winfo_exists():
            popover.close()
            return
        self._schedule_popover = SchedulePopover(self).popup()

    def open_schedule_history(self) -> None:
        """近期定时执行情况。"""
        ScheduleHistoryDialog(self, self.settings).wait()

    def _image_count(self) -> int:
        try:
            return sum(1 for name in os.listdir(self.image_dir)
                       if name.lower().endswith(
                           (".png", ".jpg", ".jpeg", ".bmp")))
        except OSError:
            return 0

    def open_image_manager(self) -> None:
        dialog = ImageManagerDialog(self, self.project, self.image_dir)
        dialog.wait()

    def open_macro_recorder(self) -> None:
        bar = MacroBar(self)
        steps = bar.wait()
        if not steps:
            return
        self._push_undo("宏录制")
        group = self._current_group()
        if group is None:
            return
        group.steps.extend(steps)
        self._refresh_all(select_step=len(group.steps) - 1)
        self._log("info", f"宏录制完成：已插入 {len(steps)} 个坐标步骤"
                          "（可用拖拽调整顺序）")

    def show_shortcuts(self) -> None:
        """右下角「?」/ 帮助菜单：弹出快捷键速查卡。"""
        ShortcutsDialog(self).wait()

    def show_help(self) -> None:
        lines = [i18n.tr("【概述】"),
                 i18n.tr("左侧管理执行组，右侧编辑组内步骤，点击「运行组」即可执行。"),
                 i18n.tr("所有执行组均自动保存，无需手动维护项目文件。"),
                 i18n.tr("执行组可通过「调用执行组」指令被其他执行组复用。"),
                 "",
                 i18n.tr("【操作要点】"),
                 i18n.tr("· 执行组操作：在左侧执行组上右键，可新建、重命名、复制、删除或排序。"),
                 i18n.tr("· 运行指定执行组：在该执行组上右键，选择「运行这个组」。"),
                 i18n.tr("· 步骤列表：双击单元格可编辑内容、类型、重复次数与备注。"),
                 i18n.tr("· 右键菜单提供新增、编辑、复制、删除、截图及上下移动等完整操作。"),
                 i18n.tr("· 图像类步骤可点击「截图」框选屏幕区域，并自动保存至图像目录。"),
                 i18n.tr("· 复用编排：在组内新增「调用执行组」，内容填写目标执行组名称。"),
                 "",
                 i18n.tr("【指令类型】")]
        separators = ":" if i18n.get_language() == "en" else "："
        for index, (key, (name, hint, need_repeat, repeat_hint)) in enumerate(
                STEP_TYPES.items(), start=1):
            lines.append(f"{index}. {i18n.tr(name)}{separators} {i18n.tr(hint)}")
            if repeat_hint:
                lines.append(f"     {i18n.tr('重复次数：')}{i18n.tr(repeat_hint)}")
        lines += ["", i18n.tr("【快捷键】"),
                  i18n.tr("Ctrl+G 新建执行组　Ctrl+Enter 新增步骤　"
                          "Ctrl+E 启用/禁用步骤　F2 编辑步骤　Delete 删除步骤"),
                  i18n.tr("F5 运行当前组（全局）　F9 停止（全局，任何时候均有效）"),
                  "",
                  i18n.tr("【安全】将鼠标快速移至屏幕左上角，可立即中断执行。")]

        win = tk.Toplevel(self)
        win.title("使用说明")
        win.configure(bg=CARD)
        win.transient(self)
        win.after_idle(lambda: uikit.style_window(win, self.dark))
        text = tk.Text(win, width=64, height=27, wrap="word", relief="flat",
                       bg=CARD, fg=TEXT, font=FONT_NOTE,
                       padx=self.s(14), pady=self.s(12))
        text.pack(fill="both", expand=True)
        text.insert("1.0", "\n".join(lines))
        text.configure(state="disabled")
        uikit.RoundButton(win, "知道了", "check", win.destroy, kind="accent",
                          scale=self.scale, bg=CARD).pack(pady=(0, self.s(12)))
        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_height()) // 3
        self.place_window(win, x, y, win.winfo_width(), win.winfo_height())

    def show_about(self) -> None:
        message = (
            "MeowGo\n\n"
            "将原 Excel 配置迁移至客户端界面：可视化维护执行组与步骤，\n"
            "支持执行组间调用复用、屏幕截图、运行前校验、日志记录与全局停止热键。\n\n"
            "执行组自动保存于程序目录的 rpa_settings.json；\n"
            "支持 Excel 单组或全部工作表导入导出（兼容旧版 cmd.xls）。")
        message = (i18n.tr(message) + "\n\n"
                   + i18n.tr("版本：") + APP_VERSION)
        messagebox.showinfo("关于", message, parent=self)

    # --------------------------------------------------------------- 关闭
    def on_close(self) -> None:
        if self.running:
            if not messagebox.askyesno("确认退出", "任务仍在运行，确定退出？",
                                       parent=self):
                return
            self.executor.stop()
        if self._close_hides_to_tray():
            self._hide_to_tray()
            return
        self._quit()

    def _quit(self) -> None:
        """真正退出：停运行、存配置、收托盘、关窗口（托盘「退出」也走这里）。"""
        if self._quitting:
            return
        self._quitting = True
        if self.running:
            self.executor.stop()
        self.project.image_dir = self.image_dir
        self._save_state()
        self.destroy()

    def destroy(self) -> None:
        """关闭窗口：顺带停掉全局热键监听 / 托盘图标，避免测试反复建窗时残留线程。"""
        hotkeys = getattr(self, "_global_hotkeys", None)
        if hotkeys is not None:
            try:
                hotkeys.stop()
            except Exception:  # noqa: BLE001
                pass
            self._global_hotkeys = None
        icon = getattr(self, "tray", None)
        if icon is not None:
            try:
                icon.stop()
            except Exception:  # noqa: BLE001
                pass
            self.tray = None
        try:
            self._inline_recorder.stop()
        except Exception:  # noqa: BLE001
            pass
        super().destroy()

    # --------------------------------------------------------------- 托盘
    def _tray_wanted(self) -> bool:
        """两个托盘开关任一打开，就该有托盘图标。"""
        return bool(self.settings.get("tray_start_minimized")
                    or self.settings.get("tray_on_close"))

    def _init_tray(self) -> bool:
        """按当前设置创建托盘图标；已创建 / 不需要 / 失败都不重复建。"""
        if self.tray is not None:
            return True
        if not platform_support.available("system_tray"):
            return False
        if not self._tray_wanted():
            return False
        menu_items = [(i18n.tr("显示主窗口"), self._tray_show_window),
                      None,
                      (i18n.tr("运行当前组"), self.run_group),
                      (i18n.tr("停止运行"), self.stop_run),
                      None,
                      (i18n.tr("退出"), self._tray_quit)]
        if platform_support.IS_MACOS:
            from . import mac_native

            icon: Any = mac_native.MacTrayIcon(
                tip=APP_TITLE, on_activate=self._tray_show_window,
                menu_items=menu_items, invoke=lambda fn: self.after(0, fn))
        else:
            icon = tray.TrayIcon(
                tip=APP_TITLE, on_activate=self._tray_show_window,
                menu_items=menu_items, invoke=lambda fn: self.after(0, fn))
        if icon.start(dark=self.dark):
            self.tray = icon
            self._refresh_tray_menu()
            return True
        detail = getattr(icon, "error", "")
        self._log("warn", "托盘图标启动失败，窗口会保持可见")
        if detail:
            self._log("muted", detail)
        return False

    def _apply_tray_settings(self) -> None:
        """设置保存后按托盘开关增删图标（当前窗口可见性不变）。"""
        if self._tray_wanted():
            if self.tray is None and self._init_tray():
                self._log("info", "托盘图标已开启")
            return
        if self.tray is not None:
            if not self._tray_window_visible:
                self._tray_show_window()     # 托盘要没了，别让窗口继续藏着
            if self.tray.stop():
                self.tray = None
                self._log("info", "托盘图标已关闭")
            else:
                self._log("warn", "托盘图标退出超时，稍后保存设置时会再试一次")

    def _close_hides_to_tray(self) -> bool:
        return (self.tray is not None
                and bool(self.settings.get("tray_on_close"))
                and not self._session_ending)

    def _hide_to_tray(self, title: str = "MeowGo 还在运行",
                      text: str = "窗口已收起；双击托盘图标重新打开，"
                                  "右键菜单可退出。",
                      log: str = "窗口已最小化到托盘") -> None:
        self.withdraw()
        self._tray_window_visible = False
        if self.tray:
            self.tray.notify(title, text)
        self._log("info", log)

    def _maybe_start_hidden(self) -> None:
        """「启动后最小化到托盘」：登录自启时直接安静进后台。"""
        if self.tray and self.settings.get("tray_start_minimized"):
            self._hide_to_tray(
                title="MeowGo 已启动",
                text="窗口已按设置隐藏到托盘，双击图标打开界面。",
                log="已按设置启动并隐藏到托盘")

    def _tray_show_window(self) -> None:
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass
        if platform_support.IS_MACOS:
            # macOS 上 deiconify/lift 不一定能把整个 App 带到前台
            try:
                from AppKit import NSApplication

                NSApplication.sharedApplication().activateIgnoringOtherApps_(
                    True)
            except Exception:  # noqa: BLE001 - 没有 AppKit 时退回普通置顶
                pass
        self._tray_window_visible = True

    def _tray_quit(self) -> None:
        if self.running and not messagebox.askyesno(
                "确认退出", "任务仍在运行，确定退出？", parent=self):
            return
        self._quit()

    def _refresh_tray(self) -> None:
        if self.tray:
            self.tray.set_icon(self.dark)

    def _refresh_tray_menu(self) -> None:
        """运行状态变化时同步托盘菜单可用项与悬停提示。"""
        tray_icon = self.tray
        if tray_icon is None:
            return
        running = bool(getattr(self, "running", False))
        status = i18n.tr("运行中") if running else i18n.tr("空闲")
        try:
            tray_icon.set_menu_enabled({
                i18n.tr("运行当前组"): not running,
                i18n.tr("停止运行"): running,
            })
            tray_icon.set_tip(f"{APP_TITLE} · {status}")
        except Exception:  # noqa: BLE001 - 托盘状态更新失败不影响运行
            pass

    def _on_session_query(self) -> None:
        self._session_ending = True        # 可能要关机：先记住，别躲托盘

    def _on_session_end(self, ending: bool) -> None:
        self._session_ending = bool(ending)
        if ending:
            self.after(0, self._quit)      # 真关机：立刻退，别挡系统



class StepDialog(BaseDialog):
    """新增 / 编辑单个步骤。"""

    def __init__(self, parent: App, project: Project, step: Optional[Step] = None):
        super().__init__(parent, "编辑步骤" if step else "新增步骤",
                         width=470, height=400)
        self.project = project
        editing = step.copy() if step else Step("click", "", 1)
        self.columnconfigure(1, weight=1)

        self.label("指令类型", 0)
        self._type_labels = [info[0] for info in STEP_TYPES.values()]
        self._type_by_label = {info[0]: key
                               for key, info in STEP_TYPES.items()}
        self._type_by_label.update({
            i18n.tr(info[0]): key for key, info in STEP_TYPES.items()})
        self.var_type = tk.StringVar()
        # 分组 + 模糊搜索：基础指令与会调用执行组的指令分开展示。
        # 直接使用组件自带的圆角字段，避免“外层 RoundedCard + 内层 Entry”
        # 各画一圈边框；组件内部统一使用无边框的 Field.TEntry。
        self.combo = uikit.SearchableCombo(
            self, self._type_options(), variable=self.var_type,
            command=self._sync, scale=parent.scale, bg=CARD,
            font_size=FONT_SIZE_NOTE, width=18, shell=True)
        self.combo.grid(row=0, column=1, sticky="ew",
                        padx=(0, parent.s(16)), pady=parent.s(5))
        self.combo_field = self.combo._shell

        self.label("内容", 1).grid_configure(sticky="nw",
                                             pady=(parent.s(8), parent.s(2)))
        content = tk.Frame(self, bg=CARD)
        content.grid(row=1, column=1, sticky="ew", padx=(0, parent.s(16)),
                     pady=(parent.s(2), parent.s(4)))
        self.var_content = tk.StringVar(value=editing.content)
        self.entry = uikit.TextField(content, textvariable=self.var_content,
                                     scale=parent.scale, bg=CARD,
                                     font_size=FONT_SIZE_NOTE)
        self.entry.pack(fill="x")
        # 输入框与操作按钮之间的分隔线（有操作按钮时才显示）
        self.sep_entry = tk.Frame(content, bg=BORDER, height=1)
        # 操作按钮单独一行：图片步骤有三个按钮，条件步骤还要放组名，
        # 挤在同一行会把 470px 的对话框撑到 1000px 以上（已实测裁切）。
        self.action_row = tk.Frame(content, bg=CARD)
        self.btn_shot = uikit.RoundButton(
            self.action_row, "截图", "image",
            lambda: parent.capture_region(self._set_image), kind="outline",
            scale=parent.scale, bg=CARD, height=CONTROL_SM, pad=parent.s(8),
            font_size=FONT_SIZE_NOTE)
        self.btn_shot.pack(side="left", padx=(0, parent.s(4)))
        self.btn_pick = uikit.RoundButton(
            self.action_row, "选图", "folder_open", self._pick_image,
            kind="outline", scale=parent.scale, bg=CARD, height=CONTROL_SM,
            pad=parent.s(8), font_size=FONT_SIZE_NOTE)
        self.btn_pick.pack(side="left", padx=(0, parent.s(4)))
        # 预览：弹窗看内容框里当前写的图片（截图 / 选图完可立即核对）
        self.btn_preview = uikit.RoundButton(
            self.action_row, "预览", "eye", self._preview_image,
            kind="outline", scale=parent.scale, bg=CARD, height=CONTROL_SM,
            pad=parent.s(8), font_size=FONT_SIZE_NOTE,
            tooltip="查看内容框里当前写的图片")
        self.btn_preview.pack(side="left", padx=(0, parent.s(4)))
        # 按键录入：热键步骤可以直接按组合键，不用输入法打字
        self.btn_record = uikit.RoundButton(
            self.action_row, "", "keyboard", self._start_record,
            kind="outline", scale=parent.scale, bg=CARD, height=CONTROL_SM,
            pad=parent.s(8), icon_size=ICON_MD,
            tooltip="按键录入：直接按组合键，如 Ctrl+Shift+A")
        self.btn_record.pack(side="left", padx=(0, parent.s(4)))
        # 坐标拾取：倒计时几秒后取当前鼠标位置
        self.btn_grab_xy = uikit.RoundButton(
            self.action_row, "拾取", "mouse", self._grab_xy,
            kind="outline", scale=parent.scale, bg=CARD, height=CONTROL_SM,
            pad=parent.s(8), font_size=FONT_SIZE_NOTE,
            tooltip="把鼠标移到目标位置，点此倒计时拾取坐标")
        self.btn_grab_xy.pack(side="left", padx=(0, parent.s(4)))

        # 条件步骤的第二段：要执行的组名（写成 图片|组名 的图形化版）
        self.var_group = tk.StringVar()
        if (editing.type in PIPE_CONDITION_TYPES
                or editing.type in LOOP_TYPES) and "|" in editing.content:
            image, _, group = editing.content.partition("|")
            self.var_content.set(image.strip())
            self.var_group.set(group.strip())
        self.group_box = tk.Frame(content, bg=CARD)
        self.group_box.columnconfigure(1, weight=1)
        self.sep_if = tk.Frame(self.group_box, bg=BORDER, height=1)
        self.sep_if.grid(row=0, column=0, columnspan=3, sticky="ew",
                         pady=(parent.s(8), parent.s(4)))
        self.lbl_group = tk.Label(self.group_box, text="满足后执行", bg=CARD,
                                  fg=MUTED, font=FONT_CAPTION)
        self.lbl_group.grid(row=1, column=0, sticky="w",
                            padx=(0, parent.s(8)))
        self.group_entry = uikit.TextField(
            self.group_box, textvariable=self.var_group, scale=parent.scale,
            bg=CARD, font_size=FONT_SIZE_NOTE)
        self.group_entry.grid(row=1, column=1, sticky="ew")
        self.btn_pick_group = uikit.RoundButton(
            self.group_box, "选组", "list", self._pick_group, kind="outline",
            scale=parent.scale, bg=CARD, height=CONTROL_SM, pad=parent.s(8),
            font_size=FONT_SIZE_NOTE, tooltip="选择条件满足时要执行的执行组")
        self.btn_pick_group.grid(row=1, column=2, padx=(parent.s(4), 0))
        # if_else 的第三段：没出现图片时执行的组（第二行，仅该类型可见）
        self.var_group2 = tk.StringVar()
        if editing.type == "if_else":
            parts = (editing.content.split("|") + ["", ""])[:3]
            self.var_content.set(parts[0].strip())
            self.var_group.set(parts[1].strip())
            self.var_group2.set(parts[2].strip())
        self.label_group2 = tk.Label(self.group_box, text="没出现时执行",
                                     bg=CARD, fg=MUTED, font=FONT_CAPTION)
        self.label_group2.grid(row=2, column=0, sticky="w",
                               padx=(0, parent.s(8)), pady=(parent.s(4), 0))
        self.group2_entry = uikit.TextField(
            self.group_box, textvariable=self.var_group2, scale=parent.scale,
            bg=CARD, font_size=FONT_SIZE_NOTE)
        self.group2_entry.grid(row=2, column=1, sticky="ew",
                               pady=(parent.s(4), 0))
        self.btn_pick_group2 = uikit.RoundButton(
            self.group_box, "选组", "list", self._pick_group2, kind="outline",
            scale=parent.scale, bg=CARD, height=CONTROL_SM, pad=parent.s(8),
            font_size=FONT_SIZE_NOTE, tooltip="选择没出现图片时要执行的执行组")
        self.btn_pick_group2.grid(row=2, column=2, padx=(parent.s(4), 0),
                                  pady=(parent.s(4), 0))

        self.hint = tk.Label(self, text="", bg=CARD, fg=MUTED,
                             wraplength=parent.s(470) - parent.s(48),
                             justify="left", font=FONT_NOTE)
        self.hint.grid(row=2, column=0, columnspan=2, sticky="ew",
                       padx=parent.s(16), pady=(0, parent.s(4)))

        self.label("重复次数", 3)
        self.var_repeat = tk.StringVar(
            value="" if not editing.repeat else f"{editing.repeat:g}")
        self.spin = ttk.Spinbox(self, from_=-1, to=9999, width=8,
                                textvariable=self.var_repeat)
        self.spin.grid(row=3, column=1, sticky="w", padx=(0, parent.s(16)),
                       pady=parent.s(5))

        # 高级选项默认折叠：主界面只留类型、内容、重复次数
        self._advanced_open = False
        self.btn_advanced = uikit.RoundButton(
            self, "高级选项", "chevron_right", self._toggle_advanced,
            kind="ghost", scale=parent.scale, bg=CARD, height=CONTROL_SM,
            pad=parent.s(6), font_size=FONT_SIZE_NOTE,
            tooltip="识别范围、第几个、灰度、等待、重试、输入方式")
        self.btn_advanced.grid(row=4, column=0, columnspan=2, sticky="w",
                               padx=parent.s(10), pady=(parent.s(2), 0))

        self.advanced = tk.Frame(self, bg=CARD)
        self.advanced.grid(row=5, column=0, columnspan=2, sticky="ew",
                           padx=parent.s(16), pady=(0, parent.s(2)))
        self.advanced.columnconfigure(1, weight=1)
        self.advanced.grid_remove()

        # 等待（图片 / 拖拽 / 条件）
        self.lbl_timeout = self._adv_label("等待", 0)
        row_timeout = tk.Frame(self.advanced, bg=CARD)
        row_timeout.grid(row=0, column=1, sticky="w", pady=parent.s(3))
        self.var_timeout = tk.StringVar(
            value="" if not editing.timeout else f"{editing.timeout:g}")
        self.spin_timeout = ttk.Spinbox(row_timeout, from_=0, to=3600,
                                        width=8, textvariable=self.var_timeout)
        self.spin_timeout.grid(row=0, column=0, sticky="w")
        self.hint_timeout = tk.Label(
            row_timeout, text="秒（0 = 一直等）", bg=CARD, fg=MUTED,
            font=FONT_CAPTION)
        self.hint_timeout.grid(row=0, column=1, sticky="w",
                               padx=(parent.s(4), parent.s(14)))
        self.lbl_fail = tk.Label(row_timeout, text="超时后", bg=CARD, fg=TEXT,
                                 font=FONT_NOTE)
        self.lbl_fail.grid(row=0, column=2, sticky="w")
        if editing.on_fail == "stop":
            fail_text = "停止执行"
        elif editing.on_fail.startswith("goto:"):
            fail_text = "跳转到组…"
        else:
            fail_text = "跳过这步"
        self.var_on_fail = tk.StringVar(value=fail_text)
        self.combo_fail = uikit.LocalizedCombobox(
            row_timeout, variable=self.var_on_fail, state="readonly",
            values=["跳过这步", "停止执行", "跳转到组…"], width=11,
            font=FONT_NOTE, command=lambda _value=None: self._sync())
        self.combo_fail.grid(row=0, column=3, sticky="w",
                             padx=(parent.s(4), 0))

        # 识别范围 + 框选
        self.lbl_region = self._adv_label("识别范围", 1)
        row_region = tk.Frame(self.advanced, bg=CARD)
        row_region.grid(row=1, column=1, sticky="ew", pady=parent.s(3))
        row_region.columnconfigure(0, weight=1)
        self.var_region = tk.StringVar(value=editing.region)
        self.entry_region = uikit.TextField(
            row_region, textvariable=self.var_region, scale=parent.scale,
            bg=CARD, font_size=FONT_SIZE_NOTE)
        self.entry_region.grid(row=0, column=0, sticky="ew")
        self.btn_pick_region = uikit.RoundButton(
            row_region, "框选", "image", self._pick_region, kind="outline",
            scale=parent.scale, bg=CARD, height=CONTROL_SM, pad=parent.s(8),
            font_size=FONT_SIZE_NOTE, tooltip="在屏幕上拖拽选择识别区域")
        self.btn_pick_region.grid(row=0, column=1, padx=(parent.s(4), 0))
        self.hint_region = tk.Label(
            row_region, text="留空 = 全屏查找；格式 x,y,宽,高",
            bg=CARD, fg=MUTED, font=FONT_CAPTION)
        self.hint_region.grid(row=1, column=0, columnspan=2, sticky="w",
                              pady=(parent.s(2), 0))

        # 第几个 / 灰度 / 失败重试（同一行，省高度）
        self.lbl_match = self._adv_label("匹配 / 重试", 2)
        row_match = tk.Frame(self.advanced, bg=CARD)
        row_match.grid(row=2, column=1, sticky="w", pady=parent.s(3))
        self.var_nth = tk.StringVar(value=str(int(editing.nth or 0) + 1))
        self.lbl_nth = tk.Label(row_match, text="第", bg=CARD, fg=TEXT,
                                font=FONT_NOTE)
        self.lbl_nth.grid(row=0, column=0, sticky="w")
        self.spin_nth = ttk.Spinbox(row_match, from_=1, to=999, width=5,
                                    textvariable=self.var_nth)
        self.spin_nth.grid(row=0, column=1, sticky="w", padx=(parent.s(3), 0))
        self.lbl_nth_suffix = tk.Label(row_match, text="个", bg=CARD, fg=TEXT,
                                       font=FONT_NOTE)
        self.lbl_nth_suffix.grid(row=0, column=2, sticky="w",
                                 padx=(parent.s(3), parent.s(10)))
        self.var_gray = tk.BooleanVar(value=editing.grayscale)
        self.gray_box = tk.Frame(row_match, bg=CARD)
        self.gray_box.grid(row=0, column=3, sticky="w",
                           padx=(0, parent.s(10)))
        self.chk_gray = uikit.Switch(
            self.gray_box, self.var_gray, scale=parent.scale, bg=CARD,
            tooltip="灰度识别：颜色变化大时更稳")
        self.chk_gray.pack(side="left")
        self.lbl_gray = tk.Label(self.gray_box, text="灰度", bg=CARD, fg=TEXT,
                                 font=FONT_NOTE)
        self.lbl_gray.pack(side="left", padx=(parent.s(4), 0))
        # 失败重试装成一个小盒子：位置在 _layout_retry_row 里定，
        # 图片步骤一行挤不下时整盒换到下一行，避免「间隔」被右边缘切掉
        self.retry_box = tk.Frame(row_match, bg=CARD)
        self.lbl_retry = tk.Label(self.retry_box, text="失败重试", bg=CARD,
                                  fg=TEXT, font=FONT_NOTE)
        self.lbl_retry.pack(side="left")
        self.var_retry = tk.StringVar(value=str(editing.retry or 0))
        self.spin_retry = ttk.Spinbox(self.retry_box, from_=0, to=9, width=4,
                                      textvariable=self.var_retry)
        self.spin_retry.pack(side="left", padx=(parent.s(3), 0))
        self.lbl_retry_unit = tk.Label(self.retry_box, text="次，间隔", bg=CARD,
                                       fg=TEXT, font=FONT_NOTE)
        self.lbl_retry_unit.pack(side="left", padx=(parent.s(3), 0))
        self.var_retry_delay = tk.StringVar(
            value=f"{editing.retry_delay:g}")
        self.spin_retry_delay = ttk.Spinbox(
            self.retry_box, from_=0, to=60, increment=0.1, width=5,
            textvariable=self.var_retry_delay)
        self.spin_retry_delay.pack(side="left", padx=(parent.s(3), 0))
        self.lbl_retry_sec = tk.Label(self.retry_box, text="秒", bg=CARD,
                                      fg=TEXT, font=FONT_NOTE)
        self.lbl_retry_sec.pack(side="left", padx=(parent.s(2), 0))

        # 输入方式 / 命中检查（同一行，按类型切换）
        self.lbl_other = self._adv_label("其他", 3)
        row_other = tk.Frame(self.advanced, bg=CARD)
        row_other.grid(row=3, column=1, sticky="w", pady=parent.s(3))
        self.var_input_mode = tk.StringVar(
            value="逐字输入（英文/数字）" if editing.input_mode == "type"
            else "粘贴（快，支持中文）")
        self.combo_input = uikit.LocalizedCombobox(
            row_other, variable=self.var_input_mode, state="readonly",
            values=["粘贴（快，支持中文）", "逐字输入（英文/数字）"],
            width=20, font=FONT_NOTE)
        self.combo_input.grid(row=0, column=0, sticky="w")
        self.btn_check = uikit.RoundButton(
            row_other, "检测图片", "eye", self._check_image, kind="outline",
            scale=parent.scale, bg=CARD, height=CONTROL_SM, pad=parent.s(8),
            font_size=FONT_SIZE_NOTE, tooltip="隐藏窗口后检查当前屏幕能否找到图片")
        self.btn_check.grid(row=0, column=0, sticky="w")
        self.lbl_check_result = tk.Label(
            row_other, text="", bg=CARD, fg=MUTED,
            font=FONT_CAPTION)
        self.lbl_check_result.grid(row=0, column=1, sticky="w",
                                   padx=(parent.s(8), 0))

        # 失败跳转：失败后执行哪个组（on_fail = goto:组名）
        self.lbl_fail_group = self._adv_label("失败后执行", 4)
        self.row_fail_group = tk.Frame(self.advanced, bg=CARD)
        self.row_fail_group.grid(row=4, column=1, sticky="ew",
                                 pady=parent.s(3))
        self.row_fail_group.columnconfigure(0, weight=1)
        fail_target = (editing.on_fail[5:].strip()
                       if editing.on_fail.startswith("goto:") else "")
        self.var_fail_group = tk.StringVar(value=fail_target)
        self.entry_fail_group = uikit.TextField(
            self.row_fail_group, textvariable=self.var_fail_group,
            scale=parent.scale, bg=CARD, font_size=FONT_SIZE_NOTE)
        self.entry_fail_group.grid(row=0, column=0, sticky="ew")
        self.btn_fail_group = uikit.RoundButton(
            self.row_fail_group, "选组", "list", self._pick_fail_group,
            kind="outline", scale=parent.scale, bg=CARD,
            height=CONTROL_SM, pad=parent.s(8), font_size=FONT_SIZE_NOTE,
            tooltip="选择失败后要执行的执行组")
        self.btn_fail_group.grid(row=0, column=1, padx=(parent.s(4), 0))

        self._adv_timeout: list[tk.Widget] = [self.lbl_timeout, row_timeout]
        self._adv_fail: list[tk.Widget] = [self.lbl_fail, self.combo_fail]
        self._adv_fail_group: list[tk.Widget] = [self.lbl_fail_group,
                                                  self.row_fail_group]
        self._adv_image: list[tk.Widget] = [self.lbl_region, row_region]
        self._adv_nth: list[tk.Widget] = [self.lbl_nth, self.spin_nth,
                                          self.lbl_nth_suffix, self.gray_box]
        self._adv_match: list[tk.Widget] = [self.lbl_match, row_match]
        self._adv_other: list[tk.Widget] = [self.lbl_other, row_other]

        self.label("备注", 6)
        self.var_note = tk.StringVar(value=editing.note)
        self.note_field = uikit.TextField(
            self, textvariable=self.var_note, scale=parent.scale, bg=CARD,
            font_size=FONT_SIZE_NOTE)
        self.note_field.grid(row=6, column=1, sticky="ew",
                             padx=(0, parent.s(16)), pady=parent.s(5))
        # 兼容既有调用/测试：note_entry 指向真正接收键盘焦点的 Entry。
        self.note_entry = self.note_field.entry

        # 启用开关：禁用后该步骤仍保留在列表里，但运行 / 校验时都会跳过。
        self.var_enabled = tk.BooleanVar(value=bool(editing.enabled))
        self.enable_box = tk.Frame(self, bg=CARD)
        self.enable_box.grid(row=7, column=0, columnspan=2, sticky="w",
                             padx=parent.s(16), pady=(0, parent.s(2)))
        uikit.Switch(self.enable_box, self.var_enabled, scale=parent.scale,
                     bg=CARD).pack(side="left")
        self.enable_label = tk.Label(
            self.enable_box, text="启用此步骤（禁用后运行时跳过）",
            bg=CARD, fg=TEXT, font=FONT_NOTE)
        self.enable_label.pack(side="left", padx=(parent.s(6), 0))

        _, self.btn_ok, self.btn_cancel = self.footer(8)

        self._recorder = KeyRecorder(
            self,
            on_state=self._set_recording_ui,
            on_preview=lambda names: self.var_content.set("+".join(names)),
            on_done=self._record_done,
            on_cancel=self._record_cancelled,
            on_hint=lambda text: self._show_hint(text))
        self.var_type.set(editing.label())
        self.combo.set(editing.type)
        self._sync()
        self._autosize()
        self.entry.focus_set()
        self.center()

    # -- 高级选项 -----------------------------------------------------------
    def _adv_label(self, text: str, row: int) -> tk.Label:
        label = tk.Label(self.advanced, text=text, bg=CARD, fg=TEXT,
                         font=FONT_NOTE)
        label.grid(row=row, column=0, sticky="w", pady=self.app.s(3))
        return label

    def _layout_retry_row(self, second_line: bool) -> None:
        """「失败重试 N 次，间隔 S 秒」放哪一行。

        图片步骤同一行已经有「第几个 + 灰度」，再挤进去会被窗口右边缘
        切掉「间隔」和秒数输入框，所以换到下一行左对齐；其它类型一行
        放得下，就跟在后面。
        """
        row = 1 if second_line else 0
        info = self.retry_box.grid_info()
        if info and int(info.get("row", 0)) == row:
            return
        if second_line:
            self.retry_box.grid(row=1, column=0, columnspan=4, sticky="w",
                                pady=(self.app.s(4), 0))
        else:
            self.retry_box.grid(row=0, column=4, sticky="w", pady=0)

    def _autosize(self) -> None:
        """按内容收缩高度，去掉表单底部的大块空白。"""
        if not self.winfo_exists():
            return
        self.update_idletasks()
        width = max(self.app.s(470), self.winfo_width())
        self.geometry(f"{width}x{self.winfo_reqheight()}")

    def _toggle_advanced(self) -> None:
        self._advanced_open = not self._advanced_open
        if self._advanced_open:
            self.advanced.grid()
            self.btn_advanced.set_text("高级选项", "chevron_down")
        else:
            self.advanced.grid_remove()
            self.btn_advanced.set_text("高级选项", "chevron_right")
        self._autosize()

    def _pick_region(self) -> None:
        self.app.pick_region(self._set_region)

    def _set_region(self, x: int, y: int, width: int, height: int) -> None:
        self.var_region.set(f"{x},{y},{width},{height}")
        self._show_hint(f"识别范围：{x},{y},{width},{height}")
        self.lift()
        self.focus_force()

    def _check_image(self) -> None:
        """隐藏窗口后检查当前屏幕能否找到内容框里的图片（不点击）。"""
        key = self._type_key()
        if key not in IMAGE_STEP_TYPES:
            return
        content = self.var_content.get().strip().split("|", 1)[0].strip()
        if not content:
            self.lbl_check_result.configure(text="先填写图片名",
                                            fg=WARNING)
            return
        raw_region = self.var_region.get().strip()
        region = parse_region(raw_region)
        if raw_region and region is None:
            self.lbl_check_result.configure(
                text="识别范围格式不对，要写 x,y,宽,高", fg=WARNING)
            return
        grayscale = bool(self.var_gray.get())
        confidence = float(self.app.settings.get("confidence", 0.9) or 0.9)
        self.btn_check.set_enabled(False)
        self.lbl_check_result.configure(text="检查中…", fg=MUTED)
        # 对话框和主窗都可能挡住目标，先全部藏起来
        self.app.withdraw()
        self.withdraw()

        def run() -> None:
            if not self.winfo_exists():
                self.app.deiconify()
                return
            message = "检查失败"
            warn = True
            try:
                items = find_images(content, self.app.image_dir, confidence,
                                    region, grayscale)
                missing = [item["name"] for item in items
                           if item["missing"]]
                points = [point for item in items
                          for point in item["points"]]
                if missing and not points:
                    message = "找不到图片文件：" + "、".join(missing)
                elif not points:
                    message = "当前屏幕未找到匹配图像（请确认目标窗口已打开、图片未过期）"
                elif len(points) == 1:
                    message = "找到 1 处匹配 ✓"
                    warn = False
                else:
                    message = (f"找到 {len(points)} 处匹配，可能定位不准"
                               "（可使用「第几个」或框选识别范围）")
                try:
                    nth = int(self.var_nth.get() or 1)
                except ValueError:
                    nth = 1
                if points and nth > len(points):
                    message = f"仅找到 {len(points)} 处匹配，不存在第 {nth} 个"
                    warn = True
            except Exception as exc:      # noqa: BLE001
                message = f"检查失败：{exc}"
            self.app.deiconify()
            self.deiconify()
            self.lift()
            self.focus_force()
            try:
                self.grab_set()
            except tk.TclError:
                pass
            self.btn_check.set_enabled(True)
            self.lbl_check_result.configure(
                text=message, fg=WARNING if warn else SUCCESS)

        self.after(350, run)

    # -- 辅助 -------------------------------------------------------------
    @staticmethod
    def _type_options() -> list[tuple[str, str, str]]:
        """指令类型下拉项：(内部值, 显示名, 分组)。"""
        return [(key, info[0],
                 "执行组" if key in TYPE_GROUP_EXEC else "基础")
                for key, info in STEP_TYPES.items()]

    def _type_key(self) -> str:
        text = self.var_type.get().strip()
        if "·" in text:                # 兼容旧格式「3 · 左键单击」
            text = text.split("·", 1)[1].strip()
        if text in self._type_by_label:
            return self._type_by_label[text]
        if text in STEP_TYPES:
            return text
        # 输入框里是搜索词而不是完整类型名：仍按上次真正选中的类型走
        return self.combo.value or "click"

    def _sync(self) -> None:
        if self._recorder.active:
            return                     # 录制中不响应类型切换
        key = self._type_key()
        needs_image = key in IMAGE_STEP_TYPES
        _, hint, need_repeat, repeat_hint = STEP_TYPES[key]
        text = hint
        if repeat_hint:
            text += f"\n重复次数：{repeat_hint}"
        if key == "call":
            names = "、".join(self.project.names()) or "（还没有其它组）"
            text += f"\n可调用：{names}"
        if key == "hotkey":
            text += "\n也可点击右侧键盘按钮录入组合键（如 Ctrl+Shift+A）。"
        if key in PIPE_CONDITION_TYPES:
            if key == "if_var":
                text += ("\n左侧填写比较条件，右侧填写满足后执行的执行组。"
                         "\n运算符：== != > < >= <= contains notcontains，"
                         "如 计数>3。")
            else:
                text += ("\n左侧填写判断图像，右侧填写条件满足后执行的执行组"
                         "（可点击「选组」）。")
        if key == "if_else":
            text += "\n下方两个输入框分别填写图像出现与未出现时执行的执行组" \
                    "（均可点击「选组」）。"
        if key == "set":
            text += "\n设置后，任意步骤的内容均可通过 {变量名} 读取。"
        if key == "copy":
            text += "\n请先复制目标文字；此步骤会将其存入指定变量" \
                    "（发送一次 Ctrl+C，随后恢复剪贴板内容）。"
        if key == "drag":
            text += "\n截图、选图或拾取的结果将填入第一个空白段。"
        if key == "window":
            text += "\n窗口标题可只填写其中一部分，例如 activate:记事本。"
        if key == "file":
            text += ("\ncopy / move / write / append 的目标目录不存在会自动创建。"
                     "\nwrite 覆盖、append 追加（都自动补换行）；"
                     "read 把文件内容读进变量。"
                     "\nwait 不写秒数时使用「等待」值（留空按 10 秒）。")
        if key in ("click_xy", "move_xy"):
            text += "\n也可将鼠标移至目标位置，点击「拾取」自动填入坐标。"
        if key == "input":
            text += ("\n默认通过剪贴板粘贴（支持中文）。"
                     "\n若个别应用不支持粘贴，可在「高级选项」中改为逐字输入。")
        if key in TEXT_STEP_TYPES:
            text += ("\n左侧填写屏幕上要识别的文字；可夹 @x,y,宽,高 限定范围。"
                     "\n文字识别依赖 OCR 组件（中文需安装 chi_sim 语言包）。")
        if key in WAIT_IMAGE_TYPES:
            text += ("\n只等待、不点击；「最多等 N 秒」留空表示一直等。"
                     "\n超时按「失败后处理」执行（默认记为失败后继续）。")
        if key == "ocr_read":
            text += ("\n把指定区域里的文字整段读进变量（换行保留）。"
                     "\n用「识别范围」框住目标区域，识别更快也更准。")
        if key == "ui_click":
            text += ("\n左侧填写控件名称，可用「名称|控件类型」限定，"
                     "如 确定|Button。\n控件识别依赖 uiautomation 组件。")
        if key == "script":
            text += ("\n左侧按「环境:脚本|参数」填写，如 "
                     "python:tools/导出.py|--day|7。"
                     "\n多个参数用 | 分隔，末尾加 =>变量名 可保存标准输出。"
                     "\n可用环境：" + "、".join(script_runner.env_names())
                     + "\n自定义环境（可指定解释器 / 环境变量）："
                     + script_runner.custom_env_path())
        if key == "notify":
            text += ("\n左侧按「标题|正文」填写，正文可为空并支持 {变量}，"
                     "\n如 今日提交|{提交清单}。"
                     "\n发送到 Windows Toast / macOS 通知中心 / Linux notify-send。")
        if key == "http":
            text += ("\n要写成 方法 地址|变量名[|请求体]，如 "
                     "GET https://example.com|响应。\n响应内容会存入指定变量。")
        if key == "for_each":
            text += ("\n每轮把当前值写入 {loop}，轮次写入 {loop_index}；"
                     "\n可把「重复次数」当作轮次上限（留空 = 不限制）。")
        if key == "while_var":
            text += ("\n每轮执行前重新判断条件；变量由循环内的步骤修改。"
                     "\n「重复次数」是最大轮次（留空 = 1000）。")
        if key in ("break", "continue"):
            text += "\n只能放在循环调用的执行组里，直接放在循环外会报错。"
        if needs_image:
            text += ("\n截图或选图后，请点击「预览」核对内容。"
                     "\n若定位有误，可在「高级选项」中框选识别范围或指定第几个匹配项。")
        elif key in CONDITION_TYPES:
            text += "\n可在「高级选项」中设置最长等待时间。"
        self._show_hint(text)
        is_hotkey = key == "hotkey"
        is_else = key == "if_else"
        is_loop = key in LOOP_TYPES
        is_if = key in PIPE_CONDITION_TYPES or is_else or is_loop
        is_xy = key in ("click_xy", "move_xy", "drag")
        has_actions = needs_image or is_hotkey or is_xy
        # 操作按钮单独一行：用不到的整个收起，把宽度让给内容输入框
        self.btn_shot.set_enabled(needs_image)
        self.btn_pick.set_enabled(needs_image)
        self.btn_preview.set_enabled(needs_image)
        self.btn_record.set_enabled(is_hotkey)
        for visible, button in (
                (needs_image, self.btn_shot),
                (needs_image, self.btn_pick),
                (needs_image, self.btn_preview),
                (is_hotkey, self.btn_record),
                (is_xy, self.btn_grab_xy)):
            if visible:
                button.pack(side="left", padx=(0, self.app.s(4)))
            else:
                button.pack_forget()
        if has_actions:
            self.sep_entry.pack(fill="x", pady=(self.app.s(6), 0),
                                after=self.entry)
            self.action_row.pack(fill="x", pady=(self.app.s(4), 0),
                                 after=self.sep_entry)
        else:
            self.action_row.pack_forget()
            self.sep_entry.pack_forget()
        # 条件步骤：显示“组名”第二段；坐标/拖拽步骤：显示拾取按钮
        self.btn_grab_xy.set_enabled(is_xy)
        self.btn_pick_group.set_enabled(is_if)
        if is_if:
            if is_else:
                label = "出现时执行"
            elif is_loop:
                label = "每轮执行"
            else:
                label = "满足后执行"
            self.lbl_group.configure(text=label)
            anchor = self.action_row if has_actions else self.entry
            self.group_box.pack(fill="x", after=anchor)
        else:
            self.group_box.pack_forget()
        # if_else 的第三段（没出现时执行的组）单独一行
        widget: tk.Widget
        for widget in (self.label_group2, self.group2_entry,
                       self.btn_pick_group2):
            widget.grid() if is_else else widget.grid_remove()
        # 等待策略：图片 / 拖拽 / 条件 / 命令 / 文字 / 控件 / 文件等待
        has_timeout = key in TIMEOUT_STEP_TYPES
        for widget in self._adv_timeout:
            widget.grid() if has_timeout else widget.grid_remove()
        try:
            timeout = float(self.var_timeout.get() or 0)
        except ValueError:
            timeout = 0
        show_fail = key in FAIL_ACTION_STEP_TYPES and timeout > 0
        for widget in self._adv_fail:
            widget.grid() if show_fail else widget.grid_remove()
        show_goto = self.var_on_fail.get() == "跳转到组…" and show_fail
        for widget in self._adv_fail_group:
            widget.grid() if show_goto else widget.grid_remove()
        # 识别范围对图片 / 文字 / 控件都有效；「第几个」和灰度只对图片
        for widget in self._adv_image:
            widget.grid() if key in REGION_STEP_TYPES else widget.grid_remove()
        for widget in self._adv_nth:
            widget.grid() if needs_image else widget.grid_remove()
        self.lbl_match.configure(text="匹配 / 重试" if needs_image else "重试")
        for widget in self._adv_match:
            widget.grid()
        self._layout_retry_row(needs_image)
        # 输入方式 / 命中检查共用一行，按类型切换
        has_other = key == "input" or needs_image
        for widget in self._adv_other:
            widget.grid() if has_other else widget.grid_remove()
        if has_other:
            if key == "input":
                self.lbl_other.configure(text="输入方式")
                self.combo_input.grid()
                self.btn_check.grid_remove()
                self.lbl_check_result.grid_remove()
            else:
                self.lbl_other.configure(text="命中检查")
                self.combo_input.grid_remove()
                self.btn_check.grid()
                self.lbl_check_result.grid()
        self.spin.configure(state="normal" if need_repeat else "disabled")
        if not self.var_content.get().strip():
            defaults = {"time": "当前时间", "wait": "1", "scroll": "-200",
                        "hotkey": "enter"}
            if key in defaults:
                self.var_content.set(defaults[key])
        if need_repeat and not self.var_repeat.get().strip():
            self.var_repeat.set("1")
        if (platform_support.IS_MACOS
                and key in ("window", "if_window", "ui_click")):
            self._show_hint("该步骤类型只支持 Windows，macOS 上执行会报错",
                            warn=True)
        # 类型切换会增减行：窗口已显示时再按新内容收缩高度。
        # 构造阶段（尚未映射）不排队，避免用错误的初始宽度算高度。
        if self.winfo_ismapped():
            self.after_idle(self._autosize)

    # -- 按键录入 -----------------------------------------------------------
    def _show_hint(self, text: str, warn: bool = False) -> None:
        i18n.set_text_source(self.hint, text,
                             fg=WARNING if warn else MUTED)

    def _start_record(self) -> None:
        if self._recorder.active:
            return
        if not self._recorder.start(self.var_content.get()):
            self._show_hint("无法启动键盘录制（键盘钩子安装失败），请手动输入",
                            warn=True)

    def _set_recording_ui(self, active: bool) -> None:
        """录制期间禁用其它控件，避免误触；结束后恢复。"""
        self.combo.configure(state="disabled" if active else "readonly")
        self.entry.configure(state="disabled" if active else "normal")
        self.group_entry.configure(state="disabled" if active else "normal")
        self.group2_entry.configure(state="disabled" if active else "normal")
        self.note_entry.configure(state="disabled" if active else "normal")
        self.spin.configure(state="disabled" if active else "normal")
        self.spin_timeout.configure(state="disabled" if active else "normal")
        self.combo_fail.configure(state="disabled" if active else "readonly")
        for widget in (self.spin_nth, self.spin_retry,
                       self.spin_retry_delay, self.entry_region):
            widget.configure(state="disabled" if active else "normal")
        self.combo_input.configure(
            state="disabled" if active else "readonly")
        self.chk_gray.set_enabled(not active)
        for button in (self.btn_shot, self.btn_pick, self.btn_preview,
                       self.btn_record, self.btn_cancel, self.btn_ok,
                       self.btn_pick_group, self.btn_grab_xy,
                       self.btn_advanced, self.btn_pick_region,
                       self.btn_check):
            button.set_enabled(not active)

    def _record_done(self, text: str) -> None:
        self.var_content.set(text)
        self._show_hint(f"已录入：{text.replace(',', ' + ')}（可继续手动修改）")
        self.entry.focus_set()
        self.entry.select_range(0, "end")

    def _record_cancelled(self) -> None:
        self._show_hint("已取消按键录入")

    def _teardown(self) -> None:
        self._recorder.stop()

    def _set_image(self, name: str) -> None:
        if self._type_key() == "drag":
            self._fill_drag_part(name)
            return
        self.var_content.set(name)
        self._show_hint("已填入截图；可点「预览」核对，或到「高级选项」"
                        "点「检测图片」确认当前屏幕能命中")
        self.lift()
        self.focus_force()

    def _fill_drag_part(self, value: str) -> None:
        """拖拽的「起点|终点」：截图 / 选图 / 拾取填进第一个空着的一段。"""
        start, _, end = self.var_content.get().strip().partition("|")
        start, end = start.strip(), end.strip()
        if not start:
            start = value
        elif not end:
            end = value
        else:
            start = value          # 两段都满了：替换起点
        self.var_content.set(f"{start}|{end}")
        self._show_hint(f"拖拽：{start} → {end}" if end
                        else f"拖拽起点：{start}（还要填终点）")
        self.lift()
        self.focus_force()

    def _pick_image(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="选择图片", initialdir=self.app.image_dir,
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp")])
        if path:
            self._set_image(self.app._relative_image(path))

    def _preview_image(self) -> None:
        """点「预览」：弹窗查看内容框里当前写的图片。

        条件类型的第二段在组名框里、drag 是「起点|终点」两段，
        这里统一只取第一段（判断用图 / 起点图）。找不到文件时在提示
        区说明，不弹窗打断编辑。
        """
        name = self.var_content.get().strip().split("|", 1)[0].strip()
        if not name:
            self._show_hint("内容框还是空的，先截图或选一张图片")
            return
        path = assets.resolve_image(name, self.app.image_dir)
        if not os.path.isfile(path):
            self._show_hint(f"找不到图片文件：{name}", warn=True)
            return
        ImagePreviewDialog(self.app, path)

    def cancel(self) -> None:
        self._recorder.stop()
        super().cancel()

    def ok(self) -> None:
        if self._recorder.active:
            return                     # 录制中不提交（按钮本就禁用，双保险）
        key = self._type_key()
        if key in PIPE_CONDITION_TYPES or key in LOOP_TYPES:
            content = f"{self.var_content.get().strip()}|" \
                      f"{self.var_group.get().strip()}"
        elif key == "if_else":
            content = "|".join([self.var_content.get().strip(),
                                self.var_group.get().strip(),
                                self.var_group2.get().strip()])
        else:
            content = self.var_content.get()
        step = Step(type=key, content=content, repeat=0.0,
                    note=self.var_note.get(),
                    enabled=bool(self.var_enabled.get()))
        if STEP_TYPES[step.type][2]:
            try:
                step.repeat = float(self.var_repeat.get() or 0)
            except ValueError:
                messagebox.showwarning("格式错误", "重复次数必须是数字", parent=self)
                return
        has_timeout = key in TIMEOUT_STEP_TYPES
        if has_timeout:
            try:
                step.timeout = float(self.var_timeout.get() or 0)
            except ValueError:
                messagebox.showwarning("格式错误", "等待超时必须是数字",
                                       parent=self)
                return
        fail_value = self.var_on_fail.get()
        if fail_value == "停止执行":
            step.on_fail = "stop"
        elif fail_value == "跳转到组…":
            step.on_fail = "goto:" + self.var_fail_group.get().strip()
        else:
            step.on_fail = "skip"
        if key in IMAGE_STEP_TYPES:
            try:
                nth = int(self.var_nth.get() or 1)
                if nth < 1:
                    raise ValueError
            except ValueError:
                messagebox.showwarning("格式错误", "「第几个」要是 1 以上的整数",
                                       parent=self)
                return
            step.nth = nth - 1
            step.region = self.var_region.get().strip()
            step.grayscale = bool(self.var_gray.get())
        elif key in TEXT_STEP_TYPES:
            step.region = self.var_region.get().strip()
        try:
            step.retry = max(0, int(self.var_retry.get() or 0))
        except ValueError:
            messagebox.showwarning("格式错误", "重试次数必须是整数", parent=self)
            return
        try:
            step.retry_delay = max(0.0, float(self.var_retry_delay.get() or 0))
        except ValueError:
            messagebox.showwarning("格式错误", "重试间隔必须是数字", parent=self)
            return
        if key == "input":
            step.input_mode = ("type" if "逐字" in self.var_input_mode.get()
                               else "paste")
        problems = step.validate()
        if problems:
            messagebox.showwarning("配置有误", "\n".join(problems), parent=self)
            return
        self.result = step
        self.destroy()

    # -- 新类型辅助 -----------------------------------------------------------
    def _pick_group(self) -> None:
        """条件步骤：从现有执行组里选一个作为满足条件时执行的组。"""
        names = self.project.names()
        if not names:
            self._show_hint("还没有其它执行组，请先新建", warn=True)
            return
        menu = tk.Menu(self, tearoff=0, font=FONT_NOTE)
        for name in names:
            menu.add_command(
                label=name,
                command=self._group_picker(
                    self.var_group, "将执行组", name))
        x = self.btn_pick_group.winfo_rootx()
        y = self.btn_pick_group.winfo_rooty() + self.btn_pick_group.winfo_height()
        menu.post(x, y)

    def _choose_group(self, var: tk.StringVar, label: str, name: str) -> None:
        var.set(name)
        self._show_hint(f"{label}：{name}")

    def _group_picker(self, var: tk.StringVar, label: str,
                      name: str) -> Callable[[], None]:
        def choose() -> None:
            self._choose_group(var, label, name)
        return choose

    def _pick_fail_group(self) -> None:
        """失败跳转：选择失败后要执行的组。"""
        names = self.project.names()
        if not names:
            self._show_hint("还没有其它执行组，请先新建", warn=True)
            return
        menu = tk.Menu(self, tearoff=0, font=FONT_NOTE)
        for name in names:
            menu.add_command(
                label=name,
                command=self._group_picker(
                    self.var_fail_group, "失败后执行组", name))
        x = self.btn_fail_group.winfo_rootx()
        y = self.btn_fail_group.winfo_rooty() + self.btn_fail_group.winfo_height()
        menu.post(x, y)

    def _pick_group2(self) -> None:
        """if_else：选「没出现图片时」要执行的组。"""
        names = self.project.names()
        if not names:
            self._show_hint("还没有其它执行组，请先新建", warn=True)
            return
        menu = tk.Menu(self, tearoff=0, font=FONT_NOTE)
        for name in names:
            menu.add_command(
                label=name,
                command=self._group_picker(
                    self.var_group2, "没出现时将执行组", name))
        x = self.btn_pick_group2.winfo_rootx()
        y = self.btn_pick_group2.winfo_rooty() \
            + self.btn_pick_group2.winfo_height()
        menu.post(x, y)

    def _grab_xy(self) -> None:
        """坐标拾取：倒计时 3 秒，取倒计时结束时鼠标所在位置。"""
        self._show_hint("3 秒后拾取当前鼠标位置，请把鼠标移到目标点…")
        self.after(3000, self._do_grab_xy)

    def _do_grab_xy(self) -> None:
        try:
            import pyautogui

            pos = pyautogui.position()
            if self._type_key() == "drag":
                self._fill_drag_part(f"{pos.x},{pos.y}")
            else:
                self.var_content.set(f"{pos.x},{pos.y}")
            self._show_hint(f"已拾取坐标 ({pos.x}, {pos.y})")
        except Exception as exc:  # noqa: BLE001
            self._show_hint(f"拾取失败：{exc}", warn=True)


class ImagePreviewDialog(BaseDialog):
    """查看一张图片的真实内容：按窗口大小等比缩放，附文件名 / 原始尺寸。

    从步骤对话框的「预览」按钮打开，用来核对刚截的图 / 选好的图对不对。
    关闭时会把模态 grab 还给上一个持有者（通常是步骤对话框），不让它
    因为中间插了一个预览窗就丢掉模态语义。
    """

    # 图片显示区的上限（逻辑像素，DPI 缩放在构造时换算；约为主屏的七成，
    # 再大的图就等比缩小，窗口本身还允许手动拉大）
    MAX_W = 1200
    MAX_H = 760

    def __init__(self, parent: App, path: str):
        from PIL import Image, ImageTk

        with Image.open(path) as image:
            self._origin = image.size
        scale = max(parent.scale, 1.0)
        ratio = min(self.MAX_W * scale / self._origin[0],
                    self.MAX_H * scale / self._origin[1], 1.0)
        show = (max(1, int(self._origin[0] * ratio)),
                max(1, int(self._origin[1] * ratio)))
        # 窗口 = 图片 + 左右留白 + 标题/信息条 + 按钮行（逻辑像素）
        width = int(show[0] / scale) + 32
        height = int(show[1] / scale) + 92
        super().__init__(parent, f"预览 · {os.path.basename(path)}",
                         width=width, height=height)
        self.path = path
        self._prev_grab = self.app.grab_current()   # center() 之前：还是对话框持有
        # 允许手动拉伸：大图缩小显示后想凑近看细节，可以拉着窗口放大
        self.resizable(True, True)
        try:
            image = Image.open(path).convert("RGBA")
            image.thumbnail(show, Image.Resampling.LANCZOS)
            self._photo = ImageTk.PhotoImage(image)   # 保住引用，否则被 GC
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("预览失败", f"打不开图片：{exc}", parent=parent)
            self.destroy()
            return

        tk.Label(self, image=self._photo, bg=CARD).pack(
            padx=self.app.s(16), pady=(self.app.s(14), self.app.s(4)))
        tk.Label(self, text=os.path.basename(path), bg=CARD, fg=TEXT,
                 font=FONT_NOTE_BOLD).pack()
        meta = f"{self._origin[0]} × {self._origin[1]} 像素"
        if ratio < 1:
            meta += f" · 已缩放至 {ratio * 100:.0f}%"
        try:
            meta += f" · {os.path.getsize(path) / 1024:.0f} KB"
        except OSError:
            pass
        tk.Label(self, text=meta, bg=CARD, fg=MUTED,
                 font=FONT_CAPTION).pack(
            pady=(0, self.app.s(4)))
        buttons = tk.Frame(self, bg=CARD)
        buttons.pack(fill="x", padx=self.app.s(16),
                     pady=(0, self.app.s(12)))
        uikit.RoundButton(buttons, "在文件夹中显示", "folder_open",
                          self._open_folder, kind="ghost",
                          scale=self.app.scale, bg=CARD,
                          height=CONTROL_MD).pack(side="left")
        uikit.RoundButton(buttons, "关闭", "close", self.cancel,
                          kind="accent", scale=self.app.scale, bg=CARD,
                          height=CONTROL_MD).pack(side="right")
        # 图片按物理像素贴进来，用内容请求尺寸兜底，避免底部按钮被裁
        self.fit_content()
        self.center()

    def _open_folder(self) -> None:
        """在资源管理器里定位这张图片（Windows 带选中效果）。"""
        try:
            os.system(f'explorer /select,"{os.path.normpath(self.path)}"')
        except Exception:  # noqa: BLE001
            self.app.open_image_dir()

    def destroy(self) -> None:
        # 关闭时把 grab 还给步骤对话框：它是模态的，不能因为被中间插了
        # 一个预览窗就丢掉模态语义（表现为对话框按钮全点不动）
        grab = getattr(self, "_prev_grab", None)
        try:
            if grab is not None and grab.winfo_exists():
                grab.grab_set()
        except (tk.TclError, AttributeError):
            pass
        super().destroy()


# 定时频率：内部值 -> 界面文案（顺序即下拉顺序）
SCHEDULE_MODE_CHOICES = (
    ("daily", "每天"), ("weekly", "每周"),
    ("weekday", "工作日"), ("hourly", "每 N 小时"),
)
SCHEDULE_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


class ScheduleDialog(BaseDialog):
    """定时运行 + 结束通知。

    左侧是任务列表（新增 / 选择 / 删除 / 行内开关），右侧编辑当前任务。
    定时到点后自动运行设定的执行组（软件内定时只在界面开着时有效）。
    底部可以把当前任务交给 Windows 计划任务，实现关界面也能跑的无人值守。
    """

    def __init__(self, parent: App, settings: dict, group_names: list[str]):
        super().__init__(parent, "定时运行与通知", width=790, height=575)
        self._group_names = list(group_names)
        self._tasks = schedule_tasks(settings)
        # 老配置（没有 schedules 键）首次打开时给一条草稿，方便直接填时间；
        # 用户明确删光任务后（schedules=[]）则保持空列表。
        if not self._tasks and "schedules" not in settings:
            self._tasks = [make_schedule_task()]
        self._selected = 0 if self._tasks else -1
        self._loading = False
        self._task_rows: list[dict] = []

        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        current = self._tasks[self._selected] if self._tasks else {}
        hour, _, minute = str(current.get("time", "09:00")).partition(":")
        self.var_enabled = tk.BooleanVar(value=bool(current.get("enabled", True)))
        self.var_hour = tk.StringVar(value=hour or "09")
        self.var_minute = tk.StringVar(value=minute or "00")
        self.var_group = tk.StringVar(
            value=current.get("group") or "（当前组）")
        self.var_mode = tk.StringVar(
            value=i18n.tr(SCHEDULE_MODE_CHOICES[0][1]))
        self.var_weekday = tk.StringVar(value=i18n.tr(SCHEDULE_WEEKDAYS[0]))
        self.var_interval = tk.StringVar(
            value=str(current.get("interval", 1) or 1))
        self.var_sound = tk.BooleanVar(value=bool(settings.get("sound")))
        # 系统通知：运行结束后用操作系统通知中心提醒（默认开启）
        self.var_system_notify = tk.BooleanVar(
            value=bool(settings.get("system_notify", True)))
        self.var_webhook = tk.StringVar(value=settings.get("webhook", ""))

        body = tk.Frame(self, bg=CARD)
        body.grid(row=0, column=0, sticky="nsew",
                  padx=parent.s(16), pady=(parent.s(12), 0))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=0, minsize=parent.s(250))
        body.columnconfigure(1, weight=1)

        self._build_task_panel(body)
        self._build_editor(body)

        self._load_task()
        self._rebuild_task_list()
        self.footer(1, "保存", "check", self.ok, pad_top=10)
        self.fit_content(width=parent.s(790))
        self.center()

    # -- 左侧任务列表 -------------------------------------------------------
    def _build_task_panel(self, parent) -> None:
        app = self.app
        panel = tk.Frame(parent, bg=HEADING_BG, highlightthickness=1,
                         highlightbackground=BORDER, width=app.s(250))
        panel.grid(row=0, column=0, sticky="nsew", padx=(0, app.s(14)))
        panel.grid_propagate(False)
        panel.rowconfigure(1, weight=1)
        panel.columnconfigure(0, weight=1)

        head = tk.Frame(panel, bg=HEADING_BG)
        head.grid(row=0, column=0, sticky="ew",
                  padx=app.s(10), pady=(app.s(8), app.s(4)))
        tk.Label(head, text="定时任务", bg=HEADING_BG, fg=TEXT,
                 font=FONT_SECTION).pack(side="left")
        self.btn_add_task = uikit.RoundButton(
            head, "新增", "add", self._add_task, kind="outline",
            scale=app.scale, bg=HEADING_BG, height=CONTROL_XS,
            pad=app.s(7), font_size=FONT_SIZE_CAPTION,
            tooltip="新增一个定时任务")
        self.btn_add_task.pack(side="right")

        wrap = tk.Frame(panel, bg=HEADING_BG)
        wrap.grid(row=1, column=0, sticky="nsew", padx=app.s(4))
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.task_canvas = tk.Canvas(
            wrap, bg=HEADING_BG, highlightthickness=0, bd=0)
        self.task_canvas.grid(row=0, column=0, sticky="nsew")
        scroll = uikit.SlimScrollbar(
            wrap, orient="vertical", command=self.task_canvas.yview,
            scale=app.scale, bg=HEADING_BG)
        scroll.grid(row=0, column=1, sticky="ns", padx=(app.s(2), 0))
        self.task_canvas.configure(yscrollcommand=scroll.set)
        self.task_inner = tk.Frame(self.task_canvas, bg=HEADING_BG)
        self._task_window = self.task_canvas.create_window(
            (0, 0), window=self.task_inner, anchor="nw")
        self.task_inner.bind(
            "<Configure>",
            lambda e: self.task_canvas.configure(
                scrollregion=self.task_canvas.bbox("all")))
        self.task_canvas.bind(
            "<Configure>",
            lambda e: self.task_canvas.itemconfigure(
                self._task_window, width=e.width))

        foot = tk.Frame(panel, bg=HEADING_BG)
        foot.grid(row=2, column=0, sticky="ew",
                  padx=app.s(8), pady=(app.s(4), app.s(8)))
        self.btn_delete_task = uikit.RoundButton(
            foot, "删除", "trash", self._delete_task, kind="ghost",
            scale=app.scale, bg=HEADING_BG, height=CONTROL_XS,
            pad=app.s(6), font_size=FONT_SIZE_CAPTION)
        self.btn_delete_task.pack(side="left")

    def _rebuild_task_list(self) -> None:
        for child in self.task_inner.winfo_children():
            child.destroy()
        self._task_rows = []
        app = self.app
        if not self._tasks:
            tk.Label(self.task_inner, text="暂无定时任务\n请点击「新增」创建",
                     bg=HEADING_BG, fg=MUTED, font=FONT_CAPTION,
                     justify="left", anchor="w").pack(
                fill="x", padx=app.s(10), pady=app.s(12))
            return
        for index, task in enumerate(self._tasks):
            selected = index == self._selected
            bg = ACCENT_MIST if selected else CARD
            row = tk.Frame(self.task_inner, bg=bg, cursor="hand2",
                           highlightthickness=1,
                           highlightbackground=(ACCENT if selected else BORDER))
            row.pack(fill="x", padx=app.s(4), pady=app.s(3))
            row.columnconfigure(2, weight=1)
            variable = tk.BooleanVar(value=bool(task.get("enabled")))
            switch = uikit.Switch(row, variable, scale=app.scale, bg=bg,
                                  tooltip="快速启用或停用此任务")
            switch.grid(row=0, column=0, rowspan=2,
                        padx=(app.s(6), app.s(2)), pady=app.s(6))
            variable.trace_add(
                "write",
                lambda *_a, i=index, var=variable:
                self._on_row_switch(i, var))
            time_label = tk.Label(row, text=schedule_task_label(task),
                                  bg=bg, fg=TEXT if task.get("enabled")
                                  else MUTED, font=FONT_NOTE_BOLD,
                                  anchor="w", cursor="hand2")
            time_label.grid(row=0, column=1, columnspan=2, sticky="ew",
                            padx=(app.s(4), app.s(6)),
                            pady=(app.s(6), 0))
            group_label = tk.Label(
                row, text=task.get("group") or "（当前组）", bg=bg,
                fg=MUTED, font=FONT_CAPTION, anchor="w", cursor="hand2")
            group_label.grid(row=1, column=1, columnspan=2, sticky="ew",
                             padx=(app.s(4), app.s(6)),
                             pady=(0, app.s(6)))
            def bind_select(_event: object, i: int = index) -> None:
                self._select(i)

            for widget in (row, time_label, group_label):
                widget.bind("<Button-1>", bind_select)
            self._task_rows.append({"row": row, "switch": switch,
                                    "time": time_label,
                                    "group": group_label})

    def _refresh_task_row_styles(self) -> None:
        for index, parts in enumerate(self._task_rows):
            if index >= len(self._tasks):
                break
            task = self._tasks[index]
            selected = index == self._selected
            bg = ACCENT_MIST if selected else CARD
            parts["row"].configure(
                bg=bg, highlightbackground=(ACCENT if selected else BORDER))
            parts["time"].configure(
                bg=bg, fg=TEXT if task.get("enabled") else MUTED)
            parts["group"].configure(bg=bg)
            parts["switch"].set_bg(bg)

    def _select(self, index: int) -> None:
        if not (0 <= index < len(self._tasks)) or index == self._selected:
            return
        self._commit_current()
        self._selected = index
        self._load_task()
        # 重建一次让上一行的时间 / 执行组跟着编辑结果刷新
        self._rebuild_task_list()

    def _add_task(self) -> None:
        self._commit_current()
        task = make_schedule_task()
        self._tasks.append(task)
        self._selected = len(self._tasks) - 1
        self._load_task()
        self._rebuild_task_list()
        self.task_canvas.after_idle(
            lambda: self.task_canvas.yview_moveto(1.0))

    def _delete_task(self) -> None:
        if not (0 <= self._selected < len(self._tasks)):
            return
        task = self._tasks[self._selected]
        name = (f"{schedule_task_label(task)} · "
                f"{task.get('group') or i18n.tr('当前组')}")
        if not messagebox.askyesno("删除定时任务",
                                   f"确定删除定时任务「{name}」？",
                                   parent=self):
            return
        del self._tasks[self._selected]
        if self._selected >= len(self._tasks):
            self._selected = len(self._tasks) - 1
        self._load_task()
        self._rebuild_task_list()

    def _on_row_switch(self, index: int, variable: tk.BooleanVar) -> None:
        if self._loading or not (0 <= index < len(self._tasks)):
            return
        task = self._tasks[index]
        task["enabled"] = bool(variable.get())
        if index == self._selected:
            self._loading = True
            try:
                self.var_enabled.set(task["enabled"])
            finally:
                self._loading = False
        self._refresh_task_row_styles()

    # -- 右侧编辑区 ---------------------------------------------------------
    def _build_editor(self, parent) -> None:
        app = self.app
        right = tk.Frame(parent, bg=CARD)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(1, weight=1)
        self.right_panel = right

        self.editor_title = tk.Label(right, text="任务设置", bg=CARD, fg=TEXT,
                                     font=FONT_SECTION, anchor="w")
        self.editor_title.grid(row=0, column=0, columnspan=2, sticky="w",
                               pady=(app.s(2), app.s(6)))
        self.editor_hint = tk.Label(
            right, text="修改后请点击底部「保存」；左侧开关可快速启用或停用任务。",
            bg=CARD, fg=MUTED, font=FONT_CAPTION, anchor="w")
        self.editor_hint.grid(row=1, column=0, columnspan=2, sticky="w",
                              pady=(0, app.s(4)))

        def row_label(text: str, row: int) -> tk.Label:
            label = tk.Label(right, text=text, bg=CARD, fg=TEXT,
                             font=FONT_NOTE)
            label.grid(row=row, column=0, sticky="w",
                       padx=(0, app.s(10)), pady=app.s(5))
            return label

        row_label("运行频率", 2)
        self.row_time = tk.Frame(right, bg=CARD)
        self.row_time.grid(row=2, column=1, sticky="ew", pady=app.s(5))
        self.combo_mode = uikit.ReadonlySelect(
            self.row_time,
            values=[i18n.tr(label) for _mode, label in SCHEDULE_MODE_CHOICES],
            variable=self.var_mode, scale=app.scale, bg=CARD,
            font_size=FONT_SIZE_NOTE, width=9)
        self.combo_weekday = uikit.ReadonlySelect(
            self.row_time,
            values=[i18n.tr(name) for name in SCHEDULE_WEEKDAYS],
            variable=self.var_weekday, scale=app.scale, bg=CARD,
            font_size=FONT_SIZE_NOTE, width=5)
        self.spin_hour = ttk.Spinbox(
            self.row_time, from_=0, to=23, width=4, wrap=True,
            format="%02.0f", textvariable=self.var_hour)
        self.lbl_colon = tk.Label(self.row_time, text=":", bg=CARD, fg=TEXT,
                                  font=FONT_NOTE)
        self.spin_minute = ttk.Spinbox(
            self.row_time, from_=0, to=59, width=4, wrap=True,
            format="%02.0f", textvariable=self.var_minute)
        self.lbl_interval_prefix = tk.Label(
            self.row_time, text="起，每", bg=CARD, fg=MUTED,
            font=FONT_NOTE)
        self.spin_interval = ttk.Spinbox(
            self.row_time, from_=1, to=24, width=3,
            textvariable=self.var_interval)
        self.lbl_interval_unit = tk.Label(
            self.row_time, text="小时", bg=CARD, fg=MUTED, font=FONT_NOTE)
        self.var_mode.trace_add(
            "write", lambda *_a: self._on_schedule_mode_change())
        # 星期 / 间隔 / 时间改了立刻写回任务，左侧摘要同步
        for variable in (self.var_weekday, self.var_interval,
                         self.var_hour, self.var_minute):
            variable.trace_add(
                "write", lambda *_a: self._on_schedule_field_change())
        self._layout_schedule_row()

        row_label("目标执行组", 3)
        self.group_combo = uikit.ReadonlySelect(
            right, values=[i18n.tr("（当前组）")] + list(self._group_names),
            variable=self.var_group, scale=app.scale, bg=CARD,
            font_size=FONT_SIZE_NOTE, width=18)
        self.group_combo.grid(row=3, column=1, sticky="ew", pady=app.s(5))

        tk.Frame(right, bg=BORDER, height=1).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=app.s(10))
        row_label("完成通知", 5)
        sound_box = tk.Frame(right, bg=CARD)
        sound_box.grid(row=5, column=1, sticky="w", pady=app.s(3))
        uikit.Switch(sound_box, self.var_sound, scale=app.scale,
                     bg=CARD).pack(side="left")
        tk.Label(sound_box, text="播放提示音", bg=CARD, fg=TEXT,
                 font=FONT_NOTE).pack(side="left", padx=(app.s(6), 0))
        # 系统通知与提示音放在同一行，避免整个对话框的固定行号顺移
        uikit.Switch(
            sound_box, self.var_system_notify, scale=app.scale, bg=CARD,
            tooltip="运行结束后弹出系统通知（窗口隐藏或使用计划任务时也能收到）"
        ).pack(side="left", padx=(app.s(18), 0))
        tk.Label(sound_box, text="系统通知", bg=CARD, fg=TEXT,
                 font=FONT_NOTE).pack(side="left", padx=(app.s(6), 0))

        row_label("Webhook", 6)
        row_hook = tk.Frame(right, bg=CARD)
        row_hook.grid(row=6, column=1, sticky="ew", pady=app.s(5))
        row_hook.columnconfigure(0, weight=1)
        uikit.TextField(row_hook, textvariable=self.var_webhook,
                        scale=app.scale, bg=CARD,
                        font_size=FONT_SIZE_NOTE).grid(
            row=0, column=0, sticky="ew")
        self.btn_webhook_test = uikit.RoundButton(
            row_hook, "测试", "meow", self._test_webhook, kind="outline",
            scale=app.scale, bg=CARD, height=CONTROL_SM, pad=app.s(8),
            font_size=FONT_SIZE_NOTE,
            tooltip="向当前地址发送一条测试消息")
        self.btn_webhook_test.grid(row=0, column=1, padx=(app.s(6), 0))
        self.lbl_webhook_test = tk.Label(
            row_hook, text="", bg=CARD, fg=MUTED, font=FONT_CAPTION)
        self.lbl_webhook_test.grid(row=0, column=2, padx=(app.s(6), 0))
        tk.Label(right, text="填写企业微信或钉钉机器人地址，任务结束后自动推送；"
                             "留空则不推送。完成通知由所有定时任务共用。",
                 bg=CARD, fg=MUTED, font=FONT_CAPTION,
                 wraplength=app.s(445), justify="left", anchor="w").grid(
            row=7, column=0, columnspan=2, sticky="ew",
            pady=(0, app.s(2)))

        tk.Frame(right, bg=BORDER, height=1).grid(
            row=8, column=0, columnspan=2, sticky="ew", pady=app.s(9))
        row_label("无人值守运行", 9)
        if platform_support.IS_MACOS:
            task_hint = ("由 macOS launchd 执行，客户端关闭后仍会按时运行。"
                         "\n无需管理员权限；任务文件位于 "
                         "~/Library/LaunchAgents。")
        elif platform_support.available("scheduled_task"):
            task_hint = ("由 Windows 任务计划程序执行，客户端关闭后仍会按时运行。"
                         "\n无需管理员权限；创建后可在「任务计划程序」中查看。")
        else:
            task_hint = ("当前系统不支持系统计划任务。软件内定时运行在"
                         "界面打开时仍会生效，关掉界面则不会触发。")
        tk.Label(right, text=i18n.tr(task_hint),
                 bg=CARD, fg=MUTED, font=FONT_CAPTION,
                 wraplength=app.s(300), justify="left", anchor="w").grid(
            row=9, column=1, sticky="w", pady=(0, app.s(2)))
        row_task = tk.Frame(right, bg=CARD)
        row_task.grid(row=10, column=1, sticky="w", pady=(app.s(4), 0))
        self.btn_task_create = uikit.RoundButton(
            row_task, "创建计划任务", "calendar", self._create_task,
            kind="outline", scale=app.scale, bg=CARD, height=CONTROL_SM,
            pad=app.s(8), font_size=FONT_SIZE_NOTE,
            tooltip="用当前任务的时间和执行组创建每天运行的计划任务")
        self.btn_task_create.pack(side="left", padx=(0, app.s(4)))
        self.btn_task_delete = uikit.RoundButton(
            row_task, "删除计划任务", "trash", self._delete_task_plan,
            kind="ghost", scale=app.scale, bg=CARD, height=CONTROL_SM,
            pad=app.s(8), font_size=FONT_SIZE_NOTE)
        self.btn_task_delete.pack(side="left", padx=app.s(4))
        self.btn_task_status = uikit.RoundButton(
            row_task, "查询状态", "refresh", self._on_task_status_click,
            kind="ghost", scale=app.scale, bg=CARD, height=CONTROL_SM,
            pad=app.s(8), font_size=FONT_SIZE_NOTE,
            tooltip="查看计划任务的上次运行结果与下次运行时间")
        self.btn_task_status.pack(side="left", padx=app.s(4))
        if not platform_support.available("scheduled_task"):
            for button in (self.btn_task_create, self.btn_task_delete,
                           self.btn_task_status):
                button.set_enabled(False)
        self.lbl_task = tk.Label(right, text="", bg=CARD, fg=MUTED,
                                 font=FONT_CAPTION,
                                 wraplength=app.s(445), justify="left",
                                 anchor="w")
        self.lbl_task.grid(row=11, column=0, columnspan=2, sticky="ew",
                           pady=(app.s(4), 0))

    def _schedule_mode(self) -> str:
        """当前选中的频率内部值。"""
        label = self.var_mode.get()
        for mode, text in SCHEDULE_MODE_CHOICES:
            if label in i18n.variants(text):
                return mode
        return "daily"

    def _schedule_weekday(self) -> int:
        label = self.var_weekday.get()
        for index, name in enumerate(SCHEDULE_WEEKDAYS):
            if label in i18n.variants(name):
                return index
        return 0

    def _schedule_interval(self) -> int:
        try:
            return min(24, max(1, int(float(self.var_interval.get() or 1))))
        except (TypeError, ValueError):
            return 1

    def _layout_schedule_row(self) -> None:
        """按频率显示 / 隐藏「星期」和「每 N 小时」，其余控件保持顺序。"""
        mode = self._schedule_mode()
        for widget in (self.combo_mode, self.combo_weekday, self.spin_hour,
                       self.lbl_colon, self.spin_minute,
                       self.lbl_interval_prefix, self.spin_interval,
                       self.lbl_interval_unit):
            widget.pack_forget()
        self.combo_mode.pack(side="left")
        if mode == "weekly":
            self.combo_weekday.pack(side="left", padx=(self.app.s(6), 0))
        self.spin_hour.pack(side="left", padx=(self.app.s(6), 0))
        self.lbl_colon.pack(side="left", padx=self.app.s(2))
        self.spin_minute.pack(side="left")
        if mode == "hourly":
            self.lbl_interval_prefix.pack(side="left",
                                          padx=(self.app.s(8), 0))
            self.spin_interval.pack(side="left", padx=(self.app.s(3), 0))
            self.lbl_interval_unit.pack(side="left", padx=(self.app.s(3), 0))

    def _on_schedule_mode_change(self) -> None:
        if self._loading:
            return
        self._layout_schedule_row()
        self._commit_current()
        self._refresh_selected_label()

    def _on_schedule_field_change(self) -> None:
        if self._loading:
            return
        self._commit_current()
        self._refresh_selected_label()

    def _refresh_selected_label(self) -> None:
        """左侧列表的摘要跟着编辑结果一起变，不用等切走再切回来。"""
        if 0 <= self._selected < len(self._task_rows):
            task = self._tasks[self._selected]
            self._task_rows[self._selected]["time"].configure(
                text=schedule_task_label(task))

    def _load_task(self) -> None:
        task = (self._tasks[self._selected]
                if 0 <= self._selected < len(self._tasks) else None)
        self._loading = True
        try:
            if task is None:
                self.var_enabled.set(False)
                self.var_hour.set("09")
                self.var_minute.set("00")
                self.var_group.set(i18n.tr("（当前组）"))
            else:
                hour, _, minute = str(task.get("time", "09:00")).partition(":")
                self.var_enabled.set(bool(task.get("enabled")))
                self.var_hour.set(hour or "09")
                self.var_minute.set(minute or "00")
                self.var_group.set(task.get("group")
                                   or i18n.tr("（当前组）"))
                mode = str(task.get("mode") or "daily")
                mode_text = dict(SCHEDULE_MODE_CHOICES).get(mode, "每天")
                self.var_mode.set(i18n.tr(mode_text))
                weekday = int(task.get("weekday", 0) or 0) % 7
                self.var_weekday.set(i18n.tr(SCHEDULE_WEEKDAYS[weekday]))
                self.var_interval.set(str(task.get("interval", 1) or 1))
        finally:
            self._loading = False
        if task is None:
            self.editor_title.configure(text="暂无定时任务")
            self.editor_hint.configure(
                text="请点击左侧「新增」创建；行内开关可快速启用或停用。")
        else:
            self.editor_title.configure(
                text=f"第 {self._selected + 1} 个任务")
            self.editor_hint.configure(
                text="修改后请点击底部「保存」；左侧开关可快速启用或停用任务。")
        self._set_editor_enabled(task is not None)
        self._layout_schedule_row()

    def _set_editor_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        combo_state = "readonly" if enabled else "disabled"
        for spin in (self.spin_hour, self.spin_minute, self.spin_interval):
            spin.configure(state=state)
        for combo in (self.combo_mode, self.combo_weekday,
                      self.group_combo):
            combo.configure(state=combo_state)
        self.btn_task_create.set_enabled(enabled)
        self.btn_task_delete.set_enabled(enabled)
        self.btn_delete_task.set_enabled(enabled)

    # -- Webhook 测试 -------------------------------------------------------
    def _test_webhook(self) -> None:
        """用当前输入框里的地址发一条测试消息（不依赖保存）。"""
        url = self.var_webhook.get().strip()
        if not url:
            self.lbl_webhook_test.configure(text="请先填写地址", fg=WARNING)
            return
        if not url.lower().startswith(("http://", "https://")):
            self.lbl_webhook_test.configure(
                text="地址需以 http(s):// 开头", fg=WARNING)
            return
        self.btn_webhook_test.set_enabled(False)
        self.lbl_webhook_test.configure(text="发送中…", fg=MUTED)

        def run() -> None:
            error = ""
            try:
                _post_webhook(url, "MeowGo 测试消息：Webhook 配置成功。")
            except Exception as exc:  # noqa: BLE001 - 展示给用户即可
                error = str(exc)
            try:
                self.after(0, lambda: self._webhook_test_done(error))
            except (tk.TclError, RuntimeError):
                pass

        threading.Thread(target=run, daemon=True).start()

    def _webhook_test_done(self, error: str) -> None:
        if not self.winfo_exists():
            return
        self.btn_webhook_test.set_enabled(True)
        if error:
            self.lbl_webhook_test.configure(text="发送失败", fg=DANGER)
            messagebox.showerror("Webhook 测试失败", error, parent=self)
        else:
            self.lbl_webhook_test.configure(text="发送成功 ✓", fg=SUCCESS)

    def _commit_current(self) -> None:
        if self._loading or not (0 <= self._selected < len(self._tasks)):
            return

        def number(value, default):
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return default

        task = self._tasks[self._selected]
        hour = min(23, max(0, number(self.var_hour.get(), 9)))
        minute = min(59, max(0, number(self.var_minute.get(), 0)))
        group = self.var_group.get()
        task["enabled"] = bool(self.var_enabled.get())
        task["mode"] = self._schedule_mode()
        task["weekday"] = self._schedule_weekday()
        task["interval"] = self._schedule_interval()
        task["time"] = f"{hour:02d}:{minute:02d}"
        task["group"] = "" if group in i18n.variants("（当前组）") else group

    # -- 保存 ---------------------------------------------------------------
    def ok(self) -> None:
        self._commit_current()
        schedules = [{
            "id": str(task.get("id") or f"task-{index + 1}"),
            "enabled": bool(task.get("enabled")),
            "mode": str(task.get("mode") or "daily"),
            "time": _valid_schedule_time(task.get("time")),
            "weekday": int(task.get("weekday", 0) or 0),
            "interval": int(task.get("interval", 1) or 1),
            "group": task.get("group") or "",
        } for index, task in enumerate(self._tasks)]
        legacy = ({key: schedules[0][key]
                   for key in ("enabled", "time", "group")}
                  if schedules else {})
        self.result = {
            "schedules": schedules,
            "schedule": legacy,
            "sound": bool(self.var_sound.get()),
            "system_notify": bool(self.var_system_notify.get()),
            "webhook": self.var_webhook.get().strip(),
        }
        self.destroy()

    # -- Windows 计划任务（无人值守） ---------------------------------------
    def _task_time(self) -> tuple[int, int]:
        def number(value, default):
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return default

        return (min(23, max(0, number(self.var_hour.get(), 9))),
                min(59, max(0, number(self.var_minute.get(), 0))))

    def _task_schedule_args(self) -> list[str]:
        """schtasks 的频率参数：每天 / 每周几 / 工作日 / 每 N 小时。"""
        hour, minute = self._task_time()
        stamp = f"{hour:02d}:{minute:02d}"
        mode = self._schedule_mode()
        if mode == "hourly":
            return ["/SC", "HOURLY", "/MO", str(self._schedule_interval()),
                    "/ST", stamp]
        if mode == "weekly":
            day = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")[
                self._schedule_weekday()]
            return ["/SC", "WEEKLY", "/D", day, "/ST", stamp]
        if mode == "weekday":
            return ["/SC", "WEEKLY", "/D", "MON,TUE,WED,THU,FRI", "/ST", stamp]
        return ["/SC", "DAILY", "/ST", stamp]

    def _task_schedule_label(self) -> str:
        """当前编辑器里这条任务的摘要文字（用于成功提示）。"""
        hour, minute = self._task_time()
        return schedule_task_label({
            "mode": self._schedule_mode(), "time": f"{hour:02d}:{minute:02d}",
            "weekday": self._schedule_weekday(),
            "interval": self._schedule_interval()})

    def _task_group(self) -> str:
        group = self.var_group.get()
        if group == "（当前组）":
            group = self.app._current_group_name()
        return (group or "").strip()

    def _task_name(self, group: str) -> str:
        safe = re.sub(r'[\\/:*?"<>|\s]+', "_", group).strip("_")[:50]
        return f"MeowGo_{safe or '当前组'}"

    def _task_parts(self, group: str) -> tuple[str, str]:
        """计划任务的 (程序, 参数)，供 schtasks 与状态查询复用。"""
        if getattr(sys, "frozen", False):
            return sys.executable, f'--run "{group}"'
        python = sys.executable
        pythonw = os.path.join(os.path.dirname(python), "pythonw.exe")
        if os.path.exists(pythonw):
            python = pythonw
        script = os.path.join(APP_DIR, "start-app.py")
        return python, f'"{script}" --run "{group}"'

    def _task_command(self, group: str) -> str:
        """计划任务要执行的命令行（无窗口，跑完自动退出）。"""
        program, arguments = self._task_parts(group)
        return f'"{program}" {arguments}'

    def _task_arguments(self, group: str) -> list[str]:
        """macOS LaunchAgent 用的参数列表（不经过 shell，无需引号）。"""
        if getattr(sys, "frozen", False):
            return [sys.executable, "--run", group]
        return [sys.executable, os.path.join(APP_DIR, "start-app.py"),
                "--run", group]

    @staticmethod
    def _powershell(script: str, timeout: float = 20):
        """跑一段 PowerShell（用于读取任务状态 / 修改任务设置）。"""
        kwargs: dict[str, Any] = {
            "capture_output": True, "text": True, "errors": "replace",
            "timeout": timeout}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            **kwargs)

    @staticmethod
    def _ps_quote(value: str) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    def _query_task_info(self, name: str):
        """返回 {last, result, next}；任务不存在 / 查询失败返回 None。"""
        if platform_support.IS_MACOS:
            if not launchd.installed(name):
                return None
            hour, minute = self._task_time()
            return {"last": "", "result": 267011,
                    "next": launchd.next_run(hour, minute)}
        if os.name != "nt":
            return None
        quoted = self._ps_quote(name)
        script = (
            f"$i=Get-ScheduledTaskInfo -TaskName {quoted} -ErrorAction Stop;"
            "$l=if($i.LastRunTime){$i.LastRunTime.ToString("
            "'yyyy-MM-dd HH:mm:ss')}else{''};"
            "$n=if($i.NextRunTime){$i.NextRunTime.ToString("
            "'yyyy-MM-dd HH:mm:ss')}else{''};"
            '"$l|$($i.LastTaskResult)|$n"')
        try:
            proc = self._powershell(script)
        except (OSError, AttributeError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        parts = (proc.stdout or "").strip().split("|")
        if len(parts) != 3:
            return None
        try:
            result = int(parts[1])
        except ValueError:
            result = -1
        return {"last": parts[0], "result": result, "next": parts[2]}

    @staticmethod
    def _task_result_text(code: int) -> str:
        if code == 0:
            return "成功"
        if code in (267009, 0x41301):
            return "正在运行"
        if code in (267011, 0x41303):
            return "尚未运行过"
        return f"结果代码 {code}"

    def _format_task_info(self, info: dict) -> str:
        result = self._task_result_text(info.get("result", -1))
        if info.get("last"):
            text = (i18n.tr("上次运行：") + info["last"]
                    + "（" + i18n.tr(result) + "）")
        else:
            text = i18n.tr(result)
        if info.get("next"):
            text += i18n.tr(" · 下次：") + info["next"]
        return text

    def _on_task_status_click(self) -> None:
        self._refresh_task_status(show_error=True)

    def _refresh_task_status(self, show_error: bool = False) -> None:
        name = self._task_name(self._task_group())
        info = self._query_task_info(name)
        if info is None:
            if show_error:
                self.lbl_task.configure(
                    text="查询失败：尚未创建该计划任务，或任务计划程序不可用",
                    fg=WARNING)
            return
        self.lbl_task.configure(text=self._format_task_info(info), fg=SUCCESS)

    def _enable_catch_up(self, name: str) -> None:
        """开启「错过计划时间后尽快补跑」（StartWhenAvailable）。"""
        if os.name != "nt":
            return
        quoted = self._ps_quote(name)
        script = (
            f"$t=Get-ScheduledTask -TaskName {quoted} -ErrorAction Stop;"
            "$t.Settings.StartWhenAvailable=$true;"
            f"Set-ScheduledTask -TaskName {quoted} -Settings $t.Settings "
            "| Out-Null")
        try:
            self._powershell(script)
        except (OSError, AttributeError, subprocess.TimeoutExpired):
            pass                       # 补跑设置失败不影响任务创建

    def _schtasks(self, args: list[str], timeout: float = 20):
        kwargs: dict[str, Any] = {
            "capture_output": True, "text": True, "errors": "replace",
            "timeout": timeout}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.run(["schtasks"] + args, **kwargs)

    def _create_task(self) -> None:
        group = self._task_group()
        if not group:
            messagebox.showwarning("还没有执行组",
                                   "请先新建并保存一个执行组。", parent=self)
            return
        if group not in self.app.project.names():
            messagebox.showwarning(
                "执行组不存在",
                f"执行组「{group}」还没保存，请先保存项目。", parent=self)
            return
        hour, minute = self._task_time()
        name = self._task_name(group)
        if platform_support.IS_MACOS:
            self._create_launchd_task(name, group, hour, minute)
            return
        command = self._task_command(group)
        args = ["/Create", *self._task_schedule_args(),
                "/TN", name, "/TR", command, "/F"]
        user = os.environ.get("USERNAME", "").strip()
        if user:
            # /IT：仅在当前用户登录时交互运行，无需保存密码
            args += ["/RU", user, "/IT"]
        try:
            # 计划任务启动时读磁盘上的配置，先确保当前项目已保存
            self.app._save_state()
            proc = self._schtasks(args)
        except (OSError, AttributeError, subprocess.TimeoutExpired) as exc:
            messagebox.showerror("创建失败", f"调用 schtasks 失败：{exc}",
                                 parent=self)
            return
        if proc.returncode == 0:
            self._enable_catch_up(name)
            self.app._log("info", f"Windows 计划任务已创建：{name}")
            self._refresh_task_status()
            if not self.lbl_task.cget("text"):
                self.lbl_task.configure(
                    text=f"已创建：{name}（{self._task_schedule_label()}"
                         f" → {group}）", fg=SUCCESS)
        else:
            detail = (proc.stderr or proc.stdout or "").strip()[:500]
            self.lbl_task.configure(text="创建失败：" + detail,
                                    fg=DANGER)
            messagebox.showerror("创建失败", detail or "schtasks 返回错误",
                                 parent=self)

    def _create_launchd_task(self, name: str, group: str, hour: int,
                             minute: int) -> None:
        """macOS：写 LaunchAgent，到点由 launchd 启动无头运行。"""
        try:
            # 计划任务启动时读磁盘上的配置，先确保当前项目已保存
            self.app._save_state()
            mode = self._schedule_mode()
            weekdays = None
            interval = 0
            if mode == "weekly":
                # launchd：0 / 7 = 周日，1 = 周一
                weekdays = [(self._schedule_weekday() + 1) % 7]
            elif mode == "weekday":
                weekdays = [1, 2, 3, 4, 5]
            elif mode == "hourly":
                interval = self._schedule_interval()
            ok, error = launchd.install(
                name, self._task_arguments(group), hour, minute,
                weekdays=weekdays, interval_hours=interval)
        except (OSError, ValueError) as exc:
            ok, error = False, str(exc)
        if ok:
            self.app._log("info", f"macOS 定时任务已创建：{name}")
            self._refresh_task_status()
            if not self.lbl_task.cget("text"):
                self.lbl_task.configure(
                    text=f"已创建：{name}（{self._task_schedule_label()}"
                         f" → {group}）", fg=SUCCESS)
            self.app._set_status(f"已创建 macOS 定时任务：{name}")
            return
        self.lbl_task.configure(text="创建失败：" + error, fg=DANGER)
        messagebox.showerror("创建失败", error, parent=self)

    def _delete_task_plan(self) -> None:
        group = self._task_group()
        name = self._task_name(group)
        if not messagebox.askyesno(
                "删除计划任务",
                f"确定删除系统计划任务「{name}」？\n\n"
                "只删除系统计划任务，不影响软件里的定时设置。",
                parent=self):
            return
        if platform_support.IS_MACOS:
            ok, error = launchd.uninstall(name)
            if ok:
                self.lbl_task.configure(text=f"已删除：{name}", fg=SUCCESS)
                self.app._log("info", f"macOS 定时任务已删除：{name}")
            else:
                self.lbl_task.configure(text="删除失败：" + error, fg=DANGER)
                messagebox.showerror("删除失败", error, parent=self)
            return
        try:
            proc = self._schtasks(["/Delete", "/TN", name, "/F"])
        except (OSError, AttributeError, subprocess.TimeoutExpired) as exc:
            messagebox.showerror("删除失败", f"调用 schtasks 失败：{exc}",
                                 parent=self)
            return
        if proc.returncode == 0:
            self.lbl_task.configure(text=f"已删除：{name}", fg=SUCCESS)
            self.app._log("info", f"Windows 计划任务已删除：{name}")
        else:
            detail = (proc.stderr or proc.stdout or "").strip()[:500]
            self.lbl_task.configure(text="删除失败：" + detail, fg=DANGER)
            messagebox.showerror("删除失败", detail or "schtasks 返回错误",
                                 parent=self)


class SchedulePopover(tk.Toplevel):
    """顶栏时钟弹出的浮窗：即将执行的任务 + 实时倒计时 + 历史入口。"""

    def __init__(self, parent: App):
        super().__init__(parent)
        self.app = parent
        self.withdraw()
        self.overrideredirect(True)
        self._closed = False
        self._armed = False
        self._after_id: str | None = None
        self._poll_id: str | None = None
        self._rows: list[dict] = []
        self._width = parent.s(330)

        try:
            self.configure(bg=uikit.TRANSPARENT_KEY)
            self.wm_attributes("-transparentcolor", uikit.TRANSPARENT_KEY)
            outer_bg = uikit.TRANSPARENT_KEY
        except tk.TclError:
            outer_bg = BORDER
            self.configure(bg=outer_bg)

        outer = tk.Frame(self, bg=outer_bg)
        outer.pack(fill="both", expand=True)
        inner = tk.Frame(outer, bg=CARD)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        head = tk.Frame(inner, bg=CARD)
        head.pack(fill="x", padx=parent.s(14), pady=(parent.s(12), parent.s(6)))
        tk.Label(head, image=parent.pic("clock", 16, ACCENT_STRONG),
                 bg=CARD).pack(side="left")
        tk.Label(head, text="即将执行的定时任务", bg=CARD, fg=TEXT,
                 font=FONT_NOTE_BOLD).pack(side="left", padx=(parent.s(7), 0))

        self.rows_frame = tk.Frame(inner, bg=CARD)
        self.rows_frame.pack(fill="x", padx=parent.s(10), pady=(0, parent.s(4)))
        self._rebuild_rows()

        tk.Frame(inner, bg=BORDER, height=1).pack(
            fill="x", padx=parent.s(12), pady=(parent.s(4), 0))
        foot = tk.Frame(inner, bg=CARD)
        foot.pack(fill="x", padx=parent.s(12), pady=(parent.s(8), parent.s(10)))
        self.btn_manage = uikit.RoundButton(
            foot, "管理", "calendar", self._manage, kind="ghost",
            scale=parent.scale, bg=CARD, height=CONTROL_SM,
            pad=parent.s(8), font_size=FONT_SIZE_NOTE,
            tooltip="打开定时运行与通知")
        self.btn_manage.pack(side="left")
        self.btn_history = uikit.RoundButton(
            foot, "历史", "list", self._history, kind="outline",
            scale=parent.scale, bg=CARD, height=CONTROL_SM,
            pad=parent.s(9), font_size=FONT_SIZE_NOTE,
            tooltip="查看近期定时执行情况")
        self.btn_history.pack(side="right")
        self.footer_hint = tk.Label(
            inner, text="仅保留最近 60 条记录；客户端运行时将按时触发。",
            bg=CARD, fg=MUTED, font=FONT_CAPTION, anchor="w")
        self.footer_hint.pack(fill="x", padx=parent.s(14),
                              pady=(0, parent.s(10)))

        self.bind("<Escape>", lambda e: self.close())
        self.bind("<FocusOut>", lambda e: self.close())
        self._tick()

    # -- 内容 ---------------------------------------------------------------
    def _rebuild_rows(self) -> None:
        for child in self.rows_frame.winfo_children():
            child.destroy()
        self._rows = []
        now = datetime.now()
        tasks = [task for task in schedule_tasks(self.app.settings)
                 if task.get("enabled")]
        tasks.sort(key=lambda task: schedule_next_run(task, now))
        if not tasks:
            tk.Label(self.rows_frame, text="还没有启用的定时任务",
                     bg=CARD, fg=MUTED, font=FONT_NOTE, anchor="w").pack(
                fill="x", padx=self.app.s(4), pady=self.app.s(10))
            return
        # 浮窗里最多列 6 条，避免任务很多时把窗口撑出屏幕
        for task in tasks[:6]:
            target = schedule_next_run(task, now)
            row = tk.Frame(self.rows_frame, bg=CARD)
            row.pack(fill="x", pady=self.app.s(3))
            left = tk.Frame(row, bg=CARD)
            left.pack(side="left", fill="x", expand=True)
            time_text = task.get("time", "--:--")
            day_label = tk.Label(left, text=f"{schedule_day_text(target, now)} "
                                            f"{time_text}",
                                 bg=CARD, fg=TEXT, font=FONT_NOTE_BOLD,
                                 anchor="w")
            day_label.pack(anchor="w")
            tk.Label(left, text=task.get("group") or "（当前组）", bg=CARD,
                     fg=MUTED, font=FONT_CAPTION, anchor="w").pack(anchor="w")
            countdown = tk.Label(row, text="", bg=CARD, fg=ACCENT_STRONG,
                                 font=FONT_NOTE_BOLD, anchor="e")
            countdown.pack(side="right", padx=(self.app.s(8), 0))
            self._rows.append({"target": target, "countdown": countdown,
                               "day": day_label, "time": time_text})
        if len(tasks) > 6:
            tk.Label(self.rows_frame,
                     text=f"还有 {len(tasks) - 6} 个任务…", bg=CARD,
                     fg=MUTED, font=FONT_CAPTION, anchor="w").pack(
                fill="x", padx=self.app.s(4), pady=(self.app.s(2), 0))

    def _tick(self) -> None:
        if self._closed:
            return
        now = datetime.now()
        if any(row["target"] <= now for row in self._rows):
            self._rebuild_rows()
            now = datetime.now()
        for row in self._rows:
            row["day"].configure(
                text=f"{schedule_day_text(row['target'], now)} "
                     f"{row['time']}")
            row["countdown"].configure(
                text=schedule_countdown_text(row["target"], now))
        try:
            self._after_id = self.after(1000, self._tick)
        except tk.TclError:
            self._after_id = None

    # -- 交互 ---------------------------------------------------------------
    def popup(self) -> "SchedulePopover":
        self.update_idletasks()
        width = max(self._width, self.winfo_reqwidth())
        height = self.winfo_reqheight()
        button = getattr(self.app, "btn_schedule", None)
        if button is not None and button.winfo_exists():
            x = button.winfo_rootx() + button.winfo_width() - width
            y = button.winfo_rooty() + button.winfo_height() \
                + self.app.s(6)
        else:
            x = self.app.winfo_rootx() + self.app.winfo_width() - width
            y = self.app.winfo_rooty() + self.app.s(80)
        rect = uikit.monitor_work_area(x, y)
        if rect is not None:
            left, top, right, bottom = rect
            margin = self.app.s(6)
            x = min(max(x, left), max(left, right - width - margin))
            y = min(max(y, top), max(top, bottom - height - margin))
        self.geometry(f"{int(width)}x{int(height)}+{int(x)}+{int(y)}")
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

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for attr in ("_after_id", "_poll_id"):
            identifier = getattr(self, attr, None)
            if identifier is not None:
                try:
                    self.after_cancel(identifier)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        try:
            self.grab_release()
        except tk.TclError:
            pass
        if getattr(self.app, "_schedule_popover", None) is self:
            self.app._schedule_popover = None
        try:
            self.destroy()
        except tk.TclError:
            pass

    def _inside(self) -> bool:
        try:
            x, y = self.winfo_pointerxy()
            left, top = self.winfo_rootx(), self.winfo_rooty()
            return (left <= x < left + self.winfo_width()
                    and top <= y < top + self.winfo_height())
        except tk.TclError:
            return True

    def _watch(self) -> None:
        """点浮窗外的任意位置就收起（和右键菜单同一套鼠标轮询）。"""
        if self._closed:
            return
        try:
            get = ctypes.windll.user32.GetAsyncKeyState
            get.restype = ctypes.c_short
            left, right = int(get(0x01)), int(get(0x02))
            edge = bool(left & 0x0001) or bool(right & 0x0001)
            down = bool(left & 0x8000) or bool(right & 0x8000)
            if self._armed:
                if (edge or down) and not self._inside():
                    self.close()
                    return
            elif not edge and not down:
                self._armed = True
            self._poll_id = self.after(50, self._watch)
        except (tk.TclError, AttributeError):
            self._poll_id = None

    def _history(self) -> None:
        self.close()
        self.app.after(0, self.app.open_schedule_history)

    def _manage(self) -> None:
        self.close()
        self.app.after(0, self.app.open_schedule)


class ScheduleHistoryDialog(BaseDialog):
    """近期定时执行情况：触发时间、任务、结果与耗时。"""

    def __init__(self, parent: App, settings: dict):
        super().__init__(parent, "定时执行历史", width=680, height=470)
        self._history = [item for item in
                         (settings.get("schedule_history") or [])
                         if isinstance(item, dict)]
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        columns = ("at", "task", "result", "stats")
        self.tree = ttk.Treeview(self, columns=columns, show="headings",
                                 selectmode="browse")
        self.tree.heading("at", text="触发时间")
        self.tree.heading("task", text="任务")
        self.tree.heading("result", text="结果")
        self.tree.heading("stats", text="执行情况")
        self.tree.column("at", width=155)
        self.tree.column("task", width=190)
        self.tree.column("result", width=80)
        self.tree.column("stats", width=205)
        self.tree.grid(row=0, column=0, sticky="nsew",
                       padx=(parent.s(12), 0), pady=(parent.s(12), 0))
        scroll = uikit.SlimScrollbar(
            self, orient="vertical", command=self.tree.yview,
            scale=parent.scale, bg=CARD)
        scroll.grid(row=0, column=1, sticky="ns", pady=(parent.s(12), 0),
                    padx=(parent.s(2), 0))
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.tag_configure("ok", foreground=SUCCESS)
        self.tree.tag_configure("danger", foreground=DANGER)
        self.tree.tag_configure("muted", foreground=MUTED)
        self.tree.tag_configure("running", foreground=ACCENT_STRONG)

        for entry in self._history:
            status = entry.get("status", "")
            result = SCHEDULE_RESULT_TEXT.get(status, status or "未知")
            seconds = float(entry.get("seconds") or 0)
            steps = int(entry.get("steps") or 0)
            failed = int(entry.get("failed") or 0)
            if status == "skipped":
                stats = entry.get("detail") or "已跳过"
            elif status == "running":
                stats = "正在执行…"
            else:
                stats = f"{steps} 步 · 失败 {failed} · {seconds:g} 秒"
            tag = {"finished": "ok", "running": "running",
                   "error": "danger", "stopped": "muted",
                   "skipped": "muted"}.get(status, "muted")
            self.tree.insert(
                "", "end", tags=(tag,), values=(
                    entry.get("at", ""),
                    f"{entry.get('time', '--:--')} · "
                    f"{entry.get('group') or '当前组'}",
                    result, stats))

        latest = self._history[0].get("at", "") if self._history else ""
        summary = (f"近 {len(self._history)} 次"
                   + (f" · 最近 {latest}" if latest else "")
                   if self._history else "还没有定时执行记录")
        self.summary = tk.Label(self, text=summary, bg=CARD, fg=MUTED,
                                font=FONT_CAPTION, anchor="w")
        self.summary.grid(row=1, column=0, columnspan=2, sticky="ew",
                          padx=parent.s(14), pady=(parent.s(4), 0))

        self.footer(2, "关闭", "close", self.cancel,
                    cancel_text="", pad_top=8, pad_bottom=12)
        self.center()


class MacroBar(BaseDialog):
    """宏录制控制条：开始 / 停止并生成步骤 / 取消。

    录下来的是一串坐标步骤（点击、移动、滚轮），停止后插入当前组。
    注意：录的是**绝对坐标**，与「图片识别」步骤互补——适合界面元素
    固定不变的场景（游戏、全屏应用、桌面图标）。
    """

    def __init__(self, parent: App):
        super().__init__(parent, "宏录制", width=380, height=190)
        self.result: Optional[list[Step]] = None
        self.columnconfigure(0, weight=1)

        self.label("点「开始录制」后正常操作鼠标；\n"
                   "录的是绝对坐标，适合界面固定的场景。\n"
                   "停止时请用本窗口的按钮。", 0, muted=True)
        self.status = tk.Label(self, text="未开始", bg=CARD, fg=MUTED,
                               font=FONT_NOTE)
        self.status.grid(row=1, column=0, pady=parent.s(8))

        buttons = tk.Frame(self, bg=CARD)
        buttons.grid(row=2, column=0, pady=parent.s(6))
        self.btn_start = uikit.RoundButton(
            buttons, "开始录制", "run", self._toggle, kind="accent",
            scale=parent.scale, bg=CARD)
        self.btn_start.pack(side="left", padx=parent.s(4))
        self.btn_cancel = uikit.RoundButton(
            buttons, "取消", "close", self._cancel, kind="ghost",
            scale=parent.scale, bg=CARD)
        self.btn_cancel.pack(side="left", padx=parent.s(4))

        self._recorder = MacroRecorder(
            self,
            on_state=self._on_state,
            on_done=self._on_done,
            on_cancel=self._on_cancelled,
            on_hint=lambda text: self.status.configure(text=text))
        self.center()

    # -- 控制 ---------------------------------------------------------------
    def _self_rect(self) -> tuple[int, int, int, int]:
        self.update_idletasks()
        left, top = self.winfo_rootx(), self.winfo_rooty()
        return (left, top, left + self.winfo_width(),
                top + self.winfo_height())

    def _toggle(self) -> None:
        if self._recorder.active:
            self._recorder.stop()          # 结果走 on_done
        elif not self._recorder.start(ignore_rect=self._self_rect()):
            self.status.configure(text="无法启动鼠标录制（鼠标钩子安装失败）")

    def _cancel(self) -> None:
        self._recorder.cancel()
        self.result = None
        self.destroy()

    # -- 录制器回调 -----------------------------------------------------------
    def _on_state(self, active: bool) -> None:
        self.btn_start.set_text("停止并生成步骤" if active else "开始录制",
                                "stop" if active else "run")
        self.btn_start.kind = "danger" if active else "accent"
        self.btn_cancel.set_enabled(not active)

    def _on_done(self, steps: list[Step]) -> None:
        self.result = steps
        self.status.configure(text=f"已生成 {len(steps)} 个步骤")
        self.destroy()

    def _on_cancelled(self) -> None:
        if self.winfo_exists():
            self.status.configure(text="已取消 / 没有录到操作")

    def cancel(self) -> None:
        self._recorder.cancel()
        super().cancel()


class RunMiniBar:
    """运行中的迷你置顶条：进度 + 当前步骤，点一下回到主窗口。

    主窗口运行时会最小化，这条 always-on-top 的小黑条让用户随时看到
    进度；Esc 或单击都能把主窗口唤回。
    """

    def __init__(self, parent: App):
        self.parent = parent
        self.win = tk.Toplevel(parent)
        self.win.withdraw()            # 量好文字宽度再显示，避免闪一下
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=LOG_BG)
        s = parent.s

        box = tk.Frame(self.win, bg=LOG_BG)
        box.pack(fill="both", expand=True, padx=s(12), pady=s(7))
        self.counts = tk.Label(box, text="准备开始", bg=LOG_BG,
                               fg=ACCENT,
                               font=FONT_NOTE_BOLD)
        self.counts.pack(anchor="w")
        self.text = tk.Label(box, text="", bg=LOG_BG, fg=LOG_FG,
                             font=FONT_NOTE, justify="left")
        self.text.pack(anchor="w")
        self.bar = ttk.Progressbar(box, style="Mini.Horizontal.TProgressbar",
                                   mode="determinate", maximum=100)
        self.bar.pack(fill="x", pady=(s(4), 0))
        for widget in (self.win, box, self.counts, self.text, self.bar):
            widget.bind("<Button-1>", self._restore)
        self.win.bind("<Escape>", self._restore)
        self._last_text = ""
        self._fit()
        self.win.deiconify()

    def _fit(self) -> None:
        """按文字实际宽度收放小黑条：长步骤说明不再被硬裁掉。"""
        s = self.parent.s
        screen_w = self.win.winfo_screenwidth()
        max_width = max(s(320), int(screen_w * 0.6))
        self.text.configure(wraplength=0)
        self.win.update_idletasks()
        width = min(max(s(320), self.win.winfo_reqwidth()), max_width)
        if self.win.winfo_reqwidth() > max_width:
            # 超长内容在最大宽度内折行，窗口高度跟着长，内容仍能看全
            self.text.configure(wraplength=max(s(160), width - s(24)))
            self.win.update_idletasks()
        width = min(max(s(320), self.win.winfo_reqwidth()), max_width)
        height = max(s(58), self.win.winfo_reqheight())
        x = (screen_w - width) // 2
        self.win.geometry(f"{width}x{height}+{x}+{s(8)}")
        self.bar.configure(length=max(s(80), width - s(24)))

    def _restore(self, _event=None) -> None:
        try:
            self.parent.deiconify()
            self.parent.lift()
        except tk.TclError:
            pass

    def set_counts(self, done: int, total: int) -> None:
        if total >= 0:
            if str(self.bar.cget("mode")) != "determinate":
                self.bar.stop()
                self.bar.configure(mode="determinate", maximum=max(total, 1))
            self.bar["value"] = min(done, total)
            self.counts.configure(text=f"{done}/{total} 步")
        else:
            if str(self.bar.cget("mode")) != "indeterminate":
                self.bar.configure(mode="indeterminate")
                self.bar.start(8)
            self.counts.configure(text=f"已执行 {done} 步")

    def set_step(self, text: str) -> None:
        if text == self._last_text:
            return
        self._last_text = text
        self.text.configure(text=text)
        self._fit()

    def destroy(self) -> None:
        try:
            self.bar.stop()
        except Exception:  # noqa: BLE001 — 收尾不许再抛
            pass
        try:
            self.win.destroy()
        except tk.TclError:
            pass


class ImageManagerDialog(BaseDialog):
    """图片管家：一张表看清 images/ 里的图片都被谁引用。

    - 未被引用的图片可以一键清理（删前会确认）
    - 重命名图片时，所有引用它的步骤会自动跟着改
    """

    def __init__(self, parent: App, project: Project, image_dir: str):
        super().__init__(parent, "图片管家", width=580, height=430)
        self.project = project
        self.image_dir = image_dir
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        columns = ("name", "refs", "size")
        self.tree = ttk.Treeview(self, columns=columns, show="headings",
                                 selectmode="browse")
        self.tree.heading("name", text="图片文件")
        self.tree.heading("refs", text="被谁引用")
        self.tree.heading("size", text="大小")
        self.tree.column("name", width=200)
        self.tree.column("refs", width=280)
        self.tree.column("size", width=70, anchor="e")
        self.tree.grid(row=0, column=0, sticky="nsew", padx=parent.s(12),
                       pady=(parent.s(12), parent.s(6)))
        scroll = uikit.SlimScrollbar(
            self, orient="vertical", command=self.tree.yview,
            scale=parent.scale, bg=CARD)
        scroll.grid(row=0, column=1, sticky="ns", pady=(parent.s(12), 0),
                    padx=(parent.s(2), 0))
        self.tree.configure(yscrollcommand=scroll.set)

        self.summary = tk.Label(self, text="", bg=CARD, fg=MUTED,
                                font=FONT_CAPTION, justify="left", anchor="w",
                                wraplength=parent.s(540))
        self.summary.grid(row=1, column=0, columnspan=2, sticky="ew",
                          padx=parent.s(14), pady=(parent.s(2), 0))

        buttons = tk.Frame(self, bg=CARD)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew",
                     padx=parent.s(16), pady=(parent.s(8), parent.s(14)))
        uikit.RoundButton(buttons, "关闭", "close", self.cancel,
                          kind="ghost", scale=parent.scale, bg=CARD,
                          height=CONTROL_MD).pack(side="right")
        uikit.RoundButton(buttons, "重命名并同步引用", "pencil",
                          self._rename, kind="outline", scale=parent.scale,
                          bg=CARD, height=CONTROL_MD).pack(side="left")
        uikit.RoundButton(buttons, "清理未引用图片", "trash",
                          self._clean_unused, kind="danger",
                          scale=parent.scale, bg=CARD, height=CONTROL_MD).pack(
            side="left", padx=(0, parent.s(8)))
        uikit.RoundButton(buttons, "打开目录", "folder_open",
                          self._open_dir, kind="ghost", scale=parent.scale,
                          bg=CARD, height=CONTROL_MD).pack(
            side="left", padx=(0, parent.s(8)))
        self._reload()
        self.center()

    # -- 数据 ---------------------------------------------------------------
    def _references(self) -> dict[str, list[str]]:
        """{图片文件名: [组名·第N步, ...]}"""
        refs: dict[str, list[str]] = {}
        for group in self.project.groups:
            for index, step in enumerate(group.steps, start=1):
                names = []
                if step.type in IMAGE_STEP_TYPES:
                    names.append(step.content.split("|")[0].strip())
                for name in names:
                    if name:
                        refs.setdefault(os.path.basename(name), []).append(
                            f"{group.name} · 第{index}步")
        return refs

    def _reload(self) -> None:
        self.tree.delete(*self.tree.get_children())
        refs = self._references()
        unused = 0
        try:
            entries = sorted(os.listdir(self.image_dir))
        except OSError:
            entries = []
        for name in entries:
            path = os.path.join(self.image_dir, name)
            if not os.path.isfile(path):
                continue
            if not name.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
                continue
            users = refs.get(name, [])
            if not users:
                unused += 1
            size = os.path.getsize(path) / 1024
            self.tree.insert(
                "", "end", iid=name,
                values=(name,
                        "、".join(users) if users else "（未引用）",
                        f"{size:.0f} KB"))
        total = len(self.tree.get_children())
        self.summary.configure(
            text=f"共 {total} 张图片，{unused} 张未被引用"
                 + (f"；图片目录：{self.image_dir}" if total else
                    f"（目录为空或不存在：{self.image_dir}）"))

    def _selected(self) -> Optional[str]:
        selection = self.tree.selection()
        return selection[0] if selection else None

    # -- 操作 ---------------------------------------------------------------
    def _open_dir(self) -> None:
        try:
            os.makedirs(self.image_dir, exist_ok=True)
            platform_support.open_path(self.image_dir)
        except (OSError, AttributeError) as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)

    def _rename(self) -> None:
        name = self._selected()
        if not name:
            return
        dialog = GroupDialog(self.app, f"重命名图片 {name}", name)
        new_name = dialog.wait()
        if not new_name or new_name == name:
            return
        if os.path.exists(os.path.join(self.image_dir, new_name)):
            messagebox.showwarning("重名", "目标文件名已存在", parent=self)
            return
        try:
            os.rename(os.path.join(self.image_dir, name),
                      os.path.join(self.image_dir, new_name))
        except OSError as exc:
            messagebox.showerror("重命名失败", str(exc), parent=self)
            return
        # 同步所有引用（含子目录写法）
        changed = 0
        for group in self.project.groups:
            for step in group.steps:
                if step.type in IMAGE_STEP_TYPES:
                    image, sep, rest = step.content.partition("|")
                    if os.path.basename(image.strip()) == name:
                        step.content = f"{new_name}|{rest}" if sep \
                            else new_name
                        changed += 1
        self.app._mark_dirty()
        self.app._refresh_all()
        self._reload()
        self.app._set_status(f"已重命名为 {new_name}，同步 {changed} 处引用")

    def _clean_unused(self) -> None:
        refs = self._references()
        try:
            entries = os.listdir(self.image_dir)
        except OSError:
            return
        unused = [name for name in entries
                  if name.lower().endswith((".png", ".jpg", ".jpeg", ".bmp"))
                  and name not in refs
                  and os.path.isfile(os.path.join(self.image_dir, name))]
        if not unused:
            messagebox.showinfo("提示", "没有未被引用的图片", parent=self)
            return
        if not messagebox.askyesno(
                "确认清理",
                f"将删除 {len(unused)} 张未被引用的图片：\n"
                + "、".join(unused[:10])
                + ("…" if len(unused) > 10 else ""), parent=self):
            return
        failed = []
        for name in unused:
            try:
                os.remove(os.path.join(self.image_dir, name))
            except OSError:
                failed.append(name)
        self._reload()
        if failed:
            messagebox.showwarning("部分失败",
                                   "这些文件没删掉：\n" + "、".join(failed),
                                   parent=self)
        else:
            self.app._set_status(f"已清理 {len(unused)} 张未引用图片")


class OnboardingDialog(BaseDialog):
    """首次启动的三步引导（可勾选不再显示）。"""

    TIPS = (
        "1. 左侧新建执行组，右侧添加步骤；所有修改都会自动保存。",
        "2. 图片步骤先点「截图」框选按钮，再用「检测图片」确认能命中。",
        "3. 按 F5 或顶部「运行」开始；失败后可在状态栏从失败步骤继续。",
    )

    def __init__(self, parent: App):
        super().__init__(parent, "欢迎使用 MeowGo", width=520, height=360)
        self.columnconfigure(0, weight=1)
        tk.Label(self, text="三步开始使用 MeowGo", bg=CARD, fg=TEXT,
                 font=FONT_TITLE, anchor="w").grid(
            row=0, column=0, sticky="ew", padx=parent.s(22),
            pady=(parent.s(18), parent.s(10)))
        for row, tip in enumerate(self.TIPS, start=1):
            tk.Label(self, text=i18n.tr(tip), bg=CARD, fg=TEXT,
                     font=FONT_NOTE, wraplength=parent.s(460),
                     justify="left", anchor="w").grid(
                row=row, column=0, sticky="ew", padx=parent.s(22),
                pady=parent.s(6))
        self.var_hide = tk.BooleanVar(value=False)
        row_hide = tk.Frame(self, bg=CARD)
        row_hide.grid(row=4, column=0, sticky="w", padx=parent.s(22),
                      pady=(parent.s(10), 0))
        uikit.Switch(row_hide, self.var_hide, scale=parent.scale,
                     bg=CARD).pack(side="left")
        tk.Label(row_hide, text="不再显示此提示", bg=CARD, fg=MUTED,
                 font=FONT_NOTE).pack(side="left", padx=(parent.s(6), 0))
        self.footer(5, "开始使用", "check", self.ok, cancel_text="",
                    pad_top=12)
        self.fit_content(width=parent.s(520))
        self.center()

    def ok(self) -> None:
        self.result = {"hide": bool(self.var_hide.get())}
        self.destroy()


class SettingsDialog(BaseDialog):
    """运行设置：倒计时、相似度、图片目录、开关项、开机自启与托盘。"""

    def __init__(self, parent: App, settings: dict, image_dir: str):
        super().__init__(parent, "运行设置", width=500, height=560)
        self.columnconfigure(1, weight=1)
        # 用传进来的设置做底稿，保存时只改本对话框的键，别冲掉定时等配置
        self._base_settings = dict(settings)
        self._is_windows = platform_support.IS_WINDOWS
        self._can_autostart = platform_support.available("autostart")
        self._can_tray = platform_support.available("system_tray")

        def section(text: str, row: int) -> None:
            tk.Label(self, text=i18n.tr(text), bg=CARD, fg=TEXT,
                     font=FONT_SECTION, anchor="w").grid(
                row=row, column=0, columnspan=2, sticky="ew",
                padx=parent.s(16), pady=(parent.s(10), parent.s(2)))

        def note(text: str, row: int) -> None:
            tk.Label(self, text=i18n.tr(text), bg=CARD, fg=MUTED,
                     font=FONT_CAPTION, wraplength=parent.s(450),
                     justify="left", anchor="w").grid(
                row=row, column=0, columnspan=2, sticky="ew",
                padx=parent.s(16), pady=(parent.s(2), 0))

        section("通用", 0)
        self.label("界面语言", 1)
        self.var_language = tk.StringVar(
            value=i18n.language_display(str(settings.get("language", "zh"))))
        row_lang = tk.Frame(self, bg=CARD)
        row_lang.grid(row=1, column=1, sticky="e",
                      padx=(0, parent.s(16)), pady=parent.s(5))
        self.combo_language = uikit.ReadonlySelect(
            row_lang,
            values=[i18n.language_display(code) for code, _name in i18n.LANGUAGES],
            variable=self.var_language, scale=parent.scale, bg=CARD,
            font_size=FONT_SIZE_NOTE, width=20)
        self.combo_language.pack(side="left")

        self.label("界面缩放（重启后生效）", 2)
        self._scale_values = ["90%", "100%", "110%", "125%", "150%"]
        try:
            percent = round(float(settings.get("ui_scale", 1.0)) * 100)
        except (TypeError, ValueError):
            percent = 100
        nearest = min(self._scale_values,
                      key=lambda value: abs(int(value[:-1]) - percent))
        self.var_ui_scale = tk.StringVar(value=nearest)
        self.combo_scale = uikit.ReadonlySelect(
            self, values=self._scale_values, variable=self.var_ui_scale,
            scale=parent.scale, bg=CARD, font_size=FONT_SIZE_NOTE, width=8)
        self.combo_scale.grid(row=2, column=1, sticky="e",
                              padx=(0, parent.s(16)), pady=parent.s(5))

        self.label("启动前倒计时（秒）", 3)
        self.var_delay = tk.StringVar(value=str(settings.get("delay", 3)))
        ttk.Spinbox(self, from_=0, to=30, width=8,
                    textvariable=self.var_delay).grid(
            row=3, column=1, sticky="e", padx=(0, parent.s(16)), pady=parent.s(5))

        self.label("图像匹配相似度", 4)
        self.var_conf = tk.StringVar(value=str(settings.get("confidence", 0.9)))
        ttk.Spinbox(self, from_=0.5, to=1.0, increment=0.01, width=8,
                    textvariable=self.var_conf).grid(
            row=4, column=1, sticky="e", padx=(0, parent.s(16)), pady=parent.s(5))

        section("运行", 5)
        self.label("图像目录", 6)
        row = tk.Frame(self, bg=CARD)
        row.grid(row=6, column=1, sticky="ew", padx=(0, parent.s(16)),
                 pady=parent.s(5))
        row.columnconfigure(0, weight=1)
        self.var_dir = tk.StringVar(value=image_dir)
        uikit.TextField(row, textvariable=self.var_dir, scale=parent.scale,
                        bg=CARD, font_size=FONT_SIZE_NOTE).grid(
            row=0, column=0, sticky="ew")
        uikit.RoundButton(row, "浏览…", "folder_open", self._browse,
                          kind="outline", scale=parent.scale, bg=CARD,
                          height=CONTROL_SM, pad=parent.s(8),
                          font_size=FONT_SIZE_NOTE).grid(
            row=0, column=1, padx=(parent.s(4), 0))

        self.label("运行时显示悬浮进度条", 7)
        self.var_mini = tk.BooleanVar(
            value=bool(settings.get("show_mini_bar", True)))
        uikit.Switch(self, self.var_mini, scale=parent.scale, bg=CARD).grid(
            row=7, column=1, sticky="e", padx=(0, parent.s(16)),
            pady=parent.s(3))

        self.label("失败时保存屏幕截图", 8)
        self.var_shot = tk.BooleanVar(
            value=bool(settings.get("shot_on_fail", True)))
        uikit.Switch(self, self.var_shot, scale=parent.scale, bg=CARD).grid(
            row=8, column=1, sticky="e", padx=(0, parent.s(16)),
            pady=parent.s(3))

        self.label("数据处理完成后写入结果文件", 9)
        self.var_results = tk.BooleanVar(
            value=bool(settings.get("save_results", True)))
        uikit.Switch(self, self.var_results, scale=parent.scale, bg=CARD).grid(
            row=9, column=1, sticky="e", padx=(0, parent.s(16)),
            pady=parent.s(3))

        section("系统集成" if (self._can_autostart or self._can_tray)
                else "系统集成（暂不可用）", 10)
        # 开机自启：开关状态直接反映注册表实况（Run 项在不在），
        # 不在 settings 里另存一份“真相”，避免两边不一致
        self.label("开机时自动启动客户端", 11)
        self.var_autostart = tk.BooleanVar(value=autostart.is_enabled())
        self.sw_autostart = uikit.Switch(
            self, self.var_autostart, scale=parent.scale, bg=CARD)
        self.sw_autostart.grid(row=11, column=1, sticky="e",
                               padx=(0, parent.s(16)), pady=parent.s(3))

        # 托盘：两个开关任一打开就会出现托盘图标；互不依赖
        self.label("启动后最小化至系统托盘", 12)
        self.var_tray_min = tk.BooleanVar(
            value=bool(settings.get("tray_start_minimized")))
        self.sw_tray_min = uikit.Switch(
            self, self.var_tray_min, scale=parent.scale, bg=CARD)
        self.sw_tray_min.grid(row=12, column=1, sticky="e",
                              padx=(0, parent.s(16)), pady=parent.s(3))

        self.label("关闭窗口时最小化至系统托盘", 13)
        self.var_tray_close = tk.BooleanVar(
            value=bool(settings.get("tray_on_close")))
        self.sw_tray_close = uikit.Switch(
            self, self.var_tray_close, scale=parent.scale, bg=CARD)
        self.sw_tray_close.grid(row=13, column=1, sticky="e",
                                padx=(0, parent.s(16)), pady=parent.s(3))

        if not self._can_autostart:
            self.sw_autostart.set_enabled(False)
        if not self._can_tray:
            self.sw_tray_min.set_enabled(False)
            self.sw_tray_close.set_enabled(False)

        if platform_support.IS_MACOS:
            note("开机自启通过 LaunchAgent（~/Library/LaunchAgents）实现，"
                 "登录后仅启动客户端，不自动执行流程。菜单栏托盘图标点按后"
                 "可显示主窗口或退出；「启动后最小化」适合配合开机自启使用。",
                 14)
            note("相似度阈值越低，识别范围越宽，误匹配风险也越高。"
                 "失败截图保存于 reports/shots；数据结果写入数据文件所在目录。",
                 15)
        elif self._is_windows:
            note("开机自启通过当前用户注册表 Run 项实现，无需管理员权限。"
                 "登录 Windows 后仅启动客户端，不自动执行流程；如需定时无人"
                 "值守运行，请使用「定时运行 → 创建计划任务」。启动命令："
                 + autostart.launch_command(), 14)
            note("托盘选项：启用后，启动或关闭客户端只会隐藏窗口，系统托盘将"
                 "显示 MeowGo 图标。双击图标可重新打开窗口，右键菜单可退出"
                 "客户端。", 15)
            note("相似度阈值越低，识别范围越宽，误匹配风险也越高。"
                 "失败截图保存于 reports/shots；数据结果写入数据文件所在目录。",
                 16)
        else:
            note("当前系统不支持开机自启与系统托盘，相关开关已停用。", 14)
            note("相似度阈值越低，识别范围越宽，误匹配风险也越高。"
                 "失败截图保存于 reports/shots；数据结果写入数据文件所在目录。",
                 15)

        actions = tk.Frame(self, bg=CARD)
        actions.grid(row=17, column=0, columnspan=2, sticky="ew",
                     padx=parent.s(16), pady=(parent.s(6), 0))
        self.lbl_defaults = tk.Label(actions, text="", bg=CARD, fg=MUTED,
                                     font=FONT_CAPTION)
        self.lbl_defaults.pack(side="left")
        uikit.RoundButton(actions, "恢复默认设置", "undo",
                          self._restore_defaults, kind="ghost",
                          scale=parent.scale, bg=CARD, height=CONTROL_SM,
                          pad=parent.s(10), font_size=FONT_SIZE_NOTE).pack(
            side="right")

        self.footer(18, "保存", "check", self.ok, pad_top=10)
        self.fit_content(width=parent.s(500))
        self.center()

    def _restore_defaults(self) -> None:
        """把本页设置恢复成出厂默认值（点「保存」后生效）。"""
        self.var_language.set(i18n.language_display("zh"))
        self.var_ui_scale.set("100%")
        self.var_delay.set("3")
        self.var_conf.set("0.9")
        self.var_dir.set(DEFAULT_IMAGE_DIR)
        self.var_mini.set(True)
        self.var_shot.set(True)
        self.var_results.set(True)
        if self._can_autostart:
            self.var_autostart.set(False)
        if self._can_tray:
            self.var_tray_min.set(False)
            self.var_tray_close.set(False)
        self.lbl_defaults.configure(text="已恢复默认值，点「保存」生效")

    def _browse(self) -> None:
        path = filedialog.askdirectory(parent=self, title="选择图片目录",
                                       initialdir=self.var_dir.get())
        if path:
            self.var_dir.set(path)

    def ok(self) -> None:
        def number(value, default):
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        directory = self.var_dir.get().strip() or self.app.image_dir
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError:
            pass
        # 开机自启：开关状态与注册表实况对齐后再收结果，失败就留在对话框里
        if not self._apply_autostart():
            return
        # 在现有设置上改键，别把定时运行等其它配置冲掉
        try:
            ui_scale = int(str(self.var_ui_scale.get()).rstrip("%")) / 100
        except ValueError:
            ui_scale = 1.0
        self.result = dict(self._base_settings)
        self.result.update({
            "language": i18n.language_code(self.var_language.get()),
            "ui_scale": ui_scale,
            "delay": int(number(self.var_delay.get(), 3)),
            "confidence": number(self.var_conf.get(), 0.9),
            "image_dir": directory,
            "show_mini_bar": bool(self.var_mini.get()),
            "shot_on_fail": bool(self.var_shot.get()),
            "save_results": bool(self.var_results.get()),
            "autostart": (bool(self.var_autostart.get())
                          if self._can_autostart else False),
            "tray_start_minimized": (bool(self.var_tray_min.get())
                                     if self._can_tray else False),
            "tray_on_close": (bool(self.var_tray_close.get())
                              if self._can_tray else False),
        })
        self.destroy()

    def _apply_autostart(self) -> bool:
        """把「开机自动启动」开关同步到系统的自启项。

        返回 False 表示失败（已弹错误框），调用方应保持对话框不关闭。
        程序被整体搬家后，已注册的命令会过期——重新注册一次即可自愈。
        不支持的平台（Linux）直接跳过。
        """
        if not platform_support.available("autostart"):
            return True
        want = bool(self.var_autostart.get())
        have = autostart.is_enabled()
        # macOS 的 current_command 是参数列表拼成的，格式与 launch_command
        # 不同，不能直接比较；只有 Windows 注册表需要检测命令是否过期。
        stale = (have and platform_support.IS_WINDOWS
                 and autostart.current_command() != autostart.launch_command())
        if want and (not have or stale):
            ok, error = autostart.enable()
            if not ok:
                messagebox.showerror("开机自启开启失败", error, parent=self)
                return False
            self.app._log("info", "开机自启已开启：" + autostart.launch_command())
        elif have and not want:
            ok, error = autostart.disable()
            if not ok:
                messagebox.showerror("开机自启关闭失败", error, parent=self)
                return False
            self.app._log("info", "开机自启已关闭")
        return True


class DataPreviewDialog(BaseDialog):
    """带数据运行前的预览：看清列名、行数和前几行再开跑。"""

    def __init__(self, parent: App, path: str, data: list[dict]):
        super().__init__(parent, "带数据运行 · 预览", width=560, height=400)
        columns = list(data[0].keys()) if data else []
        shown = columns[:6]
        self.columnconfigure(0, weight=1)

        tk.Label(self, text=f"文件：{os.path.basename(path)}", bg=CARD, fg=TEXT,
                 font=FONT_BODY_BOLD).grid(
            row=0, column=0, sticky="w", padx=parent.s(16),
            pady=(parent.s(14), parent.s(2)))
        tk.Label(self, text=f"共 {len(data)} 行数据 · "
                            f"{len(columns)} 列：{'、'.join(columns) or '（无）'}",
                 bg=CARD, fg=MUTED, font=FONT_CAPTION,
                 wraplength=parent.s(500), justify="left").grid(
            row=1, column=0, sticky="w", padx=parent.s(16))

        wrap = tk.Frame(self, bg=CARD)
        wrap.grid(row=2, column=0, sticky="nsew", padx=parent.s(16),
                  pady=parent.s(10))
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        tree = ttk.Treeview(wrap, columns=shown, show="headings",
                            selectmode="none", height=6)
        for index, name in enumerate(shown, start=1):
            tree.heading(name, text=name)
            tree.column(name, width=parent.s(90), anchor="w")
        for row in data[:5]:
            tree.insert("", "end",
                        values=[row.get(name, "") for name in shown])
        tree.grid(row=0, column=0, sticky="nsew")
        scroll = uikit.SlimScrollbar(
            wrap, orient="vertical", command=tree.yview,
            scale=parent.scale, bg=CARD)
        scroll.grid(row=0, column=1, sticky="ns", padx=(parent.s(2), 0))
        tree.configure(yscrollcommand=scroll.set)
        if len(columns) > 6:
            tk.Label(wrap, text=f"（只显示前 6 列，共 {len(columns)} 列）",
                     bg=CARD, fg=MUTED,
                     font=FONT_CAPTION).grid(
                row=1, column=0, sticky="w", pady=(parent.s(4), 0))

        tk.Label(self, text="步骤内容里写 {列名}，运行时会被替换成当前行的值；"
                            "{index} 是行号。", bg=CARD, fg=MUTED,
                 font=FONT_CAPTION).grid(
            row=3, column=0, sticky="w", padx=parent.s(16), pady=(0, parent.s(8)))

        self.footer(4, "开始运行", "run", self.ok,
                    pad_top=0, pad_bottom=12)
        self.center()

    def ok(self) -> None:
        self.result = True
        self.destroy()


class DebugStepDialog(BaseDialog):
    """单步调试：执行下一步之前问一句（运行 / 跳过 / 继续 / 停止）。

    由 _ask_debug 在主线程弹出；wait() 的返回值交给工作线程：
    run = 只执行这一步，skip = 不执行，continue = 执行并跑完剩余步骤，
    stop = 终止整个运行。无人操作 3 分钟后自动按停止处理。
    """

    AUTO_STOP_SECONDS = 180

    def __init__(self, parent: App, group_name: str, index: int, step: Step,
                 snapshot: Optional[dict] = None, target_title: str = ""):
        super().__init__(parent, "单步调试", width=520, height=330)
        self._snapshot = snapshot if snapshot is not None else {}
        # 单步调试时主窗是刻意最小化的；Tk 会把 transient 子窗跟着藏起来，
        # 所以主窗不可见时去掉 transient 关系，让确认框独立置顶显示。
        self._standalone = parent.state() != "normal"
        if self._standalone:
            self.tk.call("wm", "transient", str(self), "")
        self.attributes("-topmost", True)
        self.columnconfigure(0, weight=1)
        self._timeout_id: Optional[str] = None
        self._type_icon = None

        group = next((g for g in parent.project.groups
                      if g.name == group_name), None)
        total = len(group.steps) if group is not None else 0
        heading = (f"执行组「{group_name}」· 第 {index + 1} / {total} 步"
                   if total else f"执行组「{group_name}」· 第 {index + 1} 步")
        self.label(heading, 0)

        # 类型徽标：图标 + 类型名，颜色沿用步骤列表的语义色
        type_row = tk.Frame(self, bg=CARD)
        type_row.grid(row=1, column=0, sticky="w", padx=parent.s(16),
                      pady=(parent.s(2), parent.s(4)))
        color = TYPE_COLORS.get(step.type, TYPE_ICON_COLOR)
        self._type_icon = uikit.icon(
            TYPE_ICONS.get(step.type, "note"), parent.s(16), color)
        tk.Label(type_row, image=self._type_icon, bg=CARD).pack(side="left")
        tk.Label(type_row, text=step.label(), bg=CARD, fg=color,
                 font=FONT_CAPTION).pack(side="left", padx=(parent.s(6), 0))
        if step.repeat and step.repeat != 1:
            tk.Label(type_row, text=f"重复 {step.repeat:g} 次", bg=CARD,
                     fg=MUTED, font=FONT_CAPTION).pack(
                side="left", padx=(parent.s(10), 0))

        content = (step.content or "").strip() or "（无内容）"
        tk.Label(self, text=content, bg=CARD, fg=TEXT, font=FONT_BODY_BOLD,
                 wraplength=parent.s(460), justify="left").grid(
            row=2, column=0, sticky="w", padx=parent.s(16),
            pady=(0, parent.s(6)))

        options = self._options_summary(step)
        if options:
            tk.Label(self, text=options, bg=CARD, fg=MUTED,
                     font=FONT_CAPTION, wraplength=parent.s(460),
                     justify="left").grid(
                row=3, column=0, sticky="w", padx=parent.s(16))
        if step.note.strip():
            tk.Label(self, text=f"备注：{step.note.strip()}", bg=CARD, fg=MUTED,
                     font=FONT_CAPTION, wraplength=parent.s(460),
                     justify="left").grid(
                row=4, column=0, sticky="w", padx=parent.s(16),
                pady=(parent.s(4), 0))

        # 现场信息：目标窗口 / 数据行 / 运行变量（单步调试时最常看的）
        self._target_title = target_title
        self.context_label = tk.Label(
            self, text=self._context_text(self._snapshot, target_title),
            bg=CARD, fg=MUTED, font=FONT_CAPTION, wraplength=parent.s(460),
            justify="left")
        self.context_label.grid(
            row=5, column=0, sticky="w", padx=parent.s(16),
            pady=(parent.s(8), 0))
        # 临时改变量：只写进这一程运行，不会保存到项目配置
        editor_row = tk.Frame(self, bg=CARD)
        editor_row.grid(row=6, column=0, sticky="ew", padx=parent.s(16),
                        pady=(parent.s(6), 0))
        self.var_name = ttk.Combobox(
            editor_row, values=sorted(self._snapshot.get("vars") or {}),
            width=12, font=FONT_NOTE)
        self.var_name.pack(side="left")
        self.var_value_var = tk.StringVar()
        self.var_value = uikit.TextField(
            editor_row, textvariable=self.var_value_var, width=16,
            scale=parent.scale, bg=CARD, font_size=FONT_SIZE_NOTE)
        self.var_value.pack(side="left", padx=(parent.s(6), 0))
        uikit.RoundButton(
            editor_row, "写入", "check", self._write_var, kind="outline",
            scale=parent.scale, bg=CARD, height=CONTROL_SM,
            pad=parent.s(6), font_size=FONT_SIZE_NOTE,
            tooltip="调试期间临时改变量（{变量名} 将使用新值）"
            ).pack(side="left", padx=(parent.s(6), 0))
        self.var_note_label = tk.Label(
            editor_row, text="（选中的变量会被替换）", bg=CARD, fg=MUTED,
            font=FONT_CAPTION)
        self.var_note_label.pack(side="left", padx=(parent.s(6), 0))
        # 输入框里按空格 / 回车不能触发「跳过 / 运行」；回车 = 写入变量
        def on_write(_event: object) -> object:
            self._write_var()
            return "break"

        for widget in (self.var_name, self.var_value):
            widget.bind("<space>", lambda _event: "break")
            widget.bind("<Return>", on_write)

        # 快捷键提示 + 自动停止倒计时：让“没人管”的情况也可预期
        hint_row = tk.Frame(self, bg=CARD)
        hint_row.grid(row=7, column=0, sticky="ew", padx=parent.s(16),
                      pady=(parent.s(8), 0))
        tk.Label(hint_row, text="Enter 运行　·　Space 跳过　·　"
                                "F5 继续运行　·　Esc 停止",
                 bg=CARD, fg=MUTED, font=FONT_CAPTION).pack(side="left")
        self._timeout_label = tk.Label(hint_row, text="", bg=CARD, fg=MUTED,
                                       font=FONT_CAPTION)
        self._timeout_label.pack(side="right")

        buttons = tk.Frame(self, bg=CARD)
        buttons.grid(row=8, column=0, sticky="ew", padx=parent.s(16),
                     pady=(parent.s(10), parent.s(14)))
        uikit.RoundButton(buttons, "停止", "stop", self._stop,
                          kind="danger", scale=parent.scale, bg=CARD,
                          height=CONTROL_MD).pack(side="left")
        uikit.RoundButton(buttons, "运行这一步", "run", self._run,
                          kind="accent", scale=parent.scale, bg=CARD,
                          height=CONTROL_MD).pack(side="right")
        uikit.RoundButton(buttons, "继续运行", "continue", self._continue,
                          kind="outline", scale=parent.scale, bg=CARD,
                          height=CONTROL_MD).pack(side="right",
                                                   padx=(0, parent.s(8)))
        uikit.RoundButton(buttons, "跳过", "next", self._skip,
                          kind="outline", scale=parent.scale, bg=CARD,
                          height=CONTROL_MD).pack(side="right",
                                                   padx=(0, parent.s(8)))

        self.bind("<Return>", lambda _event: self._run())
        self.bind("<KP_Enter>", lambda _event: self._run())
        self.bind("<space>", lambda _event: self._skip())
        self.bind("<F5>", lambda _event: self._continue())
        self._remaining = self.AUTO_STOP_SECONDS
        self._tick_timeout()
        self.fit_content(width=parent.s(520))
        self.center(on_screen=self._standalone)

    @staticmethod
    def _options_summary(step: Step) -> str:
        """把非默认的高级选项压成一行，确认时不用另开属性框核对。"""
        parts: list[str] = []
        if step.timeout and step.timeout > 0:
            parts.append(f"最多等 {step.timeout:g} 秒")
        if step.on_fail == "stop":
            parts.append("失败时停止")
        if step.retry and step.retry > 0:
            parts.append(f"失败重试 {step.retry} 次")
        if step.region:
            parts.append(f"限定区域 {step.region}")
        if step.nth and step.nth > 0:
            parts.append(f"命中第 {step.nth} 个")
        if step.grayscale:
            parts.append("灰度匹配")
        return " · ".join(i18n.tr(part) for part in parts)

    @staticmethod
    def _context_text(snapshot: dict, target_title: str = "") -> str:
        """把现场信息压成几行短句：目标窗口 / 数据行 / 运行变量。

        每行都截断长度，防止一条超长数据把确认框顶得没法看。
        """
        def clip(text: object, limit: int) -> str:
            value = str(text)
            return value if len(value) <= limit else value[:limit] + "…"

        lines: list[str] = []
        title = (target_title or "").strip()
        lines.append(i18n.tr("目标窗口") + "："
                     + (clip(title, 40) if title else i18n.tr("未识别")))
        row = snapshot.get("row") or {}
        if row:
            pairs = [f"{clip(key, 10)}={clip(value, 16)}"
                     for key, value in list(row.items())[:4]]
            if len(row) > 4:
                pairs.append(i18n.tr("共 {n} 列").format(n=len(row)))
            lines.append(f"{i18n.tr('数据行')} {snapshot.get('row_no') or 1}："
                         + "；".join(pairs))
        variables = snapshot.get("vars") or {}
        pairs = [f"{clip(key, 10)}={clip(value, 24)}"
                 for key, value in list(variables.items())[:5]]
        if len(variables) > 5:
            pairs.append(i18n.tr("共 {n} 个").format(n=len(variables)))
        lines.append(i18n.tr("运行变量") + "："
                     + ("；".join(pairs) if pairs else i18n.tr("（还没有）")))
        return "\n".join(lines)

    def _write_var(self) -> None:
        """把确认框里临时填的值写进本程运行的变量表（不落盘）。"""
        name = self.var_name.get().strip()
        if not name:
            self.var_note_label.configure(
                text=i18n.tr("请先在左侧填变量名"), fg=WARNING)
            return
        value = self.var_value_var.get()
        preview = value if len(value) <= 40 else value[:40] + "…"
        self.app.executor.vars[name] = value
        values = list(self.var_name["values"] or [])
        if name not in values:
            self.var_name.configure(values=values + [name])
        fresh = self.app.executor.debug_snapshot()
        self.context_label.configure(text=self._context_text(
            fresh, self._target_title))
        self.var_note_label.configure(
            text=i18n.tr("已写入 {name} = {value}").format(name=name,
                                                          value=preview),
            fg=SUCCESS)
        self.app._log("info", i18n.tr("单步调试：变量 {name} 已改为 {value}")
                      .format(name=name, value=preview))

    def _tick_timeout(self) -> None:
        if self._remaining <= 0:
            self._stop()                 # 无人操作：按停止处理，运行不挂住
            return
        minutes, seconds = divmod(self._remaining, 60)
        self._timeout_label.configure(
            text=f"无操作 {minutes:02d}:{seconds:02d} 后自动停止")
        self._remaining -= 1
        self._timeout_id = self.after(1000, self._tick_timeout)

    def _teardown(self) -> None:
        if self._timeout_id is not None:
            try:
                self.after_cancel(self._timeout_id)
            except tk.TclError:
                pass
            self._timeout_id = None

    def _run(self) -> None:
        self.result = "run"
        self.destroy()

    def _skip(self) -> None:
        self.result = "skip"
        self.destroy()

    def _continue(self) -> None:
        self.result = "continue"
        self.destroy()

    def _stop(self) -> None:
        self.result = "stop"
        self.destroy()

    def cancel(self) -> None:
        self.result = "stop"          # 关对话框 = 停止运行
        self.destroy()


def main(project_path: str = "") -> None:
    logs.setup()
    # 同时开两个界面会互相覆盖配置，也没必要
    lock = InstanceLock(make_lock_name(APP_DIR, "GUI"))
    if not lock.acquire():
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo("MeowGo 已经在运行",
                                "MeowGo 已经打开了一个窗口。")
            root.destroy()
        except Exception:        # noqa: BLE001
            print("MeowGo 已经在运行")
        return
    try:
        app = App()
        logs.install_tk_handler(app)
        if project_path:
            # 双击 .mgproj 启动：等窗口建好再打开，失败也有界面提示
            app.after(0, lambda: app.load_project_file(project_path))
        app.mainloop()
    finally:
        lock.release()


if __name__ == "__main__":
    enable_dpi_awareness()
    main()
