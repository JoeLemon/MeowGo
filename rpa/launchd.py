# -*- coding: utf-8 -*-
"""macOS 定时任务：用 LaunchAgent（launchd）在指定时间无头运行流程。

与 Windows 的 schtasks 计划任务对应：任务由系统负责启动，
即使 MeowGo 界面没开也会按时执行。只负责纯文本生成与安装 / 卸载，
界面调用这些函数即可。
"""
from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timedelta
from typing import Iterable

LABEL_PREFIX = "com.meowgo.task."


def task_label(name: str) -> str:
    """把任务名转成合法的 launchd label。"""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", name or "").strip("-")
    return LABEL_PREFIX + (safe or "task")


def plist_path(name: str) -> str:
    return os.path.join(
        os.path.expanduser("~/Library/LaunchAgents"),
        task_label(name) + ".plist")


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def task_plist(name: str, arguments: Iterable[str],
               hour: int, minute: int) -> str:
    """生成每天定点运行的 LaunchAgent plist 文本。"""
    args = "".join(
        f"        <string>{_xml_escape(str(item))}</string>\n"
        for item in arguments)
    hour = max(0, min(23, int(hour)))
    minute = max(0, min(59, int(minute)))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        '<dict>\n'
        f'    <key>Label</key>\n    <string>{task_label(name)}</string>\n'
        '    <key>ProgramArguments</key>\n    <array>\n'
        f'{args}'
        '    </array>\n'
        '    <key>StartCalendarInterval</key>\n    <dict>\n'
        f'        <key>Hour</key>\n        <integer>{hour}</integer>\n'
        f'        <key>Minute</key>\n        <integer>{minute}</integer>\n'
        '    </dict>\n'
        '    <key>RunAtLoad</key>\n    <false/>\n'
        '</dict>\n'
        '</plist>\n')


def _launchctl(*args: str) -> None:
    try:
        subprocess.run(["launchctl", *args], check=False, timeout=10,
                       capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pass


def installed(name: str) -> bool:
    return os.path.isfile(plist_path(name))


def install(name: str, arguments: Iterable[str], hour: int,
            minute: int) -> tuple[bool, str]:
    """写入并加载 LaunchAgent；返回 (是否成功, 错误信息)。"""
    path = plist_path(name)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(task_plist(name, arguments, hour, minute))
    except OSError as exc:
        return False, f"写入 LaunchAgent 失败：{exc}"
    _launchctl("unload", path)       # 覆盖同名任务前先卸载旧定义
    _launchctl("load", path)
    return True, ""


def uninstall(name: str) -> tuple[bool, str]:
    """卸载并删除任务；本来没有也算成功。"""
    path = plist_path(name)
    if not os.path.isfile(path):
        return True, ""
    _launchctl("unload", path)
    try:
        os.remove(path)
    except OSError as exc:
        return False, f"删除 LaunchAgent 失败：{exc}"
    return True, ""


def next_run(hour: int, minute: int, now: datetime | None = None) -> str:
    """下一次到点时间（今天已过则算明天），格式与 Windows 任务查询一致。"""
    now = now or datetime.now()
    target = now.replace(hour=max(0, min(23, int(hour))),
                         minute=max(0, min(59, int(minute))),
                         second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target.strftime("%Y-%m-%d %H:%M:%S")
