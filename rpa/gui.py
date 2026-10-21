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
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional

from . import assets, uikit
from .engine import (CONDITION_TYPES, IMAGE_STEP_TYPES, IMAGE_TYPES,
                     STEP_TYPES, Executor, Group, Project, Step,
                     estimate_steps, export_excel, find_images, import_excel,
                     parse_region, validate_project)
from .lock import InstanceLock, make_lock_name

APP_TITLE = "MeowGo"


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

# 配色（默认浅色；_apply_theme 会整组换血并重染界面）
BG = "#f4f6fa"
CARD = "#ffffff"
ACCENT = "#3b6ff5"
TEXT = "#22262e"
MUTED = "#7a8290"
BORDER = "#e2e7f0"
LOG_BG = "#1e2228"
LOG_FG = "#d7dce3"
ZEBRA = "#f5f8fd"                 # 步骤表斑马纹（奇数行）
HEADING_BG = "#eef2f8"            # 表头
HEADING_FG = "#66708a"
SELECT_BG = "#dbe7ff"             # 树控件选中色
ENTRY_BG = "white"                # 输入框底色
SCROLL_BG = "#ccd3e0"
SCROLL_TROUGH = "#ffffff"
PROGRESS_TROUGH = "#e8edf6"
PREVIEW_BG = "#f8fafd"            # 图片预览窗格
SUMMARY_BG = "#eef3ff"            # 运行摘要条
SUMMARY_BORDER = "#c9d9f8"
MARK_RUNNING = "#fff4d6"          # 运行染色：正在执行
MARK_DONE = "#eaf6ec"             # 成功
MARK_FAILED = "#fdecec"           # 失败

# 主题色板：_apply_theme 按这张表换模块常量 + 重染控件
LIGHT_PALETTE = {
    "BG": BG, "CARD": CARD, "ACCENT": ACCENT, "TEXT": TEXT, "MUTED": MUTED,
    "BORDER": BORDER, "LOG_BG": LOG_BG, "LOG_FG": LOG_FG, "ZEBRA": ZEBRA,
    "HEADING_BG": HEADING_BG, "HEADING_FG": HEADING_FG, "SELECT_BG": SELECT_BG,
    "ENTRY_BG": ENTRY_BG, "SCROLL_BG": SCROLL_BG,
    "SCROLL_TROUGH": SCROLL_TROUGH, "PROGRESS_TROUGH": PROGRESS_TROUGH,
    "PREVIEW_BG": PREVIEW_BG, "SUMMARY_BG": SUMMARY_BG,
    "SUMMARY_BORDER": SUMMARY_BORDER, "MARK_RUNNING": MARK_RUNNING,
    "MARK_DONE": MARK_DONE, "MARK_FAILED": MARK_FAILED,
}
DARK_PALETTE = {
    "BG": "#171b22", "CARD": "#1f242e", "ACCENT": "#5b8cff",
    "TEXT": "#e6eaf2", "MUTED": "#8b95a5", "BORDER": "#2e3542",
    "LOG_BG": "#101318", "LOG_FG": "#c9d1dc", "ZEBRA": "#232935",
    "HEADING_BG": "#262d3a", "HEADING_FG": "#93a0b4", "SELECT_BG": "#2e3f5f",
    "ENTRY_BG": "#262d3a", "SCROLL_BG": "#39414f", "SCROLL_TROUGH": "#1f242e",
    "PROGRESS_TROUGH": "#2a313c", "PREVIEW_BG": "#232935",
    "SUMMARY_BG": "#1d2635", "SUMMARY_BORDER": "#31405c",
    "MARK_RUNNING": "#3a3324", "MARK_DONE": "#22332a", "MARK_FAILED": "#3a2628",
}

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

# 每种步骤类型的图标色调：鼠标操作蓝、文本紫、等待橙、键盘青、条件棕
TYPE_COLORS = {
    "click": "#3b6ff5", "dblclick": "#3b6ff5", "rightclick": "#3b6ff5",
    "click_xy": "#3b6ff5", "move_xy": "#3b6ff5", "drag": "#3b6ff5",
    "input": "#8a5cf6", "time": "#8a5cf6", "set": "#c2557a", "copy": "#c2557a",
    "wait": "#e0a83c", "scroll": "#e0a83c",
    "hotkey": "#0e9f9f", "cmd": "#0e9f9f",
    "call": "#66708a",
    "if_image": "#e2683c", "if_not_image": "#e2683c", "if_else": "#e2683c",
    "if_window": "#e2683c", "window": "#e2683c",
}
TYPE_ICON_COLOR = "#5a6478"
# 深色下把类型图标提亮一档，避免深底看不清（_apply_theme 时整组替换）
TYPE_COLORS_DARK = dict(TYPE_COLORS)
LIGHT_TYPE_COLORS = dict(TYPE_COLORS)   # 留底：切回浅色时整组还原
TYPE_COLORS_DARK.update({
    "click": "#6f9bff", "dblclick": "#6f9bff", "rightclick": "#6f9bff",
    "click_xy": "#6f9bff", "move_xy": "#6f9bff", "drag": "#6f9bff",
    "input": "#a98cf0", "time": "#a98cf0", "set": "#e07b9c", "copy": "#e07b9c",
    "wait": "#f0bc5e", "scroll": "#f0bc5e",
    "hotkey": "#3fc4c4", "cmd": "#3fc4c4",
    "call": "#8b95a5",
    "if_image": "#f08a5d", "if_not_image": "#f08a5d", "if_else": "#f08a5d",
    "if_window": "#f08a5d", "window": "#f08a5d",
})

# 步骤搜索框的占位文字（失焦为空时显示，不计入过滤条件）
SEARCH_PLACEHOLDER = "搜索步骤…"


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


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.scale = self._init_scaling()
        self.geometry(f"{self.s(1180)}x{self.s(760)}")
        self.minsize(self.s(1000), self.s(640))
        self.configure(bg=BG)

        self.settings = self._load_settings()
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
        self._build_toolbar()
        self._build_statusbar()
        self._build_log()
        self._build_body()
        self._register_right_click()
        self._bind_shortcuts()
        self._restore_ui_state()
        self._apply_theme(self.dark)

        if not self.project.groups:
            self.project.groups.append(Group(self.project.unique_name("默认组")))
        self._refresh_all(select_group=0)
        self._start_global_hotkey()
        self._warm_imports()
        self.after(120, self._drain_queue)
        self.after(2000, self._autosave_tick)
        self.after(1000, self._schedule_tick)
        self._check_dpi_setup()
        self._log("info", f"欢迎使用 {APP_TITLE}")
        self._log("muted", "左侧维护执行组，右侧编辑步骤；「调用执行组」指令可复用其它组。")

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

    def pic(self, name: str, size: int = 16, color: str = "#33405a"):
        key = (name, self.s(size), color)
        if key not in self._icons:
            self._icons[key] = uikit.icon(name, self.s(size), color)
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
                        font=("Microsoft YaHei UI", 9))
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Card.TLabel", background=CARD, foreground=TEXT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("Treeview", background=CARD, fieldbackground=CARD,
                        rowheight=self.s(27), borderwidth=0,
                        font=("Microsoft YaHei UI", 9))
        style.configure("Treeview.Heading", background=HEADING_BG,
                        foreground=HEADING_FG,
                        font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Treeview", background=[("selected", SELECT_BG)],
                  foreground=[("selected", TEXT)])
        style.configure("TEntry", fieldbackground=ENTRY_BG,
                        foreground=TEXT, padding=self.s(4),
                        bordercolor=BORDER)
        style.configure("TCombobox", padding=self.s(3))
        style.configure("TSpinbox", padding=self.s(3))
        style.configure("Vertical.TScrollbar", background=SCROLL_BG,
                        troughcolor=SCROLL_TROUGH, bordercolor=CARD,
                        arrowcolor="#8d97ab", width=self.s(11))
        # 进度条：主窗口细条 + 迷你置顶条里的深色款
        style.configure("Accent.Horizontal.TProgressbar",
                        background=ACCENT, troughcolor=PROGRESS_TROUGH,
                        borderwidth=0, thickness=self.s(6))
        style.configure("Mini.Horizontal.TProgressbar",
                        background="#4f8cff", troughcolor="#2c313a",
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
                                       height=30, pad=self.s(11), font_size=9)
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
        header = tk.Frame(self, bg=CARD, height=self.s(46))
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Frame(header, bg=BORDER, height=1).pack(side="bottom", fill="x")

        left = tk.Frame(header, bg=CARD)
        left.pack(side="left", padx=(self.s(16), 0))
        logo = self.app_icon(self.s(24))     # 用程序自带的默认图标
        if logo is not None:
            tk.Label(left, image=logo, bg=CARD).pack(
                side="left", pady=self.s(11), padx=(0, self.s(2)))
        tk.Label(left, text=APP_TITLE, bg=CARD, fg=TEXT,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(
            side="left", padx=(self.s(7), self.s(14)))

        self._build_menubar(left)

        self.autosave_label = tk.Label(
            header, text="执行组自动保存", bg=CARD, fg=MUTED,
            font=("Microsoft YaHei UI", 8))
        self.autosave_label.pack(side="right", padx=self.s(18))

    def _build_toolbar(self) -> None:
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=self.s(12), pady=(self.s(8), self.s(4)))

        def add(text, icon, cmd, kind="solid", pad=self.s(4), side="left",
                tooltip=""):
            button = uikit.RoundButton(bar, text, icon, cmd, kind=kind,
                                       scale=self.scale, bg=BG, tooltip=tooltip)
            button.pack(side=side, padx=pad)
            return button

        # 导入 / 导出 Excel 走菜单栏「文件」，工具栏不再重复放按钮
        self.btn_settings = add("设置", "sliders", self.open_settings,
                                kind="ghost", tooltip="运行设置")
        self.btn_new_group = add("＋ 新建组", "add", self.add_group,
                                 kind="ghost", tooltip="新建执行组（Ctrl+G）")
        self.btn_undo = add("", "undo", self.undo, kind="ghost",
                            pad=self.s(2), tooltip="撤销（Ctrl+Z）")
        self.btn_redo = add("", "redo", self.redo, kind="ghost",
                            pad=self.s(2), tooltip="重做（Ctrl+Y）")
        self.btn_undo.set_enabled(False)
        self.btn_redo.set_enabled(False)
        self.btn_debug = add("单步", "debug", self.toggle_debug, kind="ghost",
                             tooltip="单步调试：每步执行前确认（F8）")
        if self.debug_mode:
            self.btn_debug.set_kind("solid")

        self.btn_stop = add("停止", "stop", self.stop_run, kind="danger",
                            side="right", tooltip="停止（全局热键 F9）")
        self.btn_pause = add("暂停", "pause", self.toggle_pause, side="right",
                             tooltip="暂停 / 继续")
        self.btn_loop = add("循环", "loop", self.run_loop, side="right",
                            tooltip="循环运行当前组")
        self.btn_run = add("运行组", "run", self.run_group, kind="accent",
                           side="right", tooltip="运行当前组 F5")
        self.btn_stop.set_enabled(False)
        self.btn_pause.set_enabled(False)

    def _build_body(self) -> None:
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=self.s(12))

        self.paned = ttk.PanedWindow(body, orient="horizontal")
        self.paned.pack(fill="both", expand=True)

        self._build_group_panel(self.paned)
        self._build_step_panel(self.paned)

    def _build_group_panel(self, parent) -> None:
        panel = tk.Frame(parent, bg=CARD, highlightthickness=1,
                         highlightbackground=BORDER)
        parent.add(panel, weight=0)

        head = tk.Frame(panel, bg=CARD)
        head.pack(fill="x", padx=self.s(12), pady=(self.s(10), self.s(4)))
        tk.Label(head, image=self.pic("folder", 16, "#e0a83c"), bg=CARD).pack(
            side="left")
        tk.Label(head, text="执行组", bg=CARD, fg=TEXT,
                 font=("Microsoft YaHei UI", 10, "bold")).pack(
            side="left", padx=(self.s(6), self.s(2)))
        uikit.RoundButton(head, "", "add", self.add_group, kind="ghost",
                          scale=self.scale, bg=CARD, height=24, pad=self.s(4),
                          icon_size=13, tooltip="新建执行组（Ctrl+G）"
                          ).pack(side="left")
        self.group_count = tk.Label(head, text="", bg=CARD, fg=MUTED,
                                    font=("Microsoft YaHei UI", 8))
        self.group_count.pack(side="right")

        hint = tk.Label(panel, text="右键执行组：新建 / 重命名 / 删除 / 排序",
                        bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 8))
        hint.pack(side="bottom", pady=self.s(9))

        wrap = tk.Frame(panel, bg=CARD)
        wrap.pack(fill="both", expand=True, padx=(self.s(6), 0))
        self.group_tree = ttk.Treeview(wrap, show="tree", selectmode="browse")
        self.group_tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(wrap, orient="vertical",
                               command=self.group_tree.yview)
        scroll.pack(side="right", fill="y")
        self.group_tree.configure(yscrollcommand=scroll.set)
        self.group_tree.tag_configure("running", foreground="#b06a00")
        self.group_tree.bind("<<TreeviewSelect>>", self._on_group_select)
        self.group_tree.bind("<Double-1>", self._on_group_double)
        self.group_tree.bind("<Button-3>", self._group_menu)
        enable_tree_drag(self.group_tree, self._on_group_drop)

        self.group_empty = tk.Label(
            panel, text="还没有执行组\n在列表上右键 → 新建执行组", bg=CARD, fg=MUTED,
            justify="center", font=("Microsoft YaHei UI", 9))

    def _build_step_panel(self, parent) -> None:
        panel = tk.Frame(parent, bg=CARD, highlightthickness=1,
                         highlightbackground=BORDER)
        parent.add(panel, weight=1)

        head = tk.Frame(panel, bg=CARD)
        head.pack(fill="x", padx=self.s(12), pady=(self.s(10), self.s(4)))
        tk.Label(head, image=self.pic("note", 16, ACCENT), bg=CARD).pack(
            side="left")
        self.step_title = tk.Label(head, text="步骤", bg=CARD, fg=TEXT,
                                   font=("Microsoft YaHei UI", 10, "bold"))
        self.step_title.pack(side="left", padx=(self.s(6), 0))
        tk.Label(head, text="双击单元格可直接修改", bg=CARD,
                 fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(
            side="left", padx=(self.s(10), 0))
        self.btn_add_step = uikit.RoundButton(
            head, "＋ 步骤", "add", self.add_step, kind="ghost",
            scale=self.scale, bg=CARD, height=24, pad=self.s(7),
            font_size=8, tooltip="新增步骤（Ctrl+Enter）")
        self.btn_add_step.pack(side="right")
        # 步骤搜索：按类型 / 内容 / 备注过滤，右侧显示「匹配数/总数」
        self.search_count = tk.Label(head, text="", bg=CARD, fg=MUTED,
                                     font=("Microsoft YaHei UI", 8))
        self.search_count.pack(side="right", padx=(0, self.s(6)))
        self.step_search = ttk.Entry(head, width=self.s(13))
        self.step_search.pack(side="right", padx=(0, self.s(2)))
        self.step_search.insert(0, SEARCH_PLACEHOLDER)
        self.step_search.configure(foreground=MUTED)
        self.step_search.bind("<FocusIn>", self._on_search_focus_in)
        self.step_search.bind("<FocusOut>", self._on_search_focus_out)
        self.step_search.bind("<KeyRelease>", self._on_search_key)
        self.step_search.bind("<Escape>", self._on_search_escape)

        wrap = tk.Frame(panel, bg=CARD)
        wrap.pack(fill="both", expand=True, padx=(self.s(12), 0),
                  pady=(0, self.s(12)))
        self.step_wrap = wrap
        columns = ("no", "type", "content", "repeat", "note")
        self.step_tree = ttk.Treeview(wrap, columns=columns,
                                      show="tree headings",
                                      selectmode="extended")
        self.step_tree.heading("#0", text="")
        self.step_tree.column("#0", width=self.s(30), stretch=False)
        for key, (text, width, stretch) in {
                "no": ("#", 44, False), "type": ("类型", 96, False),
                "content": ("内容", 320, True), "repeat": ("重复", 60, False),
                "note": ("备注", 180, True)}.items():
            self.step_tree.heading(key, text=text)
            self.step_tree.column(key, width=self.s(width), anchor="w",
                                  stretch=stretch)
        self.step_tree.pack(side="left", fill="both", expand=True)
        scroll2 = ttk.Scrollbar(wrap, orient="vertical",
                                command=self.step_tree.yview)
        scroll2.pack(side="right", fill="y")
        self.step_tree.configure(yscrollcommand=scroll2.set)
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

        self.step_empty = tk.Label(
            wrap, text="这个组还没有步骤\n在列表上右键 → 新增步骤", bg=CARD,
            fg=MUTED, justify="center", font=("Microsoft YaHei UI", 9))

        # 图片预览窗格：选中图片类步骤时出现，显示缩略图与文件信息
        self.preview_frame = tk.Frame(panel, bg=PREVIEW_BG,
                                      highlightthickness=1,
                                      highlightbackground=BORDER)
        self.preview_image = tk.Label(self.preview_frame, bg=PREVIEW_BG)
        self.preview_image.pack(side="left", padx=self.s(8), pady=self.s(6))
        info = tk.Frame(self.preview_frame, bg=PREVIEW_BG)
        info.pack(side="left", fill="x", expand=True, pady=self.s(6))
        self.preview_name = tk.Label(info, text="", bg=PREVIEW_BG, fg=TEXT,
                                     font=("Microsoft YaHei UI", 9, "bold"))
        self.preview_name.pack(anchor="w")
        self.preview_meta = tk.Label(info, text="", bg=PREVIEW_BG, fg=MUTED,
                                     font=("Microsoft YaHei UI", 8))
        self.preview_meta.pack(anchor="w")
        uikit.RoundButton(self.preview_frame, "在文件夹中显示", "folder_open",
                          self._open_preview_image_dir, kind="ghost",
                          scale=self.scale, bg=PREVIEW_BG, height=22,
                          pad=self.s(6), font_size=8).pack(side="right",
                                                           padx=self.s(6))

    def _build_log(self) -> None:
        self.log_card = tk.Frame(self, bg=CARD, highlightthickness=1,
                                 highlightbackground=BORDER)
        self.log_card.pack(fill="x", side="bottom", padx=self.s(12),
                           pady=(self.s(8), 0))

        head = tk.Frame(self.log_card, bg=CARD)
        head.pack(fill="x", padx=self.s(10), pady=self.s(6))
        self.btn_log_toggle = uikit.RoundButton(
            head, "运行日志", "chevron_down", self.toggle_log, kind="ghost",
            scale=self.scale, bg=CARD, height=26, pad=self.s(6), font_size=8,
            tooltip="收起 / 展开日志")
        self.btn_log_toggle.pack(side="left")
        self.log_state = tk.Label(head, text="就绪", bg=CARD, fg=MUTED,
                                  font=("Microsoft YaHei UI", 9))
        self.log_state.pack(side="left", padx=(self.s(10), 0))
        self.btn_log_clear = uikit.RoundButton(
            head, "清空", "trash", self.clear_log, kind="ghost",
            scale=self.scale, bg=CARD, height=26, pad=self.s(8), font_size=8)
        self.btn_log_clear.pack(side="right")
        # 点标题栏空白处也能收放
        for widget in (head, self.log_state):
            widget.bind("<Button-1>", lambda e: self.toggle_log())

        # 运行进度条：已知总步数→确定进度；循环 / 无限重复→脉冲滚动
        self.progress_frame = tk.Frame(self.log_card, bg=CARD)
        self.progress_frame.pack(fill="x", padx=self.s(10), pady=(0, self.s(4)))
        self.progress = ttk.Progressbar(
            self.progress_frame, style="Accent.Horizontal.TProgressbar",
            mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True)
        self.progress_label = tk.Label(self.progress_frame, text="", bg=CARD,
                                       fg=MUTED,
                                       font=("Microsoft YaHei UI", 8))
        self.progress_label.pack(side="left", padx=(self.s(8), 0))

        # 运行摘要条：默认隐藏，一次运行结束后显示结论 + 打开报告
        self.summary_frame = tk.Frame(self.log_card, bg=SUMMARY_BG,
                                      highlightthickness=1,
                                      highlightbackground=SUMMARY_BORDER)
        self.summary_label = tk.Label(self.summary_frame, text="",
                                      bg=SUMMARY_BG, fg=TEXT,
                                      font=("Microsoft YaHei UI", 9))
        self.summary_label.pack(side="left", padx=self.s(10))
        uikit.RoundButton(self.summary_frame, "", "close", self._hide_summary,
                          kind="ghost", scale=self.scale, bg=SUMMARY_BG,
                          height=22, pad=self.s(4), icon_size=12,
                          tooltip="收起摘要").pack(side="right", padx=self.s(4))
        self.btn_open_report = uikit.RoundButton(
            self.summary_frame, "打开报告", "folder_open",
            self._open_last_report,
            kind="outline", scale=self.scale, bg=SUMMARY_BG, height=22,
            pad=self.s(7), font_size=8)
        self.btn_open_report.pack(side="right")

        self.log_text = tk.Text(self.log_card, height=7, bg=LOG_BG, fg=LOG_FG,
                                insertbackground=LOG_FG, relief="flat",
                                font=("Consolas", 9), wrap="word",
                                padx=self.s(10), pady=self.s(6), state="disabled")
        self.log_text.pack(fill="x", padx=1, pady=(0, 1))
        # 可点击的「第 N 步」日志行：点一下跳到步骤表对应行
        # （先建 link，info/warn/error 后建——Tk 里后建的标签优先级高，
        #  这样错误行仍是红色，只额外获得下划线）
        self.log_text.tag_configure("link", foreground="#7cc7ff",
                                    underline=True)
        self.log_text.tag_bind("link", "<Button-1>",
                               lambda e: self._on_log_link_click(e))
        self.log_text.tag_bind("link", "<Enter>",
                               lambda e: self.log_text.configure(cursor="hand2"))
        self.log_text.tag_bind("link", "<Leave>",
                               lambda e: self.log_text.configure(cursor=""))
        self.log_text.tag_configure("info", foreground="#9fd3a0")
        self.log_text.tag_configure("warn", foreground="#f0c674")
        self.log_text.tag_configure("error", foreground="#f2777a")
        self.log_text.tag_configure("muted", foreground="#8b93a1")
        self.log_text.tag_configure("time", foreground="#6b7480")

    def toggle_log(self) -> None:
        if self.log_visible:
            self.log_text.pack_forget()
            self.btn_log_toggle.set_text("运行日志", "chevron_right")
        else:
            self.log_text.pack(fill="x", padx=1, pady=(0, 1))
            self.btn_log_toggle.set_text("运行日志", "chevron_down")
        self.log_visible = not self.log_visible

    def _build_statusbar(self) -> None:
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", side="bottom", padx=self.s(14),
                 pady=(self.s(2), self.s(6)))
        self.status_var = tk.StringVar(value="就绪")
        tk.Label(bar, textvariable=self.status_var, bg=BG, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.btn_shortcuts = uikit.RoundButton(
            bar, "", "question", self.show_shortcuts, kind="ghost",
            scale=self.scale, bg=BG, height=24, pad=self.s(4),
            icon_size=14, tooltip="快捷键速查（?）")
        self.btn_shortcuts.pack(side="right")
        self.image_label = tk.Label(bar, text="", bg=BG, fg=MUTED,
                                    font=("Microsoft YaHei UI", 9))
        self.image_label.pack(side="right", padx=(0, self.s(6)))
        self.btn_locate = uikit.RoundButton(
            bar, "定位到失败步骤", "target", self._locate_failed_step,
            kind="outline", scale=self.scale, bg=BG, height=24,
            pad=self.s(7), font_size=8, tooltip="在步骤表里标出失败的那一步")
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
            state["columns"] = {
                key: int(round(self.step_tree.column(key, "width")
                               / self.scale))
                for key in ("content", "note")}
        except tk.TclError:
            pass
        return state

    def _restore_ui_state(self) -> None:
        """启动时恢复上次的窗口大小、分隔条位置、列宽与日志展开状态。"""
        geometry = str(self.settings.get("window") or "")
        if geometry:
            try:
                self.geometry(geometry)
            except tk.TclError:
                pass
        columns = self.settings.get("columns") or {}
        for key in ("content", "note"):
            width = columns.get(key)
            if isinstance(width, int) and width > 0:
                self.step_tree.column(key, width=self.s(width))
        if not self.log_visible:
            self.log_text.pack_forget()
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
        for key, value in palette.items():
            globals()[key] = value
        TYPE_COLORS.clear()
        TYPE_COLORS.update(TYPE_COLORS_DARK if dark else LIGHT_TYPE_COLORS)
        uikit.set_dark_mode(dark)
        self._build_style()
        self._recolor_widgets(self, previous, palette)
        # 树标签（斑马纹 / 运行染色）跟着色板走
        self.step_tree.tag_configure("running", background=MARK_RUNNING)
        self.step_tree.tag_configure("done", background=MARK_DONE)
        self.step_tree.tag_configure("failed", background=MARK_FAILED)
        self.step_tree.tag_configure("even", background=CARD)
        self.step_tree.tag_configure("odd", background=ZEBRA)
        self._icons.clear()            # 图标按新色调重画
        self._refresh_groups()
        self._refresh_steps()
        if self.step_search.get() == SEARCH_PLACEHOLDER:
            self.step_search.configure(foreground=MUTED)
        self._last_palette = dict(palette)

    def _recolor_widgets(self, widget, old: dict, new: dict) -> None:
        """递归重染控件树：Label/Frame/Text/Canvas 按颜色映射换 bg/fg。

        ttk 控件不在这里处理（它们吃 style，已由 _build_style 重建）；
        RoundButton 是自绘 Canvas，换完底色还要重画一遍。
        """
        bg_map = {old[key]: new[key] for key in new if key in old}
        fg_map = {old["TEXT"]: new["TEXT"], old["MUTED"]: new["MUTED"],
                  old["ACCENT"]: new["ACCENT"]}

        def _get(widget, option):
            try:
                return str(widget.cget(option))
            except tk.TclError:
                return ""          # Frame / 根窗口没有 -fg，无所谓

        if isinstance(widget, uikit.RoundButton):
            bg = _get(widget, "bg")
            widget.set_bg(bg_map.get(bg, bg))
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
        """头部保存状态指示：已保存 · 时间。"""
        if getattr(self, "autosave_label", None) is not None:
            self.autosave_label.configure(
                text=f"已保存 · {time.strftime('%H:%M')}")

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
            self.autosave_label.configure(text="未保存…")

    def open_image_dir(self) -> None:
        """在文件管理器里打开图片目录。"""
        try:
            os.makedirs(self.image_dir, exist_ok=True)
            os.startfile(self.image_dir)          # noqa: S606 (Windows)
        except (OSError, AttributeError) as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)
        self.log_state.configure(text=text)

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
                image=self.pic("folder", 15, "#e0a83c"),
                open=(index == self.group_index))
            for step_index, step in enumerate(group.steps):
                self.group_tree.insert(
                    iid, "end", iid=f"{iid}_{step_index}",
                    text="   " + self._step_summary(step, step_index),
                    image=self.pic(TYPE_ICONS.get(step.type, "note"), 13,
                                   TYPE_COLORS.get(step.type,
                                                   TYPE_ICON_COLOR)))
        self.group_count.configure(text=f"{len(self.project.groups)} 个组")
        if self.project.groups:
            iid = f"g{self.group_index}"
            if self.group_tree.exists(iid):
                self.group_tree.selection_set(iid)
            self.group_empty.place_forget()
        else:
            self.group_empty.place(relx=0.5, rely=0.4, anchor="center")

    def _step_summary(self, step: Step, index: int) -> str:
        text = step.content.strip().replace("\n", " ")
        if step.type == "call":
            text = f"调用组：{step.content}"
        if len(text) > 22:
            text = text[:22] + "…"
        repeat = f"  ×{step.repeat:g}" if step.repeat else ""
        return f"{index + 1}. {step.label()} {text}{repeat}"

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
                image=self.pic(TYPE_ICONS.get(step.type, "note"), 14,
                               TYPE_COLORS.get(step.type, TYPE_ICON_COLOR)),
                values=(index + 1, step.label(), step.content, repeat,
                        step.note),
                tags=(self._zebra_tag(index),))
        if matched:
            self.step_empty.place_forget()
        else:
            self.step_empty.configure(
                text=f"没有包含“{self._step_filter}”的步骤"
                if self._step_filter_active() else
                "这个组还没有步骤\n在列表上右键 → 新增步骤")
            self.step_empty.place(relx=0.5, rely=0.4, anchor="center")
        if self._step_filter_active():
            self.search_count.configure(
                text=f"{matched}/{len(group.steps)}")
        else:
            self.search_count.configure(text="")
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
                            image=self.pic(TYPE_ICONS.get(step.type, "note"), 14,
                                           TYPE_COLORS.get(step.type,
                                                           TYPE_ICON_COLOR)),
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
        if self.step_search.get() == SEARCH_PLACEHOLDER:
            self.step_search.delete(0, "end")
            self.step_search.configure(foreground=TEXT)

    def _on_search_focus_out(self, _event=None) -> None:
        if not self.step_search.get():
            self.step_search.insert(0, SEARCH_PLACEHOLDER)
            self.step_search.configure(foreground=MUTED)
        self._apply_step_filter()

    def _on_search_key(self, event) -> None:
        if event.keysym == "Escape":
            return                      # Escape 走 _on_search_escape
        self._apply_step_filter()

    def _on_search_escape(self, _event=None) -> str:
        self._clear_step_filter()
        self.step_search.focus_set()
        return "break"

    def _apply_step_filter(self) -> None:
        text = self.step_search.get()
        self._step_filter = ("" if text == SEARCH_PLACEHOLDER
                             else text.strip())
        self._refresh_steps()

    def _clear_step_filter(self) -> None:
        self._step_filter = ""
        self.step_search.delete(0, "end")
        self.step_search.insert(0, SEARCH_PLACEHOLDER)
        self.step_search.configure(foreground=MUTED)
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
            widget: tk.Widget = ttk.Combobox(
                self.step_tree, state="readonly",
                values=[f"{i + 1} · {info[0]}"
                        for i, info in enumerate(STEP_TYPES.values())],
                font=("Microsoft YaHei UI", 9))
            widget.set(f"{list(STEP_TYPES).index(step.type) + 1} · {step.label()}")
        else:
            widget = ttk.Entry(self.step_tree, font=("Microsoft YaHei UI", 9))
            widget.insert(0, {"content": step.content,
                              "repeat": "" if not step.repeat else f"{step.repeat:g}",
                              "note": step.note}[field])
        can_record = field == "content" and step.type == "hotkey"
        entry_width = max(width - self.s(40), self.s(80)) if can_record \
            else max(width, self.s(120))
        widget.place(x=x, y=y, width=entry_width, height=height)
        widget.focus_set()
        if isinstance(widget, ttk.Entry):
            widget.select_range(0, "end")
        self._editor = widget

        if can_record:
            # 热键内容列右侧放一个键盘按钮：点击后直接按组合键录入
            self._inline_target = widget
            button = uikit.RoundButton(
                self.step_tree, "", "keyboard", self._inline_record,
                kind="outline", scale=self.scale, bg=CARD,
                height=max(1, round(height / self.scale)),
                pad=self.s(6), icon_size=14,
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
        if isinstance(widget, ttk.Combobox):
            # 组合框失焦通常发生在「弹出下拉」时，延后一点再提交
            widget.bind("<<ComboboxSelected>>", commit)
            widget.bind("<FocusOut>", lambda e: self.after(150, commit))
        else:
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
            self._set_status("无法启动键盘录制（装不上钩子），请手动输入")

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
            fill="white", font=("Microsoft YaHei UI", 16, "bold"))
        state = {"x": 0, "y": 0, "rect": None}

        def on_press(event):
            state["x"], state["y"] = event.x, event.y
            state["rect"] = canvas.create_rectangle(event.x, event.y, event.x,
                                                    event.y, outline="#4d8dff",
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
            self.deiconify()
            self.lift()
            coords_callback = getattr(self, "_capture_coords_callback", None)
            self._capture_coords_callback = None
            if abs(x2 - x1) < 8 or abs(y2 - y1) < 8:
                self._set_status("框选区域太小，已取消")
                return
            if coords_callback:
                coords_callback(min(x1, x2), min(y1, y2),
                                abs(x2 - x1), abs(y2 - y1))
                return
            path = assets.unique_name(self.image_dir)
            try:
                assets.grab_region(x1, y1, x2, y2, path)
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("截图失败", str(exc), parent=self)
                return
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
                stamp = time.strftime("%H:%M:%S")
                self.log_text.configure(state="normal")
                self.log_text.insert("end", f"[{stamp}] ", "time")
                # 出错 / 超时的日志行关联到当前执行的步骤，可点击跳转
                if (self._current_run_step is not None
                        and (level == "error" or "出错" in message
                             or "超时" in message)):
                    line = int(self.log_text.index("end-1c").split(".")[0])
                    self._step_log_lines[line] = self._current_run_step
                    self.log_text.insert("end", message + "\n", ("link", level))
                else:
                    self.log_text.insert("end", message + "\n", level)
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
        problems = validate_project(groups, base_dir=self.image_dir,
                                    check_images=check_images)
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

    def run_group(self) -> None:
        self._start(self._current_group_name())

    def run_group_for(self, name: str) -> None:
        """运行指定组（定时运行用；不切换左侧选中）。"""
        self._start(name)

    def run_loop(self) -> None:
        self._start(self._current_group_name(), loop=True)

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
                                "选「否」则从头开始。")
                else:
                    question = (f"上次跑到第 {last} 行，"
                                f"是否从第 {last + 1} 行继续？\n\n"
                                "选「否」则从头开始。")
                if messagebox.askyesno("继续上次？", question, parent=self):
                    start_row = resume
            elif last >= len(data) and len(data):
                if not messagebox.askyesno(
                        "已经跑完",
                        f"上次已经跑完全部 {len(data)} 行，是否从头再跑一遍？",
                        parent=self):
                    return
        except Exception:      # noqa: BLE001 - 旧结果文件读不了就从头跑
            start_row = 0
        self._start(self._current_group_name(), data=data,
                    data_path=path, row_offset=start_row)

    def _start(self, entry: Optional[str], loop: bool = False,
               data: Optional[list] = None,
               data_path: Optional[str] = None,
               row_offset: int = 0) -> None:
        if self.running:
            messagebox.showinfo("提示", "正在运行中，请先停止", parent=self)
            return
        names = None if entry is None else [entry]
        if entry is not None and not entry:
            return
        if sum(len(g.steps) for g in self.project.groups) == 0:
            messagebox.showinfo("提示", "还没有可执行的步骤", parent=self)
            return
        if not self._validate(names):
            return
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

    def _countdown(self, seconds: int, callback) -> None:
        self._countdown_token += 1
        token = self._countdown_token
        if seconds <= 0:
            callback()
            return
        overlay = tk.Toplevel(self)
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        overlay.configure(bg="#1e2228")
        width, height = self.s(280), self.s(126)
        x = (self.winfo_screenwidth() - width) // 2
        y = (self.winfo_screenheight() - height) // 2
        overlay.geometry(f"{width}x{height}+{x}+{y}")
        label = tk.Label(overlay, text="", bg="#1e2228", fg="white",
                         font=("Microsoft YaHei UI", 22, "bold"))
        label.pack(expand=True)
        tk.Label(overlay, text="即将开始，请切换到目标窗口（Esc 取消）",
                 bg="#1e2228", fg="#9aa3af",
                 font=("Microsoft YaHei UI", 9)).pack(pady=(0, self.s(12)))
        state = {"left": seconds, "cancelled": False}

        def cancel(_event=None):
            state["cancelled"] = True
            self._countdown_token += 1
            overlay.destroy()
            self.deiconify()

        overlay.bind("<Escape>", cancel)

        def tick():
            if state["cancelled"] or token != self._countdown_token:
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
        # 防止界面和计划任务 / 另一个副本同时抢鼠标
        lock = InstanceLock(make_lock_name(APP_DIR, "RUN"))
        if not lock.acquire():
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
            self._notify_finish(result)
        finally:
            self.running = False
            self.paused = False
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
            self.deiconify()
            self.lift()
            self._refresh_all()
            self._set_status({"finished": "执行完成", "stopped": "已停止",
                              "error": "执行出错"}.get(result, result))
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
                                before=self.log_text)

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
        self.btn_debug.set_kind("solid" if self.debug_mode else "ghost")
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

    # --------------------------------------------------------------- 定时运行
    def _schedule_tick(self) -> None:
        """每秒对一次表：到点就自动跑设定的执行组（界面开着才有效）。"""
        try:
            schedule = self.settings.get("schedule") or {}
            if schedule.get("enabled") and not self.running:
                now = time.localtime()
                today = time.strftime("%Y-%m-%d")
                key = f"{today} {schedule.get('time', '')}"
                if (time.strftime("%H:%M") == schedule.get("time")
                        and key not in self._schedule_fired):
                    self._schedule_fired.add(key)
                    group = schedule.get("group") or ""
                    if group and group in self.project.names():
                        self._log("info", f"定时运行触发：{group}")
                        self.run_group_for(group)
                    elif not group:
                        self._log("info", "定时运行触发：当前组")
                        self.run_group()
                    else:
                        self._log("warn",
                                  f"定时运行跳过：找不到执行组「{group}」")
            # 清理前一天的火星记录，避免集合无限增长
            if len(self._schedule_fired) > 8:
                self._schedule_fired = {
                    k for k in self._schedule_fired if k.startswith(today)}
        finally:
            self.after(1000, self._schedule_tick)

    def _schedule_summary(self) -> str:
        """状态栏 / 菜单里显示的定时运行概况。"""
        schedule = self.settings.get("schedule") or {}
        if not schedule.get("enabled"):
            return "定时运行未启用"
        group = schedule.get("group") or "当前组"
        return f"定时 {schedule.get('time', '--:--')} → {group}"

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
        """运行结束通知：声音 + webhook（企业微信/钉钉等机器人地址）。"""
        if self.settings.get("sound"):
            try:
                import winsound

                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:  # noqa: BLE001
                pass
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
        self._save_state()
        self.image_label.configure(text=f"图片目录：{self.image_dir}")

    def open_schedule(self) -> None:
        dialog = ScheduleDialog(self, self.settings, self.project.names())
        if dialog.wait() is None:
            return
        self.settings.update(dialog.result)
        self._mark_dirty()
        self._save_state()
        self._log("info", f"定时运行设置已保存（{self._schedule_summary()}）")

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
        lines = ["【一句话】",
                 "左侧管理「执行组」，右侧编辑组里的步骤，点「运行组」执行。",
                 "没有“项目”概念：所有执行组自动保存，下次打开还在。",
                 "一个组可以被别的组用「调用执行组」指令反复复用。",
                 "",
                 "【操作要点】",
                 "· 组操作：在左侧组上右键 → 新建 / 重命名 / 复制 / 删除 / 排序；",
                 "· 直接运行某个组：在该组上右键 →「运行这个组」；",
                 "· 步骤列表双击单元格可直接改内容、类型、重复次数、备注；",
                 "· 右键列表＝完整操作菜单（新增/编辑/复制/删除/截图/上下移）；",
                 "· 图片类步骤点「截图」框选屏幕，自动保存到图片目录；",
                 "· 编排复用：在组里新增「调用执行组」，内容填要调用的组名。",
                 "",
                 "【指令类型】"]
        for index, (key, (name, hint, need_repeat, repeat_hint)) in enumerate(
                STEP_TYPES.items(), start=1):
            lines.append(f"{index}. {name}：{hint}")
            if repeat_hint:
                lines.append(f"     重复次数：{repeat_hint}")
        lines += ["", "【快捷键】",
                  "Ctrl+G 新建组　Ctrl+Enter 新增步骤　F2 编辑步骤　Delete 删除步骤",
                  "F5 运行当前组（全局）　F9 停止（全局，任何时候都有效）",
                  "",
                  "【安全】把鼠标快速甩到屏幕左上角可立即中断 pyautogui。"]

        win = tk.Toplevel(self)
        win.title("使用说明")
        win.configure(bg=CARD)
        win.transient(self)
        text = tk.Text(win, width=64, height=27, wrap="word", relief="flat",
                       bg=CARD, fg=TEXT, font=("Microsoft YaHei UI", 9),
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
        messagebox.showinfo(
            "关于",
            f"{APP_TITLE}\n\n"
            "把原来的 Excel 配置搬到界面上：可视化维护执行组与步骤、\n"
            "组间可互相调用复用、一键截图、运行前校验、日志与全局停止热键。\n\n"
            "执行组自动保存在程序目录的 rpa_settings.json，\n"
            "Excel 互转：单组导入导出 / 全部工作表导入导出（旧版 cmd.xls 通用）。",
            parent=self)

    # --------------------------------------------------------------- 关闭
    def on_close(self) -> None:
        if self.running:
            if not messagebox.askyesno("确认退出", "任务仍在运行，确定退出？",
                                       parent=self):
                return
            self.executor.stop()
        self._inline_recorder.stop()   # 卸载可能装着的键盘钩子
        self.project.image_dir = self.image_dir
        self._save_state()
        self.destroy()


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

    def label(self, text: str, row: int, muted: bool = False) -> None:
        tk.Label(self, text=text, bg=CARD, fg=MUTED if muted else TEXT,
                 font=("Microsoft YaHei UI", 9)).grid(
            row=row, column=0, sticky="w", padx=(self.app.s(16), self.app.s(8)),
            pady=self.app.s(5))


class GroupDialog(BaseDialog):
    """输入 / 修改执行组名称。"""

    def __init__(self, parent: App, title: str, initial: str = ""):
        super().__init__(parent, title, width=380, height=170)
        self.label("执行组名称", 0)
        self.var = tk.StringVar(value=initial)
        entry = ttk.Entry(self, textvariable=self.var,
                          font=("Microsoft YaHei UI", 10))
        entry.grid(row=0, column=1, sticky="ew", padx=(0, parent.s(16)),
                   pady=parent.s(5))
        self.columnconfigure(1, weight=1)
        self.label("尽量简短，便于在「调用执行组」里引用", 1, muted=True)
        buttons = tk.Frame(self, bg=CARD)
        buttons.grid(row=2, column=0, columnspan=2, pady=parent.s(12))
        uikit.RoundButton(buttons, "取消", "close", self.cancel, kind="ghost",
                          scale=parent.scale, bg=CARD).pack(side="left",
                                                            padx=parent.s(4))
        uikit.RoundButton(buttons, "确定", "check", self.ok, kind="accent",
                          scale=parent.scale, bg=CARD).pack(side="left",
                                                            padx=parent.s(4))
        entry.bind("<Return>", lambda e: self.ok())
        entry.focus_set()
        entry.select_range(0, "end")
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
        self.var_type = tk.StringVar()
        self.combo = ttk.Combobox(
            self, textvariable=self.var_type, state="readonly",
            values=self._type_labels,
            font=("Microsoft YaHei UI", 9))
        self.combo.grid(row=0, column=1, sticky="ew", padx=(0, parent.s(16)),
                        pady=parent.s(5))
        self.combo.bind("<<ComboboxSelected>>", lambda e: self._sync())

        self.label("内容", 1)
        content = tk.Frame(self, bg=CARD)
        content.grid(row=1, column=1, sticky="ew", padx=(0, parent.s(16)),
                     pady=parent.s(5))
        content.columnconfigure(0, weight=1)
        self.var_content = tk.StringVar(value=editing.content)
        self.entry = ttk.Entry(content, textvariable=self.var_content,
                               font=("Microsoft YaHei UI", 9))
        self.entry.grid(row=0, column=0, sticky="ew")
        # 输入框与右侧按钮组之间的分隔线（有按钮时才显示）
        self.sep_entry = tk.Frame(content, bg=BORDER, width=1)
        self.sep_entry.grid(row=0, column=1, sticky="ns",
                            padx=(parent.s(6), parent.s(2)), pady=parent.s(3))
        # 条件步骤的第二段：要执行的组名（写成 图片|组名 的图形化版）
        self.var_group = tk.StringVar()
        if editing.type in PIPE_CONDITION_TYPES and "|" in editing.content:
            image, _, group = editing.content.partition("|")
            self.var_content.set(image.strip())
            self.var_group.set(group.strip())
        self.group_entry = ttk.Entry(content, textvariable=self.var_group,
                                     width=10, font=("Microsoft YaHei UI", 9))
        self.group_entry.grid(row=0, column=2, padx=(parent.s(2), 0))
        self.btn_pick_group = uikit.RoundButton(
            content, "选组", "list", self._pick_group, kind="outline",
            scale=parent.scale, bg=CARD, height=30, pad=parent.s(8),
            font_size=8, tooltip="选择条件满足时要执行的执行组")
        self.btn_pick_group.grid(row=0, column=3, padx=(parent.s(4), 0))
        # if_else 的第三段：没出现图片时执行的组（第二行，仅该类型可见）
        self.var_group2 = tk.StringVar()
        if editing.type == "if_else":
            parts = (editing.content.split("|") + ["", ""])[:3]
            self.var_content.set(parts[0].strip())
            self.var_group.set(parts[1].strip())
            self.var_group2.set(parts[2].strip())
        self.label_group2 = tk.Label(content, text="没出现时执行", bg=CARD,
                                     fg=MUTED, font=("Microsoft YaHei UI", 8))
        self.label_group2.grid(row=1, column=1, sticky="e",
                               pady=(parent.s(4), 0))
        self.group2_entry = ttk.Entry(content, textvariable=self.var_group2,
                                      width=10, font=("Microsoft YaHei UI", 9))
        self.group2_entry.grid(row=1, column=2, padx=(parent.s(2), 0),
                               pady=(parent.s(4), 0))
        self.btn_pick_group2 = uikit.RoundButton(
            content, "选组", "list", self._pick_group2, kind="outline",
            scale=parent.scale, bg=CARD, height=30, pad=parent.s(8),
            font_size=8, tooltip="选择没出现图片时要执行的执行组")
        self.btn_pick_group2.grid(row=1, column=3, padx=(parent.s(4), 0),
                                  pady=(parent.s(4), 0))
        # 「选组」与「截图/选图」之间的分隔线（仅条件步骤可见）
        self.sep_if = tk.Frame(content, bg=BORDER, width=1)
        self.sep_if.grid(row=0, column=4, sticky="ns",
                         padx=(parent.s(2), parent.s(2)), pady=parent.s(3))
        self.btn_shot = uikit.RoundButton(
            content, "截图", "image",
            lambda: parent.capture_region(self._set_image), kind="outline",
            scale=parent.scale, bg=CARD, height=30, pad=parent.s(8),
            font_size=8)
        self.btn_shot.grid(row=0, column=5, padx=(parent.s(4), 0))
        self.btn_pick = uikit.RoundButton(
            content, "选图", "folder_open", self._pick_image, kind="outline",
            scale=parent.scale, bg=CARD, height=30, pad=parent.s(8),
            font_size=8)
        self.btn_pick.grid(row=0, column=6, padx=(parent.s(4), 0))
        # 预览：弹窗看内容框里当前写的图片（截图 / 选图完可立即核对）
        self.btn_preview = uikit.RoundButton(
            content, "预览", "eye", self._preview_image, kind="outline",
            scale=parent.scale, bg=CARD, height=30, pad=parent.s(8),
            font_size=8, tooltip="查看内容框里当前写的图片")
        self.btn_preview.grid(row=0, column=7, padx=(parent.s(4), 0))
        # 按键录入：热键步骤可以直接按组合键，不用输入法打字
        self.btn_record = uikit.RoundButton(
            content, "", "keyboard", self._start_record, kind="outline",
            scale=parent.scale, bg=CARD, height=30, pad=parent.s(8),
            icon_size=16, tooltip="按键录入：直接按组合键，如 Ctrl+Shift+A")
        self.btn_record.grid(row=0, column=8, padx=(parent.s(4), 0))
        # 坐标拾取：倒计时几秒后取当前鼠标位置
        self.btn_grab_xy = uikit.RoundButton(
            content, "拾取", "mouse", self._grab_xy, kind="outline",
            scale=parent.scale, bg=CARD, height=30, pad=parent.s(8),
            font_size=8, tooltip="把鼠标移到目标位置，点此倒计时拾取坐标")
        self.btn_grab_xy.grid(row=0, column=9, padx=(parent.s(4), 0))

        self.hint = tk.Label(self, text="", bg=CARD, fg=MUTED,
                             wraplength=parent.s(320), justify="left",
                             font=("Microsoft YaHei UI", 8))
        self.hint.grid(row=2, column=0, columnspan=2, sticky="w",
                       padx=parent.s(16))

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
            kind="ghost", scale=parent.scale, bg=CARD, height=30,
            pad=parent.s(6), font_size=8,
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
            font=("Microsoft YaHei UI", 8))
        self.hint_timeout.grid(row=0, column=1, sticky="w",
                               padx=(parent.s(4), parent.s(14)))
        self.lbl_fail = tk.Label(row_timeout, text="超时后", bg=CARD, fg=TEXT,
                                 font=("Microsoft YaHei UI", 9))
        self.lbl_fail.grid(row=0, column=2, sticky="w")
        self.var_on_fail = tk.StringVar(
            value="跳过这步" if editing.on_fail != "stop" else "停止执行")
        self.combo_fail = ttk.Combobox(
            row_timeout, textvariable=self.var_on_fail, state="readonly",
            values=["跳过这步", "停止执行"], width=9,
            font=("Microsoft YaHei UI", 9))
        self.combo_fail.grid(row=0, column=3, sticky="w",
                             padx=(parent.s(4), 0))

        # 识别范围 + 框选
        self.lbl_region = self._adv_label("识别范围", 1)
        row_region = tk.Frame(self.advanced, bg=CARD)
        row_region.grid(row=1, column=1, sticky="ew", pady=parent.s(3))
        row_region.columnconfigure(0, weight=1)
        self.var_region = tk.StringVar(value=editing.region)
        self.entry_region = ttk.Entry(row_region, textvariable=self.var_region,
                                      font=("Microsoft YaHei UI", 9))
        self.entry_region.grid(row=0, column=0, sticky="ew")
        self.btn_pick_region = uikit.RoundButton(
            row_region, "框选", "image", self._pick_region, kind="outline",
            scale=parent.scale, bg=CARD, height=30, pad=parent.s(8),
            font_size=8, tooltip="在屏幕上拖拽选择识别区域")
        self.btn_pick_region.grid(row=0, column=1, padx=(parent.s(4), 0))
        self.hint_region = tk.Label(
            row_region, text="留空 = 全屏查找；格式 x,y,宽,高",
            bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 8))
        self.hint_region.grid(row=1, column=0, columnspan=2, sticky="w",
                              pady=(parent.s(2), 0))

        # 第几个 / 灰度 / 失败重试（同一行，省高度）
        self.lbl_match = self._adv_label("匹配 / 重试", 2)
        row_match = tk.Frame(self.advanced, bg=CARD)
        row_match.grid(row=2, column=1, sticky="w", pady=parent.s(3))
        self.var_nth = tk.StringVar(value=str(int(editing.nth or 0) + 1))
        self.lbl_nth = tk.Label(row_match, text="第", bg=CARD, fg=TEXT,
                                font=("Microsoft YaHei UI", 9))
        self.lbl_nth.grid(row=0, column=0, sticky="w")
        self.spin_nth = ttk.Spinbox(row_match, from_=1, to=999, width=5,
                                    textvariable=self.var_nth)
        self.spin_nth.grid(row=0, column=1, sticky="w", padx=(parent.s(3), 0))
        self.lbl_nth_suffix = tk.Label(row_match, text="个", bg=CARD, fg=TEXT,
                                       font=("Microsoft YaHei UI", 9))
        self.lbl_nth_suffix.grid(row=0, column=2, sticky="w",
                                 padx=(parent.s(3), parent.s(10)))
        self.var_gray = tk.BooleanVar(value=editing.grayscale)
        self.chk_gray = ttk.Checkbutton(
            row_match, text="灰度", variable=self.var_gray)
        self.chk_gray.grid(row=0, column=3, sticky="w",
                           padx=(0, parent.s(10)))
        self.lbl_retry = tk.Label(row_match, text="失败重试", bg=CARD, fg=TEXT,
                                  font=("Microsoft YaHei UI", 9))
        self.lbl_retry.grid(row=0, column=4, sticky="w")
        self.var_retry = tk.StringVar(value=str(editing.retry or 0))
        self.spin_retry = ttk.Spinbox(row_match, from_=0, to=9, width=4,
                                      textvariable=self.var_retry)
        self.spin_retry.grid(row=0, column=5, sticky="w", padx=(parent.s(3), 0))
        self.lbl_retry_unit = tk.Label(row_match, text="次，间隔", bg=CARD,
                                       fg=TEXT,
                                       font=("Microsoft YaHei UI", 9))
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
                                      font=("Microsoft YaHei UI", 9))
        self.lbl_retry_sec.grid(row=0, column=8, sticky="w",
                                padx=(parent.s(2), 0))

        # 输入方式 / 命中检查（同一行，按类型切换）
        self.lbl_other = self._adv_label("其他", 3)
        row_other = tk.Frame(self.advanced, bg=CARD)
        row_other.grid(row=3, column=1, sticky="w", pady=parent.s(3))
        self.var_input_mode = tk.StringVar(
            value="逐字输入（英文/数字）" if editing.input_mode == "type"
            else "粘贴（快，支持中文）")
        self.combo_input = ttk.Combobox(
            row_other, textvariable=self.var_input_mode, state="readonly",
            values=["粘贴（快，支持中文）", "逐字输入（英文/数字）"],
            width=20, font=("Microsoft YaHei UI", 9))
        self.combo_input.grid(row=0, column=0, sticky="w")
        self.btn_check = uikit.RoundButton(
            row_other, "检测图片", "eye", self._check_image, kind="outline",
            scale=parent.scale, bg=CARD, height=30, pad=parent.s(8),
            font_size=8, tooltip="隐藏窗口后检查当前屏幕能否找到图片")
        self.btn_check.grid(row=0, column=0, sticky="w")
        self.lbl_check_result = tk.Label(
            row_other, text="", bg=CARD, fg=MUTED,
            font=("Microsoft YaHei UI", 8))
        self.lbl_check_result.grid(row=0, column=1, sticky="w",
                                   padx=(parent.s(8), 0))

        self._adv_timeout = [self.lbl_timeout, row_timeout]
        self._adv_fail = [self.lbl_fail, self.combo_fail]
        self._adv_image = [self.lbl_region, row_region]
        self._adv_nth = [self.lbl_nth, self.spin_nth,
                         self.lbl_nth_suffix, self.chk_gray]
        self._adv_match = [self.lbl_match, row_match]
        self._adv_other = [self.lbl_other, row_other]

        self.label("备注", 6)
        self.var_note = tk.StringVar(value=editing.note)
        self.note_entry = ttk.Entry(self, textvariable=self.var_note)
        self.note_entry.grid(row=6, column=1, sticky="ew",
                             padx=(0, parent.s(16)), pady=parent.s(5))

        buttons = tk.Frame(self, bg=CARD)
        buttons.grid(row=7, column=0, columnspan=2, pady=parent.s(12))
        self.btn_cancel = uikit.RoundButton(
            buttons, "取消", "close", self.cancel, kind="ghost",
            scale=parent.scale, bg=CARD)
        self.btn_cancel.pack(side="left", padx=parent.s(4))
        self.btn_ok = uikit.RoundButton(
            buttons, "确定", "check", self.ok, kind="accent",
            scale=parent.scale, bg=CARD)
        self.btn_ok.pack(side="left", padx=parent.s(4))

        self._recorder = KeyRecorder(
            self,
            on_state=self._set_recording_ui,
            on_preview=lambda names: self.var_content.set("+".join(names)),
            on_done=self._record_done,
            on_cancel=self._record_cancelled,
            on_hint=lambda text: self._show_hint(text))
        self.var_type.set(editing.label())
        self._sync()
        self.entry.focus_set()
        self.center()

    # -- 高级选项 -----------------------------------------------------------
    def _adv_label(self, text: str, row: int) -> tk.Label:
        label = tk.Label(self.advanced, text=text, bg=CARD, fg=TEXT,
                         font=("Microsoft YaHei UI", 9))
        label.grid(row=row, column=0, sticky="w", pady=self.app.s(3))
        return label

    def _toggle_advanced(self) -> None:
        self._advanced_open = not self._advanced_open
        if self._advanced_open:
            self.advanced.grid()
            self.btn_advanced.set_text("高级选项", "chevron_down")
        else:
            self.advanced.grid_remove()
            self.btn_advanced.set_text("高级选项", "chevron_right")
        self.update_idletasks()
        self.geometry(f"{self.winfo_width()}x{self.winfo_reqheight()}")

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
                                            fg="#b06a00")
            return
        raw_region = self.var_region.get().strip()
        region = parse_region(raw_region)
        if raw_region and region is None:
            self.lbl_check_result.configure(
                text="识别范围格式不对，要写 x,y,宽,高", fg="#b06a00")
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
                    message = "当前屏幕没找到（确认窗口已打开、缩放一致）"
                elif len(points) == 1:
                    message = "命中 1 处 ✓"
                    warn = False
                else:
                    message = (f"命中 {len(points)} 处，可能点错"
                               "（可用「第几个」或框选范围）")
                try:
                    nth = int(self.var_nth.get() or 1)
                except ValueError:
                    nth = 1
                if points and nth > len(points):
                    message = f"只命中 {len(points)} 处，没有第 {nth} 个"
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
                text=message, fg="#b06a00" if warn else "#2e7d32")

        self.after(350, run)

    # -- 辅助 -------------------------------------------------------------
    def _type_key(self) -> str:
        try:
            return list(STEP_TYPES)[self._type_labels.index(
                self.var_type.get())]
        except (ValueError, IndexError):
            return "click"

    def _sync(self) -> None:
        if self._recorder.active:
            return                     # 录制中不响应类型切换
        key = self._type_key()
        needs_image = key in IMAGE_STEP_TYPES
        _, hint, need_repeat, repeat_hint = STEP_TYPES[key]
        text = hint if not repeat_hint else f"{hint}｜重复次数：{repeat_hint}"
        if key == "call":
            names = "、".join(self.project.names()) or "（还没有其它组）"
            text += f"\n可调用：{names}"
        if key == "hotkey":
            text += "\n也可点右侧键盘按钮直接按组合键录入（Ctrl+Shift+A 这种）"
        if key in PIPE_CONDITION_TYPES:
            text += ("\n左边填判断用图片，右边填满足后要执行的组"
                     "（可点「选组」）")
        if key == "if_else":
            text += "\n下面两个输入框分别填出现时 / 没出现时要执行的组" \
                    "（都能点「选组」）"
        if key == "set":
            text += "\n设置后，任意步骤的内容里都能用 {变量名} 读回"
        if key == "copy":
            text += "\n先手动复制好文字，这一步把它存进指定变量" \
                    "（发一次 Ctrl+C，剪贴板稍后会还原）"
        if key == "drag":
            text += "\n截图 / 选图 / 拾取会填进第一个空着的一段"
        if key == "window":
            text += "\n标题写窗口名的一部分就行，如 activate:记事本"
        if key in ("click_xy", "move_xy"):
            text += "\n也可以把鼠标移到目标处，点「拾取」自动填坐标"
        if key == "input":
            text += ("\n默认用剪贴板粘贴（支持中文）；个别应用不认粘贴时，"
                     "可在「高级选项」改逐字输入")
        if needs_image:
            text += ("\n截图 / 选图后可以点「预览」核对图片内容；"
                     "点错了可在「高级选项」里框选范围或指定第几个")
        elif key in CONDITION_TYPES:
            text += "\n「高级选项」里可以设置最多等几秒"
        self._show_hint(text)
        is_image = key in IMAGE_TYPES
        is_hotkey = key == "hotkey"
        # 内容行右侧的按钮：用不到的整个收起，把宽度让给内容输入框
        self.btn_shot.set_enabled(needs_image)
        self.btn_pick.set_enabled(needs_image)
        self.btn_preview.set_enabled(needs_image)
        self.btn_record.set_enabled(is_hotkey)
        self.btn_shot.grid() if needs_image else self.btn_shot.grid_remove()
        self.btn_pick.grid() if needs_image else self.btn_pick.grid_remove()
        self.btn_preview.grid() if needs_image else \
            self.btn_preview.grid_remove()
        self.btn_record.grid() if is_hotkey else self.btn_record.grid_remove()
        # 条件步骤：显示“组名”第二段；坐标/拖拽步骤：显示拾取按钮
        is_else = key == "if_else"
        is_if = key in PIPE_CONDITION_TYPES or is_else
        is_xy = key in ("click_xy", "move_xy", "drag")
        self.group_entry.grid() if is_if else self.group_entry.grid_remove()
        self.btn_pick_group.grid() if is_if else self.btn_pick_group.grid_remove()
        self.btn_pick_group.set_enabled(is_if)
        self.btn_grab_xy.set_enabled(is_xy)
        self.btn_grab_xy.grid() if is_xy else self.btn_grab_xy.grid_remove()
        # if_else 的第三段（没出现时执行的组）单独一行
        for widget in (self.label_group2, self.group2_entry,
                       self.btn_pick_group2):
            widget.grid() if is_else else widget.grid_remove()
        # 条件步骤才有「选组 | 截图/选图」的分组，分隔线随之显隐
        self.sep_if.grid() if (is_if and needs_image) \
            else self.sep_if.grid_remove()
        # 右侧一个按钮都没有时，输入框后的分隔线也收起
        self.sep_entry.grid() if (needs_image or is_hotkey or is_xy) \
            else self.sep_entry.grid_remove()
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

    # -- 按键录入 -----------------------------------------------------------
    def _show_hint(self, text: str, warn: bool = False) -> None:
        self.hint.configure(text=text, fg="#b06a00" if warn else MUTED)

    def _start_record(self) -> None:
        if self._recorder.active:
            return
        if not self._recorder.start(self.var_content.get()):
            self._show_hint("无法启动键盘录制（装不上钩子），请手动输入",
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
        self.chk_gray.configure(state="disabled" if active else "normal")
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
        menu = tk.Menu(self, tearoff=0, font=("Microsoft YaHei UI", 9))
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
        menu = tk.Menu(self, tearoff=0, font=("Microsoft YaHei UI", 9))
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
                 font=("Microsoft YaHei UI", 9, "bold")).pack()
        meta = f"{self._origin[0]} × {self._origin[1]} 像素"
        if ratio < 1:
            meta += f" · 已缩放至 {ratio * 100:.0f}%"
        try:
            meta += f" · {os.path.getsize(path) / 1024:.0f} KB"
        except OSError:
            pass
        tk.Label(self, text=meta, bg=CARD, fg=MUTED,
                 font=("Microsoft YaHei UI", 8)).pack(
            pady=(0, self.app.s(4)))
        buttons = tk.Frame(self, bg=CARD)
        buttons.pack(pady=(0, self.app.s(12)))
        uikit.RoundButton(buttons, "在文件夹中显示", "folder_open",
                          self._open_folder, kind="ghost",
                          scale=self.app.scale, bg=CARD).pack(
            side="left", padx=self.app.s(4))
        uikit.RoundButton(buttons, "关闭", "close", self.cancel,
                          kind="accent",
                          scale=self.app.scale, bg=CARD).pack(
            side="left", padx=self.app.s(4))
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

    定时到点后自动运行设定的执行组（软件内定时只在界面开着时有效）。
    底部可以把同样的时间和组交给 Windows 计划任务，实现关界面也能跑的无人值守。
    """

    def __init__(self, parent: App, settings: dict, group_names: list[str]):
        super().__init__(parent, "定时运行与通知", width=470, height=475)
        self.columnconfigure(1, weight=1)
        self._group_names = list(group_names)
        schedule = settings.get("schedule") or {}
        hour, _, minute = str(schedule.get("time", "09:00")).partition(":")

        self.label("启用定时运行", 0)
        self.var_enabled = tk.BooleanVar(value=bool(schedule.get("enabled")))
        ttk.Checkbutton(self, variable=self.var_enabled).grid(
            row=0, column=1, sticky="w", padx=(0, parent.s(16)),
            pady=parent.s(5))

        self.label("每天运行时间", 1)
        row_time = tk.Frame(self, bg=CARD)
        row_time.grid(row=1, column=1, sticky="w", padx=(0, parent.s(16)),
                      pady=parent.s(5))
        self.var_hour = tk.StringVar(value=hour or "09")
        self.var_minute = tk.StringVar(value=minute or "00")
        ttk.Spinbox(row_time, from_=0, to=23, width=4, wrap=True,
                    format="%02.0f", textvariable=self.var_hour).pack(
            side="left")
        tk.Label(row_time, text=":", bg=CARD).pack(side="left")
        ttk.Spinbox(row_time, from_=0, to=59, width=4, wrap=True,
                    format="%02.0f", textvariable=self.var_minute).pack(
            side="left")
        tk.Label(row_time, text="  时 : 分", bg=CARD, fg=MUTED).pack(
            side="left", padx=parent.s(6))

        self.label("运行哪个组", 2)
        self.var_group = tk.StringVar(
            value=schedule.get("group") or "（当前组）")
        ttk.Combobox(self, textvariable=self.var_group, state="readonly",
                     values=["（当前组）"] + list(group_names),
                     font=("Microsoft YaHei UI", 9)).grid(
            row=2, column=1, sticky="ew", padx=(0, parent.s(16)),
            pady=parent.s(5))

        tk.Frame(self, bg=BORDER).grid(row=3, column=0, columnspan=2,
                                       sticky="ew", pady=parent.s(10))
        self.label("结束通知", 4)
        self.var_sound = tk.BooleanVar(value=bool(settings.get("sound")))
        ttk.Checkbutton(self, text="播放提示音",
                        variable=self.var_sound).grid(
            row=4, column=1, sticky="w", padx=(0, parent.s(16)))

        self.label("Webhook", 5)
        self.var_webhook = tk.StringVar(value=settings.get("webhook", ""))
        ttk.Entry(self, textvariable=self.var_webhook).grid(
            row=5, column=1, sticky="ew", padx=(0, parent.s(16)),
            pady=parent.s(5))
        tk.Label(self, text="填企业微信 / 钉钉机器人地址，运行结束自动推送；"
                            "留空则不推送",
                 bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 8),
                 wraplength=parent.s(400), justify="left", anchor="w").grid(
            row=6, column=0, columnspan=2, sticky="ew",
            padx=parent.s(16), pady=(0, parent.s(2)))

        tk.Frame(self, bg=BORDER).grid(row=7, column=0, columnspan=2,
                                       sticky="ew", pady=parent.s(8))
        self.label("无人值守（Windows 计划任务）", 8)
        tk.Label(self, text="由 Windows 负责，关掉界面也会按时跑；"
                            "不需要管理员权限，创建后可在「任务计划程序」里查看。",
                 bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 8),
                 wraplength=parent.s(400), justify="left", anchor="w").grid(
            row=9, column=0, columnspan=2, sticky="ew",
            padx=parent.s(16), pady=(0, parent.s(2)))
        row_task = tk.Frame(self, bg=CARD)
        row_task.grid(row=10, column=0, columnspan=2, pady=(parent.s(2),
                                                            parent.s(4)))
        self.btn_task_create = uikit.RoundButton(
            row_task, "创建计划任务", "calendar", self._create_task,
            kind="outline", scale=parent.scale, bg=CARD, height=30,
            pad=parent.s(10), font_size=8,
            tooltip="用上面的时间和执行组创建每天运行的计划任务")
        self.btn_task_create.pack(side="left", padx=parent.s(4))
        self.btn_task_delete = uikit.RoundButton(
            row_task, "删除计划任务", "trash", self._delete_task,
            kind="ghost", scale=parent.scale, bg=CARD, height=30,
            pad=parent.s(10), font_size=8)
        self.btn_task_delete.pack(side="left", padx=parent.s(4))
        self.lbl_task = tk.Label(self, text="", bg=CARD, fg=MUTED,
                                 font=("Microsoft YaHei UI", 8),
                                 wraplength=parent.s(400), justify="left")
        self.lbl_task.grid(row=11, column=0, columnspan=2,
                           padx=parent.s(16), pady=(0, parent.s(4)))

        buttons = tk.Frame(self, bg=CARD)
        buttons.grid(row=12, column=0, columnspan=2, pady=parent.s(14))
        uikit.RoundButton(buttons, "取消", "close", self.cancel, kind="ghost",
                          scale=parent.scale, bg=CARD).pack(
            side="left", padx=parent.s(4))
        uikit.RoundButton(buttons, "保存", "check", self.ok, kind="accent",
                          scale=parent.scale, bg=CARD).pack(
            side="left", padx=parent.s(4))
        self.center()

    def ok(self) -> None:
        def number(value, default):
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return default

        hour = min(23, max(0, number(self.var_hour.get(), 9)))
        minute = min(59, max(0, number(self.var_minute.get(), 0)))
        group = self.var_group.get()
        self.result = {
            "schedule": {"enabled": bool(self.var_enabled.get()),
                         "time": f"{hour:02d}:{minute:02d}",
                         "group": "" if group == "（当前组）" else group},
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
            self.lbl_task.configure(text=text, fg="#2e7d32")
            self.app._log("info", f"Windows 计划任务已创建：{name}")
        else:
            detail = (proc.stderr or proc.stdout or "").strip()[:500]
            self.lbl_task.configure(text="创建失败：" + detail,
                                    fg="#c62828")
            messagebox.showerror("创建失败", detail or "schtasks 返回错误",
                                 parent=self)

    def _delete_task(self) -> None:
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
            self.lbl_task.configure(text=f"已删除：{name}", fg="#2e7d32")
            self.app._log("info", f"Windows 计划任务已删除：{name}")
        else:
            detail = (proc.stderr or proc.stdout or "").strip()[:500]
            self.lbl_task.configure(text="删除失败：" + detail, fg="#c62828")
            messagebox.showerror("删除失败", detail or "schtasks 返回错误",
                                 parent=self)


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
                               font=("Microsoft YaHei UI", 9))
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
            self.status.configure(text="无法启动鼠标录制（装不上钩子）")

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
        self.win.configure(bg="#1e2228")
        s = parent.s
        width, height = s(320), s(58)
        x = (self.win.winfo_screenwidth() - width) // 2
        self.win.geometry(f"{width}x{height}+{x}+{s(8)}")

        box = tk.Frame(self.win, bg="#1e2228")
        box.pack(fill="both", expand=True, padx=s(12), pady=s(7))
        self.counts = tk.Label(box, text="准备开始", bg="#1e2228",
                               fg="#7cc7ff",
                               font=("Microsoft YaHei UI", 8, "bold"))
        self.counts.pack(anchor="w")
        self.text = tk.Label(box, text="", bg="#1e2228", fg="#e8ecf2",
                             font=("Microsoft YaHei UI", 9))
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

        columns = ("refs", "size")
        self.tree = ttk.Treeview(self, columns=columns, show="headings",
                                 selectmode="browse")
        self.tree.heading("#0", text="图片文件")
        self.tree.heading("refs", text="被谁引用")
        self.tree.heading("size", text="大小")
        self.tree.column("#0", width=200)
        self.tree.column("refs", width=280)
        self.tree.column("size", width=70, anchor="e")
        self.tree.grid(row=0, column=0, sticky="nsew", padx=parent.s(12),
                       pady=(parent.s(12), parent.s(6)))
        scroll = ttk.Scrollbar(self, orient="vertical",
                               command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns", pady=(parent.s(12), 0))
        self.tree.configure(yscrollcommand=scroll.set)

        self.summary = tk.Label(self, text="", bg=CARD, fg=MUTED,
                                font=("Microsoft YaHei UI", 8))
        self.summary.grid(row=1, column=0, columnspan=2, sticky="w",
                          padx=parent.s(14))

        buttons = tk.Frame(self, bg=CARD)
        buttons.grid(row=2, column=0, columnspan=2, pady=parent.s(10))
        uikit.RoundButton(buttons, "重命名并同步引用", "pencil",
                          self._rename, kind="outline", scale=parent.scale,
                          bg=CARD, height=30, pad=parent.s(8),
                          font_size=8).pack(side="left", padx=parent.s(4))
        uikit.RoundButton(buttons, "清理未引用图片", "trash",
                          self._clean_unused, kind="danger",
                          scale=parent.scale, bg=CARD, height=30,
                          pad=parent.s(8), font_size=8).pack(
            side="left", padx=parent.s(4))
        uikit.RoundButton(buttons, "打开目录", "folder_open",
                          self._open_dir, kind="ghost", scale=parent.scale,
                          bg=CARD, height=30, pad=parent.s(8),
                          font_size=8).pack(side="left", padx=parent.s(4))
        uikit.RoundButton(buttons, "关闭", "close", self.cancel,
                          kind="ghost", scale=parent.scale, bg=CARD,
                          height=30, pad=parent.s(8), font_size=8).pack(
            side="left", padx=parent.s(4))
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
                text=name, values=("、".join(users) if users else "（未引用）",
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
        super().__init__(parent, "恢复配置备份", width=420, height=300)
        self.names = names
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.listbox = tk.Listbox(self, font=("Microsoft YaHei UI", 9))
        self.listbox.grid(row=0, column=0, columnspan=2, sticky="nsew",
                          padx=parent.s(12), pady=(parent.s(12), 0))
        for name in names:
            # rpa_settings_20261020_121500.json → 2026-10-20 12:15:00
            stamp = name[len("rpa_settings_"):-len(".json")]
            pretty = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} " \
                     f"{stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}"
            self.listbox.insert("end", pretty)
        self.listbox.selection_set(0)

        buttons = tk.Frame(self, bg=CARD)
        buttons.grid(row=1, column=0, columnspan=2, pady=parent.s(12))
        uikit.RoundButton(buttons, "取消", "close", self.cancel, kind="ghost",
                          scale=parent.scale, bg=CARD).pack(
            side="left", padx=parent.s(4))
        uikit.RoundButton(buttons, "恢复选中", "check", self.ok,
                          kind="accent", scale=parent.scale, bg=CARD).pack(
            side="left", padx=parent.s(4))
        self.listbox.bind("<Double-1>", lambda e: self.ok())
        self.center()

    def ok(self) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        self.result = self.names[selection[0]]
        self.destroy()


class SettingsDialog(BaseDialog):
    """运行设置：倒计时、相似度、图片目录。"""

    def __init__(self, parent: App, settings: dict, image_dir: str):
        super().__init__(parent, "运行设置", width=430, height=365)
        self.columnconfigure(1, weight=1)
        # 用传进来的设置做底稿，保存时只改本对话框的键，别冲掉定时等配置
        self._base_settings = dict(settings)

        self.label("启动倒计时（秒）", 0)
        self.var_delay = tk.StringVar(value=str(settings.get("delay", 3)))
        ttk.Spinbox(self, from_=0, to=30, width=8,
                    textvariable=self.var_delay).grid(
            row=0, column=1, sticky="w", padx=(0, parent.s(16)), pady=parent.s(5))

        self.label("图片相似度", 1)
        self.var_conf = tk.StringVar(value=str(settings.get("confidence", 0.9)))
        ttk.Spinbox(self, from_=0.5, to=1.0, increment=0.01, width=8,
                    textvariable=self.var_conf).grid(
            row=1, column=1, sticky="w", padx=(0, parent.s(16)), pady=parent.s(5))

        self.label("图片目录", 2)
        row = tk.Frame(self, bg=CARD)
        row.grid(row=2, column=1, sticky="ew", padx=(0, parent.s(16)),
                 pady=parent.s(5))
        row.columnconfigure(0, weight=1)
        self.var_dir = tk.StringVar(value=image_dir)
        ttk.Entry(row, textvariable=self.var_dir).grid(row=0, column=0,
                                                       sticky="ew")
        uikit.RoundButton(row, "浏览", "folder_open", self._browse,
                          kind="outline", scale=parent.scale, bg=CARD,
                          height=30, pad=parent.s(8), font_size=8).grid(
            row=0, column=1, padx=(parent.s(4), 0))

        self.label("运行时显示迷你进度条", 3)
        self.var_mini = tk.BooleanVar(
            value=bool(settings.get("show_mini_bar", True)))
        ttk.Checkbutton(self, variable=self.var_mini).grid(
            row=3, column=1, sticky="w", padx=(0, parent.s(16)),
            pady=parent.s(5))

        self.label("失败时保存截图", 4)
        self.var_shot = tk.BooleanVar(
            value=bool(settings.get("shot_on_fail", True)))
        ttk.Checkbutton(self, variable=self.var_shot).grid(
            row=4, column=1, sticky="w", padx=(0, parent.s(16)),
            pady=parent.s(5))

        self.label("数据跑完写结果文件", 5)
        self.var_results = tk.BooleanVar(
            value=bool(settings.get("save_results", True)))
        ttk.Checkbutton(self, variable=self.var_results).grid(
            row=5, column=1, sticky="w", padx=(0, parent.s(16)),
            pady=parent.s(5))

        # 说明文字横跨两列并自动换行，避免把左侧标签列撑得过宽
        tk.Label(self, text="相似度越低越容易识别，也越容易误点。"
                            "失败截图在 reports/shots；数据结果写在数据文件旁边，"
                            "下次运行同一份数据时可以选择从上次停下的地方继续。",
                 bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 8),
                 wraplength=parent.s(390), justify="left", anchor="w").grid(
            row=6, column=0, columnspan=2, sticky="ew",
            padx=parent.s(16), pady=(parent.s(2), 0))

        buttons = tk.Frame(self, bg=CARD)
        buttons.grid(row=7, column=0, columnspan=2, pady=parent.s(14))
        uikit.RoundButton(buttons, "取消", "close", self.cancel, kind="ghost",
                          scale=parent.scale, bg=CARD).pack(side="left",
                                                            padx=parent.s(4))
        uikit.RoundButton(buttons, "保存", "check", self.ok, kind="accent",
                          scale=parent.scale, bg=CARD).pack(side="left",
                                                            padx=parent.s(4))
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
        # 在现有设置上改键，别把定时运行等其它配置冲掉
        self.result = dict(self._base_settings)
        self.result.update({
            "delay": int(number(self.var_delay.get(), 3)),
            "confidence": number(self.var_conf.get(), 0.9),
            "image_dir": directory,
            "show_mini_bar": bool(self.var_mini.get()),
            "shot_on_fail": bool(self.var_shot.get()),
            "save_results": bool(self.var_results.get()),
        })
        self.destroy()


class DataPreviewDialog(BaseDialog):
    """带数据运行前的预览：看清列名、行数和前几行再开跑。"""

    def __init__(self, parent: App, path: str, data: list[dict]):
        super().__init__(parent, "带数据运行 · 预览", width=560, height=400)
        columns = list(data[0].keys()) if data else []
        shown = columns[:6]
        self.columnconfigure(0, weight=1)

        tk.Label(self, text=f"文件：{os.path.basename(path)}", bg=CARD, fg=TEXT,
                 font=("Microsoft YaHei UI", 10, "bold")).grid(
            row=0, column=0, sticky="w", padx=parent.s(16),
            pady=(parent.s(14), parent.s(2)))
        tk.Label(self, text=f"共 {len(data)} 行数据 · "
                            f"{len(columns)} 列：{'、'.join(columns) or '（无）'}",
                 bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 8),
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
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        if len(columns) > 6:
            tk.Label(wrap, text=f"（只显示前 6 列，共 {len(columns)} 列）",
                     bg=CARD, fg=MUTED,
                     font=("Microsoft YaHei UI", 8)).grid(
                row=1, column=0, sticky="w", pady=(parent.s(4), 0))

        tk.Label(self, text="步骤内容里写 {列名}，运行时会被替换成当前行的值；"
                            "{index} 是行号。", bg=CARD, fg=MUTED,
                 font=("Microsoft YaHei UI", 8)).grid(
            row=3, column=0, sticky="w", padx=parent.s(16), pady=(0, parent.s(8)))

        buttons = tk.Frame(self, bg=CARD)
        buttons.grid(row=4, column=0, pady=(0, parent.s(12)))
        uikit.RoundButton(buttons, "取消", "close", self.cancel, kind="ghost",
                          scale=parent.scale, bg=CARD).pack(side="left",
                                                            padx=parent.s(4))
        uikit.RoundButton(buttons, "开始运行", "run", self.ok, kind="accent",
                          scale=parent.scale, bg=CARD).pack(side="left",
                                                            padx=parent.s(4))
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
                          fg=TEXT, font=("Microsoft YaHei UI", 10, "bold"),
                          wraplength=parent.s(410), justify="left")
        detail.grid(row=1, column=0, sticky="w", padx=parent.s(16),
                    pady=(parent.s(2), parent.s(6)))
        if step.repeat and step.repeat != 1:
            tk.Label(self, text=f"重复 {step.repeat:g} 次", bg=CARD, fg=MUTED,
                     font=("Microsoft YaHei UI", 8)).grid(
                row=2, column=0, sticky="w", padx=parent.s(16))
        tk.Label(self, text="运行：执行这一步　·　跳过：不执行　·　"
                            "停止：结束整个运行（Esc）", bg=CARD, fg=MUTED,
                 font=("Microsoft YaHei UI", 8)).grid(
            row=3, column=0, sticky="w", padx=parent.s(16),
            pady=(parent.s(8), 0))

        buttons = tk.Frame(self, bg=CARD)
        buttons.grid(row=4, column=0, pady=parent.s(16))
        uikit.RoundButton(buttons, "运行这一步", "run", self._run,
                          kind="accent", scale=parent.scale,
                          bg=CARD, height=30, pad=parent.s(10),
                          font_size=8).pack(side="left", padx=parent.s(4))
        uikit.RoundButton(buttons, "跳过", "debug", self._skip,
                          kind="outline", scale=parent.scale, bg=CARD,
                          height=30, pad=parent.s(10),
                          font_size=8).pack(side="left", padx=parent.s(4))
        uikit.RoundButton(buttons, "停止", "stop", self._stop,
                          kind="danger", scale=parent.scale, bg=CARD,
                          height=30, pad=parent.s(10),
                          font_size=8).pack(side="left", padx=parent.s(4))
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
                     font=("Consolas", 9)).grid(
                row=row, column=0, sticky="w", padx=(parent.s(18), parent.s(10)),
                pady=parent.s(4))
            tk.Label(self, text=text, bg=CARD, fg=TEXT,
                     font=("Microsoft YaHei UI", 9)).grid(
                row=row, column=1, sticky="w")
        tk.Label(self, text="运行安全：把鼠标快速甩到屏幕左上角，"
                            "可立即中断执行。", bg=CARD, fg=MUTED,
                 font=("Microsoft YaHei UI", 8),
                 wraplength=parent.s(330), justify="left").grid(
            row=len(self.ROWS), column=0, columnspan=2, sticky="w",
            padx=parent.s(18), pady=(parent.s(10), 0))
        buttons = tk.Frame(self, bg=CARD)
        buttons.grid(row=len(self.ROWS) + 1, column=0, columnspan=2,
                     pady=parent.s(14))
        uikit.RoundButton(buttons, "知道了", "check", self.ok, kind="accent",
                          scale=parent.scale, bg=CARD).pack(side="left",
                                                            padx=parent.s(4))
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
