# -*- coding: utf-8 -*-
"""日志模块自检：每次运行一个文件、步骤上下文、同名文件不覆盖。"""
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa.logs import RunLog  # noqa: E402

pytestmark = pytest.mark.engine


def test_run_log_writes_steps_and_summary():
    tmp = tempfile.mkdtemp(prefix="rpa_test_logs_")
    try:
        run = RunLog(tmp, "全部执行组")
        assert run.opened and os.path.exists(run.path)
        run.step_start("登录", 0, "输入文本", "admin")
        run.write("error", "找不到图片", ("登录", 0))
        run.close("运行结束：finished · 1 步，失败 0，耗时 0.1 秒")

        with open(run.path, encoding="utf-8") as fh:
            text = fh.read()
        assert "开始运行：全部执行组" in text
        assert "▶ 输入文本（admin）" in text
        assert "[登录 第1步] 找不到图片" in text
        assert "ERROR" in text
        assert "运行结束：finished" in text

        # 同一秒内再建一个，不能覆盖前一个文件
        second = RunLog(tmp, "第二次")
        assert second.path != run.path
        second.close()
        assert os.path.exists(run.path) and os.path.exists(second.path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_run_log_unwritable_dir_does_not_raise():
    # 把「文件」当目录用：Windows / Linux 都会抛 OSError
    with tempfile.NamedTemporaryFile() as fh:
        run = RunLog(os.path.join(fh.name, "logs"))
        assert not run.opened
        run.write("info", "x")            # 没有文件也应静默
        run.close("summary")
