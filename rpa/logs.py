# -*- coding: utf-8 -*-
"""应用日志：崩溃兜底 + 每次运行的过程日志。

- ``setup()``：配置 app.log（滚动）、安装未捕获异常钩子、开启 faulthandler；
- ``install_tk_handler()``：接管 Tk 回调异常，避免异常只写进空的 stderr；
- ``RunLog``：一次运行一个文件，记录时间戳 / 级别 / 当前步骤。
"""
from __future__ import annotations

import faulthandler
import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from typing import Optional

APP_TITLE = "MeowGo"
_logger = logging.getLogger("meowgo")
_setup_done = False
_faulthandler_file = None
_dialog_shown: set[tuple[str, str]] = set()
_dialog_lock = threading.Lock()

LEVELS = {"debug": "DEBUG", "muted": "DEBUG", "info": "INFO",
          "warn": "WARNING", "error": "ERROR"}


def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def log_dir() -> str:
    return os.path.join(app_dir(), "logs")


def setup(level: int = logging.INFO) -> logging.Logger:
    """初始化应用日志；重复调用只生效一次。"""
    global _setup_done, _faulthandler_file
    if _setup_done:
        return _logger
    _setup_done = True
    try:
        os.makedirs(log_dir(), exist_ok=True)
        handler = RotatingFileHandler(
            os.path.join(log_dir(), "app.log"), maxBytes=2 * 1024 * 1024,
            backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        root = logging.getLogger()
        root.setLevel(level)
        root.addHandler(handler)
        if sys.stderr is not None:
            stream = logging.StreamHandler()
            stream.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)-7s %(message)s"))
            root.addHandler(stream)
    except OSError:
        pass                       # 日志目录不可写时不能影响主流程

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    try:
        _faulthandler_file = open(os.path.join(log_dir(), "crash.log"),
                                  "a", encoding="utf-8")
        faulthandler.enable(file=_faulthandler_file, all_threads=True)
    except (OSError, ValueError):
        _faulthandler_file = None
    _logger.info("%s 启动：%s", APP_TITLE, sys.executable)
    return _logger


def get_logger(name: str = "") -> logging.Logger:
    return logging.getLogger(f"meowgo.{name}" if name else "meowgo")


def _show_crash_dialog(exc_type, exc) -> None:
    """同一个错误只弹一次，避免 Tk 回调反复触发时满屏对话框。"""
    key = (exc_type.__name__, str(exc))
    with _dialog_lock:
        if key in _dialog_shown:
            return
        _dialog_shown.add(key)
    try:
        import tkinter as tk
        from tkinter import messagebox

        if getattr(tk, "_default_root", None) is None:
            return
        messagebox.showerror(
            "程序遇到错误",
            f"{exc_type.__name__}: {exc}\n\n"
            f"详细信息已写入：{os.path.join(log_dir(), 'app.log')}")
    except Exception:  # noqa: BLE001 - 弹窗失败时至少日志里有
        pass


def _excepthook(exc_type, exc, tb) -> None:
    _logger.critical("未捕获异常", exc_info=(exc_type, exc, tb))
    if sys.stderr is None:
        _show_crash_dialog(exc_type, exc)


def _thread_excepthook(args) -> None:
    if args.exc_type is SystemExit:
        return
    name = getattr(args.thread, "name", "?")
    _logger.critical("线程 %s 未捕获异常", name,
                     exc_info=(args.exc_type, args.exc_value,
                               args.exc_traceback))


def install_tk_handler(root) -> None:
    """Tk 回调里的异常走日志 + 一次性提示，而不是消失。"""
    def handler(exc_type, exc, tb):
        _logger.error("界面回调异常", exc_info=(exc_type, exc, tb))
        _show_crash_dialog(exc_type, exc)

    try:
        root.report_callback_exception = handler
    except Exception:  # noqa: BLE001
        pass


class RunLog:
    """一次运行一个日志文件：时间戳 + 级别 + 步骤号。"""

    def __init__(self, directory: str, title: str = "") -> None:
        self.path = ""
        self._fh = None
        self._lock = threading.Lock()
        try:
            os.makedirs(directory, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            base = os.path.join(directory, f"run_{stamp}")
            path = base + ".log"
            index = 2
            while os.path.exists(path):
                path = f"{base}_{index}.log"
                index += 1
            self._fh = open(path, "w", encoding="utf-8")
            self.path = path
        except OSError:
            self._fh = None
            return
        self.write("info", f"开始运行：{title}" if title else "开始运行")

    @property
    def opened(self) -> bool:
        return self._fh is not None

    def write(self, level: str, message: str,
              step: Optional[tuple[str, int]] = None) -> None:
        if self._fh is None:
            return
        prefix = ""
        if step is not None:
            group, index = step
            prefix = f"[{group} 第{index + 1}步] "
        line = (f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"{LEVELS.get(level, level.upper()):<7} {prefix}{message}\n")
        try:
            with self._lock:
                self._fh.write(line)
                self._fh.flush()
        except (OSError, ValueError):
            self._fh = None

    def step_start(self, group: str, index: int, label: str,
                   content: str) -> None:
        detail = f"（{content}）" if content else ""
        self.write("info", f"▶ {label}{detail}", (group, index))

    def close(self, summary: str = "") -> None:
        if self._fh is None:
            return
        if summary:
            self.write("info", summary)
        try:
            with self._lock:
                self._fh.close()
        except OSError:
            pass
        self._fh = None
