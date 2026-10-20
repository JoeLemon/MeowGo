# -*- coding: utf-8 -*-
"""图形界面：左侧执行组（可收放），右侧组步骤编辑，日志可收放。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
import types
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional

from . import assets, uikit
from .engine import (IMAGE_TYPES, STEP_TYPES, Executor, Group, Project, Step,
                     export_excel, import_excel, validate_project)

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
BACKUPS_DIR = os.path.join(APP_DIR, "backups")
MAX_CONFIG_BACKUPS = 10

# 配色
BG = "#f4f6fa"
CARD = "#ffffff"
ACCENT = "#3b6ff5"
TEXT = "#22262e"
MUTED = "#7a8290"
BORDER = "#e2e7f0"
LOG_BG = "#1e2228"
LOG_FG = "#d7dce3"

TYPE_ICONS = {
    "click": "image", "dblclick": "image", "rightclick": "image",
    "input": "text", "wait": "clock", "scroll": "scroll",
    "hotkey": "keyboard", "time": "calendar", "cmd": "terminal",
    "call": "link", "if_image": "check", "window": "open",
    "click_xy": "mouse", "move_xy": "mouse",
}


def enable_tree_drag(tree, on_drop) -> None:
    """给 Treeview 加拖拽换行（组树 / 步骤树共用）。

    按住并移动超过 8 px 才算拖拽，因此单击选中、双击编辑都不受影响；
    拖拽过程中选中行跟随鼠标，作为落点提示。
    on_drop(source_iid, target_iid) 在松开时调用。
    """
    state = {"iid": None, "y": 0, "dragging": False}

    def on_press(event):
        state["iid"] = tree.identify_row(event.y)
        state["y"] = event.y
        state["dragging"] = False

    def on_motion(event):
        if state["iid"] is None:
            return
        if not state["dragging"] and abs(event.y - state["y"]) < 8:
            return                      # 还没超过阈值，算单击
        state["dragging"] = True
        target = tree.identify_row(event.y)
        if target and target != state["iid"]:
            tree.selection_set(target)  # 落点提示

    def on_release(event):
        iid, dragging = state["iid"], state["dragging"]
        state["iid"] = None
        state["dragging"] = False
        if not (iid and dragging):
            return
        target = tree.identify_row(event.y)
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
        self.log_visible = True
        self._dirty = False
        self._icons: dict[tuple, object] = {}

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

        if not self.project.groups:
            self.project.groups.append(Group(self.project.unique_name("默认组")))
        self._refresh_all(select_group=0)
        self._start_global_hotkey()
        self.after(120, self._drain_queue)
        self.after(2000, self._autosave_tick)
        self.after(1000, self._schedule_tick)
        self._check_dpi_setup()
        self._log("info", f"欢迎使用 {APP_TITLE}")
        self._log("muted", "左侧维护执行组，右侧编辑步骤；「调用执行组」指令可复用其它组。")

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
        style.configure("Treeview.Heading", background="#eef2f8",
                        foreground="#66708a",
                        font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#dbe7ff")],
                  foreground=[("selected", TEXT)])
        style.configure("TEntry", fieldbackground="white", padding=self.s(4),
                        bordercolor=BORDER)
        style.configure("TCombobox", padding=self.s(3))
        style.configure("TSpinbox", padding=self.s(3))
        style.configure("Vertical.TScrollbar", background="#ccd3e0",
                        troughcolor=CARD, bordercolor=CARD,
                        arrowcolor="#8d97ab", width=self.s(11))

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
        index = self._selected_step()
        total = len(group.steps) if group else 0
        has = index is not None
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
            MI("编辑步骤…", "pencil", self.edit_step, "F2", enabled=has),
            MI("复制步骤", "copy", self.duplicate_step, enabled=has),
            MI("删除步骤", "trash", self.delete_step, "Delete", enabled=has,
               danger=True),
            "-",
            MI("截取屏幕并替换图片…", "image",
               lambda: self.capture_region(self._apply_capture),
               enabled=has and group.steps[index].type in IMAGE_TYPES),
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
            MI("运行设置…", "sliders", self.open_settings),
            MI("图片管家…", "image", self.open_image_manager,
               f"{self._image_count()} 张"),
        ]

    def _help_items(self) -> list:
        MI = uikit.MenuItem
        return [MI("使用说明", "info", self.show_help),
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
            side="left", padx=(self.s(6), 0))
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
        tk.Label(head, text="双击单元格可直接修改，右键为完整操作菜单", bg=CARD,
                 fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(
            side="left", padx=(self.s(10), 0))

        wrap = tk.Frame(panel, bg=CARD)
        wrap.pack(fill="both", expand=True, padx=(self.s(12), 0),
                  pady=(0, self.s(12)))
        columns = ("no", "type", "content", "repeat", "note")
        self.step_tree = ttk.Treeview(wrap, columns=columns,
                                      show="tree headings", selectmode="browse")
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
        self.step_tree.tag_configure("running", background="#fff4d6")
        self.step_tree.bind("<Double-1>", self._on_step_double)
        self.step_tree.bind("<Button-3>", self._step_menu)
        self.step_tree.bind("<Delete>", self._on_step_key_delete)
        self.step_tree.bind("<F2>", self._on_step_key_edit)
        enable_tree_drag(self.step_tree, self._on_step_drop)

        self.step_empty = tk.Label(
            wrap, text="这个组还没有步骤\n在列表上右键 → 新增步骤", bg=CARD,
            fg=MUTED, justify="center", font=("Microsoft YaHei UI", 9))

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

        self.log_text = tk.Text(self.log_card, height=7, bg=LOG_BG, fg=LOG_FG,
                                insertbackground=LOG_FG, relief="flat",
                                font=("Consolas", 9), wrap="word",
                                padx=self.s(10), pady=self.s(6), state="disabled")
        self.log_text.pack(fill="x", padx=1, pady=(0, 1))
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
        self.image_label = tk.Label(bar, text="", bg=BG, fg=MUTED,
                                    font=("Microsoft YaHei UI", 9))
        self.image_label.pack(side="right")

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-g>", lambda e: self.add_group())
        self.bind("<Control-Return>", lambda e: self.add_step())
        self.bind("<F5>", lambda e: self.run_group())
        self.bind("<F2>", lambda e: self.edit_step())
        self.bind("<Delete>", self._on_delete_key)
        self.bind("<Control-z>", lambda e: self.undo())
        self.bind("<Control-y>", lambda e: self.redo())
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
        data.update({"version": 2, "image_dir": self.image_dir,
                     "groups": [group.to_dict() for group in self.project.groups]})
        return data

    def _save_state(self) -> None:
        """把执行组与设置写回程序目录的状态文件（没有“项目”概念）。

        写之前先把旧文件备份到 backups/（只留最近几份），改坏了能找回。
        """
        self._backup_settings()
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
                json.dump(self._state_dict(), fh, ensure_ascii=False, indent=2)
            self._dirty = False
        except OSError:
            pass

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
                                   "#8a93a5"))
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
        for index, step in enumerate(group.steps):
            repeat = "" if not step.repeat else f"{step.repeat:g}"
            iid = str(index)
            self.step_tree.insert(
                "", "end", iid=iid,
                image=self.pic(TYPE_ICONS.get(step.type, "note"), 14),
                values=(index + 1, step.label(), step.content, repeat, step.note))
        if group.steps:
            self.step_empty.place_forget()
        else:
            self.step_empty.place(relx=0.5, rely=0.4, anchor="center")
        if select is not None and 0 <= select < len(group.steps):
            self.step_tree.selection_set(str(select))
            self.step_tree.see(str(select))
        self._set_status(f"当前组：{group.name}（{len(group.steps)} 步）"
                         if not self.running else self.status_var.get())

    def _selected_step(self) -> Optional[int]:
        selection = self.step_tree.selection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except ValueError:
            return None

    def _refresh_step_row(self, index: int) -> None:
        group = self._current_group()
        if group is None or not self.step_tree.exists(str(index)):
            return
        step = group.steps[index]
        repeat = "" if not step.repeat else f"{step.repeat:g}"
        self.step_tree.item(str(index),
                            image=self.pic(TYPE_ICONS.get(step.type, "note"), 14),
                            values=(index + 1, step.label(), step.content,
                                    repeat, step.note))
        self._refresh_groups()
        self._mark_dirty()

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
        index = self._selected_step()
        position = len(group.steps) if index is None else index + 1
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
        index = self._selected_step()
        if group is None or index is None:
            return
        self._push_undo("复制步骤")
        group.steps.insert(index + 1, group.steps[index].copy())
        self._refresh_all(select_step=index + 1)

    def delete_step(self) -> None:
        group = self._current_group()
        index = self._selected_step()
        if group is None or index is None:
            return
        step = group.steps[index]
        if step.content and not messagebox.askyesno(
                "确认删除", f"确定删除第 {index + 1} 步（{step.label()}）？",
                parent=self):
            return
        self._push_undo("删除步骤")
        group.steps.pop(index)
        self._refresh_all(select_step=min(index, len(group.steps) - 1))

    def move_step(self, delta: int) -> None:
        group = self._current_group()
        index = self._selected_step()
        if group is None or index is None:
            return
        target = index + delta
        if not (0 <= target < len(group.steps)):
            return
        self._push_undo("移动步骤")
        group.steps[index], group.steps[target] = \
            group.steps[target], group.steps[index]
        self._refresh_all(select_step=target)

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
            self.step_tree.selection_set(row)
        group = self._current_group()
        index = self._selected_step()
        total = len(group.steps) if group else 0
        has = index is not None
        is_image = has and group.steps[index].type in IMAGE_TYPES
        items = [
            uikit.MenuItem("新增步骤…", "add", self.add_step, "Ctrl+Enter",
                           accent=True),
            uikit.MenuItem("编辑步骤…", "pencil", self.edit_step, "F2",
                           enabled=has),
            uikit.MenuItem("复制步骤", "copy", self.duplicate_step, enabled=has),
            uikit.MenuItem("截取屏幕并替换图片…", "image",
                           lambda: self.capture_region(self._apply_capture),
                           enabled=is_image),
            "-",
            uikit.MenuItem("上移", "up", lambda: self.move_step(-1),
                           enabled=has and index > 0),
            uikit.MenuItem("下移", "down", lambda: self.move_step(1),
                           enabled=has and index < total - 1),
            "-",
            uikit.MenuItem("删除步骤", "trash", self.delete_step, "Delete",
                           enabled=has, danger=True),
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
        canvas.create_text(width // 2, self.s(60),
                           text="按住鼠标拖拽框选要识别的区域，Esc 取消",
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
            self.deiconify()
            self.lift()
            if abs(x2 - x1) < 8 or abs(y2 - y1) < 8:
                self._set_status("截图区域太小，已取消")
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
            overlay.destroy()
            self.deiconify()
            self.lift()

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
        if step.type not in IMAGE_TYPES:
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
                elif kind == "end":
                    self._on_run_end(str(payload))
                elif kind == "stop":
                    self.stop_run()
        except queue.Empty:
            pass
        self.after(120, self._drain_queue)

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
        self._start(self._current_group_name(), data=data)

    def _start(self, entry: Optional[str], loop: bool = False,
               data: Optional[list] = None) -> None:
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
        self.executor.on_step = self._mark_running_step
        delay = int(self.settings.get("delay", 3))
        self.iconify()
        self._countdown(delay, lambda: self._launch(entry, loop))

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

    def _launch(self, entry: Optional[str], loop: bool) -> None:
        self.running = True
        self.paused = False
        for button in (self.btn_run, self.btn_loop):
            button.set_enabled(False)
        self.btn_pause.set_enabled(True)
        self.btn_stop.set_enabled(True)
        self._set_status("运行中…" + ("（循环）" if loop else ""))
        if not self.log_visible:      # 收起时自动展开，方便看进度
            self.toggle_log()

        def worker():
            result = self.executor.run(entry, loop=loop)
            self.ui_queue.put(("end", result))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _on_run_end(self, result: str) -> None:
        self.running = False
        self.paused = False
        for button in (self.btn_run, self.btn_loop, self.btn_pause):
            button.set_enabled(True)
        self.btn_pause.set_text("暂停", "pause")
        self.btn_stop.set_enabled(False)
        if self.step_tree.exists("_running"):
            self.step_tree.delete("_running")
        self.deiconify()
        self.lift()
        self._refresh_all()
        self._set_status({"finished": "执行完成", "stopped": "已停止",
                          "error": "执行出错"}.get(result, result))
        self._write_run_report(result)
        self._notify_finish(result)

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
        """把这次运行写成 markdown 报告，放在程序目录 reports/ 下。"""
        try:
            from rpa.engine import report_markdown

            os.makedirs(REPORTS_DIR, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            path = os.path.join(REPORTS_DIR, f"run_{stamp}.md")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(report_markdown(self.executor.report(), result))
            self._log("info", f"运行报告已保存：{path}")
        except OSError as exc:
            self._log("warn", f"运行报告保存失败：{exc}")

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
        self.ui_queue.put(("step", (group_name, index)))

    def _highlight_step(self, group_name: str, index: int) -> None:
        names = self.project.names()
        if group_name in names:
            position = names.index(group_name)
            if position != self.group_index:
                self.group_index = position
                self._refresh_groups()
                self._refresh_steps()
        for item in self.step_tree.get_children():
            if item != str(index):
                self.step_tree.item(item, tags=())
        if self.step_tree.exists(str(index)):
            self.step_tree.item(str(index), tags=("running",))
            self.step_tree.see(str(index))
        self._set_status(f"正在执行：{group_name} · 第 {index + 1} 步")

    # ------------------------------------------------------- 全局停止热键
    def _start_global_hotkey(self) -> None:
        if os.name != "nt":
            return

        def loop():
            try:
                import ctypes
                from ctypes import wintypes

                user32 = ctypes.windll.user32
                if not user32.RegisterHotKey(None, 1, 0, 0x78):  # VK_F9
                    self._log("warn", "全局停止热键 F9 注册失败（可能被占用）")
                    return
                msg = wintypes.MSG()
                while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    if msg.message == 0x0312:  # WM_HOTKEY
                        self.ui_queue.put(("stop", None))
                user32.UnregisterHotKey(None, 1)
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
                  "F5 运行当前组　F9 停止（全局，任何时候都有效）",
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
        self.var_type = tk.StringVar()
        self.combo = ttk.Combobox(
            self, textvariable=self.var_type, state="readonly",
            values=[f"{i + 1} · {info[0]}"
                    for i, info in enumerate(STEP_TYPES.values())],
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
        # 条件步骤的第二段：要执行的组名（写成 图片|组名 的图形化版）
        self.var_group = tk.StringVar()
        if editing.type == "if_image" and "|" in editing.content:
            image, _, group = editing.content.partition("|")
            self.var_content.set(image.strip())
            self.var_group.set(group.strip())
        self.group_entry = ttk.Entry(content, textvariable=self.var_group,
                                     width=10, font=("Microsoft YaHei UI", 9))
        self.group_entry.grid(row=0, column=1, padx=(parent.s(2), 0))
        self.btn_pick_group = uikit.RoundButton(
            content, "选组", "list", self._pick_group, kind="outline",
            scale=parent.scale, bg=CARD, height=30, pad=parent.s(8),
            font_size=8, tooltip="选择条件满足时要执行的执行组")
        self.btn_pick_group.grid(row=0, column=2, padx=(parent.s(4), 0))
        self.btn_shot = uikit.RoundButton(
            content, "截图", "image",
            lambda: parent.capture_region(self._set_image), kind="outline",
            scale=parent.scale, bg=CARD, height=30, pad=parent.s(8),
            font_size=8)
        self.btn_shot.grid(row=0, column=3, padx=(parent.s(4), 0))
        self.btn_pick = uikit.RoundButton(
            content, "选图", "folder_open", self._pick_image, kind="outline",
            scale=parent.scale, bg=CARD, height=30, pad=parent.s(8),
            font_size=8)
        self.btn_pick.grid(row=0, column=4, padx=(parent.s(4), 0))
        # 按键录入：热键步骤可以直接按组合键，不用输入法打字
        self.btn_record = uikit.RoundButton(
            content, "", "keyboard", self._start_record, kind="outline",
            scale=parent.scale, bg=CARD, height=30, pad=parent.s(8),
            icon_size=16, tooltip="按键录入：直接按组合键，如 Ctrl+Shift+A")
        self.btn_record.grid(row=0, column=5, padx=(parent.s(4), 0))
        # 坐标拾取：倒计时几秒后取当前鼠标位置
        self.btn_grab_xy = uikit.RoundButton(
            content, "拾取", "mouse", self._grab_xy, kind="outline",
            scale=parent.scale, bg=CARD, height=30, pad=parent.s(8),
            font_size=8, tooltip="把鼠标移到目标位置，点此倒计时拾取坐标")
        self.btn_grab_xy.grid(row=0, column=6, padx=(parent.s(4), 0))

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

        # 图片步骤的等待策略：默认 0 = 一直等（与旧版一致）
        self.label("等待超时（秒）", 4)
        self.var_timeout = tk.StringVar(
            value="" if not editing.timeout else f"{editing.timeout:g}")
        self.spin_timeout = ttk.Spinbox(self, from_=0, to=3600, width=8,
                                        textvariable=self.var_timeout)
        self.spin_timeout.grid(row=4, column=1, sticky="w",
                               padx=(0, parent.s(16)), pady=parent.s(5))
        self.hint_timeout = tk.Label(
            self, text="0 = 一直等图片出现（默认）", bg=CARD, fg=MUTED,
            font=("Microsoft YaHei UI", 8))
        self.hint_timeout.grid(row=4, column=1, sticky="w",
                               padx=parent.s(70))

        self.label("超时后", 5)
        self.var_on_fail = tk.StringVar(
            value="跳过这步" if editing.on_fail != "stop" else "停止执行")
        self.combo_fail = ttk.Combobox(
            self, textvariable=self.var_on_fail, state="readonly",
            values=["跳过这步", "停止执行"], width=10,
            font=("Microsoft YaHei UI", 9))
        self.combo_fail.grid(row=5, column=1, sticky="w",
                             padx=(0, parent.s(16)), pady=parent.s(5))

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
        self.var_type.set(f"{list(STEP_TYPES).index(editing.type) + 1} · "
                          f"{editing.label()}")
        self._sync()
        self.entry.focus_set()
        self.center()

    # -- 辅助 -------------------------------------------------------------
    def _type_key(self) -> str:
        try:
            return list(STEP_TYPES)[int(self.var_type.get().split("·")[0].strip()) - 1]
        except (ValueError, IndexError):
            return "click"

    def _sync(self) -> None:
        if self._recorder.active:
            return                     # 录制中不响应类型切换
        key = self._type_key()
        _, hint, need_repeat, repeat_hint = STEP_TYPES[key]
        text = hint if not repeat_hint else f"{hint}｜重复次数：{repeat_hint}"
        if key == "call":
            names = "、".join(self.project.names()) or "（还没有其它组）"
            text += f"\n可调用：{names}"
        if key == "hotkey":
            text += "\n也可点右侧键盘按钮直接按组合键录入（Ctrl+Shift+A 这种）"
        if key == "if_image":
            text += ("\n左边填判断用图片，右边填满足后要执行的组"
                     "（可点「选组」）")
        if key == "window":
            text += "\n标题写窗口名的一部分就行，如 activate:记事本"
        if key in ("click_xy", "move_xy"):
            text += "\n也可以把鼠标移到目标处，点「拾取」自动填坐标"
        self._show_hint(text)
        is_image = key in IMAGE_TYPES
        self.btn_shot.set_enabled(is_image)
        self.btn_pick.set_enabled(is_image)
        self.btn_record.set_enabled(key == "hotkey")
        # 条件步骤：显示“组名”第二段；坐标步骤：显示拾取按钮
        is_if = key == "if_image"
        is_xy = key in ("click_xy", "move_xy")
        self.group_entry.grid() if is_if else self.group_entry.grid_remove()
        self.btn_pick_group.set_enabled(is_if)
        self.btn_grab_xy.set_enabled(is_xy)
        # 等待策略只对图片步骤有意义
        self.spin_timeout.grid() if is_image else \
            self.spin_timeout.grid_remove()
        self.hint_timeout.grid() if is_image else \
            self.hint_timeout.grid_remove()
        try:
            timeout = float(self.var_timeout.get() or 0)
        except ValueError:
            timeout = 0
        self.combo_fail.grid() if is_image and timeout > 0 else \
            self.combo_fail.grid_remove()
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
        self.note_entry.configure(state="disabled" if active else "normal")
        self.spin.configure(state="disabled" if active else "normal")
        self.spin_timeout.configure(state="disabled" if active else "normal")
        self.combo_fail.configure(state="disabled" if active else "readonly")
        for button in (self.btn_shot, self.btn_pick, self.btn_record,
                       self.btn_cancel, self.btn_ok, self.btn_pick_group,
                       self.btn_grab_xy):
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
        self.var_content.set(name)
        self.lift()
        self.focus_force()

    def _pick_image(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="选择图片", initialdir=self.app.image_dir,
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp")])
        if path:
            self._set_image(self.app._relative_image(path))

    def cancel(self) -> None:
        self._recorder.stop()
        super().cancel()

    def ok(self) -> None:
        if self._recorder.active:
            return                     # 录制中不提交（按钮本就禁用，双保险）
        key = self._type_key()
        if key == "if_image":
            content = f"{self.var_content.get().strip()}|" \
                      f"{self.var_group.get().strip()}"
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
        if key in IMAGE_TYPES:
            try:
                step.timeout = float(self.var_timeout.get() or 0)
            except ValueError:
                messagebox.showwarning("格式错误", "等待超时必须是数字",
                                       parent=self)
                return
            step.on_fail = "stop" if self.var_on_fail.get() == "停止执行" \
                else "skip"
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

    def _grab_xy(self) -> None:
        """坐标拾取：倒计时 3 秒，取倒计时结束时鼠标所在位置。"""
        self._show_hint("3 秒后拾取当前鼠标位置，请把鼠标移到目标点…")
        self.after(3000, self._do_grab_xy)

    def _do_grab_xy(self) -> None:
        try:
            import pyautogui

            pos = pyautogui.position()
            self.var_content.set(f"{pos.x},{pos.y}")
            self._show_hint(f"已拾取坐标 ({pos.x}, {pos.y})")
        except Exception as exc:  # noqa: BLE001
            self._show_hint(f"拾取失败：{exc}", warn=True)


class ScheduleDialog(BaseDialog):
    """定时运行 + 结束通知。

    定时到点后自动运行设定的执行组（界面开着才有效；要无人值守
    关界面跑，请用命令行 start-app.py --run 组名 配合任务计划）。
    """

    def __init__(self, parent: App, settings: dict, group_names: list[str]):
        super().__init__(parent, "定时运行与通知", width=450, height=330)
        self.columnconfigure(1, weight=1)
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
        self.label("填企业微信 / 钉钉机器人地址，运行结束自动推送；"
                   "留空则不推送", 6, muted=True)

        buttons = tk.Frame(self, bg=CARD)
        buttons.grid(row=7, column=0, columnspan=2, pady=parent.s(14))
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
                if step.type in IMAGE_TYPES:
                    names.append(step.content.strip())
                elif step.type == "if_image":
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
                if step.type in IMAGE_TYPES:
                    if os.path.basename(step.content.strip()) == name:
                        step.content = new_name
                        changed += 1
                elif step.type == "if_image":
                    image, sep, rest = step.content.partition("|")
                    if os.path.basename(image.strip()) == name:
                        step.content = f"{new_name}|{rest}"
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
        super().__init__(parent, "运行设置", width=430, height=240)
        self.columnconfigure(1, weight=1)

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

        self.label("相似度越低越容易识别，也越容易误点。\n"
                   "运行时会自动最小化窗口，避免遮挡识别区。", 3, muted=True)

        buttons = tk.Frame(self, bg=CARD)
        buttons.grid(row=4, column=0, columnspan=2, pady=parent.s(14))
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
        self.result = {"delay": int(number(self.var_delay.get(), 3)),
                       "confidence": number(self.var_conf.get(), 0.9),
                       "image_dir": directory}
        self.destroy()


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    enable_dpi_awareness()
    main()
