# -*- coding: utf-8 -*-
"""RPA 核心引擎：执行组、步骤模型、Excel 读写、校验与执行。

与界面完全解耦，方便单独测试或命令行调用。
"""
from __future__ import annotations

import os
import random
import re
import sys
import time
import threading
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from . import assets

try:  # 窗口操作只在 Windows 上有意义；其它平台导入 engine 不应失败
    import ctypes
    import ctypes.wintypes as wt
except ImportError:  # pragma: no cover - 非 Windows
    ctypes = None
    wt = None

if ctypes is not None:  # pragma: no cover - 非 Windows
    _WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    _SW_SHOW = 5
    _SW_RESTORE = 1
    _VK_MENU = 0x12
    _KEYEVENTF_KEYUP = 0x0002
    _WM_CLOSE = 0x0010

# ---------------------------------------------------------------------------
# 步骤类型定义
# ---------------------------------------------------------------------------

# key: 内部标识；value: (中文名, 内容提示, 是否用重复次数, 重复次数含义)
STEP_TYPES = {
    "click":   ("左键单击", "图片文件名，如 按钮.png", True,  "点击次数"),
    "dblclick": ("左键双击", "图片文件名，如 按钮.png", True,  "双击次数"),
    "rightclick": ("右键单击", "图片文件名，如 菜单.png", True, "点击次数"),
    "input":   ("输入文本", "要粘贴的文字（会占用剪贴板）", False, ""),
    "wait":    ("等待",     "等待秒数，如 1.5", False, ""),
    "scroll":  ("滚轮",     "滚动距离，正数向上 / 负数向下，如 -200", False, ""),
    "hotkey":  ("热键组合", "用英文逗号分隔，如 ctrl,s 或 win,r", True, "重复次数（-1 无限）"),
    "time":    ("粘贴当前时间", "固定填 当前时间 即可", False, ""),
    "cmd":     ("系统命令", "CMD 命令，如 start notepad", False, ""),
    "call":    ("调用执行组", "要调用的组名（实现步骤复用）", True, "调用次数（-1 无限）"),
    # ---- 新版扩展类型 ----
    "if_image": ("条件：出现图片则执行", "图片名|组名，如 弹窗.png|关闭弹窗", True, "执行次数"),
    "window":  ("窗口操作", "activate:标题 / wait:标题|秒 / close:标题", False, ""),
    "click_xy": ("坐标点击", "x,y，如 500,300；可加 ,right 表示右键", True, "点击次数"),
    "move_xy": ("移动鼠标", "x,y，如 500,300", False, ""),
}

# Excel 中第 1 列使用的数字编号（1-9 与旧版 cmd.xls 完全兼容）
TYPE_TO_CODE = {
    "click": 1.0,
    "dblclick": 2.0,
    "rightclick": 3.0,
    "input": 4.0,
    "wait": 5.0,
    "scroll": 6.0,
    "hotkey": 7.0,
    "time": 8.0,
    "cmd": 9.0,
    "call": 10.0,
    "if_image": 11.0,
    "window": 12.0,
    "click_xy": 13.0,
    "move_xy": 14.0,
}
CODE_TO_TYPE = {v: k for k, v in TYPE_TO_CODE.items()}

IMAGE_TYPES = {"click", "dblclick", "rightclick"}
REPEAT_TYPES = {"click", "dblclick", "rightclick", "hotkey", "call",
                "if_image", "click_xy"}

# 图片步骤超时后的处理方式（timeout > 0 时生效）
FAIL_ACTIONS = ("skip", "stop")

DEFAULT_CONFIDENCE = 0.9
POLL_INTERVAL = 0.1
MAX_CALL_DEPTH = 20
HEADERS = ["指令类型", "内容", "重复次数(-1无限)", "备注"]
GENERIC_SHEET_NAMES = {"sheet1", "sheet", "工作表1", "sheet2"}


class StepError(ValueError):
    """步骤配置错误。"""


def _valid_xy(text: str) -> bool:
    """"500,300" 或 "500,300,right" 是否合法。"""
    parts = [p.strip() for p in (text or "").split(",")]
    if len(parts) not in (2, 3):
        return False
    try:
        x, y = float(parts[0]), float(parts[1])
    except ValueError:
        return False
    if len(parts) == 3 and parts[2] not in ("left", "right", "middle"):
        return False
    return x >= 0 and y >= 0


def _parse_xy(text: str) -> tuple[float, float, str]:
    """"500,300" -> (500.0, 300.0, "left")；可带第三段指定按键。"""
    parts = [p.strip() for p in text.split(",")]
    x, y = float(parts[0]), float(parts[1])
    button = parts[2] if len(parts) > 2 and parts[2] in (
        "left", "right", "middle") else "left"
    return x, y, button


@dataclass
class Step:
    """一条指令。

    timeout / on_fail 只对图片类步骤有意义：
      timeout = 0   一直等图片出现（旧版行为，默认）
      timeout > 0   最多等这么久；到时按 on_fail 处理
      on_fail = skip  跳过这步继续往下（默认）
      on_fail = stop  中止整个执行
    Excel 只有 4 列，没有这两项的列——导入的旧文件一律按默认值
    （一直等）处理，与旧版完全一致。
    """

    type: str = "click"
    content: str = ""
    repeat: float = 0
    note: str = ""
    timeout: float = 0
    on_fail: str = "skip"

    def label(self) -> str:
        return STEP_TYPES.get(self.type, (self.type,))[0]

    def validate(self, row: int = 0) -> list[str]:
        """返回该步骤的问题列表，空列表代表合法。"""
        errors: list[str] = []
        where = f"第 {row} 行" if row else "该步骤"
        if self.type not in STEP_TYPES:
            errors.append(f"{where}：未知的指令类型 {self.type!r}")
            return errors

        text = (self.content or "").strip()
        if self.type in IMAGE_TYPES:
            if not text:
                errors.append(f"{where}：需要填写图片文件名")
        elif self.type in ("input", "hotkey", "time", "cmd", "call"):
            if not text:
                errors.append(f"{where}：内容不能为空")
        elif self.type == "wait":
            try:
                if float(text) < 0:
                    errors.append(f"{where}：等待时间不能为负数")
            except ValueError:
                errors.append(f"{where}：等待时间必须是数字")
        elif self.type == "scroll":
            try:
                int(float(text))
            except ValueError:
                errors.append(f"{where}：滚动距离必须是数字")

        if self.type == "hotkey" and text:
            keys = [k.strip() for k in text.split(",")]
            if any(not k for k in keys):
                errors.append(f"{where}：热键里存在空项，请检查逗号")
            if len(keys) > 10:
                errors.append(f"{where}：热键最多 10 个按键")
            if any(k.isdigit() for k in keys):
                errors.append(f"{where}：单个数字键无法识别，请改用 num0 这类写法")

        # 图片类与拖拽（两端可能是图片）都支持等待超时
        if (self.type in IMAGE_TYPES or self.type == "drag") and self.timeout:
            try:
                if float(self.timeout) < 0:
                    errors.append(f"{where}：等待超时不能为负数")
            except (TypeError, ValueError):
                errors.append(f"{where}：等待超时必须是数字")
            if self.on_fail and self.on_fail not in FAIL_ACTIONS:
                errors.append(f"{where}：超时后处理只能是 skip 或 stop")

        if self.type == "if_image" and text:
            if "|" not in text:
                errors.append(f"{where}：条件步骤要写成 图片名|组名")
        elif self.type == "if_not_image" and text:
            if "|" not in text:
                errors.append(f"{where}：条件步骤要写成 图片名|组名")
        elif self.type == "if_else" and text:
            parts = [p.strip() for p in text.split("|")]
            if len(parts) != 3:
                errors.append(f"{where}：二选一要写成 图片名|出现执行|没出现执行")
            elif not parts[0] or not parts[1]:
                errors.append(f"{where}：图片名和「出现执行」的组名不能为空")
        elif self.type == "if_window" and text:
            if "|" not in text:
                errors.append(f"{where}：窗口条件要写成 窗口标题|组名")
        elif self.type == "set" and text:
            name, sep, _value = text.partition("=")
            if not sep or not name.strip():
                errors.append(f"{where}：设置变量要写成 变量名=值")
            elif "{" in name or "}" in name:
                errors.append(f"{where}：变量名里不能带 {{ 或 }}")
        elif self.type == "drag" and text:
            parts = [p.strip() for p in text.split("|")]
            if len(parts) != 2 or not all(parts):
                errors.append(f"{where}：拖拽要写成 起点|终点（图或 x,y）")
            else:
                for part in parts:
                    if not _valid_xy(part) and not image_names(part):
                        errors.append(f"{where}：拖拽的「{part}」既不是坐标"
                                      "（x,y）也不是图片文件名")
        elif self.type == "window" and text:
            action = text.split(":", 1)[0].strip().lower()
            if action not in ("activate", "wait", "close"):
                errors.append(f"{where}：窗口操作只支持 activate / wait / close")
            if action == "wait" and "|" not in text:
                errors.append(f"{where}：等待窗口要写成 wait:标题|秒")
        elif self.type in ("click_xy", "move_xy") and text:
            if not _valid_xy(text):
                errors.append(f"{where}：坐标要写成 x,y，如 500,300")

        if self.region and self.type in IMAGE_TYPES:
            if _parse_region(self.region) is None:
                errors.append(f"{where}：识别范围要写成 x,y,宽,高")

        if self.type in REPEAT_TYPES:
            try:
                if float(self.repeat) < -1:
                    errors.append(f"{where}：重复次数不能小于 -1")
            except (TypeError, ValueError):
                errors.append(f"{where}：重复次数必须是数字")

        try:
            if int(self.retry) < 0:
                errors.append(f"{where}：重试次数不能为负数")
        except (TypeError, ValueError):
            errors.append(f"{where}：重试次数必须是整数")
        try:
            if float(self.retry_delay) < 0:
                errors.append(f"{where}：重试间隔不能为负数")
        except (TypeError, ValueError):
            errors.append(f"{where}：重试间隔必须是数字")
        return errors

    def to_row(self) -> list:
        """转换为 Excel 一行（4 列）。"""
        repeat = self.repeat
        if repeat in (0, 0.0, "", None):
            repeat = ""
        elif float(repeat) == int(float(repeat)):
            repeat = float(int(float(repeat)))
        return [TYPE_TO_CODE.get(self.type, 1.0), self.content, repeat, self.note]

    @classmethod
    def from_row(cls, values: Iterable) -> "Step":
        values = list(values) + [None] * 4
        raw_type, content, repeat, note = values[:4]
        try:
            step_type = CODE_TO_TYPE.get(float(raw_type), "click")
        except (TypeError, ValueError):
            step_type = "click"
        try:
            repeat_val = float(repeat) if repeat not in ("", None) else 0.0
        except (TypeError, ValueError):
            repeat_val = 0.0
        return cls(
            type=step_type,
            content="" if content is None else str(content),
            repeat=repeat_val,
            note="" if note is None else str(note),
        )

    def copy(self) -> "Step":
        return Step(self.type, self.content, self.repeat, self.note,
                    self.timeout, self.on_fail, self.region, self.nth,
                    self.grayscale, self.retry, self.retry_delay)

    def to_dict(self) -> dict:
        return {"type": self.type, "content": self.content,
                "repeat": self.repeat, "note": self.note,
                "timeout": self.timeout, "on_fail": self.on_fail,
                "region": self.region, "nth": self.nth,
                "grayscale": self.grayscale, "retry": self.retry,
                "retry_delay": self.retry_delay}

    @classmethod
    def from_dict(cls, data: dict) -> "Step":
        return cls(type=str(data.get("type", "click")),
                   content=str(data.get("content", "")),
                   repeat=float(data.get("repeat", 0) or 0),
                   note=str(data.get("note", "")),
                   timeout=float(data.get("timeout", 0) or 0),
                   on_fail=str(data.get("on_fail", "skip") or "skip"),
                   region=str(data.get("region", "") or ""),
                   nth=int(data.get("nth", 0) or 0),
                   grayscale=bool(data.get("grayscale", False)),
                   retry=int(data.get("retry", 0) or 0),
                   retry_delay=float(data.get("retry_delay", 0.5) or 0.5))


@dataclass
class Group:
    """一个可复用的执行组。"""

    name: str = "新组"
    steps: list[Step] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "note": self.note,
                "steps": [step.to_dict() for step in self.steps]}

    @classmethod
    def from_dict(cls, data: dict) -> "Group":
        return cls(name=str(data.get("name", "新组")),
                   note=str(data.get("note", "")),
                   steps=[Step.from_dict(item) for item in data.get("steps", [])])


@dataclass
class Project:
    """一组执行组 + 全局设置（界面上不再有“项目”，只作状态文件容器）。"""

    groups: list[Group] = field(default_factory=list)
    image_dir: str = "images"

    # -- 便捷访问 ---------------------------------------------------------
    def names(self) -> list[str]:
        return [group.name for group in self.groups]

    def get(self, name: str) -> Optional[Group]:
        for group in self.groups:
            if group.name == name:
                return group
        return None

    def unique_name(self, base: str = "新组") -> str:
        existing = set(self.names())
        if base not in existing:
            return base
        index = 2
        while f"{base}{index}" in existing:
            index += 1
        return f"{base}{index}"

    def as_map(self) -> dict[str, list[Step]]:
        return {group.name: list(group.steps) for group in self.groups}

    # -- JSON 存取 --------------------------------------------------------
    def to_dict(self) -> dict:
        return {"version": 2, "image_dir": self.image_dir,
                "groups": [group.to_dict() for group in self.groups]}

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        groups = [Group.from_dict(item) for item in data.get("groups", [])]
        if not groups and data.get("steps"):
            # 兼容 v1 格式：单一列表
            groups = [Group("默认组", [Step.from_dict(i)
                                       for i in data["steps"]])]
        return cls(groups=groups, image_dir=str(data.get("image_dir", "images")))


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def validate_project(groups: Iterable[Group], base_dir: str = "",
                     check_images: bool = False) -> list[str]:
    """校验所有组：字段合法性、被调用组是否存在、是否存在循环调用。

    check_images=True 时额外检查图片步骤引用的文件是否真的存在
    （配合 base_dir 解析相对路径）。
    """
    groups = list(groups)
    problems: list[str] = []
    seen: set[str] = set()
    for group in groups:
        if not group.name.strip():
            problems.append("存在未命名的执行组")
        elif group.name in seen:
            problems.append(f"执行组名重复：{group.name}")
        seen.add(group.name)

    for group in groups:
        for index, step in enumerate(group.steps, start=1):
            for message in step.validate(row=index):
                problems.append(f"[{group.name}] {message}")
            if check_images and step.type in IMAGE_TYPES:
                for name in image_names(step.content):
                    if name and not os.path.exists(
                            assets.resolve_image(name, base_dir or None)):
                        problems.append(
                            f"[{group.name}] 第 {index} 行：找不到图片文件 {name}")
            if check_images and step.type in ("if_image", "if_not_image",
                                              "if_else"):
                name = (step.content or "").split("|")[0].strip()
                for item in image_names(name):
                    if item and not os.path.exists(
                            assets.resolve_image(item, base_dir or None)):
                        problems.append(
                            f"[{group.name}] 第 {index} 行：找不到图片文件 {item}")
            if check_images and step.type == "drag":
                for item in image_names((step.content or "").split("|")[0]):
                    if item and not os.path.exists(
                            assets.resolve_image(item, base_dir or None)):
                        problems.append(
                            f"[{group.name}] 第 {index} 行：找不到图片文件 {item}")

    index = {group.name: group for group in groups}
    for group in groups:
        for step in group.steps:
            for target in target_groups(step):
                if target not in index:
                    problems.append(f"[{group.name}] 调用的执行组不存在：{target}")

    problems.extend(_find_cycles(index))
    return problems


def _find_cycles(index: dict[str, Group]) -> list[str]:
    """深度优先找出调用环，返回可读的环路径。"""
    problems: list[str] = []
    state: dict[str, int] = {}  # 0 未访问 1 访问中 2 完成
    stack: list[str] = []

    def walk(name: str) -> None:
        group = index.get(name)
        if group is None:
            return
        state[name] = 1
        stack.append(name)
        for step in group.steps:
            for target in target_groups(step):
                if target not in index:
                    continue
                if state.get(target) == 1:
                    cycle = stack[stack.index(target):] + [target]
                    problems.append("存在循环调用：" + " → ".join(cycle))
                elif state.get(target, 0) == 0:
                    walk(target)
        stack.pop()
        state[name] = 2

    for name in index:
        if state.get(name, 0) == 0:
            walk(name)
    # 去重
    return sorted(set(problems))


# ---------------------------------------------------------------------------
# Excel 读写：一个工作表 = 一个执行组
# ---------------------------------------------------------------------------

def _read_sheets(path: str) -> list[tuple[str, list[list]]]:
    """读取工作簿，返回 [(表名, 行列表), ...]。"""
    ext = os.path.splitext(path)[1].lower()
    sheets: list[tuple[str, list[list]]] = []
    if ext == ".xls":
        import xlrd

        book = xlrd.open_workbook(filename=path)
        for sheet in book.sheets():
            rows = []
            for i in range(sheet.nrows):
                rows.append([cell.value if cell.ctype != 0 else ""
                             for cell in sheet.row(i)])
            sheets.append((sheet.name, rows))
    elif ext in (".xlsx", ".xlsm"):
        import openpyxl

        book = openpyxl.load_workbook(path, data_only=True, read_only=True)
        for sheet in book.worksheets:
            sheets.append((sheet.title,
                           [list(row) for row in sheet.iter_rows(values_only=True)]))
        book.close()
    else:
        raise ValueError("仅支持 .xls / .xlsx 文件")
    return sheets


def _rows_to_steps(rows: list[list]) -> list[Step]:
    steps: list[Step] = []
    for row in rows[1:]:  # 跳过表头
        if not row or all(v in ("", None) for v in row):
            continue
        steps.append(Step.from_row(row))
    return steps


def import_excel(path: str, single: bool = False) -> list[Group]:
    """导入 Excel。single=True 时只取第一个工作表，否则每个表一个组。"""
    sheets = _read_sheets(path)
    if not sheets:
        return []
    stem = os.path.splitext(os.path.basename(path))[0]
    if single:
        sheets = sheets[:1]
    groups: list[Group] = []
    for name, rows in sheets:
        group_name = name
        if len(sheets) == 1 and name.strip().lower() in GENERIC_SHEET_NAMES:
            group_name = stem
        if not group_name.strip():
            group_name = stem
        groups.append(Group(group_name, _rows_to_steps(rows)))
    return groups


def export_excel(path: str, groups: Iterable[Group], single: bool = False) -> None:
    """导出 Excel。single=True 时只导出第一个组（单表），否则一组一个表。"""
    groups = list(groups)
    if single:
        groups = groups[:1]
    ext = os.path.splitext(path)[1].lower()

    if ext == ".xls":
        import xlwt

        book = xlwt.Workbook(encoding="utf-8")
        used: set[str] = set()
        for group in groups:
            sheet = book.add_sheet(_safe_sheet_name(group.name, used))
            for col, head in enumerate(HEADERS):
                sheet.write(0, col, head)
            for row, step in enumerate(group.steps, start=1):
                for col, value in enumerate(step.to_row()):
                    sheet.write(row, col, value)
        book.save(path)
        return

    import openpyxl

    book = openpyxl.Workbook()
    book.remove(book.active)
    used = set()
    for group in groups:
        sheet = book.create_sheet(_safe_sheet_name(group.name, used))
        sheet.append(HEADERS)
        for step in group.steps:
            sheet.append(step.to_row())
        sheet.column_dimensions["B"].width = 50
        sheet.column_dimensions["D"].width = 30
    if not book.worksheets:
        book.create_sheet("Sheet1").append(HEADERS)
    book.save(path)


def _safe_sheet_name(name: str, used: set[str]) -> str:
    """生成合法且不重复的工作表名（Excel 限制 31 字符、禁止若干符号）。"""
    clean = "".join(ch for ch in (name or "组")
                    if ch not in '[]:*?/\\').strip() or "组"
    clean = clean[:31]
    candidate = clean
    index = 2
    while candidate.lower() in used:
        suffix = f"_{index}"
        candidate = clean[:31 - len(suffix)] + suffix
        index += 1
    used.add(candidate.lower())
    return candidate


# 兼容旧接口：只处理第一个工作表
def load_steps(path: str) -> list[Step]:
    groups = import_excel(path, single=True)
    return groups[0].steps if groups else []


def read_table(path: str) -> tuple[list[str], list[list]]:
    """读取数据表（CSV 之外的由 Excel 读取器处理），返回 (表头, 数据行)。

    供数据驱动使用：第一行是表头，之后每行是一条数据。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xls", ".xlsx", ".xlsm"):
        sheets = _read_sheets(path)
        if not sheets:
            return [], []
        name, rows = sheets[0]
        return ([str(c).strip() if c is not None else "" for c in rows[0]],
                rows[1:]) if rows else ([], [])
    import csv

    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = [row for row in csv.reader(fh)]
    if not rows:
        return [], []
    return ([str(c).strip() if c is not None else "" for c in rows[0]],
            rows[1:])


def load_data(path: str) -> list[dict]:
    """把 CSV / Excel 数据文件读成 [{列名: 值}, ...]，供数据驱动使用。

    第一行当表头；没有表头时用 col1/col2/… 兜底；
    整行都是空的跳过（Excel 末尾常见）。
    """
    header, rows = read_table(path)
    if not header:
        return []
    if not any(header):
        width = max((len(r) for r in rows), default=0)
        header = [f"col{i + 1}" for i in range(width)]
    data = []
    for row in rows:
        if not row or all(v in ("", None) for v in row):
            continue
        data.append({header[i] if i < len(header) else f"col{i + 1}":
                     ("" if v is None else str(v).strip())
                     for i, v in enumerate(row)})
    return data


def save_steps(path: str, steps: Iterable[Step]) -> None:
    export_excel(path, [Group("Sheet1", list(steps))], single=True)


def _count_steps(groups: dict[str, list[Step]], name: str,
                 seen: frozenset) -> int:
    """递归数一个组展开后有多少步；遇到无限重复返回 -1。

    调用 / 条件步骤会展开目标组（条件判断本身也算一步）；if_else 两个分支
    只会走一个，估算时取步数较多的那个。
    """
    if name in seen:                 # 循环调用：已展开过，不再重复数
        return 0
    seen = seen | {name}
    steps = groups.get(name)
    if steps is None:
        return 0
    total = 0
    for step in steps:
        if step.repeat == -1:
            return -1
        targets = target_groups(step)
        if targets:
            times = int(step.repeat) if step.repeat and step.repeat > 0 else 1
            inner = max(_count_steps(groups, target, seen)
                        for target in targets)
            if inner < 0:
                return -1
            total += (inner + 1) * times    # +1：条件/调用判断本身那一步
        else:
            total += 1
    return total


def estimate_steps(groups: dict[str, list[Step]], order: list[str],
                   data_rows: int = 1) -> int:
    """估算一次运行总共会执行多少步（展开调用组，乘数据行数）。

    循环运行或含「无限重复」时步数不可估，返回 -1（界面改用不确定进度）。
    """
    total = 0
    for name in order:
        count = _count_steps(groups, name, frozenset())
        if count < 0:
            return -1
        total += count
    return total * max(1, data_rows)


# ---------------------------------------------------------------------------
# 窗口操作（Windows）
# ---------------------------------------------------------------------------

def _enum_windows() -> list[tuple[int, str]]:
    """返回 [(hwnd, 标题), ...]，只收可见、有标题的窗口。"""
    if ctypes is None:  # pragma: no cover - 非 Windows
        return []
    found: list[tuple[int, str]] = []

    @_WNDENUMPROC
    def proc(hwnd, _lparam):
        # 回调里只做纯 Python 的标题读取与比较，绝不调外部函数
        user32 = ctypes.windll.user32
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value or ""
        if title.strip():
            found.append((int(hwnd), title))
        return True

    ctypes.windll.user32.EnumWindows(proc, 0)
    return found


def _find_window(title: str) -> Optional[int]:
    """按标题子串（不区分大小写）找窗口，返回 hwnd；找不到返回 None。"""
    if not title:
        return None
    needle = title.strip().lower()
    for hwnd, text in _enum_windows():
        if needle in text.lower():
            return hwnd
    return None


def _force_foreground(hwnd: int) -> None:
    """把窗口带到前台。绕过系统对后台进程设置前台的限制。"""
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, _SW_RESTORE)
    user32.ShowWindow(hwnd, _SW_SHOW)
    # 经典技巧：假装自己刚收到一个 Alt 按键，系统就允许设置前台
    user32.keybd_event(_VK_MENU, 0, 0, 0)
    user32.keybd_event(_VK_MENU, 0, _KEYEVENTF_KEYUP, 0)
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)


def _window_action(text: str) -> str:
    """执行 activate:标题 / wait:标题|秒 / close:标题，返回结果描述。"""
    if ctypes is None:
        raise StepError("窗口操作只支持 Windows")
    action, _, rest = (text or "").partition(":")
    action = action.strip().lower()
    if action == "wait":
        title, _, seconds = rest.partition("|")
        title = title.strip()
        try:
            limit = float(seconds.strip() or 5)
        except ValueError:
            limit = 5.0
        deadline = time.time() + max(0.0, limit)
        while True:
            hwnd = _find_window(title)
            if hwnd is not None:
                _force_foreground(hwnd)
                return f"窗口「{title}」已出现并激活"
            if time.time() >= deadline:
                raise StepError(f"等待窗口「{title}」超时（{limit:g} 秒）")
            time.sleep(POLL_INTERVAL)

    title = rest.strip()
    hwnd = _find_window(title)
    if hwnd is None:
        raise StepError(f"找不到标题包含「{title}」的窗口")
    if action == "activate":
        _force_foreground(hwnd)
        return f"已激活窗口「{title}」"
    if action == "close":
        ctypes.windll.user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
        return f"已关闭窗口「{title}」"
    raise StepError(f"未知窗口操作：{action}")


# ---------------------------------------------------------------------------
# 执行引擎
# ---------------------------------------------------------------------------

LogFn = Callable[[str, str], None]
StepFn = Callable[[str, int, "Step"], None]
StepDoneFn = Callable[[str, int, "Step", bool], None]
ConfirmFn = Callable[[str, int, "Step"], str]
ProgressFn = Callable[[int, int], None]


def render_content(text: str, row: Optional[dict] = None,
                   row_no: int = 0, vars: Optional[dict] = None) -> str:
    """把内容里的 {占位符} 替换成实际值。

    支持的占位符（查找顺序：内置 → 数据列 → 变量）：
      {index}   当前行号（从 1 开始）
      {time}    当前时刻 HH:MM:SS
      {date}    今天日期 YYYY-MM-DD
      {random}  4 位随机数字
      {列名}    当前数据行里该列的值（数据驱动）
      {变量名}  「设置变量 / 复制到变量」步骤存下的值
    数据列、变量不存在时保留原样，方便用户发现拼写问题。
    """
    if not text or "{" not in text:
        return text
    row = row or {}
    now = time.localtime()

    def replace(match: "re.Match") -> str:
        name = match.group(1).strip()
        if name == "index":
            return str(row_no)
        if name == "time":
            return time.strftime("%H:%M:%S", now)
        if name == "date":
            return time.strftime("%Y-%m-%d", now)
        if name == "random":
            return f"{random.randint(0, 9999):04d}"
        if name in row:
            value = row[name]
            return "" if value is None else str(value)
        if vars and name in vars:
            return str(vars[name])
        return match.group(0)   # 列/变量不存在：保留原样，方便发现拼写问题

    return re.sub(r"\{([^{}]+)\}", replace, text)


@dataclass
class Executor:
    """在后台线程中按组执行步骤，支持调用组、暂停与停止。

    data：数据驱动的行列表（list[dict]）。给了 data 时，整个流程会对每一行
    完整跑一遍，步骤内容里的 {列名} 会替换成该行的值。

    vars：运行期变量（「设置变量 / 复制到变量」写入，任意步骤里用 {变量名}
    读回）。作用范围是**一场运行**：run() 开始时清空，之后同一场运行内的
    所有组、所有数据行、所有循环轮次共享；下一场运行重新开始。
    """

    confidence: float = DEFAULT_CONFIDENCE
    base_dir: str = ""
    groups: dict[str, list[Step]] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    stop_event: threading.Event = field(default_factory=threading.Event)
    pause_event: threading.Event = field(default_factory=threading.Event)
    log: Optional[LogFn] = None
    on_step: Optional[StepFn] = None
    on_step_done: Optional[StepDoneFn] = None
    on_progress: Optional[ProgressFn] = None
    confirm: Optional[ConfirmFn] = None
    on_finish: Optional[Callable[[str], None]] = None
    data: list[dict] = field(default_factory=list)
    stats: list[dict] = field(default_factory=list)
    vars: dict[str, str] = field(default_factory=dict)
    _progress_done: int = 0
    _progress_total: int = 0

    # -- 生命周期 ---------------------------------------------------------
    def reset(self) -> None:
        self.stop_event.clear()
        self.pause_event.clear()
        self._stack = []
        self._row = {}
        self._row_no = 0
        self._step_ok = True
        self.stats = []
        self.vars = {}
        self._progress_done = 0

    def stop(self) -> None:
        self.stop_event.set()
        self.pause_event.clear()

    def toggle_pause(self) -> bool:
        if self.pause_event.is_set():
            self.pause_event.clear()
        else:
            self.pause_event.set()
        return self.pause_event.is_set()

    def _emit(self, level: str, msg: str) -> None:
        if self.log:
            self.log(level, msg)

    def _sleep(self, seconds: float) -> bool:
        """可中断的等待，返回 False 表示被停止。"""
        end = time.time() + max(0.0, seconds)
        while time.time() < end:
            if self.stop_event.is_set():
                return False
            while self.pause_event.is_set():
                if self.stop_event.is_set():
                    return False
                time.sleep(POLL_INTERVAL)
            time.sleep(min(POLL_INTERVAL, max(0.0, end - time.time())))
        return not self.stop_event.is_set()

    def _wait_ready(self) -> bool:
        while self.pause_event.is_set():
            if self.stop_event.is_set():
                return False
            time.sleep(POLL_INTERVAL)
        return not self.stop_event.is_set()

    # -- 单步动作 ---------------------------------------------------------
    def _locate(self, image: str):
        import pyautogui

        path = assets.resolve_image(image, self.base_dir or None)
        if not os.path.exists(path):
            # 文件缺失是配置错误（预检本该拦住）：明确中止，而不是让
            # OpenCV 抛一句看不懂的 imread 失败、更不是永远等下去
            raise StepError(f"找不到图片文件：{image}（{path}）")
        try:
            return pyautogui.locateCenterOnScreen(path,
                                                  confidence=self.confidence)
        except pyautogui.ImageNotFoundException:
            # pyautogui 默认「找不到就抛异常」，但引擎的语义是「返回 None，
            # 由调用方的等待/重试/超时逻辑决定怎么办」——这里必须接住，
            # 否则「等图片出现」整套逻辑都是死代码
            return None

    def _click(self, image: str, clicks: int, button: str, repeat: float,
               timeout: float = 0, on_fail: str = "skip") -> bool:
        """点击图片。返回 False 表示这步没做成（超时跳过或被停止）。

        timeout = 0 时一直重试直到找到——与旧版行为完全一致。
        """
        import pyautogui

        times = int(repeat) if repeat and repeat > 0 else 1
        done = 0
        deadline = time.time() + timeout if timeout and timeout > 0 else None
        self._step_ok = True
        while done < times:
            if not self._wait_ready():
                return False
            location = self._locate(image)
            if location is not None:
                pyautogui.click(location.x, location.y, clicks=clicks,
                                interval=0.2, duration=0.2, button=button)
                done += 1
                self._emit("info", f"已{('点击' if clicks == 1 else '双击')} {image} ({done}/{times})")
            else:
                if deadline is not None and time.time() >= deadline:
                    self._step_ok = False
                    self._emit("warn", f"等待图片 {image} 超时（{timeout:g} 秒）")
                    if on_fail == "stop":
                        raise StepError(f"等待图片 {image} 超时，按设置中止执行")
                    return False        # skip：这步算失败，继续往下
                self._emit("warn", f"未找到图片 {image}，重试中…")
            if not self._sleep(POLL_INTERVAL):
                return False
        return True

    def _hotkey(self, keys: str, repeat: float) -> None:
        import pyautogui

        parts = [k.strip() for k in keys.split(",") if k.strip()]
        if not parts:
            return
        times = int(repeat) if repeat and repeat > 0 else 1
        if repeat == -1:
            while self._wait_ready():
                pyautogui.hotkey(*parts)
                self._emit("info", f"执行热键 {','.join(parts)}")
                if not self._sleep(0.1):
                    return
            return
        for _ in range(times):
            if not self._wait_ready():
                return
            pyautogui.hotkey(*parts)
            self._emit("info", f"执行热键 {','.join(parts)}")
            if not self._sleep(0.1):
                return

    def run_step(self, step: Step, depth: int = 0) -> None:
        import pyautogui
        import pyperclip

        text = render_content((step.content or "").strip(), self._row,
                              self._row_no, self.vars)
        if step.type == "click":
            self._click(text, 1, "left", step.repeat, step.timeout,
                        step.on_fail)
        elif step.type == "dblclick":
            self._click(text, 2, "left", step.repeat, step.timeout,
                        step.on_fail)
        elif step.type == "rightclick":
            self._click(text, 1, "right", step.repeat, step.timeout,
                        step.on_fail)
        elif step.type == "input":
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            self._emit("info", f"输入：{text}")
            self._sleep(0.5)
        elif step.type == "wait":
            seconds = float(text)
            self._emit("info", f"等待 {seconds:g} 秒")
            self._sleep(seconds)
        elif step.type == "scroll":
            distance = int(float(text))
            # Windows 滚轮增量以 120 为 1 格（WHEEL_DELTA），pyautogui 又把
            # 参数原样传给 mouse_event 的 dwData——若直接传 100，多数应用
            # 做 100/120=0 的整数除法会一格都不滚。这里统一换算成整数格。
            notches = int(round(distance / 100.0))
            if notches == 0 and distance != 0:
                notches = 1 if distance > 0 else -1
            pyautogui.scroll(notches * 120)
            self._emit("info", f"滚轮 {distance}（{notches} 格）")
            self._sleep(0.1)
        elif step.type == "hotkey":
            self._hotkey(text, step.repeat)
        elif step.type == "time":
            now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            pyperclip.copy(now)
            pyautogui.hotkey("ctrl", "v")
            self._emit("info", f"粘贴时间：{now}")
            self._sleep(0.5)
        elif step.type == "cmd":
            self._emit("info", f"执行命令：{text}")
            os.system(text)
            self._sleep(0.5)
        elif step.type == "if_image":
            self._run_if_image(text, step, depth)
        elif step.type == "if_not_image":
            self._run_if_not_image(text, step, depth)
        elif step.type == "if_else":
            self._run_if_else(text, step, depth)
        elif step.type == "if_window":
            self._run_if_window(text, step, depth)
        elif step.type == "set":
            self._set_var(text)
        elif step.type == "copy":
            self._copy_to_var(text)
        elif step.type == "drag":
            self._drag(text, step)
        elif step.type == "window":
            result = _window_action(text)
            self._emit("info", result)
            self._sleep(0.2)
        elif step.type == "click_xy":
            x, y, button = _parse_xy(text)
            times = int(step.repeat) if step.repeat and step.repeat > 0 else 1
            for _ in range(times):
                if not self._wait_ready():
                    return
                pyautogui.click(x, y, button=button, interval=0.2,
                                duration=0.2)
                self._emit("info", f"点击坐标 ({x:g}, {y:g})")
                self._sleep(0.1)
        elif step.type == "move_xy":
            x, y, _button = _parse_xy(text)
            pyautogui.moveTo(x, y, duration=0.2)
            self._emit("info", f"鼠标移到 ({x:g}, {y:g})")
        else:
            raise StepError(f"未知指令类型：{step.type}")

    def _run_if_image(self, text: str, step: Step, depth: int = 0) -> None:
        """条件步骤：屏幕上出现指定图片，就调用对应的执行组。"""
        image, _, target = text.partition("|")
        image, target = image.strip(), target.strip()
        if not image or not target:
            raise StepError("条件步骤要写成 图片名|组名")
        location = self._locate(image)
        if location is None:
            self._emit("info", f"条件不满足：屏幕上没有 {image}，跳过")
            return
        self._emit("info", f"条件满足：找到 {image}，执行「{target}」")
        call_step = Step("call", target, step.repeat)
        self._call_group(call_step, depth)

    def _run_if_not_image(self, text: str, step: Step,
                          depth: int = 0) -> None:
        """条件步骤：屏幕上**没有**指定图片，才调用对应的执行组。"""
        image, _, target = text.partition("|")
        image, target = image.strip(), target.strip()
        if not image or not target:
            raise StepError("条件步骤要写成 图片名|组名")
        location = self._locate(image)
        if location is not None:
            self._emit("info", f"条件不满足：屏幕上还有 {image}，跳过")
            return
        self._emit("info", f"条件满足：屏幕上没有 {image}，执行「{target}」")
        call_step = Step("call", target, step.repeat)
        self._call_group(call_step, depth)

    def _run_if_else(self, text: str, step: Step, depth: int = 0) -> None:
        """条件步骤：图片出现执行 A 组，没出现执行 B 组（二选一）。"""
        image, _, rest = text.partition("|")
        yes, _, no = rest.partition("|")
        image, yes, no = image.strip(), yes.strip(), no.strip()
        if not image or not yes:
            raise StepError("二选一要写成 图片名|出现执行|没出现执行")
        location = self._locate(image)
        if location is not None:
            self._emit("info", f"条件满足：找到 {image}，执行「{yes}」")
            self._call_group(Step("call", yes, step.repeat), depth)
        elif no:
            self._emit("info", f"条件不满足：没有 {image}，执行「{no}」")
            self._call_group(Step("call", no, step.repeat), depth)
        else:
            self._emit("info", f"条件不满足：没有 {image}，跳过")

    def _run_if_window(self, text: str, step: Step, depth: int = 0) -> None:
        """条件步骤：存在标题包含指定文字的窗口，才调用对应的执行组。"""
        if ctypes is None:
            raise StepError("窗口条件只支持 Windows")
        title, _, target = text.partition("|")
        title, target = title.strip(), target.strip()
        if not title or not target:
            raise StepError("窗口条件要写成 窗口标题|组名")
        if _find_window(title) is None:
            self._emit("info", f"条件不满足：找不到窗口「{title}」，跳过")
            return
        self._emit("info", f"条件满足：窗口「{title}」存在，执行「{target}」")
        call_step = Step("call", target, step.repeat)
        self._call_group(call_step, depth)

    # -- 变量 -------------------------------------------------------------
    def _set_var(self, text: str) -> None:
        """设置变量：变量名=值（值里的占位符在传入前已渲染好）。"""
        name, _, value = text.partition("=")
        name = name.strip()
        if not name:
            raise StepError("设置变量要写成 变量名=值")
        self.vars[name] = value.strip()
        self._emit("info", f"设置变量 {name} = {value.strip()}")

    def _copy_to_var(self, text: str) -> None:
        """复制选中文字到变量：发一次 Ctrl+C，把剪贴板内容存进变量。

        为避免「其实没复制到、却把上一次的剪贴板内容当成成果」，步骤会先把
        剪贴板清空再复制；读到的文字同时存进变量，原来剪贴板里的内容会还原。
        """
        import pyautogui
        import pyperclip

        name = text.strip()
        if not name:
            raise StepError("复制到变量：变量名不能为空")
        try:
            previous = pyperclip.paste()
        except Exception:      # noqa: BLE001 - 剪贴板不可用时不影响主流程
            previous = ""
        pyperclip.copy("")     # 清空：没选中文字时结果应为空而不是旧内容
        pyautogui.hotkey("ctrl", "c")
        if not self._sleep(0.2):
            return             # 已停止
        try:
            value = pyperclip.paste()
        except Exception:      # noqa: BLE001
            value = ""
        try:
            pyperclip.copy(previous)   # 还原用户原来的剪贴板内容
        except Exception:      # noqa: BLE001
            pass
        if not value:
            self._step_ok = False
            self._emit("warn", f"没复制到文字（变量 {name} 未更新）")
            return
        self.vars[name] = value
        self._emit("info", f"已复制到变量 {name}：{value[:40]}"
                    + ("…" if len(value) > 40 else ""))

    # -- 拖拽 -------------------------------------------------------------
    def _drag_point(self, text: str, step: Step, what: str):
        """把拖拽的一端（图片名或 x,y）解析成屏幕坐标。

        图片等不到时按 step.timeout / step.on_fail 处理（与点击一致）；
        timeout=0 表示一直等。返回 None 表示这步没做成。
        """
        if _valid_xy(text):
            x, y, _button = _parse_xy(text)
            return (x, y)
        deadline = (time.time() + step.timeout
                    if step.timeout and step.timeout > 0 else None)
        while True:
            if not self._wait_ready():
                return None
            location = self._locate(text)
            if location is not None:
                return (location.x, location.y)
            if deadline is not None and time.time() >= deadline:
                self._step_ok = False
                self._emit("warn",
                           f"等待{what}图片 {text} 超时（{step.timeout:g} 秒）")
                if step.on_fail == "stop":
                    raise StepError(f"等待{what}图片 {text} 超时，"
                                    "按设置中止执行")
                return None
            self._emit("warn", f"未找到{what}图片 {text}，重试中…")
            if not self._sleep(POLL_INTERVAL):
                return None

    def _drag(self, text: str, step: Step) -> bool:
        """拖拽：起点|终点，两端各自是图片名或 x,y。

        两端都就位后才按下鼠标——避免「按下了却等不到终点」把界面卡在
        拖拽状态里。
        """
        import pyautogui

        start_text, _, end_text = text.partition("|")
        start = self._drag_point(start_text.strip(), step, "起点")
        if start is None:
            return False
        end = self._drag_point(end_text.strip(), step, "终点")
        if end is None:
            return False
        pyautogui.moveTo(start[0], start[1], duration=0.2)
        pyautogui.mouseDown()
        if not self._sleep(0.1):
            return False
        pyautogui.moveTo(end[0], end[1], duration=0.4)
        pyautogui.mouseUp()
        self._emit("info", f"拖拽 ({start[0]:g}, {start[1]:g}) → "
                           f"({end[0]:g}, {end[1]:g})")
        return True

    # -- 组执行 -----------------------------------------------------------
    def _run_group(self, name: str, depth: int = 0) -> None:
        steps = self.groups.get(name)
        if steps is None:
            raise StepError(f"找不到执行组：{name}")
        if depth > MAX_CALL_DEPTH:
            raise StepError(f"调用层级过深（可能循环调用）：{name}")
        self._emit("info", f"▶ 进入执行组「{name}」（{len(steps)} 步）")
        for index, step in enumerate(steps):
            if self.stop_event.is_set():
                return
            if not self._wait_ready():
                return
            if self.confirm is not None and step.type != "call":
                answer = self.confirm(name, index, step)
                if answer == "stop":
                    raise StepError("已停止（单步调试）")
                if answer == "skip":
                    self._emit("info", f"单步调试：跳过第 {index + 1} 步"
                                       f"（{step.label()}）")
                    continue
            if self.on_step:
                self.on_step(name, index, step)
            if self.on_progress:
                self.on_progress(self._progress_done, self._progress_total)
            started = time.time()
            failed = False
            fatal: Optional[StepError] = None
            self._step_ok = True
            if step.type == "call":
                self._call_group(step, depth)
            else:
                try:
                    self.run_step(step, depth)
                except StepError as exc:
                    # 致命错误（如窗口等待超时）：记入统计后再向上抛，
                    # 保证报告里能看到是哪一步失败的
                    failed = True
                    fatal = exc
                except Exception as exc:  # 单步失败不应终止整体
                    failed = True
                    self._emit("error", f"第 {index + 1} 步出错：{exc}")
            ok = not failed and self._step_ok
            self.stats.append({
                "group": name, "index": index + 1, "type": step.type,
                "content": render_content(step.content, self._row,
                                          self._row_no, self.vars),
                "note": step.note,
                "seconds": round(time.time() - started, 2),
                "ok": ok, "row": self._row_no,
            })
            self._progress_done += 1
            if self.on_step_done:
                self.on_step_done(name, index, step, ok)
            self._step_ok = True
            if fatal is not None:
                raise fatal
            if self.stop_event.is_set():
                return

    def _call_group(self, step: Step, depth: int) -> None:
        target = (step.content or "").strip()
        repeat = step.repeat or 0
        if repeat == -1:
            self._emit("info", f"↻ 无限调用「{target}」")
            while self._wait_ready():
                self._run_group(target, depth + 1)
            return
        times = int(repeat) if repeat and repeat > 0 else 1
        for _ in range(times):
            if not self._wait_ready():
                return
            self._run_group(target, depth + 1)

    def run(self, entry: Optional[str] = None, loop: bool = False) -> str:
        """阻塞执行。entry 为组名；None 表示按 order 顺序跑所有组。

        设置了 self.data 时，整个流程会对每一行数据完整执行一遍。
        """
        self.reset()
        if entry is None:
            names = [n for n in self.order if n in self.groups]
            if not names:
                names = list(self.groups)
        else:
            names = [entry]
        if not names:
            self._emit("error", "没有可执行的执行组")
            return "error"

        rows = self.data or [{}]
        if len(rows) > 1:
            self._emit("info", f"数据驱动：共 {len(rows)} 行，逐行执行")
        self._emit("info", f"开始执行：{'、'.join(names)}" +
                   ("（循环模式）" if loop else ""))
        try:
            for row_no, row in enumerate(rows, start=1):
                if self.stop_event.is_set():
                    self._emit("warn", "已停止")
                    return "stopped"
                self._row = row or {}
                self._row_no = row_no
                if len(rows) > 1:
                    self._emit("info", f"—— 第 {row_no}/{len(rows)} 行 ——")
                round_no = 0
                while True:
                    round_no += 1
                    if loop:
                        self._emit("info", f"—— 第 {round_no} 轮 ——")
                    for name in names:
                        if self.stop_event.is_set():
                            self._emit("warn", "已停止")
                            return "stopped"
                        self._run_group(name)
                        if self.stop_event.is_set():
                            self._emit("warn", "已停止")
                            return "stopped"
                    if not loop:
                        break
                    if not self._sleep(0.1):
                        self._emit("warn", "已停止")
                        return "stopped"
            self._emit("info", "执行完成")
            return "finished"
        except StepError as exc:
            self._emit("error", str(exc))
            return "error"
        except Exception as exc:  # noqa: BLE001
            self._emit("error", f"执行中断：{exc}")
            return "error"
        finally:
            if self.on_finish:
                self.on_finish("done")

    def report(self) -> dict:
        """结构化运行报告（每步耗时 / 成败 / 数据行号）。"""
        total = sum(item["seconds"] for item in self.stats)
        return {
            "steps": len(self.stats),
            "failed": sum(1 for item in self.stats if not item["ok"]),
            "seconds": round(total, 2),
            "rows": max((item["row"] for item in self.stats), default=0),
            "detail": list(self.stats),
        }


def report_markdown(report: dict, result: str) -> str:
    """把 report() 的结果渲染成 markdown（界面和无头模式共用）。"""
    lines = [
        "# 运行报告",
        "",
        f"- 结果：{result}",
        f"- 步骤数：{report['steps']}，失败：{report['failed']}",
        f"- 总耗时：{report['seconds']:g} 秒",
        f"- 数据行数：{report['rows']}",
        "",
        "| 执行组 | 行号 | 步骤 | 类型 | 内容 | 耗时(秒) | 结果 |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in report["detail"]:
        content = str(item["content"]).replace("|", "\\|")
        lines.append(
            f"| {item['group']} | {item['row']} | {item['index']} "
            f"| {STEP_TYPES.get(item['type'], (item['type'],))[0]} | {content} "
            f"| {item['seconds']:g} | {'成功' if item['ok'] else '失败'} |")
    return "\n".join(lines) + "\n"
