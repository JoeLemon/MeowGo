# -*- coding: utf-8 -*-
"""公共测试夹具。

用例可能在无人值守的机器上跑（屏幕锁定、没有交互式会话），
而执行器检测到「锁屏 / 安全桌面」会一直暂停等待解锁，
表现为「测试卡住不动」——既不报错也不超时，很难查。

所以默认把「被会话状态挡住」当成否；真正要验证锁屏检测的用例
（tests/test_platform_optional.py）测的是 ``engine.session_locked``
这个底层函数，不受本夹具影响。
"""
from __future__ import annotations

import pytest

from rpa.engine import Executor


@pytest.fixture(autouse=True)
def _never_blocked_by_session_lock(monkeypatch):
    """避免引擎用例因锁屏检测一直等待。"""
    monkeypatch.setattr(Executor, "_session_blocked", lambda self: False)
