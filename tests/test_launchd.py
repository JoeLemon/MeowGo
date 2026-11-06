# -*- coding: utf-8 -*-
"""macOS LaunchAgent 定时任务：plist 生成、安装 / 卸载与下次时间。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa import launchd  # noqa: E402


def test_task_plist_contains_schedule_and_arguments():
    text = launchd.task_plist(
        "MeowGo_流程", ["/usr/bin/python3", "start-app.py", "--run", "主流程"],
        7, 5)
    assert launchd.task_label("MeowGo_流程") == "com.meowgo.task.MeowGo_"
    assert "<key>Hour</key>" in text and "<integer>7</integer>" in text
    assert "<key>Minute</key>" in text and "<integer>5</integer>" in text
    assert "<key>StartCalendarInterval</key>" in text
    assert "--run" in text and "主流程" in text
    assert "com.meowgo.task.MeowGo_" in text


def test_install_and_uninstall(monkeypatch, tmp_path):
    target = tmp_path / "MeowGo_test.plist"
    monkeypatch.setattr(launchd, "plist_path", lambda _name: str(target))
    monkeypatch.setattr(launchd, "_launchctl", lambda *args: None)
    name = "MeowGo_主流程"
    ok, error = launchd.install(
        name, ["/usr/bin/python3", "start-app.py", "--run", "主流程"], 9, 30)
    assert ok, error
    path = str(target)
    assert os.path.isfile(path)
    assert launchd.installed(name)

    ok, error = launchd.uninstall(name)
    assert ok, error
    assert not os.path.exists(path)
    assert not launchd.installed(name)
    # 重复卸载也算成功
    assert launchd.uninstall(name) == (True, "")


def test_next_run_rolls_to_tomorrow():
    from datetime import datetime

    now = datetime(2026, 11, 5, 10, 0, 0)
    assert launchd.next_run(9, 30, now) == "2026-11-06 09:30:00"
    assert launchd.next_run(10, 30, now) == "2026-11-05 10:30:00"
    # 越界输入会被夹到合法范围（99 点 → 23，-1 分 → 0）
    assert launchd.next_run(99, -1, now) == "2026-11-05 23:00:00"
