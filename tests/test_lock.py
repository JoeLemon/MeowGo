# -*- coding: utf-8 -*-
"""单实例锁：同名第二次拿不到，释放后可以重新拿。"""
import os
import tempfile
import uuid

import pytest

from rpa.lock import InstanceLock, make_lock_name

pytestmark = pytest.mark.engine


@pytest.mark.skipif(os.name != "nt", reason="命名互斥是 Windows 能力")
def test_instance_lock_blocks_and_releases():
    name = make_lock_name(tempfile.gettempdir(),
                          "TEST_" + uuid.uuid4().hex)
    first = InstanceLock(name)
    assert first.acquire() is True
    try:
        second = InstanceLock(name)
        assert second.acquire() is False
    finally:
        first.release()

    third = InstanceLock(name)
    assert third.acquire() is True
    third.release()
