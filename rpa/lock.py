# -*- coding: utf-8 -*-
"""轻量跨进程锁：防止开两个界面、或两次自动运行同时抢鼠标。

Windows 下用命名互斥（Local\\ 作用域，不需要管理员权限）；
其它平台直接放行（本项目主要面向 Windows）。
"""
from __future__ import annotations

import hashlib
import os


def make_lock_name(app_dir: str, prefix: str) -> str:
    """按程序目录生成互斥名：不同副本互不影响。"""
    digest = hashlib.md5(
        os.path.abspath(app_dir).encode("utf-8", "replace")).hexdigest()[:10]
    return f"Local\\MeowGo_{prefix}_{digest}"


class InstanceLock:
    """命名互斥锁；acquire() 返回 False 表示已有同名的实例在跑。"""

    def __init__(self, name: str):
        self.name = name
        self._handle = None

    def acquire(self) -> bool:
        if os.name != "nt":
            return True
        if self._handle:
            return True              # 同一个对象重复 acquire 不重复打开句柄
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = [
                wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.CreateMutexW(None, False, self.name)
            if not handle:
                return True          # 创建失败就别拦着用户
            if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
                kernel32.CloseHandle(handle)    # 只是打开了别人的锁，关掉
                self._handle = None
                return False
            self._handle = handle
            return True
        except Exception:            # noqa: BLE001 - 锁失败不应阻止启动
            return True

    def release(self) -> None:
        if not self._handle or os.name != "nt":
            self._handle = None
            return
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32")
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(self._handle)
        except Exception:            # noqa: BLE001
            pass
        self._handle = None

    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
