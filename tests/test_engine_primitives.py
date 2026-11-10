# -*- coding: utf-8 -*-
"""新增流程原语测试：循环 / 调用参数 / 失败跳转 / 文件与文本步骤。"""
import os
import tempfile

import pytest

from rpa.engine import Executor, Group, Step

pytestmark = pytest.mark.engine


def _executor(groups, entry="main", **kwargs):
    log = kwargs.pop("log", lambda level, msg: None)
    return Executor(groups=groups, order=[entry], log=log, **kwargs)


def test_range_loop_and_loop_vars():
    ex = _executor({
        "main": [Step("set", "累计="), Step("for_each", "1,3|body")],
        "body": [Step("set", "累计={累计}{loop}")],
    })
    assert ex.run("main") == "finished"
    assert ex.vars["累计"] == "123"
    # 循环结束后 loop / loop_index 不应污染运行变量
    assert "loop" not in ex.vars and "loop_index" not in ex.vars


def test_while_loop_and_break():
    ex = _executor({
        "main": [Step("set", "n=0"), Step("while_var", "n<3|body")],
        "body": [Step("set", "n={n}+1"), Step("break")],
    })
    assert ex.run("main") == "finished"
    assert ex.vars["n"] == "1"
    assert any(item["type"] == "break" and item["ok"] for item in ex.stats)


def test_continue_skips_rest_of_round():
    ex = _executor({
        "main": [Step("set", "n=0"), Step("set", "flag="),
                 Step("for_each", "1,3|body")],
        "body": [Step("set", "n={n}+1"), Step("continue"),
                 Step("set", "flag={flag}x")],
    })
    assert ex.run("main") == "finished"
    assert ex.vars["n"] == "3"
    assert ex.vars["flag"] == ""


def test_call_parameters_are_local():
    ex = _executor({
        "main": [Step("set", "外部=保留"), Step("call", "sub|x=1")],
        "sub": [Step("set", "内部={x}")],
    })
    assert ex.run("main") == "finished"
    assert ex.vars["内部"] == "1"
    assert ex.vars["外部"] == "保留"
    assert "x" not in ex.vars


def test_on_fail_goto_runs_cleanup_group():
    tmp = tempfile.mkdtemp()
    missing = os.path.join(tmp, "never.txt")
    ex = _executor({
        "main": [Step("file", f"wait:{missing}|0.01", timeout=0.01,
                      on_fail="goto:cleanup")],
        "cleanup": [Step("set", "已清理=是")],
    })
    assert ex.run("main") == "finished"
    assert ex.vars["已清理"] == "是"
    assert ex.report()["failed"] == 1


def test_file_json_regex_date_steps():
    tmp = tempfile.mkdtemp()
    source = os.path.join(tmp, "a.txt")
    with open(source, "w", encoding="utf-8") as fh:
        fh.write("hello")
    ex = _executor({
        "main": [
            Step("file", f"copy:{source}|{tmp}/b.txt"),
            Step("file", f"mkdir:{tmp}/sub"),
            Step("json", "数据|user.name|姓名"),
            Step("regex", r"姓名|\d+"),
            Step("date", "今天|%Y"),
            Step("random_wait", "0,0.001"),
            Step("file", f"delete:{tmp}/b.txt"),
        ],
    }, data=[{"数据": '{"user": {"name": "Meow 42"}}'}])
    assert ex.run("main") == "finished"
    assert os.path.exists(f"{tmp}/b.txt") is False
    assert os.path.isdir(f"{tmp}/sub")
    assert ex.vars["姓名"] == "42"
    assert len(ex.vars["今天"]) == 4


def test_run_from_selected_step():
    ex = _executor({
        "main": [Step("set", "a=1"), Step("set", "b=2"), Step("set", "c=3")],
    })
    assert ex.run("main", start_step=1) == "finished"
    assert "a" not in ex.vars
    assert ex.vars["b"] == "2" and ex.vars["c"] == "3"
    assert [item["index"] for item in ex.stats] == [2, 3]


def test_new_types_validate():
    assert not Step("for_each", "1,3|处理").validate()
    assert Step("for_each", "1,3").validate(), "缺目标组应报错"
    assert Step("for_each", "1,3,0|处理").validate(), "步长 0 应报错"
    assert not Step("while_var", "次数<5|重试").validate()
    assert Step("while_var", "次数<5").validate()
    assert Step("break", "多余内容").validate()
    assert Step("file", "copy:只有源").validate()
    assert not Step("file", "copy:源|目标").validate()
    assert not Step("json", "data|user.name").validate()
    assert Step("json", "缺字段路径").validate()
    assert not Step("regex", r"结果|\d+").validate()
    assert Step("regex", "结果|(").validate(), "非法正则应报错"
    assert not Step("date", "今天|%Y-%m-%d").validate()
    assert Step("random_wait", "3,1").validate(), "最小值大于最大值应报错"
    assert Step("section", "").validate()
    assert Step("click", "a.png", on_fail="随便写").validate()
    assert not Step("click", "a.png", on_fail="goto:清理").validate()


def test_nested_loops_restore_outer_loop_vars():
    ex = _executor({
        "main": [Step("set", "外="), Step("set", "内="),
                 Step("set", "外序号="), Step("for_each", "1,2|outer")],
        "outer": [
            Step("set", "外={外}{loop}"),
            Step("for_each", "1,2|inner"),
            Step("set", "外序号={外序号}{loop_index}"),
        ],
        "inner": [Step("set", "内={内}{loop}")],
    })
    assert ex.run("main") == "finished"
    assert ex.vars["外"] == "12"
    assert ex.vars["内"] == "1212"
    # 内层结束后必须恢复外层的 loop_index，而不是留下 2
    assert ex.vars["外序号"] == "12"
    assert "loop" not in ex.vars and "loop_index" not in ex.vars


def test_break_in_inner_loop_does_not_stop_outer():
    ex = _executor({
        "main": [Step("set", "内="), Step("for_each", "1,2|outer")],
        "outer": [Step("for_each", "1,3|inner")],
        "inner": [Step("set", "内={内}{loop}"),
                  Step("if_var", "loop==2|stop_inner")],
        "stop_inner": [Step("break")],
    })
    assert ex.run("main") == "finished"
    # 每轮外层里，内层跑到 2 就被 break；外层仍继续到第 2 轮
    assert ex.vars["内"] == "1212"


def test_while_loop_max_rounds_warns():
    messages = []
    ex = _executor({
        "main": [Step("set", "n=0"),
                 Step("while_var", "n<99|body", repeat=3)],
        "body": [Step("set", "n={n}+1")],
    }, log=lambda level, msg: messages.append((level, msg)))
    assert ex.run("main") == "finished"
    assert ex.vars["n"] == "3"
    assert any(level == "warn" and "达到最大轮次 3" in msg
               for level, msg in messages)


def test_loop_call_parameters_see_loop_vars():
    ex = _executor({
        "main": [Step("set", "累计="),
                 Step("for_each", "1,2|body")],
        "body": [Step("call", "sub|x={loop}")],
        "sub": [Step("set", "累计={累计}{x}")],
    })
    assert ex.run("main") == "finished"
    assert ex.vars["累计"] == "12"
    assert "x" not in ex.vars


def test_retry_inside_loop_keeps_running():
    tmp = tempfile.mkdtemp()
    missing = os.path.join(tmp, "never.txt")
    ex = _executor({
        "main": [Step("for_each", "1,2|body")],
        "body": [Step("file", f"delete:{missing}", retry=1,
                      retry_delay=0)],
    })
    assert ex.run("main") == "finished"
    file_stats = [item for item in ex.stats if item["type"] == "file"]
    assert len(file_stats) == 2          # 两轮各执行一次
    assert all(item["attempts"] == 2 for item in file_stats)
    assert ex.report()["failed"] == 2


def test_on_fail_goto_inside_loop_runs_cleanup_each_round():
    tmp = tempfile.mkdtemp()
    missing = os.path.join(tmp, "never.txt")
    ex = _executor({
        "main": [Step("set", "已清理="),
                 Step("for_each", "1,2|body")],
        "body": [Step("file", f"wait:{missing}|0.01", timeout=0.01,
                      on_fail="goto:cleanup")],
        "cleanup": [Step("set", "已清理={已清理}{loop}")],
    })
    assert ex.run("main") == "finished"
    assert ex.vars["已清理"] == "12"
    assert ex.report()["failed"] == 2


def test_new_primitive_field_validation():
    # file：wait 秒数、delete/mkdir 路径都要校验
    assert Step("file", "wait:some.txt|abc").validate()
    assert Step("file", "wait:some.txt|-1").validate()
    assert Step("file", "delete:").validate()
    assert Step("file", "mkdir:").validate()
    assert not Step("file", "wait:some.txt|0.5", timeout=2).validate()
    assert Step("file", "wait:some.txt|1", timeout=-1).validate()

    # regex：分组号必须是整数且不能超过实际分组数
    assert Step("regex", r"结果|\d+|abc").validate()
    assert Step("regex", r"结果|\d+|2").validate()
    assert not Step("regex", r"结果|(\d+)|1").validate()
    assert Step("regex", r"结果|\d+|0|多余").validate()

    # random_wait：必须正好两个非负数字
    assert Step("random_wait", "1").validate()
    assert Step("random_wait", "1,2,3").validate()
    assert Step("random_wait", "-1,2").validate()
    assert Step("random_wait", "3,1").validate()
    assert not Step("random_wait", "1,3").validate()


def test_wait_image_types_registered():
    """等待图片出现 / 消失：编号、集合归属与界面字段都按图片步骤处理。"""
    from rpa.engine import (FAIL_ACTION_STEP_TYPES, IMAGE_STEP_TYPES,
                            REGION_STEP_TYPES, STEP_TYPES, TIMEOUT_STEP_TYPES,
                            TYPE_TO_CODE, WAIT_IMAGE_TYPES)

    assert STEP_TYPES["wait_image"][0] == "等待图片出现"
    assert STEP_TYPES["wait_gone"][0] == "等待图片消失"
    assert TYPE_TO_CODE["wait_image"] == 38.0
    assert TYPE_TO_CODE["wait_gone"] == 39.0
    # 界面据此显示「截图 / 选图 / 预览 / 识别范围 / 第几个 / 灰度 / 超时」
    for types in (IMAGE_STEP_TYPES, REGION_STEP_TYPES, TIMEOUT_STEP_TYPES,
                  FAIL_ACTION_STEP_TYPES):
        assert WAIT_IMAGE_TYPES <= types, types


def test_wait_image_validation():
    assert not Step("wait_image", "加载完成.png", timeout=5).validate()
    assert not Step("wait_gone", "遮罩.png", timeout=5,
                    region="10,20,300,200").validate()
    # 候选图 / 第几个 / 偏移 这些后缀写法也能用
    assert not Step("wait_image", "a.png;b.png#2+4,0").validate()
    # 空内容、只有区域没有文件名、非法区域、负超时都要拦下
    assert Step("wait_image", "").validate()
    assert Step("wait_image", "@10,20,300,200").validate()
    assert Step("wait_image", "a.png", region="1,2,3").validate()
    assert Step("wait_image", "a.png", timeout=-1).validate()


def test_wait_image_runtime(monkeypatch):
    """等到 / 等不到 / 消失成功 / 超时中止 四条路径。"""
    from rpa.engine import Point

    def fake_locate(self, image, step=None):
        return Point(10, 20) if image == "有.png" else None

    monkeypatch.setattr(Executor, "_locate", fake_locate)

    # 出现：找到即成功，后面步骤照跑
    ex = _executor({"main": [Step("wait_image", "有.png", timeout=0.5),
                             Step("set", "后续=跑了")]})
    assert ex.run("main") == "finished"
    assert ex.stats[0]["ok"] is True
    assert ex.vars["后续"] == "跑了"

    # 消失：图片本来就不在，立即算成功
    ex = _executor({"main": [Step("wait_gone", "没有.png", timeout=0.5)]})
    assert ex.run("main") == "finished"
    assert ex.stats[0]["ok"] is True

    # 出现：等不到 → 这步失败，默认 continue（skip）继续下一步
    ex = _executor({"main": [Step("wait_image", "没有.png", timeout=0.2),
                             Step("set", "后续=跑了")]})
    assert ex.run("main") == "finished"
    assert ex.stats[0]["ok"] is False
    assert ex.vars["后续"] == "跑了"

    # 消失：一直没消失 + on_fail=stop → 整轮中止
    ex = _executor({"main": [Step("wait_gone", "有.png", timeout=0.2,
                                  on_fail="stop"),
                             Step("set", "后续=不该跑")]})
    assert ex.run("main") == "error"
    assert "后续" not in ex.vars


def test_wait_image_failure_respects_on_fail_goto():
    """超时后按「失败后处理 → 跳转到组」走清理组。"""

    def fake_locate(self, image, step=None):
        return None

    original = Executor._locate
    Executor._locate = fake_locate
    try:
        ex = _executor({
            "main": [Step("wait_image", "没有.png", timeout=0.1,
                          on_fail="goto:收尾")],
            "收尾": [Step("set", "已收尾=是")],
        })
        assert ex.run("main") == "finished"
        assert ex.vars["已收尾"] == "是"
    finally:
        Executor._locate = original


def test_file_write_append_read_steps():
    """文件写 / 追加 / 读：自动建目录、自动补换行、变量可回落。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "out", "记录.txt")
        ex = _executor({"main": [
            Step("set", "名字=张三"),
            Step("file", "write:" + path + "|第一行 {名字}"),
            Step("file", "append:" + path + "|第二行"),
            Step("file", "read:" + path + "|内容"),
        ]})
        assert ex.run("main") == "finished"
        assert ex.vars["内容"] == "第一行 张三\n第二行\n"
        # 覆盖写而不是追加
        ex2 = _executor({"main": [Step("file", "write:" + path + "|重来")]})
        assert ex2.run("main") == "finished"
        with open(path, encoding="utf-8") as fh:
            assert fh.read() == "重来\n"


def test_file_read_missing_marks_step_failed_but_continues():
    with tempfile.TemporaryDirectory() as tmp:
        missing = os.path.join(tmp, "没有.txt")
        ex = _executor({"main": [Step("file", "read:" + missing + "|内容"),
                                 Step("set", "后续=跑了")]})
        assert ex.run("main") == "finished"
        assert ex.stats[0]["ok"] is False
        assert "内容" not in ex.vars
        assert ex.vars["后续"] == "跑了"


def test_file_write_validation():
    assert Step("file", "write:out/a.txt").validate()
    assert Step("file", "write:|内容").validate()
    assert Step("file", "append:out/a.txt").validate()
    assert Step("file", "read:out/a.txt").validate()
    assert Step("file", "read:out/a.txt|").validate()
    assert Step("file", "rename:a|b").validate()
    assert not Step("file", "write:out/a.txt|内容").validate()
    assert not Step("file", "read:out/a.txt|文本").validate()


def test_ocr_read_step(monkeypatch):
    """读取屏幕文字：写进变量；缺 OCR 时按失败处理。"""
    import numpy as np

    from rpa import ocr

    assert not Step("ocr_read", "单号").validate()
    assert Step("ocr_read", "").validate()
    assert not Step("ocr_read", "单号", region="10,20,300,100").validate()
    assert Step("ocr_read", "单号", region="1,2,3").validate()

    monkeypatch.setattr(Executor, "_grab_screen",
                        lambda self: (np.zeros((20, 30, 3), dtype="uint8"),
                                      (0, 0)))
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "read_text", lambda *a, **k: "单号：A001\n金额：88")
    ex = _executor({"main": [Step("ocr_read", "文本")]})
    assert ex.run("main") == "finished"
    assert ex.vars["文本"] == "单号：A001\n金额：88"

    # 没识别到文字：这步失败，变量不动
    monkeypatch.setattr(ocr, "read_text", lambda *a, **k: "")
    ex2 = _executor({"main": [Step("ocr_read", "空白")]})
    assert ex2.run("main") == "finished"
    assert ex2.stats[0]["ok"] is False
    assert "空白" not in ex2.vars

    # 缺 OCR 依赖：直接中止并提示
    monkeypatch.setattr(ocr, "available", lambda: False)
    ex3 = _executor({"main": [Step("ocr_read", "文本")]})
    assert ex3.run("main") == "error"


def test_group_retry_reruns_whole_group(monkeypatch):
    """组级重试：整组跑完有失败步骤就再跑一遍整组。"""
    state = {"attempt": 0}

    def fake_run_step(self, step, depth=0):
        if step.type == "wait":
            state["attempt"] += 1
            if state["attempt"] == 1:
                self._step_ok = False       # 第一次失败
                return
        self._step_ok = True

    monkeypatch.setattr(Executor, "run_step", fake_run_step)
    ex = Executor(groups={"body": [Step("wait", "0.1")]}, order=["body"],
                  group_retry={"body": (1, 0.0)})
    assert ex.run("body") == "finished"
    assert state["attempt"] == 2
    assert [item["ok"] for item in ex.stats] == [False, True]

    # 没配重试就只跑一次
    state["attempt"] = 0
    ex2 = Executor(groups={"body": [Step("wait", "0.1")]}, order=["body"])
    assert ex2.run("body") == "finished"
    assert state["attempt"] == 1


def test_group_retry_serialization():
    group = Group("主流程", [Step("wait", "1")], retry=2, retry_delay=1.5)
    restored = Group.from_dict(group.to_dict())
    assert (restored.retry, restored.retry_delay) == (2, 1.5)
    # 旧配置没有这两个键：按 0 次、0.5 秒处理
    legacy = Group.from_dict({"name": "旧组", "steps": []})
    assert (legacy.retry, legacy.retry_delay) == (0, 0.5)


def test_persistent_variables_survive_runs():
    """{持久:键} 跨运行保留，写入时回调落盘。"""
    store: dict[str, str] = {}
    saved: list[tuple[str, str]] = []
    ex = _executor({"main": [
        Step("set", "持久:单号=A001"),
        Step("set", "回读={持久:单号}"),
    ]}, persist=store, on_persist=lambda key, value: saved.append((key, value)))
    assert ex.run("main") == "finished"
    assert ex.vars["回读"] == "A001"
    assert store == {"单号": "A001"}
    assert saved == [("单号", "A001")]

    # 第二次运行：没有写，也能读到上次的值
    ex2 = _executor({"main": [Step("set", "回读={持久:单号}")]}, persist=store)
    assert ex2.run("main") == "finished"
    assert ex2.vars["回读"] == "A001"

    # 持久变量名不能为空
    assert Step("set", "持久:=值").validate()
