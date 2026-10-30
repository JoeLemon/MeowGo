# -*- coding: utf-8 -*-
"""RPA 核心引擎：执行组、步骤模型、Excel 读写、校验与执行。

与界面完全解耦，方便单独测试或命令行调用。
"""
from __future__ import annotations

import ast
import csv
import operator
import os
import random
import re
import subprocess
import sys
import time
import threading
from collections import namedtuple
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from . import assets, theme

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
    "input":   ("输入文本", "要输入的文字", False, ""),
    "wait":    ("等待",     "等待秒数，如 1.5", False, ""),
    "scroll":  ("滚轮",     "滚动距离，正数向上 / 负数向下，如 -200", False, ""),
    "hotkey":  ("热键组合", "用英文逗号分隔，如 ctrl,s 或 win,r", True, "（-1 表示无限）"),
    "time":    ("粘贴当前时间", "固定填写 当前时间（保留该关键字）", False, ""),
    "cmd":     ("系统命令", "CMD 命令，如 start notepad", False, ""),
    "call":    ("调用执行组", "要调用的组名（实现步骤复用）", True, "（-1 表示无限）"),
    # ---- 新版扩展类型 ----
    "if_image": ("条件：出现图片则执行", "图片名|组名，如 弹窗.png|关闭弹窗", True, "执行次数"),
    "window":  ("窗口操作", "activate:标题 / wait:标题|秒 / close:标题", False, ""),
    "click_xy": ("坐标点击", "x,y，如 500,300；可加 ,right 表示右键", True, "点击次数"),
    "move_xy": ("移动鼠标", "x,y，如 500,300", False, ""),
    # ---- 条件与变量 ----
    "if_not_image": ("条件：没出现图片则执行", "图片名|组名", True, "执行次数"),
    "if_else": ("条件：二选一", "图片名|出现执行|没出现执行", True, "执行次数"),
    "if_window": ("条件：窗口存在则执行", "窗口标题|组名", True, "执行次数"),
    "set": ("设置变量", "变量名=值，如 次数={index}", False, ""),
    "copy": ("复制选中文字到变量", "变量名，如 订单号", False, ""),
    # ---- 鼠标 ----
    "drag": ("拖拽", "起点图|终点图；也可写 x1,y1|x2,y2", False, ""),
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
    "if_not_image": 15.0,
    "if_else": 16.0,
    "if_window": 17.0,
    "set": 18.0,
    "copy": 19.0,
    "drag": 20.0,
}
CODE_TO_TYPE = {v: k for k, v in TYPE_TO_CODE.items()}

IMAGE_TYPES = {"click", "dblclick", "rightclick"}
REPEAT_TYPES = {"click", "dblclick", "rightclick", "hotkey", "call",
                "if_image", "if_not_image", "if_else", "if_window",
                "click_xy"}
# 界面上「截图替换」等图片相关操作适用的步骤类型
IMAGE_STEP_TYPES = IMAGE_TYPES | {"if_image", "if_not_image", "if_else",
                                  "drag"}


Point = namedtuple("Point", "x y")

# 条件类步骤（内容里的 | 后面是执行组）
CONDITION_TYPES = {"if_image", "if_not_image", "if_else", "if_window"}

_OFFSET_RE = re.compile(r"\+\s*(-?\d+)\s*,\s*(-?\d+)\s*$")
_NTH_RE = re.compile(r"#\s*(\d+)\s*$")
_REGION_RE = re.compile(
    r"@\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*$")


@dataclass
class ImageTarget:
    """一条图片写法解析后的结果。

    写法（顺序固定，都可省略）：
        按钮.png                  普通图片
        按钮.png;备用.png         任一命中即可
        按钮.png@100,200,600,400  只在区域内找
        按钮.png#2                命中第 2 个（从上到下、从左到右）
        按钮.png+12,-8            命中点再偏移一点
    """

    name: str = ""
    region: Optional[tuple[int, int, int, int]] = None
    nth: Optional[int] = None          # None = 用步骤上的设置；0 = 第一个
    offset: tuple[int, int] = (0, 0)


def _split_image_suffix(part: str) -> ImageTarget:
    """拆出单个图片写法里的 @区域 / #第几个 / +偏移。"""
    name = (part or "").strip()
    offset = (0, 0)
    nth: Optional[int] = None
    region = None

    match = _OFFSET_RE.search(name)
    if match:
        offset = (int(match.group(1)), int(match.group(2)))
        name = name[:match.start()].strip()

    match = _NTH_RE.search(name)
    if match:
        nth = max(0, int(match.group(1)) - 1)   # 用户写 #1 = 第一个
        name = name[:match.start()].strip()

    match = _REGION_RE.search(name)
    if match:
        region = (int(match.group(1)), int(match.group(2)),
                  int(match.group(3)), int(match.group(4)))
        name = name[:match.start()].strip()

    return ImageTarget(name, region, nth, offset)


def parse_image_targets(text: str) -> list[ImageTarget]:
    """把内容解析成候选图列表（; 分隔，每个可带自己的后缀）。"""
    targets = []
    for part in str(text or "").split(";"):
        target = _split_image_suffix(part)
        if target.name:
            targets.append(target)
    return targets


def parse_image_target(text: str) -> ImageTarget:
    """只取第一条候选（给只需要单个图片的调用方用）。"""
    targets = parse_image_targets(text)
    return targets[0] if targets else ImageTarget()


def image_names(text: str) -> list[str]:
    """内容里引用的图片文件名（去掉 @区域 / #序号 / +偏移 等后缀）。"""
    return [target.name for target in parse_image_targets(text)]


def _parse_region(text: str) -> Optional[tuple[int, int, int, int]]:
    """把 "x,y,宽,高" 解析成整数四元组；格式不对返回 None。"""
    if not text:
        return None
    parts = [p.strip() for p in str(text).split(",")]
    if len(parts) != 4:
        return None
    try:
        x, y, width, height = (int(float(p)) for p in parts)
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return x, y, width, height


def parse_region(text: str) -> Optional[tuple[int, int, int, int]]:
    """公开的「x,y,宽,高」解析（界面的识别范围输入框用）。"""
    return _parse_region(text)


def target_groups(step: "Step") -> list[str]:
    """这一步会调用哪些执行组（调用 / 条件满足或不满足时）。"""
    text = (step.content or "").strip()
    if step.type == "call":
        return [text] if text else []
    if step.type in ("if_image", "if_not_image", "if_window"):
        parts = [p.strip() for p in text.split("|")]
        return [parts[1]] if len(parts) > 1 and parts[1] else []
    if step.type == "if_else":
        parts = [p.strip() for p in text.split("|")]
        return [p for p in parts[1:3] if p]
    return []

# 图片步骤超时后的处理方式（timeout > 0 时生效）
FAIL_ACTIONS = ("skip", "stop")
# 文本输入方式：paste = 剪贴板粘贴（快，支持中文）；type = 逐字输入（兼容个别应用）
INPUT_MODES = ("paste", "type")

DEFAULT_CONFIDENCE = 0.9
POLL_INTERVAL = 0.1
MAX_CALL_DEPTH = 20
HEADERS = ["指令类型", "内容", "重复次数(-1无限)", "备注"]
GENERIC_SHEET_NAMES = {"sheet1", "sheet", "工作表1", "sheet2"}

# pyautogui 的 confidence 参数（相似度匹配）依赖 OpenCV；没装 cv2 时它只会抛
# 一句「The confidence keyword argument is only available if OpenCV is
# installed.」——对用户毫无帮助，所以这里统一换成能照着做的提示。
OPENCV_HINT = ("未安装 OpenCV（opencv-python），图片识别无法运行。\n"
               "请在命令行执行下面的命令，装好后重启 MeowGo：\n"
               "pip install opencv-python")
# 校验结果里用的单行版本
OPENCV_PROBLEM = ("未安装 OpenCV（opencv-python），图片识别步骤无法运行。"
                  "请在命令行执行：pip install opencv-python")


def opencv_available() -> bool:
    """当前环境能否做相似度匹配（pyautogui 的 confidence 需要 cv2）。"""
    try:
        import cv2  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 - 缺失或 DLL 加载失败都按不可用处理
        return False


def find_images(text: str, base_dir: str = "",
                confidence: float = DEFAULT_CONFIDENCE,
                region: Optional[tuple[int, int, int, int]] = None,
                grayscale: bool = False) -> list[dict]:
    """在屏幕上找内容里写的图片（界面「检测图片」用，不点击）。

    返回每个候选图的命中情况：name / missing / points。points 是匹配框
    的中心点，按从上到下、从左到右排序。
    """
    import pyautogui

    result = []
    for target in parse_image_targets(text):
        path = assets.resolve_image(target.name, base_dir or None)
        item = {"name": target.name, "missing": False, "points": []}
        if not os.path.exists(path):
            item["missing"] = True
            result.append(item)
            continue
        if not opencv_available():
            raise StepError(OPENCV_HINT)
        kwargs = {"confidence": confidence, "grayscale": grayscale}
        use_region = target.region or region
        if use_region:
            kwargs["region"] = use_region
        try:
            boxes = list(pyautogui.locateAllOnScreen(path, **kwargs))
        except Exception:  # noqa: BLE001 - 找不到/环境不支持都按「没命中」
            boxes = []
        boxes.sort(key=lambda box: (box.top, box.left))
        item["points"] = [(box.left + box.width / 2,
                           box.top + box.height / 2) for box in boxes]
        result.append(item)
    return result


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


# ---------------------------------------------------------------------------
# 变量表达式求值：set 步骤里「变量名=表达式」。表达式**只支持纯算术**
# （+ - * / // % ** 和括号），其余一律当作字符串原样保存——既避免 eval
# 注入，也符合「set 文本=订单-{编号}」这种拼接场景的直觉。
# ---------------------------------------------------------------------------
_ARITH_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_OPERATOR_RE = re.compile(r"[+\-*/%]")
_NUMERIC_RE = re.compile(r"^[0-9+\-*/().%\s]+$")


def _eval_arith(node: "ast.AST"):
    """受控求值：只允许数字常量 + 算术运算符，其余抛异常。"""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise ValueError("非常量")
    if isinstance(node, ast.BinOp) and type(node.op) in _ARITH_OPS:
        return _ARITH_OPS[type(node.op)](_eval_arith(node.left),
                                         _eval_arith(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in (ast.UAdd, ast.USub):
        value = _eval_arith(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    raise ValueError("不支持的表达式")


def _eval_expression(expr: str) -> str:
    """求 set 步骤的值；纯算术表达式才计算，其余原样返回字符串。"""
    expr = (expr or "").strip()
    if not expr or _OPERATOR_RE.search(expr) is None:
        return expr                      # 无运算符：字符串或纯数字，原样
    if not _NUMERIC_RE.fullmatch(expr):
        return expr                      # 含非数字字符：字符串拼接，原样
    try:
        result = _eval_arith(ast.parse(expr, mode="eval").body)
    except Exception:  # noqa: BLE001 — 求值失败按字符串原样保存
        return expr
    if isinstance(result, float):
        if result.is_integer():
            return str(int(result))
        return f"{result:.6f}".rstrip("0").rstrip(".")
    return str(result)


@dataclass
class Step:
    """一条指令。

    timeout / on_fail 对图片 / 拖拽 / 条件步骤有效：
      timeout = 0   一直等图片出现（旧版行为，默认）
      timeout > 0   最多等这么久；到时按 on_fail 处理
      on_fail = skip  跳过这步继续往下（默认）
      on_fail = stop  中止整个执行
    Excel 只有 4 列，没有这两项的列——导入的旧文件一律按默认值
    （一直等）处理，与旧版完全一致。条件步骤超时按「条件不满足」处理。

    region / nth / grayscale 是可选的识别微调（界面上放在「高级选项」）：
      region     "x,y,宽,高"，只在屏幕这块区域里找
      nth        命中第几个（0 = 最左上那个）
      grayscale  灰度匹配，抗颜色抖动
    retry / retry_delay：这一步没成功时重试几次、每次间隔多少秒。
    input_mode：input 步骤用 paste（默认）还是 type（逐字，仅英文数字）。
    """

    type: str = "click"
    content: str = ""
    repeat: float = 0
    note: str = ""
    timeout: float = 0
    on_fail: str = "skip"
    region: str = ""
    nth: int = 0
    grayscale: bool = False
    retry: int = 0
    retry_delay: float = 0.5
    input_mode: str = "paste"

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
        elif self.type in ("input", "hotkey", "time", "cmd", "call", "copy",
                           "set"):
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

        # 图片类、拖拽、条件都可以「最多等 N 秒」
        if (self.type in IMAGE_TYPES or self.type == "drag"
                or self.type in CONDITION_TYPES) and self.timeout:
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

        if self.region and self.type in IMAGE_STEP_TYPES:
            if _parse_region(self.region) is None:
                errors.append(f"{where}：识别范围要写成 x,y,宽,高")

        if self.input_mode not in INPUT_MODES:
            errors.append(f"{where}：输入方式只能是 paste 或 type")

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
                    self.grayscale, self.retry, self.retry_delay,
                    self.input_mode)

    def to_dict(self) -> dict:
        return {"type": self.type, "content": self.content,
                "repeat": self.repeat, "note": self.note,
                "timeout": self.timeout, "on_fail": self.on_fail,
                "region": self.region, "nth": self.nth,
                "grayscale": self.grayscale, "retry": self.retry,
                "retry_delay": self.retry_delay,
                "input_mode": self.input_mode}

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
                   retry_delay=float(
                       data.get("retry_delay", 0.5)
                       if data.get("retry_delay") not in (None, "") else 0.5),
                   input_mode=str(data.get("input_mode", "paste")
                                 or "paste"))


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
                start = (step.content or "").split("|")[0].strip()
                # 起点写坐标（100,200）时没有图片可查
                for item in ([] if _valid_xy(start) else image_names(start)):
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


def validate_environment(groups: Iterable[Group]) -> list[str]:
    """运行前的本机环境检查：有图片步骤却没装 OpenCV 时提前说清楚。

    和 validate_project 分开，是因为这检查的是「这台机器」而不是配置；
    只有纯坐标 / 文本流程的用户不需要 OpenCV，不该被拦。
    """
    if opencv_available():
        return []
    for group in groups:
        for step in group.steps:
            if step.type in IMAGE_TYPES or step.type in (
                    "if_image", "if_not_image", "if_else"):
                return [OPENCV_PROBLEM]
            if step.type == "drag":
                start = (step.content or "").split("|")[0].strip()
                # 起点写坐标时不需要图片识别
                if start and not _valid_xy(start):
                    return [OPENCV_PROBLEM]
    return []


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


RESULT_SUFFIX = ".result.csv"


def result_path(data_path: str) -> str:
    """数据文件对应的结果文件：同目录下 <文件名>.result.csv。"""
    stem, _ = os.path.splitext(os.path.abspath(data_path))
    return stem + RESULT_SUFFIX


def read_results(path: str) -> list[dict]:
    """读回结果文件（断点续跑 / 合并用）；不存在返回空列表。"""
    if not path or not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_results(data: list[dict], results: list[dict], path: str,
                  merge: bool = True) -> str:
    """把每行结果写成 CSV（带 BOM，Excel 直接打开）。

    data     原始数据行（用来补齐数据列）
    results  Executor.row_results：{row, ok, failed_step, vars}
    merge    文件已存在时按 __行号 合并，断点续跑不会丢旧行
    """
    previous = read_results(path) if merge else []
    merged: dict[int, dict] = {}
    for old in previous:
        try:
            key = int(float(old.get("__行号", "")))
        except (TypeError, ValueError):
            continue
        merged[key] = old

    columns: list[str] = []
    for row in data:
        for key in row:
            if key not in columns:
                columns.append(key)

    extra: list[str] = []
    for item in results:
        for key in (item.get("vars") or {}):
            if key not in columns and key not in extra:
                extra.append(key)
    reserved = {"__行号", "__结果", "__失败步骤"}
    var_columns = []
    for key in extra:
        name = (key if key not in columns and key not in reserved
                else f"结果_{key}")
        while name in columns or name in reserved:
            name = f"结果_{name}"
        var_columns.append((key, name))

    fieldnames = list(columns) + ["__行号", "__结果", "__失败步骤"]
    fieldnames += [name for _key, name in var_columns]

    for item in results:
        row_no = int(item["row"])
        source = data[row_no - 1] if 0 < row_no <= len(data) else {}
        record = {key: source.get(key, "") for key in columns}
        record["__行号"] = row_no
        record["__结果"] = "成功" if item.get("ok") else "失败"
        record["__失败步骤"] = item.get("failed_step", "")
        values = item.get("vars") or {}
        for key, name in var_columns:
            record[name] = values.get(key, "")
        merged[row_no] = record

    # 旧文件里可能有这次没再出现的变量列，原样保留
    for old in previous:
        for key in old:
            if key and key not in fieldnames:
                fieldnames.append(key)
    for record in merged.values():
        for name in fieldnames:
            record.setdefault(name, "")

    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames,
                                extrasaction="ignore")
        writer.writeheader()
        for row_no in sorted(merged):
            writer.writerow(merged[row_no])
    os.replace(tmp, path)
    return path


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
    # 失败截图目录（空 = 不截图）；由界面 / 命令行按需设置
    shot_dir: str = ""
    # 数据驱动从第几行之后继续（0 = 从头）；由「断点续跑」设置
    row_offset: int = 0
    # 每行跑完后的一行结果：{row, ok, failed_step, vars}
    row_results: list[dict] = field(default_factory=list)
    _progress_done: int = 0
    _progress_total: int = 0
    # 当前数据行里写过的变量名（结果回写只带本行产生/更新的列）
    _row_var_keys: set[str] = field(default_factory=set)

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
        self.row_results = []
        self._row_var_keys = set()
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
    def _locate(self, image: str, step: Optional[Step] = None):
        """找图片，返回 Point；找不到返回 None。

        支持候选图（; 分隔）、区域（@x,y,w,h 或 step.region）、第 N 个
        （#n 或 step.nth）、灰度（step.grayscale）和命中点偏移（+dx,dy）。
        """
        targets = parse_image_targets(image)
        if not targets:
            raise StepError("图片名不能为空")

        missing = []
        for target in targets:
            path = assets.resolve_image(target.name, self.base_dir or None)
            if not os.path.exists(path):
                missing.append(target.name)
                continue
            region = target.region or _parse_region(
                getattr(step, "region", "") if step else "")
            nth = (target.nth if target.nth is not None
                   else int(getattr(step, "nth", 0) or 0))
            grayscale = bool(getattr(step, "grayscale", False))
            point = self._locate_one(path, region, nth, grayscale)
            if point is not None:
                dx, dy = target.offset
                return Point(point.x + dx, point.y + dy)

        if missing and len(missing) == len(targets):
            # 文件缺失是配置错误（预检本该拦住）：明确中止，而不是让
            # OpenCV 抛一句看不懂的 imread 失败、更不是永远等下去
            raise StepError("找不到图片文件：" + "、".join(missing))
        return None

    def _locate_one(self, path: str,
                    region: Optional[tuple[int, int, int, int]],
                    nth: int, grayscale: bool):
        """在单张图上找第 nth 个（0 = 第一个）。"""
        import pyautogui

        if not opencv_available():
            raise StepError(OPENCV_HINT)
        kwargs = {"confidence": self.confidence, "grayscale": grayscale}
        if region:
            kwargs["region"] = region
        try:
            if nth and nth > 0:
                boxes = list(pyautogui.locateAllOnScreen(path, **kwargs))
                # 统一成「从上到下、从左到右」，用户写 #2 才有稳定含义
                boxes.sort(key=lambda box: (box.top, box.left))
                if nth >= len(boxes):
                    return None
                box = boxes[nth]
                return Point(box.left + box.width / 2,
                             box.top + box.height / 2)
            return pyautogui.locateCenterOnScreen(path, **kwargs)
        except pyautogui.ImageNotFoundException:
            # pyautogui 默认「找不到就抛异常」，但引擎的语义是「返回 None，
            # 由调用方的等待/重试/超时逻辑决定怎么办」——这里必须接住，
            # 否则「等图片出现」整套逻辑都是死代码
            return None

    def _click(self, image: str, clicks: int, button: str, repeat: float,
               timeout: float = 0, on_fail: str = "skip",
               step: Optional[Step] = None) -> bool:
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
            location = self._locate(image, step)
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
                        step.on_fail, step=step)
        elif step.type == "dblclick":
            self._click(text, 2, "left", step.repeat, step.timeout,
                        step.on_fail, step=step)
        elif step.type == "rightclick":
            self._click(text, 1, "right", step.repeat, step.timeout,
                        step.on_fail, step=step)
        elif step.type == "input":
            self._input_text(text, step.input_mode)
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
            self._input_text(now)
            self._emit("info", f"粘贴时间：{now}")
        elif step.type == "cmd":
            self._run_cmd(text)
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

    def _input_text(self, text: str, mode: str = "paste") -> None:
        """输入文本：默认剪贴板粘贴；type 模式逐字输入（仅英文数字）。

        粘贴后会等目标应用读完剪贴板，再把用户原来的剪贴板内容还回去；
        如果这期间剪贴板已经被别的程序改了，就不动它。
        """
        import pyautogui
        import pyperclip

        if mode == "type":
            if all(ord(ch) < 128 for ch in text):
                pyautogui.write(text, interval=0.02)
                self._emit("info", f"逐字输入：{text}")
                self._sleep(0.2)
                return
            self._emit("warn", "逐字输入不支持中文，已改用剪贴板粘贴")

        try:
            previous = pyperclip.paste()
        except Exception:      # noqa: BLE001 - 剪贴板不可用时不影响主流程
            previous = ""
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
        self._emit("info", f"输入：{text}")
        self._sleep(0.5)
        try:
            if pyperclip.paste() == text:      # 没被别人改过才还原
                pyperclip.copy(previous)
        except Exception:      # noqa: BLE001
            pass

    def _run_cmd(self, text: str) -> None:
        """执行系统命令，并按返回码判断这步是否成功。"""
        self._emit("info", f"执行命令：{text}")
        kwargs = {"shell": True, "capture_output": True, "text": True,
                  "errors": "replace"}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(text, **kwargs)
        except Exception as exc:  # noqa: BLE001
            self._step_ok = False
            self._emit("error", f"命令执行失败：{exc}")
            return
        output = (proc.stdout or "").strip()
        error = (proc.stderr or "").strip()
        if output:
            self._emit("info", output[:500])
        if proc.returncode:
            self._step_ok = False
            self._emit("error", f"命令返回 {proc.returncode}："
                                f"{(error or output)[:300]}")
        self._sleep(0.2)

    def _wait_image(self, image: str, step: Step, want_present: bool):
        """按 step.timeout 等图片出现/消失；timeout=0 时只判断一次。"""
        deadline = (time.time() + step.timeout
                    if step.timeout and step.timeout > 0 else None)
        while True:
            location = self._locate(image, step)
            if (location is not None) == want_present:
                return location
            if deadline is None or time.time() >= deadline:
                return location
            if not self._sleep(POLL_INTERVAL):
                return location

    def _wait_window(self, title: str, timeout: float):
        """按 timeout 等窗口出现；timeout=0 时只找一次。"""
        deadline = (time.time() + timeout
                    if timeout and timeout > 0 else None)
        while True:
            window = _find_window(title)
            if window is not None:
                return window
            if deadline is None or time.time() >= deadline:
                return None
            if not self._sleep(POLL_INTERVAL):
                return None

    def _run_if_image(self, text: str, step: Step, depth: int = 0) -> None:
        """条件步骤：屏幕上出现指定图片，就调用对应的执行组。"""
        image, _, target = text.partition("|")
        image, target = image.strip(), target.strip()
        if not image or not target:
            raise StepError("条件步骤要写成 图片名|组名")
        location = self._wait_image(image, step, True)
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
        location = self._wait_image(image, step, False)
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
        location = self._wait_image(image, step, True)
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
        if self._wait_window(title, step.timeout) is None:
            self._emit("info", f"条件不满足：找不到窗口「{title}」，跳过")
            return
        self._emit("info", f"条件满足：窗口「{title}」存在，执行「{target}」")
        call_step = Step("call", target, step.repeat)
        self._call_group(call_step, depth)

    # -- 变量 -------------------------------------------------------------
    def _set_var(self, text: str) -> None:
        """设置变量：变量名=值。

        值里的 {占位符} 在传入前已渲染好；若渲染后是纯算术表达式
        （如 5+1、12.5*4），会先算出结果再存，否则按字符串原样保存。
        """
        name, _, value = text.partition("=")
        name = name.strip()
        if not name:
            raise StepError("设置变量要写成 变量名=值")
        value = _eval_expression(value)
        self.vars[name] = value
        self._row_var_keys.add(name)
        self._emit("info", f"设置变量 {name} = {value}")

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
        self._row_var_keys.add(name)
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
            location = self._locate(text, step)
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
        try:
            if not self._sleep(0.1):
                return False
            pyautogui.moveTo(end[0], end[1], duration=0.4)
            self._emit("info", f"拖拽 ({start[0]:g}, {start[1]:g}) → "
                               f"({end[0]:g}, {end[1]:g})")
            return True
        finally:
            # 即使中途停止 / 移动失败，也一定把左键松开
            pyautogui.mouseUp()

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
            attempts = 0
            max_attempts = 1 + max(0, int(step.retry or 0))
            while True:
                attempts += 1
                failed = False
                fatal = None
                self._step_ok = True
                try:
                    if step.type == "call":
                        self._call_group(step, depth)
                    else:
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
                if ok or fatal is not None or attempts >= max_attempts:
                    break
                if self.stop_event.is_set() or not self._wait_ready():
                    break
                self._emit("warn", f"第 {index + 1} 步失败，"
                                   f"{step.retry_delay:g} 秒后重试"
                                   f"（{attempts}/{max_attempts - 1}）")
                if not self._sleep(step.retry_delay):
                    break

            ok = not failed and self._step_ok
            shot = ""
            # 调用组的失败截图由组内那一步负责，避免重复
            if (not ok and step.type != "call"
                    and not self.stop_event.is_set() and self.shot_dir):
                shot = self._capture_failure(name, index, step)
            self.stats.append({
                "group": name, "index": index + 1, "type": step.type,
                "content": render_content(step.content, self._row,
                                          self._row_no, self.vars),
                "note": step.note,
                "seconds": round(time.time() - started, 2),
                "ok": ok, "row": self._row_no,
                "attempts": attempts, "shot": shot,
            })
            self._progress_done += 1
            if self.on_step_done:
                self.on_step_done(name, index, step, ok)
            self._step_ok = True
            if fatal is not None:
                raise fatal
            if self.stop_event.is_set():
                return

    def _capture_failure(self, group: str, index: int, step: Step) -> str:
        """失败时截一张全屏图，返回文件路径；失败返回空串。"""
        import pyautogui

        try:
            os.makedirs(self.shot_dir, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            stamp += f"_{int(time.time() * 1000) % 1000:03d}"
            safe = re.sub(r'[\\/:*?"<>|()\s]+', "_",
                          f"{group}_{index + 1}")
            path = os.path.join(self.shot_dir, f"fail_{stamp}_{safe}.png")
            image = pyautogui.screenshot()
            image.save(path)
            self._emit("info", f"已保存失败截图：{path}")
            return path
        except Exception as exc:  # noqa: BLE001 - 截图失败不能影响主流程
            self._emit("warn", f"失败截图保存失败：{exc}")
            return ""

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
        start_row = max(0, int(self.row_offset or 0))
        if self.data and start_row:
            self._emit("info", f"从第 {start_row + 1} 行继续"
                               f"（跳过前 {start_row} 行）")
        if len(rows) > 1:
            self._emit("info", f"数据驱动：共 {len(rows)} 行，逐行执行")
        self._emit("info", f"开始执行：{'、'.join(names)}" +
                   ("（循环模式）" if loop else ""))
        stat_start = 0
        try:
            for row_no, row in enumerate(rows, start=1):
                if row_no <= start_row:
                    continue
                if self.stop_event.is_set():
                    self._emit("warn", "已停止")
                    return "stopped"
                self._row = row or {}
                self._row_no = row_no
                self._row_var_keys = set()
                stat_start = len(self.stats)
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
                if not loop:
                    self._record_row(row_no, stat_start)
            self._emit("info", "执行完成")
            return "finished"
        except StepError as exc:
            self._emit("error", str(exc))
            if not loop:
                self._record_row(self._row_no, stat_start, force_failed=True)
            return "error"
        except Exception as exc:  # noqa: BLE001
            self._emit("error", f"执行中断：{exc}")
            return "error"
        finally:
            if self.on_finish:
                self.on_finish("done")

    def _record_row(self, row_no: int, stat_start: int,
                    force_failed: bool = False) -> None:
        """把一行数据的结果收进 row_results（给结果回写用）。"""
        if not self.data or row_no <= 0:
            return
        if (self.row_results
                and self.row_results[-1].get("row") == row_no):
            return
        items = self.stats[stat_start:]
        failed = [item for item in items if not item["ok"]]
        failed_step = ""
        if failed:
            item = failed[0]
            label = STEP_TYPES.get(item["type"], (item["type"],))[0]
            failed_step = f"第 {item['index']} 步 {label}"
        self.row_results.append({
            "row": row_no,
            "ok": not failed and not force_failed,
            "failed_step": failed_step,
            # 只带本行写过的变量，避免上一行的结果串到下一行
            "vars": {key: self.vars[key] for key in self._row_var_keys
                     if key in self.vars},
        })

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


def report_markdown(report: dict, result: str,
                    report_dir: Optional[str] = None) -> str:
    """把 report() 的结果渲染成 markdown（界面和无头模式共用）。

    report_dir 给了时，截图链接会转成相对于报告目录的路径，方便直接点开。
    """
    detail = report["detail"]
    has_shot = any(item.get("shot") for item in detail)
    has_attempts = any(int(item.get("attempts", 1) or 1) > 1
                       for item in detail)
    headers = ["执行组", "行号", "步骤", "类型", "内容", "耗时(秒)", "结果"]
    aligns = ["---"] * len(headers)
    if has_attempts:
        headers.append("重试")
        aligns.append("---")
    if has_shot:
        headers.append("截图")
        aligns.append("---")

    lines = [
        "# 运行报告",
        "",
        f"- 结果：{result}",
        f"- 步骤数：{report['steps']}，失败：{report['failed']}",
        f"- 总耗时：{report['seconds']:g} 秒",
        f"- 数据行数：{report['rows']}",
        "",
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(aligns) + "|",
    ]
    for item in detail:
        content = str(item["content"]).replace("|", "\\|")
        cells = [
            str(item["group"]), str(item["row"]), str(item["index"]),
            STEP_TYPES.get(item["type"], (item["type"],))[0], content,
            f"{item['seconds']:g}", "成功" if item["ok"] else "失败",
        ]
        if has_attempts:
            cells.append(str(item.get("attempts", 1)))
        if has_shot:
            shot = item.get("shot") or ""
            if shot:
                if report_dir:
                    shot = os.path.relpath(shot, report_dir)
                else:
                    shot = os.path.basename(shot)
                cells.append(f"[查看]({shot.replace(os.sep, '/')})")
            else:
                cells.append("")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def write_report_index(directory: str, keep: int = 100) -> str:
    """生成 reports/index.html，并只保留最近 keep 份报告。

    返回 index.html 的路径。截图只清理「没有被保留报告引用、且超过 7 天」
    的文件，避免误删正在用的图。
    """
    import html

    os.makedirs(directory, exist_ok=True)
    names = [name for name in os.listdir(directory)
             if name.startswith("run_") and name.endswith(".md")]
    paths = sorted((os.path.join(directory, name) for name in names),
                   key=os.path.getmtime, reverse=True)
    for old in paths[keep:]:
        try:
            os.remove(old)
        except OSError:
            pass
    paths = paths[:keep]

    rows = []
    referenced_shots = set()
    for path in paths:
        result = ""
        steps = failed = seconds = ""
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        referenced_shots.update(re.findall(r"\((shots/[^)]+)\)", text))
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("- 结果："):
                result = line[len("- 结果："):].strip()
            elif line.startswith("- 步骤数："):
                match = re.search(r"步骤数：(\d+)，失败：(\d+)", line)
                if match:
                    steps, failed = match.group(1), match.group(2)
            elif line.startswith("- 总耗时："):
                seconds = line[len("- 总耗时："):].replace(" 秒", "").strip()
        label = {"finished": "成功", "error": "失败",
                 "stopped": "已停止"}.get(result, result or "未知")
        css = {"finished": "ok", "error": "bad",
               "stopped": "warn"}.get(result, "")
        stamp = time.strftime("%Y-%m-%d %H:%M",
                              time.localtime(os.path.getmtime(path)))
        rows.append(
            "<tr>"
            f"<td>{html.escape(stamp)}</td>"
            f"<td><span class=\"pill {css}\">{html.escape(label)}</span></td>"
            f"<td class=\"num\">{html.escape(steps)}</td>"
            f"<td class=\"num\">{html.escape(failed)}</td>"
            f"<td class=\"num\">{html.escape(seconds)}</td>"
            f"<td><a href=\"{html.escape(os.path.basename(path))}\">"
            "查看报告</a></td>"
            "</tr>")

    # 清理没人引用的旧截图
    shots_dir = os.path.join(directory, "shots")
    if os.path.isdir(shots_dir):
        cutoff = time.time() - 7 * 86400
        for name in os.listdir(shots_dir):
            rel = f"shots/{name}"
            full = os.path.join(shots_dir, name)
            try:
                if rel not in referenced_shots and os.path.getmtime(full) < cutoff:
                    os.remove(full)
            except OSError:
                pass

    body = "\n".join(rows) if rows else (
        "<tr><td colspan=\"6\" class=\"empty\">还没有运行报告</td></tr>")
    # 报告页是产品界面的一部分：色值、圆角、字号一律取自 rpa/theme.py 的
    # 设计令牌（浅色暖白板），与桌面端保持同一套视觉规范，不再另维护配色。
    p = theme.LIGHT_PALETTE
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MeowGo 运行报告</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='16' fill='{theme.INK}'/%3E%3Ccircle cx='43' cy='21' r='8' fill='{p['ACCENT']}'/%3E%3C/svg%3E">
<style>
  body {{ margin: 0; padding: 40px 20px; background: {p['BG']};
         color: {p['TEXT']};
         font: 14px/1.6 "{theme.FONT_FAMILY}", system-ui, sans-serif; }}
  main {{ max-width: 920px; margin: 0 auto; }}
  .head {{ display: flex; align-items: baseline; gap: 8px; }}
  h1 {{ margin: 0; font-size: 22px; font-weight: 700; }}
  .dot {{ color: {p['ACCENT']}; font-weight: 800; }}
  p.muted {{ margin: 2px 0 20px; color: {p['MUTED']}; font-size: 12px; }}
  .table-wrap {{ background: {p['CARD']}; border: 1px solid {p['BORDER']};
                 border-radius: {theme.RADIUS_M}px;
                 box-shadow: 0 1px 2px rgba(8, 8, 6, 0.04);
                 overflow: hidden; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ padding: 10px 14px; text-align: left; white-space: nowrap; }}
  thead th {{ background: {p['HEADING_BG']}; color: {p['HEADING_FG']};
              font-weight: 600; font-size: 12px; }}
  tbody tr:nth-child(even) {{ background: {p['ZEBRA']}; }}
  tbody tr:hover {{ background: {p['ACCENT_MIST']}; }}
  td.num {{ font-variant-numeric: tabular-nums; }}
  a {{ color: {p['ACCENT_STRONG']}; text-decoration: none; font-weight: 500; }}
  a:hover {{ text-decoration: underline; }}
  .pill {{ display: inline-block; padding: 1px 10px; border-radius: 999px;
           font-size: 12px; background: {p['HEADING_BG']};
           color: {p['MUTED']}; }}
  .pill.ok {{ background: {p['MARK_DONE']}; color: {p['SUCCESS_TEXT']}; }}
  .pill.bad {{ background: {p['MARK_FAILED']}; color: {p['DANGER_TEXT']}; }}
  .pill.warn {{ background: {p['MARK_RUNNING']}; color: {p['WARNING_TEXT']}; }}
  .empty {{ color: {p['TEXT_WEAK']}; text-align: center; padding: 40px 0; }}
</style>
</head>
<body>
<main>
  <div class="head"><h1>MeowGo</h1><span class="dot">·</span></div>
  <p class="muted">运行报告 · 最近 {keep} 次 · 自动刷新 · 点击「查看报告」打开详情</p>
  <div class="table-wrap">
    <table>
      <thead><tr><th>时间</th><th>结果</th><th>步骤</th><th>失败</th>
      <th>耗时(秒)</th><th>报告</th></tr></thead>
      <tbody>
{body}
      </tbody>
    </table>
  </div>
</main>
</body>
</html>
"""
    path = os.path.join(directory, "index.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(document)
    return path
