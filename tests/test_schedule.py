# -*- coding: utf-8 -*-
"""定时任务纯逻辑：时间规范化、旧配置兼容、倒计时文案。"""
from datetime import datetime

import pytest

from rpa import i18n
from rpa.schedule import (
    _valid_schedule_time,
    make_schedule_task,
    normalize_schedule_task,
    schedule_countdown_text,
    schedule_day_text,
    schedule_next_run,
    schedule_tasks,
)

pytestmark = pytest.mark.engine


def test_valid_schedule_time_clamps_and_falls_back():
    assert _valid_schedule_time(" 7:5 ") == "07:05"
    assert _valid_schedule_time("23:59") == "23:59"
    assert _valid_schedule_time("99:99") == "23:59"
    assert _valid_schedule_time("") == "09:00"
    assert _valid_schedule_time("not-a-time") == "09:00"


def test_make_and_normalize_task():
    task = make_schedule_task("8:3", "  登录  ", enabled=0)
    assert task["time"] == "08:03"
    assert task["group"] == "登录"
    assert task["enabled"] is False
    assert task["id"].startswith("task-")

    assert normalize_schedule_task("bad") is None
    assert normalize_schedule_task({}) is None
    normalized = normalize_schedule_task(
        {"time": "25:70", "group": " 处理 ", "enabled": 0}, 2)
    assert normalized == {"id": "task-3", "enabled": False, "mode": "daily",
                          "time": "23:59", "weekday": 0, "interval": 1,
                          "group": "处理"}
    # 旧配置没有 mode / weekday / interval：按每天 09:00 处理
    legacy = normalize_schedule_task({"time": "07:30", "group": "早班"})
    assert (legacy["mode"], legacy["weekday"], legacy["interval"]) == \
        ("daily", 0, 1)
    # 频率字段非法时夹到合法范围
    weird = normalize_schedule_task({"time": "07:30", "mode": "hourly",
                                     "weekday": 99, "interval": 0})
    assert (weird["weekday"], weird["interval"]) == (6, 1)


def test_schedule_tasks_prefers_list_and_supports_legacy():
    listed = schedule_tasks({"schedules": [
        {"time": "07:00", "group": "早班"},
        "坏数据",
        {},
    ]})
    assert listed == [{"id": "task-1", "enabled": True, "mode": "daily",
                       "time": "07:00", "weekday": 0, "interval": 1,
                       "group": "早班"}]

    legacy = schedule_tasks({"schedule": {
        "time": "18:30", "group": "晚班", "enabled": True}})
    assert legacy[0]["id"] == "legacy"
    assert legacy[0]["time"] == "18:30" and legacy[0]["group"] == "晚班"
    assert schedule_tasks({}) == []


def test_next_run_rolls_to_tomorrow():
    now = datetime(2026, 11, 2, 8, 0, 0)
    assert schedule_next_run({"time": "09:00"}, now) == \
        datetime(2026, 11, 2, 9, 0, 0)
    assert schedule_next_run({"time": "08:00"}, now) == \
        datetime(2026, 11, 3, 8, 0, 0)


def test_next_run_by_mode():
    """每周 / 工作日 / 每 N 小时的下一次运行时间。"""
    from rpa.schedule import schedule_task_label

    # 2026-11-02 是周一
    monday = datetime(2026, 11, 2, 8, 0, 0)
    weekly = {"time": "09:00", "mode": "weekly", "weekday": 2}   # 周三
    assert schedule_next_run(weekly, monday) == datetime(2026, 11, 4, 9, 0)
    # 当天已过点：顺延一周
    passed = {"time": "07:00", "mode": "weekly", "weekday": 0}
    assert schedule_next_run(passed, monday) == datetime(2026, 11, 9, 7, 0)

    # 工作日：周六 / 周日跳到下周一
    saturday = datetime(2026, 11, 7, 10, 0, 0)
    workday = {"time": "09:00", "mode": "weekday"}
    assert schedule_next_run(workday, saturday) == datetime(2026, 11, 9, 9, 0)
    # 工作日当天未过点：就是今天
    assert schedule_next_run(workday, monday) == datetime(2026, 11, 2, 9, 0)

    # 每 2 小时：从 09:00 起算
    hourly = {"time": "09:00", "mode": "hourly", "interval": 2}
    assert schedule_next_run(hourly, datetime(2026, 11, 2, 8, 0)) == \
        datetime(2026, 11, 2, 9, 0)
    assert schedule_next_run(hourly, datetime(2026, 11, 2, 10, 30)) == \
        datetime(2026, 11, 2, 11, 0)
    assert schedule_next_run(hourly, datetime(2026, 11, 2, 23, 30)) == \
        datetime(2026, 11, 3, 1, 0)

    # 列表摘要
    saved = i18n.get_language()
    i18n.set_language("zh")
    try:
        assert schedule_task_label({"time": "09:00"}) == "每天 09:00"
        assert schedule_task_label(weekly) == "周三 09:00"
        assert schedule_task_label(workday) == "工作日 09:00"
        assert schedule_task_label(hourly) == "每 2 小时"
    finally:
        i18n.set_language(saved)


def test_countdown_and_day_text():
    saved = i18n.get_language()
    i18n.set_language("zh")
    try:
        now = datetime(2026, 11, 2, 10, 0, 0)
        assert schedule_countdown_text(
            datetime(2026, 11, 2, 10, 2, 3), now) == "00:02:03"
        assert schedule_countdown_text(
            datetime(2026, 11, 3, 12, 3, 4), now) == "1天 02:03:04"
        # 已经过点（理论上不会发生）也不显示负数
        assert schedule_countdown_text(
            datetime(2026, 11, 2, 9, 0, 0), now) == "00:00:00"
        assert schedule_day_text(datetime(2026, 11, 2, 20, 0), now) == "今天"
        assert schedule_day_text(datetime(2026, 11, 3, 20, 0), now) == "明天"
        assert schedule_day_text(datetime(2026, 11, 4, 20, 0), now) == "周三"
    finally:
        i18n.set_language(saved)
