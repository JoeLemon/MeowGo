# -*- coding: utf-8 -*-
"""敏感配置（Webhook / Token）加密存储。

macOS 用「钥匙串」（security 命令）；Windows 用 DPAPI（当前用户绑定）。
取不到系统能力时退回明文保存，保证旧配置和低权限环境仍然可用。

设置文件里不再保留明文：保存时写成空值 + `<键>_stored = "keychain"`，
读取时再从系统凭据库取回。整个过程对界面透明。
"""
from __future__ import annotations

import base64
import ctypes
import os
import subprocess
import sys
from typing import Optional

SERVICE = "MeowGo"
SENSITIVE_KEYS = ("webhook", "token", "password", "secret")
MARKER = "keychain"
_CACHE: dict[tuple[str, str], str] = {}


def available() -> bool:
    """当前系统是否支持安全存储。"""
    if sys.platform == "darwin":
        return os.path.isfile("/usr/bin/security")
    return os.name == "nt"


# ------------------------------------------------------------------ macOS
def _mac_store(key: str, value: str) -> bool:
    try:
        subprocess.run(
            ["security", "add-generic-password", "-U", "-a", key,
             "-s", SERVICE, "-w", value],
            check=True, timeout=10, capture_output=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _mac_load(key: str) -> str:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", key,
             "-s", SERVICE, "-w"],
            check=True, timeout=10, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (result.stdout or "").strip()


def _mac_delete(key: str) -> None:
    try:
        subprocess.run(
            ["security", "delete-generic-password", "-a", key,
             "-s", SERVICE],
            check=False, timeout=10, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pass


# ---------------------------------------------------------------- Windows
class _DataBlob(ctypes.Structure):
    # Win32 DWORD 固定 32 位；用 c_ulong 会在 64 位进程里变成 8 字节，
    # 传给 CryptProtectData 时会把结构体布局弄错。
    _fields_ = [("cbData", ctypes.c_uint32),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _windows_store(key: str, value: str) -> bool:
    try:
        crypt32 = ctypes.windll.crypt32
        blob_in = _DataBlob(len(value.encode("utf-8")),
                            ctypes.cast(
                                ctypes.create_string_buffer(
                                    value.encode("utf-8")),
                                ctypes.POINTER(ctypes.c_char)))
        blob_out = _DataBlob()
        if not crypt32.CryptProtectData(
                ctypes.byref(blob_in), None, None, None, None, 0,
                ctypes.byref(blob_out)):
            return False
        try:
            raw = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        _CACHE[(SERVICE, key)] = value
        _write_windows_blob(key, base64.b64encode(raw).decode("ascii"))
        return True
    except (OSError, AttributeError, ValueError):
        return False


def _windows_blob_path(key: str) -> str:
    return os.path.join(os.path.expanduser("~"), ".meowgo",
                        f"{key}.secret")


def _write_windows_blob(key: str, payload: str) -> None:
    path = _windows_blob_path(key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="ascii") as fh:
        fh.write(payload)


def _windows_load(key: str) -> str:
    try:
        path = _windows_blob_path(key)
        with open(path, "r", encoding="ascii") as fh:
            payload = base64.b64decode(fh.read().strip())
        crypt32 = ctypes.windll.crypt32
        blob_in = _DataBlob(
            len(payload),
            ctypes.cast(ctypes.create_string_buffer(payload),
                        ctypes.POINTER(ctypes.c_char)))
        blob_out = _DataBlob()
        if not crypt32.CryptUnprotectData(
                ctypes.byref(blob_in), None, None, None, None, 0,
                ctypes.byref(blob_out)):
            return ""
        try:
            raw = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return raw.decode("utf-8", "replace")
    except (OSError, AttributeError, ValueError):
        return ""


def _windows_delete(key: str) -> None:
    try:
        os.remove(_windows_blob_path(key))
    except OSError:
        pass


# ------------------------------------------------------------------ 对外
def store(key: str, value: str) -> bool:
    """写入系统凭据库；值没变时直接复用缓存，避免每 2 秒重复写。"""
    if not value or not available():
        return False
    if _CACHE.get((SERVICE, key)) == value:
        return True
    if sys.platform == "darwin":
        ok = _mac_store(key, value)
    else:
        ok = _windows_store(key, value)
    if ok:
        _CACHE[(SERVICE, key)] = value
    return ok


def load(key: str) -> str:
    """从系统凭据库读取；取不到返回空串。"""
    if not available():
        return ""
    cached = _CACHE.get((SERVICE, key))
    if cached is not None:
        return cached
    value = _mac_load(key) if sys.platform == "darwin" else _windows_load(key)
    if value:
        _CACHE[(SERVICE, key)] = value
    return value


def delete(key: str) -> None:
    _CACHE.pop((SERVICE, key), None)
    if sys.platform == "darwin":
        _mac_delete(key)
    elif os.name == "nt":
        _windows_delete(key)


def protect_settings(settings: dict) -> dict:
    """保存前：把敏感项挪进凭据库，返回文件里应写的副本。"""
    data = dict(settings)
    for key in SENSITIVE_KEYS:
        value = str(data.get(key, "") or "").strip()
        if value and store(key, value):
            data[key] = ""
            data[f"{key}_stored"] = MARKER
    return data


def restore_settings(settings: dict) -> dict:
    """读取后：把凭据库里的敏感项填回内存副本。"""
    data = dict(settings)
    for key in SENSITIVE_KEYS:
        if data.get(key):
            continue
        if data.get(f"{key}_stored") == MARKER:
            value = load(key)
            if value:
                data[key] = value
    return data


def clear_marker(data: dict) -> dict:
    """用户主动清空敏感项时，同步删除凭据库里的旧值。"""
    for key in SENSITIVE_KEYS:
        if not str(data.get(key, "") or "").strip():
            if data.get(f"{key}_stored") == MARKER:
                delete(key)
                data.pop(f"{key}_stored", None)
    return data


def cache_get(key: str) -> Optional[str]:
    """测试 / 诊断用：查看内存缓存（不触发系统调用）。"""
    return _CACHE.get((SERVICE, key))
