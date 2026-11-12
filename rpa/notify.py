# -*- coding: utf-8 -*-
"""系统通知：运行结果推送到操作系统的通知中心 / 通知气泡。

三端都不引入第三方依赖：
    Windows  PowerShell + WinRT 的 Toast 通知（Win10 / 11）；
    macOS    AppleScript 的 display notification；
    Linux    notify-send，缺失时退回 zenity。

调用方只需 ``send(标题, 正文)``，失败时返回 False（不抛异常），
通知发不出去不应影响主流程。
"""
from __future__ import annotations

import base64
import shutil
import subprocess
import sys

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"

# Windows Toast 用 Windows PowerShell 的 AppUserModelID：系统已登记过，
# 不需要我们额外创建开始菜单快捷方式也能正常显示。
_WINDOWS_AUMID = ("{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\"
                  "WindowsPowerShell\\v1.0\\powershell.exe")


def _no_window_flags() -> int:
    """Windows 上隐藏子进程控制台窗口，避免弹黑框。"""
    if not IS_WINDOWS:
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _powershell_quote(text: str) -> str:
    """PowerShell 单引号字符串转义（单引号写两遍）。"""
    return "'" + str(text).replace("'", "''") + "'"


def _apple_quote(text: str) -> str:
    """AppleScript 字符串转义。"""
    escaped = (str(text).replace("\\", "\\\\").replace('"', '\\"'))
    return f'"{escaped}"'


def _windows_toast(title: str, message: str) -> bool:
    """用 PowerShell 调用 WinRT 发一条 Toast 通知。"""
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "[Windows.UI.Notifications.ToastNotificationManager, "
        "Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null",
        "$template = [Windows.UI.Notifications.ToastNotificationManager]"
        "::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02)",
        "$texts = $template.GetElementsByTagName('text')",
        "$texts.Item(0).AppendChild("
        f"$template.CreateTextNode({_powershell_quote(title)})) | Out-Null",
        "$texts.Item(1).AppendChild("
        f"$template.CreateTextNode({_powershell_quote(message)})) | Out-Null",
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($template)",
        "[Windows.UI.Notifications.ToastNotificationManager]"
        f"::CreateToastNotifier({_powershell_quote(_WINDOWS_AUMID)})"
        ".Show($toast)",
    ])
    # -EncodedCommand 用 UTF-16LE + Base64，彻底绕开中文与引号的转义问题
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
            capture_output=True, timeout=20,
            creationflags=_no_window_flags())
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _macos_notify(title: str, message: str) -> bool:
    """osascript 发通知（与界面里托盘气泡用的是同一条通道）。"""
    script = (f"display notification {_apple_quote(message)} "
              f"with title {_apple_quote(title)}")
    try:
        completed = subprocess.run(
            ["osascript", "-e", script], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _linux_notify(title: str, message: str) -> bool:
    """优先 notify-send；桌面环境没有时退回 zenity。"""
    if shutil.which("notify-send"):
        try:
            completed = subprocess.run(
                ["notify-send", "-a", "MeowGo", title, message],
                capture_output=True, timeout=10)
            if completed.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    if shutil.which("zenity"):
        try:
            completed = subprocess.run(
                ["zenity", "--notification", "--text", f"{title}\n{message}"],
                capture_output=True, timeout=10)
            return completed.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    return False


def send(title: str, message: str = "") -> bool:
    """发一条系统通知；成功返回 True。

    该函数是阻塞的（Windows 上约需 1 秒启动 PowerShell），
    界面里请放到后台线程调用；命令行退出前直接调用即可。
    """
    title = str(title or "MeowGo").strip()[:120]
    message = str(message or "").strip()[:500]
    if IS_WINDOWS:
        return _windows_toast(title, message)
    if IS_MACOS:
        return _macos_notify(title, message)
    return _linux_notify(title, message)
