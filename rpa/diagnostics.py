# -*- coding: utf-8 -*-
"""导出诊断信息：环境、依赖、配置摘要与最近日志。

给用户报问题时使用：一键生成 zip，里面只有文本和日志，不包含截图、
执行组步骤内容，Webhook / Token 等敏感字段会自动打码。
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import platform
import sys
import time
import zipfile
from collections import deque
from typing import Any, Optional
from urllib.parse import urlsplit

from . import __version__, logs

TAIL_LINES = 200
MAX_RUN_LOGS = 3
# 这些键属于用户流程数据，不进诊断包（可能含账号、密码、业务内容）
PROJECT_KEYS = {"groups", "steps", "order", "columns", "sash",
                "schedule_history"}
SENSITIVE_WORDS = ("webhook", "token", "password", "passwd", "secret",
                   "apikey", "api_key", "access_key", "private_key")

DEPENDENCIES = (
    ("pyautogui", "pyautogui"),
    ("PIL", "pillow"),
    ("cv2", "opencv-python"),
    ("numpy", "numpy"),
    ("pyperclip", "pyperclip"),
    ("xlrd", "xlrd"),
    ("openpyxl", "openpyxl"),
    ("pytesseract", "pytesseract"),
    ("uiautomation", "uiautomation"),
)


def mask_secret(value: Any) -> str:
    """把敏感值变成可辨认但不泄露内容的形式。"""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        parts = None
    if parts is not None and parts.scheme and parts.netloc:
        # URL 只保留协议和主机，路径 / 查询串里常带 token
        return f"{parts.scheme}://{parts.netloc}/***"
    if len(text) <= 8:
        return "***"
    return text[:3] + "***"


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(word in lowered for word in SENSITIVE_WORDS)


def sanitize_settings(settings: Optional[dict]) -> dict:
    """去掉流程数据，并给敏感设置打码；返回可安全写进诊断包的副本。"""
    if not isinstance(settings, dict):
        return {}
    clean: dict[str, Any] = {}
    for key, value in settings.items():
        if key in PROJECT_KEYS:
            continue
        if _is_sensitive(str(key)):
            clean[key] = mask_secret(value)
        elif isinstance(value, dict):
            clean[key] = sanitize_settings(value)
        elif isinstance(value, list):
            clean[key] = [
                sanitize_settings(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            clean[key] = value
    return clean


def _version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "未安装"
    except Exception:  # noqa: BLE001 - 元数据损坏时不影响导出
        return "未知"


def _module_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _dependency_lines() -> list[str]:
    lines = []
    for module, distribution in DEPENDENCIES:
        if _module_available(module):
            lines.append(f"{module}: {_version(distribution)}")
        else:
            lines.append(f"{module}: 未安装")
    return lines


def _display_lines() -> list[str]:
    lines: list[str] = []
    if os.name != "nt":
        return lines
    try:
        import ctypes

        user32 = ctypes.windll.user32
        monitors = user32.GetSystemMetrics(80)      # SM_CMONITORS
        left = user32.GetSystemMetrics(76)          # SM_XVIRTUALSCREEN
        top = user32.GetSystemMetrics(77)
        width = user32.GetSystemMetrics(78)
        height = user32.GetSystemMetrics(79)
        lines.append(f"显示器数量: {monitors}")
        lines.append(f"虚拟桌面: {width}x{height} @ ({left},{top})")
        awareness = ctypes.c_int(0)
        try:
            ctypes.windll.shcore.GetProcessDpiAwareness(
                None, ctypes.byref(awareness))
            names = {0: "unaware", 1: "system", 2: "per-monitor"}
            lines.append(
                f"DPI 感知: {names.get(awareness.value, awareness.value)}")
        except Exception:  # noqa: BLE001 - 老系统没有 shcore
            lines.append("DPI 感知: 未知")
        try:
            lines.append(f"系统 DPI: {user32.GetDpiForSystem()} "
                         f"({user32.GetDpiForSystem() / 96:.0%})")
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001 - 取不到也不影响导出
        pass
    return lines


def _tail(path: str, lines: int = TAIL_LINES) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return "".join(deque(fh, maxlen=lines))
    except OSError:
        return ""


def _file_info(path: str) -> str:
    try:
        size = os.path.getsize(path)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S",
                              time.localtime(os.path.getmtime(path)))
        return f"{size} 字节 · {stamp}"
    except OSError:
        return "不存在"


def _group_stats(settings: Optional[dict]) -> str:
    groups = (settings or {}).get("groups")
    if not isinstance(groups, list):
        return "执行组: 0，步骤: 0"
    total = 0
    disabled = 0
    for group in groups:
        if not isinstance(group, dict):
            continue
        steps = group.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            total += 1
            if not step.get("enabled", True):
                disabled += 1
    return f"执行组: {len(groups)}，步骤: {total}（禁用 {disabled}）"


def _optional_status() -> list[str]:
    lines: list[str] = []
    try:
        from .engine import opencv_available

        lines.append("OpenCV 可用: " + ("是" if opencv_available() else "否"))
    except Exception:  # noqa: BLE001
        lines.append("OpenCV 可用: 未知")
    try:
        from . import ocr

        lines.append("OCR 可用: " + ("是" if ocr.available() else "否"))
    except Exception:  # noqa: BLE001
        lines.append("OCR 可用: 未知")
    try:
        from . import uia

        lines.append("UIA 可用: " + ("是" if uia.available() else "否"))
    except Exception:  # noqa: BLE001
        lines.append("UIA 可用: 未知")
    return lines


def collect(settings: Optional[dict] = None, *, app_dir: str = "",
            image_dir: str = "", report_dir: str = "", log_dir: str = "",
            version: str = "") -> str:
    """生成一份纯文本诊断报告。"""
    app_dir = app_dir or logs.app_dir()
    log_dir = log_dir or logs.log_dir()
    image_dir = image_dir or os.path.join(app_dir, "images")
    report_dir = report_dir or os.path.join(app_dir, "reports")
    version = version or __version__
    try:
        import tkinter

        tk_version = str(tkinter.TkVersion)
    except Exception:  # noqa: BLE001 - 无 Tk 的环境也能导出其余信息
        tk_version = "未安装"

    lines = [
        "MeowGo 诊断信息",
        "=" * 60,
        f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"版本: {version}",
        f"打包运行: {'是' if getattr(sys, 'frozen', False) else '否'}",
        f"Python: {sys.version}",
        f"Python 路径: {sys.executable}",
        f"平台: {platform.platform()}",
        f"架构: {platform.machine()}",
        f"语言/编码: {sys.getfilesystemencoding()}",
        "",
        "[依赖]",
        *_dependency_lines(),
        "tkinter: " + tk_version,
        "",
        "[运行环境]",
        *_optional_status(),
        *_display_lines(),
        "",
        "[路径]",
        f"程序目录: {app_dir}",
        f"配置文件: {os.path.join(app_dir, 'rpa_settings.json')}"
        f"（{_file_info(os.path.join(app_dir, 'rpa_settings.json'))}）",
        f"图片目录: {image_dir}",
        f"报告目录: {report_dir}",
        f"日志目录: {log_dir}",
        "",
        "[配置摘要]",
        _group_stats(settings),
        json.dumps(sanitize_settings(settings), ensure_ascii=False, indent=2,
                   default=str),
        "",
        "[最近日志]",
    ]
    names = ("app.log", "app.log.1", "crash.log")
    for name in names:
        path = os.path.join(log_dir, name)
        lines.append(f"{name}: {_file_info(path)}")
    lines.extend(["", "[app.log 末尾]", _tail(os.path.join(log_dir, "app.log")),
                  "", "[crash.log 末尾]",
                  _tail(os.path.join(log_dir, "crash.log"), 100)])
    return "\n".join(lines)


def _latest_run_logs(log_dir: str, limit: int = MAX_RUN_LOGS) -> list[str]:
    try:
        names = [name for name in os.listdir(log_dir)
                 if name.startswith("run_") and name.endswith(".log")]
    except OSError:
        return []
    paths = [os.path.join(log_dir, name) for name in names]
    paths.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    return paths[:limit]


def write_zip(path: str, settings: Optional[dict] = None, *,
              app_dir: str = "", image_dir: str = "", report_dir: str = "",
              log_dir: str = "", version: str = "") -> str:
    """把诊断报告和最近日志写成 zip，返回实际路径。"""
    log_dir = log_dir or logs.log_dir()
    text = collect(settings, app_dir=app_dir, image_dir=image_dir,
                   report_dir=report_dir, log_dir=log_dir, version=version)
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("diagnostics.txt", text)
        archive.writestr(
            "settings.sanitized.json",
            json.dumps(sanitize_settings(settings), ensure_ascii=False,
                       indent=2, default=str))
        for name in ("app.log", "crash.log"):
            source = os.path.join(log_dir, name)
            if os.path.isfile(source):
                archive.write(source, f"logs/{name}")
        for source in _latest_run_logs(log_dir):
            archive.write(source, f"logs/{os.path.basename(source)}")
    return path
