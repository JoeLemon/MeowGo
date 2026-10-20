# -*- coding: utf-8 -*-
"""引擎自检：执行组、Excel 读写往返、校验、调用与停止。"""
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa.engine import (STEP_TYPES, TYPE_TO_CODE, Executor, Group,  # noqa: E402
                        Project, Step, export_excel, import_excel,
                        load_steps, render_content, validate_project)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Point:
    def __init__(self, x, y):
        self.x, self.y = x, y


class _FakeIO:
    """假的输入输出系统：记录调用 + 一块共享剪贴板。

    selection：模拟「当前选中的文字」——Ctrl+C 时写进剪贴板；
    为空串表示没选中任何东西（复制不到）。
    """

    def __init__(self, calls, located=(100, 100), selection="选中的文字"):
        self.calls = calls
        self.located = located
        self.selection = selection
        self.clipboard = "旧的剪贴板内容"
        self.base_dir = ""


class _FakePyAutoGUI:
    """记录调用、不碰真实鼠标键盘。located=None 表示“屏幕上没有”。

    与真实 pyautogui 保持一致：找不到图片时抛 ImageNotFoundException
    （引擎必须接住并当成「没出现」，否则等待/重试逻辑全是死代码）。
    """

    ImageNotFoundException = type("ImageNotFoundException", (Exception,), {})

    def __init__(self, io):
        self._io = io
        self.KEYBOARD_KEYS = set()

    def locateCenterOnScreen(self, image, confidence=0.9):
        self._io.calls.append(("locate", image))
        if not self._io.located:
            raise self.ImageNotFoundException(image)
        return _Point(*self._io.located)

    def click(self, x, y, **kwargs):
        self._io.calls.append(("click", x, y, kwargs.get("button", "left")))

    def hotkey(self, *keys):
        self._io.calls.append(("hotkey", ",".join(keys)))
        if tuple(keys) == ("ctrl", "c"):
            # 模拟目标应用响应复制：把选中文字写进剪贴板
            self._io.clipboard = self._io.selection

    def scroll(self, n):
        self._io.calls.append(("scroll", n))

    def moveTo(self, x, y, **kwargs):
        self._io.calls.append(("moveTo", x, y))

    def mouseDown(self, **kwargs):
        self._io.calls.append(("mouseDown",))

    def mouseUp(self, **kwargs):
        self._io.calls.append(("mouseUp",))


class _FakePyperclip:
    def __init__(self, io):
        self._io = io

    def copy(self, text):
        self._io.calls.append(("copy", text))
        self._io.clipboard = text

    def paste(self):
        return self._io.clipboard


@contextmanager
def _fake_input_modules(calls, located=(100, 100), selection="选中的文字",
                        images=()):
    """把 pyautogui / pyperclip 换成假的，退出时恢复原模块。

    images：需要「真实存在」的图片文件名——引擎识别前会检查文件在不在
    （缺文件会明确报「找不到图片文件」并中止，而不是让 OpenCV 抛看不懂的
    异常），所以跑图片步骤的测试要把文件造出来，并把 executor.base_dir
    指到 io.base_dir。

    产出 _FakeIO（calls / clipboard / base_dir 都能从上面读），老测试只用
    自己传进去的 calls 列表，不受影响。
    """
    io = _FakeIO(calls, located, selection)
    tmp = tempfile.mkdtemp(prefix="rpa_test_imgs_")
    try:
        for name in images:
            path = os.path.join(tmp, name)
            os.makedirs(os.path.dirname(path) or tmp, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("x")       # 内容无所谓，假 locate 不读文件
        io.base_dir = tmp
        saved = {}
        for name, module in (("pyautogui", _FakePyAutoGUI(io)),
                             ("pyperclip", _FakePyperclip(io))):
            saved[name] = sys.modules.get(name)
            sys.modules[name] = module
        try:
            yield io
        finally:
            for name, old in saved.items():
                if old is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = old
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_types_defined():
    assert len(STEP_TYPES) == 20
    for key, info in STEP_TYPES.items():
        assert len(info) == 4, key
    assert "call" in STEP_TYPES
    for key in ("if_image", "window", "click_xy", "move_xy"):
        assert key in STEP_TYPES, key
    assert STEP_TYPES["if_image"][0].startswith("条件")
    # 条件 / 变量 / 拖拽一族（类型表里有声明）
    for key in ("if_not_image", "if_else", "if_window", "set", "copy", "drag"):
        assert key in STEP_TYPES, key
    for key, code in TYPE_TO_CODE.items():
        assert isinstance(code, float), key


def test_step_validate():
    assert Step("click", "").validate(), "空图片名应报错"
    assert not Step("click", "a.png").validate()
    assert Step("wait", "abc").validate(), "非数字等待应报错"
    assert not Step("wait", "1.5").validate()
    assert Step("scroll", "x").validate()
    assert not Step("scroll", "-200").validate()
    assert Step("input", "   ").validate()
    assert not Step("input", "hello").validate()
    assert Step("hotkey", "1").validate(), "单数字键应报错"
    assert not Step("hotkey", "ctrl,s").validate()
    assert Step("hotkey", ",".join(["a"] * 11)).validate(), "超过10个应报错"
    assert Step("click", "a.png", repeat=-5).validate(), "负重复应报错"
    assert not Step("click", "a.png", repeat=-1).validate()
    assert Step("call", "  ").validate(), "调用组必须填组名"
    assert not Step("call", "常用操作").validate()


def test_row_roundtrip():
    step = Step("hotkey", "win,r", 2, "打开运行")
    row = step.to_row()
    assert row[0] == 7.0 and row[1] == "win,r" and row[2] == 2.0
    back = Step.from_row(row)
    assert back.type == "hotkey" and back.content == "win,r"
    assert back.repeat == 2 and back.note == "打开运行"
    # 调用组使用编号 10，且能往返
    call = Step("call", "登录流程", -1)
    assert call.to_row()[0] == 10.0
    assert Step.from_row(call.to_row()).type == "call"


def test_project_json_roundtrip():
    project = Project(groups=[Group("登录", [Step("input", "admin")]),
                              Group("主流程", [Step("call", "登录")])],
                      image_dir="images")
    data = project.to_dict()
    again = Project.from_dict(data)
    assert again.names() == ["登录", "主流程"]
    assert again.groups[1].steps[0].type == "call"
    # 兼容 v1 旧格式
    legacy = Project.from_dict({"image_dir": "images",
                                "steps": [{"type": "wait", "content": "1",
                                           "repeat": 0, "note": ""}]})
    assert legacy.names() == ["默认组"]
    assert legacy.groups[0].steps[0].content == "1"


def test_validate_project():
    groups = [Group("A", [Step("call", "B"), Step("wait", "1")]),
              Group("B", [Step("wait", "0.1")])]
    assert validate_project(groups) == []
    # 调用了不存在的组
    bad = validate_project([Group("A", [Step("call", "没这个组")])])
    assert any("不存在" in p for p in bad), bad
    # 循环调用
    cyc = validate_project([Group("A", [Step("call", "B")]),
                            Group("B", [Step("call", "A")])])
    assert any("循环调用" in p for p in cyc), cyc
    # 自调用
    self_cyc = validate_project([Group("A", [Step("call", "A")])])
    assert any("循环调用" in p for p in self_cyc), self_cyc


def test_excel_multi_sheet_roundtrip():
    groups = [Group("登录", [Step("wait", "0.5"), Step("click", "btn.png", 2)]),
              Group("主流程", [Step("call", "登录"), Step("cmd", "start notepad")]),
              Group("时间/日期", [Step("time", "当前时间")])]
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "out.xlsx")
        export_excel(path, groups)
        loaded = import_excel(path)
    assert len(loaded) == 3, [g.name for g in loaded]
    assert loaded[0].name == "登录"
    assert loaded[1].steps[0].type == "call"
    assert loaded[1].steps[0].content == "登录"
    assert loaded[2].steps[0].type == "time"
    # 非法字符被清理
    assert "/" not in loaded[2].name


def test_excel_single_group_export():
    groups = [Group("第一组", [Step("wait", "1")]),
              Group("第二组", [Step("wait", "2")])]
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "one.xlsx")
        export_excel(path, groups, single=True)
        loaded = import_excel(path)
    assert len(loaded) == 1, [g.name for g in loaded]
    assert loaded[0].steps[0].content == "1"


def test_read_legacy_xls():
    demo = os.path.join(ROOT, "demo.xls")
    if not os.path.exists(demo):
        print("  (跳过：未找到 demo.xls)")
        return
    steps = load_steps(demo)
    assert len(steps) > 30, len(steps)
    assert steps[0].type == "hotkey" and steps[0].content == "win,r"
    # 全部导入：demo.xls 有两个工作表 → 两个组
    groups = import_excel(demo)
    assert len(groups) == 2, [g.name for g in groups]
    assert groups[0].name == "Sheet1", groups[0].name
    # 单组导入：只取第一个工作表，并按文件名命名
    single = import_excel(demo, single=True)
    assert len(single) == 1 and single[0].name == "demo", [g.name for g in single]


def _executor(**kwargs):
    executor = Executor(**kwargs)
    executor.logs = []
    executor.log = lambda level, msg: executor.logs.append((level, msg))
    return executor


def test_executor_call_group():
    logs = []
    ex = Executor()
    ex.log = lambda level, msg: logs.append((level, msg))
    ex.groups = {"A": [Step("call", "B"), Step("wait", "0.05")],
                 "B": [Step("wait", "0.05")]}
    result = ex.run("A")
    assert result == "finished", result
    text = "\n".join(m for _, m in logs)
    assert "进入执行组「A」" in text and "进入执行组「B」" in text
    assert text.index("「A」") < text.index("「B」"), text


def test_executor_call_repeat_and_missing():
    logs = []
    ex = Executor()
    ex.log = lambda level, msg: logs.append((level, msg))
    ex.groups = {"A": [Step("call", "B", 3)], "B": [Step("wait", "0.02")]}
    assert ex.run("A") == "finished"
    assert sum("进入执行组「B」" in m for _, m in logs) == 3

    ex2 = Executor()
    ex2.log = lambda level, msg: None
    ex2.groups = {"A": [Step("call", "幽灵组")]}
    assert ex2.run("A") == "error"


def test_executor_recursion_guard():
    ex = Executor()
    ex.log = lambda level, msg: None
    ex.groups = {"A": [Step("call", "A")]}
    assert ex.run("A") == "error", "自调用应被层级保护拦下"


def test_executor_run_all_in_order():
    order = []
    ex = Executor()
    ex.log = lambda level, msg: None
    ex.groups = {"一": [Step("wait", "0.01")], "二": [Step("wait", "0.01")]}
    ex.order = ["二", "一"]
    ex.on_step = lambda name, index, step: order.append(name)
    assert ex.run() == "finished"
    assert order == ["二", "一"], order


def test_executor_stop():
    import threading
    import time

    logs = []
    ex = Executor()
    ex.log = lambda level, msg: logs.append((level, msg))
    ex.groups = {"主": [Step("wait", "30")]}
    box = {}
    thread = threading.Thread(target=lambda: box.update(result=ex.run("主")),
                              daemon=True)
    thread.start()
    time.sleep(0.4)
    ex.stop()
    thread.join(timeout=3)
    assert box.get("result") == "stopped", box
    assert any("开始执行" in m for _, m in logs)


def test_executor_pause_resume():
    import threading
    import time

    ex = Executor()
    ex.log = lambda level, msg: None
    ex.groups = {"主": [Step("wait", "0.3")]}
    box = {}
    thread = threading.Thread(target=lambda: box.update(result=ex.run("主")),
                              daemon=True)
    thread.start()
    time.sleep(0.05)
    assert ex.toggle_pause() is True
    time.sleep(0.3)
    assert thread.is_alive(), "暂停时不应结束"
    assert ex.toggle_pause() is False
    thread.join(timeout=3)
    assert box.get("result") == "finished", box


def test_resolve_image():
    """图片名解析要同时兼容新旧两种写法。"""
    from rpa import assets

    with tempfile.TemporaryDirectory() as tmp:
        image_dir = os.path.join(tmp, "images")
        os.makedirs(image_dir)
        target = os.path.join(image_dir, "btn.png")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("x")
        # 新写法：相对图片目录
        assert assets.resolve_image("btn.png", image_dir) == target
        # 旧写法：相对程序目录（带 images/ 前缀）
        assert assets.resolve_image("images/btn.png", tmp) == target
        # 绝对路径原样返回
        assert assets.resolve_image(target, image_dir) == target
        # 找不到时也给一个可用路径，而不是抛异常
        assert assets.resolve_image("none.png", image_dir).endswith("none.png")


# ---------------------------------------------------------------- 新版测试

def test_load_data_csv():
    """load_data：CSV 读成 [{列名: 值}]，空行跳过，缺列兜底。"""
    from rpa.engine import load_data

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "t.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("名字,密码\n甲,111\n\n乙,222\n,,\n")
        data = load_data(path)
    assert data == [{"名字": "甲", "密码": "111"},
                    {"名字": "乙", "密码": "222"}], data

    # 没有表头时用 col1/col2 兜底
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "t2.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("x,y\n1,2\n")
        data = load_data(path)
    assert data == [{"x": "1", "y": "2"}], data


def test_new_step_types_validate():
    """四种新步骤类型的校验规则。"""
    assert Step("if_image", "弹窗.png").validate(), "缺 |组名 应报错"
    assert not Step("if_image", "弹窗.png|关闭弹窗").validate()
    assert Step("window", "最小化:标题").validate(), "未知动作应报错"
    assert not Step("window", "activate:记事本").validate()
    assert Step("window", "wait:标题").validate(), "wait 缺秒数应报错"
    assert not Step("window", "wait:标题|10").validate()
    assert Step("click_xy", "abc").validate(), "坐标必须是 x,y"
    assert not Step("click_xy", "500,300").validate()
    assert Step("move_xy", "500").validate()
    # 新参数：timeout / on_fail
    assert Step("click", "a.png", timeout=-1).validate(), "负超时应报错"
    assert Step("click", "a.png", timeout=5, on_fail="重试").validate()
    assert not Step("click", "a.png", timeout=5, on_fail="skip").validate()
    # 新类型的 Excel 编号可往返
    for code, key in ((11.0, "if_image"), (12.0, "window"),
                      (13.0, "click_xy"), (14.0, "move_xy")):
        assert Step.from_row([code, "x", 1, ""]).type == key
        assert Step(key, "x").to_row()[0] == code


def test_render_content_placeholders():
    """占位符替换：数据列 + 内置变量。"""
    assert render_content("你好{名字}", {"名字": "甲"}) == "你好甲"
    assert render_content("{index} 行", {}, 3) == "3 行"
    assert render_content("{date} {time}", {}).count("-") == 2
    assert len(render_content("{random}", {})) == 4
    # 没有占位符或列不存在时原样返回
    assert render_content("ctrl,s", {"a": 1}) == "ctrl,s"
    assert render_content("{缺失}", {"a": 1}) == "{缺失}"


def test_data_driven_run():
    """数据驱动：每一行完整跑一遍，内容按行替换。"""
    calls = []
    with _fake_input_modules(calls):
        ex = Executor()
        logs = []
        ex.log = lambda level, msg: logs.append((level, msg))
        ex.groups = {"主": [Step("input", "你好{名字}"), Step("wait", "0.01")]}
        ex.data = [{"名字": "甲"}, {"名字": "乙"}, {"名字": "丙"}]
        assert ex.run("主") == "finished"
    pasted = [c[1] for c in calls if c[0] == "copy"]
    assert pasted == ["你好甲", "你好乙", "你好丙"], pasted
    text = "\n".join(m for _, m in logs)
    assert "数据驱动：共 3 行" in text and "第 2/3 行" in text
    # 每行都记了统计，行号分别是 1/2/3
    assert [item["row"] for item in ex.stats if item["type"] == "input"] == \
        [1, 2, 3]


def test_xy_steps_execute():
    """move_xy / click_xy 的执行路径（含右键与 repeat）。"""
    calls = []
    with _fake_input_modules(calls):
        ex = Executor()
        ex.log = lambda level, msg: None
        ex._sleep = lambda s: None
        ex.groups = {"坐": [Step("move_xy", "600,400"),
                            Step("click_xy", "600,400"),
                            Step("click_xy", "700,500,right", repeat=2)]}
        assert ex.run("坐") == "finished"
    moves = [c for c in calls if c[0] == "moveTo"]
    clicks = [c for c in calls if c[0] == "click"]
    assert moves == [("moveTo", 600, 400)], moves
    # 左键一次 + 右键两次；button 由 content 第三段决定
    assert len(clicks) == 3, clicks
    assert [(c[1], c[2], c[3]) for c in clicks] == [
        (600, 400, "left"), (700, 500, "right"), (700, 500, "right")], clicks


def test_scroll_scaled_to_wheel_delta():
    """滚轮步骤按 100=1 格换算成 120 的倍数再执行。

    Windows 滚轮增量以 120 为 1 格（WHEEL_DELTA），pyautogui 又把参数原样
    传给 mouse_event 的 dwData——直接传 100 的话，多数应用做 100/120=0 的
    整数除法会一格都不滚。
    """
    calls = []
    with _fake_input_modules(calls):
        ex = Executor()
        ex.log = lambda level, msg: None
        ex._sleep = lambda s: None
        ex.groups = {"滚": [Step("scroll", "100"), Step("scroll", "300"),
                            Step("scroll", "-200"), Step("scroll", "50"),
                            Step("scroll", "0")]}
        assert ex.run("滚") == "finished"
    got = [n for kind, n in calls if kind == "scroll"]
    assert got == [120, 360, -240, 120, 0], got


def test_fail_strategy_timeout_skip_and_stop():
    """图片超时后：skip 继续往下，stop 中止整个执行。"""
    # skip：超时后这步算失败，但流程继续
    with _fake_input_modules([], located=None, images=("x.png",)) as io:
        ex = Executor()
        ex.base_dir = io.base_dir
        logs = []
        ex.log = lambda level, msg: logs.append((level, msg))
        ex.groups = {"主": [Step("click", "x.png", timeout=0.2, on_fail="skip"),
                            Step("wait", "0.01")]}
        assert ex.run("主") == "finished"
    text = "\n".join(m for _, m in logs)
    assert "超时（0.2 秒）" in text
    click_stat = [i for i in ex.stats if i["type"] == "click"][0]
    assert click_stat["ok"] is False, "超时跳过的步骤在报告里应算失败"

    # stop：超时直接中止
    with _fake_input_modules([], located=None, images=("x.png",)) as io:
        ex2 = Executor()
        ex2.base_dir = io.base_dir
        ex2.log = lambda level, msg: None
        ex2.groups = {"主": [Step("click", "x.png", timeout=0.2,
                                  on_fail="stop"), Step("wait", "0.01")]}
        assert ex2.run("主") == "error"

    # 没设超时时保持旧行为：一直等（用 wait 模拟不会卡住的场景）
    with _fake_input_modules([], located=(100, 100),
                             images=("x.png",)) as io:
        ex3 = Executor()
        ex3.base_dir = io.base_dir
        ex3.log = lambda level, msg: None
        ex3.groups = {"主": [Step("click", "x.png")]}
        assert ex3.run("主") == "finished"
    assert [i for i in ex3.stats if i["type"] == "click"][0]["ok"] is True


def test_if_image_condition():
    """条件步骤：图片出现才调用对应组。"""
    with _fake_input_modules([], located=(100, 100),
                             images=("弹窗.png",)) as io:
        ex = Executor()
        ex.base_dir = io.base_dir
        logs = []
        ex.log = lambda level, msg: logs.append((level, msg))
        ex.groups = {"主": [Step("if_image", "弹窗.png|关弹窗")],
                     "关弹窗": [Step("wait", "0.01")]}
        assert ex.run("主") == "finished"
    text = "\n".join(m for _, m in logs)
    assert "条件满足" in text and "进入执行组「关弹窗」" in text

    # 图片不在（文件在、屏幕上没有）：跳过，不报错
    with _fake_input_modules([], located=None,
                             images=("弹窗.png",)) as io:
        ex2 = Executor()
        ex2.base_dir = io.base_dir
        ex2.log = lambda level, msg: None
        ex2.groups = {"主": [Step("if_image", "弹窗.png|关弹窗")],
                      "关弹窗": [Step("wait", "0.01")]}
        assert ex2.run("主") == "finished"
    assert ex2.stats[0]["type"] == "if_image"


def test_executor_report_stats():
    """report() 汇总每步耗时与成败。"""
    with _fake_input_modules([]):
        ex = Executor()
        ex.log = lambda level, msg: None
        ex.groups = {"主": [Step("wait", "0.05"), Step("wait", "0.05")]}
        ex.run("主")
    report = ex.report()
    assert report["steps"] == 2 and report["failed"] == 0
    assert report["seconds"] >= 0.09
    assert len(report["detail"]) == 2 and "group" in report["detail"][0]


def test_check_images_prevalidate():
    """预检：图片文件缺失要能被运行前发现。"""
    with tempfile.TemporaryDirectory() as tmp:
        image_dir = os.path.join(tmp, "images")
        os.makedirs(image_dir)
        with open(os.path.join(image_dir, "在的.png"), "w",
                  encoding="utf-8") as fh:
            fh.write("x")
        groups = [Group("主", [Step("click", "在的.png"),
                               Step("click", "不在的.png")])]
        # 不查文件时：格式没问题
        assert not validate_project(groups)
        # 查文件时：缺的那个要被点名（且只点名这一个）
        problems = validate_project(groups, base_dir=image_dir,
                                    check_images=True)
        assert len(problems) == 1 and "不在的.png" in problems[0], problems


def test_window_action_errors():
    """窗口操作的错误路径（不需要真实窗口）。"""
    from rpa.engine import StepError, _window_action

    for text in ("activate:绝对不存在的窗口标题xyz", "close:不存在的窗口xyz"):
        try:
            _window_action(text)
            raise AssertionError(f"{text} 应该抛 StepError")
        except StepError:
            pass


def test_window_timeout_recorded_in_stats():
    """窗口等待超时：整场中止，但该步必须出现在统计里（报告能看到败因）。"""
    with _fake_input_modules([]):
        ex = Executor()
        ex.log = lambda level, msg: None
        ex._sleep = lambda s: None
        ex.groups = {"等": [Step("wait", "0.01"),
                            Step("window", "wait:绝对不存在的窗口xyz|0.2"),
                            Step("wait", "0.01")]}
        assert ex.run("等") == "error"
    report = ex.report()
    assert report["failed"] == 1, report
    last = report["detail"][-1]
    assert last["type"] == "window" and last["ok"] is False
    # 后续步骤不再执行
    assert len(report["detail"]) == 2, report["detail"]


def test_report_markdown():
    """报告渲染：表格行列与成败标记。"""
    from rpa.engine import report_markdown

    report = {"steps": 2, "failed": 1, "seconds": 1.5, "rows": 1,
              "detail": [
                  {"group": "甲", "index": 1, "type": "wait", "content": "1",
                   "note": "", "seconds": 0.5, "ok": True, "row": 1},
                  {"group": "甲", "index": 2, "type": "click",
                   "content": "a.png", "note": "", "seconds": 1.0,
                   "ok": False, "row": 1},
              ]}
    text = report_markdown(report, "finished")
    assert "结果：finished" in text and "失败：1" in text
    assert "| 甲 | 1 | 1 | 等待 | 1 | 0.5 | 成功 |" in text
    assert "| 甲 | 1 | 2 | 左键单击 | a.png | 1 | 失败 |" in text


def test_set_var_and_placeholder():
    """设置变量 + {变量} 读回；数据列优先于同名变量；跨数据行共享。"""
    with _fake_input_modules([]):
        ex = Executor()
        logs = []
        ex.log = lambda level, msg: logs.append((level, msg))
        ex._sleep = lambda s: True
        ex.groups = {"主": [Step("set", "订单号=ABC123"),
                            Step("input", "单号是{订单号}")]}
        assert ex.run("主") == "finished"
        assert ex.vars["订单号"] == "ABC123"
        assert "设置变量 订单号 = ABC123" in "\n".join(m for _, m in logs)
        # 报告里的内容也是渲染后的值
        assert ex.stats[1]["content"] == "单号是ABC123"

    # set 的值里也能用占位符（数据列 / index 等）
    with _fake_input_modules([]):
        ex2 = Executor()
        ex2.log = lambda level, msg: None
        ex2._sleep = lambda s: True
        ex2.data = [{"名字": "甲"}, {"名字": "乙"}]
        ex2.groups = {"主": [Step("set", "行号={index}"),
                             Step("input", "{名字}-{行号}")]}
        assert ex2.run("主") == "finished"
        inputs = [i for i in ex2.stats if i["type"] == "input"]
        assert [i["content"] for i in inputs] == ["甲-1", "乙-2"]
        assert ex2.vars["行号"] == "2"      # 同一场运行内跨行共享，留最后值

    # 数据列与变量同名时数据列赢；变量没定义则保留原样
    with _fake_input_modules([]):
        ex3 = Executor()
        ex3.log = lambda level, msg: None
        ex3._sleep = lambda s: True
        ex3.data = [{"名字": "甲"}]
        ex3.groups = {"主": [Step("set", "名字=变量值"),
                             Step("input", "{名字}"),
                             Step("input", "{没定义过}")]}
        assert ex3.run("主") == "finished"
        assert ex3.vars["名字"] == "变量值"
        assert ex3.stats[1]["content"] == "甲", "数据列应优先于同名变量"
        assert ex3.stats[2]["content"] == "{没定义过}"


def test_copy_to_var():
    """复制选中文字到变量：清空→Ctrl+C→读取→还原剪贴板。"""
    with _fake_input_modules([], selection="选中的文字") as io:
        ex = Executor()
        logs = []
        ex.log = lambda level, msg: logs.append((level, msg))
        ex._sleep = lambda s: True
        ex.groups = {"主": [Step("copy", "片段")]}
        assert ex.run("主") == "finished"
        assert ex.vars["片段"] == "选中的文字"
        assert ("hotkey", "ctrl,c") in io.calls
        # 先清空剪贴板（避免把旧内容当成果），成功后还原
        assert [c[1] for c in io.calls if c[0] == "copy"] == \
            ["", "旧的剪贴板内容"]
        assert "已复制到变量 片段：选中的文字" in "\n".join(
            m for _, m in logs)

    # 没选中任何文字：步骤记失败、变量不更新、剪贴板照样还原
    with _fake_input_modules([], selection="") as io2:
        ex2 = Executor()
        logs2 = []
        ex2.log = lambda level, msg: logs2.append((level, msg))
        ex2._sleep = lambda s: True
        ex2.groups = {"主": [Step("copy", "片段")]}
        assert ex2.run("主") == "finished"
        assert "片段" not in ex2.vars
        assert ex2.report()["failed"] == 1
        assert "没复制到文字" in "\n".join(m for _, m in logs2)
        assert io2.clipboard == "旧的剪贴板内容"


def test_if_not_image_condition():
    """条件步骤：屏幕上没有图片才调用组。"""
    with _fake_input_modules([], located=(100, 100),
                             images=("错误.png",)) as io:
        ex = Executor()
        ex.base_dir = io.base_dir
        logs = []
        ex.log = lambda level, msg: logs.append((level, msg))
        ex.groups = {"主": [Step("if_not_image", "错误.png|重试")],
                     "重试": [Step("wait", "0.01")]}
        assert ex.run("主") == "finished"
        assert "条件不满足" in "\n".join(m for _, m in logs)
        assert not any(i["group"] == "重试" for i in ex.stats)

    with _fake_input_modules([], located=None,
                             images=("错误.png",)) as io:
        ex2 = Executor()
        ex2.base_dir = io.base_dir
        logs2 = []
        ex2.log = lambda level, msg: logs2.append((level, msg))
        ex2.groups = {"主": [Step("if_not_image", "错误.png|重试")],
                      "重试": [Step("wait", "0.01")]}
        assert ex2.run("主") == "finished"
        assert "条件满足：屏幕上没有 错误.png" in "\n".join(
            m for _, m in logs2)
        assert any(i["group"] == "重试" for i in ex2.stats)


def test_if_else_condition():
    """条件步骤：出现走 A 组，没出现走 B 组；B 组留空则什么都不做。"""
    with _fake_input_modules([], located=(100, 100),
                             images=("弹窗.png",)) as io:
        ex = Executor()
        ex.base_dir = io.base_dir
        ex.log = lambda level, msg: None
        ex.groups = {"主": [Step("if_else", "弹窗.png|关弹窗|等弹窗")],
                     "关弹窗": [Step("wait", "0.01")],
                     "等弹窗": [Step("wait", "0.01")]}
        assert ex.run("主") == "finished"
        assert any(i["group"] == "关弹窗" for i in ex.stats)
        assert not any(i["group"] == "等弹窗" for i in ex.stats)

    with _fake_input_modules([], located=None,
                             images=("弹窗.png",)) as io:
        ex2 = Executor()
        ex2.base_dir = io.base_dir
        ex2.log = lambda level, msg: None
        ex2.groups = {"主": [Step("if_else", "弹窗.png|关弹窗|等弹窗")],
                      "关弹窗": [Step("wait", "0.01")],
                      "等弹窗": [Step("wait", "0.01")]}
        assert ex2.run("主") == "finished"
        assert any(i["group"] == "等弹窗" for i in ex2.stats)
        assert not any(i["group"] == "关弹窗" for i in ex2.stats)

        ex3 = Executor()
        ex3.base_dir = io.base_dir
        ex3.log = lambda level, msg: None
        ex3.groups = {"主": [Step("if_else", "弹窗.png|关弹窗|")]}
        assert ex3.run("主") == "finished"
        assert len(ex3.stats) == 1, "B 组留空：只应有条件步骤本身"


def test_if_window_condition():
    """窗口条件：窗口存在才调用组（_find_window 打桩，不需要真窗口）。"""
    import rpa.engine as engine

    if engine.ctypes is None:          # 非 Windows 平台没有窗口枚举
        return
    with _fake_input_modules([]):
        original = engine._find_window
        engine._find_window = lambda title: 1 if "记事本" in title else None
        try:
            ex = Executor()
            logs = []
            ex.log = lambda level, msg: logs.append((level, msg))
            ex.groups = {"主": [Step("if_window", "记事本|处理")],
                         "处理": [Step("wait", "0.01")]}
            assert ex.run("主") == "finished"
            assert any(i["group"] == "处理" for i in ex.stats)
            assert "条件满足" in "\n".join(m for _, m in logs)

            ex2 = Executor()
            ex2.log = lambda level, msg: None
            ex2.groups = {"主": [Step("if_window", "不存在的窗口xyz|处理")],
                          "处理": [Step("wait", "0.01")]}
            assert ex2.run("主") == "finished"
            assert not any(i["group"] == "处理" for i in ex2.stats)
        finally:
            engine._find_window = original


def test_drag_step():
    """拖拽：坐标直接拖；图片两端先定位；等不到按超时策略，不误按鼠标。"""
    calls = []
    with _fake_input_modules(calls):
        ex = Executor()
        ex.log = lambda level, msg: None
        ex.groups = {"主": [Step("drag", "100,200|300,400")]}
        assert ex.run("主") == "finished"
    assert ("moveTo", 100, 200) in calls and ("moveTo", 300, 400) in calls
    assert calls.index(("mouseDown",)) < \
        calls.index(("moveTo", 300, 400)) < calls.index(("mouseUp",))

    # 两端都写图片：各自定位后再按
    calls2 = []
    with _fake_input_modules(calls2, located=(150, 250),
                             images=("起点.png", "终点.png")) as io:
        ex2 = Executor()
        ex2.base_dir = io.base_dir
        ex2.log = lambda level, msg: None
        ex2.groups = {"主": [Step("drag", "起点.png|终点.png")]}
        assert ex2.run("主") == "finished"
    assert any(c[0] == "locate" and c[1].endswith("起点.png")
               for c in calls2)
    assert any(c[0] == "locate" and c[1].endswith("终点.png")
               for c in calls2)
    assert ("moveTo", 150, 250) in calls2

    # 终点图片等不到（文件在、屏幕上没有）：超时跳过这步，且没按下过鼠标
    calls3 = []
    with _fake_input_modules(calls3, located=None,
                             images=("终点.png",)) as io:
        ex3 = Executor()
        ex3.base_dir = io.base_dir
        ex3.log = lambda level, msg: None
        ex3.groups = {"主": [Step("drag", "100,200|终点.png",
                                  timeout=0.2, on_fail="skip")]}
        assert ex3.run("主") == "finished"
    assert ex3.report()["failed"] == 1
    assert ("mouseDown",) not in calls3, "两端没都就位不能按下鼠标"

    # on_fail=stop：直接中止整场
    with _fake_input_modules([], located=None,
                             images=("终点.png",)) as io:
        ex4 = Executor()
        ex4.base_dir = io.base_dir
        ex4.log = lambda level, msg: None
        ex4.groups = {"主": [Step("drag", "100,200|终点.png",
                                  timeout=0.2, on_fail="stop")]}
        assert ex4.run("主") == "error"


def test_locate_missing_file_aborts():
    """图片文件缺失：明确报「找不到图片文件」并中止，而不是 OpenCV 裸错。"""
    from rpa.engine import StepError

    with _fake_input_modules([], located=(100, 100)):
        ex = Executor()
        ex.base_dir = tempfile.mkdtemp(prefix="rpa_test_missing_")
        try:
            try:
                ex._locate("缺失.png")
                raise AssertionError("缺文件应抛 StepError")
            except StepError as exc:
                assert "找不到图片文件" in str(exc)

            logs = []
            ex.log = lambda level, msg: logs.append((level, msg))
            ex.groups = {"主": [Step("click", "缺失.png"), Step("wait", "1")]}
            assert ex.run("主") == "error"
            assert "找不到图片文件" in "\n".join(m for _, m in logs)
            assert len(ex.stats) == 1, "缺文件应立即中止，后续步骤不执行"
        finally:
            shutil.rmtree(ex.base_dir, ignore_errors=True)


def test_estimate_counts_conditions():
    """进度估算：条件步骤要展开被调用组（含判断本身那一步）。"""
    from rpa.engine import estimate_steps

    groups = {"主": [Step("if_image", "a.png|处理"), Step("wait", "0.5")],
              "处理": [Step("wait", "0.5"), Step("wait", "0.5")]}
    assert estimate_steps(groups, ["主"]) == 4      # 1 + 2 + 1

    # if_else 只走一个分支：估 Two 中步数较多的那个
    groups2 = {"主": [Step("if_else", "a.png|甲|乙")],
               "甲": [Step("wait", "1")],
               "乙": [Step("wait", "1"), Step("wait", "1")]}
    assert estimate_steps(groups2, ["主"]) == 3     # 1 + 2

    # 无限重复不可估
    groups3 = {"主": [Step("call", "甲", -1)], "甲": [Step("wait", "1")]}
    assert estimate_steps(groups3, ["主"]) == -1

    # 数据行数相乘
    assert estimate_steps(groups, ["主"], data_rows=3) == 12


def test_validate_new_types():
    """新类型的校验：set 变量名、drag 两端、copy 非空。"""
    assert Step("set", "名字=值").validate() == []
    assert Step("set", "没等号").validate()
    assert Step("set", "{名字}=值").validate(), "变量名不能带花括号"
    assert Step("copy", "订单号").validate() == []
    assert Step("copy", "").validate()
    assert Step("drag", "100,200|300,400").validate() == []
    assert Step("drag", "起点.png|终点.png").validate() == []
    assert Step("drag", "100,200").validate(), "只有一段"
    assert Step("drag", "起点.png|").validate(), "终点为空"
    assert Step("drag", "100,200|@1,2,3,4").validate(), \
        "既不是坐标也不是图片文件名"
    assert Step("if_not_image", "a.png|甲").validate() == []
    assert Step("if_window", "记事本|甲").validate() == []
    assert Step("if_else", "a.png|甲|乙").validate() == []
    assert Step("if_else", "a.png|甲").validate(), "二选一要三段"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERR   {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
