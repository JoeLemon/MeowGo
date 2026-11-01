# -*- coding: utf-8 -*-
"""macOS 启动冒烟：构建主窗口、日志 Listbox 可用、不依赖 tk.Text。"""
import os
import sys
import tkinter as tk

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa import gui  # noqa: E402

pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(sys.platform != "darwin", reason="仅 macOS 启动冒烟"),
]


def test_app_builds_with_listbox_log(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(gui, "REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(gui, "SHOTS_DIR", str(tmp_path / "reports" / "shots"))
    monkeypatch.setenv("MEOWGO_NO_ONBOARD", "1")
    monkeypatch.setenv("MEOWGO_NO_PERMISSION_PROMPT", "1")

    app = gui.App()
    try:
        # 日志必须是 Listbox：Tk 8.5 的可见 Text 会造成整窗重绘
        assert isinstance(app.log_list, tk.Listbox)
        assert not hasattr(app, "log_text")
        app._log("info", "mac 启动冒烟")
        app._drain_queue()
        assert "mac 启动冒烟" in "\n".join(
            str(line) for line in app.log_list.get(0, "end"))
        assert app.log_list.size() >= 1
    finally:
        app.destroy()
