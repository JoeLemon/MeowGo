# -*- coding: utf-8 -*-
"""键鼠录制模块：键名映射、钩子返回值类型、非 Windows 降级。"""
import ctypes
import ctypes.wintypes
import os

import pytest

from rpa import recorder

pytestmark = pytest.mark.engine


def _noop(*_args, **_kwargs):
    return None


def test_vk_to_key_name_basics():
    assert recorder.vk_to_key_name(0x0D) == "enter"
    assert recorder.vk_to_key_name(0x70) == "f1"
    assert recorder.vk_to_key_name(0x87) == "f24"
    assert recorder.vk_to_key_name(0x60) == "num0"
    assert recorder.vk_to_key_name(0x69) == "num9"
    assert recorder.vk_to_key_name(0x30) is None      # 顶层数字键不识别
    assert recorder.vk_to_key_name(0x41) in (None, "a")


def test_ordered_modifiers_and_idempotent_stop():
    rec = recorder.KeyRecorder(None, _noop, _noop, _noop, _noop, _noop)
    rec._held = ["a", "shift", "ctrl"]
    assert rec._ordered() == ["ctrl", "shift", "a"]
    rec.stop()
    rec.stop()


def test_non_windows_start_degrades(monkeypatch):
    hints = []
    rec = recorder.KeyRecorder(None, _noop, _noop, _noop, _noop, hints.append)
    macro = recorder.MacroRecorder(None, _noop, _noop, _noop, hints.append)
    monkeypatch.setattr(recorder.sys, "platform", "linux")
    assert rec.start() is False
    assert macro.start() is False
    assert hints == ["按键录入只支持 Windows / macOS",
                     "宏录制只支持 Windows / macOS"]


@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 有 Win32 钩子 API")
def test_win32_hook_prototypes_keep_64bit_results():
    user32 = recorder._win_user32()
    assert user32.CallNextHookEx.restype is ctypes.c_ssize_t
    assert user32.GetWindowThreadProcessId.restype is ctypes.wintypes.DWORD
    assert recorder._HOOKPROC._restype_ is ctypes.c_ssize_t
    assert recorder._MOUSEHOOKPROC._restype_ is ctypes.c_ssize_t
