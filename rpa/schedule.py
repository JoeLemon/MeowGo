# -*- coding: utf-8 -*-
"""定时任务的纯数据逻辑：规范化、下次运行时间与倒计时文案。

从 ``gui.py`` 拆出，方便单独测试和静态检查；界面只负责渲染。
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Optional

from . import i18n


# 运行频率：每天 / 每周某天 / 工作日 / 每 N 小时
MODES = ("daily", "weekly", "weekday", "hourly")
# 周一 = 0，与 datetime.weekday() 一致
WEEKDAY_KEYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _valid_mode(value: object) -> str:
    mode = str(value or "daily").strip().lower()
    return mode if mode in MODES else "daily"


def _valid_weekday(value: object) -> int:
    try:
        day = int(value)                      # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return min(6, max(0, day))


def _valid_interval(value: object) -> int:
    """每 N 小时：限制在 1–24，避免 0 小时导致死循环。"""
    try:
        hours = int(float(value))             # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1
    return min(24, max(1, hours))


def make_schedule_task(time_text: str = "09:00", group: str = "",
                       enabled: bool = True, mode: str = "daily",
                       weekday: int = 0, interval: int = 1) -> dict:
    """生成一条新的定时任务（id 只用于区分同一分钟内的多条任务）。"""
    return {
        "id": f"task-{time.time_ns()}",
        "enabled": bool(enabled),
        "mode": _valid_mode(mode),
        "time": _valid_schedule_time(time_text),
        "weekday": _valid_weekday(weekday),
        "interval": _valid_interval(interval),
        "group": (group or "").strip(),
    }


def _valid_schedule_time(value: object) -> str:
    """把各种输入夹成 HH:MM；非法值退回 09:00。"""
    match = re.match(r"^\s*(\d{1,2})\s*:\s*(\d{1,2})\s*$", str(value or ""))
    if not match:
        return "09:00"
    hour = min(23, max(0, int(match.group(1))))
    minute = min(59, max(0, int(match.group(2))))
    return f"{hour:02d}:{minute:02d}"


def normalize_schedule_task(raw: object,
                            index: int = 0) -> Optional[dict]:
    """把磁盘上的任务字典补齐成可控结构；明显无效的条目返回 None。"""
    if not isinstance(raw, dict):
        return None
    if not raw.get("time") and not raw.get("group") and not raw.get("enabled"):
        return None
    return {
        "id": str(raw.get("id") or f"task-{index + 1}"),
        "enabled": bool(raw.get("enabled", True)),
        "mode": _valid_mode(raw.get("mode")),
        "time": _valid_schedule_time(raw.get("time", "09:00")),
        "weekday": _valid_weekday(raw.get("weekday")),
        "interval": _valid_interval(raw.get("interval")),
        "group": str(raw.get("group") or "").strip(),
    }


def schedule_tasks(settings: dict) -> list[dict]:
    """读取定时任务列表，兼容旧版单条 schedule 配置。"""
    raw = settings.get("schedules")
    tasks: list[dict] = []
    if isinstance(raw, list):
        for index, item in enumerate(raw):
            task = normalize_schedule_task(item, index)
            if task is not None:
                tasks.append(task)
        return tasks
    legacy = settings.get("schedule")
    if isinstance(legacy, dict):
        task = normalize_schedule_task(legacy, 0)
        if task is not None:
            task["id"] = "legacy"
            tasks.append(task)
    return tasks


def schedule_next_run(task: dict,
                      now: Optional[datetime] = None) -> datetime:
    """下一次到点时间（按 mode 计算；已过点则顺延到下一个周期）。"""
    now = now or datetime.now()
    hour, minute = (int(part) for part in task.get("time", "09:00").split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    mode = _valid_mode(task.get("mode"))
    if mode == "hourly":
        step = _valid_interval(task.get("interval"))
        if target > now:
            return target
        elapsed = (now - target).total_seconds()
        steps = int(elapsed // (step * 3600)) + 1
        return target + timedelta(hours=step * steps)
    if mode == "weekly":
        day = _valid_weekday(task.get("weekday"))
        delta = (day - now.weekday()) % 7
        if delta == 0 and target <= now:
            delta = 7
        return target + timedelta(days=delta)
    if mode == "weekday":
        delta = 0
        while True:
            candidate = target + timedelta(days=delta)
            if candidate.weekday() < 5 and candidate > now:
                return candidate
            delta += 1
    if target <= now:
        target += timedelta(days=1)
    return target


def schedule_task_label(task: dict) -> str:
    """任务列表里的一行摘要：每天 09:00 / 周三 09:00 / 每 2 小时。"""
    mode = _valid_mode(task.get("mode"))
    time_text = task.get("time", "09:00")
    if mode == "hourly":
        return f"{i18n.tr('每')} {_valid_interval(task.get('interval'))} " \
               f"{i18n.tr('小时')}"
    if mode == "weekly":
        name = i18n.tr(WEEKDAY_KEYS[_valid_weekday(task.get("weekday"))])
        return f"{name} {time_text}"
    if mode == "weekday":
        return f"{i18n.tr('工作日')} {time_text}"
    return f"{i18n.tr('每天')} {time_text}"


def schedule_countdown_text(target: datetime,
                            now: Optional[datetime] = None) -> str:
    """把剩余时间写成 02:13:45 / 1天 02:13:45。"""
    now = now or datetime.now()
    seconds = max(0, int((target - now).total_seconds()))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    text = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{days}{i18n.tr('天 ')}{text}" if days else text


def schedule_day_text(target: datetime,
                      now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    offset = (target.date() - now.date()).days
    if offset == 0:
        return i18n.tr("今天")
    if offset == 1:
        return i18n.tr("明天")
    return i18n.tr("周" + "一二三四五六日"[target.weekday()])


def schedule_due(task: dict, now: Optional[datetime] = None) -> bool:
    """当前这一分钟是否该触发（软件内定时每秒对表时用）。"""
    now = now or datetime.now()
    hour, minute = (int(part) for part in task.get("time", "09:00").split(":"))
    if now.minute != minute:
        return False
    mode = _valid_mode(task.get("mode"))
    if mode == "hourly":
        step = _valid_interval(task.get("interval"))
        return ((now.hour - hour) % 24) % step == 0
    if now.hour != hour:
        return False
    if mode == "weekly":
        return now.weekday() == _valid_weekday(task.get("weekday"))
    if mode == "weekday":
        return now.weekday() < 5
    return True
