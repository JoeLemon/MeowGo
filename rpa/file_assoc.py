# -*- coding: utf-8 -*-
"""Windows 文件关联：把 .mgproj 双击交给 MeowGo 打开。

只写 HKCU\\Software\\Classes，不需要管理员权限，也不改系统默认程序提示；
取消关联时只删自己写的那几个键，不碰别的程序。

macOS / Linux 不做注册：macOS 需要 bundle 的 Info.plist（打包时才有），
Linux 走桌面环境的 MIME 配置，这里先不做，界面会给说明。
"""
from __future__ import annotations

import os
import sys

from . import platform_support
from .project_io import PROJECT_EXT

PROG_ID = "MeowGo.Project"
CLASSES = r"Software\Classes"


def _app_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def open_command(exe: str = "", script: str = "") -> str:
    """双击文件时执行的命令行（带 %1 占位符）。

    打包后是 MeowGo.exe；源码运行时优先 pythonw.exe + start-app.py，
    这样双击不会弹出黑色控制台窗口。
    """
    if exe:
        return f'"{exe}" "%1"'
    python = sys.executable
    pythonw = os.path.join(os.path.dirname(python), "pythonw.exe")
    if os.path.exists(pythonw):
        python = pythonw
    target = script or os.path.join(_app_dir(), "start-app.py")
    return f'"{python}" "{target}" "%1"'


def _executable() -> str:
    """当前运行方式对应的程序路径（打包 / 源码）。"""
    if getattr(sys, "frozen", False):
        return sys.executable
    return ""


def _winreg():
    import winreg

    return winreg


def is_registered() -> bool:
    """当前用户是否已经把 .mgproj 指到 MeowGo。"""
    if not platform_support.IS_WINDOWS:
        return False
    try:
        winreg = _winreg()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            rf"{CLASSES}\{PROJECT_EXT}") as key:
            value, _kind = winreg.QueryValueEx(key, "")
        return str(value) == PROG_ID
    except OSError:
        return False


def register() -> tuple[bool, str]:
    """写入 ProgID 与 .mgproj 关联；返回 (是否成功, 错误信息)。"""
    if not platform_support.IS_WINDOWS:
        return False, "文件关联只支持 Windows。"
    exe = _executable()
    command = open_command(exe=exe)
    try:
        winreg = _winreg()
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              rf"{CLASSES}\{PROG_ID}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "MeowGo 流程")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              rf"{CLASSES}\{PROG_ID}\DefaultIcon") as key:
            icon = exe or os.path.join(_app_dir(), "build_assets", "app.ico")
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, icon)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              rf"{CLASSES}\{PROG_ID}\shell\open\command"
                              ) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              rf"{CLASSES}\{PROJECT_EXT}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, PROG_ID)
            winreg.SetValueEx(key, "Content Type", 0, winreg.REG_SZ,
                              "application/x-meowgo-project")
        with winreg.CreateKey(
                winreg.HKEY_CURRENT_USER,
                rf"{CLASSES}\{PROJECT_EXT}\OpenWithProgids") as key:
            winreg.SetValueEx(key, PROG_ID, 0, winreg.REG_SZ, "")
    except OSError as exc:
        return False, f"写入注册表失败：{exc}"
    return True, ""


def unregister() -> tuple[bool, str]:
    """删除自己写的关联键（没装过也算成功）。"""
    if not platform_support.IS_WINDOWS:
        return False, "文件关联只支持 Windows。"
    winreg = _winreg()
    ok = True
    for path in (rf"{CLASSES}\{PROJECT_EXT}", rf"{CLASSES}\{PROG_ID}"):
        for _ in range(8):          # 逐层删除子键，最多 8 层足够
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
            except OSError:
                break
            except Exception:       # noqa: BLE001 - 权限异常不致命
                ok = False
                break
    return ok, "" if ok else "部分注册表键删除失败（可能被其它程序占用）。"
