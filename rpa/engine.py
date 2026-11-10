# -*- coding: utf-8 -*-
"""RPA 核心引擎：执行组、步骤模型、Excel 读写、校验与执行。

与界面完全解耦，方便单独测试或命令行调用。
"""
from __future__ import annotations

import ast
import csv
import json
import operator
import os
import random
import re
import shutil
import signal
import subprocess
import threading
import time
from collections import namedtuple
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from . import assets, platform_support, theme

try:  # 窗口操作只在 Windows 上有意义；其它平台导入 engine 不应失败
    import ctypes
    import ctypes.wintypes as wt
except ImportError:  # pragma: no cover - 非 Windows
    ctypes = None  # type: ignore[assignment]
    wt = None  # type: ignore[assignment]

# macOS / Linux 也有 ctypes 与 ctypes.wintypes，但没有 WINFUNCTYPE/windll，
# 统一按「不可用」处理，让窗口相关功能走各自的平台判断分支。
if ctypes is not None and not hasattr(ctypes, "WINFUNCTYPE"):
    ctypes = None  # type: ignore[assignment]
    wt = None  # type: ignore[assignment]

if ctypes is not None:  # pragma: no cover - 非 Windows
    _WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    _SW_SHOW = 5
    _SW_RESTORE = 1
    _VK_MENU = 0x12
    _KEYEVENTF_KEYUP = 0x0002
    _WM_CLOSE = 0x0010

_USER32_READY = False


def _win_user32():
    """返回声明过原型的 user32。

    HWND 在 64 位进程中是 8 字节；不声明 argtypes 时 ctypes 会按 C int
    截断，窗口操作会随机失败或把消息发到无效句柄。所有窗口相关调用统一
    走这里，避免每个调用点重复设置。
    """
    global _USER32_READY
    assert ctypes is not None and wt is not None
    user32 = ctypes.windll.user32
    if _USER32_READY:  # pragma: no branch - 只有首次调用做声明
        return user32
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.OpenInputDesktop.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    user32.OpenInputDesktop.restype = wt.HANDLE
    user32.CloseDesktop.argtypes = [wt.HANDLE]
    user32.CloseDesktop.restype = wt.BOOL
    user32.EnumWindows.argtypes = [_WNDENUMPROC, wt.LPARAM]
    user32.EnumWindows.restype = wt.BOOL
    user32.IsWindowVisible.argtypes = [wt.HWND]
    user32.IsWindowVisible.restype = wt.BOOL
    user32.GetWindowTextLengthW.argtypes = [wt.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.IsIconic.argtypes = [wt.HWND]
    user32.IsIconic.restype = wt.BOOL
    user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wt.BOOL
    user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD,
                                   ctypes.c_size_t]
    user32.keybd_event.restype = None
    user32.SetForegroundWindow.argtypes = [wt.HWND]
    user32.SetForegroundWindow.restype = wt.BOOL
    user32.BringWindowToTop.argtypes = [wt.HWND]
    user32.BringWindowToTop.restype = wt.BOOL
    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.PostMessageW.restype = wt.BOOL
    _USER32_READY = True
    return user32

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
    # ---- 文字 / 控件 / 网络 ----
    "click_text": ("点击文字", "屏幕上的文字，如 提交；可加 @x,y,宽,高",
                   False, ""),
    "wait_text": ("等待文字出现", "要等的文字；可加 @x,y,宽,高", False, ""),
    "assert_text": ("断言文字出现", "必须出现的文字；可加 @x,y,宽,高",
                    False, ""),
    "ui_click": ("点击控件", "控件名称，如 确定；可写 名称|控件类型",
                 False, ""),
    "if_var": ("条件：变量比较则执行", "变量名 运算符 值|组名，如 次数<5|重试",
               False, ""),
    "http": ("HTTP 请求", "GET https://…|变量名；POST 可再加 |请求体",
             False, ""),
    # ---- 循环与流程控制 ----
    "for_each": ("循环：按范围", "起始,结束[,步长]|组名，如 1,5|处理",
                 True, "轮次上限"),
    "while_var": ("循环：变量条件", "变量名 运算符 值|组名，如 次数<5|重试",
                  True, "最大轮次"),
    "break": ("跳出循环", "留空", False, ""),
    "continue": ("跳到下一轮", "留空", False, ""),
    # ---- 文件 / 数据 / 文本 ----
    "file": ("文件操作", "copy:源|目标 / move:源|目标 / delete:路径 / "
             "mkdir:目录 / wait:路径|秒", False, ""),
    "json": ("读取 JSON", "变量名|字段路径，如 data|user.name", False, ""),
    "regex": ("正则提取", "变量名|表达式[|分组号]，如 结果|\\d+", False, ""),
    "date": ("日期格式化", "变量名|格式，如 今天|%Y-%m-%d", False, ""),
    "clipboard": ("读取剪贴板到变量", "变量名，如 文本", False, ""),
    "random_wait": ("随机等待", "最小秒,最大秒，如 1,3", False, ""),
    "section": ("小节标题", "标题文字，如 登录步骤", False, ""),
    # ---- 等待画面 ----
    "wait_image": ("等待图片出现", "要等的图片，如 加载完成.png", False, ""),
    "wait_gone": ("等待图片消失", "要等的图片，如 加载遮罩.png", False, ""),
    # ---- 屏幕取字 ----
    "ocr_read": ("读取屏幕文字", "变量名，如 单号；可用「识别范围」限定区域",
                 False, ""),
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
    "click_text": 21.0,
    "wait_text": 22.0,
    "assert_text": 23.0,
    "ui_click": 24.0,
    "if_var": 25.0,
    "http": 26.0,
    "for_each": 27.0,
    "while_var": 28.0,
    "break": 29.0,
    "continue": 30.0,
    "file": 31.0,
    "json": 32.0,
    "regex": 33.0,
    "date": 34.0,
    "clipboard": 35.0,
    "random_wait": 36.0,
    "section": 37.0,
    "wait_image": 38.0,
    "wait_gone": 39.0,
    "ocr_read": 40.0,
}
CODE_TO_TYPE = {v: k for k, v in TYPE_TO_CODE.items()}

IMAGE_TYPES = {"click", "dblclick", "rightclick"}
# 只「看」不点：等图片出现 / 消失（不需要点击，但同样用模板匹配）
WAIT_IMAGE_TYPES = {"wait_image", "wait_gone"}
# 需要 OCR / UIA 的步骤类型
TEXT_STEP_TYPES = {"click_text", "wait_text", "assert_text"}
# 只做文字识别、不点也不等（把屏幕文字读进变量）
OCR_READ_TYPES = {"ocr_read"}
UIA_STEP_TYPES = {"ui_click"}
# 可以填「识别范围」的步骤类型（图片 + 文字）
REGION_STEP_TYPES = (IMAGE_TYPES | TEXT_STEP_TYPES | WAIT_IMAGE_TYPES
                     | OCR_READ_TYPES
                     | {"if_image", "if_not_image", "if_else", "drag"})
REPEAT_TYPES = {"click", "dblclick", "rightclick", "hotkey", "call",
                "if_image", "if_not_image", "if_else", "if_window",
                "click_xy", "for_each", "while_var"}
# 循环步骤（内部可以执行 break / continue）
LOOP_TYPES = {"for_each", "while_var"}
# 界面上「截图替换」等图片相关操作适用的步骤类型
IMAGE_STEP_TYPES = IMAGE_TYPES | WAIT_IMAGE_TYPES | {
    "if_image", "if_not_image", "if_else", "drag"}
# macOS 需要「辅助功能」：模拟键鼠 / 读取窗口与控件
MAC_ACCESSIBILITY_TYPES = (IMAGE_TYPES | TEXT_STEP_TYPES | UIA_STEP_TYPES
                           | {"drag", "scroll", "input", "hotkey",
                              "click_xy", "move_xy", "window", "if_window"})
# macOS 需要「屏幕录制」：截图 / 找图 / 文字识别
MAC_SCREEN_TYPES = IMAGE_STEP_TYPES | TEXT_STEP_TYPES | OCR_READ_TYPES


Point = namedtuple("Point", "x y")

# 持久变量的占位符前缀：{持久:键} 读取，设置变量时写「持久:键=值」
PERSIST_PREFIX = "持久:"

# 条件类步骤（内容里的 | 后面是执行组）
CONDITION_TYPES = {"if_image", "if_not_image", "if_else", "if_window"}
# 支持「最多等 N 秒」的步骤类型（文件 wait 也用步骤 timeout 作为兜底）
TIMEOUT_STEP_TYPES = (IMAGE_TYPES | WAIT_IMAGE_TYPES | {"drag", "cmd", "file"}
                      | CONDITION_TYPES | TEXT_STEP_TYPES | UIA_STEP_TYPES)
# 界面上显示「失败后处理」的步骤类型（条件类不需要）
FAIL_ACTION_STEP_TYPES = (IMAGE_TYPES | WAIT_IMAGE_TYPES | {"drag", "cmd", "file"}
                          | TEXT_STEP_TYPES | UIA_STEP_TYPES)

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
        name = text.split("|", 1)[0].strip()
        return [name] if name else []
    if step.type in LOOP_TYPES:
        parts = [p.strip() for p in text.split("|")]
        return [parts[1]] if len(parts) > 1 and parts[1] else []
    if step.type in ("if_image", "if_not_image", "if_window"):
        parts = [p.strip() for p in text.split("|")]
        return [parts[1]] if len(parts) > 1 and parts[1] else []
    if step.type == "if_else":
        parts = [p.strip() for p in text.split("|")]
        return [p for p in parts[1:3] if p]
    if step.type == "if_var":
        parts = [p.strip() for p in text.split("|")]
        return [parts[1]] if len(parts) > 1 and parts[1] else []
    return []

# 图片步骤超时后的处理方式（timeout > 0 时生效）
# 除内置两项外，还支持 goto:<组名>：失败后跳到清理 / 重试组
FAIL_ACTIONS = ("skip", "stop")


def parse_on_fail(value) -> tuple[str, str]:
    """把 on_fail 解析成 (动作, 目标组)；无法识别时返回空动作。"""
    text = str(value or "").strip()
    if not text:
        return "skip", ""
    if text in FAIL_ACTIONS:
        return text, ""
    if text.startswith("goto:"):
        target = text[5:].strip()
        return ("goto", target) if target else ("", "")
    return "", ""


def valid_on_fail(value) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    action, _target = parse_on_fail(text)
    return action in FAIL_ACTIONS or action == "goto"
# 文本输入方式：paste = 剪贴板粘贴（快，支持中文）；type = 逐字输入（兼容个别应用）
INPUT_MODES = ("paste", "type")

DEFAULT_CONFIDENCE = 0.9
POLL_INTERVAL = 0.1
# 同一轮轮询里复用一帧截图：略小于 POLL_INTERVAL，既省 CPU 又保证
# 下一次轮询一定拿到新画面
SCREEN_CACHE_TTL = 0.08
# 锁屏 / 安全桌面检查的节流间隔
SESSION_CHECK_INTERVAL = 1.0


def _mac_display_bounds() -> list[tuple[float, float, float, float]]:
    """活跃显示器的逻辑矩形；取不到返回空列表。"""
    try:
        import Quartz

        _err, displays, count = Quartz.CGGetActiveDisplayList(16, None, None)
        rects: list[tuple[float, float, float, float]] = []
        for display in list(displays)[:int(count)]:
            box = Quartz.CGDisplayBounds(display)
            rects.append((float(box.origin.x), float(box.origin.y),
                          float(box.size.width), float(box.size.height)))
        return rects
    except Exception:  # noqa: BLE001 - 取不到就退回主屏逻辑尺寸
        return []


def virtual_screen_rect() -> tuple[int, int, int, int]:
    """整个虚拟桌面的 (left, top, width, height)；副屏在左侧时 left 为负。"""
    if platform_support.IS_MACOS:
        rects = _mac_display_bounds()
        if rects:
            left = min(rect[0] for rect in rects)
            top = min(rect[1] for rect in rects)
            right = max(rect[0] + rect[2] for rect in rects)
            bottom = max(rect[1] + rect[3] for rect in rects)
            if right > left and bottom > top:
                return (int(left), int(top), int(right - left),
                        int(bottom - top))
    if os.name == "nt":
        try:
            user32 = _win_user32()
            left = user32.GetSystemMetrics(76)      # SM_XVIRTUALSCREEN
            top = user32.GetSystemMetrics(77)       # SM_YVIRTUALSCREEN
            width = user32.GetSystemMetrics(78)     # SM_CXVIRTUALSCREEN
            height = user32.GetSystemMetrics(79)    # SM_CYVIRTUALSCREEN
            if width > 0 and height > 0:
                return left, top, width, height
        except Exception:  # noqa: BLE001
            pass
    try:
        import pyautogui

        width, height = pyautogui.size()
        return 0, 0, int(width), int(height)
    except Exception:  # noqa: BLE001
        return 0, 0, 1920, 1080


def session_locked() -> bool:
    """当前输入桌面是否不可用（锁屏 / UAC 安全桌面 / RDP 断开）。

    锁屏时截图是全黑的，继续找图只会白等——调用方应暂停并提示。
    macOS 上用 CGSessionCopyCurrentDictionary 的 ScreenIsLocked 标记；
    取不到状态时一律按未锁屏处理，避免误暂停。
    """
    if platform_support.IS_MACOS:
        try:
            import Quartz

            info = Quartz.CGSessionCopyCurrentDictionary() or {}
            return bool(info.get("CGSSessionScreenIsLocked"))
        except Exception:  # noqa: BLE001
            return False
    if os.name != "nt":
        return False
    try:
        user32 = _win_user32()
        desk = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
        if not desk:
            return True
        user32.CloseDesktop(desk)
        return False
    except Exception:  # noqa: BLE001 - 取不到状态时按可用处理
        return False


def display_scale() -> float:
    """截图像素与屏幕逻辑坐标的比例（Retina 屏为 2，普通屏为 1）。"""
    if not platform_support.IS_MACOS:
        return 1.0
    try:
        import Quartz

        display = Quartz.CGMainDisplayID()
        points_w = float(Quartz.CGDisplayBounds(display).size.width)
        pixels_w = float(Quartz.CGDisplayPixelsWide(display))
        if points_w > 0 and pixels_w > 0:
            return max(1.0, pixels_w / points_w)
    except Exception:  # noqa: BLE001 - 取不到就按普通屏处理
        pass
    return 1.0


def _split_capture(result):
    """兼容 capture_screen 的 2 元组（旧测试替身）与 3 元组（带缩放）。"""
    try:
        frame, origin, scale = result
    except (TypeError, ValueError):
        frame, origin = result
        scale = 1.0
    return frame, origin, float(scale)


def _search_bounds(region, origin, scale, shape) -> tuple[int, int, int, int]:
    """把逻辑坐标的识别区域换算成截图里的像素范围。"""
    rx, ry, rw, rh = region
    x0 = max(0, int(round((rx - origin[0]) * scale)))
    y0 = max(0, int(round((ry - origin[1]) * scale)))
    x1 = min(shape[1], x0 + int(round(rw * scale)))
    y1 = min(shape[0], y0 + int(round(rh * scale)))
    return x0, y0, x1, y1


def _match_point(base_x: float, base_y: float, scale: float,
                 box: tuple[float, int, int, int, int]) -> "Point":
    """把截图里的命中框中心换算回屏幕逻辑坐标。"""
    _score, x, y, width, height = box
    return Point(base_x + (x + width / 2) / scale,
                 base_y + (y + height / 2) / scale)


def capture_screen():
    """采集一帧整个虚拟桌面，返回 (BGR ndarray, (left, top), scale)。

    副屏在主屏左侧时 left/top 为负；scale 是截图相对逻辑坐标的倍数
    （macOS Retina 下为 2，其它情况为 1），供找图结果换算点击坐标。
    """
    import numpy as np

    try:
        from PIL import ImageGrab

        image = ImageGrab.grab(all_screens=True)
        frame = np.asarray(image)
        if frame.ndim == 3 and frame.shape[2] >= 3:
            frame = frame[:, :, :3][:, :, ::-1]      # RGB -> BGR
        origin = virtual_screen_rect()[:2]
    except Exception:  # noqa: BLE001 - ImageGrab 不可用时退回 pyautogui
        import pyautogui

        frame = np.asarray(pyautogui.screenshot())[:, :, ::-1]
        origin = (0, 0)
    return frame, origin, display_scale()


MAX_CALL_DEPTH = 20
# 循环运行没有自然的结束点；步数统计只保留最近一段，避免内存无限增长
MAX_LOOP_STATS = 5000
KEEP_LOOP_STATS = 2000
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
    的中心点，按从上到下、从左到右排序。与执行引擎一样支持 DPI /
    屏幕缩放变化：原尺寸找不到时会自动尝试常见缩放比例。
    """
    result = []
    frame = None
    origin = (0, 0)
    for target in parse_image_targets(text):
        path = assets.resolve_image(target.name, base_dir or None)
        item = {"name": target.name, "missing": False, "points": []}
        if not os.path.exists(path):
            item["missing"] = True
            result.append(item)
            continue
        if not opencv_available():
            raise StepError(OPENCV_HINT)
        import cv2
        import numpy as np

        if frame is None:
            frame, origin, scale = _split_capture(capture_screen())
            if frame.size == 0:
                result.append(item)
                continue
        try:
            data = np.fromfile(path, dtype=np.uint8)
            template = (cv2.imdecode(data, cv2.IMREAD_COLOR)
                        if data.size else None)
        except (OSError, ValueError):
            template = None
        if template is None:
            template = cv2.imread(path, cv2.IMREAD_COLOR)
        if template is None:
            result.append(item)
            continue

        use_region = target.region or region
        if use_region:
            x0, y0, x1, y1 = _search_bounds(
                use_region, origin, scale, frame.shape)
            if x1 <= x0 or y1 <= y0:
                result.append(item)
                continue
            search = frame[y0:y1, x0:x1]
            base_x = origin[0] + x0 / scale
            base_y = origin[1] + y0 / scale
        else:
            search = frame
            base_x, base_y = origin

        if grayscale:
            search = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)
            template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        boxes = _match_at_scale(search, template, confidence, 1.0)
        if not boxes:
            scaled_confidence = _scale_confidence(confidence)
            factor = _discover_scale(search, template, scaled_confidence)
            if factor is not None:
                boxes = _match_at_scale(search, template,
                                        scaled_confidence, factor)
        item["points"] = [
            (point.x, point.y)
            for point in (_match_point(base_x, base_y, scale, box)
                          for box in boxes)]
        result.append(item)
    return result


def _box_iou(box1: tuple[int, int, int, int],
             box2: tuple[int, int, int, int]) -> float:
    """两个 (x, y, w, h) 的重叠率，用于合并同一目标的相邻命中。"""
    x1, y1, w1, h1 = box1
    x2, y2, w2, h2 = box2
    inter_w = max(0, min(x1 + w1, x2 + w2) - max(x1, x2))
    inter_h = max(0, min(y1 + h1, y2 + h2) - max(y1, y2))
    inter = inter_w * inter_h
    if inter <= 0:
        return 0.0
    union = w1 * h1 + w2 * h2 - inter
    return inter / union if union else 0.0


# DPI / 屏幕缩放变化时，模板需要按比例缩放再匹配。这里覆盖 Windows
# 常见缩放档位（100% / 125% / 150% / 175% / 200%）两两之间的比例；
# 先试最常用的，命中后把比例记到 _scale_hints，后续轮询只试这个比例。
MULTI_SCALE_FACTORS = (
    1.25, 0.8, 1.5, 2 / 3, 1.2, 5 / 6, 4 / 3, 0.75,
    1.75, 4 / 7, 2.0, 0.5, 1.6, 0.625, 7 / 6, 6 / 7,
)
# 全比例扫描时把屏幕缩到这个宽度以内，降低 matchTemplate 的开销；
# 找到候选后再回原图局部验证，精度不受影响。
COARSE_MATCH_MAX = 960.0
SCALE_SWEEP_INTERVAL = 0.8       # 同一张图两次全比例扫描的最小间隔（秒）


def _scale_confidence(confidence: float) -> float:
    """缩放匹配会经过重采样，分数通常比原尺寸略低。

    给 0.1 的容差（不低于 0.7），避免明明能认出来却因为 0.90 vs 0.89
    的差距一直等到超时；原尺寸匹配仍然使用用户设置的严格阈值。
    """
    return max(0.7, float(confidence) - 0.1)


def _resize_template(template: Any, factor: float) -> Any:
    """按比例缩放模板；1.0 直接返回原图，避免无谓拷贝。"""
    if abs(factor - 1.0) < 1e-6:
        return template
    import cv2

    width = max(1, int(round(template.shape[1] * factor)))
    height = max(1, int(round(template.shape[0] * factor)))
    interpolation = cv2.INTER_AREA if factor < 1 else cv2.INTER_LINEAR
    return cv2.resize(template, (width, height), interpolation=interpolation)


def _match_template_boxes(
        search: Any, template: Any,
        confidence: float) -> list[tuple[float, int, int, int, int]]:
    """单尺度匹配，返回 (分数, x, y, w, h) 列表（未去重）。"""
    import cv2
    import numpy as np

    height, width = template.shape[:2]
    if (height <= 0 or width <= 0
            or height > search.shape[0] or width > search.shape[1]):
        return []
    result = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
    result = np.nan_to_num(result, nan=-1.0, posinf=-1.0, neginf=-1.0)
    ys, xs = np.where(result >= float(confidence))
    if len(xs) == 0:
        return []
    order = np.argsort(-result[ys, xs])[:400]
    return [(float(result[int(ys[index]), int(xs[index])]),
             int(xs[index]), int(ys[index]), width, height)
            for index in order]


def _dedupe_boxes(
        boxes: list[tuple[float, int, int, int, int]]
        ) -> list[tuple[float, int, int, int, int]]:
    """同一目标会产出一片相邻命中：按分数优先保留，再按位置排序。"""
    boxes.sort(key=lambda item: item[0], reverse=True)
    kept: list[tuple[float, int, int, int, int]] = []
    for box in boxes[:400]:
        _score, x, y, width, height = box
        if any(_box_iou((x, y, width, height),
                        (kx, ky, kw, kh)) > 0.3
               for _s, kx, ky, kw, kh in kept):
            continue
        kept.append(box)
    kept.sort(key=lambda item: (item[2], item[1]))
    return kept


def _match_at_scale(search: Any, template: Any, confidence: float,
                    factor: float) -> list[tuple[float, int, int, int, int]]:
    scaled = _resize_template(template, factor)
    return _dedupe_boxes(_match_template_boxes(search, scaled, confidence))


def _discover_scale(search: Any, template: Any, confidence: float,
                    stop_event: Optional[threading.Event] = None
                    ) -> Optional[float]:
    """在缩小的搜索图上快速试各种比例，再回原图局部验证。

    返回第一个通过原图验证的比例；都不匹配返回 None。这样即使屏幕是
    4K，也只需要在小图上跑十几次 matchTemplate，而不是每次都在原图上跑。
    """
    import cv2
    import numpy as np

    longest = max(search.shape[0], search.shape[1])
    if longest <= 0:
        return None
    coarse_scale = min(1.0, COARSE_MATCH_MAX / longest)
    if coarse_scale < 1.0:
        coarse = cv2.resize(search, None, fx=coarse_scale, fy=coarse_scale,
                            interpolation=cv2.INTER_AREA)
    else:
        coarse = search

    for factor in MULTI_SCALE_FACTORS:
        if stop_event is not None and stop_event.is_set():
            return None
        full = _resize_template(template, factor)
        full_h, full_w = full.shape[:2]
        cw = max(1, int(round(full_w * coarse_scale)))
        ch = max(1, int(round(full_h * coarse_scale)))
        # 太小 / 太大都没有可靠匹配的意义
        if (cw < 6 or ch < 6
                or cw > coarse.shape[1] or ch > coarse.shape[0]):
            continue
        ctemplate = cv2.resize(
            full, (cw, ch),
            interpolation=(cv2.INTER_AREA if coarse_scale < 1
                           else cv2.INTER_LINEAR))
        result = cv2.matchTemplate(coarse, ctemplate, cv2.TM_CCOEFF_NORMED)
        result = np.nan_to_num(result, nan=-1.0, posinf=-1.0, neginf=-1.0)
        ys, xs = np.where(result >= float(confidence))
        if len(xs) == 0:
            continue
        order = np.argsort(-result[ys, xs])[:3]
        for index in order:
            cx, cy = int(xs[index]), int(ys[index])
            fx = int(round(cx / coarse_scale))
            fy = int(round(cy / coarse_scale))
            # 留一点边，抵消缩图取整带来的位置偏差
            pad = max(8, int(max(full_w, full_h) * 0.15),
                      int(2 / coarse_scale) + 2)
            x0 = max(0, fx - pad)
            y0 = max(0, fy - pad)
            x1 = min(search.shape[1], fx + full_w + pad)
            y1 = min(search.shape[0], fy + full_h + pad)
            if x1 <= x0 or y1 <= y0:
                continue
            if _match_template_boxes(search[y0:y1, x0:x1], full, confidence):
                return factor
    return None


class LoopBreak(Exception):
    """break 步骤：结束最近一层循环。"""


class LoopContinue(Exception):
    """continue 步骤：跳到最近一层循环的下一轮。"""


class StepError(ValueError):
    """步骤配置错误。"""


class Stopped(StepError):
    """用户主动停止或触发急停：不是配置错误，按「已停止」收尾。"""


def _is_failsafe(exc: BaseException) -> bool:
    """pyautogui 的急停异常（鼠标移到屏幕角落时抛出）。

    不在模块顶层 import pyautogui（导入要十几秒），按类名判断即可。
    """
    return type(exc).__name__ == "FailSafeException"


def _valid_xy(text: str) -> bool:
    """"500,300" 或 "500,300,right" 是否合法。

    允许负数：副屏在主屏左侧 / 上方时，桌面坐标本来就是负的。
    """
    parts = [p.strip() for p in (text or "").split(",")]
    if len(parts) not in (2, 3):
        return False
    try:
        float(parts[0]), float(parts[1])
    except ValueError:
        return False
    if len(parts) == 3 and parts[2] not in ("left", "right", "middle"):
        return False
    return True


def _parse_xy(text: str) -> tuple[float, float, str]:
    """"500,300" -> (500.0, 300.0, "left")；可带第三段指定按键。"""
    parts = [p.strip() for p in text.split(",")]
    x, y = float(parts[0]), float(parts[1])
    button = parts[2] if len(parts) > 2 and parts[2] in (
        "left", "right", "middle") else "left"
    return x, y, button


_VAR_COND_RE = re.compile(
    r"^\s*([^{}\s|]+)\s*(==|!=|>=|<=|>|<|contains|notcontains)\s*(.*?)\s*$",
    re.IGNORECASE)
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _parse_var_condition(text: str) -> tuple[str, str, str]:
    """解析「变量名 运算符 值」，返回 (变量名, 运算符, 目标值)。"""
    match = _VAR_COND_RE.match(text or "")
    if not match:
        raise StepError("条件要写成 变量名 运算符 值"
                        "（运算符：== != > < >= <= contains notcontains）")
    return match.group(1), match.group(2).lower(), match.group(3)


def _compare_values(actual: str, operator: str, expected: str) -> bool:
    if operator in ("contains", "notcontains"):
        found = str(expected).casefold() in str(actual).casefold()
        return found if operator == "contains" else not found
    try:
        left, right = float(actual), float(expected)
    except (TypeError, ValueError):
        text_left, text_right = str(actual), str(expected)
        if operator == "==":
            return text_left == text_right
        if operator == "!=":
            return text_left != text_right
        return False
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    if operator == ">":
        return left > right
    if operator == "<":
        return left < right
    if operator == ">=":
        return left >= right
    return left <= right


def _parse_http_spec(text: str) -> tuple[str, str, str, str, dict[str, str]]:
    """解析「方法 URL|变量名[|请求体[|键: 值;键2: 值2]]」。

    返回 (方法, 地址, 变量名, 请求体, 请求头)。请求头可选，用英文分号分隔，
    例如 ``X-Token: abc;Accept: application/json``。
    """
    spec, sep, rest = (text or "").partition("|")
    if not sep:
        raise StepError("要写成 方法 URL|变量名，如 "
                        "GET https://example.com|响应")
    parts = rest.split("|", 2)
    var = parts[0].strip()
    body = parts[1] if len(parts) > 1 else ""
    header_text = parts[2] if len(parts) > 2 else ""
    if not var:
        raise StepError("变量名不能为空")
    method, _, url = spec.strip().partition(" ")
    method = method.upper()
    if method not in HTTP_METHODS:
        url = spec.strip()
        method = "GET"
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        raise StepError("只支持 http:// 或 https:// 地址")
    headers: dict[str, str] = {}
    for item in header_text.split(";"):
        item = item.strip()
        if not item:
            continue
        key, colon, value = item.partition(":")
        if not colon or not key.strip():
            raise StepError(f"请求头要写成 键: 值，用 ; 分隔：{item}")
        headers[key.strip()] = value.strip()
    return method, url, var, body, headers


def parse_text_target(text: str, region_text: str = ""):
    """解析文字步骤的「文字@x,y,宽,高」，返回 (文字, 区域或 None)。"""
    name = (text or "").strip()
    region = _parse_region(region_text)
    match = _REGION_RE.search(name)
    if match:
        x, y, width, height = (int(part) for part in match.groups())
        region = (x, y, width, height)
        name = name[:match.start()].strip()
    return name, region


def _read_temp_text(handle) -> str:
    """读取子进程输出临时文件。Windows 中文环境下 cmd 输出多为 GBK。"""
    import locale

    try:
        handle.flush()
        handle.seek(0)
        data = handle.read()
    finally:
        handle.close()
    if isinstance(data, bytes):
        data = data.decode(locale.getpreferredencoding(False), "replace")
    return data.strip()


def _read_text_file(path: str) -> str:
    """读文本文件：优先 UTF-8（含 BOM），失败再用 GB18030 兜底。

    Windows 上不少导出文件是 GBK 编码，直接按 UTF-8 读会抛异常；
    GB18030 是 GBK 的超集，能覆盖绝大多数中文旧文件。
    """
    last_error: Optional[UnicodeDecodeError] = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with open(path, "r", encoding=encoding) as fh:
                return fh.read()
        except UnicodeDecodeError as exc:
            last_error = exc
    raise StepError(f"文本编码无法识别（已尝试 UTF-8 / GB18030）：{path}"
                    ) from last_error


def _command_error_hint(text: str) -> str:
    """系统命令失败时补一句平台相关的说明（目前只处理 macOS 上的 start）。"""
    if not platform_support.IS_MACOS:
        return ""
    if re.match(r"\s*start(\s|$)", text or ""):
        return ('\n这是 Windows 的 start 命令；macOS 请改用 '
                'open -a "Google Chrome" 或 open "https://example.com"。')
    return ""


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
    return _plain_number(result)


def _plain_number(value) -> str:
    """把 1.0 显示成 1，把 1.234567 压到 6 位小数。"""
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


_JSON_TOKEN_RE = re.compile(r"[^.\[\]]+|\[(\d+)\]")


def _json_path(data, path: str):
    """按 user.name / items[0].id 这样的路径取 JSON 字段。"""
    current = data
    for token in _JSON_TOKEN_RE.finditer(str(path or "")):
        index = token.group(1)
        if index is not None:
            current = current[int(index)]
        else:
            current = current[token.group(0)]
    return current


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
    enabled：False 时运行会跳过这一步（配置校验、环境检查也一并忽略），
            方便临时停用某一步而不用删除。旧配置 / Excel 里没有该字段，
            默认按启用处理。
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
    enabled: bool = True

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
        if self.type in IMAGE_TYPES or self.type in WAIT_IMAGE_TYPES:
            if not text:
                errors.append(f"{where}：需要填写图片文件名")
            elif self.type in WAIT_IMAGE_TYPES and not parse_image_targets(text):
                errors.append(f"{where}：要写成图片文件名，如 加载完成.png")
        elif self.type in ("input", "hotkey", "time", "cmd", "call", "copy",
                           "set", "click_text", "wait_text", "assert_text",
                           "ui_click", "if_var", "http", "for_each",
                           "while_var", "file", "json", "regex", "date",
                           "clipboard", "random_wait", "section", "ocr_read"):
            if not text:
                errors.append(f"{where}：内容不能为空")
        elif self.type in ("break", "continue") and text:
            errors.append(f"{where}：跳出循环/跳到下一轮不需要填写内容")
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
            # Windows 沿用旧版限制（顶层数字键不识别）；macOS 可正常使用
            if (not platform_support.IS_MACOS
                    and any(k.isdigit() for k in keys)):
                errors.append(f"{where}：单个数字键无法识别，请改用 num0 这类写法")

        # 图片类、拖拽、条件、命令、文字/控件、文件等待都可以「最多等 N 秒」
        if self.type in TIMEOUT_STEP_TYPES and self.timeout:
            try:
                if float(self.timeout) < 0:
                    errors.append(f"{where}：等待超时不能为负数")
            except (TypeError, ValueError):
                errors.append(f"{where}：等待超时必须是数字")
        if self.on_fail and not valid_on_fail(self.on_fail):
            errors.append(f"{where}：失败后处理只能是 skip、stop 或 goto:组名")

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
            elif name.strip() == PERSIST_PREFIX.rstrip(":"):
                errors.append(f"{where}：持久变量要写成 持久:键=值")
            elif (name.strip().startswith(PERSIST_PREFIX)
                  and not name.strip()[len(PERSIST_PREFIX):].strip()):
                errors.append(f"{where}：持久变量要写成 持久:键=值")
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
        elif self.type == "if_var" and text:
            cond, sep, target = text.partition("|")
            if not sep or not target.strip():
                errors.append(f"{where}：变量条件要写成 "
                              "变量名 运算符 值|组名")
            else:
                try:
                    _parse_var_condition(cond)
                except StepError as exc:
                    errors.append(f"{where}：{exc}")
        elif self.type == "http" and text:
            try:
                _parse_http_spec(text)
            except StepError as exc:
                errors.append(f"{where}：{exc}")
        elif self.type == "ui_click" and text:
            if not text.split("|", 1)[0].strip():
                errors.append(f"{where}：控件名称不能为空")
        elif self.type in LOOP_TYPES and text:
            condition, sep, target = text.partition("|")
            if not sep or not target.strip():
                errors.append(f"{where}：循环要写成 条件|组名")
            elif self.type == "for_each":
                try:
                    numbers = [float(p) for p in condition.split(",")]
                except ValueError:
                    numbers = []
                if len(numbers) not in (2, 3):
                    errors.append(f"{where}：范围要写成 起始,结束[,步长]")
                elif len(numbers) == 3 and numbers[2] == 0:
                    errors.append(f"{where}：循环步长不能为 0")
            else:
                try:
                    _parse_var_condition(condition)
                except StepError as exc:
                    errors.append(f"{where}：{exc}")
        elif self.type == "file" and text:
            action = text.split(":", 1)[0].strip().lower()
            rest = text.split(":", 1)[1].strip() if ":" in text else ""
            if action not in ("copy", "move", "delete", "mkdir", "wait",
                              "write", "append", "read"):
                errors.append(f"{where}：文件操作只支持 copy / move / delete / "
                              "mkdir / wait / write / append / read")
            elif action in ("copy", "move") and "|" not in rest:
                errors.append(f"{where}：复制/移动要写成 {action}:源|目标")
            elif action in ("write", "append") and "|" not in rest:
                errors.append(f"{where}：{action} 要写成 {action}:路径|内容")
            elif action == "write" and not rest.split("|", 1)[0].strip():
                errors.append(f"{where}：写文件要填写目标路径")
            elif action == "read" and (
                    "|" not in rest or not rest.split("|", 1)[1].strip()):
                errors.append(f"{where}：读文件要写成 read:路径|变量名")
            elif action in ("delete", "mkdir") and not rest:
                errors.append(f"{where}：{action} 后面要填写路径")
            elif action == "wait":
                path, sep, seconds = rest.partition("|")
                if not path.strip():
                    errors.append(f"{where}：等待文件要写成 wait:路径|秒")
                elif sep and seconds.strip():
                    try:
                        if float(seconds.strip()) < 0:
                            errors.append(f"{where}：等待文件的秒数不能为负数")
                    except ValueError:
                        errors.append(f"{where}：等待文件的秒数必须是数字")
        elif self.type == "json" and text:
            if "|" not in text or not text.split("|", 1)[0].strip():
                errors.append(f"{where}：JSON 要写成 变量名|字段路径")
        elif self.type == "regex" and text:
            parts = [p.strip() for p in text.split("|")]
            if len(parts) < 2 or not parts[0] or not parts[1]:
                errors.append(f"{where}：正则要写成 变量名|表达式[|分组号]")
            elif len(parts) > 3:
                errors.append(f"{where}：正则最多写成 变量名|表达式|分组号")
            else:
                try:
                    pattern = re.compile(parts[1])
                except re.error as exc:
                    errors.append(f"{where}：正则表达式无效：{exc}")
                else:
                    group_text = parts[2] if len(parts) > 2 else ""
                    if group_text:
                        try:
                            group = int(group_text)
                        except ValueError:
                            errors.append(f"{where}：正则分组号必须是整数")
                        else:
                            if group < 0 or group > pattern.groups:
                                errors.append(
                                    f"{where}：正则只有 {pattern.groups} 个分组，"
                                    f"分组号应在 0–{pattern.groups} 之间")
        elif self.type == "date" and text:
            if "|" not in text or not text.split("|", 1)[0].strip():
                errors.append(f"{where}：日期格式化要写成 变量名|格式")
        elif self.type == "random_wait" and text:
            parts = [p.strip() for p in text.split(",")]
            if len(parts) != 2:
                errors.append(f"{where}：随机等待要写成 最小秒,最大秒（两个数字）")
            else:
                try:
                    low, high = (float(parts[0]), float(parts[1]))
                except ValueError:
                    errors.append(f"{where}：随机等待要写成 最小秒,最大秒")
                else:
                    if low < 0 or high < 0:
                        errors.append(f"{where}：随机等待的秒数不能为负数")
                    elif low > high:
                        errors.append(f"{where}：随机等待的最小值不能大于最大值")

        if self.region and self.type in REGION_STEP_TYPES:
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
        value: float | str = self.repeat
        if value in (0, 0.0, "", None):
            value = ""
        elif float(value) == int(float(value)):
            value = float(int(float(value)))
        return [TYPE_TO_CODE.get(self.type, 1.0), self.content, value, self.note]

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
                    self.input_mode, self.enabled)

    def to_dict(self) -> dict:
        return {"type": self.type, "content": self.content,
                "repeat": self.repeat, "note": self.note,
                "timeout": self.timeout, "on_fail": self.on_fail,
                "region": self.region, "nth": self.nth,
                "grayscale": self.grayscale, "retry": self.retry,
                "retry_delay": self.retry_delay,
                "input_mode": self.input_mode,
                "enabled": self.enabled}

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
                                 or "paste"),
                   enabled=bool(data.get("enabled", True)))


@dataclass
class Group:
    """一个可复用的执行组。

    retry / retry_delay：整组跑完发现仍有失败步骤时，整组重跑几次、间隔多久。
    致命错误（on_fail=stop / 停止）不重试。
    """

    name: str = "新组"
    steps: list[Step] = field(default_factory=list)
    note: str = ""
    retry: int = 0
    retry_delay: float = 0.5

    def to_dict(self) -> dict:
        return {"name": self.name, "note": self.note,
                "retry": int(self.retry or 0),
                "retry_delay": float(self.retry_delay or 0.5),
                "steps": [step.to_dict() for step in self.steps]}

    @classmethod
    def from_dict(cls, data: dict) -> "Group":
        try:
            retry = max(0, int(data.get("retry", 0) or 0))
        except (TypeError, ValueError):
            retry = 0
        try:
            retry_delay = max(0.0, float(data.get("retry_delay", 0.5)
                                         if data.get("retry_delay")
                                         not in (None, "") else 0.5))
        except (TypeError, ValueError):
            retry_delay = 0.5
        return cls(name=str(data.get("name", "新组")),
                   note=str(data.get("note", "")),
                   retry=retry, retry_delay=retry_delay,
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
            if not step.enabled:
                continue              # 禁用步骤：不校验、不检查图片
            for message in step.validate(row=index):
                problems.append(f"[{group.name}] {message}")
            if check_images and step.type in (IMAGE_TYPES | WAIT_IMAGE_TYPES):
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

    by_name = {group.name: group for group in groups}
    for group in groups:
        for step in group.steps:
            if not step.enabled:
                continue
            for target in target_groups(step):
                if target not in by_name:
                    problems.append(f"[{group.name}] 调用的执行组不存在：{target}")

    problems.extend(_find_cycles(by_name))
    return problems


def validate_environment(groups: Iterable[Group]) -> list[str]:
    """运行前的本机环境检查：缺 OCR / UIA / OpenCV 时提前说清楚。

    和 validate_project 分开，是因为这检查的是「这台机器」而不是配置；
    只有纯坐标 / 文本流程的用户不需要 OpenCV，不该被拦。
    """
    needs_opencv = False
    needs_ocr = False
    needs_uia = False
    needs_accessibility = False
    needs_screen_recording = False
    for group in groups:
        for step in group.steps:
            if not step.enabled:
                continue
            if (step.type in IMAGE_TYPES or step.type in WAIT_IMAGE_TYPES
                    or step.type in ("if_image", "if_not_image", "if_else")):
                needs_opencv = True
            elif step.type == "drag":
                start = (step.content or "").split("|")[0].strip()
                # 起点写坐标时不需要图片识别
                if start and not _valid_xy(start):
                    needs_opencv = True
            if step.type in TEXT_STEP_TYPES or step.type in OCR_READ_TYPES:
                needs_ocr = True
            elif step.type in UIA_STEP_TYPES:
                needs_uia = True
            if step.type in MAC_ACCESSIBILITY_TYPES:
                needs_accessibility = True
            if step.type in MAC_SCREEN_TYPES:
                needs_screen_recording = True

    problems: list[str] = []
    if needs_opencv and not opencv_available():
        problems.append(OPENCV_PROBLEM)
    if needs_ocr:
        from . import ocr

        if not ocr.available():
            problems.append(ocr.OCR_INSTALL_HINT)
    if needs_uia:
        from . import uia

        if not uia.available():
            problems.append(uia.UIA_INSTALL_HINT)
    if platform_support.IS_MACOS:
        missing = set(platform_support.missing_permissions())
        if needs_accessibility and "accessibility" in missing:
            problems.append(
                "缺少「辅助功能」权限，鼠标键盘模拟 / 窗口操作 / 控件点击会失败。"
                "请到 系统设置 → 隐私与安全性 → 辅助功能 中勾选运行 MeowGo "
                "的终端 / Python，然后重启 MeowGo。")
        if needs_screen_recording and "screen_recording" in missing:
            problems.append(
                "缺少「屏幕录制」权限，截图 / 找图 / 文字识别步骤会失败。"
                "请到 系统设置 → 隐私与安全性 → 屏幕录制 中勾选运行 MeowGo "
                "的终端 / Python，然后重启 MeowGo。")
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
            if not step.enabled:
                continue
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
    """数据文件对应的结果文件。

    CSV / XLS 输入写 .result.csv；XLSX 输入写 .result.xlsx，
    保留原表格格式，用户在 Excel 里直接续用。
    """
    stem, ext = os.path.splitext(os.path.abspath(data_path))
    suffix = (".result.xlsx" if ext.lower() in (".xlsx", ".xlsm")
              else RESULT_SUFFIX)
    return stem + suffix


def read_results(path: str) -> list[dict]:
    """读回结果文件（断点续跑 / 合并用）；不存在返回空列表。"""
    if not path or not os.path.exists(path):
        return []
    if path.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl

        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            rows = list(book.active.iter_rows(values_only=True))
        finally:
            book.close()
        if not rows:
            return []
        headers = ["" if value is None else str(value) for value in rows[0]]
        return [
            {headers[i]: ("" if i >= len(row) or row[i] is None
                         else str(row[i]))
             for i in range(len(headers))}
            for row in rows[1:]
        ]
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
            row_key = int(float(old.get("__行号", "")))
        except (TypeError, ValueError):
            continue
        merged[row_key] = old

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
    var_columns: list[tuple[str, str]] = []
    for key in extra:
        column_name = (key if key not in columns and key not in reserved
                       else f"结果_{key}")
        while column_name in columns or column_name in reserved:
            column_name = f"结果_{column_name}"
        var_columns.append((key, column_name))

    fieldnames = list(columns) + ["__行号", "__结果", "__失败步骤"]
    fieldnames += [column_name for _key, column_name in var_columns]

    for item in results:
        row_no = int(item["row"])
        source = data[row_no - 1] if 0 < row_no <= len(data) else {}
        record = {key: source.get(key, "") for key in columns}
        record["__行号"] = row_no
        record["__结果"] = "成功" if item.get("ok") else "失败"
        record["__失败步骤"] = item.get("failed_step", "")
        values = item.get("vars") or {}
        for key, column_name in var_columns:
            record[column_name] = values.get(key, "")
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
    if path.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl

        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = "结果"
        sheet.append(fieldnames)
        for row_no in sorted(merged):
            record = merged[row_no]
            sheet.append([record.get(name, "") for name in fieldnames])
        sheet.column_dimensions["A"].width = 24
        book.save(path)
        return path

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
# 窗口操作（Windows / macOS）
# ---------------------------------------------------------------------------

_MAC_WINDOW_SCRIPT = """
tell application "System Events"
    set out to {}
    repeat with proc in (application processes whose background only is false)
        try
            repeat with win in windows of proc
                try
                    set end of out to (name of win) & "\\t" & (name of proc)
                end try
            end repeat
        end try
    end repeat
    return out
end tell
"""


def _osascript(script: str, timeout: float = 10) -> str:
    """执行 AppleScript；失败 / 无权限 / 非 macOS 时返回空串。"""
    if not platform_support.IS_MACOS:
        return ""
    try:
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True,
            text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _mac_windows() -> list[tuple[str, str]]:
    """[(窗口标题, 进程名)]；读不到（未授权辅助功能）时返回空列表。"""
    pairs: list[tuple[str, str]] = []
    for line in _osascript(_MAC_WINDOW_SCRIPT).splitlines():
        title, sep, process = line.partition("\t")
        if sep and title.strip():
            pairs.append((title.strip(), process.strip()))
    return pairs


def _mac_window_pair(title: str) -> Optional[tuple[str, str]]:
    """按标题子串找 (窗口标题, 进程名)。"""
    needle = (title or "").strip().lower()
    if not needle:
        return None
    for window_title_text, process in _mac_windows():
        if needle in window_title_text.lower():
            return window_title_text, process
    return None


def _mac_window_action(action: str, title: str) -> str:
    """macOS 窗口操作：activate / close（wait 由调用方轮询）。"""
    pair = _mac_window_pair(title)
    if pair is None:
        raise StepError(f"找不到标题包含「{title}」的窗口")
    window_text, process = pair
    process_literal = json.dumps(process, ensure_ascii=False)
    window_literal = json.dumps(window_text, ensure_ascii=False)
    if action == "activate":
        script = (
            f'tell application "System Events"\n'
            f'  tell process {process_literal}\n'
            f'    set frontmost to true\n'
            f'    try\n'
            f'      perform action "AXRaise" of window {window_literal}\n'
            f'    end try\n'
            f'  end tell\n'
            f'end tell')
        _osascript(script)
        return f"已激活窗口「{title}」"
    if action == "close":
        script = (
            f'tell application "System Events"\n'
            f'  tell process {process_literal}\n'
            f'    set frontmost to true\n'
            f'    try\n'
            f'      click button 1 of window {window_literal}\n'
            f'    on error\n'
            f'      delay 0.2\n'
            f'      keystroke "w" using command down\n'
            f'    end try\n'
            f'  end tell\n'
            f'end tell')
        _osascript(script)
        return f"已关闭窗口「{title}」"
    raise StepError(f"未知窗口操作：{action}")


def _window_exists(title: str) -> bool:
    """窗口是否存在（Windows 枚举句柄，macOS 走 AppleScript）。"""
    if platform_support.IS_MACOS:
        return _mac_window_pair(title) is not None
    return _find_window(title) is not None


def _enum_windows() -> list[tuple[int, str]]:
    """返回 [(hwnd, 标题), ...]，只收可见、有标题的窗口。"""
    if ctypes is None:  # pragma: no cover - 非 Windows
        return []
    found: list[tuple[int, str]] = []

    user32 = _win_user32()

    @_WNDENUMPROC
    def proc(hwnd, _lparam):
        # 回调里只做纯 Python 的标题读取与比较，绝不调外部函数
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

    user32.EnumWindows(proc, 0)
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


def window_title(hwnd: Optional[int]) -> str:
    """按 hwnd 查标题；查不到（已关闭 / 非 Windows）返回空串。"""
    if not hwnd:
        return ""
    for found, title in _enum_windows():
        if found == hwnd:
            return title
    return ""


def _force_foreground(hwnd: int) -> None:
    """把窗口带到前台。绕过系统对后台进程设置前台的限制。"""
    user32 = _win_user32()
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, _SW_RESTORE)
    user32.ShowWindow(hwnd, _SW_SHOW)
    # 经典技巧：假装自己刚收到一个 Alt 按键，系统就允许设置前台
    user32.keybd_event(_VK_MENU, 0, 0, 0)
    user32.keybd_event(_VK_MENU, 0, _KEYEVENTF_KEYUP, 0)
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)


def _mac_permission_hint(kind: str) -> str:
    """macOS 缺少某项权限时，返回一句可直接照做的提示（其它平台为空串）。"""
    if not platform_support.IS_MACOS:
        return ""
    if kind not in platform_support.missing_permissions():
        return ""
    names = {
        "accessibility": "辅助功能",
        "screen_recording": "屏幕录制",
    }
    if kind not in names:
        return ""
    return (f"缺少「{names[kind]}」权限：请到 系统设置 → 隐私与安全性 → "
            f"{names[kind]} 勾选 MeowGo（或运行它的终端），然后重启 MeowGo。")


def _window_action(text: str) -> str:
    """执行 activate:标题 / wait:标题|秒 / close:标题，返回结果描述。"""
    if ctypes is None and not platform_support.IS_MACOS:
        raise StepError("窗口操作只支持 Windows 和 macOS")
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
            if platform_support.IS_MACOS:
                if _mac_window_pair(title) is not None:
                    _mac_window_action("activate", title)
                    return f"窗口「{title}」已出现并激活"
            else:
                hwnd = _find_window(title)
                if hwnd is not None:
                    _force_foreground(hwnd)
                    return f"窗口「{title}」已出现并激活"
            if time.time() >= deadline:
                hint = _mac_permission_hint("accessibility")
                detail = f"：{hint}" if hint else ""
                raise StepError(f"等待窗口「{title}」超时（{limit:g} 秒）{detail}")
            time.sleep(POLL_INTERVAL)

    title = rest.strip()
    if platform_support.IS_MACOS:
        return _mac_window_action(action, title)
    hwnd = _find_window(title)
    if hwnd is None:
        raise StepError(f"找不到标题包含「{title}」的窗口")
    if action == "activate":
        _force_foreground(hwnd)
        return f"已激活窗口「{title}」"
    if action == "close":
        _win_user32().PostMessageW(hwnd, _WM_CLOSE, 0, 0)
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
      {变量.字段} 变量里存的是 JSON 时按字段路径取值，
                 支持 user.name、items[0].id
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
        # {变量.字段}：变量里存的是 JSON 时按路径取值（支持 items[0].id）
        root, sep, path = name.partition(".")
        if sep and vars and root in vars:
            raw = vars[root]
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
                value = _json_path(data, path)
            except (KeyError, IndexError, TypeError, ValueError):
                return match.group(0)
            if isinstance(value, str):
                return value
            return json.dumps(value, ensure_ascii=False)
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
    # 组级重试：组名 -> (重试次数, 间隔秒)；由界面 / 命令行按项目配置注入
    group_retry: dict[str, tuple[int, float]] = field(default_factory=dict)
    # 持久变量：跨运行保留，任意步骤里用 {持久:键} 读；写用「设置变量 持久:键=值」
    persist: dict[str, str] = field(default_factory=dict)
    on_persist: Optional[Callable[[str, str], None]] = None
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
    # 截图缓存：同一轮轮询复用一帧，避免每个候选图都重采全屏
    _screen_frame: object = None
    _screen_origin: tuple[int, int] = (0, 0)
    _screen_at: float = 0.0
    # 截图相对屏幕逻辑坐标的倍数（macOS Retina 为 2）
    _screen_scale: float = 1.0
    # 模板图缓存：path -> (mtime, ndarray)
    _template_cache: dict[str, tuple[float, object]] = field(
        default_factory=dict)
    # 多尺度匹配：图片文件 + mtime -> 验证过的缩放比例
    _scale_hints: dict[tuple[str, float], float] = field(default_factory=dict)
    # 同一张图两次全比例扫描的时间戳（节流）
    _scale_sweep_at: dict[tuple[str, float], float] = field(
        default_factory=dict)
    # 本场运行已经提示过缩放命中的图片
    _scale_notices: set[tuple[str, float]] = field(default_factory=set)
    # 锁屏检测（节流）
    _session_locked: bool = False
    _session_checked_at: float = 0.0
    _session_warned: bool = False
    # 递归调用栈 / 当前数据行（reset 时清空，运行期由执行器维护）
    _stack: list = field(default_factory=list)
    _row: dict = field(default_factory=dict)
    # 当前数据行号（1 起；非数据驱动运行时为 0）
    _row_no: int = 0

    # -- 生命周期 ---------------------------------------------------------
    def reset(self) -> None:
        self.stop_event.clear()
        self.pause_event.clear()
        self._stack = []
        self._row = {}
        self._row_no = 0
        self._step_ok = True
        self.stats = []
        # 运行期变量从持久变量起头：{持久:键} 在任何步骤里都能读到
        self.vars = {PERSIST_PREFIX + key: str(value)
                     for key, value in self.persist.items()}
        self.row_results = []
        self._row_var_keys = set()
        self._progress_done = 0
        self._screen_frame = None
        self._screen_at = 0.0
        self._screen_scale = 1.0
        self._scale_notices.clear()
        self._scale_sweep_at.clear()
        self._session_locked = False
        self._session_checked_at = 0.0
        self._session_warned = False

    def stop(self) -> None:
        self.stop_event.set()
        self.pause_event.clear()

    def debug_snapshot(self) -> dict:
        """单步调试用快照：当前变量、数据行和行号。

        工作线程停在 confirm 等待时调用，读的是一份浅拷贝，
        确认框里改变量不会影响这里已经取走的内容。
        """
        return {"vars": dict(self.vars),
                "row": dict(self._row),
                "row_no": int(self._row_no)}

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
        while True:
            if self.stop_event.is_set():
                return False
            if self.pause_event.is_set():
                time.sleep(POLL_INTERVAL)
                continue
            if self._session_blocked():
                time.sleep(POLL_INTERVAL)
                continue
            return True

    def _session_blocked(self) -> bool:
        """锁屏 / 安全桌面时返回 True，并提示一次；解锁后自动继续。"""
        now = time.time()
        if now - self._session_checked_at >= SESSION_CHECK_INTERVAL:
            self._session_checked_at = now
            locked = session_locked()
            if locked and not self._session_warned:
                self._emit("warn", "检测到锁屏 / 安全桌面：已暂停等待，"
                                   "解锁后自动继续（可随时停止）")
            elif not locked and self._session_warned:
                self._emit("info", "会话已恢复，继续执行")
            self._session_warned = locked
            self._session_locked = locked
        return self._session_locked

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

    def _invalidate_screen(self) -> None:
        """执行过鼠标/键盘动作后，下一轮必须重新截图。"""
        self._screen_at = 0.0

    def _capture_screen(self):
        """采集一帧整个虚拟桌面（单独一层便于测试注入假屏幕）。"""
        return capture_screen()

    def _grab_screen(self):
        """取一帧屏幕（带 TTL 缓存）。

        同一轮轮询（≤ SCREEN_CACHE_TTL）内复用，避免每个候选图都重采
        全屏。
        """
        import numpy as np

        now = time.time()
        if (self._screen_frame is not None
                and now - self._screen_at <= SCREEN_CACHE_TTL):
            return self._screen_frame, self._screen_origin
        frame, origin, scale = _split_capture(self._capture_screen())
        frame = np.ascontiguousarray(frame)
        self._screen_frame = frame
        self._screen_origin = origin
        self._screen_scale = scale
        self._screen_at = now
        return frame, self._screen_origin

    def _load_template(self, path: str):
        """模板图预加载：文件没变就一直复用，避免每轮重复读盘解码。"""
        import cv2
        import numpy as np

        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0.0
        cached = self._template_cache.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        # cv2.imread 在 Windows 上不支持中文路径；先读字节再解码
        try:
            data = np.fromfile(path, dtype=np.uint8)
            image = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
        except (OSError, ValueError):
            image = None
        if image is None:
            image = cv2.imread(path, cv2.IMREAD_COLOR)
        if image is None:
            raise StepError(f"图片无法读取（文件损坏或格式不支持）：{path}")
        if len(self._template_cache) >= 64:
            self._template_cache.clear()
            self._scale_hints.clear()
            self._scale_sweep_at.clear()
        self._template_cache[path] = (mtime, image)
        return image

    def _allow_scale_sweep(self, key: tuple[str, float]) -> bool:
        """同一张图的全比例扫描做节流，避免等待图片时持续占满 CPU。"""
        now = time.time()
        last = self._scale_sweep_at.get(key, 0.0)
        if now - last < SCALE_SWEEP_INTERVAL:
            return False
        self._scale_sweep_at[key] = now
        return True

    def _note_scale(self, path: str, factor: float) -> None:
        """缩放命中时提示一次，方便用户知道 DPI 适配生效了。"""
        if abs(factor - 1.0) < 1e-6:
            return
        key = (path, round(factor, 4))
        if key in self._scale_notices:
            return
        self._scale_notices.add(key)
        self._emit("info", f"图片 {os.path.basename(path)} 按 "
                           f"{factor:.0%} 缩放命中（已自动适配 DPI / 屏幕缩放）")

    def _locate_one(self, path: str,
                    region: Optional[tuple[int, int, int, int]],
                    nth: int, grayscale: bool):
        """在单张模板图上找第 nth 个（0 = 第一个）。

        先按原尺寸匹配（旧行为）；失败后再尝试常见 DPI 缩放比例。
        某个比例验证成功后记进 _scale_hints，后续轮询优先用它，
        避免每次都做完整的比例扫描。
        """
        import cv2

        if not opencv_available():
            raise StepError(OPENCV_HINT)

        template = self._load_template(path)
        frame, (origin_x, origin_y) = self._grab_screen()
        if frame.size == 0:
            return None
        scale = self._screen_scale
        if region:
            x0, y0, x1, y1 = _search_bounds(
                region, (origin_x, origin_y), scale, frame.shape)
            if x1 <= x0 or y1 <= y0:
                return None
            search = frame[y0:y1, x0:x1]
            base_x = origin_x + x0 / scale
            base_y = origin_y + y0 / scale
        else:
            search = frame
            base_x, base_y = origin_x, origin_y

        if grayscale:
            search = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)
            template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        if template.size == 0:
            return None

        mtime = self._template_cache.get(path, (0.0, None))[0]
        key = (path, float(mtime))
        hint = self._scale_hints.get(key)
        scaled_confidence = _scale_confidence(self.confidence)

        def pick(boxes: list[tuple[float, int, int, int, int]]):
            if not boxes:
                return None
            index = nth if nth and nth > 0 else 0
            if index >= len(boxes):
                return None
            return _match_point(base_x, base_y, scale, boxes[index])

        # 1. 上次验证成功的比例优先（DPI 通常整场运行都不变）
        if hint is not None:
            conf = (self.confidence if abs(hint - 1.0) < 1e-6
                    else scaled_confidence)
            point = pick(_match_at_scale(search, template, conf, hint))
            if point is not None:
                self._note_scale(path, hint)
                return point
            if abs(hint - 1.0) < 1e-6:
                hint = None

        # 2. 原尺寸（旧行为；也覆盖用户把缩放改回去的情况）
        if hint != 1.0:
            point = pick(_match_at_scale(search, template, self.confidence,
                                         1.0))
            if point is not None:
                return point

        # 3. 全比例扫描：首次遇到 DPI 变化时最多每 0.8 秒一次
        if self._allow_scale_sweep(key):
            factor = _discover_scale(search, template, scaled_confidence,
                                     self.stop_event)
            if factor is not None:
                self._scale_hints[key] = factor
                point = pick(_match_at_scale(search, template,
                                             scaled_confidence, factor))
                if point is not None:
                    self._note_scale(path, factor)
                    return point
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
                self._invalidate_screen()
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
        # macOS 上用户写的 ctrl / win 实际对应 Command
        parts = [p.lower() for p in parts]
        if platform_support.IS_MACOS:
            parts = ["command" if p in ("ctrl", "control", "win") else p
                     for p in parts]
        times = int(repeat) if repeat and repeat > 0 else 1
        if repeat == -1:
            while self._wait_ready():
                pyautogui.hotkey(*parts)
                self._invalidate_screen()
                self._emit("info", f"执行热键 {','.join(parts)}")
                if not self._sleep(0.1):
                    return
            return
        for _ in range(times):
            if not self._wait_ready():
                return
            pyautogui.hotkey(*parts)
            self._invalidate_screen()
            self._emit("info", f"执行热键 {','.join(parts)}")
            if not self._sleep(0.1):
                return

    def run_step(self, step: Step, depth: int = 0) -> None:
        import pyautogui

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
            self._invalidate_screen()
            self._emit("info", f"滚轮 {distance}（{notches} 格）")
            self._sleep(0.1)
        elif step.type == "hotkey":
            self._hotkey(text, step.repeat)
        elif step.type == "time":
            now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            self._input_text(now)
            self._emit("info", f"粘贴时间：{now}")
        elif step.type == "cmd":
            self._run_cmd(text, step)
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
            self._invalidate_screen()
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
                self._invalidate_screen()
                self._emit("info", f"点击坐标 ({x:g}, {y:g})")
                self._sleep(0.1)
        elif step.type == "move_xy":
            x, y, _button = _parse_xy(text)
            pyautogui.moveTo(x, y, duration=0.2)
            self._invalidate_screen()
            self._emit("info", f"鼠标移到 ({x:g}, {y:g})")
        elif step.type in WAIT_IMAGE_TYPES:
            self._wait_for_image(text, step,
                                 want_present=(step.type == "wait_image"))
        elif step.type in ("click_text", "wait_text", "assert_text"):
            self._wait_text(text, step, click=(step.type == "click_text"))
        elif step.type == "ui_click":
            self._ui_click(text, step)
        elif step.type in OCR_READ_TYPES:
            self._read_ocr_var(text, step)
        elif step.type == "if_var":
            self._run_if_var(text, step, depth)
        elif step.type == "http":
            self._http_request(text, step)
        elif step.type == "for_each":
            self._run_range_loop(text, step, depth)
        elif step.type == "while_var":
            self._run_while_loop(text, step, depth)
        elif step.type == "break":
            raise LoopBreak()
        elif step.type == "continue":
            raise LoopContinue()
        elif step.type == "file":
            self._run_file(text, step)
        elif step.type == "json":
            self._run_json(text)
        elif step.type == "regex":
            self._run_regex(text)
        elif step.type == "date":
            self._run_date(text)
        elif step.type == "clipboard":
            self._read_clipboard(text)
        elif step.type == "random_wait":
            self._random_wait(text)
        elif step.type == "section":
            self._emit("info", f"── {text} ──")
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
                self._invalidate_screen()
                self._emit("info", f"逐字输入：{text}")
                self._sleep(0.2)
                return
            self._emit("warn", "逐字输入不支持中文，已改用剪贴板粘贴")

        try:
            previous = pyperclip.paste()
        except Exception:      # noqa: BLE001 - 剪贴板不可用时不影响主流程
            previous = ""
        pyperclip.copy(text)
        pyautogui.hotkey(platform_support.MOD_KEY, "v")
        self._invalidate_screen()
        self._emit("info", f"输入：{text}")
        self._sleep(0.5)
        try:
            if pyperclip.paste() == text:      # 没被别人改过才还原
                pyperclip.copy(previous)
        except Exception:      # noqa: BLE001
            pass

    def _run_cmd(self, text: str, step: Optional[Step] = None) -> None:
        """执行系统命令，并按返回码判断这步是否成功。

        用 Popen 轮询而不是 subprocess.run：命令执行期间也能响应「停止」，
        并且支持步骤级超时（step.timeout，0 = 不限）。子进程输出写到临时
        文件，避免管道写满导致死锁。
        """
        import tempfile

        self._emit("info", f"执行命令：{text}")
        kwargs: dict[str, Any] = {"shell": True, "stdin": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NO_WINDOW", 0)
        else:
            # 独立进程组：停止 / 超时时能把命令拉起的子进程一起结束
            kwargs["start_new_session"] = True
        out_file = tempfile.TemporaryFile()
        err_file = tempfile.TemporaryFile()
        try:
            proc = subprocess.Popen(text, stdout=out_file,
                                    stderr=err_file, **kwargs)
        except Exception as exc:  # noqa: BLE001
            out_file.close()
            err_file.close()
            self._step_ok = False
            self._emit("error", f"命令执行失败：{exc}")
            return

        timeout = float(getattr(step, "timeout", 0) or 0)
        deadline = time.time() + timeout if timeout > 0 else None
        timed_out = False
        while proc.poll() is None:
            if self.stop_event.is_set():
                self._kill_process(proc)
                out_file.close()
                err_file.close()
                self._step_ok = False
                self._emit("warn", "命令已终止（收到停止请求）")
                return
            if deadline is not None and time.time() >= deadline:
                timed_out = True
                self._kill_process(proc)
                break
            time.sleep(POLL_INTERVAL)

        output = _read_temp_text(out_file)
        error = _read_temp_text(err_file)
        if output:
            self._emit("info", output[:500])
        if timed_out:
            self._step_ok = False
            self._emit("error", f"命令执行超时（{timeout:g} 秒），已强制结束")
            if getattr(step, "on_fail", "skip") == "stop":
                raise StepError(f"命令执行超时（{timeout:g} 秒），按设置中止执行")
            return
        if proc.returncode:
            self._step_ok = False
            self._emit("error", f"命令返回 {proc.returncode}："
                                f"{(error or output)[:300]}"
                                f"{_command_error_hint(text)}")
        self._sleep(0.2)

    @staticmethod
    def _kill_process(proc) -> None:
        """结束命令及其子进程（Windows 用 taskkill /T，POSIX 杀整个进程组）。"""
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    capture_output=True, timeout=10,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                killpg = getattr(os, "killpg", None)
                getpgid = getattr(os, "getpgid", None)
                sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
                if killpg and getpgid:
                    try:
                        killpg(getpgid(proc.pid), sigkill)
                    except (OSError, ProcessLookupError):
                        proc.kill()
                else:                       # 理论上不会走到：没有进程组 API
                    proc.kill()
        except Exception:  # noqa: BLE001 - 进程可能已经自己退出
            pass
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass

    def _locate_text(self, text: str, step: Step):
        """OCR 找文字，返回屏幕坐标；找不到返回 None。"""
        from . import ocr

        if not ocr.available():
            raise StepError(ocr.OCR_INSTALL_HINT)
        name, region = parse_text_target(
            text, getattr(step, "region", "") or "")
        if not name:
            raise StepError("要识别的文字不能为空")
        frame, (origin_x, origin_y) = self._grab_screen()
        if frame.size == 0:
            return None
        scale = self._screen_scale
        if region:
            x0, y0, x1, y1 = _search_bounds(
                region, (origin_x, origin_y), scale, frame.shape)
            if x1 <= x0 or y1 <= y0:
                return None
            crop = frame[y0:y1, x0:x1]
            base_x = origin_x + x0 / scale
            base_y = origin_y + y0 / scale
        else:
            crop = frame
            base_x, base_y = origin_x, origin_y
        try:
            point = ocr.find_text(crop, name)
        except ocr.OcrUnavailable as exc:
            raise StepError(str(exc)) from exc
        if point is None:
            return None
        return Point(base_x + point[0] / scale, base_y + point[1] / scale)

    def _wait_text(self, text: str, step: Step, click: bool = False) -> bool:
        """等文字出现（click=True 时顺便点一下），超时按 on_fail 处理。"""
        import pyautogui

        deadline = (time.time() + step.timeout
                    if step.timeout and step.timeout > 0 else None)
        self._step_ok = True
        while True:
            if not self._wait_ready():
                return False
            point = self._locate_text(text, step)
            if point is not None:
                if click:
                    pyautogui.click(point.x, point.y, duration=0.2)
                    self._invalidate_screen()
                    self._emit("info", f"已点击文字：{text}")
                else:
                    self._emit("info", f"已找到文字：{text}")
                return True
            if deadline is not None and time.time() >= deadline:
                self._step_ok = False
                self._emit("warn", f"等待文字「{text}」超时"
                                   f"（{step.timeout:g} 秒）")
                if step.on_fail == "stop":
                    raise StepError(f"等待文字「{text}」超时，按设置中止执行")
                return False
            self._emit("warn", f"未找到文字「{text}」，重试中…")
            if not self._sleep(POLL_INTERVAL):
                return False

    def _read_ocr_var(self, text: str, step: Step) -> None:
        """读取屏幕文字到变量：内容写变量名，区域走「识别范围」。"""
        from . import ocr

        name = (text or "").strip()
        if not name:
            raise StepError("读取屏幕文字：变量名不能为空")
        if not ocr.available():
            raise StepError(ocr.OCR_INSTALL_HINT)
        frame, (origin_x, origin_y) = self._grab_screen()
        if frame.size == 0:
            raise StepError("截图失败，读不到屏幕内容")
        region = _parse_region(getattr(step, "region", "") or "")
        if region:
            x0, y0, x1, y1 = _search_bounds(region, (origin_x, origin_y),
                                            self._screen_scale, frame.shape)
            if x1 <= x0 or y1 <= y0:
                self._step_ok = False
                self._emit("warn", "识别范围超出屏幕，没能读到文字")
                return
            crop = frame[y0:y1, x0:x1]
        else:
            crop = frame
        value = ocr.read_text(crop)
        if not value:
            self._step_ok = False
            self._emit("warn", f"没识别到文字（变量 {name} 未更新）")
            return
        self.vars[name] = value
        self._row_var_keys.add(name)
        preview = value.replace("\n", " / ")
        self._emit("info", f"读到文字 → {name}：{preview[:60]}"
                           + ("…" if len(preview) > 60 else ""))

    def _ui_click(self, text: str, step: Step) -> bool:
        """按 UIA 控件名称点击（内容可写「名称|控件类型」）。"""
        from . import uia

        if not uia.available():
            raise StepError(uia.UIA_INSTALL_HINT)
        name, _, control_type = text.partition("|")
        name, control_type = name.strip(), control_type.strip()
        if not name:
            raise StepError("控件名称不能为空")
        deadline = (time.time() + step.timeout
                    if step.timeout and step.timeout > 0 else None)
        self._step_ok = True
        while True:
            if not self._wait_ready():
                return False
            try:
                clicked = uia.click_control(name, control_type, timeout=0.5)
            except uia.UiaUnavailable as exc:
                raise StepError(str(exc)) from exc
            if clicked:
                self._invalidate_screen()
                self._emit("info", f"已点击控件：{name}")
                return True
            if deadline is not None and time.time() >= deadline:
                self._step_ok = False
                self._emit("warn", f"等待控件「{name}」超时"
                                   f"（{step.timeout:g} 秒）")
                if step.on_fail == "stop":
                    raise StepError(f"等待控件「{name}」超时，按设置中止执行")
                return False
            if not self._sleep(POLL_INTERVAL):
                return False

    def _http_request(self, text: str, step: Optional[Step] = None) -> None:
        """发 HTTP 请求，把响应正文存入变量，状态码存到「变量.status」。

        请求头可选（内容第三段之后），例如 ``X-Token: abc``；请求体以
        ``{`` / ``[`` 开头时自动按 JSON 发送。超时取步骤的「最多等 N 秒」，
        没填按 30 秒。4xx / 5xx 与网络错误都记为这步失败，响应正文仍会存进
        变量（方便用「读取 JSON」取错误码）。
        """
        from urllib.error import HTTPError, URLError
        from urllib.request import Request, urlopen

        method, url, var, body, headers = _parse_http_spec(text)
        data = body.encode("utf-8") if body else None
        request = Request(url, data=data, method=method)
        request.add_header("User-Agent", "MeowGo/1.0")
        for key, value in headers.items():
            request.add_header(key, value)
        if data and not request.has_header("Content-Type"):
            looks_json = body.lstrip().startswith(("{", "["))
            request.add_header(
                "Content-Type", "application/json; charset=utf-8"
                if looks_json else "text/plain; charset=utf-8")
        timeout = (float(step.timeout)
                   if step is not None and step.timeout and step.timeout > 0
                   else 30.0)
        self._emit("info", f"HTTP {method} {url}"
                           + ("（带请求头）" if headers else ""))
        status = 0
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310
                raw = response.read()
                status = int(getattr(response, "status", 0) or 0)
        except HTTPError as exc:
            status = int(exc.code or 0)
            try:
                raw = exc.read()
            except Exception:      # noqa: BLE001 - 读错误正文失败不影响流程
                raw = b""
            text_body = raw.decode("utf-8", "replace")
            self.vars[var] = text_body
            self.vars[f"{var}.status"] = str(status)
            self._row_var_keys.update({var, f"{var}.status"})
            self._step_ok = False
            self._emit("error", f"HTTP {method} {url} 返回 {status}"
                                + (f"：{text_body[:80]}" if text_body else ""))
            return
        except (URLError, OSError, ValueError) as exc:
            self.vars[f"{var}.status"] = "0"
            self._row_var_keys.add(f"{var}.status")
            self._step_ok = False
            self._emit("error", f"HTTP {method} {url} 失败：{exc}")
            return
        text_body = raw.decode("utf-8", "replace")
        self.vars[var] = text_body
        self.vars[f"{var}.status"] = str(status)
        self._row_var_keys.update({var, f"{var}.status"})
        self._emit("info", f"响应 {status}，{len(text_body)} 字符，"
                           f"已存入变量 {var}（状态码 {var}.status）")

    def _run_if_var(self, text: str, step: Step, depth: int) -> None:
        condition, _, target = text.partition("|")
        target = target.strip()
        if not target:
            raise StepError("变量条件要写成 变量名 运算符 值|组名")
        name, operator, expected = _parse_var_condition(condition)
        actual = self.vars.get(name, "")
        matched = _compare_values(actual, operator, expected)
        self._emit("info", f"变量条件：{name}={actual!r} {operator} "
                           f"{expected!r} → {'满足' if matched else '不满足'}")
        if matched:
            self._run_group(target, depth + 1)

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

    def _wait_for_image(self, image: str, step: Step,
                        want_present: bool) -> bool:
        """等待图片出现 / 消失，返回是否等到。

        与条件步骤的 ``_wait_image`` 不同：这里的「最多等 N 秒」留空
        （0）表示一直等，和「单击图片」的旧行为一致——步骤名字就是
        「等待」，默认不设上限才符合直觉。超时后按 step.on_fail 处理：
        stop 中止整轮执行，其余记为这步失败并继续下一步。
        """
        if not parse_image_targets(image):
            raise StepError("要等待的图片不能为空")

        action = "出现" if want_present else "消失"
        deadline = (time.time() + step.timeout
                    if step.timeout and step.timeout > 0 else None)
        if deadline is None:
            self._emit("info", f"等待图片{action}（不限时）：{image}")
        else:
            self._emit("info",
                       f"等待图片{action}（最多 {step.timeout:g} 秒）：{image}")

        started = time.time()
        while True:
            if not self._wait_ready():
                return False
            location = self._locate(image, step)
            if (location is not None) == want_present:
                waited = time.time() - started
                self._emit("info", f"图片已{action}（等待 {waited:.1f} 秒）："
                                   f"{image}")
                return True
            if deadline is not None and time.time() >= deadline:
                break
            if not self._sleep(POLL_INTERVAL):
                return False

        self._step_ok = False
        self._emit("warn",
                   f"等待图片{action}超时（{step.timeout:g} 秒）：{image}")
        if step.on_fail == "stop":
            raise StepError(f"等待图片{action}超时，按设置中止执行")
        return False

    def _wait_window(self, title: str, timeout: float):
        """按 timeout 等窗口出现；timeout=0 时只找一次。"""
        deadline = (time.time() + timeout
                    if timeout and timeout > 0 else None)
        while True:
            if _window_exists(title):
                return True
            if deadline is None or time.time() >= deadline:
                return False
            if not self._sleep(POLL_INTERVAL):
                return False

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
        title, _, target = text.partition("|")
        title, target = title.strip(), target.strip()
        if not title or not target:
            raise StepError("窗口条件要写成 窗口标题|组名")
        if not self._wait_window(title, step.timeout):
            self._emit("info", f"条件不满足：找不到窗口「{title}」，跳过")
            return
        self._emit("info", f"条件满足：窗口「{title}」存在，执行「{target}」")
        call_step = Step("call", target, step.repeat)
        self._call_group(call_step, depth)

    # -- 循环 -------------------------------------------------------------
    def _set_loop_vars(self, value, index: int) -> dict:
        """写入 {loop} / {loop_index}，返回旧值（嵌套循环结束时还原）。"""
        saved = {key: self.vars.get(key) for key in ("loop", "loop_index")}
        self.vars["loop"] = _plain_number(value)
        self.vars["loop_index"] = str(index)
        return saved

    def _restore_loop_vars(self, saved: dict) -> None:
        for key, value in saved.items():
            if value is None:
                self.vars.pop(key, None)
            else:
                self.vars[key] = value

    def _run_range_loop(self, text: str, step: Step, depth: int) -> None:
        """按范围循环：起始,结束[,步长]|组名，每轮把当前值写进 {loop}。"""
        spec, _, target = text.partition("|")
        target = target.strip()
        if not target:
            raise StepError("循环要写成 起始,结束[,步长]|组名")
        try:
            numbers = [float(part) for part in spec.split(",")]
        except ValueError:
            raise StepError("循环范围必须是数字：起始,结束[,步长]") from None
        if len(numbers) not in (2, 3):
            raise StepError("循环范围要写成 起始,结束[,步长]")
        start, end = numbers[0], numbers[1]
        step_value = numbers[2] if len(numbers) == 3 else 1.0
        if step_value == 0:
            raise StepError("循环步长不能为 0")
        limit = int(step.repeat) if step.repeat and step.repeat > 0 else 0
        saved = self._set_loop_vars(start, 0)
        rounds = 0
        try:
            value = start
            while (value <= end) if step_value > 0 else (value >= end):
                if limit and rounds >= limit:
                    self._emit("warn", f"达到轮次上限 {limit}，循环结束")
                    break
                if not self._wait_ready():
                    return
                rounds += 1
                self.vars["loop"] = _plain_number(value)
                self.vars["loop_index"] = str(rounds)
                self._emit("info", f"↻ 第 {rounds} 轮（loop={self.vars['loop']}）")
                try:
                    self._run_group(target, depth + 1)
                except LoopContinue:
                    pass
                except LoopBreak:
                    self._emit("info", "循环被 break 步骤结束")
                    break
                value += step_value
        finally:
            self._restore_loop_vars(saved)

    def _run_while_loop(self, text: str, step: Step, depth: int) -> None:
        """变量条件循环：条件|组名；repeat 是最大轮次（默认 1000）。"""
        condition, _, target = text.partition("|")
        target = target.strip()
        if not target:
            raise StepError("循环要写成 变量名 运算符 值|组名")
        limit = int(step.repeat) if step.repeat and step.repeat > 0 else 1000
        saved = self._set_loop_vars(1, 0)
        rounds = 0
        try:
            while rounds < limit:
                name, operator, expected = _parse_var_condition(condition)
                actual = self.vars.get(name, "")
                if not _compare_values(actual, operator, expected):
                    break
                if not self._wait_ready():
                    return
                rounds += 1
                self.vars["loop"] = str(rounds)
                self.vars["loop_index"] = str(rounds)
                self._emit("info", f"↻ 第 {rounds} 轮（{name}={actual}）")
                try:
                    self._run_group(target, depth + 1)
                except LoopContinue:
                    pass
                except LoopBreak:
                    self._emit("info", "循环被 break 步骤结束")
                    break
            else:
                self._emit("warn", f"达到最大轮次 {limit}，循环结束")
        finally:
            self._restore_loop_vars(saved)

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
        if name.startswith(PERSIST_PREFIX):
            # 持久变量：写进运行器外部的字典并通知调用方落盘，
            # 之后用 {持久:键} 就能取到（下次运行仍然有效）
            key = name[len(PERSIST_PREFIX):].strip()
            if not key:
                raise StepError("持久变量要写成 持久:键=值")
            self.persist[key] = value
            self.vars[PERSIST_PREFIX + key] = value
            if self.on_persist is not None:
                try:
                    self.on_persist(key, value)
                except Exception as exc:  # noqa: BLE001 - 存不下不该中断流程
                    self._emit("warn", f"持久变量没能保存到配置文件：{exc}")
            self._emit("info", f"保存持久变量 {key} = {value}"
                               f"（下次运行仍可用）")
            return
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
        pyautogui.hotkey(platform_support.MOD_KEY, "c")
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

    # -- 文件 / 数据 / 文本 -----------------------------------------------
    def _value_of(self, name: str) -> str:
        """先取运行期变量，其次取当前数据行的同名列。"""
        if name in self.vars:
            return str(self.vars[name])
        value = self._row.get(name, "")
        return "" if value is None else str(value)

    def _run_file(self, text: str, step: Step) -> None:
        """文件操作：copy / move / delete / mkdir / wait。"""
        action, _, rest = text.partition(":")
        action = action.strip().lower()
        rest = rest.strip()
        if action in ("copy", "move"):
            source, _, target = rest.partition("|")
            source, target = os.path.expanduser(source.strip()), \
                os.path.expanduser(target.strip())
            if not source or not target:
                raise StepError(f"文件{action}要写成 {action}:源|目标")
            if not os.path.exists(source):
                raise StepError(f"源文件不存在：{source}")
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            if action == "copy":
                shutil.copy2(source, target)
            else:
                shutil.move(source, target)
            self._emit("info", f"文件已{('复制' if action == 'copy' else '移动')}"
                               f"：{source} → {target}")
        elif action == "delete":
            path = os.path.expanduser(rest)
            if not path:
                raise StepError("删除文件要写成 delete:路径")
            if not os.path.exists(path):
                self._step_ok = False
                self._emit("warn", f"要删除的文件/目录不存在：{path}")
                return
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            self._emit("info", f"已删除：{path}")
        elif action == "mkdir":
            path = os.path.expanduser(rest)
            if not path:
                raise StepError("建目录要写成 mkdir:目录")
            os.makedirs(path, exist_ok=True)
            self._emit("info", f"已创建目录：{path}")
        elif action in ("write", "append"):
            path, sep, content = rest.partition("|")
            path = os.path.expanduser(path.strip())
            if not path or not sep:
                raise StepError(f"写文件要写成 {action}:路径|内容")
            directory = os.path.dirname(os.path.abspath(path))
            os.makedirs(directory, exist_ok=True)
            # 写 / 追加都自动补一个换行，方便按行累积（已有换行不重复加）
            payload = (content if content.endswith(("\n", "\r"))
                       else content + "\n")
            if action == "write":
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8", newline="") as fh:
                    fh.write(payload)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, path)
            else:
                with open(path, "a", encoding="utf-8", newline="") as fh:
                    fh.write(payload)
            self._emit("info", f"已写入文件（{('覆盖' if action == 'write' else '追加')}"
                               f" {len(payload)} 字符）：{path}")
        elif action == "read":
            path, sep, var = rest.partition("|")
            path, var = os.path.expanduser(path.strip()), var.strip()
            if not path or not sep or not var:
                raise StepError("读文件要写成 read:路径|变量名")
            if not os.path.isfile(path):
                self._step_ok = False
                self._emit("warn", f"要读的文件不存在：{path}")
                return
            text_value = _read_text_file(path)
            self.vars[var] = text_value
            self._row_var_keys.add(var)
            self._emit("info", f"已读入 {len(text_value)} 字符到变量 {var}"
                               f"：{text_value[:40]}"
                               + ("…" if len(text_value) > 40 else ""))
        elif action == "wait":
            path, _, seconds = rest.partition("|")
            path = os.path.expanduser(path.strip())
            if not path:
                raise StepError("等待文件要写成 wait:路径|秒")
            try:
                timeout = float(seconds.strip()) if seconds.strip() else \
                    float(step.timeout or 10)
            except ValueError:
                raise StepError("等待文件的秒数必须是数字") from None
            deadline = time.time() + max(0.0, timeout)
            while not os.path.exists(path):
                if not self._sleep(POLL_INTERVAL):
                    return
                if time.time() >= deadline:
                    raise StepError(f"等待文件超时：{path}")
            self._emit("info", f"文件已出现：{path}")
        else:
            raise StepError("文件操作只支持 copy / move / delete / mkdir / "
                            "wait / write / append / read")

    def _run_json(self, text: str) -> None:
        """从变量里的 JSON 取字段：源变量|字段路径[|目标变量]。"""
        parts = [part.strip() for part in text.split("|")]
        source = parts[0]
        path = parts[1] if len(parts) > 1 else ""
        target = parts[2] if len(parts) > 2 and parts[2] else source
        raw = self._value_of(source)
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise StepError(f"变量 {source} 不是合法 JSON：{exc}") from None
        try:
            value = _json_path(data, path)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise StepError(f"JSON 字段不存在：{path}（{exc}）") from None
        if isinstance(value, str):
            self.vars[target] = value
        else:
            self.vars[target] = json.dumps(value, ensure_ascii=False)
        self._row_var_keys.add(target)
        self._emit("info", f"JSON 字段 {path} → {target}："
                           f"{self.vars[target][:60]}")

    def _run_regex(self, text: str) -> None:
        """对变量做正则提取：变量名|表达式[|分组号]，结果写回同名变量。"""
        parts = [part.strip() for part in text.split("|")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise StepError("正则要写成 变量名|表达式[|分组号]")
        if len(parts) > 3:
            raise StepError("正则最多写成 变量名|表达式|分组号")
        name, pattern = parts[0], parts[1]
        group_text = parts[2] if len(parts) > 2 else ""
        try:
            group = int(group_text) if group_text else 0
        except ValueError:
            raise StepError("正则分组号必须是整数") from None
        if group < 0:
            raise StepError("正则分组号不能为负数")
        source = self._value_of(name)
        try:
            match = re.search(pattern, source, re.MULTILINE)
        except re.error as exc:
            raise StepError(f"正则表达式无效：{exc}") from None
        if not match:
            self._step_ok = False
            self._emit("warn", f"正则没有匹配到内容：{pattern}")
            return
        try:
            value = match.group(group)
        except IndexError:
            raise StepError(f"正则没有第 {group} 个分组") from None
        self.vars[name] = value
        self._row_var_keys.add(name)
        self._emit("info", f"正则提取 {name} = {value[:60]}")

    def _run_date(self, text: str) -> None:
        """日期格式化：变量名|格式，如 今天|%Y-%m-%d %H:%M。"""
        name, _, fmt = text.partition("|")
        name, fmt = name.strip(), fmt.strip()
        if not name or not fmt:
            raise StepError("日期格式化要写成 变量名|格式")
        value = time.strftime(fmt, time.localtime())
        self.vars[name] = value
        self._row_var_keys.add(name)
        self._emit("info", f"日期变量 {name} = {value}")

    def _read_clipboard(self, text: str) -> None:
        """读取剪贴板到变量。"""
        import pyperclip

        name = text.strip()
        if not name:
            raise StepError("读取剪贴板要写成 变量名")
        try:
            value = pyperclip.paste()
        except Exception as exc:  # noqa: BLE001
            raise StepError(f"读取剪贴板失败：{exc}") from None
        self.vars[name] = value
        self._row_var_keys.add(name)
        self._emit("info", f"剪贴板 → {name}：{value[:60]}")

    def _random_wait(self, text: str) -> None:
        """随机等待：最小秒,最大秒。"""
        parts = [part.strip() for part in text.split(",")]
        if len(parts) != 2:
            raise StepError("随机等待要写成 最小秒,最大秒（两个数字）")
        try:
            low, high = float(parts[0]), float(parts[1])
        except ValueError:
            raise StepError("随机等待要写成 最小秒,最大秒") from None
        if low < 0 or high < 0:
            raise StepError("随机等待的秒数不能为负数")
        if low > high:
            raise StepError("随机等待的最小值不能大于最大值")
        seconds = random.uniform(low, high)
        self._emit("info", f"随机等待 {seconds:.2f} 秒")
        self._sleep(seconds)

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
            self._invalidate_screen()
            self._emit("info", f"拖拽 ({start[0]:g}, {start[1]:g}) → "
                               f"({end[0]:g}, {end[1]:g})")
            return True
        finally:
            # 即使中途停止 / 移动失败，也一定把左键松开
            pyautogui.mouseUp()
            self._invalidate_screen()

    # -- 组执行 -----------------------------------------------------------
    def _group_retry(self, name: str) -> tuple[int, float]:
        """读组级重试配置（重试次数, 间隔秒），没配置返回 (0, 0.5)。"""
        raw = self.group_retry.get(name)
        if not raw:
            return 0, 0.5
        try:
            retries = max(0, int(raw[0]))
        except (TypeError, ValueError, IndexError):
            retries = 0
        try:
            delay = max(0.0, float(raw[1]))
        except (TypeError, ValueError, IndexError):
            delay = 0.5
        return retries, delay

    def _run_group(self, name: str, depth: int = 0,
                   start_index: int = 0) -> None:
        """执行一个组；配了组级重试时，整组跑完发现有失败就重跑整组。

        致命错误（on_fail=stop、停止、调用层级过深）不走重试，直接向上抛。
        """
        steps = self.groups.get(name)
        if steps is None:
            raise StepError(f"找不到执行组：{name}")
        if depth > MAX_CALL_DEPTH:
            raise StepError(f"调用层级过深（可能循环调用）：{name}")
        retries, delay = self._group_retry(name)
        attempts = 1 + retries
        for attempt in range(1, attempts + 1):
            before = len(self.stats)
            self._run_group_once(name, steps, depth,
                                 start_index if attempt == 1 else 0)
            failed = [item for item in self.stats[before:] if not item["ok"]]
            if not failed:
                return
            if attempt >= attempts or self.stop_event.is_set():
                if retries:
                    self._emit("warn", f"执行组「{name}」重试 {retries} 次后"
                                       f"仍有 {len(failed)} 步失败")
                return
            self._emit("warn", f"执行组「{name}」有 {len(failed)} 步失败，"
                               f"整组重试（{attempt}/{retries}），"
                               f"{delay:g} 秒后开始")
            if not self._sleep(delay):
                return

    def _run_group_once(self, name: str, steps: list[Step], depth: int,
                        start_index: int = 0) -> None:
        """按顺序跑一遍组内步骤（不做重试，重试交给 _run_group）。"""
        if start_index:
            self._emit("info", f"▶ 进入执行组「{name}」"
                               f"（从第 {start_index + 1} 步开始）")
        else:
            self._emit("info", f"▶ 进入执行组「{name}」（{len(steps)} 步）")
        for index, step in enumerate(steps[start_index:], start=start_index):
            if self.stop_event.is_set():
                return
            if not self._wait_ready():
                return
            if not step.enabled:
                # 禁用步骤：不确认、不执行、不计入明细，但进度条仍算一步
                self._emit("info", f"第 {index + 1} 步已禁用，跳过"
                                   f"（{step.label()}）")
                self._progress_done += 1
                if self.on_progress:
                    self.on_progress(self._progress_done,
                                     self._progress_total)
                continue
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
            control: Optional[Exception] = None
            attempts = 0
            max_attempts = 1 + max(0, int(step.retry or 0))
            while True:
                attempts += 1
                failed = False
                fatal = None
                control = None
                self._step_ok = True
                try:
                    if step.type == "call":
                        self._call_group(step, depth)
                    else:
                        self.run_step(step, depth)
                except (LoopBreak, LoopContinue) as exc:
                    # break / continue 是控制流，不是失败：交回外层循环处理
                    control = exc
                except StepError as exc:
                    # 致命错误（如窗口等待超时）：记入统计后再向上抛，
                    # 保证报告里能看到是哪一步失败的
                    failed = True
                    fatal = exc
                except Exception as exc:  # 单步失败不应终止整体
                    if _is_failsafe(exc):
                        # 急停必须真的停下来：不能重试，也不能继续下一步
                        failed = True
                        self._step_ok = False
                        self.stop_event.set()
                        fatal = Stopped(
                            "检测到急停（鼠标已移到屏幕角落），本次运行已停止")
                    else:
                        failed = True
                        self._emit("error", f"第 {index + 1} 步出错：{exc}")

                ok = control is not None or (not failed and self._step_ok)
                if (ok or fatal is not None or control is not None
                        or attempts >= max_attempts):
                    break
                if self.stop_event.is_set() or not self._wait_ready():
                    break
                self._emit("warn", f"第 {index + 1} 步失败，"
                                   f"{step.retry_delay:g} 秒后重试"
                                   f"（{attempts}/{max_attempts - 1}）")
                if not self._sleep(step.retry_delay):
                    break

            ok = control is not None or (not failed and self._step_ok)
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
            if control is not None:
                raise control
            if not ok:
                action, target = parse_on_fail(step.on_fail)
                if action == "goto" and target:
                    self._emit("warn", f"第 {index + 1} 步失败，"
                                       f"跳转到「{target}」")
                    self._run_group(target, depth + 1)
                    fatal = None       # 已由跳转组接手，继续下一步
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
            hint = _mac_permission_hint("screen_recording")
            suffix = f"（{hint}）" if hint else ""
            self._emit("warn", f"失败截图保存失败：{exc}{suffix}")
            return ""

    def _call_group(self, step: Step, depth: int) -> None:
        """调用执行组，支持 组名|键=值;键=值 的参数（子组内局部生效）。"""
        raw = (step.content or "").strip()
        target, _, params = raw.partition("|")
        target = target.strip()
        if not target:
            raise StepError("调用组要写成 组名 或 组名|键=值;键=值")
        saved: dict[str, Optional[str]] = {}
        if params.strip():
            rendered = render_content(params, self._row, self._row_no,
                                      self.vars)
            for pair in re.split(r"[;；]", rendered):
                pair = pair.strip()
                if not pair:
                    continue
                key, sep, value = pair.partition("=")
                if not sep or not key.strip():
                    raise StepError("调用组参数要写成 键=值，用分号分隔")
                key = key.strip()
                saved[key] = self.vars.get(key)
                self.vars[key] = value.strip()
            self._emit("info", f"调用参数：{rendered}")
        try:
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
        finally:
            for key, value in saved.items():
                if value is None:
                    self.vars.pop(key, None)
                else:
                    self.vars[key] = value

    def run(self, entry: Optional[str] = None, loop: bool = False,
            start_step: int = 0) -> str:
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
                        self._run_group(
                            name, start_index=(start_step if entry else 0))
                        if self.stop_event.is_set():
                            self._emit("warn", "已停止")
                            return "stopped"
                    if not loop:
                        break
                    # 循环模式：只保留最近一段明细，防止 stats 无限增长
                    if len(self.stats) > MAX_LOOP_STATS:
                        self.stats = self.stats[-KEEP_LOOP_STATS:]
                    if not self._sleep(0.1):
                        self._emit("warn", "已停止")
                        return "stopped"
                if not loop:
                    self._record_row(row_no, stat_start)
            self._emit("info", "执行完成")
            return "finished"
        except Stopped as exc:
            self._emit("warn", str(exc))
            if not loop:
                self._record_row(self._row_no, stat_start, force_failed=True)
            return "stopped"
        except (LoopBreak, LoopContinue):
            self._emit("error", "break / continue 只能用在循环步骤里")
            if not loop:
                self._record_row(self._row_no, stat_start, force_failed=True)
            return "error"
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
            "vars": dict(self.vars),
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
    # 运行结束时变量快照（只记录最后一场运行的值，方便排查）
    snapshot = report.get("vars") or {}
    if snapshot:
        lines += ["", "## 运行变量", ""]
        for key in sorted(snapshot):
            value = str(snapshot[key]).replace("\n", " ")
            if len(value) > 200:
                value = value[:200] + "…"
            lines.append(f"- {key} = {value}")
    return "\n".join(lines) + "\n"


def parse_report_meta(text: str) -> dict:
    """从 markdown 运行报告里读出摘要字段（结果 / 步数 / 失败 / 耗时 / 行数）。"""
    meta = {"result": "", "steps": 0, "failed": 0, "seconds": 0.0, "rows": 0}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- 结果："):
            meta["result"] = line[len("- 结果："):].strip()
        elif line.startswith("- 步骤数："):
            match = re.search(r"步骤数：(\d+)，失败：(\d+)", line)
            if match:
                meta["steps"] = int(match.group(1))
                meta["failed"] = int(match.group(2))
        elif line.startswith("- 总耗时："):
            value = line[len("- 总耗时："):].replace("秒", "").strip()
            try:
                meta["seconds"] = float(value)
            except ValueError:
                pass
        elif line.startswith("- 数据行数："):
            try:
                meta["rows"] = int(line[len("- 数据行数："):].strip())
            except ValueError:
                pass
    return meta


def _split_table_row(line: str) -> list[str]:
    """拆一行 markdown 表格；内容里的 ``\\|`` 还原成普通竖线。"""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip().replace("\\|", "|")
            for cell in re.split(r"(?<!\\)\|", line)]


def parse_report_table(text: str) -> tuple[list[str], list[list[str]]]:
    """拆出报告里的 markdown 表格：返回（表头，数据行），跳过对齐行。"""
    headers: list[str] = []
    rows: list[list[str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = _split_table_row(line)
        if not headers:
            headers = cells
            continue
        if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells):
            continue
        rows.append(cells)
    return headers, rows


def list_run_reports(directory: str) -> list[dict]:
    """列出目录里的 run_*.md，按修改时间倒序，附带解析出的头信息。"""
    try:
        names = [name for name in os.listdir(directory)
                 if name.startswith("run_") and name.endswith(".md")]
    except OSError:
        return []
    reports = []
    for name in names:
        path = os.path.join(directory, name)
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        reports.append({"path": path, "name": name, "mtime": mtime,
                        "text": text, **parse_report_meta(text)})
    reports.sort(key=lambda item: item["mtime"], reverse=True)
    return reports


def first_failed_step(text: str) -> str:
    """从报告表格里找出第一个失败的步骤（「类型：内容」），找不到返回空串。"""
    headers, rows = parse_report_table(text)
    if not headers or "结果" not in headers:
        return ""
    result_index = headers.index("结果")
    type_index = headers.index("类型") if "类型" in headers else -1
    content_index = headers.index("内容") if "内容" in headers else -1
    for row in rows:
        if len(row) <= result_index or "失败" not in row[result_index]:
            continue
        kind = row[type_index].strip() if 0 <= type_index < len(row) else ""
        content = (row[content_index].strip()
                   if 0 <= content_index < len(row) else "")
        text_value = f"{kind}：{content}" if kind and content else (kind or content)
        return text_value[:40]
    return ""


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
    stats = {"total": len(paths), "ok": 0, "seconds": 0.0, "failed": {}}
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        referenced_shots.update(re.findall(r"\((shots/[^)]+)\)", text))
        meta = parse_report_meta(text)
        result = meta["result"]
        steps = str(meta["steps"])
        failed = str(meta["failed"])
        seconds = f"{meta['seconds']:g}"
        label = {"finished": "成功", "error": "失败",
                 "stopped": "已停止"}.get(result, result or "未知")
        css = {"finished": "ok", "error": "bad",
               "stopped": "warn"}.get(result, "")
        if result == "finished":
            stats["ok"] += 1
        stats["seconds"] += float(meta["seconds"] or 0)
        if result == "error":
            step_name = first_failed_step(text)
            if step_name:
                stats["failed"][step_name] = stats["failed"].get(step_name, 0) + 1
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
    total = stats["total"]
    rate = f"{stats['ok'] * 100 / total:.0f}%" if total else "—"
    average = f"{stats['seconds'] / total:.1f}" if total else "—"
    if stats["failed"]:
        top_step, top_times = max(stats["failed"].items(),
                                  key=lambda item: item[1])
        top_text = f"{top_step}（{top_times} 次）"
    else:
        top_text = "—"
    summary = (
        '<div class="cards">'
        f'<div class="card"><span class="k">成功率</span>'
        f'<span class="v">{rate}</span>'
        f'<span class="n">成功 {stats["ok"]} / {total} 次</span></div>'
        f'<div class="card"><span class="k">平均耗时</span>'
        f'<span class="v">{average}</span>'
        '<span class="n">秒 / 次</span></div>'
        f'<div class="card wide"><span class="k">最常失败的步骤</span>'
        f'<span class="v small">{html.escape(top_text)}</span></div>'
        '</div>')
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
  .cards {{ display: flex; gap: 12px; margin: 0 0 16px; flex-wrap: wrap; }}
  .card {{ flex: 1 1 200px; display: flex; flex-direction: column; gap: 2px;
           background: {p['CARD']}; border: 1px solid {p['BORDER']};
           border-radius: {theme.RADIUS_M}px; padding: 12px 16px;
           box-shadow: 0 1px 2px rgba(8, 8, 6, 0.04); }}
  .card.wide {{ flex: 1 1 320px; }}
  .card .k {{ font-size: 12px; color: {p['MUTED']}; }}
  .card .v {{ font-size: 22px; font-weight: 700;
              font-variant-numeric: tabular-nums; }}
  .card .v.small {{ font-size: 14px; font-weight: 600; }}
  .card .n {{ font-size: 12px; color: {p['TEXT_WEAK']}; }}
</style>
</head>
<body>
<main>
  <div class="head"><h1>MeowGo</h1><span class="dot">·</span></div>
  <p class="muted">运行报告 · 最近 {keep} 次 · 自动刷新 · 点击「查看报告」打开详情</p>
{summary}
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
