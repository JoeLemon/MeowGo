# -*- coding: utf-8 -*-
"""开机自启：在 Windows 注册表 Run 项写入 / 移除 MeowGo 的启动命令。

当前用户级（HKCU\\...\\Run），不需要管理员权限。与「定时运行」里的
Windows 计划任务互补：计划任务负责“到点无头跑流程”，本模块负责
“登录 Windows 后打开界面”，只开界面、不会自动跑任何执行组。

非 Windows 平台一律返回“不支持”，不影响其它平台导入 engine / gui。
"""
from __future__ import annotations

import os
import sys

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "MeowGo"

NOT_WINDOWS = "开机自启只支持 Windows。"


def launch_command() -> str:
    """要写进注册表的命令行。

    打包后就是 exe 本身；开发运行时优先用同目录的 pythonw.exe（无黑窗），
    与 启动GUI.bat、计划任务命令行（ScheduleDialog._task_command）同款取法。
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    python = sys.executable
    pythonw = os.path.join(os.path.dirname(python), "pythonw.exe")
    if os.path.exists(pythonw):
        python = pythonw
    # autostart.py 在 rpa/ 下，上一级即程序目录（start-app.py 所在处）
    script = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "start-app.py")
    return f'"{python}" "{script}"'


def is_enabled(key_path: str = RUN_KEY_PATH,
               name: str = VALUE_NAME) -> bool:
    """当前是否已写入开机自启项。"""
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.QueryValueEx(key, name)
        return True
    except OSError:
        return False


def current_command(key_path: str = RUN_KEY_PATH,
                    name: str = VALUE_NAME) -> str:
    """已注册的启动命令；没有注册时返回空串。"""
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _kind = winreg.QueryValueEx(key, name)
        return str(value)
    except OSError:
        return ""


def enable(key_path: str = RUN_KEY_PATH,
           name: str = VALUE_NAME) -> tuple[bool, str]:
    """写入开机自启项。返回 (是否成功, 错误信息)。"""
    if os.name != "nt":
        return False, NOT_WINDOWS
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, launch_command())
        return True, ""
    except OSError as exc:
        return False, f"写入注册表失败：{exc}"


def disable(key_path: str = RUN_KEY_PATH,
            name: str = VALUE_NAME) -> tuple[bool, str]:
    """移除开机自启项；本来没有也算成功。返回 (是否成功, 错误信息)。"""
    if os.name != "nt":
        return False, NOT_WINDOWS
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
        return True, ""
    except FileNotFoundError:
        return True, ""          # 没有这一项，视为已关闭
    except OSError as exc:
        return False, f"删除注册表项失败：{exc}"
