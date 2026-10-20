# -*- coding: utf-8 -*-
"""宏录制真实钩子 E2E 探针（修正版）。

诊断结论（上一轮）：
- 钩子健在，9 次回调全部到达；之前探针的失败是它自己原始 SendInput 的 bug
- pyautogui 的移动走 SetCursorPos，不过 LL 钩子 —— 本探针用 mouse_event
  （与 pyautogui 按钮同一条注入路径）补上移动事件，覆盖 move 分支
- 上一轮的 scroll 缺失是退出竞态：发完事件后多泵 500ms 再停
"""
import ctypes
import os
import sys
import tempfile
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
os.chdir(os.path.dirname(_HERE))

from rpa import gui as gui_module  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="rpa_probe_")
gui_module.SETTINGS_PATH = os.path.join(_TMP, "settings.json")
gui_module.enable_dpi_awareness()

app = gui_module.App()
app.geometry("1000x600+1500+60")     # 挪到角落，避开合成坐标区
app.update()
print(f"[probe] scale={app.scale} screen={app.winfo_screenwidth()}x"
      f"{app.winfo_screenheight()}")

import pyautogui  # noqa: E402

# ---- 与 pyautogui 按钮完全同一条注入路径：mouse_event + 归一化绝对坐标 ----
_user32 = ctypes.windll.user32
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_ABSOLUTE = 0x8000


def inject_move(x, y):
    """合成一次鼠标移动（pyautogui 的 _moveTo 走 SetCursorPos，不过钩子）。"""
    width, height = pyautogui.size()
    nx = 65536 * x // width + 1
    ny = 65536 * y // height + 1
    _user32.mouse_event(_MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE,
                        ctypes.c_long(nx), ctypes.c_long(ny), 0, 0)


rec = gui_module.MacroRecorder(
    app,
    on_state=lambda active: print(f"[state] active={active}"),
    on_done=lambda steps: print(
        f"[done] {[(s.type, s.content, s.repeat) for s in steps]}"),
    on_cancel=lambda: print("[cancel] 空录制"),
    on_hint=lambda text: print(f"[hint] {text}"))

# ---- 在 start() 之前包装实例方法，让真钩子调到计数包装 ----
calls = {"n": 0}
_orig_on_hook = gui_module.MacroRecorder._on_hook


def _counting(self, nCode, wParam, lParam):
    calls["n"] += 1
    return _orig_on_hook(self, nCode, wParam, lParam)


rec._on_hook = _counting.__get__(rec)

ok = rec.start(ignore_rect=(0, 0, 1, 1))   # 只忽略左上角 1px，不影响合成坐标
print(f"[probe] start={ok} hook={rec._hook} proc={rec._proc is not None}")

# ---- 工作线程：模拟引擎在跑，依次发合成鼠标事件 ----
sent = {"n": 0, "err": None}


def _script():
    try:
        inject_move(300, 200)
        pyautogui.click(300, 200)
        sent["n"] += 1
        time.sleep(0.12)
        inject_move(400, 200)
        pyautogui.click(400, 200)
        pyautogui.click(400, 200)          # 双击（同点位，间隔 < 450ms）
        sent["n"] += 1
        time.sleep(0.12)
        inject_move(500, 300)
        pyautogui.click(500, 300, button="right")
        sent["n"] += 1
        time.sleep(0.12)
        pyautogui.scroll(360)               # 滚轮 3 格（OS 增量须为 120 倍数）
        sent["n"] += 1
    except Exception as exc:               # noqa: BLE001
        sent["err"] = f"{type(exc).__name__}: {exc}"


t0 = time.monotonic()
threading.Thread(target=_script, daemon=True).start()

# ---- 主线程：泵消息 + 诊断；发完后多泵 500ms 冲掉尾部事件 ----
while time.monotonic() < t0 + 5.0:
    app.update()
    time.sleep(0.02)
    if sent["n"] >= 4 or sent["err"]:
        # 事件已进 Windows 队列但回调可能还没派发：继续泵一会儿
        flush_until = time.monotonic() + 0.5
        while time.monotonic() < flush_until:
            app.update()
            time.sleep(0.02)
        break

print(f"[probe] sent={sent['n']} err={sent['err']} "
      f"hook_calls={calls['n']} pending_events={len(rec._events)}")

# ---- 钩子存活判定：卸载句柄，若已被系统移除会返回 0 + error 1428 ----
u32 = ctypes.WinDLL("user32", use_last_error=True)
ctypes.set_last_error(0)
r = u32.UnhookWindowsHookEx(rec._hook)
err = ctypes.get_last_error()
print(f"[probe] UnhookWindowsHookEx -> {r} last_error={err} "
      f"({'钩子已被系统移除' if r == 0 and err == 1428 else '钩子原本健在'})")

rec.stop()
app.destroy()
got = [(s.type, s.content, s.repeat) for s in rec._steps]
want = [
    ("move_xy", "300,200", 0),
    ("click_xy", "300,200", 0),
    ("move_xy", "400,200", 0),
    ("click_xy", "400,200", 2),
    ("move_xy", "500,300", 0),
    ("click_xy", "500,300,right", 0),
    ("scroll", "300", 0),
]


def as_subsequence(expected, actual):
    """期望步骤是否按顺序出现在实际结果里（允许夹带真实鼠标活动）。"""
    it = iter(actual)
    return all(item in it for item in expected)


print("[probe] 期望(按序):", want)
print("[probe] 实际:", got)
if not as_subsequence(want, got):
    print("MACRO-E2E FAIL")
elif got == want:
    print("MACRO-E2E PASS（无多余步骤）")
else:
    extra = [s for s in got if s not in want]
    print(f"MACRO-E2E PASS（另有 {len(extra)} 步真实鼠标活动被一并录入，"
          f"属正常：钩子对真实操作一律忠实记录）")
