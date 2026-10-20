# -*- coding: utf-8 -*-
"""GUI 冒烟测试：组树 / 步骤列表 / 内联编辑 / 对话框流程 / 通过界面跑一次。

对话框会被替换成假实现，避免阻塞；所有弹窗（确认框）自动应答。
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa import gui as gui_module  # noqa: E402
from rpa.engine import Group, Step, import_excel, validate_project  # noqa: E402

# 把配置写到临时文件，避免影响用户设置
_TMP = tempfile.mkdtemp(prefix="rpa_gui_test_")
gui_module.SETTINGS_PATH = os.path.join(_TMP, "settings.json")
gui_module.enable_dpi_awareness()

app = gui_module.App()
print(f"  INFO  DPI 缩放系数 = {app.scale}")

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
        files = os.listdir(gui_module.REPORTS_DIR)
        assert files and files[0].endswith(".md"), files
        text = open(os.path.join(gui_module.REPORTS_DIR, files[0]),
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
    gui_module.filedialog.askopenfilename = lambda *a, **k: path
    try:
        app.settings["delay"] = 0
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


def finish():
    try:
        checks()
        test_schedule_dialog_and_tick()
        test_editing_features()
        test_macro_recorder()
        test_macro_wheel_accumulate()
        test_run_with_data()
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
