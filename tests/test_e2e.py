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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa.engine import Executor, Step  # noqa: E402
from rpa.gui import enable_dpi_awareness as _enable_dpi_awareness  # noqa: E402

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


def run_steps_async(steps):
    executor = Executor()
    executor.base_dir = TMP
    executor.groups = {"自检": steps}
    box = {}

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

    root.destroy()
    print()
    if failures:
        print("端到端测试失败：" + "，".join(failures))
        return 1
    print("端到端测试全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
