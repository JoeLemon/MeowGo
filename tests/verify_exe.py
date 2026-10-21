# -*- coding: utf-8 -*-
"""打包产物验证：启动 dist 里的 exe，确认窗口真的出现，截图后关闭。

按「进程 ID 属于刚启动的 exe」来找窗口：只按标题找会被同名的资源管理器
窗口（比如正好开着 dist\\MeowGo 文件夹）蒙混过去。

用法：
    python tests\\verify_exe.py
"""
import ctypes
import ctypes.wintypes as wintypes
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "dist", "MeowGo", "MeowGo.exe")
TITLE = "MeowGo"          # 窗口标题应当正是这个名字
SW_RESTORE = 9

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
found: list[tuple[int, str]] = []
target_pid = 0


def _window_pid(hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _enum_callback(hwnd, _lparam):
    if not user32.IsWindowVisible(hwnd):
        return True
    if target_pid and _window_pid(hwnd) != target_pid:
        return True
    length = user32.GetWindowTextLengthW(hwnd)
    if length:
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        found.append((hwnd, buffer.value))
    return True


_callback = EnumProc(_enum_callback)


def wait_window(timeout: float = 60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        found.clear()
        user32.EnumWindows(_callback, 0)
        if found:
            return found[0]
        time.sleep(0.5)
    return None


def main() -> int:
    global target_pid

    if not os.path.exists(EXE):
        print(f"  FAIL  找不到打包产物：{EXE}")
        return 1
    size = sum(os.path.getsize(os.path.join(base, name))
               for base, _, names in os.walk(os.path.dirname(EXE))
               for name in names)
    print(f"  INFO  产物大小 {size / 1024 / 1024:.1f} MB")
    print(f"  INFO  启动 {os.path.basename(EXE)}")

    proc = subprocess.Popen([EXE], cwd=os.path.dirname(EXE))
    target_pid = proc.pid
    window = wait_window()
    if window is None:
        proc.terminate()
        print("  FAIL  等待 60 秒仍未出现窗口（可能缺少依赖或崩溃）")
        return 1
    hwnd, title = window
    print(f"  PASS  窗口已出现（pid {proc.pid}）：{title}")

    # 置顶并截图，确认界面（图标/字体/data 文件）都正常
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    time.sleep(1.5)
    try:
        from PIL import ImageGrab

        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        box = (rect.left, rect.top, rect.right, rect.bottom)
        size_ok = rect.right > rect.left and rect.bottom > rect.top
        # all_screens：副屏在主屏左边时坐标是负的，只抓主屏会得到一张白图
        grab = ImageGrab.grab(bbox=box, all_screens=True) if size_ok else None
        if grab is not None:
            path = os.path.join(ROOT, "tests", "exe_preview.png")
            grab.save(path)
            colors = grab.convert("RGB").getcolors(maxcolors=4096)
            print(f"  PASS  截图已保存 tests/exe_preview.png {box}"
                  f"（{len(colors) if colors else '>'} 种颜色）")
    except Exception as exc:  # noqa: BLE001
        print(f"  INFO  截图跳过：{exc}")

    time.sleep(0.5)
    alive = proc.poll() is None
    print(f"  INFO  进程仍在运行：{alive}")
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    print("  PASS  已关闭打包程序")
    return 0 if (title == TITLE and alive) else 1


if __name__ == "__main__":
    sys.exit(main())
