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


def test_image_dir_path_resolves_relative():
    """旧配置里的相对图片目录要按程序目录解析，不受启动工作目录影响。"""
    assert gui.image_dir_path("images") == os.path.join(gui.APP_DIR, "images")
    assert gui.image_dir_path("/tmp/meowgo_images") == "/tmp/meowgo_images"
    assert gui.image_dir_path("") == gui.DEFAULT_IMAGE_DIR


def test_round_button_set_text_resizes(tmp_path, monkeypatch):
    """改文案时按钮要按新宽度重绘，不能沿用旧背景图（曾抛 TclError）。"""
    import tkinter as tk

    from rpa import uikit

    root = tk.Tk()
    root.withdraw()
    try:
        button = uikit.RoundButton(root, "暂停", "pause", scale=1.0)
        original = button._width
        button.set_text("继续运行下一个执行组", "pause")
        assert button._width > original
        assert int(button["width"]) == button._width
        button.set_text("停", None)
        assert button._width < original
        assert int(button["width"]) == button._width
    finally:
        root.destroy()


def test_tray_show_window_restores_hidden_window(tmp_path, monkeypatch):
    """托盘「显示主窗口」要把 withdraw 的窗口恢复并重新标记可见。"""
    monkeypatch.setattr(gui, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(gui, "REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(gui, "SHOTS_DIR", str(tmp_path / "reports" / "shots"))
    monkeypatch.setattr(gui, "BACKUPS_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("MEOWGO_NO_ONBOARD", "1")
    monkeypatch.setenv("MEOWGO_NO_PERMISSION_PROMPT", "1")

    from AppKit import NSWorkspace

    previous = NSWorkspace.sharedWorkspace().frontmostApplication()
    app = gui.App()
    try:
        app.withdraw()
        app.update()
        assert app.state() == "withdrawn"
        # 测试里不要把整个测试进程激活到前台（会影响后续 AX 用例）
        monkeypatch.setattr(gui.platform_support, "IS_MACOS", False)
        monkeypatch.setattr(app, "focus_force", lambda: None)
        monkeypatch.setattr(app, "lift", lambda: None)
        app._tray_show_window()
        app.update()
        assert app.state() in ("normal", "zoomed")
        assert app._tray_window_visible is True
    finally:
        app.destroy()
        try:
            if previous is not None:
                previous.activateWithOptions_(0)
        except Exception:  # noqa: BLE001
            pass


def test_refresh_tray_menu_tracks_run_state(tmp_path, monkeypatch):
    """托盘菜单随运行状态切换：空闲时可运行，运行中只能停止。"""
    monkeypatch.setattr(gui, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(gui, "REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(gui, "SHOTS_DIR", str(tmp_path / "reports" / "shots"))
    monkeypatch.setattr(gui, "BACKUPS_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("MEOWGO_NO_ONBOARD", "1")
    monkeypatch.setenv("MEOWGO_NO_PERMISSION_PROMPT", "1")

    from rpa import i18n

    class FakeTray:
        def __init__(self):
            self.states = {}
            self.tip = ""

        def set_menu_enabled(self, states):
            self.states.update(states)

        def set_tip(self, text):
            self.tip = text

        def stop(self):
            return True

    app = gui.App()
    fake = FakeTray()
    try:
        app.tray = fake
        app.running = False
        app._refresh_tray_menu()
        assert fake.states[i18n.tr("运行当前组")] is True
        assert fake.states[i18n.tr("停止运行")] is False
        assert i18n.tr("空闲") in fake.tip

        app.running = True
        app._refresh_tray_menu()
        assert fake.states[i18n.tr("运行当前组")] is False
        assert fake.states[i18n.tr("停止运行")] is True
        assert i18n.tr("运行中") in fake.tip
    finally:
        app.tray = None
        app.destroy()


def test_app_builds_with_listbox_log(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(gui, "REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(gui, "SHOTS_DIR", str(tmp_path / "reports" / "shots"))
    monkeypatch.setattr(gui, "BACKUPS_DIR", str(tmp_path / "backups"))
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

        # 项目文件：打开后继续编辑，状态保存会同步回 .mgproj
        from rpa import project_io
        from rpa.engine import Group, Project, Step

        source = Project(groups=[Group("主流程", [Step("wait", "1")])],
                         image_dir=str(tmp_path / "images"))
        path = str(tmp_path / "demo.mgproj")
        project_io.save_project(path, source, source.image_dir)
        assert app.load_project_file(path) is True
        assert app.project_path == os.path.abspath(path)
        assert app.project.names() == ["主流程"]

        app.project.groups.append(Group("收尾", [Step("wait", "0.1")]))
        app._mark_dirty()
        assert app._save_state() is True
        reloaded, _meta = project_io.load_project(path)
        assert reloaded.names() == ["主流程", "收尾"]
    finally:
        app.destroy()
