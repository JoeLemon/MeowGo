# -*- coding: utf-8 -*-
"""新增流程原语测试：循环 / 调用参数 / 失败跳转 / 文件与文本步骤。"""
import os
import tempfile

import pytest

from rpa.engine import Executor, Step

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
