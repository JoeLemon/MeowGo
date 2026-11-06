# -*- coding: utf-8 -*-
"""开机自启：Windows 写注册表 Run 项，macOS 写 LaunchAgent plist。

Windows 用当前用户级 HKCU\\...\\Run；macOS 用
~/Library/LaunchAgents/com.meowgo.autostart.plist。两者都不需要管理员权限，
只负责“登录后打开界面”，不会自动跑任何执行组。

其它平台一律返回“不支持”，不影响导入 engine / gui。
"""
from __future__ import annotations

import os
import subprocess
import sys

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "MeowGo"
LAUNCH_AGENT_LABEL = "com.meowgo.autostart"
NOT_WINDOWS = "开机自启只支持 Windows 和 macOS。"


def _is_windows() -> bool:
    return os.name == "nt"


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _script_path() -> str:
    # autostart.py 在 rpa/ 下，上一级即程序目录（start-app.py 所在处）
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "start-app.py")


def launch_arguments() -> list[str]:
    """启动参数列表（macOS LaunchAgent 用；Windows 也用它拼命令行）。"""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, _script_path()]


def launch_command() -> str:
    """要写进注册表 / plist 的命令行。

    打包后就是 exe 本身；开发运行时优先用同目录的 pythonw.exe（无黑窗），
    与 启动GUI.bat、计划任务命令行（ScheduleDialog._task_command）同款取法。
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    python = sys.executable
    pythonw = os.path.join(os.path.dirname(python), "pythonw.exe")
    if os.path.exists(pythonw):
        python = pythonw
    return f'"{python}" "{_script_path()}"'


# ---------------------------------------------------------------- Windows
def _is_enabled_windows(key_path: str, name: str) -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.QueryValueEx(key, name)
        return True
    except OSError:
        return False


def _current_windows(key_path: str, name: str) -> str:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _kind = winreg.QueryValueEx(key, name)
        return str(value)
    except OSError:
        return ""


def _enable_windows(key_path: str, name: str) -> tuple[bool, str]:
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, launch_command())
        return True, ""
    except OSError as exc:
        return False, f"写入注册表失败：{exc}"


def _disable_windows(key_path: str, name: str) -> tuple[bool, str]:
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


# ------------------------------------------------------------------ macOS
def plist_path() -> str:
    """当前用户的 LaunchAgent plist 路径。"""
    return os.path.join(
        os.path.expanduser("~/Library/LaunchAgents"),
        LAUNCH_AGENT_LABEL + ".plist")


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def plist_text() -> str:
    """LaunchAgent plist 内容（RunAtLoad = 登录后打开界面）。"""
    arguments = "".join(
        f"        <string>{_xml_escape(item)}</string>\n"
        for item in launch_arguments())
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        '<dict>\n'
        f'    <key>Label</key>\n    <string>{LAUNCH_AGENT_LABEL}</string>\n'
        '    <key>ProgramArguments</key>\n    <array>\n'
        f'{arguments}'
        '    </array>\n'
        '    <key>RunAtLoad</key>\n    <true/>\n'
        '</dict>\n'
        '</plist>\n')


def _launchctl(*args: str) -> None:
    """尽力让 plist 立即生效；失败（权限 / 已加载）不影响下次登录生效。"""
    try:
        subprocess.run(["launchctl", *args], check=False, timeout=10,
                       capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pass


def _is_enabled_macos() -> bool:
    return os.path.isfile(plist_path())


def _current_macos() -> str:
    if not os.path.isfile(plist_path()):
        return ""
    return " ".join(launch_arguments())


def _enable_macos() -> tuple[bool, str]:
    path = plist_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(plist_text())
    except OSError as exc:
        return False, f"写入 LaunchAgent 失败：{exc}"
    _launchctl("load", path)
    return True, ""


def _disable_macos() -> tuple[bool, str]:
    path = plist_path()
    if not os.path.isfile(path):
        return True, ""
    _launchctl("unload", path)
    try:
        os.remove(path)
    except OSError as exc:
        return False, f"删除 LaunchAgent 失败：{exc}"
    return True, ""


def is_enabled(key_path: str = RUN_KEY_PATH,
               name: str = VALUE_NAME) -> bool:
    """当前是否已写入开机自启项。"""
    if _is_macos():
        return _is_enabled_macos()
    if not _is_windows():
        return False
    return _is_enabled_windows(key_path, name)


def current_command(key_path: str = RUN_KEY_PATH,
                    name: str = VALUE_NAME) -> str:
    """已注册的启动命令；没有注册时返回空串。"""
    if _is_macos():
        return _current_macos()
    if not _is_windows():
        return ""
    return _current_windows(key_path, name)


def enable(key_path: str = RUN_KEY_PATH,
           name: str = VALUE_NAME) -> tuple[bool, str]:
    """写入开机自启项。返回 (是否成功, 错误信息)。"""
    if _is_macos():
        return _enable_macos()
    if not _is_windows():
        return False, NOT_WINDOWS
    return _enable_windows(key_path, name)


def disable(key_path: str = RUN_KEY_PATH,
            name: str = VALUE_NAME) -> tuple[bool, str]:
    """移除开机自启项；本来没有也算成功。返回 (是否成功, 错误信息)。"""
    if _is_macos():
        return _disable_macos()
    if not _is_windows():
        return False, NOT_WINDOWS
    return _disable_windows(key_path, name)
