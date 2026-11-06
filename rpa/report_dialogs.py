# -*- coding: utf-8 -*-
"""运行报告 / 恢复备份 / 最近项目对话框。

从 gui.py 拆出，行为不变；gui 模块继续 re-export 这些名称。
"""
from __future__ import annotations

import os
import re
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from . import i18n, platform_support, project_io, uikit
from .dialogs import BaseDialog
from .engine import list_run_reports, parse_report_table, write_report_index
from .theme import (
    CONTROL_MD,
    FONT_CAPTION,
    FONT_CAPTION_BOLD,
    FONT_MONO,
    FONT_NOTE,
    FONT_SECTION,
    LIGHT_PALETTE,
)

if TYPE_CHECKING:  # 仅供类型标注，避免与 gui 产生循环导入
    from .gui import App

# 用到的浅色令牌点名为模块常量，主题切换时由 apply_palette 整组替换。
ACCENT = LIGHT_PALETTE["ACCENT"]
BORDER = LIGHT_PALETTE["BORDER"]
CARD = LIGHT_PALETTE["CARD"]
DANGER = LIGHT_PALETTE["DANGER"]
DANGER_TEXT = LIGHT_PALETTE["DANGER_TEXT"]
ENTRY_BG = LIGHT_PALETTE["ENTRY_BG"]
FIELD_FOCUS = LIGHT_PALETTE["FIELD_FOCUS"]
HEADING_BG = LIGHT_PALETTE["HEADING_BG"]
LOG_BG = LIGHT_PALETTE["LOG_BG"]
LOG_FG = LIGHT_PALETTE["LOG_FG"]
MARK_DONE = LIGHT_PALETTE["MARK_DONE"]
MARK_FAILED = LIGHT_PALETTE["MARK_FAILED"]
MUTED = LIGHT_PALETTE["MUTED"]
SELECT_BG = LIGHT_PALETTE["SELECT_BG"]
SUCCESS = LIGHT_PALETTE["SUCCESS"]
SUCCESS_TEXT = LIGHT_PALETTE["SUCCESS_TEXT"]
TEXT = LIGHT_PALETTE["TEXT"]
ZEBRA = LIGHT_PALETTE["ZEBRA"]

# 运行报告目录由调用方传入；gui.REPORTS_DIR 在测试里会被替换，
# 因此这里只保留一个兜底值，不在模块里复制 gui 的路径。
REPORTS_DIR = ""

REPORT_RESULT_TEXT = {
    "finished": "成功",
    "error": "失败",
    "stopped": "已停止",
}

REPORT_RESULT_TAG = {
    "finished": "ok",
    "error": "danger",
    "stopped": "muted",
}


def apply_palette(palette: dict) -> None:
    """主题切换时同步本模块的模块级颜色常量。"""
    global ACCENT, BORDER, CARD, DANGER, DANGER_TEXT, ENTRY_BG, FIELD_FOCUS
    global HEADING_BG, LOG_BG, LOG_FG, MARK_DONE, MARK_FAILED, MUTED
    global SELECT_BG, SUCCESS, SUCCESS_TEXT, TEXT, ZEBRA
    ACCENT = palette["ACCENT"]
    BORDER = palette["BORDER"]
    CARD = palette["CARD"]
    DANGER = palette["DANGER"]
    DANGER_TEXT = palette["DANGER_TEXT"]
    ENTRY_BG = palette["ENTRY_BG"]
    FIELD_FOCUS = palette["FIELD_FOCUS"]
    HEADING_BG = palette["HEADING_BG"]
    LOG_BG = palette["LOG_BG"]
    LOG_FG = palette["LOG_FG"]
    MARK_DONE = palette["MARK_DONE"]
    MARK_FAILED = palette["MARK_FAILED"]
    MUTED = palette["MUTED"]
    SELECT_BG = palette["SELECT_BG"]
    SUCCESS = palette["SUCCESS"]
    SUCCESS_TEXT = palette["SUCCESS_TEXT"]
    TEXT = palette["TEXT"]
    ZEBRA = palette["ZEBRA"]


def set_reports_dir(path: str) -> None:
    """同步运行报告目录（gui 构造对话框时也会直接传参）。"""
    global REPORTS_DIR
    REPORTS_DIR = path


def _reports_dir(parent: App, reports_dir: str | None) -> str:
    """优先使用显式传入的目录，其次读 parent 上的属性，最后用兜底值。"""
    if reports_dir:
        return reports_dir
    return str(getattr(parent, "reports_dir", "") or REPORTS_DIR)


class RunReportDialog(BaseDialog):
    """运行报告详情：弹窗直接浏览 markdown 报告。"""

    def __init__(self, parent: App, report: dict,
                 reports_dir: str | None = None):
        super().__init__(parent, "运行报告详情", width=900, height=560)
        self.report = report
        self._reports_dir = _reports_dir(parent, reports_dir)
        self._links: dict[tuple[int, int], str] = {}
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        head = tk.Frame(self, bg=CARD)
        head.grid(row=0, column=0, columnspan=2, sticky="ew",
                  padx=parent.s(16), pady=(parent.s(14), parent.s(2)))
        tk.Label(head, text="运行报告", bg=CARD, fg=TEXT,
                 font=FONT_SECTION).pack(side="left")
        self._result_pill(head, report.get("result", ""))

        stamp = time.strftime("%Y-%m-%d %H:%M:%S",
                              time.localtime(report.get("mtime") or time.time()))
        parts = [stamp]
        if report.get("rows"):
            parts.append(f"{report['rows']} {i18n.tr('行数据')}")
        parts.append(f"{report.get('steps', 0)} {i18n.tr('步')}")
        parts.append(f"{i18n.tr('失败')} {report.get('failed', 0)}")
        parts.append(
            f"{i18n.tr('耗时')} {float(report.get('seconds') or 0):g} "
            f"{i18n.tr('秒')}")
        tk.Label(self, text=" · ".join(parts), bg=CARD, fg=MUTED,
                 font=FONT_CAPTION, anchor="w").grid(
            row=1, column=0, columnspan=2, sticky="ew",
            padx=parent.s(16), pady=(0, parent.s(8)))

        text = report.get("text", "")
        headers, rows = parse_report_table(text)
        if headers:
            self._build_table(headers, rows)
        else:
            self._build_raw(text)

        footer = tk.Frame(self, bg=CARD)
        footer.grid(row=3, column=0, columnspan=2, sticky="ew",
                    padx=parent.s(16), pady=(parent.s(8), parent.s(14)))
        if self._links:
            tk.Label(footer, text="双击「查看」打开截图", bg=CARD, fg=MUTED,
                     font=FONT_CAPTION).pack(side="left")
        uikit.RoundButton(footer, "关闭", "close", self.cancel,
                          kind="accent", scale=parent.scale, bg=CARD,
                          height=CONTROL_MD).pack(side="right")
        uikit.RoundButton(footer, "打开所在文件夹", "folder_open",
                          self._open_folder, kind="ghost",
                          scale=parent.scale, bg=CARD,
                          height=CONTROL_MD).pack(
            side="right", padx=(0, parent.s(8)))
        self.btn_rerun = uikit.RoundButton(
            footer, "重跑该组", "run", self._rerun, kind="outline",
            scale=parent.scale, bg=CARD, height=CONTROL_MD)
        self.btn_rerun.pack(side="left")
        if not self._report_group():
            self.btn_rerun.set_enabled(False)
        self.center()

    def _report_group(self) -> str:
        """从报告表格里取第一条记录的「执行组」名。"""
        headers, rows = parse_report_table(self.report.get("text", ""))
        if "执行组" not in headers:
            return ""
        index = headers.index("执行组")
        for row in rows:
            if index < len(row) and row[index].strip():
                return row[index].strip()
        return ""

    def _rerun(self) -> None:
        """关掉报告，重新运行报告里记录的组。"""
        group = self._report_group()
        if not group:
            return
        if group not in self.app.project.names():
            messagebox.showwarning(
                "执行组不存在",
                f"执行组「{group}」已不在当前项目中，无法重跑。",
                parent=self)
            return
        self.destroy()
        self.app.run_group_for(group)

    def _result_pill(self, parent, result: str) -> None:
        label = REPORT_RESULT_TEXT.get(result, result or "未知")
        tag = REPORT_RESULT_TAG.get(result, "muted")
        colors = {
            "ok": (MARK_DONE, SUCCESS_TEXT),
            "danger": (MARK_FAILED, DANGER_TEXT),
            "muted": (HEADING_BG, MUTED),
        }
        bg, fg = colors.get(tag, colors["muted"])
        tk.Label(parent, text=i18n.tr(label), bg=bg, fg=fg,
                 font=FONT_CAPTION_BOLD, padx=self.app.s(10),
                 pady=self.app.s(2)).pack(
            side="left", padx=(self.app.s(10), 0))

    def _build_table(self, headers: list[str],
                     rows: list[list[str]]) -> None:
        columns = [f"c{index}" for index in range(len(headers))]
        self.tree = ttk.Treeview(self, columns=columns, show="headings",
                                 selectmode="browse")
        for index, title in enumerate(headers):
            self.tree.heading(columns[index], text=title)
            self.tree.column(
                columns[index], width=self.app.s(self._col_width(title)),
                anchor="w", stretch=(title == "内容"))
        self.tree.grid(row=2, column=0, sticky="nsew",
                       padx=(self.app.s(16), 0))
        scroll = uikit.SlimScrollbar(
            self, orient="vertical", command=self.tree.yview,
            scale=self.app.scale, bg=CARD)
        scroll.grid(row=2, column=1, sticky="ns",
                    padx=(self.app.s(2), self.app.s(16)))
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.tag_configure("zebra", background=ZEBRA)
        self.tree.tag_configure("failed", background=MARK_FAILED)

        for row_index, cells in enumerate(rows):
            values = []
            for col_index in range(len(columns)):
                cell = cells[col_index] if col_index < len(cells) else ""
                label, target = self._split_link(cell)
                values.append(label)
                if target:
                    self._links[(row_index, col_index)] = target
            tags: tuple[str, ...]
            if "失败" in cells:
                tags = ("failed",)
            elif row_index % 2:
                tags = ("zebra",)
            else:
                tags = ()
            self.tree.insert("", "end", iid=str(row_index),
                             values=values, tags=tags)
        self.tree.bind("<Double-1>", self._open_link)

    def _build_raw(self, text: str) -> None:
        """没有表格的异常报告：直接按原文显示。"""
        self.raw = tk.Text(self, bg=LOG_BG, fg=LOG_FG, bd=0,
                           highlightthickness=0, wrap="word",
                           font=FONT_MONO)
        self.raw.insert("1.0", text)
        self.raw.configure(state="disabled")
        self.raw.grid(row=2, column=0, sticky="nsew",
                      padx=(self.app.s(16), 0))
        scroll = uikit.SlimScrollbar(
            self, orient="vertical", command=self.raw.yview,
            scale=self.app.scale, bg=CARD)
        scroll.grid(row=2, column=1, sticky="ns",
                    padx=(self.app.s(2), self.app.s(16)))
        self.raw.configure(yscrollcommand=scroll.set)

    def _col_width(self, title: str) -> int:
        return {"执行组": 110, "行号": 56, "步骤": 56, "类型": 90,
                "内容": 300, "耗时(秒)": 80, "结果": 64,
                "重试": 56, "截图": 64}.get(title, 100)

    @staticmethod
    def _split_link(cell: str) -> tuple[str, str]:
        match = re.fullmatch(r"\[([^\]]*)\]\(([^)]+)\)", cell.strip())
        if match:
            return i18n.tr(match.group(1) or "查看"), match.group(2)
        return cell, ""

    def _open_link(self, event) -> None:
        tree = getattr(self, "tree", None)
        if tree is None:
            return
        row = tree.identify_row(event.y)
        column = tree.identify_column(event.x)
        if not row or not column or not column.startswith("#"):
            return
        index = int(column[1:]) - 1
        target = self._links.get((int(row), index))
        if not target:
            return
        path = target
        if not os.path.isabs(path):
            path = os.path.join(
                os.path.dirname(self.report.get("path", "")), path)
        if not os.path.exists(path):
            messagebox.showwarning("文件不存在", path, parent=self)
            return
        try:
            platform_support.open_path(path)
        except (OSError, AttributeError) as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)

    def _open_folder(self) -> None:
        path = self.report.get("path") or ""
        folder = os.path.dirname(path) or self._reports_dir
        try:
            platform_support.open_path(folder)
        except (OSError, AttributeError) as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)


class RunHistoryDialog(BaseDialog):
    """运行历史：列出 reports/run_*.md，可查看详情、清空历史。"""

    def __init__(self, parent: App, reports_dir: str | None = None):
        super().__init__(parent, "运行历史", width=780, height=480)
        self._reports_dir = _reports_dir(parent, reports_dir)
        self._reports: list[dict] = []
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        head = tk.Frame(self, bg=CARD)
        head.grid(row=0, column=0, columnspan=2, sticky="ew",
                  padx=parent.s(16), pady=(parent.s(14), parent.s(6)))
        tk.Label(head, text="运行历史", bg=CARD, fg=TEXT,
                 font=FONT_SECTION).pack(side="left")
        self.count_label = tk.Label(head, text="", bg=CARD, fg=MUTED,
                                    font=FONT_CAPTION)
        self.count_label.pack(side="right")

        columns = ("at", "result", "steps", "failed", "seconds", "file")
        self.tree = ttk.Treeview(self, columns=columns, show="headings",
                                 selectmode="browse")
        for key, text, width in (
                ("at", "时间", 150), ("result", "结果", 80),
                ("steps", "步骤", 70), ("failed", "失败", 70),
                ("seconds", "耗时(秒)", 90), ("file", "报告", 250)):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=parent.s(width), anchor="w")
        self.tree.grid(row=1, column=0, sticky="nsew",
                       padx=(parent.s(16), 0))
        scroll = uikit.SlimScrollbar(
            self, orient="vertical", command=self.tree.yview,
            scale=parent.scale, bg=CARD)
        scroll.grid(row=1, column=1, sticky="ns",
                    padx=(parent.s(2), parent.s(16)))
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.tag_configure("ok", foreground=SUCCESS)
        self.tree.tag_configure("danger", foreground=DANGER)
        self.tree.tag_configure("muted", foreground=MUTED)
        self.tree.bind("<Double-1>", lambda e: self._open_selected())
        self.tree.bind("<Return>", lambda e: self._open_selected())

        self.summary = tk.Label(self, text="", bg=CARD, fg=MUTED,
                                font=FONT_CAPTION, anchor="w")
        self.summary.grid(row=2, column=0, columnspan=2, sticky="ew",
                          padx=parent.s(16), pady=(parent.s(4), 0))

        footer = tk.Frame(self, bg=CARD)
        footer.grid(row=3, column=0, columnspan=2, sticky="ew",
                    padx=parent.s(16), pady=(parent.s(10), parent.s(12)))
        self.btn_detail = uikit.RoundButton(
            footer, "查看详情", "note", self._open_selected, kind="accent",
            scale=parent.scale, bg=CARD, height=CONTROL_MD)
        self.btn_detail.pack(side="right")
        uikit.RoundButton(footer, "关闭", "close", self.cancel, kind="ghost",
                          scale=parent.scale, bg=CARD,
                          height=CONTROL_MD).pack(
            side="right", padx=(0, parent.s(8)))
        self.btn_clear = uikit.RoundButton(
            footer, "清空历史", "trash", self._clear, kind="outline",
            scale=parent.scale, bg=CARD, height=CONTROL_MD)
        self.btn_clear.pack(side="left")

        self._reload()
        self.center()

    def _reload(self) -> None:
        self._reports = list_run_reports(self._reports_dir)
        self.tree.delete(*self.tree.get_children())
        for index, report in enumerate(self._reports):
            result = report.get("result", "")
            label = REPORT_RESULT_TEXT.get(result, result or "未知")
            tag = REPORT_RESULT_TAG.get(result, "muted")
            stamp = time.strftime("%Y-%m-%d %H:%M",
                                  time.localtime(report["mtime"]))
            self.tree.insert(
                "", "end", iid=str(index), tags=(tag,),
                values=(stamp, i18n.tr(label), report["steps"],
                        report["failed"], f"{report['seconds']:g}",
                        report["name"]))
        self.count_label.configure(
            text=f"{len(self._reports)}{i18n.tr('份报告')}")
        self.summary.configure(
            text=("双击一行查看报告详情" if self._reports
                  else "还没有运行报告，跑一次后会自动生成"))
        enabled = bool(self._reports)
        self.btn_detail.set_enabled(enabled)
        self.btn_clear.set_enabled(enabled)

    def _open_selected(self) -> None:
        selection = self.tree.selection()
        if not selection:
            self.app._set_status("请先选择一份运行报告")
            return
        try:
            index = int(selection[0])
        except ValueError:
            return
        if not 0 <= index < len(self._reports):
            return
        self.grab_release()
        try:
            RunReportDialog(self.app, self._reports[index],
                            self._reports_dir).wait()
        finally:
            try:
                self.grab_set()
            except tk.TclError:
                pass

    def _clear(self) -> None:
        if not self._reports:
            return
        if not messagebox.askyesno(
                i18n.tr("清空运行历史"),
                i18n.tr("确定删除全部 ") + str(len(self._reports))
                + i18n.tr(" 份运行报告？\n此操作不可撤销。"),
                parent=self):
            return
        removed = 0
        for report in self._reports:
            try:
                os.remove(report["path"])
                removed += 1
            except OSError:
                pass
        try:
            write_report_index(self._reports_dir)
        except Exception:  # noqa: BLE001 — 索引刷新失败不影响清理
            pass
        self._reload()
        self.app._set_status(
            i18n.tr("已清空 ") + str(removed) + i18n.tr(" 份运行报告"))


class RecentProjectsDialog(BaseDialog):
    """最近项目：双击一行直接打开。"""

    def __init__(self, parent: App, paths: list[str]):
        super().__init__(parent, "最近项目", width=560, height=360)
        self.paths = list(paths)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        tk.Label(self, text="双击一行打开项目", bg=CARD, fg=MUTED,
                 font=FONT_CAPTION, anchor="w").grid(
            row=0, column=0, columnspan=2, sticky="ew",
            padx=parent.s(16), pady=(parent.s(14), parent.s(4)))
        self.listbox = tk.Listbox(
            self, font=FONT_NOTE, bg=ENTRY_BG, fg=TEXT,
            selectbackground=SELECT_BG, selectforeground=TEXT,
            relief="flat", highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=FIELD_FOCUS,
            activestyle="none", exportselection=False)
        self.listbox.grid(row=1, column=0, sticky="nsew",
                          padx=(parent.s(16), 0))
        scroll = uikit.SlimScrollbar(
            self, orient="vertical", command=self.listbox.yview,
            scale=parent.scale, bg=CARD)
        scroll.grid(row=1, column=1, sticky="ns",
                    padx=(parent.s(2), parent.s(16)))
        self.listbox.configure(yscrollcommand=scroll.set)
        for path in self.paths:
            self.listbox.insert(
                "end", f"{project_io.project_name(path)}  ·  {path}")
        self.footer(2, "打开", "open", self._open)
        self.listbox.bind("<Double-1>", lambda e: self._open())
        self.listbox.bind("<Return>", lambda e: self._open())
        if self.paths:
            self.listbox.selection_set(0)
        self.listbox.focus_set()
        self.center()

    def _open(self) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        index = selection[0]
        if 0 <= index < len(self.paths):
            self.result = self.paths[index]
        self.destroy()


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
