# -*- coding: utf-8 -*-
"""GUI 冒烟测试：组树 / 步骤列表 / 内联编辑 / 对话框流程 / 通过界面跑一次。

对话框会被替换成假实现，避免阻塞；所有弹窗（确认框）自动应答。
"""
import os
import sys
import tempfile
import time
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa import gui as gui_module  # noqa: E402
from rpa.engine import (STEP_TYPES, Group, Project, Step,  # noqa: E402
                        import_excel, validate_project)

# 把配置、报告、失败截图都写到临时目录，避免影响用户数据和仓库里的 reports/
_TMP = tempfile.mkdtemp(prefix="rpa_gui_test_")
gui_module.SETTINGS_PATH = os.path.join(_TMP, "settings.json")
gui_module.REPORTS_DIR = os.path.join(_TMP, "reports")
gui_module.SHOTS_DIR = os.path.join(gui_module.REPORTS_DIR, "shots")
gui_module.enable_dpi_awareness()

app = gui_module.App()
print(f"  INFO  DPI 缩放系数 = {app.scale}")
# App 初始化时会预置默认组；seed() 随后会覆盖项目，先留一份快照做断言
_INITIAL_GROUPS = [(group.name,
                    [(step.type, step.content) for step in group.steps])
                   for group in app.project.groups]

# ---------------------------------------------------------------- 假对话框
# 假对话框必须模拟真实对话框的模态语义：wait() 之前 result 不可用。
# 如果调用方又退回成"构造完直接读 .result"，这里会拿到 None，测试失败。
_group_name = {"value": "假组名"}
_RealStepDialog = gui_module.StepDialog   # 保留真身，供按键录入接线测试用


class FakeGroupDialog:
    def __init__(self, parent, title, initial=""):
        self.result = None
        self._result = _group_name["value"]

    def wait(self):
        self.result = self._result
        return self.result


class FakeStepDialog:
    def __init__(self, parent, project, step=None):
        self.result = None
        self._result = Step("wait", "0.1", 1, "自动生成")

    def wait(self):
        self.result = self._result
        return self.result


class FakeSettingsDialog:
    def __init__(self, parent, settings, image_dir):
        self.result = None
        self._result = dict(settings)

    def wait(self):
        self.result = self._result
        return self.result


# 真身存档：个别用例要验证真实对话框，下面的桩只影响其它用例
_RealStepDialog = gui_module.StepDialog
_RealSettingsDialog = gui_module.SettingsDialog

gui_module.GroupDialog = FakeGroupDialog
gui_module.StepDialog = FakeStepDialog
gui_module.SettingsDialog = FakeSettingsDialog

# 弹窗自动应答
gui_module.messagebox.askyesno = lambda *a, **k: True
gui_module.messagebox.showinfo = lambda *a, **k: None
gui_module.messagebox.showwarning = lambda *a, **k: None
gui_module.messagebox.showerror = lambda *a, **k: None


def seed():
    app.project.groups = [
        Group("登录", [Step("click", "登录按钮.png"), Step("input", "admin"),
                       Step("hotkey", "enter")]),
        Group("主流程", [Step("call", "登录"), Step("wait", "0.5"),
                         Step("cmd", "start notepad")]),
        Group("收尾", [Step("scroll", "-300")]),
    ]
    app._refresh_all(select_group=1)
    app._log("info", "测试日志")


_ok: list[str] = []


def mark(name):
    print(f"  PASS  {name}")
    _ok.append(name)


def checks():
    ok = _ok

    # --- 组树渲染 ------------------------------------------------------
    assert len(app.group_tree.get_children()) == 3, "组节点数不对"
    group_iid = "g1"
    children = app.group_tree.get_children(group_iid)
    assert len(children) == 3, f"主流程应有 3 个子节点，实际 {len(children)}"
    assert "执行组" in app.group_count.cget("text") or "个组" in \
        app.group_count.cget("text")
    mark("组树渲染")

    # --- 步骤列表渲染 --------------------------------------------------
    rows = app.step_tree.get_children()
    assert len(rows) == 3, f"步骤行数不对：{len(rows)}"
    first = app.step_tree.item("0")["values"]
    assert first[1] == "调用执行组", first
    assert first[2] == "登录", first
    mark("步骤列表渲染")

    # --- 组切换 --------------------------------------------------------
    app.group_index = 0
    app._refresh_steps()
    assert app.step_title.cget("text") == "步骤 · 登录", app.step_title.cget("text")
    assert len(app.step_tree.get_children()) == 3
    mark("组切换")

    # --- 左侧点组切换 --------------------------------------------------
    app.group_tree.selection_set("g2")
    app._on_group_select()
    assert app.group_index == 2, app.group_index
    mark("左侧选中联动")

    # --- 列表内编辑 ----------------------------------------------------
    app.group_index = 1
    app._refresh_steps()
    app._apply_inline(1, "content", "1.5")
    assert app.project.groups[1].steps[1].content == "1.5"
    app._apply_inline(1, "repeat", "3")
    assert app.project.groups[1].steps[1].repeat == 3.0
    app._apply_inline(1, "note", "等待加载")
    assert app.project.groups[1].steps[1].note == "等待加载"
    app._apply_inline(1, "type", "5 · 等待")
    assert app.project.groups[1].steps[1].type == "wait"
    mark("列表内联编辑")

    # --- 步骤操作 ------------------------------------------------------
    app.step_tree.selection_set("1")
    app.move_step(-1)
    assert app.project.groups[1].steps[0].content == "1.5", \
        app.project.groups[1].steps[0].content
    app.step_tree.selection_set("0")
    app.duplicate_step()
    assert len(app.project.groups[1].steps) == 4
    app.step_tree.selection_set("0")
    app.delete_step()
    assert len(app.project.groups[1].steps) == 3
    mark("步骤增删移")

    # --- 列表里的 Delete 只删一行（曾同时绑在列表和窗口上会删两行） -----
    app.project.groups = [Group("快捷键", [Step("wait", "1"), Step("wait", "2"),
                                          Step("wait", "3")])]
    app.group_index = 0
    app._refresh_all()
    app.step_tree.selection_set("0")
    app.step_tree.focus_set()
    app.update()
    app.step_tree.event_generate("<Delete>")
    app.update()
    assert len(app.project.groups[0].steps) == 2, \
        f"应只删一行，实际剩 {len(app.project.groups[0].steps)}"
    mark("Delete 只删一行")

    # --- 组操作 --------------------------------------------------------
    _group_name["value"] = "新组X"
    app.add_group()
    assert app.project.names()[-1] == "新组X", app.project.names()
    assert app.group_index == len(app.project.groups) - 1, app.group_index
    _group_name["value"] = "重命名后"
    app.rename_group()
    assert "重命名后" in app.project.names()
    app.duplicate_group()
    assert any(name.endswith("副本") for name in app.project.names()), \
        app.project.names()
    app.move_group(-1)
    total = len(app.project.groups)
    app.delete_group()
    assert len(app.project.groups) == total - 1
    mark("执行组增删移")

    # --- 重命名会同步调用引用 -------------------------------------------
    app.project.groups = [Group("旧名", [Step("wait", "0.1")]),
                          Group("调用者", [Step("call", "旧名")])]
    app.group_index = 0
    app._refresh_all()
    _group_name["value"] = "新名"
    app.rename_group()
    assert app.project.groups[1].steps[0].content == "新名"
    mark("重命名同步引用")

    # --- 校验拦截（调用不存在的组） -------------------------------------
    app.project.groups = [Group("A", [Step("call", "幽灵组")])]
    app.group_index = 0
    app._refresh_all()
    assert validate_project(app.project.groups), "应能发现问题"
    assert app._validate() is False, "存在问题的配置不该通过"
    mark("运行前校验")

    # --- 状态自动保存（没有“项目”概念，执行组直接落到状态文件） -----------
    app.project.groups = [Group("登录", [Step("wait", "1")]),
                          Group("主", [Step("call", "登录")])]
    app._refresh_all()
    app._dirty = False
    app.project.groups.append(Group("临时", []))
    app._refresh_all()
    assert app._dirty is True, "改动后应标记为待保存"
    app.project.groups.pop()
    app._save_state()
    assert os.path.exists(gui_module.SETTINGS_PATH)
    import json

    with open(gui_module.SETTINGS_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    from rpa.engine import Project

    project = Project.from_dict(data)
    assert project.names() == ["登录", "主"], project.names()
    assert project.groups[1].steps[0].type == "call"
    assert data.get("image_dir") == app.image_dir
    assert app._dirty is False
    mark("状态自动保存")

    # --- Excel 导入 / 导出（走界面方法，替换文件选择框） ----------------
    with tempfile.TemporaryDirectory() as tmp:
        xlsx = os.path.join(tmp, "out.xlsx")
        app.project.groups = [Group("一组", [Step("wait", "1")]),
                              Group("二组", [Step("scroll", "-1")])]
        app._refresh_all()
        gui_module.filedialog.asksaveasfilename = lambda *a, **k: xlsx
        app.export_excel(single=False)
        assert os.path.exists(xlsx)
        assert [g.name for g in import_excel(xlsx)] == ["一组", "二组"]
        app.project.groups = []
        gui_module.filedialog.askopenfilename = lambda *a, **k: xlsx
        app.import_excel(single=False)
        assert [g.name for g in app.project.groups] == ["一组", "二组"], \
            app.project.names()
        # 导出当前组
        one = os.path.join(tmp, "one.xlsx")
        gui_module.filedialog.asksaveasfilename = lambda *a, **k: one
        app.group_index = 0
        app.export_excel(single=True)
        assert len(import_excel(one)) == 1
    mark("Excel 组导入导出")

    # --- 日志 ----------------------------------------------------------
    app.clear_log()
    app._enqueue_log("info", "日志内容")
    app._drain_queue()
    assert "日志内容" in app.log_text.get("1.0", "end")
    app.toggle_log()
    assert app.log_visible is False and not app.log_text.winfo_ismapped()
    app.toggle_log()
    assert app.log_visible is True
    mark("日志与折叠")

    # --- 右键菜单：样式统一、点外部关闭、禁用项不响应 --------------------
    calls = []
    MenuItem = gui_module.uikit.MenuItem
    menu = gui_module.uikit.ContextMenu(app, app.scale, [
        MenuItem("动作", "check", lambda: calls.append("a"), "Ctrl+K"),
        "-",
        MenuItem("禁用项", "close", lambda: calls.append("b"), enabled=False),
        MenuItem("危险项", "trash", lambda: calls.append("c"), danger=True),
    ])
    menu.popup(200, 200)
    app.update()
    assert len(menu._rows) == 3, "分隔线不应计入菜单项"

    class Click:
        def __init__(self, x, y, x_root=None, y_root=None):
            self.x, self.y = x, y
            self.x_root = x if x_root is None else x_root
            self.y_root = y if y_root is None else y_root

    row = menu._rows[0]
    point = Click(30, (row["y1"] + row["y2"]) // 2)
    menu._on_motion(point)
    menu._on_press(point)
    menu._on_release(point)
    assert calls == ["a"], f"点第一项应只触发一次：{calls}"
    assert not menu.winfo_exists(), "点击后菜单应关闭"

    # 禁用项
    menu2 = gui_module.uikit.ContextMenu(app, app.scale, [
        MenuItem("禁用项", "close", lambda: calls.append("b"), enabled=False)])
    menu2.popup(200, 200)
    app.update()
    row = menu2._rows[0]
    point = Click(30, (row["y1"] + row["y2"]) // 2)
    menu2._on_press(point)
    menu2._on_release(point)
    assert calls == ["a"], f"禁用项不应触发：{calls}"
    assert menu2.winfo_exists(), "点禁用项应保持菜单打开（与原生菜单一致）"

    # 点在菜单外面 = 取消
    menu2.close()
    assert not menu2.winfo_exists()
    ok_menu = gui_module.uikit.ContextMenu(app, app.scale, [
        MenuItem("动作", "check", lambda: calls.append("d"))])
    ok_menu.popup(200, 200)
    app.update()
    outside = Click(-20, -20)
    ok_menu._on_press(outside)
    ok_menu._on_release(outside)
    assert calls == ["a"], f"点菜单外不应执行：{calls}"
    assert not ok_menu.winfo_exists(), "点菜单外应关闭"

    # 集成：步骤菜单第一项是「新增步骤…」，且图片类步骤才可截图
    from rpa.engine import Group as _G
    app.project.groups = [_G("菜单", [Step("wait", "1")])]
    app.group_index = 0
    app._refresh_all()
    app.step_tree.selection_set("0")
    captured = {}
    original = gui_module.uikit.ContextMenu.popup

    def fake_popup(self, x, y):
        captured["menu"] = self
        return self

    gui_module.uikit.ContextMenu.popup = fake_popup
    try:
        bbox = app.step_tree.bbox("0")
        app._step_menu(Click(0, bbox[1] + bbox[3] // 2))
    finally:
        gui_module.uikit.ContextMenu.popup = original
    items = captured["menu"].items
    assert items[0].label.startswith("新增步骤"), items[0].label
    shot = [it for it in items if it.label.startswith("截取屏幕")][0]
    assert shot.enabled is False, "等待步骤不该能截图"
    captured["menu"].close()
    mark("右键菜单行为")

    # --- 菜单外右键：收起旧菜单，并就地弹出新的（轮询那条路） -----------
    app.project.groups = [_G("菜单", [Step("wait", "1")])]
    app.group_index = 0
    app._refresh_all()
    step_mid = (app.step_tree.winfo_rootx() + app.step_tree.winfo_width() // 2,
                app.step_tree.winfo_rooty() + app.step_tree.winfo_height() // 2)
    # 转发依赖 winfo_containing 找指针下的控件；远程会话里可能有系统背板
    # 窗口挡住命中测试，这里桩掉，让测试只验证转发逻辑本身
    original_containing = app.winfo_containing

    def fake_containing(x, y):
        return app.step_tree if (x, y) == step_mid else None

    app.winfo_containing = fake_containing
    captured.clear()
    gui_module.uikit.ContextMenu.popup = fake_popup
    try:
        app._open_top_menu("帮助")
        top_menu = captured["menu"]
        assert app._top_buttons["帮助"].kind == "solid"
        top_menu._armed = True
        top_menu._key_state = staticmethod(lambda: (0, 0x8000))  # 此刻右键按着
        top_menu._inside = lambda: False
        top_menu._pointer_xy = lambda: step_mid
        top_menu._watch()
    finally:
        gui_module.uikit.ContextMenu.popup = original
    assert not top_menu.winfo_exists(), "菜单外右键应收起原菜单"
    assert app._top_buttons["帮助"].kind == "ghost", "收起后菜单栏按钮该恢复"
    swapped = captured["menu"]
    assert swapped is not top_menu and swapped.winfo_exists(), "应就地弹出新菜单"
    assert swapped.items[0].label.startswith("新增步骤"), swapped.items[0].label
    swapped.close()
    # 空白处（没有右键菜单的地方）只收起，不弹新的
    captured.clear()
    gui_module.uikit.ContextMenu.popup = fake_popup
    try:
        app._open_top_menu("帮助")
        blank = captured["menu"]
        blank._armed = True
        blank._key_state = staticmethod(lambda: (0, 0x8000))
        blank._pointer_xy = lambda: (app.winfo_rootx() + app.s(120),
                                     app.winfo_rooty() + app.s(12))
        blank._inside = lambda: False
        blank._watch()
    finally:
        gui_module.uikit.ContextMenu.popup = original
    assert not blank.winfo_exists(), "空白处右键应只收起"
    assert captured.get("menu") is blank, "空白处不该弹出新菜单"
    app.winfo_containing = original_containing
    mark("菜单外右键换菜单")

    # --- 多屏：菜单落在鼠标所在那块屏（副屏在主屏左边时是负坐标） -------
    from rpa import uikit as _uikit

    area, bounds = _uikit.monitor_work_area, _uikit.desktop_bounds
    probe = gui_module.uikit.ContextMenu(app, app.scale,
                                         [MenuItem("动作", "check")])
    try:
        _uikit.monitor_work_area = lambda x, y: (-1920, 0, 0, 1040)
        assert probe._clamp(-1800, 300) == (-1800, 300), probe._clamp(-1800, 300)
        assert probe._clamp(-5, 300) == (
            max(-1920, -probe._width - probe._s(6)), 300), probe._clamp(-5, 300)
        assert probe._clamp(-2000, 300)[0] == -1920
        assert probe._clamp(-100, 1039)[1] == max(
            0, 1040 - probe._height - probe._s(6))
        _uikit.monitor_work_area = lambda x, y: None
        _uikit.desktop_bounds = lambda: (2560, 0, 5120, 1040)   # 右侧副屏
        assert probe._clamp(3000, 400) == (3000, 400), probe._clamp(3000, 400)
        assert probe._clamp(5000, 400)[0] == max(
            2560, 5120 - probe._width - probe._s(6))
        _uikit.desktop_bounds = lambda: None                    # 两个都取不到
        assert probe._clamp(200, 150) == (200, 150), probe._clamp(200, 150)
    finally:
        _uikit.monitor_work_area, _uikit.desktop_bounds = area, bounds
        probe.close()
    corner = gui_module.uikit.ContextMenu(app, app.scale,
                                          [MenuItem("动作", "check")])
    corner.popup(app.winfo_screenwidth() - 3, app.winfo_screenheight() - 3)
    app.update()
    right = corner.winfo_rootx() + corner._width
    bottom = corner.winfo_rooty() + corner._height
    assert right <= app.winfo_screenwidth() and bottom <= app.winfo_screenheight(), \
        (right, bottom)
    corner.close()
    assert not corner.winfo_exists()
    mark("多屏菜单位置")

    # --- 弹窗位置：也留在主窗口所在那块屏（不再被 max(0, ..) 拽到主屏） --
    class _FakeWin:
        def __init__(self):
            self.calls: list[str] = []

        def geometry(self, value):
            self.calls.append(value)

    fake_win = _FakeWin()
    area, bounds = _uikit.monitor_work_area, _uikit.desktop_bounds
    try:
        _uikit.monitor_work_area = lambda x, y: (-1920, 0, 0, 1040)
        app.place_window(fake_win, -1800, 200, 400, 300)
        assert fake_win.calls[-1] == "+-1800+200", fake_win.calls
        app.place_window(fake_win, -50, 200, 400, 300)       # 越过副屏右边界
        assert fake_win.calls[-1] == "+-400+200", fake_win.calls
        app.place_window(fake_win, -1800, 1000, 400, 300)    # 越过下边界
        assert fake_win.calls[-1] == "+-1800+740", fake_win.calls
        _uikit.monitor_work_area = lambda x, y: None         # 取不到显示器信息
        app.place_window(fake_win, -100, -30, 400, 300)
        assert fake_win.calls[-1] == "+0+0", fake_win.calls
    finally:
        _uikit.monitor_work_area, _uikit.desktop_bounds = area, bounds
    mark("弹窗位置（多屏）")

    # --- 菜单栏：自绘按钮 + 同款下拉菜单，不再用系统菜单 -----------------
    installed = app.cget("menu")
    assert not installed, f"不应再配置系统菜单栏：{installed}"
    assert set(app._top_buttons) == {"文件", "编辑", "运行", "视图", "帮助"}, \
        list(app._top_buttons)
    captured.clear()
    gui_module.uikit.ContextMenu.popup = fake_popup
    try:
        app._open_top_menu("文件")
    finally:
        gui_module.uikit.ContextMenu.popup = original
    file_menu = captured["menu"]
    labels = [it.label for it in file_menu.items]
    assert any("导入 Excel" in text for text in labels), labels
    assert any("退出" == text for text in labels), labels
    assert app._top_buttons["文件"].kind == "solid", "展开菜单时按钮应高亮"
    file_menu.close()
    assert app._top_buttons["文件"].kind == "ghost", "菜单关闭后按钮应恢复"
    # 菜单里的项目：没有“项目”相关项了
    assert not any("项目" in text for text in labels), labels
    mark("自绘菜单栏")

    # --- 通过界面完整跑一次（含组调用） --------------------------------
    app.project.groups = [
        Group("子组", [Step("wait", "0.1")]),
        Group("主流程", [Step("call", "子组"), Step("wait", "0.1")]),
    ]
    app.settings["delay"] = 0
    app.group_index = 1
    app._refresh_all()
    app.clear_log()
    app.run_group()
    deadline = time.time() + 15
    while time.time() < deadline:
        app.update()
        text = app.log_text.get("1.0", "end")
        if not app.running and "执行完成" in text:
            break
        time.sleep(0.05)
    text = app.log_text.get("1.0", "end")
    assert "执行完成" in text, text
    assert "进入执行组「主流程」" in text and "进入执行组「子组」" in text, text
    assert app.btn_run.enabled, "结束后运行按钮应恢复"
    assert not app.btn_stop.enabled, "结束后停止按钮应禁用"
    mark("界面运行一次（含组调用）")

    # --- 运行中停止 ----------------------------------------------------
    app.project.groups = [Group("长任务", [Step("wait", "30")])]
    app.group_index = 0
    app._refresh_all()
    app.run_group()
    app.update()
    assert app.running
    assert app.btn_stop.enabled and not app.btn_run.enabled
    app.toggle_pause()
    assert app.paused
    app.toggle_pause()
    app.stop_run()
    deadline = time.time() + 10
    while time.time() < deadline:
        app.update()
        if not app.running:
            break
        time.sleep(0.05)
    assert not app.running, "停止后应结束"
    assert app.btn_run.enabled, "停止后运行按钮应恢复"
    mark("运行中暂停/停止")

    # --- 按键录入：VK → 引擎键名映射表 ----------------------------------
    vk = gui_module.vk_to_key_name
    assert vk(0xA2) == "ctrl" and vk(0x11) == "ctrl" and vk(0xA3) == "ctrl"
    assert vk(0xA0) == "shift" and vk(0x10) == "shift"
    assert vk(0x12) == "alt" and vk(0xA4) == "alt"
    assert vk(0x5B) == "win" and vk(0x5C) == "win"
    assert vk(0x1B) == "esc" and vk(0x0D) == "enter" and vk(0x09) == "tab"
    assert vk(0x20) == "space" and vk(0x08) == "backspace"
    assert vk(0x2E) == "delete" and vk(0x2D) == "insert"
    assert vk(0x24) == "home" and vk(0x23) == "end"
    assert vk(0x21) == "pageup" and vk(0x22) == "pagedown"
    assert vk(0x25) == "left" and vk(0x26) == "up"
    assert vk(0x27) == "right" and vk(0x28) == "down"
    assert vk(0x70) == "f1" and vk(0x7B) == "f12" and vk(0x87) == "f24"
    assert vk(0x60) == "num0" and vk(0x69) == "num9"
    assert vk(0x6E) == "decimal" and vk(0x6B) == "add"
    assert vk(0x6D) == "subtract" and vk(0x6A) == "multiply"
    assert vk(0x6F) == "divide"
    assert vk(0x14) == "capslock" and vk(0x90) == "numlock"
    assert vk(0x91) == "scrolllock" and vk(0x2C) == "printscreen"
    assert vk(0x13) == "pause" and vk(0x5D) == "apps"
    # 顶层数字键：引擎旧版限制不识别，映射为 None（提示改小键盘）
    assert vk(0x30) is None and vk(0x35) is None and vk(0x39) is None
    assert vk(0xBC) == "," and vk(0xBE) == "." and vk(0xBF) == "/"
    assert vk(0x41) in set("abcdefghijklmnopqrstuvwxyz")   # 字母按当前布局
    mark("按键映射表")

    # --- 按键录入：录制状态机（不装真钩子，直接驱动物理键序列） ----------
    def make_recorder():
        events = {"state": [], "preview": [], "done": [], "cancel": [],
                  "hints": []}
        rec = gui_module.KeyRecorder(
            app,
            on_state=lambda active: events["state"].append(active),
            on_preview=lambda names: events["preview"].append("+".join(names)),
            on_done=lambda text: events["done"].append(text),
            on_cancel=lambda: events["cancel"].append(True),
            on_hint=lambda text: events["hints"].append(text))
        rec._install_hook = lambda: True   # 不装真钩子，避免吞掉测试按键
        return rec, events

    def press(rec, *keys):
        """按 (vk, down) 序列驱动物理键，逐个刷新 after_idle 回调。"""
        for vk_code, down in keys:
            rec._track(vk_code, 0, down)
            app.update()

    # Ctrl+Shift+A → ctrl,shift,a（修饰键排前，预览用 + 连接）
    rec, ev = make_recorder()
    assert rec.start("旧内容") is True
    assert rec.active and ev["state"] == [True]
    press(rec, (0xA2, True), (0xA0, True), (0x41, True),
          (0x41, False), (0xA0, False), (0xA2, False))
    assert ev["done"] == ["ctrl,shift,a"], ev
    assert ev["preview"][-1] == "ctrl+shift+a", ev
    assert not rec.active and ev["state"][-1] is False

    # 松开顺序不影响定稿
    rec, ev = make_recorder()
    rec.start("")
    press(rec, (0x41, True), (0xA2, True), (0xA2, False), (0x41, False))
    assert ev["done"] == ["ctrl,a"], ev

    # 单键：F5 / 小键盘 5
    rec, ev = make_recorder()
    rec.start("")
    press(rec, (0x74, True), (0x74, False))
    assert ev["done"] == ["f5"], ev
    rec, ev = make_recorder()
    rec.start("")
    press(rec, (0x65, True), (0x65, False))
    assert ev["done"] == ["num5"], ev

    # Ctrl+Esc 能录；单独 Esc 取消
    rec, ev = make_recorder()
    rec.start("")
    press(rec, (0xA2, True), (0x1B, True), (0x1B, False), (0xA2, False))
    assert ev["done"] == ["ctrl,esc"], ev
    rec, ev = make_recorder()
    rec.start("原值")
    press(rec, (0x1B, True))
    assert ev["cancel"] == [True] and ev["done"] == [], ev

    # 顶层数字键被跳过并提示；组合里的数字键同样跳过，其余键照录
    rec, ev = make_recorder()
    rec.start("")
    press(rec, (0x35, True), (0x35, False))
    assert ev["done"] == [] and ev["cancel"] == [True], ev
    assert any("num0" in text for text in ev["hints"]), ev
    rec, ev = make_recorder()
    rec.start("")
    press(rec, (0xA2, True), (0x35, True), (0x35, False), (0xA2, False))
    assert ev["done"] == ["ctrl"], ev

    # 一批原始事件里连着两串：第一串定稿后剩下的直接忽略
    rec, ev = make_recorder()
    rec.start("")
    rec._pending = [(0x70, 0, True), (0x70, 0, False),
                    (0x71, 0, True), (0x71, 0, False)]
    rec._poll()
    assert ev["done"] == ["f1"], ev
    # 定稿后录制即结束，再点一次才能录下一串
    rec.start("")
    rec._track(0x71, 0, True)
    rec._track(0x71, 0, False)
    assert ev["done"] == ["f1", "f2"], ev
    mark("按键录制状态机")

    # --- 步骤对话框：按键录入按钮与类型联动 ------------------------------
    dialog = _RealStepDialog(app, app.project, Step("hotkey", "ctrl,s"))
    assert dialog.btn_record.enabled is True, "热键步骤应能按键录入"
    dialog._record_done("ctrl,shift,a")
    assert dialog.var_content.get() == "ctrl,shift,a"
    dialog.combo.set("1 · 左键单击")
    dialog._sync()
    assert dialog.btn_record.enabled is False, "非热键类型应禁用录入按钮"
    dialog.cancel()
    assert not dialog.winfo_exists()
    mark("步骤对话框按键录入")
    return ok


def test_schedule_dialog_and_tick():
    """定时运行对话框：设置构造、概况文案、到点触发、报告写入。"""
    import time as _time

    from rpa.gui import ScheduleDialog

    # ok() 产出的 settings 片段
    dialog = ScheduleDialog(app, {}, ["甲组", "乙组"])
    dialog.var_enabled.set(True)
    dialog.var_hour.set("7")
    dialog.var_minute.set("5")
    dialog.var_group.set("甲组")
    dialog.var_sound.set(True)
    dialog.var_webhook.set("https://example.invalid/hook")
    dialog.ok()
    result = dialog.result
    assert result["schedule"] == {"enabled": True, "time": "07:05",
                                  "group": "甲组"}, result
    assert result["sound"] is True and result["webhook"]

    # 非法时间要能被夹到合法范围
    dialog2 = ScheduleDialog(app, {}, [])
    dialog2.var_hour.set("99")
    dialog2.var_minute.set("abc")
    dialog2.ok()
    assert dialog2.result["schedule"]["time"] == "23:00", dialog2.result

    # 概况文案
    app.settings = {"schedule": {"enabled": True, "time": "07:05",
                                 "group": "甲组"}}
    assert "07:05" in app._schedule_summary()
    assert "甲组" in app._schedule_summary()
    app.settings = {}
    assert "未启用" in app._schedule_summary()

    # 到点触发一次（同一天不重复）
    fired = []
    app.run_group_for = lambda name: fired.append(name)
    app.project.groups = [Group("甲组", [Step("wait", "1")])]
    app.settings = {"schedule": {"enabled": True,
                                 "time": _time.strftime("%H:%M"),
                                 "group": "甲组"}}
    app._schedule_fired = set()
    app.running = False
    app._schedule_tick()
    assert fired == ["甲组"], fired
    app._schedule_tick()
    assert fired == ["甲组"], "同一天不应重复触发"
    mark("定时运行")

    # 组不存在时只警告、不触发
    fired.clear()
    app.settings = {"schedule": {"enabled": True,
                                 "time": _time.strftime("%H:%M"),
                                 "group": "幽灵组"}}
    app._schedule_fired = set()
    app._schedule_tick()
    assert fired == [], fired

    # 运行结束写报告（写到临时目录，不碰项目目录）
    original_dir = gui_module.REPORTS_DIR
    gui_module.REPORTS_DIR = os.path.join(_TMP, "reports")
    try:
        app.executor.stats = [{"group": "甲", "index": 1, "type": "wait",
                               "content": "1", "note": "", "seconds": 0.1,
                               "ok": True, "row": 1}]
        app._write_run_report("finished")
        files = [name for name in os.listdir(gui_module.REPORTS_DIR)
                 if name.endswith(".md")]
        assert files, "应写出一份 markdown 报告"
        assert os.path.exists(os.path.join(gui_module.REPORTS_DIR,
                                           "index.html"))
        latest = max(files, key=lambda name: os.path.getmtime(
            os.path.join(gui_module.REPORTS_DIR, name)))
        text = open(os.path.join(gui_module.REPORTS_DIR, latest),
                    encoding="utf-8").read()
        assert "结果：finished" in text and "| 甲 | 1 | 1 |" in text
    finally:
        gui_module.REPORTS_DIR = original_dir
    mark("运行报告")


def test_editing_features():
    """撤销重做、拖拽移动、图片管家、配置备份。"""
    import json

    # --- 撤销 / 重做 ---
    app.project.groups = [Group("甲", [Step("wait", "1")])]
    app.group_index = 0
    app._refresh_all()
    app._push_undo("测试")
    app.project.groups[0].steps.append(Step("wait", "2"))
    app.undo()
    assert len(app.project.groups[0].steps) == 1, "撤销应回到 1 步"
    app.redo()
    assert len(app.project.groups[0].steps) == 2, "重做应回到 2 步"
    app._undo_stack.clear()
    app._redo_stack.clear()

    # --- 拖拽移动（move_*_to）---
    app.project.groups[0].steps = [Step("wait", "1"), Step("wait", "2"),
                                   Step("wait", "3")]
    app._refresh_steps()
    app.move_step_to(0, 2)
    assert [s.content for s in app.project.groups[0].steps] == ["2", "3", "1"]
    app.project.groups = [Group("甲", []), Group("乙", []), Group("丙", [])]
    app._refresh_all()
    app.move_group_to(0, 2)
    assert app.project.names() == ["乙", "丙", "甲"]

    # --- 图片管家 ---
    image_dir = os.path.join(_TMP, "images_mgr")
    os.makedirs(image_dir, exist_ok=True)
    open(os.path.join(image_dir, "在用的.png"), "w").close()
    open(os.path.join(image_dir, "没用的.png"), "w").close()
    app.project.groups = [Group("甲", [Step("click", "在用的.png")])]
    manager = gui_module.ImageManagerDialog(app, app.project, image_dir)
    refs = manager._references()
    assert "在用的.png" in refs and len(refs["在用的.png"]) == 1, refs
    assert "没用的.png" not in refs
    manager._reload()
    manager._clean_unused()               # 确认框在测试里自动应答“是”
    assert not os.path.exists(os.path.join(image_dir, "没用的.png"))
    assert os.path.exists(os.path.join(image_dir, "在用的.png"))
    manager.cancel()

    # --- 配置备份 ---
    original_path = gui_module.SETTINGS_PATH
    original_backups = gui_module.BACKUPS_DIR
    tmp_settings = os.path.join(_TMP, "bk_settings.json")
    tmp_backups = os.path.join(_TMP, "bk_backups")
    gui_module.SETTINGS_PATH = tmp_settings
    gui_module.BACKUPS_DIR = tmp_backups
    try:
        with open(tmp_settings, "w", encoding="utf-8") as fh:
            json.dump({"version": 2, "groups": []}, fh)
        app._backup_settings()
        backups = os.listdir(tmp_backups)
        assert len(backups) == 1 and backups[0].startswith("rpa_settings_")
    finally:
        gui_module.SETTINGS_PATH = original_path
        gui_module.BACKUPS_DIR = original_backups
    mark("编辑增强")


def test_macro_recorder():
    """宏录制状态机：单击 / 双击 / 右键 / 滚轮 / 拖拽 / 控制条内忽略。"""
    import ctypes
    import time as _time

    events = []
    rec = gui_module.MacroRecorder(
        app,
        on_state=lambda active: events.append(("state", active)),
        on_done=lambda steps: events.append(("done", steps)),
        on_cancel=lambda: events.append(("cancel",)),
        on_hint=lambda text: None)
    rec._install_hook = lambda: True       # 不装真钩子，直接喂事件
    assert rec.start(ignore_rect=(0, 0, 10, 10)) is True

    _holder = []                           # 持有结构体，防止地址失效

    def ll(x, y, wheel=0):
        info = gui_module._MSLLHOOKSTRUCT()
        info.pt.x, info.pt.y = x, y
        info.mouseData = (int(wheel) & 0xFFFF) << 16
        _holder.append(info)
        return ctypes.addressof(info)

    def press(msg, x, y, wheel=0):
        rec._on_hook(0, msg, ll(x, y, wheel))

    # 单击
    press(gui_module._WM_LBUTTONDOWN, 500, 300)
    press(gui_module._WM_LBUTTONUP, 500, 300)
    # 双击（间隔 50ms，位置相同）
    press(gui_module._WM_LBUTTONDOWN, 600, 300)
    press(gui_module._WM_LBUTTONUP, 600, 300)
    _time.sleep(0.05)
    press(gui_module._WM_LBUTTONDOWN, 600, 300)
    press(gui_module._WM_LBUTTONUP, 600, 300)
    # 右键
    press(gui_module._WM_RBUTTONDOWN, 700, 400)
    press(gui_module._WM_RBUTTONUP, 700, 400)
    # 滚轮向上 1 格
    press(gui_module._WM_MOUSEWHEEL, 700, 400, wheel=120)
    # 大位移移动（超过 60px 才记）
    press(gui_module._WM_MOUSEMOVE, 900, 600)
    # 控制条区域内的点击要忽略
    press(gui_module._WM_LBUTTONDOWN, 5, 5)
    press(gui_module._WM_LBUTTONUP, 5, 5)
    # 拖拽：按下 → 移动 → 在别处抬起
    press(gui_module._WM_LBUTTONDOWN, 100, 100)
    press(gui_module._WM_LBUTTONUP, 200, 200)

    rec.stop()
    done = [e for e in events if e[0] == "done"]
    assert done, "应定稿出步骤"
    steps = done[-1][1]
    got = [(s.type, s.content, s.repeat) for s in steps]
    assert got == [
        ("click_xy", "500,300", 0),
        ("click_xy", "600,300", 2),
        ("click_xy", "700,400,right", 0),
        ("scroll", "100", 0),
        ("move_xy", "900,600", 0),
        ("click_xy", "100,100", 0),
        ("move_xy", "200,200", 0),
    ], got
    mark("宏录制")

    # 没录到东西 → 取消回调
    events.clear()
    rec2 = gui_module.MacroRecorder(
        app, on_state=lambda a: None,
        on_done=lambda s: events.append(("done", s)),
        on_cancel=lambda: events.append(("cancel",)),
        on_hint=lambda t: None)
    rec2._install_hook = lambda: True
    rec2.start()
    rec2.stop()
    assert events and events[-1][0] == "cancel", events

def test_run_with_data():
    """带数据运行：选文件后按行跑；普通运行不带上一次的数据残留。"""
    tmp = tempfile.mkdtemp(prefix="rpa_data_")
    path = os.path.join(tmp, "d.csv")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("名字\n甲\n乙\n")

    real_ask = gui_module.filedialog.askopenfilename
    real_preview = gui_module.DataPreviewDialog

    class _AutoPreview:
        """测试替身：自动确认预览对话框（交互由专门用例覆盖）。"""

        def __init__(self, parent, path, data):
            self.data = data

        def wait(self):
            return True

    gui_module.filedialog.askopenfilename = lambda *a, **k: path
    gui_module.DataPreviewDialog = _AutoPreview
    try:
        app.settings["delay"] = 0
        # 预热 pyautogui（cv2/numpy，冷导入十几秒）：不让它发生在计时区间里
        import pyautogui  # noqa: F401
        app.project.groups = [Group("数据组", [
            Step("input", "你好{名字}"), Step("wait", "0.05")])]
        app.group_index = 0
        app._refresh_all()
        app.clear_log()
        app.run_with_data()
        deadline = time.time() + 20
        while time.time() < deadline:
            app.update()
            text = app.log_text.get("1.0", "end")
            if not app.running and "执行完成" in text:
                break
            time.sleep(0.05)
    finally:
        gui_module.filedialog.askopenfilename = real_ask
        gui_module.DataPreviewDialog = real_preview
    text = app.log_text.get("1.0", "end")
    assert "数据驱动：共 2 行" in text, text
    assert "第 2/2 行" in text, text
    report = app.executor.report()
    assert report["rows"] == 2, report
    assert len(report["detail"]) == 4, report["detail"]   # 2 行 × 2 步
    # 统计里记的是渲染后的内容
    contents = [d["content"] for d in report["detail"]]
    assert "你好甲" in contents and "你好乙" in contents, contents

    # 普通运行后 data 必须清空：不会残留上一次的数据
    app.executor.data = [{"名字": "残留"}]
    app.clear_log()
    app.run_group()
    deadline = time.time() + 20
    while time.time() < deadline:
        app.update()
        if not app.running:
            break
        time.sleep(0.05)
    assert app.executor.data is None, "普通运行不应残留数据"
    text = app.log_text.get("1.0", "end")
    assert "数据驱动" not in text, text
    mark("带数据运行")


def test_macro_wheel_accumulate():
    """亚格滚轮增量要累加：60+60 凑满 120 记 1 格，反向 30 不足一格不记。"""
    import ctypes

    done = []
    rec = gui_module.MacroRecorder(
        app, on_state=lambda a: None,
        on_done=lambda s: done.append(s), on_cancel=lambda: None,
        on_hint=lambda t: None)
    rec._install_hook = lambda: True
    rec.start()
    holder = []

    def wheel(delta):
        info = gui_module._MSLLHOOKSTRUCT()
        info.pt.x, info.pt.y = 500, 300
        info.mouseData = (int(delta) & 0xFFFF) << 16
        holder.append(info)
        rec._on_hook(0, gui_module._WM_MOUSEWHEEL, ctypes.addressof(info))

    wheel(60)
    wheel(60)                # 凑满 120 → 记 1 格
    wheel(-30)               # 反向 30，不足一格 → 不记，留到下次
    rec.stop()
    assert [(s.type, s.content) for s in done[0]] == [("scroll", "100")], done
    assert rec._wheel_rest == -30, rec._wheel_rest
    mark("滚轮增量累加")


def test_data_preview_dialog():
    """数据预览对话框：行数/列名/前 5 行；确定 True、取消 None。"""
    data = [{"名字": "甲", "城市": "北京"}, {"名字": "乙", "城市": "上海"}]

    def labels(widget):
        found = []

        def walk(node):
            for child in node.winfo_children():
                try:
                    found.append(str(child.cget("text")))
                except tk.TclError:
                    pass
                walk(child)

        walk(widget)
        return found

    def trees(widget):
        found = []

        def walk(node):
            for child in node.winfo_children():
                if isinstance(child, ttk.Treeview):
                    found.append(child)
                walk(child)

        walk(widget)
        return found

    dialog = gui_module.DataPreviewDialog(app, "d.csv", data)
    app.update()
    assert any("共 2 行数据 · 2 列：名字、城市" in t for t in labels(dialog)), \
        labels(dialog)
    grid = trees(dialog)
    assert grid, "应有数据表格"
    values = [grid[0].item(i, "values") for i in grid[0].get_children()]
    assert values == [("甲", "北京"), ("乙", "上海")], values
    dialog.ok()
    assert dialog.result is True
    dialog2 = gui_module.DataPreviewDialog(app, "d.csv", data)
    dialog2.cancel()
    assert dialog2.result is None
    mark("数据预览对话框")


def test_undo_redo_button_states():
    """工具栏撤销/重做按钮的启用态跟随栈深度。"""
    app._undo_stack.clear()
    app._redo_stack.clear()
    app._sync_undo_buttons()
    assert not app.btn_undo._enabled, "空栈应禁用"
    assert not app.btn_redo._enabled
    app._push_undo("测试操作")
    assert app.btn_undo._enabled and not app.btn_redo._enabled
    app.undo()
    assert not app.btn_undo._enabled and app.btn_redo._enabled
    app.redo()
    assert app.btn_undo._enabled and not app.btn_redo._enabled
    mark("撤销重做按钮态")


def test_save_indicator_and_ui_state():
    """保存状态指示 + 界面状态收集与恢复。"""
    app._mark_dirty()
    assert app.autosave_label.cget("text") == "未保存…"
    app._save_state()
    assert app.autosave_label.cget("text").startswith("已保存"), \
        app.autosave_label.cget("text")
    state = app._ui_state()
    assert isinstance(state["log_visible"], bool)
    assert "window" in state
    assert set(state["columns"]) == {"content", "note"}
    app.settings.update(state)          # 模拟下次启动读到的配置
    app._restore_ui_state()             # 不应炸
    mark("保存指示与界面状态")


def test_log_link_and_failed_locate():
    """出错日志行可点击跳转；失败步骤染色 + 状态栏定位。"""
    app._step_marks = {}
    app.project.groups = [
        Group("甲组", [Step("wait", "0.1"), Step("wait", "0.2")]),
        Group("乙组", [Step("wait", "0.3")])]
    app.group_index = 0
    app._refresh_all()
    app._current_run_step = ("甲组", 1)
    app._log("error", "第 2 步出错：图片未找到")
    app._drain_queue()
    assert app._step_log_lines, "出错日志行应记录步骤映射"
    line = next(iter(app._step_log_lines))
    assert app._step_log_lines[line] == ("甲组", 1)
    app.log_text.see(f"{line}.0")
    bbox = app.log_text.bbox(f"{line}.0")
    assert bbox, "日志行应可见"

    class _Event:
        x, y = bbox[0] + 2, bbox[1] + 2

    app._on_log_link_click(_Event())
    assert app.group_index == 0
    assert app.step_tree.selection() == ("1",), app.step_tree.selection()
    # 单步结果染色（与斑马纹标签共存，done/failed 建在 zebra 之前所以优先级更高）
    app._apply_step_done("甲组", 0, True)
    app._apply_step_done("甲组", 1, False)
    assert "done" in app.step_tree.item("0", "tags")
    assert "failed" in app.step_tree.item("1", "tags")
    assert app._failed_step == ("甲组", 1)
    # 摘要条
    app._show_summary("finished")
    app.update()
    assert app.summary_frame.winfo_ismapped()
    assert "执行完成" in app.summary_label.cget("text")
    app._hide_summary()
    assert not app.summary_frame.winfo_ismapped()
    # 切组后标记仍在（刷新不丢）
    app.group_index = 1
    app._refresh_steps()
    app.group_index = 0
    app._refresh_steps()
    assert "failed" in app.step_tree.item("1", "tags")
    mark("日志跳转与失败定位")


def test_global_run_hotkey_dispatch():
    """全局 F5：ui_queue 收到 run 后走 run_group 入口。"""
    fired = []
    real_run = app.run_group
    app.run_group = lambda: fired.append(True)
    try:
        app.ui_queue.put(("run", None))
        app._drain_queue()
        app.update()
        assert fired, "全局 F5 应触发 run_group"
    finally:
        app.run_group = real_run
    mark("全局运行热键分发")


def test_log_level_coloring():
    """运行日志按级别着色：info/warn/error 各打对应 tag。"""
    app.clear_log()
    for level, message in (("info", "着色测试-正常"), ("warn", "着色测试-警告"),
                           ("error", "着色测试-错误")):
        app._log(level, message)
    app._drain_queue()
    app.update()
    assert app.log_text.tag_ranges("error"), "error 日志应打 error 标签"
    assert app.log_text.tag_ranges("warn"), "warn 日志应打 warn 标签"
    assert app.log_text.tag_ranges("info"), "info 日志应打 info 标签"
    app.clear_log()
    mark("日志分级着色")


def test_shortcuts_dialog():
    """快捷键速查卡：内容、确定/取消返回值。"""
    dialog = gui_module.ShortcutsDialog(app)
    app.update()
    texts = []

    def walk(node):
        for child in node.winfo_children():
            try:
                texts.append(str(child.cget("text")))
            except tk.TclError:
                pass
            walk(child)

    walk(dialog)
    assert any("Ctrl+G" in t for t in texts), texts
    assert any("F9" in t for t in texts), texts
    dialog.ok()
    assert dialog.result is True
    dialog2 = gui_module.ShortcutsDialog(app)
    dialog2.cancel()
    assert dialog2.result is None
    mark("快捷键速查卡")


def test_step_dialog_types():
    """步骤对话框：类型下拉无旧编号；两段式条件类型有组名框和截图按钮。"""
    fake = gui_module.StepDialog          # 测试文件全局打了桩，先换回真身
    gui_module.StepDialog = _RealStepDialog
    try:
        dialog = _RealStepDialog(app, app.project, Step("click", "a.png"))
        app.update()
        values = dialog.combo.cget("values")
        assert all("·" not in v for v in values), values
        assert dialog._type_labels[0] == "左键单击"
        assert dialog._type_key() == "click"
        # 切到「条件：出现图片则执行」
        dialog.var_type.set("条件：出现图片则执行")
        dialog._sync()
        app.update()
        assert dialog.group_entry.winfo_ismapped(), "条件步骤应显示组名输入框"
        assert dialog.sep_if.winfo_ismapped(), "条件步骤应显示分组分隔线"
        assert dialog.btn_shot._enabled, "条件步骤应能截图选判断图"
        dialog.var_content.set("弹窗.png")
        dialog.var_group.set("关闭弹窗")
        dialog.ok()
        assert dialog.result.type == "if_image"
        assert dialog.result.content == "弹窗.png|关闭弹窗", dialog.result
        # 「条件：窗口存在则执行」同为两段式
        dialog2 = _RealStepDialog(app, app.project,
                                  Step("window", "activate:记事本"))
        app.update()
        dialog2.var_type.set("条件：窗口存在则执行")
        dialog2._sync()
        app.update()
        assert dialog2.group_entry.winfo_ismapped()
        assert not dialog2.btn_shot._enabled, "窗口条件不用图片"
        dialog2.cancel()
    finally:
        gui_module.StepDialog = fake
    mark("步骤对话框类型")


def test_capture_overlay_from_modal_dialog():
    """模态对话框里点截图：遮罩必须抢走 grab，否则拖拽/Esc 全收不到。

    真实报障：对话框里点「截图」出现遮罩后拖拽没反应、Esc 也取消不了。
    根因是对话框的 local grab 把整个程序内的鼠标/键盘事件全部截走，
    遮罩作为另一个 Toplevel 收不到任何输入。遮罩要抢过 grab，截图结束
    （保存或取消）后再还给对话框，恢复它的模态语义。
    """
    fake = gui_module.StepDialog          # 测试文件全局打了桩，先换回真身
    gui_module.StepDialog = _RealStepDialog
    saved_image_dir = app.image_dir
    app.image_dir = _TMP                  # 截图写临时目录，不污染 images/
    app.project.image_dir = _TMP
    try:
        dialog = _RealStepDialog(app, app.project, Step("click", ""))
        app.update()
        grab_dialog = app.grab_current()
        assert grab_dialog is dialog, "对话框应持有模态 grab"

        def start_capture():
            """点「截图」并泵事件循环，等到遮罩抢到 grab；返回遮罩或 None。"""
            dialog.btn_shot.command()
            for _ in range(100):         # after(320) 之后才建遮罩
                app.update()
                time.sleep(0.02)
                overlay = app.grab_current()
                if overlay is not None and overlay is not grab_dialog:
                    return overlay
            return None

        def settle():
            for _ in range(30):
                app.update()
                time.sleep(0.02)

        # --- 拖拽保存 ---
        overlay = start_capture()
        assert overlay is not None, "点截图后应出现遮罩并抢到 grab"
        assert app.winfo_ismapped() == 0, "截图时主窗应隐藏"
        canvas = [w for w in overlay.winfo_children()
                  if w.winfo_class() == "Canvas"][0]
        canvas.event_generate("<ButtonPress-1>", x=100, y=100)
        canvas.event_generate("<B1-Motion>", x=260, y=180)
        canvas.event_generate("<ButtonRelease-1>", x=260, y=180)
        settle()
        name = dialog.var_content.get()
        assert name.endswith(".png"), f"拖拽后内容框应填上文件名: {name!r}"
        assert os.path.exists(os.path.join(_TMP, name)), \
            f"截图文件应已保存: {name}"
        assert app.grab_current() is grab_dialog, "结束后 grab 应还给对话框"
        assert app.winfo_ismapped() == 1, "结束后主窗应恢复"

        # --- Esc 取消 ---
        overlay2 = start_capture()
        assert overlay2 is not None, "第二次点截图也应出现遮罩"
        overlay2.event_generate("<Escape>")
        settle()
        assert app.grab_current() is grab_dialog, \
            "Esc 取消后 grab 应还给对话框"
        assert app.winfo_ismapped() == 1, "Esc 取消后主窗应恢复"
        assert dialog.var_content.get() == name, "取消后内容框不应变"
        dialog.destroy()
    finally:
        app.image_dir = saved_image_dir
        app.project.image_dir = saved_image_dir
        gui_module.StepDialog = fake
    mark("对话框截图 grab 抢占与恢复")


def test_type_icons_cover_all():
    """每种指令类型都要有图标和色调，否则列表里会出现兜底方块。"""
    for key in STEP_TYPES:
        assert key in gui_module.TYPE_ICONS, key
        image = gui_module.uikit.icon_image(gui_module.TYPE_ICONS[key], 16,
                                            "#333333")
        assert image is not None
        assert key in gui_module.TYPE_COLORS, f"{key} 缺少图标色调"
    mark("类型图标覆盖")


def test_step_dialog_new_types():
    """步骤对话框新类型：if_else 双组输入、drag 分段填充与超时、编辑回显。"""
    fake = gui_module.StepDialog          # 测试文件全局打了桩，先换回真身
    gui_module.StepDialog = _RealStepDialog
    try:
        # if_else：出现/没出现两个组名框，确定后拼成三段
        dialog = _RealStepDialog(app, app.project, Step("click", "a.png"))
        app.update()
        dialog.var_type.set("条件：二选一")
        dialog._sync()
        app.update()
        assert dialog.group_entry.winfo_ismapped()
        assert dialog.group2_entry.winfo_ismapped(), "if_else 要有第二个组名框"
        dialog._toggle_advanced()
        app.update()
        assert dialog.spin_timeout.winfo_ismapped(), "条件步骤应支持最多等几秒"
        dialog.var_content.set("弹窗.png")
        dialog.var_group.set("关弹窗")
        dialog.var_group2.set("等弹窗")
        dialog.ok()
        assert dialog.result.type == "if_else"
        assert dialog.result.content == "弹窗.png|关弹窗|等弹窗"
        # 编辑回显：三段拆回三个输入框
        dialog2 = _RealStepDialog(app, app.project, dialog.result)
        app.update()
        assert dialog2.var_content.get() == "弹窗.png"
        assert dialog2.var_group.get() == "关弹窗"
        assert dialog2.var_group2.get() == "等弹窗"
        # 第二组留空也合法（没出现时什么都不做）
        dialog3 = _RealStepDialog(app, app.project,
                                  Step("if_else", "a.png|甲|"))
        app.update()
        assert dialog3.var_group2.get() == ""
        dialog3.cancel()
        # if_else 多出来的一行不能把确定按钮顶出对话框
        dialog4 = _RealStepDialog(app, app.project, Step("click", "a.png"))
        app.update()
        dialog4.var_type.set("条件：二选一")
        dialog4._sync()
        app.update()
        bottom = dialog4.btn_ok.winfo_rooty() + dialog4.btn_ok.winfo_height()
        assert bottom <= dialog4.winfo_rooty() + dialog4.winfo_height(), \
            "if_else 的第二行把按钮顶出了对话框"
        dialog4.cancel()

        # drag：有等待超时（两端可能是图）、拾取坐标可用、分段填充
        dialog5 = _RealStepDialog(app, app.project, Step("click", "a.png"))
        app.update()
        dialog5.var_type.set("拖拽")
        dialog5._sync()
        dialog5._toggle_advanced()
        app.update()
        assert dialog5.spin_timeout.winfo_ismapped(), "拖拽要能设等待超时"
        assert dialog5.btn_grab_xy._enabled, "拖拽要能拾取坐标"
        dialog5.var_content.set("")            # 切换类型后清掉旧内容
        dialog5._fill_drag_part("100,200")
        assert dialog5.var_content.get() == "100,200|"
        dialog5._fill_drag_part("300,400")
        assert dialog5.var_content.get() == "100,200|300,400"
        dialog5._fill_drag_part("500,600")     # 都满了：替换起点
        assert dialog5.var_content.get() == "500,600|300,400"
        dialog5.var_content.set("100,200|300,400")
        dialog5.ok()
        assert dialog5.result.type == "drag"
        assert dialog5.result.timeout == 0 and dialog5.result.on_fail == "skip"

        # set：变量步骤不显示等待超时
        dialog6 = _RealStepDialog(app, app.project, Step("input", "x"))
        app.update()
        dialog6.var_type.set("设置变量")
        dialog6._sync()
        app.update()
        assert not dialog6.spin_timeout.winfo_ismapped()
        dialog6.var_content.set("订单号=ABC123")
        dialog6.ok()
        assert dialog6.result.type == "set"
        assert dialog6.result.content == "订单号=ABC123"
    finally:
        gui_module.StepDialog = fake
    mark("步骤对话框新类型")


def test_image_preview_dialog():
    """步骤对话框「预览」按钮：弹窗显示内容框里的图片，缺图不弹窗。"""
    fake = gui_module.StepDialog          # 测试文件全局打了桩，先换回真身
    gui_module.StepDialog = _RealStepDialog
    # 造一张真实图片放进临时图片目录（与 image_dir 隔离开）
    from PIL import Image

    image_dir = os.path.join(_TMP, "preview_dir")
    os.makedirs(image_dir, exist_ok=True)
    Image.new("RGB", (240, 160), "#4d8dff").save(
        os.path.join(image_dir, "preview_demo.png"))
    saved_image_dir = app.image_dir
    app.image_dir = image_dir
    try:
        dialog = _RealStepDialog(app, app.project, Step("click", ""))
        app.update()
        assert dialog.btn_preview.winfo_ismapped(), "图片步骤应有预览按钮"
        # 内容非图片步骤时收起
        dialog.var_type.set("输入文本")
        dialog._sync()
        app.update()
        assert not dialog.btn_preview.winfo_ismapped(), "非图片类型应收起预览"

        dialog.var_type.set("左键单击")
        dialog._sync()
        app.update()
        dialog.var_content.set("preview_demo.png")
        dialog.btn_preview.command()
        app.update()
        previews = [w for w in app.winfo_children()
                    if isinstance(w, gui_module.ImagePreviewDialog)]
        assert len(previews) == 1, "点预览应弹出预览窗"
        preview = previews[0]
        assert preview._photo.width() == 240 and \
            preview._photo.height() == 160, "预览应显示原尺寸的图"
        label = [w for w in preview.winfo_children()
                 if w.winfo_class() == "Label" and w.cget("text")][0]
        assert label.cget("text") == "preview_demo.png", label.cget("text")
        # 关掉预览窗后 grab 应还给步骤对话框（否则对话框按钮全点不动）
        grab_before = app.grab_current()
        assert grab_before is preview
        preview.cancel()
        app.update()
        assert app.grab_current() is dialog, "预览窗关闭后 grab 应还给对话框"

        # 文件不存在：只在提示区说明，不弹窗也不崩
        warnings = []
        real_warning = gui_module.messagebox.showwarning
        gui_module.messagebox.showwarning = \
            lambda *a, **k: warnings.append(a)
        try:
            dialog.var_content.set("没有这张图.png")
            dialog.btn_preview.command()
            app.update()
        finally:
            gui_module.messagebox.showwarning = real_warning
        assert not warnings, "缺图时不应弹 warning 框"
        assert "找不到图片文件" in dialog.hint.cget("text")
        dialog.cancel()
    finally:
        app.image_dir = saved_image_dir
        gui_module.StepDialog = fake
    mark("步骤对话框图片预览")


def test_step_search_filter():
    """步骤搜索：按类型/内容/备注过滤 + 匹配计数 + 过滤中禁拖动。"""
    app.project.groups = [Group("搜索组", [
        Step("click", "登录按钮.png", 1, "重要"),
        Step("input", "admin"),
        Step("wait", "0.5"),
        Step("hotkey", "ctrl,s")])]
    app.group_index = 0
    app._refresh_all()
    assert len(app.step_tree.get_children()) == 4

    def search(text):
        app.step_search.delete(0, "end")
        app.step_search.insert(0, text)
        app._apply_step_filter()

    search("登录")                      # 内容列匹配
    assert app.step_tree.get_children() == ("0",)
    assert app.search_count.cget("text") == "1/4"
    search("热键")                      # 类型中文标签匹配
    assert app.step_tree.get_children() == ("3",)
    search("重要")                      # 备注匹配
    assert app.step_tree.get_children() == ("0",)
    search("ctrl")                      # 类型标识匹配（小写不敏感）
    assert app.step_tree.get_children() == ("3",)
    search(gui_module.SEARCH_PLACEHOLDER)   # 占位符不算过滤词
    assert len(app.step_tree.get_children()) == 4
    assert app.search_count.cget("text") == ""
    search("不存在的词")                # 无匹配 → 空状态文案
    assert app.step_tree.get_children() == ()
    assert "不存在的词" in app.step_empty.cget("text")
    app._on_step_drop("0", "1")         # 过滤中禁止拖动排序
    assert "搜索" in app.status_var.get()
    app._clear_step_filter()            # 清空恢复
    assert len(app.step_tree.get_children()) == 4
    assert app.search_count.cget("text") == ""
    mark("步骤搜索过滤")


def test_progress_and_mini_bar():
    """运行进度：确定/脉冲两种模式 + 迷你置顶条生命周期。"""
    app._reset_progress(4, pulse=False)
    assert str(app.progress.cget("mode")) == "determinate"
    assert app.progress.cget("maximum") == 4
    assert app.progress_label.cget("text") == "0/4"
    app._apply_progress(2, 4)
    assert app.progress["value"] == 2
    assert app.progress_label.cget("text") == "2/4"
    app._reset_progress(-1, pulse=True)     # 循环运行 → 脉冲
    assert str(app.progress.cget("mode")) == "indeterminate"
    app._apply_progress(7, -1)
    assert app.progress_label.cget("text") == "已执行 7 步"
    app._finish_progress()
    assert str(app.progress.cget("mode")) == "determinate"
    # 迷你条：创建 / 更新 / 销毁
    app.settings["show_mini_bar"] = True
    app._show_mini_bar()
    assert app._mini_bar is not None
    app._mini_bar.set_counts(3, 9)
    assert app._mini_bar.counts.cget("text") == "3/9 步"
    app._mini_bar.set_step("1. 左键单击 登录按钮.png")
    assert "左键单击" in app._mini_bar.text.cget("text")
    app._mini_bar.set_counts(5, -1)
    assert app._mini_bar.counts.cget("text") == "已执行 5 步"
    app._hide_mini_bar()
    assert app._mini_bar is None
    # 设置里关掉迷你条 → 不再出现
    app.settings["show_mini_bar"] = False
    app._show_mini_bar()
    assert app._mini_bar is None
    app.settings["show_mini_bar"] = True
    mark("运行进度与迷你条")


def test_image_preview_pane():
    """选中图片类步骤→预览窗格显示缩略图与信息；其它步骤收起。"""
    tmp = tempfile.mkdtemp(prefix="rpa_img_")
    app.image_dir = tmp
    try:
        from PIL import Image

        Image.new("RGB", (160, 90), (200, 60, 60)).save(
            os.path.join(tmp, "按钮.png"))
        app.project.groups = [Group("预览组", [
            Step("click", "按钮.png"), Step("wait", "0.1")])]
        app.group_index = 0
        app._refresh_all()
        app.step_tree.selection_set("0")
        app._update_preview()
        app.update()
        assert app.preview_frame.winfo_ismapped(), "图片步骤应显示预览"
        assert app.preview_name.cget("text") == "按钮.png"
        assert "160 × 90" in app.preview_meta.cget("text")
        app.step_tree.selection_set("1")
        app._update_preview()
        app.update()
        assert not app.preview_frame.winfo_ismapped(), "非图片步骤应收起"
        # 图片文件不存在 → 同样不显示
        app.project.groups[0].steps[0] = Step("click", "不存在.png")
        app._refresh_all()
        app.step_tree.selection_set("0")
        app._update_preview()
        assert not app.preview_frame.winfo_ismapped()
        # 两段式条件类型取 | 前的图片名
        app.project.groups[0].steps[0] = Step("if_image", "按钮.png|预览组")
        app._refresh_all()
        app.step_tree.selection_set("0")
        app._update_preview()
        app.update()
        assert app.preview_frame.winfo_ismapped()
    finally:
        app.image_dir = gui_module.DEFAULT_IMAGE_DIR
    mark("图片预览窗格")


def test_zebra_and_type_colors():
    """斑马纹：奇偶行交替；运行标记优先级更高（不被斑马纹盖掉）。"""
    app.project.groups = [Group("斑马组", [
        Step("click", "a.png"), Step("input", "x"),
        Step("wait", "0.1"), Step("cmd", "dir")])]
    app.group_index = 0
    app._refresh_all()
    assert app.step_tree.item("0", "tags") == ("even",)
    assert app.step_tree.item("1", "tags") == ("odd",)
    assert app.step_tree.item("2", "tags") == ("even",)
    assert app.step_tree.item("0", "image") is not None
    app._step_marks = {"斑马组": {1: "failed"}}
    app._repaint_step_marks()
    assert set(app.step_tree.item("1", "tags")) == {"odd", "failed"}
    app._step_marks = {}
    app._repaint_step_marks()
    assert app.step_tree.item("1", "tags") == ("odd",)
    mark("斑马纹与类型着色")


def test_settings_dialog_preserves_keys():
    """运行设置：改字段不冲掉定时运行等配置；迷你进度条开关可存。"""
    real = gui_module.SettingsDialog
    gui_module.SettingsDialog = _RealSettingsDialog
    try:
        app.settings.update({"delay": 5,
                             "schedule": {"enabled": False, "time": "09:00",
                                          "group": "甲组"},
                             "show_mini_bar": True})
        dialog = _RealSettingsDialog(app, app.settings, app.image_dir)
        app.update()
        assert dialog.var_mini.get() is True
        dialog.var_delay.set("9")
        dialog.var_mini.set(False)
        dialog.ok()
        app.update()
        assert dialog.result["delay"] == 9
        assert dialog.result["show_mini_bar"] is False
        assert dialog.result["schedule"] == {"enabled": False, "time": "09:00",
                                             "group": "甲组"}, "其它配置被冲掉了"
        app.settings = dialog.result
        app._save_state()
        app._set_saved_label()
    finally:
        gui_module.SettingsDialog = real
        app.settings.update({"delay": 3,
                             "schedule": {"enabled": False, "time": "09:00",
                                          "group": "甲组"},
                             "show_mini_bar": True})
    mark("设置对话框保留其它配置")


def test_canvas_cat_empty_state():
    """空状态用 Canvas 小猫上半身，不依赖 PIL 图片，并带眨眼动画。"""
    for cat in (app.step_empty_icon, app.group_empty_icon):
        assert isinstance(cat, gui_module.uikit.CatCanvas), type(cat)
        # 身体 / 项圈 / 双耳内外 ×4 / 胡须 ×8 / 头 / 腮帮 ×2 /
        # 杏仁眼 ×2 / 瞳孔 ×2 / 高光 ×2 / 倒三角鼻 / Y 嘴 ×2
        items = cat.find_withtag("cat")
        assert len(items) == 26, len(items)
        fills = {str(cat.itemcget(item, "fill")) for item in items}
        assert gui_module.TEXT in fills, fills
        assert gui_module.ACCENT in fills, fills
        assert gui_module.ACCENT_STRONG in fills, fills
        # 手动走一次眨眼，确认临时弧线出现又消失
        cat._cancel_after("_blink_after")
        cat._blink()
        assert cat._eyes_hidden
        assert len(cat.find_withtag("blink")) == 2
        cat._open_eyes()
        assert not cat._eyes_hidden
        assert not cat.find_withtag("blink")
    mark("Canvas 小猫空状态")


def test_theme_switch():
    """深色主题：模块常量 / 控件树 / 树标签 / 图标色整体切换，可还原。"""
    light_bg = gui_module.BG
    light_card = gui_module.CARD
    app._apply_theme(True)
    app.update()
    assert gui_module.BG == "#12110F", "模块常量应换成暗色"
    assert gui_module.CARD == "#1D1B18"
    assert app.cget("bg") == "#12110F", "主窗口底色应跟上"
    assert str(app.log_card.cget("bg")) == "#1D1B18", "卡片底色应跟上"
    assert str(app.status_var.get()) != ""      # 界面还活着
    assert gui_module.TYPE_COLORS["click"] == "#FDAD63", "类型图标应提亮"
    assert str(app.btn_run["bg"]) == "#12110F", "按钮底色应跟上"
    assert str(app.step_title.cget("fg")) == "#F5F2ED", "正文应换成暖白"
    assert str(app.log_text.cget("fg")) == "#F7F4EF", "日志正文保持暖白"
    # Canvas 小猫跟随主题重绘（不是 PIL 图片，不能靠 configure(image) 换色）
    assert app.step_empty_icon.paper == gui_module.CARD
    assert app.step_empty_icon.ink == gui_module.TEXT
    assert app.step_empty_icon.accent == gui_module.ACCENT
    assert str(app.step_empty_icon.cget("bg")) == gui_module.CARD
    fills = {str(app.step_empty_icon.itemcget(item, "fill"))
             for item in app.step_empty_icon.find_all()}
    assert gui_module.TEXT in fills and gui_module.ACCENT in fills, fills
    # 树标签重配（tag_configure 返回的是 Tk string 包装对象，要 str() 一下）
    assert str(app.step_tree.tag_configure("odd", "background")) == "#25221E"
    assert str(app.step_tree.tag_configure("failed", "background")) == \
        "#352322"
    # 切回浅色：全部还原
    app._apply_theme(False)
    app.update()
    assert gui_module.BG == light_bg
    assert gui_module.CARD == light_card
    assert app.cget("bg") == light_bg
    assert str(app.btn_run["bg"]) == light_bg
    assert gui_module.TYPE_COLORS["click"] == "#E8861F"
    assert str(app.step_title.cget("fg")) == "#080806", "正文应还原成墨黑"
    assert str(app.log_text.cget("fg")) == "#F5F2ED", "日志正文仍保持暖白"
    # toggle_theme：翻 settings 并落盘，两次切换回到原样
    original = app.dark                  # 此时是浅色（前面已还原）
    app.toggle_theme()
    assert app.dark is (not original)
    assert app.settings.get("theme") == ("dark" if app.dark else "light")
    app.toggle_theme()
    assert app.dark is original
    assert app.settings.get("theme") == ("dark" if app.dark else "light")
    assert gui_module.BG == light_bg, "两次切换后应回到浅色"
    mark("深色主题切换")


def test_batch_step_ops():
    """批量多选：全选 / 批量复制 / 批量删除 / 整块移动。"""
    app.project.groups = [Group("批量组", [
        Step("wait", "0.1"), Step("input", "a"), Step("wait", "0.2"),
        Step("cmd", "dir")])]
    app.group_index = 0
    app._refresh_all()
    group = app.project.groups[0]

    app.step_tree.focus_set()
    app._select_all_steps()
    assert app._selected_steps() == [0, 1, 2, 3]

    # 批量复制第 0、2 步 → 副本紧跟在最后选中行之后
    app.step_tree.selection_set("0", "2")
    app.duplicate_step()
    assert [s.content for s in group.steps] == \
        ["0.1", "a", "0.2", "0.1", "0.2", "dir"], \
        [s.content for s in group.steps]

    # 批量删除第 1、2 步
    app.step_tree.selection_set("1", "2")
    app.delete_step()
    assert [s.content for s in group.steps] == ["0.1", "0.1", "0.2", "dir"]

    # 整块上移：选中 2、3（0.2、dir）→ 插到索引 1 之后
    app.step_tree.selection_set("2", "3")
    app.move_step(-1)
    assert [s.content for s in group.steps] == ["0.1", "0.2", "dir", "0.1"], \
        [s.content for s in group.steps]

    # 单选上移保持原行为
    app.step_tree.selection_set("3")
    app.move_step(-1)
    assert [s.content for s in group.steps] == ["0.1", "0.2", "0.1", "dir"]

    # 删除键走批量删除（确认框已自动应答 True）
    app.step_tree.selection_set("0", "1")
    app._on_step_key_delete()
    assert [s.content for s in group.steps] == ["0.1", "dir"]
    mark("批量多选操作")


def test_drag_indicator_line():
    """拖拽落点：指示线在拖拽中出现、松手消失，并完成换行。"""
    app.project.groups = [Group("拖拽组", [
        Step("wait", str(index)) for index in range(4)])]
    app.group_index = 0
    app._refresh_all()
    tree = app.step_tree
    app.update()
    bbox = tree.bbox("1")
    assert bbox, "第 2 行应可见"
    _x, top, _width, height = bbox
    root_y = tree.winfo_rooty()
    # 落点坐标：行上半段→行顶，下半段→行底
    assert gui_module._insert_y(tree, top + 1) == root_y + top
    assert gui_module._insert_y(tree, top + height - 1) == root_y + top + height
    # 第一行之上 → 贴行顶；最后一行之下 → 贴行底
    first = tree.bbox("0")
    assert gui_module._insert_y(tree, first[1] - 5) == root_y + first[1]
    last = tree.bbox("3")
    assert gui_module._insert_y(tree, last[1] + last[3] + 50) == \
        root_y + last[1] + last[3]

    def has_line():
        return any(isinstance(child, tk.Toplevel)
                   for child in tree.winfo_children())

    before = [s.content for s in app.project.groups[0].steps]
    tree.event_generate("<ButtonPress-1>", x=6, y=top + 2)
    tree.event_generate("<B1-Motion>", x=6, y=top + height + 10)
    app.update()
    assert has_line(), "拖拽中应出现落点指示线"
    tree.event_generate("<ButtonRelease-1>", x=6, y=top + height + 10)
    app.update()
    assert not has_line(), "松手后指示线应消失"
    after = [s.content for s in app.project.groups[0].steps]
    assert after != before, "拖拽应触发换行"
    mark("拖拽落点指示线")


def test_debug_step_dialog():
    """单步调试对话框：内容展示 + 三种选择 + 关窗即停止。"""
    dialog = gui_module.DebugStepDialog(app, "甲组", 1,
                                        Step("click", "按钮.png", 2))
    app.update()
    texts = []

    def walk(node):
        for child in node.winfo_children():
            try:
                texts.append(str(child.cget("text")))
            except tk.TclError:
                pass
            walk(child)

    walk(dialog)
    assert any("第 2 步" in t for t in texts), texts
    assert any("重复 2 次" in t for t in texts), texts
    dialog._skip()
    assert dialog.result == "skip"
    dialog2 = gui_module.DebugStepDialog(app, "甲组", 0, Step("wait", "0.1"))
    app.update()
    dialog2._run()
    assert dialog2.result == "run"
    dialog3 = gui_module.DebugStepDialog(app, "甲组", 0, Step("wait", "0.1"))
    app.update()
    dialog3.cancel()               # Esc / 关窗 = 停止运行
    assert dialog3.result == "stop"
    mark("单步调试对话框")


def test_debug_confirm_roundtrip():
    """单步确认的线程接力：工作线程入队 → 主线程应答 → 结果回传。"""
    import threading as _threading

    # 主线程侧（_ask_debug）：对话框返回什么就回填什么
    real_dialog = gui_module.DebugStepDialog

    class _Fake:
        def __init__(self, parent, name, index, step):
            self.result = "run"

        def wait(self):
            return self.result

    gui_module.DebugStepDialog = _Fake
    try:
        event, box = _threading.Event(), {}
        app._ask_debug("甲组", 0, Step("wait", "0.1"), event, box)
        assert event.is_set() and box["answer"] == "run"
    finally:
        gui_module.DebugStepDialog = real_dialog

    # 工作线程侧（_confirm_step）：入队后阻塞，被应答后拿到答案
    calls = []
    real_ask = app._ask_debug

    def fake_ask(name, index, step, event, box):
        calls.append((name, index, step.type))
        box["answer"] = "skip"
        event.set()

    app._ask_debug = fake_ask
    try:
        result = {}

        def worker():
            result["answer"] = app._confirm_step("甲组", 1, Step("wait", "0.2"))

        thread = _threading.Thread(target=worker, daemon=True)
        thread.start()
        deadline = time.time() + 10
        while time.time() < deadline and "answer" not in result:
            app.update()               # 让 _drain_queue 把确认请求取走
            time.sleep(0.02)
    finally:
        app._ask_debug = real_ask
    assert calls, "确认请求应经界面队列处理"
    assert calls[0][0] == "甲组" and calls[0][1] == 1
    assert result.get("answer") == "skip"
    mark("单步确认线程接力")


def test_settings_dialog_mini_bar():
    """设置对话框：迷你进度条开关读写（真身，避免被全局桩挡住）。"""
    real = gui_module.SettingsDialog
    gui_module.SettingsDialog = _RealSettingsDialog
    try:
        app.settings["show_mini_bar"] = True
        dialog = _RealSettingsDialog(app, app.settings, app.image_dir)
        app.update()
        assert dialog.var_mini.get() is True
        dialog.var_mini.set(False)
        dialog.ok()
        assert dialog.result["show_mini_bar"] is False
        app.settings = dialog.result
    finally:
        gui_module.SettingsDialog = real
        app.settings["show_mini_bar"] = True
    mark("设置对话框迷你条开关")


def test_step_dialog_advanced_roundtrip():
    """高级选项：默认折叠，展开后能回显并保存范围/第几个/灰度/重试。"""
    fake = gui_module.StepDialog
    gui_module.StepDialog = _RealStepDialog
    try:
        step = Step("click", "a.png", region="10,20,30,40", nth=1,
                    grayscale=True, retry=2, retry_delay=0.25, timeout=5)
        dialog = _RealStepDialog(app, app.project, step)
        app.update()
        assert not dialog.advanced.winfo_ismapped(), "高级选项默认折叠"
        assert dialog.var_region.get() == "10,20,30,40"
        assert dialog.var_nth.get() == "2"
        assert dialog.var_gray.get() is True
        assert dialog.var_retry.get() == "2"
        dialog._toggle_advanced()
        app.update()
        assert dialog.advanced.winfo_ismapped()
        assert dialog.spin_timeout.winfo_ismapped()

        dialog.var_region.set("1,2,300,400")
        dialog.var_nth.set("3")
        dialog.var_gray.set(False)
        dialog.var_retry.set("1")
        dialog.var_retry_delay.set("0.5")
        dialog.ok()
        result = dialog.result
        assert result.region == "1,2,300,400"
        assert result.nth == 2
        assert result.grayscale is False
        assert result.retry == 1 and result.retry_delay == 0.5

        d2 = _RealStepDialog(app, app.project,
                             Step("input", "abc", input_mode="type"))
        app.update()
        d2._toggle_advanced()
        app.update()
        assert "逐字" in d2.var_input_mode.get()
        d2.ok()
        assert d2.result.input_mode == "type"
    finally:
        gui_module.StepDialog = fake
    mark("高级选项回显与保存")


def test_settings_dialog_result_flags():
    """设置里可以关掉失败截图 / 结果回写，且不冲掉其它键。"""
    dialog = _RealSettingsDialog(
        app, {"shot_on_fail": False, "schedule": {"enabled": True}},
        app.image_dir)
    app.update()
    assert dialog.var_shot.get() is False
    assert dialog.var_results.get() is True
    dialog.var_shot.set(True)
    dialog.var_results.set(False)
    dialog.ok()
    assert dialog.result["shot_on_fail"] is True
    assert dialog.result["save_results"] is False
    assert dialog.result["schedule"] == {"enabled": True}, "定时配置应保留"
    mark("截图/结果开关")


def test_schedule_task_command():
    """计划任务：时间、组名、命令行参数正确（不真的调用 schtasks）。"""
    from rpa.gui import ScheduleDialog

    group = app.project.names()[0]
    dialog = ScheduleDialog(app, {}, app.project.names())
    dialog.var_group.set(group)
    dialog.var_hour.set("7")
    dialog.var_minute.set("5")
    calls = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    original_save = app._save_state
    app._save_state = lambda: None
    dialog._schtasks = lambda args: (calls.append(args) or _Proc())
    try:
        dialog._create_task()
    finally:
        app._save_state = original_save
    assert calls, "应调用 schtasks"
    args = calls[0]
    assert args[:2] == ["/Create", "/SC"]
    assert args[args.index("/ST") + 1] == "07:05"
    name = args[args.index("/TN") + 1]
    assert name.startswith("MeowGo_") and group in name
    command = args[args.index("/TR") + 1]
    assert "--run" in command and group in command
    assert "已创建" in dialog.lbl_task.cget("text")
    dialog.destroy()
    mark("Windows 计划任务命令")


def test_result_writeback_from_gui():
    """数据跑完后从界面收尾逻辑写结果文件。"""
    from rpa.engine import read_results, result_path

    path = os.path.join(_TMP, "gui_data.csv")
    app.executor.data = [{"订单": "A"}]
    app.executor.row_results = [{"row": 1, "ok": True, "failed_step": "",
                                 "vars": {"金额": "9"}}]
    app._data_path = path
    old = app.settings.get("save_results", True)
    app.settings["save_results"] = True
    try:
        app._write_result_file()
        rows = read_results(result_path(path))
        assert rows[0]["订单"] == "A"
        assert rows[0]["__结果"] == "成功"
        assert rows[0]["金额"] == "9"
    finally:
        app.settings["save_results"] = old
        app._data_path = ""
        app.executor.row_results = []
    mark("界面结果回写")


def test_resume_retries_failed_tail():
    """断点续跑：最后一行失败时从失败行继续，而不是跳过它。"""
    from rpa import engine as engine_module

    saved_open = gui_module.filedialog.askopenfilename
    saved_load = engine_module.load_data
    saved_read = engine_module.read_results
    saved_preview = gui_module.DataPreviewDialog
    saved_ask = gui_module.messagebox.askyesno
    saved_start = app._start
    asked = []
    started = {}

    class _Preview:
        def __init__(self, *args, **kwargs):
            pass

        def wait(self):
            return True

    def fake_start(entry, **kwargs):
        started.update(kwargs)

    try:
        gui_module.filedialog.askopenfilename = lambda **kwargs: "data.csv"
        engine_module.load_data = lambda path: [
            {"名字": "甲"}, {"名字": "乙"}, {"名字": "丙"}]
        engine_module.read_results = lambda path: [
            {"__行号": "1", "__结果": "成功"},
            {"__行号": "2", "__结果": "失败"},
        ]
        gui_module.DataPreviewDialog = _Preview
        gui_module.messagebox.askyesno = (
            lambda title, text, **kwargs: (asked.append(text) or True))
        app._start = fake_start
        app.run_with_data()
    finally:
        gui_module.filedialog.askopenfilename = saved_open
        engine_module.load_data = saved_load
        engine_module.read_results = saved_read
        gui_module.DataPreviewDialog = saved_preview
        gui_module.messagebox.askyesno = saved_ask
        app._start = saved_start

    assert started.get("row_offset") == 1, started
    assert "第 2 行失败" in asked[0], asked
    mark("断点续跑重试失败行")


def test_default_groups():
    """首次启动预置 5 个模板组；升级不覆盖用户组，也不复活已删除的旧组。"""
    names = [name for name, _steps in _INITIAL_GROUPS]
    assert names == ["打开浏览器", "打开软件", "批量打开网址", "上传文件",
                     "打开工作环境"], names
    groups = dict(_INITIAL_GROUPS)
    browser_steps = groups["打开浏览器"]
    assert [step_type for step_type, _content in browser_steps] == [
        "set", "set", "set", "cmd", "window", "hotkey", "input", "hotkey"]
    assert browser_steps[0][1] == "浏览器=chrome"
    assert browser_steps[1][1] == "窗口标题=Chrome"
    assert browser_steps[2][1] == "网址=https://www.baidu.com"
    assert browser_steps[3][1] == 'start "" "{浏览器}"'
    assert browser_steps[4][1] == "wait:{窗口标题}|10"
    assert browser_steps[6][1] == "{网址}"
    software_steps = groups["打开软件"]
    assert [step_type for step_type, _content in software_steps] == [
        "set", "cmd"]
    assert software_steps[0][1] == "软件=notepad"
    assert software_steps[1][1] == 'start "" "{软件}"'
    batch_steps = groups["批量打开网址"]
    assert [step_type for step_type, _content in batch_steps] == [
        "set", "set", "cmd", "wait"]
    assert batch_steps[2][1] == 'start "" "{浏览器}" "{网址}"'
    upload_steps = groups["上传文件"]
    assert [step_type for step_type, _content in upload_steps] == [
        "set", "click", "wait", "hotkey", "input", "hotkey"]
    assert upload_steps[1][1] == "上传按钮.png"
    assert upload_steps[3][1] == "alt,n"
    assert upload_steps[4][1] == "{文件路径}"
    workspace_steps = groups["打开工作环境"]
    assert [step_type for step_type, _content in workspace_steps] == [
        "set", "set", "set", "cmd", "wait", "cmd"]
    assert workspace_steps[5][1] == 'start "" "{浏览器}" "{网址}"'

    # 已有组保留，只补充缺失的预置组
    project = Project(groups=[Group("我的组", [Step("wait", "1")], "保留我")])
    settings = {}
    assert gui_module.seed_default_groups(project, settings) is True
    assert project.names() == ["我的组"] + names, project.names()
    assert project.get("我的组").steps[0].content == "1"
    assert project.get("我的组").note == "保留我"

    # 第二次启动不重复添加
    count = len(project.groups)
    assert gui_module.seed_default_groups(project, settings) is False
    assert len(project.groups) == count

    # 旧版留下的唯一空「默认组」占位符会被替换；用户自己的空组不删
    placeholder = Project(groups=[Group("默认组")])
    gui_module.seed_default_groups(placeholder, {})
    assert placeholder.names() == names, placeholder.names()
    custom_empty = Project(groups=[Group("我的空组")])
    gui_module.seed_default_groups(custom_empty, {})
    assert custom_empty.names() == ["我的空组"] + names, custom_empty.names()

    # v1 -> v2：没改过的浏览器模板原地升级，新增模板补在后面
    legacy_browser = Group(
        "打开浏览器",
        [
            Step("set", "浏览器=chrome",
                 note="chrome / msedge / 完整 exe 路径"),
            Step("set", "网址=https://www.baidu.com",
                 note="要打开的链接"),
            Step("cmd", 'start "" "{浏览器}"',
                 note="按变量启动浏览器"),
            Step("wait", "2",
                 note="等浏览器启动；慢就改大"),
            Step("hotkey", "ctrl,l", note="聚焦地址栏"),
            Step("input", "{网址}", note="输入网址"),
            Step("hotkey", "enter", note="打开网址"),
        ],
        note="预置模板：改前两步变量即可运行；浏览器启动慢时把等待秒数改大。",
    )
    legacy_software = Group(
        "打开软件",
        [Step("set", "软件=notepad",
              note="exe 名或完整路径，如 notepad / calc"),
         Step("cmd", 'start "" "{软件}"',
              note="按变量启动软件；找不到就填完整路径")],
        note="预置模板：把「软件」变量改成要打开的程序名或完整 exe 路径。",
    )
    upgraded = Project(groups=[legacy_browser, legacy_software])
    v1_settings = {gui_module.DEFAULT_GROUPS_KEY: 1}
    assert gui_module.seed_default_groups(upgraded, v1_settings) is True
    assert upgraded.names() == names, upgraded.names()
    assert not validate_project(upgraded.groups, check_images=False), \
        validate_project(upgraded.groups, check_images=False)
    new_browser = upgraded.get("打开浏览器")
    assert [step.type for step in new_browser.steps] == [
        "set", "set", "set", "cmd", "window", "hotkey", "input", "hotkey"]
    assert new_browser.steps[1].content == "窗口标题=Chrome"
    assert v1_settings[gui_module.DEFAULT_GROUPS_KEY] == 2

    # v1 用户删掉的旧组不加回；只补 v2 新增的三个模板
    only_software = Project(groups=[Group("打开软件", [Step("wait", "1")])])
    v1_settings = {gui_module.DEFAULT_GROUPS_KEY: 1}
    assert gui_module.seed_default_groups(only_software, v1_settings) is True
    assert only_software.names() == [
        "打开软件", "批量打开网址", "上传文件", "打开工作环境"], \
        only_software.names()
    assert only_software.get("打开浏览器") is None

    # 用户改过的旧浏览器组不覆盖
    modified = Group("打开浏览器", [Step("wait", "9")], "我改过")
    modified_project = Project(groups=[modified])
    gui_module.seed_default_groups(
        modified_project, {gui_module.DEFAULT_GROUPS_KEY: 1})
    assert modified_project.get("打开浏览器").steps[0].content == "9"
    assert modified_project.get("打开浏览器").note == "我改过"
    mark("默认组预置")


def test_restore_default_groups():
    """编辑菜单 / 右键里的「恢复预置执行组」只补缺失组，不覆盖同名组。"""
    saved_project = app.project
    saved_index = app.group_index
    try:
        labels = [getattr(item, "label", "") for item in app._edit_items()]
        assert any("恢复预置执行组" in label for label in labels), labels
        app.project = Project(groups=[
            Group("保留组", [Step("wait", "1")], "别动我")])
        app.group_index = 0
        app._refresh_all()
        app.restore_default_groups()
        expected = ["保留组"] + [name for name, _steps in _INITIAL_GROUPS]
        assert app.project.names() == expected, app.project.names()
        assert app.project.get("保留组").steps[0].content == "1"
        assert app.project.get("保留组").note == "别动我"

        # 已经有全部预置组时，再点一次不会重复添加
        count = len(app.project.groups)
        app.restore_default_groups()
        assert len(app.project.groups) == count

        # 只剩旧版空占位组时，恢复会顺手清掉它
        app.project = Project(groups=[Group("默认组")])
        app.group_index = 0
        app._refresh_all()
        app.restore_default_groups()
        assert app.project.names() == [
            name for name, _steps in _INITIAL_GROUPS], app.project.names()
    finally:
        app.project = saved_project
        app.group_index = saved_index
        app._refresh_all(select_group=saved_index)
    mark("恢复预置执行组")


def finish():
    try:
        checks()
        test_default_groups()
        test_schedule_dialog_and_tick()
        test_editing_features()
        test_macro_recorder()
        test_macro_wheel_accumulate()
        test_run_with_data()
        test_data_preview_dialog()
        test_undo_redo_button_states()
        test_save_indicator_and_ui_state()
        test_log_link_and_failed_locate()
        test_global_run_hotkey_dispatch()
        test_log_level_coloring()
        test_shortcuts_dialog()
        test_step_dialog_types()
        test_capture_overlay_from_modal_dialog()
        test_type_icons_cover_all()
        test_step_dialog_new_types()
        test_image_preview_dialog()
        test_step_search_filter()
        test_progress_and_mini_bar()
        test_image_preview_pane()
        test_zebra_and_type_colors()
        test_settings_dialog_preserves_keys()
        test_canvas_cat_empty_state()
        test_theme_switch()
        test_batch_step_ops()
        test_drag_indicator_line()
        test_debug_step_dialog()
        test_debug_confirm_roundtrip()
        test_settings_dialog_mini_bar()
        test_step_dialog_advanced_roundtrip()
        test_settings_dialog_result_flags()
        test_schedule_task_command()
        test_result_writeback_from_gui()
        test_resume_retries_failed_tail()
        test_restore_default_groups()
    except Exception as exc:  # noqa: BLE001 — 任何异常都要退出，避免测试挂住
        print(f"  FAIL  {type(exc).__name__}: {exc}")
        app.destroy()
        os._exit(1)

    try:
        from PIL import ImageGrab

        # 恢复成有代表性的示例状态再截图，便于人工检查界面
        seed()
        app._drain_queue()
        app.update()
        app.after(250, lambda: (
            ImageGrab.grab().save(os.path.join(os.path.dirname(__file__),
                                               "gui_preview.png")),
            print("  INFO  截图已保存 tests/gui_preview.png"),
            app.destroy()))
    except Exception as exc:  # noqa: BLE001
        print(f"  INFO  截图跳过：{exc}")
        app.destroy()


def run():
    seed()
    app.after(600, finish)
    app.mainloop()
    print("  PASS  界面构建与关闭")


if __name__ == "__main__":
    run()
