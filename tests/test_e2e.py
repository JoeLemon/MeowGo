# -*- coding: utf-8 -*-
"""端到端测试：在真实屏幕上验证「识图点击」与「输入文本」确实生效。

会创建一个置顶的测试窗口，截取其画面作为模板，再驱动引擎去点击/输入，
最后检查窗口是否收到了点击与粘贴内容。
"""
import os
import random
import sys
import tempfile
import threading
import time
import tkinter as tk

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa.engine import Executor, Step  # noqa: E402
from rpa.gui import enable_dpi_awareness as _enable_dpi_awareness  # noqa: E402

pytestmark = [pytest.mark.gui, pytest.mark.slow]

_enable_dpi_awareness()

TMP = tempfile.mkdtemp(prefix="rpa_e2e_")


def build_window():
    root = tk.Tk()
    root.title("RPA 自检窗口")
    root.attributes("-topmost", True)
    root.geometry("340x260+320+220")
    # 防止误点或被系统关闭导致中途销毁
    root.protocol("WM_DELETE_WINDOW", lambda: None)
    canvas = tk.Canvas(root, width=320, height=180, highlightthickness=0)
    canvas.pack(padx=10, pady=(10, 6))
    # 随机色块，保证模板在屏幕上是唯一的
    random.seed(20240101)
    for row in range(6):
        for col in range(16):
            color = "#%06x" % random.randrange(0x111111, 0xEEEEEE)
            canvas.create_rectangle(col * 20, row * 30, col * 20 + 20,
                                    row * 30 + 30, fill=color, outline="")
    entry = tk.Text(root, height=2, width=40)
    entry.pack(padx=10)
    root.update()
    return root, canvas, entry


def pump_until(root, predicate, timeout=12.0):
    """在等待期间持续刷新 Tk 事件循环。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        root.update()
        if predicate():
            return True
        time.sleep(0.05)
    return False


def nudge_mouse_away_from_corners():
    """把鼠标从屏幕角落挪开。

    pyautogui 的 fail-safe 在四个角上会让所有调用直接抛错（README 里的
    「甩鼠标停执行」就是它）。测试环境的鼠标可能恰好停在角落里，导致
    点击/按键类步骤全部假失败——用 SetCursorPos 挪到画布中心（悬停，
    不产生点击）。
    """
    import ctypes

    ctypes.windll.user32.SetCursorPos(480, 320)


def run_steps_async(steps, extra_groups=None):
    executor = Executor()
    executor.base_dir = TMP
    executor.groups = {"自检": steps}
    if extra_groups:
        executor.groups.update(extra_groups)
    box = {"executor": executor, "logs": []}
    executor.log = lambda level, msg: box["logs"].append(
        (level, msg, time.time()))

    def work():
        box["result"] = executor.run("自检")

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return box, thread


def main():
    failures = []
    root, canvas, entry = build_window()
    clicks = []

    def on_click(event):
        clicks.append((event.x, event.y))

    canvas.bind("<Button-1>", on_click)
    canvas.bind("<Double-Button-1>", lambda e: clicks.append("double"))
    # 连续跑多个界面测试时，窗口可能还没画完就被别的窗口压住，这里多等一会儿
    root.lift()
    root.attributes("-topmost", True)
    root.focus_force()
    pump_until(root, lambda: False, timeout=1.2)
    nudge_mouse_away_from_corners()

    # 用窗口自身坐标截取画布区域，作为待识别模板
    x, y = canvas.winfo_rootx(), canvas.winfo_rooty()
    w, h = canvas.winfo_width(), canvas.winfo_height()
    from PIL import ImageGrab

    template = os.path.join(TMP, "canvas.png")
    ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(template)
    rel_name = "canvas.png"
    print(f"  INFO  模板已保存 {template} ({w}x{h})")

    def retry_lift():
        """偶发失败时重新置顶、重截模板，避开被遮挡的情况。"""
        root.lift()
        root.attributes("-topmost", True)
        root.focus_force()
        pump_until(root, lambda: False, timeout=1.0)
        ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(template)

    # --- 1. 识图单击 ---------------------------------------------------
    box, thread = run_steps_async([Step("click", rel_name)])
    got = pump_until(root, lambda: len(clicks) > 0, timeout=15)
    thread.join(timeout=5)
    if not got:
        clicks.clear()
        retry_lift()
        box, thread = run_steps_async([Step("click", rel_name)])
        got = pump_until(root, lambda: len(clicks) > 0, timeout=15)
        thread.join(timeout=5)
    if got:
        print(f"  PASS  识图单击（命中 {clicks[0]}）")
    else:
        failures.append("识图单击未生效")
        print("  FAIL  识图单击未生效")

    # --- 2. 识图双击 ---------------------------------------------------
    clicks.clear()
    box, thread = run_steps_async([Step("dblclick", rel_name)])
    got = pump_until(root, lambda: "double" in clicks, timeout=15)
    thread.join(timeout=5)
    if not got:
        clicks.clear()
        retry_lift()
        box, thread = run_steps_async([Step("dblclick", rel_name)])
        got = pump_until(root, lambda: "double" in clicks, timeout=15)
        thread.join(timeout=5)
    print("  PASS  识图双击" if got else "  FAIL  识图双击未生效")
    if not got:
        failures.append("识图双击未生效")

    # --- 3. 输入文本（剪贴板粘贴） --------------------------------------
    entry.focus_force()
    root.update()
    time.sleep(0.3)
    box, thread = run_steps_async([Step("input", "自动化喵 hello 123")])
    got = pump_until(root, lambda: "自动化喵 hello 123" in entry.get("1.0", "end"),
                     timeout=15)
    thread.join(timeout=5)
    print("  PASS  输入文本" if got else "  FAIL  输入文本未生效（内容: %r）"
          % entry.get("1.0", "end").strip())
    if not got:
        failures.append("输入文本未生效")

    # --- 4. 滚轮 / 等待不报错 -------------------------------------------
    logs = []
    executor = Executor()
    executor.base_dir = TMP
    executor.log = lambda level, msg: logs.append((level, msg))
    executor.groups = {"自检": [Step("wait", "0.3"), Step("scroll", "-60")]}
    result = executor.run("自检")
    if result == "finished" and not any(lv == "error" for lv, _ in logs):
        print("  PASS  等待 / 滚轮")
    else:
        failures.append("等待/滚轮执行异常")
        print(f"  FAIL  等待 / 滚轮：{result} {logs}")

    # --- 5. 拖拽（真实按下-移动-松开） ----------------------------------
    from PIL import Image

    canvas_img = Image.open(template)
    half_w = w // 2
    canvas_img.crop((0, 0, half_w, h)).save(os.path.join(TMP, "drag_from.png"))
    canvas_img.crop((half_w, 0, w, h)).save(os.path.join(TMP, "drag_to.png"))
    drag_events = []
    canvas.bind("<ButtonPress-1>", lambda e: drag_events.append("press"))
    canvas.bind("<B1-Motion>", lambda e: drag_events.append("motion"))
    canvas.bind("<ButtonRelease-1>", lambda e: drag_events.append("release"))
    box, thread = run_steps_async(
        [Step("drag", "drag_from.png|drag_to.png")])
    got = pump_until(root, lambda: "release" in drag_events, timeout=20)
    thread.join(timeout=8)
    ok = got and "press" in drag_events and "motion" in drag_events
    print("  PASS  拖拽（按下→移动→松开）" if ok
          else f"  FAIL  拖拽未生效（事件: {drag_events}）")
    if not ok:
        failures.append("拖拽未生效")

    # --- 6. 条件二选一：两个分支都要走到 --------------------------------
    # 造一张「文件在、屏幕上没有」的图：随机噪点，匹配不到（缺文件会直接
    # 报错中止，那是另一种场景，由单元测试覆盖）
    absent = Image.new("RGB", (24, 24))
    absent.putdata([(random.randrange(256), random.randrange(256),
                     random.randrange(256)) for _ in range(24 * 24)])
    absent.save(os.path.join(TMP, "absent.png"))
    branches = {"有图分支": [Step("wait", "0.05")],
                "没图分支": [Step("wait", "0.05")]}
    for image, expect in (("canvas.png", "有图分支"),
                          ("absent.png", "没图分支")):
        box, thread = run_steps_async(
            [Step("if_else", f"{image}|有图分支|没图分支")], branches)
        got = pump_until(root, lambda: box.get("result"), timeout=20)
        thread.join(timeout=8)
        ran = {i["group"] for i in box["executor"].stats}
        other = "没图分支" if expect == "有图分支" else "有图分支"
        ok = got and expect in ran and other not in ran
        print(f"  PASS  条件二选一（{image} → {expect}）" if ok
              else f"  FAIL  条件二选一（{image}，实际跑到 {ran}）")
        if not ok:
            failures.append(f"if_else {image}")

    # --- 7. 复制选中文字到变量（真实剪贴板） ----------------------------
    import pyperclip

    entry.focus_force()
    root.update()
    entry.tag_add(tk.SEL, "1.0", "end-1c")
    entry.mark_set(tk.INSERT, "1.0")
    root.update()
    time.sleep(0.2)
    pyperclip.copy("剪贴板原内容")
    time.sleep(0.2)
    box, thread = run_steps_async([Step("copy", "片段")])
    got = pump_until(root, lambda: box.get("result"), timeout=20)
    thread.join(timeout=8)
    value = box["executor"].vars.get("片段", "")
    ok = got and "自动化喵" in value
    print(f"  PASS  复制选中文字到变量（{value.strip()[:20]!r}）" if ok
          else f"  FAIL  复制到变量未生效（{value!r}）")
    if not ok:
        failures.append("copy 到变量")
    time.sleep(0.3)
    restored = pyperclip.paste() == "剪贴板原内容"
    print("  PASS  跑完还原剪贴板" if restored else "  FAIL  剪贴板未被还原")
    if not restored:
        failures.append("剪贴板未还原")

    # --- 8. 图还没出现时会等，出现了才点（真实等待语义） ------------------
    # 真实 pyautogui 找不到图时是抛 ImageNotFoundException，引擎必须接住并
    # 当成「还没出现」继续等——否则「0 = 一直等」只是死代码。
    # 用「全屏置顶盖窗」把测试窗口遮住来模拟「图上还没出现」：相比
    # withdraw/deiconify，盖窗不改变被测窗口的映射状态，点击不会撞上
    # 窗口刚恢复时的系统投递竞态（那也是真实使用中不会出现的场景）。
    timeline = [("t0", time.time())]

    def on_click(event):
        clicks.append((event.x, event.y))
        timeline.append(("click", time.time()))

    canvas.bind("<Button-1>", on_click)
    for seq in ("<ButtonPress-1>", "<ButtonRelease-1>"):
        root.bind_all(seq, lambda e, s=seq: timeline.append((s, time.time())))

    cover = tk.Toplevel(root)
    cover.attributes("-topmost", True)
    cover.attributes("-fullscreen", True)
    cover.configure(bg="#101418")
    cover.lift()
    cover.update()
    timeline.append(("covered", time.time()))
    time.sleep(0.3)
    clicks.clear()
    started = time.time()
    timeline.append(("engine_start", started))
    box, thread = run_steps_async([Step("click", rel_name, timeout=8,
                                        on_fail="skip")])
    time.sleep(1.5)                      # 这几轮引擎应该找不到，继续等
    cover.withdraw()                     # 拿开盖窗：图出现了
    timeline.append(("uncovered", time.time()))
    got = pump_until(root, lambda: len(clicks) > 0, timeout=15)
    thread.join(timeout=8)
    waited = time.time() - started
    ok = got and waited >= 1.2, \
        f"clicks={clicks} waited={waited:.1f} logs={box['logs'][-3:]}"
    if os.environ.get("E2E_TIMELINE"):
        base = timeline[0][1]
        print("  DEBUG  timeline:")
        for name, ts in timeline:
            print(f"    {ts - base:7.3f}s  {name}")
        for level, msg, ts in box["logs"]:
            print(f"    {ts - base:7.3f}s  log[{level}] {msg}")
    print("  PASS  等待图片出现后再点击（等了 %.1f 秒）" % waited if ok[0]
          else f"  FAIL  等待识图未生效（{ok[1]}）")
    if not ok[0]:
        failures.append("等待识图")
        # --- 诊断：时间线 + 引擎点击瞬间的鼠标/命中情况 ---
        base = timeline[0][1]
        print("  DEBUG  timeline:")
        for name, ts in timeline:
            print(f"    {ts - base:7.3f}s  {name}")
        for level, msg, ts in box["logs"]:
            print(f"    {ts - base:7.3f}s  log[{level}] {msg}")
        try:
            import pyautogui as _pa

            pos = _pa.position()
            print("  DEBUG  跑完鼠标位置:", pos.x, pos.y,
                  "winfo_containing:", root.winfo_containing(pos.x, pos.y))
            print("  DEBUG  canvas geom:", canvas.winfo_rootx(),
                  canvas.winfo_rooty(), canvas.winfo_width(),
                  canvas.winfo_height(), "mapped:", root.winfo_ismapped())
            print("  DEBUG  main locate:", _pa.locateCenterOnScreen(
                os.path.join(TMP, "canvas.png"), confidence=0.9))
        except Exception as exc:  # noqa: BLE001
            print("  DEBUG  diag exc:", type(exc).__name__, exc)
        from PIL import ImageGrab as _IG

        _IG.grab().save(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "_e2e_fail.png"))
        # --- 诊断结束 ---

    root.destroy()
    print()
    if failures:
        print("端到端测试失败：" + "，".join(failures))
        return 1
    print("端到端测试全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
