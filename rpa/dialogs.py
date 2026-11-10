# -*- coding: utf-8 -*-
"""通用对话框：模态基类、执行组命名、快捷键速查。

从 gui.py 拆出，行为不变；gui 模块继续 re-export 这些名称。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Any

from . import platform_support, uikit
from .theme import CONTROL_MD, FONT_CAPTION, FONT_MONO, FONT_NOTE, FONT_SIZE_BODY, LIGHT_PALETTE

if TYPE_CHECKING:  # 仅供类型标注，避免与 gui 产生循环导入
    from .gui import App

# 用到的浅色令牌点名为模块常量，主题切换时由 apply_palette 整组替换。
ACCENT = LIGHT_PALETTE["ACCENT"]
CARD = LIGHT_PALETTE["CARD"]
MUTED = LIGHT_PALETTE["MUTED"]
TEXT = LIGHT_PALETTE["TEXT"]


def apply_palette(palette: dict) -> None:
    """主题切换时同步本模块的模块级颜色常量。"""
    global ACCENT, CARD, MUTED, TEXT
    ACCENT = palette["ACCENT"]
    CARD = palette["CARD"]
    MUTED = palette["MUTED"]
    TEXT = palette["TEXT"]


class BaseDialog(tk.Toplevel):
    """统一风格的模态对话框。"""

    # 子类 OK 时写入（字符串 / 布尔 / 列表都可能），统一按 Any 处理
    result: Any = None

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

    def center(self, on_screen: bool = False) -> None:
        """把对话框放到主窗口中间。

        on_screen=True 时改为放在鼠标所在屏幕的中上位置：主窗最小化 /
        收进托盘时它的坐标不可靠（可能变成 -32000），不能当锚点。
        """
        self.update_idletasks()
        if on_screen:
            rect = uikit.monitor_work_area(self.winfo_pointerx(),
                                           self.winfo_pointery())
            if rect is None:
                x = (self.winfo_screenwidth() - self.winfo_width()) // 2
                y = (self.winfo_screenheight() - self.winfo_height()) // 3
            else:
                left, top, right, bottom = rect
                x = left + (right - left - self.winfo_width()) // 2
                y = top + (bottom - top - self.winfo_height()) // 3
            self.geometry(f"+{int(x)}+{int(y)}")
        else:
            x = self.app.winfo_rootx() + (self.app.winfo_width() - self.winfo_width()) // 2
            y = self.app.winfo_rooty() + (self.app.winfo_height() - self.winfo_height()) // 3
            self.app.place_window(self, x, y, self.winfo_width(), self.winfo_height())
        uikit.fade_in(self)
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

    def ok(self) -> None:
        """子类覆盖；默认直接关闭（无返回值的提示类对话框用）。"""
        self.destroy()

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
    """输入 / 修改执行组名称；with_retry=True 时同时设置整组重试。"""

    def __init__(self, parent: App, title: str, initial: str = "",
                 retry: int = 0, retry_delay: float = 0.5,
                 with_retry: bool = False, show_name: bool = True):
        super().__init__(parent, title, width=380,
                         height=250 if with_retry else 170)
        self.var = tk.StringVar(value=initial)
        self.retry = max(0, int(retry or 0))
        self.retry_delay = max(0.0, float(retry_delay or 0.5))
        entry = None
        if show_name:
            self.label("执行组名称", 0)
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
        row = 2 if show_name else 0
        if with_retry:
            self.label("整组重试", row)
            box = tk.Frame(self, bg=CARD)
            box.grid(row=row, column=1, sticky="w", padx=(0, parent.s(16)),
                     pady=parent.s(5))
            self.var_retry = tk.StringVar(value=str(self.retry))
            ttk.Spinbox(box, from_=0, to=9, width=4,
                        textvariable=self.var_retry).pack(side="left")
            tk.Label(box, text="次，间隔", bg=CARD, fg=TEXT,
                     font=FONT_NOTE).pack(side="left", padx=(parent.s(3), 0))
            self.var_delay = tk.StringVar(value=f"{self.retry_delay:g}")
            ttk.Spinbox(box, from_=0, to=60, increment=0.5, width=5,
                        textvariable=self.var_delay).pack(
                side="left", padx=(parent.s(3), 0))
            tk.Label(box, text="秒", bg=CARD, fg=TEXT,
                     font=FONT_NOTE).pack(side="left", padx=(parent.s(2), 0))
            tk.Label(self,
                     text="整组跑完仍有失败步骤时才重跑整组；"
                          "设置了「停止执行」的错误不会重试。",
                     bg=CARD, fg=MUTED, font=FONT_CAPTION, justify="left",
                     anchor="w", wraplength=parent.s(340)).grid(
                row=row + 1, column=0, columnspan=2, sticky="ew",
                padx=(parent.s(16), parent.s(16)),
                pady=(0, parent.s(4)))
            row += 2
        self.footer(row, pad_top=6)
        if entry is not None:
            entry.bind("<Return>", lambda e: self.ok())
            entry.focus_set()
            entry.select_range(0, "end")
        self.fit_content(width=parent.s(380))
        self.center()

    def ok(self) -> None:
        if hasattr(self, "var_retry"):
            try:
                self.retry = max(0, int(self.var_retry.get() or 0))
            except ValueError:
                self.retry = 0
            try:
                self.retry_delay = max(0.0, float(self.var_delay.get() or 0.5))
            except ValueError:
                self.retry_delay = 0.5
        self.result = self.var.get().strip()
        self.destroy()



class ShortcutsDialog(BaseDialog):
    """快捷键速查卡（右下角「?」按钮）。"""

    ROWS = [
        ("Ctrl+G", "新建执行组"),
        ("Ctrl+Enter", "新增步骤"),
        ("Ctrl+A", "全选当前组的步骤（可批量删除/复制/移动）"),
        ("Ctrl+E", "启用 / 禁用选中的步骤"),
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
        rows = list(self.ROWS)
        for row, (key, text) in enumerate(rows):
            tk.Label(self, text=platform_support.hotkey_label(key),
                     bg=CARD, fg=ACCENT, font=FONT_MONO).grid(
                row=row, column=0, sticky="w", padx=(parent.s(18), parent.s(10)),
                pady=parent.s(4))
            tk.Label(self, text=text, bg=CARD, fg=TEXT,
                     font=FONT_NOTE).grid(
                row=row, column=1, sticky="w")
        note_row = len(rows)
        if platform_support.IS_MACOS:
            tk.Label(self, text="macOS 首次使用全局 F5 / F9 需在「系统设置 → "
                                "隐私与安全性 → 辅助功能」中授权；未授权时"
                                "只有窗口内快捷键生效。",
                     bg=CARD, fg=MUTED, font=FONT_CAPTION,
                     wraplength=parent.s(390), justify="left").grid(
                row=note_row, column=0, columnspan=2, sticky="w",
                padx=parent.s(18), pady=(parent.s(10), 0))
            note_row += 1
        tk.Label(self, text="运行安全：将鼠标快速移至屏幕左上角，"
                            "可立即中断执行。", bg=CARD, fg=MUTED,
                 font=FONT_CAPTION,
                 wraplength=parent.s(390), justify="left").grid(
            row=note_row, column=0, columnspan=2, sticky="w",
            padx=parent.s(18), pady=(parent.s(10), 0))
        self.footer(note_row + 1, "知道了", "check", self.ok,
                    cancel_text="", pad_top=10)
        self.fit_content(width=parent.s(440))
        self.center()

    def ok(self) -> None:
        self.result = True
        self.destroy()


