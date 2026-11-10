# -*- coding: utf-8 -*-
"""文件关联：命令行生成与非 Windows 降级（不真的改动系统关联）。"""
import os
import sys

import pytest

from rpa import file_assoc, platform_support

pytestmark = pytest.mark.engine


def test_open_command_uses_exe_or_script():
    exe_command = file_assoc.open_command(exe=r"C:\Apps\MeowGo\MeowGo.exe")
    assert exe_command == '"C:\\Apps\\MeowGo\\MeowGo.exe" "%1"'

    script = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "start-app.py")
    source_command = file_assoc.open_command(script=script)
    assert source_command.endswith(f'"{script}" "%1"')
    # 源码运行优先用 pythonw，双击时不该弹出控制台
    assert "python" in source_command.lower()
    if sys.platform == "win32":
        assert "pythonw.exe" in source_command.lower()


def test_register_requires_windows(monkeypatch):
    monkeypatch.setattr(platform_support, "IS_WINDOWS", False)
    ok, error = file_assoc.register()
    assert ok is False and "Windows" in error
    assert file_assoc.is_registered() is False
    ok, error = file_assoc.unregister()
    assert ok is False and "Windows" in error


def test_prog_id_is_stable():
    # 关联键名不要随手改：改了用户之前的关联会失效
    assert file_assoc.PROG_ID == "MeowGo.Project"
    assert file_assoc.CLASSES == r"Software\Classes"


@pytest.mark.system
def test_is_registered_returns_bool():
    assert isinstance(file_assoc.is_registered(), bool)
