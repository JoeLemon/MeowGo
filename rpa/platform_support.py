# -*- coding: utf-8 -*-
"""平台能力表与 macOS 权限自检。

界面和引擎都通过这里判断「当前系统能做什么」，不要各自散写
``os.name`` 判断：这样菜单置灰、设置项提示和运行时校验能保持一致。

macOS 上 pyautogui 依赖「辅助功能」权限（模拟点击/按键）、
PIL.ImageGrab 依赖「屏幕录制」权限（截图）。未授权时两者都会
安静地失效，所以启动时先检测并引导用户去系统设置里打开。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# 与系统交互的能力名 → 当前平台是否支持
CAPABILITIES = {
    "system_tray": IS_WINDOWS,
    "autostart": IS_WINDOWS,
    "scheduled_task": IS_WINDOWS,
    "window_ops": IS_WINDOWS,
    "global_hotkey": IS_WINDOWS,
    "keyboard_hook": IS_WINDOWS,
    "ui_automation": IS_WINDOWS,
    "applescript": IS_MACOS,
}

# 非 Windows 上不可用能力的替代方案说明
_ALTERNATIVES = {
    "system_tray": "macOS / Linux 上关闭窗口即退出，可用 Dock 重新打开。",
    "autostart": "macOS 可在「系统设置 → 通用 → 登录项」里添加 MeowGo.app。",
    "scheduled_task": "macOS 可用 launchd / crontab；软件内的定时运行不需要它。",
    "window_ops": "macOS 暂不支持窗口操作步骤，可用坐标点击或 AppleScript 命令替代。",
    "global_hotkey": "macOS 暂不支持全局 F5/F9，请使用窗口内的快捷键。",
    "keyboard_hook": "macOS 暂不支持按键录制，可手动填写热键。",
    "ui_automation": "macOS 暂不支持控件点击，可改用「点击文字」（需要 OCR）。",
}


def available(name: str) -> bool:
    """当前平台是否支持某项能力。"""
    return bool(CAPABILITIES.get(name, False))


def unavailable_text(name: str) -> str:
    """不可用能力的说明（含替代方案），可直接显示在界面上。"""
    base = {
        "system_tray": "系统托盘只支持 Windows。",
        "autostart": "开机自启只支持 Windows。",
        "scheduled_task": "系统计划任务只支持 Windows。",
        "window_ops": "窗口操作只支持 Windows。",
        "global_hotkey": "全局热键只支持 Windows。",
        "keyboard_hook": "按键录制只支持 Windows。",
        "ui_automation": "控件点击只支持 Windows。",
        "applescript": "AppleScript 只支持 macOS。",
    }.get(name, "当前系统不支持该功能。")
    hint = _ALTERNATIVES.get(name, "")
    return f"{base}\n{hint}" if hint else base


# ---------------------------------------------------------------- 修饰键
# pyautogui.hotkey() 与菜单快捷键在 macOS 上要用 command，而不是 ctrl。
MOD_KEY = "command" if IS_MACOS else "ctrl"
MOD_LABEL = "Cmd" if IS_MACOS else "Ctrl"
MOD_SYMBOL = "⌘" if IS_MACOS else "Ctrl"


def hotkey_label(text: str) -> str:
    """把界面上的 Ctrl+Z 这类提示换成当前平台的写法。"""
    if not IS_MACOS:
        return text
    return text.replace("Ctrl+", "Cmd+").replace("Control+", "Cmd+")


def normalize_hotkey(keys: str) -> str:
    """把用户写的热键按平台换算：macOS 下 ctrl/win → command。"""
    if not IS_MACOS:
        return keys
    parts = []
    for key in str(keys or "").split(","):
        name = key.strip().lower()
        if name in ("ctrl", "control", "win"):
            name = "command"
        parts.append(name)
    return ",".join(parts)


# ---------------------------------------------------------------- macOS 权限
def _framework(path: str):
    try:
        lib = ctypes.CDLL(path)
        return lib
    except OSError:
        return None


def _mac_screen_capture_allowed() -> bool:
    """屏幕录制权限（CGPreflightScreenCaptureAccess，10.15+）。"""
    if not IS_MACOS:
        return True
    lib = _framework(
        "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    if lib is None:
        return True                     # 取不到状态时按可用处理，不吓用户
    try:
        lib.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        lib.CGPreflightScreenCaptureAccess.argtypes = []
        return bool(lib.CGPreflightScreenCaptureAccess())
    except AttributeError:
        return True


def _mac_accessibility_allowed() -> bool:
    """辅助功能权限（AXIsProcessTrusted）。"""
    if not IS_MACOS:
        return True
    lib = _framework(
        "/System/Library/Frameworks/ApplicationServices.framework/"
        "ApplicationServices")
    if lib is None:
        return True
    try:
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        lib.AXIsProcessTrusted.argtypes = []
        return bool(lib.AXIsProcessTrusted())
    except AttributeError:
        return True


def permission_status() -> dict:
    """当前平台的权限状态（非 macOS 一律视为已授权）。"""
    if not IS_MACOS:
        return {"screen_recording": True, "accessibility": True}
    return {
        "screen_recording": _mac_screen_capture_allowed(),
        "accessibility": _mac_accessibility_allowed(),
    }


def missing_permissions() -> list[str]:
    """返回缺失的权限名（screen_recording / accessibility）。"""
    status = permission_status()
    return [name for name, ok in status.items() if not ok]


def open_privacy_settings(kind: str) -> bool:
    """打开 macOS 隐私设置对应页；成功返回 True。"""
    if not IS_MACOS:
        return False
    pages = {
        "screen_recording": ("x-apple.systempreferences:com.apple."
                             "preference.security?Privacy_ScreenCapture"),
        "accessibility": ("x-apple.systempreferences:com.apple."
                          "preference.security?Privacy_Accessibility"),
    }
    url = pages.get(kind)
    if not url:
        return False
    try:
        subprocess.run(["open", url], check=False, timeout=10)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def explain_permissions(missing: list[str]) -> str:
    """给用户看的权限说明（含操作路径）。"""
    lines = ["MeowGo 需要以下 macOS 权限才能自动操作屏幕："]
    if "accessibility" in missing:
        lines.append(
            "• 辅助功能：模拟鼠标点击和键盘输入\n"
            "  系统设置 → 隐私与安全性 → 辅助功能，勾选当前终端 / MeowGo")
    if "screen_recording" in missing:
        lines.append(
            "• 屏幕录制：截屏找图\n"
            "  系统设置 → 隐私与安全性 → 屏幕录制，勾选当前终端 / MeowGo")
    lines.append("\n勾选后建议重启一次 MeowGo。")
    return "\n".join(lines)
