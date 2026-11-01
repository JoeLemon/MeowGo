# -*- coding: utf-8 -*-
"""跨平台导入冒烟：所有模块都要能 import，不因 Windows 专属 API 崩溃。"""
import importlib
import os

import pytest

MODULES = [
    "rpa", "rpa.assets", "rpa.autostart", "rpa.dialogs", "rpa.engine",
    "rpa.gui", "rpa.i18n", "rpa.lock", "rpa.logs", "rpa.ocr",
    "rpa.pixel_cat", "rpa.platform_support", "rpa.recorder", "rpa.theme",
    "rpa.tray", "rpa.uia", "rpa.uikit",
]

pytestmark = pytest.mark.engine


def test_all_modules_import():
    for name in MODULES:
        importlib.import_module(name)


def test_engine_skips_win32_api_on_non_windows():
    from rpa import engine

    if os.name != "nt":
        assert engine.ctypes is None, "非 Windows 上不应持有 windll 相关的 ctypes"


def test_platform_capabilities_are_consistent():
    from rpa import platform_support as ps

    status = ps.permission_status()
    assert set(status) == {"screen_recording", "accessibility"}
    assert ps.available("system_tray") == ps.IS_WINDOWS
    assert ps.available("applescript") == ps.IS_MACOS
    if not ps.IS_MACOS:
        assert ps.missing_permissions() == []
