# -*- coding: utf-8 -*-
"""新增流程原语测试：循环 / 调用参数 / 失败跳转 / 文件与文本步骤。"""
import os
import tempfile

import pytest

from rpa.engine import Executor, Step

pytestmark = pytest.mark.engine


def _executor(groups, entry="main", **kwargs):
    return Executor(groups=groups, order=[entry], log=lambda level, msg: None,
                    **kwargs)


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
