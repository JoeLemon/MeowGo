# -*- coding: utf-8 -*-
"""通用对话框：模态基类、执行组命名、快捷键速查。

从 gui.py 拆出，行为不变；gui 模块继续 re-export 这些名称。
"""
from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING, Any

from . import uikit
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


