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
import threading
import time
import tkinter as tk
import types
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional

from . import assets, autostart, i18n, theme, tray, uikit
from .engine import (CONDITION_TYPES, IMAGE_STEP_TYPES, IMAGE_TYPES,
                     STEP_TYPES, Executor, Group, Project, Step,
                     estimate_steps, export_excel, find_images, import_excel,
                     opencv_available, parse_region, validate_environment,
                     validate_project)
from .lock import InstanceLock, make_lock_name
from .theme import (CONTROL_LG, CONTROL_MD, CONTROL_SM, CONTROL_XS,
                    DARK_PALETTE, FONT_BODY, FONT_BODY_BOLD, FONT_CAPTION,
                    FONT_CAPTION_BOLD, FONT_DISPLAY, FONT_HERO, FONT_MONO,
                    FONT_NOTE, FONT_NOTE_BOLD, FONT_SECTION, FONT_TITLE,
                    FONT_TITLE_LARGE, FONT_SIZE_BODY, FONT_SIZE_CAPTION,
                    FONT_SIZE_NOTE, ICON_LG, ICON_MD, ICON_SM, ICON_XS,
                    LIGHT_PALETTE, TABLE_ROW_HEIGHT)

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
DEFAULT_GROUPS_VERSION = 2
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


def make_schedule_task(time_text: str = "09:00", group: str = "",
                       enabled: bool = True) -> dict:
    """生成一条新的定时任务（id 只用于区分同一分钟内的多条任务）。"""
    return {
        "id": f"task-{time.time_ns()}",
        "enabled": bool(enabled),
        "time": _valid_schedule_time(time_text),
        "group": (group or "").strip(),
    }


def _valid_schedule_time(value) -> str:
    """把各种输入夹成 HH:MM；非法值退回 09:00。"""
    match = re.match(r"^\s*(\d{1,2})\s*:\s*(\d{1,2})\s*$", str(value or ""))
    if not match:
        return "09:00"
    hour = min(23, max(0, int(match.group(1))))
    minute = min(59, max(0, int(match.group(2))))
    return f"{hour:02d}:{minute:02d}"


def normalize_schedule_task(raw, index: int = 0) -> Optional[dict]:
    """把磁盘上的任务字典补齐成可控结构；明显无效的条目返回 None。"""
    if not isinstance(raw, dict):
        return None
    if not raw.get("time") and not raw.get("group") and not raw.get("enabled"):
        return None
    return {
        "id": str(raw.get("id") or f"task-{index + 1}"),
        "enabled": bool(raw.get("enabled", True)),
        "time": _valid_schedule_time(raw.get("time", "09:00")),
        "group": str(raw.get("group") or "").strip(),
    }


def schedule_tasks(settings: dict) -> list[dict]:
    """读取定时任务列表，兼容旧版单条 schedule 配置。"""
    raw = settings.get("schedules")
    tasks: list[dict] = []
    if isinstance(raw, list):
        for index, item in enumerate(raw):
            task = normalize_schedule_task(item, index)
            if task is not None:
                tasks.append(task)
        return tasks
    legacy = settings.get("schedule")
    if isinstance(legacy, dict):
        task = normalize_schedule_task(legacy, 0)
        if task is not None:
            task["id"] = "legacy"
            tasks.append(task)
    return tasks


def schedule_next_run(task: dict, now: Optional[datetime] = None) -> datetime:
    """下一次到点时间（每天一次；已过今天的点就算明天）。"""
    now = now or datetime.now()
    hour, minute = (int(part) for part in task.get("time", "09:00").split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def schedule_countdown_text(target: datetime,
                            now: Optional[datetime] = None) -> str:
    """把剩余时间写成 02:13:45 / 1天 02:13:45。"""
    now = now or datetime.now()
    seconds = max(0, int((target - now).total_seconds()))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    text = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{days}{i18n.tr('天 ')}{text}" if days else text


def schedule_day_text(target: datetime, now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    offset = (target.date() - now.date()).days
    if offset == 0:
        return i18n.tr("今天")
    if offset == 1:
        return i18n.tr("明天")
    return i18n.tr("周" + "一二三四五六日"[target.weekday()])

# 配色唯一来源是 rpa.theme；这里把浅色令牌铺成模块常量，既有代码可直接用
# BG / CARD / ACCENT ...。_apply_theme 切主题时同样整组替换。
globals().update(LIGHT_PALETTE)

TYPE_ICONS = {
    "click": "image", "dblclick": "image", "rightclick": "image",
    "input": "text", "wait": "clock", "scroll": "scroll",
    "hotkey": "keyboard", "time": "calendar", "cmd": "terminal",
    "call": "link", "if_image": "check", "window": "open",
    "click_xy": "mouse", "move_xy": "mouse",
    "if_not_image": "check", "if_else": "check", "if_window": "open",
    "set": "text", "copy": "copy", "drag": "mouse",
}

# 条件步骤里「图片 | 组名」两段式的类型（对话框用图形化双输入框）
PIPE_CONDITION_TYPES = ("if_image", "if_not_image", "if_window")

# 新增步骤对话框里「执行组」分类：这些类型会调用 / 进入其它执行组。
# 其余类型统一归入「基础」，方便在下拉里按分组浏览。
TYPE_GROUP_EXEC = {"call", "if_image", "if_not_image", "if_else",
                   "if_window"}

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
        hwnd = int(window.winfo_id())
        user32 = ctypes.windll.user32
        gwl_exstyle, no_activate = -20, 0x08000000
        user32.SetWindowLongW(hwnd, gwl_exstyle,
                              user32.GetWindowLongW(hwnd, gwl_exstyle)
                              | no_activate)
    except Exception:  # noqa: BLE001 — 非 Windows / 老系统无所谓
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
    state = {"iid": None, "y": 0, "dragging": False, "line": None}

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

    必须在创建 Tk 根窗口之前调用。
    """
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # Win8.1+
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # 旧系统
        except Exception:
            pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MeowGo.GUI")
    except Exception:
        pass


def build_default_groups() -> list[Group]:
    """首次启动时预置的常用执行组（每次返回新对象，避免共享状态）。"""
    browser = Group(
        "打开浏览器",
        [
            Step("set", "浏览器=chrome",
                 note="chrome / msedge / 完整 exe 路径"),
            Step("set", "窗口标题=Chrome",
                 note="窗口标题关键词；Edge 填写 Edge，Firefox 填写 Firefox。"),
            Step("set", "网址=https://www.baidu.com",
                 note="要打开的链接。"),
            Step("cmd", 'start "" "{浏览器}"',
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
            Step("set", "软件=notepad",
                 note="exe 名称或完整路径，如 notepad / calc。"),
            Step("cmd", 'start "" "{软件}"',
                 note="按变量启动软件；若无法找到，请填写完整路径。"),
        ],
        note="预置模板：请将「软件」变量修改为要打开的程序名称或完整 exe 路径。",
    )
    batch_urls = Group(
        "批量打开网址",
        [
            Step("set", "浏览器=chrome",
                 note="chrome / msedge / 完整 exe 路径"),
            Step("set", "网址=https://www.baidu.com",
                 note="默认网址；带数据运行时由「网址」列覆盖。"),
            Step("cmd", 'start "" "{浏览器}" "{网址}"',
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
            Step("set", "文件路径=C:\\Users\\Public\\Documents\\示例.txt",
                 note="要上传文件的完整路径。"),
            Step("click", "上传按钮.png",
                 note="请先将页面上的上传按钮截图保存为该文件名。"),
            Step("wait", "0.8",
                 note="等待文件选择框出现。"),
            Step("hotkey", "alt,n",
                 note="将焦点切换至文件名输入框。"),
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
            Step("set", "软件=notepad",
                 note="首先打开的程序，如 notepad / calc / 完整路径。"),
            Step("set", "浏览器=chrome",
                 note="chrome / msedge / 完整 exe 路径"),
            Step("set", "网址=https://www.baidu.com",
                 note="要打开的链接。"),
            Step("cmd", 'start "" "{软件}"',
                 note="启动常用软件。"),
            Step("wait", "0.5",
                 note="为软件预留启动时间。"),
            Step("cmd", 'start "" "{浏览器}" "{网址}"',
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
    changed = False
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


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.scale = self._init_scaling()
        self.geometry(f"{self.s(1180)}x{self.s(760)}")
        self.minsize(self.s(1000), self.s(640))
        self.configure(bg=BG)

        self.settings = self._load_settings()
        # 界面语言：在创建任何控件之前安装翻译层，后面新建的对话框也会自动生效
        i18n.install()
        i18n.set_language(str(self.settings.get("language", "zh")))
        # 不再有“项目”概念：执行组直接自动保存到程序目录的状态文件
        self.project = Project.from_dict(self.settings)
        self.image_dir = str(self.settings.get("image_dir", DEFAULT_IMAGE_DIR))
        self.project.image_dir = self.image_dir
        self.group_index = 0          # 当前编辑的组
        self.log_visible = bool(self.settings.get("log_visible", True))
        # 主题：light / dark（_apply_theme 在界面建好后整组换色）
        self.dark = str(self.settings.get("theme", "light")) == "dark"
        self._last_palette = dict(LIGHT_PALETTE)
        # 单步调试：每步执行前弹窗确认（运行 / 跳过 / 停止）
        self.debug_mode = bool(self.settings.get("debug_mode", False))
        self._debug_once = False       # 一次性调试：只作用下一程运行
        self._dirty = False
        self._icons: dict[tuple, object] = {}
        # 运行辅助：失败步骤定位、日志行→步骤映射、上次报告路径
        self._failed_step: Optional[tuple[str, int]] = None
        self._current_run_step: Optional[tuple[str, int]] = None
        self._step_log_lines: dict[int, tuple[str, int]] = {}
        self._step_marks: dict[str, dict[int, str]] = {}
        self._last_report_path = ""
        self._last_result_path = ""
        self._data_path = ""
        self._run_lock: Optional[InstanceLock] = None
        # 运行进度：estimate_steps 的总步数（-1 表示不可估）、迷你置顶条
        self._progress_total = -1
        self._progress_pulse = False
        self._mini_bar: Optional[RunMiniBar] = None
        # 系统托盘：设置里开了任一托盘开关才创建图标（见 _init_tray）
        self.tray: Optional[tray.TrayIcon] = None
        self._tray_window_visible = True
        self._session_ending = False       # 注销 / 关机中：不再躲托盘
        self._session_hook = None
        self._quitting = False
        # 步骤搜索：当前过滤词（空 = 不过滤）
        self._step_filter = ""
        # 图片预览：当前缩略图的 PhotoImage 与文件路径（防 GC / 打开目录用）
        self._preview_photo = None
        self._preview_path = ""

        self.log_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.ui_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.executor = Executor(base_dir=APP_DIR, log=self._enqueue_log)
        self.worker: Optional[threading.Thread] = None
        self.running = False
        self.paused = False
        self._countdown_token = 0
        # 撤销 / 重做：存 (操作名, 变更前的 project 快照)
        self._undo_stack: list[tuple[str, dict]] = []
        self._redo_stack: list[tuple[str, dict]] = []
        self._editor: Optional[tk.Widget] = None
        # 定时运行：记录今天已经触发过的安排，避免同一天重复跑
        self._schedule_fired: set[str] = set()
        # 最近一次由定时任务启动的运行记录（结束时补结果）
        self._active_schedule: Optional[dict] = None
        self._schedule_pending = False
        self._schedule_popover = None
        # 行内编辑时的按键录入（双击热键步骤的内容列）
        self._inline_target: Optional[tk.Widget] = None
        self._inline_record_btn: Optional[tk.Widget] = None
        self._inline_previous = ""
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
        if not self.project.groups:
            self.project.groups.append(Group(self.project.unique_name("默认组")))
        self._refresh_all(select_group=0)
        self._start_global_hotkey()
        self._warm_imports()
        self.after(120, self._drain_queue)
        self.after(2000, self._autosave_tick)
        self.after(1000, self._schedule_tick)
        self._check_dpi_setup()
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
        scale = max(1.0, min(3.0, dpi / 96.0))
        try:
            self.tk.call("tk", "scaling", dpi / 72.0)
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
                    image.thumbnail((size, size), Image.LANCZOS)
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
                    image = image.resize((target_w, target_h), Image.LANCZOS)
                    if dark:
                        pixels = image.load()
                        for y in range(target_h):
                            for x in range(target_w):
                                r, g, b, alpha = pixels[x, y]
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
                self.iconphoto(True, self._icon_image)
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

    # --------------------------------------------------------------- 菜单
    def _menu_defs(self):
        """顶部菜单栏：左到右的按钮 + 各自的下拉项。"""
        return [("文件", self._file_items), ("编辑", self._edit_items),
                ("运行", self._run_items), ("视图", self._view_items),
                ("帮助", self._help_items)]

    def _file_items(self) -> list:
        MI = uikit.MenuItem
        return [
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
        return [
            MI(self._undo_label(), "up", self.undo, "Ctrl+Z",
               enabled=bool(self._undo_stack)),
            MI(self._redo_label(), "down", self.redo, "Ctrl+Y",
               enabled=bool(self._redo_stack)),
            "-",
            MI("新建执行组", "add", self.add_group, "Ctrl+G", accent=True),
            MI("恢复预置执行组…", "add", self.restore_default_groups,
               "补回缺失模板"),
            MI("重命名执行组…", "pencil", self.rename_group),
            MI("复制为副本", "copy", self.duplicate_group),
            MI("删除执行组", "trash", self.delete_group, danger=True),
            "-",
            MI("新增步骤", "add", self.add_step, "Ctrl+Enter"),
            MI("全选步骤", "check", self._select_all_steps, "Ctrl+A",
               enabled=total > 0),
            MI("编辑步骤…", "pencil", self.edit_step, "F2", enabled=has),
            MI(f"复制选中步骤（{len(rows)}）" if multi else "复制步骤", "copy",
               self.duplicate_step, enabled=has),
            MI(f"删除选中步骤（{len(rows)}）" if multi else "删除步骤", "trash",
               self.delete_step, "Delete", enabled=has, danger=True),
            "-",
            MI("截取屏幕并替换图片…", "image",
               lambda: self.capture_region(self._apply_capture),
               enabled=has and group.steps[index].type in IMAGE_STEP_TYPES),
            "-",
            MI("宏录制鼠标操作…", "mouse", self.open_macro_recorder,
               "录成坐标步骤"),
        ]

    def _run_items(self) -> list:
        MI = uikit.MenuItem
        return [
            MI("运行当前组", "run", self.run_group, "F5", accent=True),
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
            MI("停止", "stop", self.stop_run, "F9 全局"),
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
        return [MI("使用说明", "info", self.show_help),
                MI("快捷键速查", "question", self.show_shortcuts, "?"),
                MI("关于 " + APP_TITLE, "cat", self.show_about)]

    def _build_menubar(self, parent) -> None:
        """自绘菜单栏：一排扁平按钮，点开后是与右键同款的下拉菜单。"""
        self._top_buttons: dict[str, uikit.RoundButton] = {}
        for name, _builder in self._menu_defs():
            button = uikit.RoundButton(parent, name, None,
                                       lambda n=name: self._open_top_menu(n),
                                       kind="ghost", scale=self.scale, bg=CARD,
                                       height=CONTROL_SM, pad=self.s(10),
                                       font_size=FONT_SIZE_BODY)
            button.pack(side="left", padx=self.s(1))
            self._top_buttons[name] = button

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
        self.step_tree.tag_configure("done", background=MARK_DONE)
        self.step_tree.tag_configure("failed", background=MARK_FAILED)
        self.step_tree.tag_configure("even", background=CARD)
        self.step_tree.tag_configure("odd", background=ZEBRA)
        self.step_tree.bind("<Double-1>", self._on_step_double)
        self.step_tree.bind("<Button-3>", self._step_menu)
        self.step_tree.bind("<Delete>", self._on_step_key_delete)
        self.step_tree.bind("<F2>", self._on_step_key_edit)
        self.step_tree.bind("<Control-a>", self._select_all_steps)
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

        # 运行摘要条：默认隐藏，一次运行结束后显示结论 + 打开报告
        self.summary_frame = uikit.RoundedCard(
            card, radius=self.s(INNER_RADIUS), bg=SUMMARY_BG,
            border=SUMMARY_BORDER, outer_bg=CARD, padding=self.s(3),
            shadow=False)
        summary = self.summary_frame.body
        self.summary_label = tk.Label(summary, text="",
                                      bg=SUMMARY_BG, fg=TEXT,
                                      font=FONT_NOTE)
        self.summary_label.pack(side="left", padx=self.s(10))
        uikit.RoundButton(summary, "", "close", self._hide_summary,
                          kind="ghost", scale=self.scale, bg=SUMMARY_BG,
                          height=CONTROL_XS, pad=self.s(4), icon_size=ICON_XS,
                          tooltip="收起摘要").pack(side="right", padx=self.s(4))
        self.btn_open_report = uikit.RoundButton(
            summary, "打开报告", "folder_open",
            self._open_last_report,
            kind="outline", scale=self.scale, bg=SUMMARY_BG, height=CONTROL_XS,
            pad=self.s(7), font_size=FONT_SIZE_NOTE)
        self.btn_open_report.pack(side="right")

        # 正文 + 滚动条：能回看历史日志；超过 MAX_LOG_LINES 自动丢弃最早行
        self.log_body = tk.Frame(card, bg=LOG_BG)
        self.log_body.pack(fill="x", padx=1, pady=(0, 1))
        self.log_text = tk.Text(self.log_body, height=7, bg=LOG_BG, fg=LOG_FG,
                                insertbackground=LOG_FG, relief="flat",
                                font=FONT_MONO, wrap="word",
                                padx=self.s(12), pady=self.s(8),
                                state="disabled")
        self.log_scroll = uikit.SlimScrollbar(
            self.log_body, orient="vertical", command=self.log_text.yview,
            scale=self.scale, bg=LOG_BG, variant="console")
        self.log_scroll.pack(side="right", fill="y", padx=(self.s(2), 0))
        self.log_text.pack(side="left", fill="both", expand=True)
        self.log_text.configure(yscrollcommand=self.log_scroll.set)
        # 可点击的「第 N 步」日志行：点一下跳到步骤表对应行
        # （先建 link，info/warn/error 后建——Tk 里后建的标签优先级高，
        #  这样错误行仍是红色，只额外获得下划线）
        self.log_text.tag_configure("link", foreground=ACCENT,
                                    underline=True)
        self.log_text.tag_bind("link", "<Button-1>",
                               lambda e: self._on_log_link_click(e))
        self.log_text.tag_bind("link", "<Enter>",
                               lambda e: self.log_text.configure(cursor="hand2"))
        self.log_text.tag_bind("link", "<Leave>",
                               lambda e: self.log_text.configure(cursor=""))
        # 日志着色统一取自 theme 的 LOG_* 令牌（与浅/暗色板同步切换）
        self.log_text.tag_configure("info", foreground=LOG_INFO)
        self.log_text.tag_configure("warn", foreground=LOG_WARN)
        self.log_text.tag_configure("error", foreground=LOG_ERROR)
        self.log_text.tag_configure("muted", foreground=LOG_HEAD_MUTED)
        self.log_text.tag_configure("time", foreground=LOG_TIME)

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
        tk.Label(bar, textvariable=self.status_var, bg=BG, fg=MUTED,
                 font=FONT_NOTE).pack(side="left")
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
        # 默认隐藏：运行出现失败步时才出现

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-g>", lambda e: self.add_group())
        self.bind("<Control-Return>", lambda e: self.add_step())
        self.bind("<F5>", lambda e: self.run_group())
        self.bind("<F2>", lambda e: self.edit_step())
        self.bind("<Delete>", self._on_delete_key)
        self.bind("<Control-z>", lambda e: self.undo())
        self.bind("<Control-y>", lambda e: self.redo())
        self.bind("<Control-d>", lambda e: self.toggle_theme())
        self.bind("<F8>", lambda e: self.toggle_debug())
        self.bind("<question>", lambda e: self.show_shortcuts())
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # --------------------------------------------------------------- 状态
    def _load_settings(self) -> dict:
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _state_dict(self) -> dict:
        data = dict(self.settings)
        data.pop("last_path", None)        # 旧版遗留键，界面上已无“项目”
        data.update(self._ui_state())
        data.update({"version": 2, "image_dir": self.image_dir,
                     "groups": [group.to_dict() for group in self.project.groups]})
        return data

    def _ui_state(self) -> dict:
        """收集界面状态（窗口大小、分隔条、列宽、日志展开），随配置一起保存。"""
        state = {"log_visible": self.log_visible}
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
        for key, value in palette.items():
            globals()[key] = value
        TYPE_COLORS.clear()
        TYPE_COLORS.update(TYPE_COLORS_DARK if dark else LIGHT_TYPE_COLORS)
        uikit.set_dark_mode(dark)
        self._build_style()
        self._recolor_widgets(self, previous, palette)
        # 树标签（斑马纹 / 运行染色）跟着色板走
        self.group_tree.tag_configure("running", foreground=WARNING)
        self.step_tree.tag_configure("running", background=MARK_RUNNING)
        self.step_tree.tag_configure("done", background=MARK_DONE)
        self.step_tree.tag_configure("failed", background=MARK_FAILED)
        self.step_tree.tag_configure("even", background=CARD)
        self.step_tree.tag_configure("odd", background=ZEBRA)
        self.log_text.tag_configure("link", foreground=ACCENT)
        # 日志文字色跟随色板（LOG_* 令牌），切主题时一起刷新
        self.log_text.tag_configure("info", foreground=LOG_INFO)
        self.log_text.tag_configure("warn", foreground=LOG_WARN)
        self.log_text.tag_configure("error", foreground=LOG_ERROR)
        self.log_text.tag_configure("muted", foreground=LOG_HEAD_MUTED)
        self.log_text.tag_configure("time", foreground=LOG_TIME)
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

    def _save_state(self) -> None:
        """把执行组与设置写回程序目录的状态文件（没有“项目”概念）。

        写之前先把旧文件备份到 backups/（只留最近几份），改坏了能找回。
        """
        self._backup_settings()
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
                json.dump(self._state_dict(), fh, ensure_ascii=False, indent=2)
            self._dirty = False
            self._set_saved_label()
        except OSError:
            pass

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
        self.image_dir = str(data.get("image_dir", DEFAULT_IMAGE_DIR))
        self.project.image_dir = self.image_dir
        self.group_index = 0
        self._refresh_all(select_group=0)
        self._save_state()
        self._log("info", f"已恢复配置备份：{choice}")

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
            os.startfile(self.image_dir)          # noqa: S606 (Windows)
        except (OSError, AttributeError) as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)

    def _set_status(self, text: str) -> None:
        """状态栏文案。日志标题只显示运行阶段，避免同一句话出现两遍。"""
        self._status_source = text
        self.status_var.set(i18n.tr(text))

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
            self.group_tree.insert(
                "", "end", iid=iid,
                text=f"  {group.name}   ({len(group.steps)})",
                image=self.pic("folder", 16, ACCENT_STRONG),
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

    def _step_summary(self, step: Step, index: int) -> str:
        text = step.content.strip().replace("\n", " ")
        if step.type == "call":
            text = f"{i18n.tr('调用组：')}{step.content}"
        if len(text) > 22:
            text = text[:22] + "…"
        repeat = f"  ×{step.repeat:g}" if step.repeat else ""
        return f"{index + 1}. {i18n.tr(step.label())} {text}{repeat}"

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
            self.step_tree.insert(
                "", "end", iid=iid,
                image=self.step_pic(step.type, 14),
                values=(index + 1, step.label(), step.content, repeat,
                        step.note),
                tags=(self._zebra_tag(index),))
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
        self.step_tree.item(str(index),
                            image=self.step_pic(step.type, 14),
                            values=(index + 1, step.label(), step.content,
                                    repeat, step.note))
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

    def _clear_step_filter(self) -> None:
        self._step_filter = ""
        self.step_search.delete(0, "end")
        self.step_search.insert(0, _search_placeholder())
        self.step_search.configure(foreground=MUTED)
        self._sync_search_clear()
        self._refresh_steps()

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
            image.thumbnail((self.s(132), self.s(60)), Image.LANCZOS)
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
            widget: tk.Widget = uikit.ReadonlySelect(
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
        if widget is None:
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
        if widget is not None:
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
                      "group_index": self.group_index}))
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
                      "group_index": self.group_index}))
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
                      "group_index": self.group_index}))
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
        is_image = has and group.steps[index].type in IMAGE_STEP_TYPES
        if multi:
            items = [
                uikit.MenuItem(f"复制选中步骤（{len(rows)}）", "copy",
                               self.duplicate_step, accent=True),
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
                uikit.MenuItem("截取屏幕并替换图片…", "image",
                               lambda: self.capture_region(self._apply_capture),
                               enabled=is_image),
                "-",
                uikit.MenuItem("上移", "up", lambda: self.move_step(-1),
                               enabled=has and index > 0),
                uikit.MenuItem("下移", "down", lambda: self.move_step(1),
                               enabled=has and index < total - 1),
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
        self.log_queue.put((level, message))

    def _log(self, level: str, message: str) -> None:
        self._enqueue_log(level, message)

    def _drain_queue(self) -> None:
        try:
            while True:
                level, message = self.log_queue.get_nowait()
                display = i18n.tr(message)
                stamp = time.strftime("%H:%M:%S")
                self.log_text.configure(state="normal")
                self.log_text.insert("end", f"[{stamp}] ", "time")
                # 出错 / 超时的日志行关联到当前执行的步骤，可点击跳转
                if (self._current_run_step is not None
                        and (level == "error" or "出错" in message
                             or "超时" in message)):
                    line = int(self.log_text.index("end-1c").split(".")[0])
                    self._step_log_lines[line] = self._current_run_step
                    self.log_text.insert("end", display + "\n", ("link", level))
                else:
                    self.log_text.insert("end", display + "\n", level)
                self._trim_log()
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "step":
                    self._highlight_step(*payload)  # type: ignore[misc]
                elif kind == "step_done":
                    self._apply_step_done(*payload)  # type: ignore[misc]
                elif kind == "progress":
                    self._apply_progress(*payload)  # type: ignore[misc]
                elif kind == "confirm":
                    self._ask_debug(*payload)  # type: ignore[misc]
                elif kind == "end":
                    self._on_run_end(str(payload))
                elif kind == "stop":
                    self.stop_run()
                elif kind == "run":
                    self.run_group()
        except queue.Empty:
            pass
        self.after(120, self._drain_queue)

    def _trim_log(self) -> None:
        """日志行数上限：超出的最早行连同点击映射一起丢掉。"""
        lines = int(self.log_text.index("end-1c").split(".")[0])
        extra = lines - MAX_LOG_LINES
        if extra <= 0:
            return
        self.log_text.delete("1.0", f"{extra + 1}.0")
        self._step_log_lines = {
            line - extra: target
            for line, target in self._step_log_lines.items()
            if line - extra >= 1}

    def _on_log_link_click(self, event) -> None:
        """点日志里带下划线的行：跳到对应步骤。"""
        index = self.log_text.index(f"@{event.x},{event.y}")
        line = int(index.split(".")[0])
        target = self._step_log_lines.get(line)
        if target:
            self._jump_to_step(*target)

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._step_log_lines = {}

    def copy_log(self) -> None:
        """把当前可见日志（最近 MAX_LOG_LINES 行）复制到剪贴板。"""
        text = self.log_text.get("1.0", "end-1c")
        if not text.strip():
            self._set_status("日志为空，没有可复制的内容")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        lines = text.count("\n") + 1
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

    def run_group_debug(self) -> None:
        """单步调试运行当前组：只这一程每步确认，不动全局开关。"""
        if self.running:
            messagebox.showinfo("提示", "正在运行中，请先停止", parent=self)
            return
        self._debug_once = True
        self._start(self._current_group_name())

    def run_all(self) -> None:
        self._start(None)

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
               row_offset: int = 0) -> bool:
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
        self.executor.confidence = float(self.settings.get("confidence", 0.9))
        # 显式设置（普通运行为 None）：避免上一次带数据运行影响这次
        self.executor.data = data
        self.executor.row_offset = int(row_offset or 0) if data else 0
        self._data_path = data_path if data else ""
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
        self.iconify()
        self._countdown(delay, lambda: self._launch(entry, loop, debug=debug))
        return True

    def _countdown(self, seconds: int, callback) -> None:
        self._countdown_token += 1
        token = self._countdown_token
        if seconds <= 0:
            callback()
            return
        overlay = tk.Toplevel(self)
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        overlay.configure(bg=LOG_BG)
        width, height = self.s(280), self.s(126)
        x = (self.winfo_screenwidth() - width) // 2
        y = (self.winfo_screenheight() - height) // 2
        overlay.geometry(f"{width}x{height}+{x}+{y}")
        label = tk.Label(overlay, text="", bg=LOG_BG, fg=LOG_FG,
                         font=FONT_HERO)
        label.pack(expand=True)
        tk.Label(overlay, text="即将开始，请切换到目标窗口（Esc 取消）",
                 bg=LOG_BG, fg=LOG_HEAD_MUTED,
                 font=FONT_NOTE).pack(pady=(0, self.s(12)))
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

    def _launch(self, entry: Optional[str], loop: bool,
                debug: bool = False) -> None:
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
        self.paused = False
        self._debug_once = False        # 一次性开关跑完即弃
        for button in (self.btn_run, self.btn_loop):
            button.set_enabled(False)
        self.btn_pause.set_enabled(True)
        self.btn_stop.set_enabled(True)
        # 清掉上一次运行的染色与定位信息
        self._step_marks = {}
        self._failed_step = None
        self._current_run_step = None
        self._step_log_lines = {}
        self._hide_summary()
        self.btn_locate.pack_forget()
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
        if debug:
            self._log("info", "单步调试：每一步执行前都会确认")

        def worker():
            result = self.executor.run(entry, loop=loop)
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
            self.running = False
            self.paused = False
            self._schedule_pending = False
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
            self._set_status(result_text)
            self._set_log_state(result_text)
            if self._failed_step is not None:
                self.btn_locate.pack(side="right", padx=(0, self.s(6)))

    def _show_summary(self, result: str) -> None:
        """日志顶部摘要条：一次运行的结论 + 打开报告入口。"""
        try:
            report = self.executor.report()
        except Exception:  # noqa: BLE001 — 报告失败不影响收尾
            return
        title = {"finished": "执行完成", "stopped": "已停止",
                 "error": "执行出错"}.get(result, result)
        parts = [title]
        if report["rows"] > 1:
            parts.append(f"{report['rows']} 行数据")
        parts.append(f"{report['steps']} 步")
        parts.append(f"失败 {report['failed']}")
        parts.append(f"耗时 {report['seconds']:g} 秒")
        self.summary_label.configure(text=" · ".join(parts))
        self.summary_frame.pack(fill="x", padx=self.s(8), pady=(0, self.s(6)),
                                before=self.log_body)

    def _hide_summary(self) -> None:
        self.summary_frame.pack_forget()

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

        3 分钟无应答按「停止」处理——对话框被关掉或界面卡住时，
        运行必须能结束，不能永远挂住。
        """
        event = threading.Event()
        box = {"answer": "stop"}
        self.ui_queue.put(("confirm", (name, index, step, event, box)))
        event.wait(180)
        return box["answer"]

    def _ask_debug(self, name: str, index: int, step: Step,
                   event: threading.Event, box: dict) -> None:
        """主线程：弹出单步调试对话框，把用户选择交回工作线程。"""
        try:
            self.deiconify()          # 运行时会最小化，先把窗口唤回
            self.lift()
            dialog = DebugStepDialog(self, name, index, step)
            answer = dialog.wait()
        except tk.TclError:
            answer = "stop"
        box["answer"] = answer or "stop"
        event.set()
        if self.running:
            self.iconify()            # 继续跑之前再最小化，别挡识别区

    def stop_run(self) -> None:
        if not self.running:
            return
        self.executor.stop()
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
                    if not task.get("enabled") or task.get("time") != current:
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

    def _open_last_report(self) -> None:
        """打开本次运行的报告文件；没有则退而打开报告目录。"""
        path = self._last_report_path
        if path and os.path.exists(path):
            try:
                os.startfile(path)                  # noqa: S606 (Windows)
                return
            except (OSError, AttributeError):
                pass
        self.open_reports_dir()

    def _notify_finish(self, result: str) -> None:
        """运行结束通知：声音 + webhook（企业微信/钉钉等机器人地址）+ 托盘气泡。"""
        if self.settings.get("sound"):
            try:
                import winsound

                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:  # noqa: BLE001
                pass
        # 窗口藏在托盘时：用气泡告知运行结论，点气泡打开窗口
        if self.tray and not self._tray_window_visible:
            title = {"finished": "执行完成", "stopped": "已停止",
                     "error": "执行出错"}.get(result, "运行结束")
            try:
                report = self.executor.report()
                text = (f"{report['steps']} 步，失败 {report['failed']}，"
                        f"耗时 {report['seconds']:g} 秒")
            except Exception:  # noqa: BLE001 — 报告失败不影响气泡
                text = "点击打开 MeowGo 查看详情"
            self.tray.notify(title, text, on_click=self._tray_show_window)
        url = (self.settings.get("webhook") or "").strip()
        if not url:
            return
        report = self.executor.report()
        text = (f"MeowGo 运行结束：{result}，"
                f"{report['steps']} 步，失败 {report['failed']}，"
                f"耗时 {report['seconds']:g} 秒")
        payload = {"msgtype": "text", "text": {"content": text}}

        def post():
            try:
                import json
                from urllib.request import Request, urlopen

                request = Request(
                    url, data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"})
                with urlopen(request, timeout=10) as response:  # noqa: S310
                    response.read()
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
                fh.write(self.log_text.get("1.0", "end"))
            self._set_status(f"日志已导出：{os.path.basename(path)}")
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)

    def open_reports_dir(self) -> None:
        """打开运行报告所在目录。"""
        try:
            os.makedirs(REPORTS_DIR, exist_ok=True)
            os.startfile(REPORTS_DIR)              # noqa: S606 (Windows)
        except (OSError, AttributeError) as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)

    def open_report_index(self) -> None:
        """打开/刷新报告索引页（reports/index.html）。"""
        try:
            from rpa.engine import write_report_index

            path = write_report_index(REPORTS_DIR)
            os.startfile(path)                     # noqa: S606 (Windows)
        except (OSError, AttributeError, ValueError) as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)

    def _check_dpi_setup(self) -> None:
        """多显示器 + 界面被放大时，提醒一次跨屏识别的风险。"""
        if self.scale == 1.0:
            return
        try:
            import ctypes

            screens = ctypes.windll.user32.GetSystemMetrics(80)  # SM_CMONITORS
        except Exception:  # noqa: BLE001
            return
        if screens > 1:
            self._log("warn",
                      "检测到多显示器：图片识别请在同一屏幕内完成，"
                      "跨屏（尤其缩放不同时）可能识别不到")

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
        self.ui_queue.put(("step", (group_name, index)))

    def _mark_step_done(self, group_name: str, index: int, step: Step,
                        ok: bool) -> None:
        """工作线程调用：单步结束，只入队。"""
        self.ui_queue.put(("step_done", (group_name, index, ok)))

    def _highlight_step(self, group_name: str, index: int) -> None:
        names = self.project.names()
        if group_name in names:
            position = names.index(group_name)
            if position != self.group_index:
                self.group_index = position
                self._refresh_groups()
                self._refresh_steps()
        self._repaint_step_marks()
        if self.step_tree.exists(str(index)):
            self.step_tree.item(str(index),
                                tags=(self._zebra_tag(index), "running"))
            self.step_tree.see(str(index))
        self._set_status(f"正在执行：{group_name} · 第 {index + 1} 步")
        if self._mini_bar is not None and group_name in names:
            group = self.project.groups[names.index(group_name)]
            if 0 <= index < len(group.steps):
                step = group.steps[index]
                summary = step.content.strip().replace("\n", " ")[:24]
                self._mini_bar.set_step(
                    f"{index + 1}. {step.label()} {summary}")

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
        for item in self.step_tree.get_children():
            index = int(item)
            # 先回到斑马纹，再按需叠运行染色（标记标签优先级更高，盖得住）
            tags = [self._zebra_tag(index)]
            mark = marks.get(index)
            if mark:
                tags.append(mark)
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

    # --------------------------------------------------------- 设置 / 帮助
    def open_settings(self) -> None:
        dialog = SettingsDialog(self, self.settings, self.image_dir)
        if dialog.wait() is None:
            return
        self.settings = dialog.result
        self.image_dir = dialog.result["image_dir"]
        self.project.image_dir = self.image_dir
        language = str(self.settings.get("language", i18n.get_language()))
        if language != i18n.get_language():
            self.set_language(language, save=False)
        self._save_state()
        self._apply_tray_settings()
        self.image_label.configure(text=f"图片目录：{self.image_dir}")

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
                  i18n.tr("Ctrl+G 新建执行组　Ctrl+Enter 新增步骤　F2 编辑步骤　"
                          "Delete 删除步骤"),
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
            f"{APP_TITLE}\n\n"
            "将原 Excel 配置迁移至客户端界面：可视化维护执行组与步骤，\n"
            "支持执行组间调用复用、屏幕截图、运行前校验、日志记录与全局停止热键。\n\n"
            "执行组自动保存于程序目录的 rpa_settings.json；\n"
            "支持 Excel 单组或全部工作表导入导出（兼容旧版 cmd.xls）。")
        messagebox.showinfo("关于", i18n.tr(message), parent=self)

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
        self._inline_recorder.stop()   # 卸载可能装着的键盘钩子
        self.project.image_dir = self.image_dir
        self._save_state()
        if self.tray:
            self.tray.stop()
        self.destroy()

    # --------------------------------------------------------------- 托盘
    def _tray_wanted(self) -> bool:
        """两个托盘开关任一打开，就该有托盘图标。"""
        return bool(self.settings.get("tray_start_minimized")
                    or self.settings.get("tray_on_close"))

    def _init_tray(self) -> bool:
        """按当前设置创建托盘图标；已创建 / 不需要 / 失败都不重复建。"""
        if self.tray is not None:
            return True
        if os.name != "nt" or not self._tray_wanted():
            return False
        icon = tray.TrayIcon(
            tip=APP_TITLE,
            on_activate=self._tray_show_window,
            menu_items=[("显示主窗口", self._tray_show_window),
                        None,
                        ("退出", self._tray_quit)],
            invoke=lambda fn: self.after(0, fn))
        if icon.start(dark=self.dark):
            self.tray = icon
            return True
        self._log("warn", "托盘图标启动失败，窗口会保持可见")
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
            self.tray.stop()
            self.tray = None
            self._log("info", "托盘图标已关闭")

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
        self._tray_window_visible = True

    def _tray_quit(self) -> None:
        if self.running and not messagebox.askyesno(
                "确认退出", "任务仍在运行，确定退出？", parent=self):
            return
        self._quit()

    def _refresh_tray(self) -> None:
        if self.tray:
            self.tray.set_icon(self.dark)

    def _on_session_query(self) -> None:
        self._session_ending = True        # 可能要关机：先记住，别躲托盘

    def _on_session_end(self, ending: bool) -> None:
        self._session_ending = bool(ending)
        if ending:
            self.after(0, self._quit)      # 真关机：立刻退，别挡系统


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


class BaseDialog(tk.Toplevel):
    """统一风格的模态对话框。"""

    def __init__(self, parent: App, title: str, width: int = 420,
                 height: int = 260):
        super().__init__(parent)
        self.app = parent
        self.result = None
        self.title(title)
        self.configure(bg=CARD)
        self.transient(parent)
        self.resizable(False, False)
        self.geometry(f"{parent.s(width)}x{parent.s(height)}")
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda e: self.cancel())
        self.after_idle(lambda: uikit.style_window(self, bool(parent.dark)))

    def fit_content(self, width: int = 0, min_height: int = 0) -> None:
        """按内容收缩窗口，去掉底部/右侧的大块空白。

        width 传物理像素；0 表示用内容请求宽度。min_height 是逻辑像素下限。
        """
        self.update_idletasks()
        w = width or max(self.winfo_reqwidth(), self.winfo_width())
        h = max(self.winfo_reqheight(), self.app.s(min_height))
        self.geometry(f"{int(w)}x{int(h)}")

    def center(self) -> None:
        self.update_idletasks()
        x = self.app.winfo_rootx() + (self.app.winfo_width() - self.winfo_width()) // 2
        y = self.app.winfo_rooty() + (self.app.winfo_height() - self.winfo_height()) // 3
        self.app.place_window(self, x, y, self.winfo_width(), self.winfo_height())
        self.wait_visibility(self)
        self.grab_set()
        self.focus_force()

    def wait(self):
        """模态阻塞：等用户关闭对话框后返回结果。

        调用方必须走这个方法拿结果，不能在构造函数返回后直接读
        .result——那时用户还没来得及输入，result 恒为 None。
        """
        self.wait_window(self)
        return self.result

    def cancel(self) -> None:
        self.result = None
        self.destroy()

    def destroy(self) -> None:
        teardown = getattr(self, "_teardown", None)
        if teardown is not None:
            try:
                teardown()             # 子类在这里释放资源（如键盘钩子）
            except Exception:
                pass
        super().destroy()

    def label(self, text: str, row: int, muted: bool = False):
        label = tk.Label(self, text=text, bg=CARD, fg=MUTED if muted else TEXT,
                         font=FONT_NOTE)
        label.grid(
            row=row, column=0, sticky="w", padx=(self.app.s(16), self.app.s(8)),
            pady=self.app.s(5))
        return label

    def footer(self, row: int, ok_text: str = "确定", ok_icon: str = "check",
               on_ok=None, cancel_text: str = "取消", ok_kind: str = "accent",
               pad_top: int = 8, pad_bottom: int = 14):
        """统一的对话框底栏：主按钮靠右，取消按钮在它左边。"""
        frame = tk.Frame(self, bg=CARD)
        frame.grid(row=row, column=0, columnspan=2, sticky="ew",
                   padx=self.app.s(16),
                   pady=(self.app.s(pad_top), self.app.s(pad_bottom)))
        ok = uikit.RoundButton(
            frame, ok_text, ok_icon, on_ok or self.ok, kind=ok_kind,
            scale=self.app.scale, bg=CARD, height=CONTROL_MD)
        ok.pack(side="right")
        cancel = None
        if cancel_text:
            cancel = uikit.RoundButton(
                frame, cancel_text, "close", self.cancel, kind="ghost",
                scale=self.app.scale, bg=CARD, height=CONTROL_MD)
            cancel.pack(side="right", padx=(0, self.app.s(8)))
        return frame, ok, cancel


class GroupDialog(BaseDialog):
    """输入 / 修改执行组名称。"""

    def __init__(self, parent: App, title: str, initial: str = ""):
        super().__init__(parent, title, width=380, height=170)
        self.label("执行组名称", 0)
        self.var = tk.StringVar(value=initial)
        entry = uikit.TextField(self, textvariable=self.var,
                                scale=parent.scale, bg=CARD,
                                font_size=FONT_SIZE_BODY)
        entry.grid(row=0, column=1, sticky="ew", padx=(0, parent.s(16)),
                   pady=parent.s(5))
        self.columnconfigure(1, weight=1)
        tk.Label(self, text="尽量简短，便于在「调用执行组」里引用",
                 bg=CARD, fg=MUTED, font=FONT_CAPTION, justify="left",
                 anchor="w", wraplength=parent.s(340)).grid(
            row=1, column=0, columnspan=2, sticky="ew",
            padx=(parent.s(16), parent.s(16)),
            pady=(0, parent.s(2)))
        self.footer(2, pad_top=6)
        entry.bind("<Return>", lambda e: self.ok())
        entry.focus_set()
        entry.select_range(0, "end")
        self.fit_content(width=parent.s(380))
        self.center()

    def ok(self) -> None:
        self.result = self.var.get().strip()
        self.destroy()


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
        if editing.type in PIPE_CONDITION_TYPES and "|" in editing.content:
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
        self.var_on_fail = tk.StringVar(
            value="跳过这步" if editing.on_fail != "stop" else "停止执行")
        self.combo_fail = uikit.LocalizedCombobox(
            row_timeout, variable=self.var_on_fail, state="readonly",
            values=["跳过这步", "停止执行"], width=9,
            font=FONT_NOTE)
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
        self.lbl_retry = tk.Label(row_match, text="失败重试", bg=CARD, fg=TEXT,
                                  font=FONT_NOTE)
        self.lbl_retry.grid(row=0, column=4, sticky="w")
        self.var_retry = tk.StringVar(value=str(editing.retry or 0))
        self.spin_retry = ttk.Spinbox(row_match, from_=0, to=9, width=4,
                                      textvariable=self.var_retry)
        self.spin_retry.grid(row=0, column=5, sticky="w", padx=(parent.s(3), 0))
        self.lbl_retry_unit = tk.Label(row_match, text="次，间隔", bg=CARD,
                                       fg=TEXT,
                                       font=FONT_NOTE)
        self.lbl_retry_unit.grid(row=0, column=6, sticky="w",
                                 padx=(parent.s(3), 0))
        self.var_retry_delay = tk.StringVar(
            value=f"{editing.retry_delay:g}")
        self.spin_retry_delay = ttk.Spinbox(
            row_match, from_=0, to=60, increment=0.1, width=5,
            textvariable=self.var_retry_delay)
        self.spin_retry_delay.grid(row=0, column=7, sticky="w",
                                   padx=(parent.s(3), 0))
        self.lbl_retry_sec = tk.Label(row_match, text="秒", bg=CARD, fg=TEXT,
                                      font=FONT_NOTE)
        self.lbl_retry_sec.grid(row=0, column=8, sticky="w",
                                padx=(parent.s(2), 0))

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

        self._adv_timeout = [self.lbl_timeout, row_timeout]
        self._adv_fail = [self.lbl_fail, self.combo_fail]
        self._adv_image = [self.lbl_region, row_region]
        self._adv_nth = [self.lbl_nth, self.spin_nth,
                         self.lbl_nth_suffix, self.gray_box]
        self._adv_match = [self.lbl_match, row_match]
        self._adv_other = [self.lbl_other, row_other]

        self.label("备注", 6)
        self.var_note = tk.StringVar(value=editing.note)
        self.note_field = uikit.TextField(
            self, textvariable=self.var_note, scale=parent.scale, bg=CARD,
            font_size=FONT_SIZE_NOTE)
        self.note_field.grid(row=6, column=1, sticky="ew",
                             padx=(0, parent.s(16)), pady=parent.s(5))
        # 兼容既有调用/测试：note_entry 指向真正接收键盘焦点的 Entry。
        self.note_entry = self.note_field.entry

        _, self.btn_ok, self.btn_cancel = self.footer(7)

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
                    message = "当前屏幕未找到匹配图像（请确认目标窗口已打开且缩放比例一致）"
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
        if key in ("click_xy", "move_xy"):
            text += "\n也可将鼠标移至目标位置，点击「拾取」自动填入坐标。"
        if key == "input":
            text += ("\n默认通过剪贴板粘贴（支持中文）。"
                     "\n若个别应用不支持粘贴，可在「高级选项」中改为逐字输入。")
        if needs_image:
            text += ("\n截图或选图后，请点击「预览」核对内容。"
                     "\n若定位有误，可在「高级选项」中框选识别范围或指定第几个匹配项。")
        elif key in CONDITION_TYPES:
            text += "\n可在「高级选项」中设置最长等待时间。"
        self._show_hint(text)
        is_image = key in IMAGE_TYPES
        is_hotkey = key == "hotkey"
        is_else = key == "if_else"
        is_if = key in PIPE_CONDITION_TYPES or is_else
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
            self.lbl_group.configure(text="出现时执行" if is_else
                                     else "满足后执行")
            anchor = self.action_row if has_actions else self.entry
            self.group_box.pack(fill="x", after=anchor)
        else:
            self.group_box.pack_forget()
        # if_else 的第三段（没出现时执行的组）单独一行
        for widget in (self.label_group2, self.group2_entry,
                       self.btn_pick_group2):
            widget.grid() if is_else else widget.grid_remove()
        # 等待策略：图片 / 拖拽 / 条件（最多等几秒）
        has_timeout = is_image or key == "drag" or key in CONDITION_TYPES
        for widget in self._adv_timeout:
            widget.grid() if has_timeout else widget.grid_remove()
        try:
            timeout = float(self.var_timeout.get() or 0)
        except ValueError:
            timeout = 0
        show_fail = (is_image or key == "drag") and timeout > 0
        for widget in self._adv_fail:
            widget.grid() if show_fail else widget.grid_remove()
        # 识别范围 / 第几个 / 灰度只对图片相关类型有意义
        for widget in self._adv_image:
            widget.grid() if needs_image else widget.grid_remove()
        for widget in self._adv_nth:
            widget.grid() if needs_image else widget.grid_remove()
        self.lbl_match.configure(text="匹配 / 重试" if needs_image else "重试")
        for widget in self._adv_match:
            widget.grid()
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
        if key in PIPE_CONDITION_TYPES:
            content = f"{self.var_content.get().strip()}|" \
                      f"{self.var_group.get().strip()}"
        elif key == "if_else":
            content = "|".join([self.var_content.get().strip(),
                                self.var_group.get().strip(),
                                self.var_group2.get().strip()])
        else:
            content = self.var_content.get()
        step = Step(type=key, content=content, repeat=0.0,
                    note=self.var_note.get())
        if STEP_TYPES[step.type][2]:
            try:
                step.repeat = float(self.var_repeat.get() or 0)
            except ValueError:
                messagebox.showwarning("格式错误", "重复次数必须是数字", parent=self)
                return
        has_timeout = (key in IMAGE_TYPES or key == "drag"
                       or key in CONDITION_TYPES)
        if has_timeout:
            try:
                step.timeout = float(self.var_timeout.get() or 0)
            except ValueError:
                messagebox.showwarning("格式错误", "等待超时必须是数字",
                                       parent=self)
                return
        if key in IMAGE_TYPES or key == "drag":
            step.on_fail = "stop" if self.var_on_fail.get() == "停止执行" \
                else "skip"
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
                command=lambda n=name: (self.var_group.set(n),
                                        self._show_hint(f"将执行组：{n}")))
        x = self.btn_pick_group.winfo_rootx()
        y = self.btn_pick_group.winfo_rooty() + self.btn_pick_group.winfo_height()
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
                command=lambda n=name: (self.var_group2.set(n),
                                        self._show_hint(f"没出现时将执行组：{n}")))
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
            image.thumbnail(show, Image.LANCZOS)
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
        self.var_sound = tk.BooleanVar(value=bool(settings.get("sound")))
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
            time_label = tk.Label(row, text=task.get("time", "09:00"),
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
            for widget in (row, time_label, group_label):
                widget.bind("<Button-1>", lambda _e, i=index: self._select(i))
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
        name = (f"{task.get('time', '--:--')} · "
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

        row_label("每日运行时间", 2)
        row_time = tk.Frame(right, bg=CARD)
        row_time.grid(row=2, column=1, sticky="w", pady=app.s(5))
        self.spin_hour = ttk.Spinbox(
            row_time, from_=0, to=23, width=4, wrap=True, format="%02.0f",
            textvariable=self.var_hour)
        self.spin_hour.pack(side="left")
        tk.Label(row_time, text=":", bg=CARD, fg=TEXT,
                 font=FONT_NOTE).pack(side="left", padx=app.s(2))
        self.spin_minute = ttk.Spinbox(
            row_time, from_=0, to=59, width=4, wrap=True, format="%02.0f",
            textvariable=self.var_minute)
        self.spin_minute.pack(side="left")
        tk.Label(row_time, text="时 : 分", bg=CARD, fg=MUTED,
                 font=FONT_NOTE).pack(side="left", padx=app.s(6))

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

        row_label("Webhook", 6)
        uikit.TextField(right, textvariable=self.var_webhook,
                        scale=app.scale, bg=CARD,
                        font_size=FONT_SIZE_NOTE).grid(
            row=6, column=1, sticky="ew", pady=app.s(5))
        tk.Label(right, text="填写企业微信或钉钉机器人地址，任务结束后自动推送；"
                             "留空则不推送。完成通知由所有定时任务共用。",
                 bg=CARD, fg=MUTED, font=FONT_CAPTION,
                 wraplength=app.s(445), justify="left", anchor="w").grid(
            row=7, column=0, columnspan=2, sticky="ew",
            pady=(0, app.s(2)))

        tk.Frame(right, bg=BORDER, height=1).grid(
            row=8, column=0, columnspan=2, sticky="ew", pady=app.s(9))
        row_label("无人值守运行", 9)
        tk.Label(right, text="由 Windows 任务计划程序执行，客户端关闭后仍会按时运行。"
                             "\n无需管理员权限；创建后可在「任务计划程序」中查看。",
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
        self.lbl_task = tk.Label(right, text="", bg=CARD, fg=MUTED,
                                 font=FONT_CAPTION,
                                 wraplength=app.s(445), justify="left",
                                 anchor="w")
        self.lbl_task.grid(row=11, column=0, columnspan=2, sticky="ew",
                           pady=(app.s(4), 0))

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

    def _set_editor_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        combo_state = "readonly" if enabled else "disabled"
        for spin in (self.spin_hour, self.spin_minute):
            spin.configure(state=state)
        self.group_combo.configure(state=combo_state)
        self.btn_task_create.set_enabled(enabled)
        self.btn_task_delete.set_enabled(enabled)
        self.btn_delete_task.set_enabled(enabled)

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
        task["time"] = f"{hour:02d}:{minute:02d}"
        task["group"] = "" if group in i18n.variants("（当前组）") else group

    # -- 保存 ---------------------------------------------------------------
    def ok(self) -> None:
        self._commit_current()
        schedules = [{
            "id": str(task.get("id") or f"task-{index + 1}"),
            "enabled": bool(task.get("enabled")),
            "time": _valid_schedule_time(task.get("time")),
            "group": task.get("group") or "",
        } for index, task in enumerate(self._tasks)]
        legacy = ({key: schedules[0][key]
                   for key in ("enabled", "time", "group")}
                  if schedules else {})
        self.result = {
            "schedules": schedules,
            "schedule": legacy,
            "sound": bool(self.var_sound.get()),
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

    def _task_group(self) -> str:
        group = self.var_group.get()
        if group == "（当前组）":
            group = self.app._current_group_name()
        return (group or "").strip()

    def _task_name(self, group: str) -> str:
        safe = re.sub(r'[\\/:*?"<>|\s]+', "_", group).strip("_")[:50]
        return f"MeowGo_{safe or '当前组'}"

    def _task_command(self, group: str) -> str:
        """计划任务要执行的命令行（无窗口，跑完自动退出）。"""
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}" --run "{group}"'
        python = sys.executable
        pythonw = os.path.join(os.path.dirname(python), "pythonw.exe")
        if os.path.exists(pythonw):
            python = pythonw
        script = os.path.join(APP_DIR, "start-app.py")
        return f'"{python}" "{script}" --run "{group}"'

    def _schtasks(self, args: list[str]):
        kwargs = {"capture_output": True, "text": True, "errors": "replace"}
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
        command = self._task_command(group)
        try:
            # 计划任务启动时读磁盘上的配置，先确保当前项目已保存
            self.app._save_state()
            proc = self._schtasks([
                "/Create", "/SC", "DAILY", "/ST", f"{hour:02d}:{minute:02d}",
                "/TN", name, "/TR", command, "/F"])
        except (OSError, AttributeError) as exc:
            messagebox.showerror("创建失败", f"调用 schtasks 失败：{exc}",
                                 parent=self)
            return
        if proc.returncode == 0:
            text = f"已创建：{name}（每天 {hour:02d}:{minute:02d} → {group}）"
            self.lbl_task.configure(text=text, fg=SUCCESS)
            self.app._log("info", f"Windows 计划任务已创建：{name}")
        else:
            detail = (proc.stderr or proc.stdout or "").strip()[:500]
            self.lbl_task.configure(text="创建失败：" + detail,
                                    fg=DANGER)
            messagebox.showerror("创建失败", detail or "schtasks 返回错误",
                                 parent=self)

    def _delete_task_plan(self) -> None:
        group = self._task_group()
        name = self._task_name(group)
        if not messagebox.askyesno(
                "删除计划任务",
                f"确定删除 Windows 计划任务「{name}」？\n\n"
                "只删除系统计划任务，不影响软件里的定时设置。",
                parent=self):
            return
        try:
            proc = self._schtasks(["/Delete", "/TN", name, "/F"])
        except (OSError, AttributeError) as exc:
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
        self._after_id = None
        self._poll_id = None
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
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=LOG_BG)
        s = parent.s
        width, height = s(320), s(58)
        x = (self.win.winfo_screenwidth() - width) // 2
        self.win.geometry(f"{width}x{height}+{x}+{s(8)}")

        box = tk.Frame(self.win, bg=LOG_BG)
        box.pack(fill="both", expand=True, padx=s(12), pady=s(7))
        self.counts = tk.Label(box, text="准备开始", bg=LOG_BG,
                               fg=ACCENT,
                               font=FONT_NOTE_BOLD)
        self.counts.pack(anchor="w")
        self.text = tk.Label(box, text="", bg=LOG_BG, fg=LOG_FG,
                             font=FONT_NOTE)
        self.text.pack(anchor="w")
        self.bar = ttk.Progressbar(box, style="Mini.Horizontal.TProgressbar",
                                   mode="determinate", maximum=100,
                                   length=width - s(24))
        self.bar.pack(fill="x", pady=(s(4), 0))
        for widget in (self.win, box, self.counts, self.text, self.bar):
            widget.bind("<Button-1>", self._restore)
        self.win.bind("<Escape>", self._restore)

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
        self.text.configure(text=text[:42] + ("…" if len(text) > 42 else ""))

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
            os.startfile(self.image_dir)              # noqa: S606 (Windows)
        except (OSError, AttributeError) as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)

    def _rename(self) -> None:
        name = self._selected()
        if not name:
            return
        dialog = GroupDialog(self, f"重命名图片 {name}", name)
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


class BackupDialog(BaseDialog):
    """列出一阵阵配置备份，选一个恢复。"""

    def __init__(self, parent: App, names: list[str]):
        super().__init__(parent, "恢复配置备份", width=420, height=430)
        self.names = names
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.listbox = tk.Listbox(
            self, font=FONT_NOTE, bg=ENTRY_BG, fg=TEXT,
            selectbackground=SELECT_BG, selectforeground=TEXT,
            relief="flat", highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=FIELD_FOCUS,
            activestyle="none")
        self.listbox.grid(row=0, column=0, sticky="nsew",
                          padx=(parent.s(12), 0), pady=(parent.s(12), 0))
        scroll = uikit.SlimScrollbar(
            self, orient="vertical", command=self.listbox.yview,
            scale=parent.scale, bg=CARD)
        scroll.grid(row=0, column=1, sticky="ns", pady=(parent.s(12), 0),
                    padx=(parent.s(2), 0))
        self.listbox.configure(yscrollcommand=scroll.set)
        for name in names:
            # rpa_settings_20261020_121500.json → 2026-10-20 12:15:00
            stamp = name[len("rpa_settings_"):-len(".json")]
            pretty = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} " \
                     f"{stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}"
            self.listbox.insert("end", pretty)
        if names:
            self.listbox.selection_set(0)

        self.footer(1, "恢复选中", "check", self.ok,
                    pad_top=6, pad_bottom=12)
        self.listbox.bind("<Double-1>", lambda e: self.ok())
        self.center()

    def ok(self) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        self.result = self.names[selection[0]]
        self.destroy()


class SettingsDialog(BaseDialog):
    """运行设置：倒计时、相似度、图片目录、开关项、开机自启与托盘。"""

    def __init__(self, parent: App, settings: dict, image_dir: str):
        super().__init__(parent, "运行设置", width=470, height=470)
        self.columnconfigure(1, weight=1)
        # 用传进来的设置做底稿，保存时只改本对话框的键，别冲掉定时等配置
        self._base_settings = dict(settings)

        self.label("界面语言", 0)
        self.var_language = tk.StringVar(
            value=i18n.language_display(str(settings.get("language", "zh"))))
        row_lang = tk.Frame(self, bg=CARD)
        row_lang.grid(row=0, column=1, sticky="e",
                      padx=(0, parent.s(16)), pady=parent.s(5))
        self.combo_language = uikit.ReadonlySelect(
            row_lang,
            values=[i18n.language_display(code) for code, _name in i18n.LANGUAGES],
            variable=self.var_language, scale=parent.scale, bg=CARD,
            font_size=FONT_SIZE_NOTE, width=20)
        self.combo_language.pack(side="left")

        self.label("启动前倒计时（秒）", 1)
        self.var_delay = tk.StringVar(value=str(settings.get("delay", 3)))
        ttk.Spinbox(self, from_=0, to=30, width=8,
                    textvariable=self.var_delay).grid(
            row=1, column=1, sticky="e", padx=(0, parent.s(16)), pady=parent.s(5))

        self.label("图像匹配相似度", 2)
        self.var_conf = tk.StringVar(value=str(settings.get("confidence", 0.9)))
        ttk.Spinbox(self, from_=0.5, to=1.0, increment=0.01, width=8,
                    textvariable=self.var_conf).grid(
            row=2, column=1, sticky="e", padx=(0, parent.s(16)), pady=parent.s(5))

        self.label("图像目录", 3)
        row = tk.Frame(self, bg=CARD)
        row.grid(row=3, column=1, sticky="ew", padx=(0, parent.s(16)),
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

        self.label("运行时显示悬浮进度条", 4)
        self.var_mini = tk.BooleanVar(
            value=bool(settings.get("show_mini_bar", True)))
        uikit.Switch(self, self.var_mini, scale=parent.scale, bg=CARD).grid(
            row=4, column=1, sticky="e", padx=(0, parent.s(16)),
            pady=parent.s(3))

        self.label("失败时保存屏幕截图", 5)
        self.var_shot = tk.BooleanVar(
            value=bool(settings.get("shot_on_fail", True)))
        uikit.Switch(self, self.var_shot, scale=parent.scale, bg=CARD).grid(
            row=5, column=1, sticky="e", padx=(0, parent.s(16)),
            pady=parent.s(3))

        self.label("数据处理完成后写入结果文件", 6)
        self.var_results = tk.BooleanVar(
            value=bool(settings.get("save_results", True)))
        uikit.Switch(self, self.var_results, scale=parent.scale, bg=CARD).grid(
            row=6, column=1, sticky="e", padx=(0, parent.s(16)),
            pady=parent.s(3))

        # 开机自启：开关状态直接反映注册表实况（Run 项在不在），
        # 不在 settings 里另存一份“真相”，避免两边不一致
        self.label("开机时自动启动客户端", 7)
        self.var_autostart = tk.BooleanVar(value=autostart.is_enabled())
        uikit.Switch(self, self.var_autostart, scale=parent.scale,
                     bg=CARD).grid(
            row=7, column=1, sticky="e", padx=(0, parent.s(16)),
            pady=parent.s(3))

        # 托盘：两个开关任一打开就会出现托盘图标；互不依赖
        self.label("启动后最小化至系统托盘", 8)
        self.var_tray_min = tk.BooleanVar(
            value=bool(settings.get("tray_start_minimized")))
        uikit.Switch(self, self.var_tray_min, scale=parent.scale,
                     bg=CARD).grid(
            row=8, column=1, sticky="e", padx=(0, parent.s(16)),
            pady=parent.s(3))

        self.label("关闭窗口时最小化至系统托盘", 9)
        self.var_tray_close = tk.BooleanVar(
            value=bool(settings.get("tray_on_close")))
        uikit.Switch(self, self.var_tray_close, scale=parent.scale,
                     bg=CARD).grid(
            row=9, column=1, sticky="e", padx=(0, parent.s(16)),
            pady=parent.s(3))

        # 说明文字横跨两列并自动换行，避免把左侧标签列撑得过宽
        tk.Label(self, text="相似度阈值越低，识别范围越宽，误匹配风险也越高。"
                            "失败截图保存于 reports/shots；数据结果写入数据文件"
                            "所在目录。再次运行同一数据文件时，可选择从上次中断"
                            "处继续。",
                 bg=CARD, fg=MUTED, font=FONT_CAPTION,
                 wraplength=parent.s(430), justify="left", anchor="w").grid(
            row=10, column=0, columnspan=2, sticky="ew",
            padx=parent.s(16), pady=(parent.s(3), 0))
        tk.Label(self, text="开机自启通过当前用户注册表 Run 项实现，无需管理员"
                            "权限。登录 Windows 后仅启动客户端，不自动执行流程；"
                            "如需定时无人值守运行，请使用「定时运行 → 创建计划"
                            "任务」。启动命令：" + autostart.launch_command(),
                 bg=CARD, fg=MUTED, font=FONT_CAPTION,
                 wraplength=parent.s(430), justify="left", anchor="w").grid(
            row=11, column=0, columnspan=2, sticky="ew",
            padx=parent.s(16), pady=(parent.s(3), 0))
        tk.Label(self, text="托盘选项：启用后，启动或关闭客户端只会隐藏窗口，"
                            "系统托盘将显示 MeowGo 图标。双击图标可重新打开窗口，"
                            "右键菜单可退出客户端。开机自启与启动后最小化至托盘"
                            "同时启用时，客户端将在登录后静默驻留后台；关闭这两个"
                            "选项并保存后，托盘图标将消失。",
                 bg=CARD, fg=MUTED, font=FONT_CAPTION,
                 wraplength=parent.s(430), justify="left", anchor="w").grid(
            row=12, column=0, columnspan=2, sticky="ew",
            padx=parent.s(16), pady=(parent.s(3), 0))

        self.footer(13, "保存", "check", self.ok, pad_top=10)
        self.fit_content(width=parent.s(470))
        self.center()

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
        self.result = dict(self._base_settings)
        self.result.update({
            "language": i18n.language_code(self.var_language.get()),
            "delay": int(number(self.var_delay.get(), 3)),
            "confidence": number(self.var_conf.get(), 0.9),
            "image_dir": directory,
            "show_mini_bar": bool(self.var_mini.get()),
            "shot_on_fail": bool(self.var_shot.get()),
            "save_results": bool(self.var_results.get()),
            "autostart": bool(self.var_autostart.get()),
            "tray_start_minimized": bool(self.var_tray_min.get()),
            "tray_on_close": bool(self.var_tray_close.get()),
        })
        self.destroy()

    def _apply_autostart(self) -> bool:
        """把「开机自动启动」开关同步到注册表 Run 项。

        返回 False 表示失败（已弹错误框），调用方应保持对话框不关闭。
        程序被整体搬家后，已注册的命令会过期——重新注册一次即可自愈。
        """
        want = bool(self.var_autostart.get())
        have = autostart.is_enabled()
        stale = (have
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
            self.app._log("info", "开机自启已关闭（已移除注册表启动项）")
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
    """单步调试：执行下一步之前问一句（运行 / 跳过 / 停止）。

    由 _ask_debug 在主线程弹出；wait() 的返回值会经 Event 交回工作线程，
    据此决定这一步是执行、跳过还是终止整个运行。
    """

    def __init__(self, parent: App, group_name: str, index: int, step: Step):
        super().__init__(parent, "单步调试", width=470, height=270)
        self.attributes("-topmost", True)
        self.columnconfigure(0, weight=1)

        self.label(f"执行组「{group_name}」· 第 {index + 1} 步", 0)
        content = (step.content or "").strip() or "（无内容）"
        detail = tk.Label(self, text=f"{step.label()}：{content}", bg=CARD,
                          fg=TEXT, font=FONT_BODY_BOLD,
                          wraplength=parent.s(410), justify="left")
        detail.grid(row=1, column=0, sticky="w", padx=parent.s(16),
                    pady=(parent.s(2), parent.s(6)))
        if step.repeat and step.repeat != 1:
            tk.Label(self, text=f"重复 {step.repeat:g} 次", bg=CARD, fg=MUTED,
                     font=FONT_CAPTION).grid(
                row=2, column=0, sticky="w", padx=parent.s(16))
        tk.Label(self, text="运行：执行这一步　·　跳过：不执行　·　"
                            "停止：结束整个运行（Esc）", bg=CARD, fg=MUTED,
                 font=FONT_CAPTION).grid(
            row=3, column=0, sticky="w", padx=parent.s(16),
            pady=(parent.s(8), 0))

        buttons = tk.Frame(self, bg=CARD)
        buttons.grid(row=4, column=0, sticky="ew", padx=parent.s(16),
                     pady=(parent.s(12), parent.s(14)))
        uikit.RoundButton(buttons, "停止", "stop", self._stop,
                          kind="danger", scale=parent.scale, bg=CARD,
                          height=CONTROL_MD).pack(side="left")
        uikit.RoundButton(buttons, "运行这一步", "run", self._run,
                          kind="accent", scale=parent.scale, bg=CARD,
                          height=CONTROL_MD).pack(side="right")
        uikit.RoundButton(buttons, "跳过", "debug", self._skip,
                          kind="outline", scale=parent.scale, bg=CARD,
                          height=CONTROL_MD).pack(side="right",
                                                   padx=(0, parent.s(8)))
        self.fit_content(width=parent.s(470))
        self.center()

    def _run(self) -> None:
        self.result = "run"
        self.destroy()

    def _skip(self) -> None:
        self.result = "skip"
        self.destroy()

    def _stop(self) -> None:
        self.result = "stop"
        self.destroy()

    def cancel(self) -> None:
        self.result = "stop"          # 关对话框 = 停止运行
        self.destroy()


class ShortcutsDialog(BaseDialog):
    """快捷键速查卡（右下角「?」按钮）。"""

    ROWS = [
        ("Ctrl+G", "新建执行组"),
        ("Ctrl+Enter", "新增步骤"),
        ("Ctrl+A", "全选当前组的步骤（可批量删除/复制/移动）"),
        ("F2", "编辑选中的步骤"),
        ("Delete", "删除选中的步骤"),
        ("F5", "运行当前组（全局，任何时候有效）"),
        ("F8", "单步调试开关（每步执行前确认）"),
        ("F9", "停止（全局，任何时候有效）"),
        ("Ctrl+D", "深色 / 浅色主题切换"),
        ("Ctrl+Z / Ctrl+Y", "撤销 / 重做"),
        ("?", "打开本速查卡"),
        ("Esc", "关闭对话框 / 取消倒计时"),
    ]

    def __init__(self, parent: App):
        super().__init__(parent, "快捷键速查", width=440, height=470)
        self.columnconfigure(1, weight=1)
        for row, (key, text) in enumerate(self.ROWS):
            tk.Label(self, text=key, bg=CARD, fg=ACCENT,
                     font=FONT_MONO).grid(
                row=row, column=0, sticky="w", padx=(parent.s(18), parent.s(10)),
                pady=parent.s(4))
            tk.Label(self, text=text, bg=CARD, fg=TEXT,
                     font=FONT_NOTE).grid(
                row=row, column=1, sticky="w")
        tk.Label(self, text="运行安全：将鼠标快速移至屏幕左上角，"
                            "可立即中断执行。", bg=CARD, fg=MUTED,
                 font=FONT_CAPTION,
                 wraplength=parent.s(390), justify="left").grid(
            row=len(self.ROWS), column=0, columnspan=2, sticky="w",
            padx=parent.s(18), pady=(parent.s(10), 0))
        self.footer(len(self.ROWS) + 1, "知道了", "check", self.ok,
                    cancel_text="", pad_top=10)
        self.fit_content(width=parent.s(440))
        self.center()

    def ok(self) -> None:
        self.result = True
        self.destroy()


def main() -> None:
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
        App().mainloop()
    finally:
        lock.release()


if __name__ == "__main__":
    enable_dpi_awareness()
    main()
