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


def test_type_options_are_grouped():
    """类型下拉按四类分组，且不丢类型：42 项一个不少。"""
    from rpa import step_help
    from rpa.engine import STEP_TYPES

    options = gui.StepDialog._type_options()
    assert len(options) == len(STEP_TYPES) == 42
    groups = [group for _key, _label, group in options]
    assert set(groups) == set(step_help.CATEGORIES)
    # 同组必须连续，否则下拉里会出现「分组标题反复出现」
    seen: list[str] = []
    for group in groups:
        if not seen or seen[-1] != group:
            seen.append(group)
    assert len(seen) == len(set(seen))


def test_step_help_covers_every_type():
    """帮助文案不能漏类型：没写说明的只能是「只需填提示」的简单类型。"""
    from rpa import step_help
    from rpa.engine import STEP_TYPES

    missing = [key for key in STEP_TYPES if not step_help.usage(key)]
    # 允许留空的只有纯值类型（等待秒数 / 留空指令）
    assert set(missing) <= {"wait", "break", "continue", "section"}, missing


def test_help_dialog_renders_and_filters(tmp_path, monkeypatch):
    """帮助面板：能建、能搜、能定位到指定类型。"""
    from rpa import dialogs

    monkeypatch.setattr(gui, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(gui, "REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(gui, "SHOTS_DIR", str(tmp_path / "reports" / "shots"))
    monkeypatch.setattr(gui, "BACKUPS_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("MEOWGO_NO_ONBOARD", "1")
    monkeypatch.setenv("MEOWGO_NO_PERMISSION_PROMPT", "1")
    # 对话框正常会 grab 焦点 + 淡入，测试里跳过
    monkeypatch.setattr(dialogs.BaseDialog, "center", lambda self, **kw: None)

    app = gui.App()
    dialog = None
    try:
        dialog = dialogs.HelpDialog(app, "file")
        app.update()
        assert dialog.tree.exists("file")
        body = dialog.text.get("1.0", "end")
        assert "write:路径|内容" in body, "应显示 file 的写法"
        assert "文件操作" in body

        # 搜索：只留命中的类型
        dialog.var_search.set("正则")
        app.update()
        assert dialog.tree.exists("regex")
        assert not dialog.tree.exists("click")

        # 清空搜索后恢复全部，并把正文切到通用规则
        dialog.var_search.set("")
        app.update()
        dialog.select_type(dialog.COMMON_KEY)
        app.update()
        assert "变量怎么用" in dialog.text.get("1.0", "end")
    finally:
        if dialog is not None:
            dialog.destroy()
        app.destroy()


def test_f1_help_focuses_selected_step_type(tmp_path, monkeypatch):
    """F1 / 右键「这一步怎么写」要定位到当前选中行的类型。"""
    monkeypatch.setattr(gui, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(gui, "REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(gui, "SHOTS_DIR", str(tmp_path / "reports" / "shots"))
    monkeypatch.setattr(gui, "BACKUPS_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("MEOWGO_NO_ONBOARD", "1")
    monkeypatch.setenv("MEOWGO_NO_PERMISSION_PROMPT", "1")

    app = gui.App()
    seen: dict = {}
    try:
        app.project = gui.Project(
            groups=[gui.Group("主", [gui.Step("http", "GET https://x|结果")])])
        app.group_index = 0
        monkeypatch.setattr(app, "show_help",
                            lambda focus_type="": seen.setdefault(
                                "type", focus_type))
        monkeypatch.setattr(app, "_selected_steps", lambda: [0])
        app.show_help_for_selected_step()
        assert seen["type"] == "http"

        # 没有选中行时退化成通用帮助
        monkeypatch.setattr(app, "_selected_steps", lambda: [])
        seen.clear()
        app.show_help_for_selected_step()
        assert seen["type"] == ""
    finally:
        app.destroy()


def test_step_dialog_segment_editor(tmp_path, monkeypatch):
    """file / window 的分段编辑：内容框与三段字段双向同步，保存结果不变。"""
    from rpa import dialogs

    monkeypatch.setattr(gui, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(gui, "REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(gui, "SHOTS_DIR", str(tmp_path / "reports" / "shots"))
    monkeypatch.setattr(gui, "BACKUPS_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("MEOWGO_NO_ONBOARD", "1")
    monkeypatch.setenv("MEOWGO_NO_PERMISSION_PROMPT", "1")
    monkeypatch.setattr(dialogs.BaseDialog, "center", lambda self, **kw: None)

    app = gui.App()
    dialog = None
    window_dialog = None
    try:
        dialog = gui.StepDialog(
            app, app.project, gui.Step("file", "copy:/a/x.txt|/b/y.txt"))
        app.update()
        # 打开时把已有内容拆成三段
        assert dialog.var_seg_action.get() == "copy"
        assert dialog.var_seg_first.get() == "/a/x.txt"
        assert dialog.var_seg_second.get() == "/b/y.txt"

        # 改字段 → 内容框跟着拼
        dialog.var_seg_first.set("/c/z.txt")
        app.update()
        assert dialog.var_content.get() == "copy:/c/z.txt|/b/y.txt"

        # 直接改内容框 → 字段跟着拆
        dialog.var_content.set("write:/tmp/out.txt|hello")
        app.update()
        assert dialog.var_seg_action.get() == "write"
        assert dialog.var_seg_first.get() == "/tmp/out.txt"
        assert dialog.var_seg_second.get() == "hello"

        # 单段动作：第二格隐藏，内容里不留多余的竖线
        dialog.var_seg_action.set("delete")
        app.update()
        assert not dialog.seg_second.winfo_manager()
        dialog.var_seg_first.set("/tmp/x")
        app.update()
        assert dialog.var_content.get() == "delete:/tmp/x"

        # 在内容框里打字时不要被拼回去：敲个动作名不该被自动补冒号
        dialog.var_content.set("copy")
        app.update()
        assert dialog.var_content.get() == "copy"
        assert dialog.var_seg_action.get() == "copy"
        assert dialog.var_seg_first.get() == ""

        # 动作名还没打完：原样保留，不打断输入
        dialog.var_content.set("del")
        app.update()
        assert dialog.var_content.get() == "del"

        dialog.var_content.set("delete:/tmp/x")
        app.update()
        assert dialog.var_seg_action.get() == "delete"

        dialog.ok()
        assert dialog.result.type == "file"
        assert dialog.result.content == "delete:/tmp/x"
        dialog = None

        # window 用另一套动作表
        window_dialog = gui.StepDialog(
            app, app.project, gui.Step("window", "activate:记事本"))
        app.update()
        assert window_dialog.var_seg_action.get() == "activate"
        assert window_dialog.var_seg_first.get() == "记事本"
        window_dialog.ok()
        assert window_dialog.result.content == "activate:记事本"
        window_dialog = None
    finally:
        for widget in (dialog, window_dialog):
            if widget is not None:
                widget.destroy()
        app.destroy()
