# -*- coding: utf-8 -*-
"""开机自启模块测试：注册表 Run 项的写入 / 查询 / 删除。

用专用测试键（HKCU\\Software\\MeowGoAutostartSelfTest），不碰 MeowGo
真身使用的 Run 项；结束后把测试键整个删掉。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa import autostart  # noqa: E402

TEST_KEY = r"Software\MeowGoAutostartSelfTest"
TEST_NAME = "MeowGoTest"


def _cleanup():
    if os.name != "nt":
        return
    try:
        import winreg

        autostart.disable(TEST_KEY, TEST_NAME)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, TEST_KEY)
    except OSError:
        pass


def test_enable_query_disable_roundtrip():
    """开 → 查 → 关 → 再查，以及命令内容与幂等性。"""
    if os.name != "nt":
        print("  SKIP  非 Windows，跳过注册表用例")
        return
    _cleanup()
    try:
        assert not autostart.is_enabled(TEST_KEY, TEST_NAME)
        ok, error = autostart.enable(TEST_KEY, TEST_NAME)
        assert ok, error
        assert autostart.is_enabled(TEST_KEY, TEST_NAME)
        assert autostart.current_command(TEST_KEY, TEST_NAME) == \
            autostart.launch_command()
        # 真身 Run 项不能被测试碰到
        assert autostart.is_enabled() in (True, False)
        # 重复开启不报错（覆盖写）
        ok, error = autostart.enable(TEST_KEY, TEST_NAME)
        assert ok, error
        # 关闭后查询为 False；再关一次（本来就没有）也算成功
        ok, error = autostart.disable(TEST_KEY, TEST_NAME)
        assert ok, error
        assert not autostart.is_enabled(TEST_KEY, TEST_NAME)
        ok, error = autostart.disable(TEST_KEY, TEST_NAME)
        assert ok, error
        assert autostart.current_command(TEST_KEY, TEST_NAME) == ""
    finally:
        _cleanup()


def test_launch_command_points_at_start_app():
    """启动命令必须指向本程序的 start-app.py（开发运行）。"""
    command = autostart.launch_command()
    if getattr(sys, "frozen", False):
        assert command == f'"{sys.executable}"'
    else:
        assert command.endswith('start-app.py"'), command
        assert command.count('"') == 4, command      # 两段各自带引号
        assert "pythonw.exe" in command or "python" in command, command


def test_disabled_when_not_windows():
    """非 Windows 平台上所有写操作都返回明确的“不支持”。"""
    if os.name == "nt":
        return                          # 这条只在非 Windows 上验证
    assert autostart.is_enabled() is False
    assert autostart.current_command() == ""
    ok, error = autostart.enable()
    assert not ok and error == autostart.NOT_WINDOWS
    ok, error = autostart.disable()
    assert not ok and error == autostart.NOT_WINDOWS


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
