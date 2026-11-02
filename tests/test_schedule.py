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
    assert normalized == {"id": "task-3", "enabled": False,
                          "time": "23:59", "group": "处理"}


def test_schedule_tasks_prefers_list_and_supports_legacy():
    listed = schedule_tasks({"schedules": [
        {"time": "07:00", "group": "早班"},
        "坏数据",
        {},
    ]})
    assert listed == [{"id": "task-1", "enabled": True,
                       "time": "07:00", "group": "早班"}]

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
