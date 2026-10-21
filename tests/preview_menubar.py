# -*- coding: utf-8 -*-
"""菜单栏下拉的外观与交互自检：真实弹出菜单、真实鼠标点别处关闭。

生成：tests/menubar_preview.png
"""
import ctypes
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa import gui as gui_module  # noqa: E402
from rpa.engine import Group, Step  # noqa: E402

gui_module.SETTINGS_PATH = os.path.join(tempfile.mkdtemp(), "settings.json")
gui_module.enable_dpi_awareness()

app = gui_module.App()
app.project.groups = [
    Group("登录", [Step("click", "登录.png"), Step("input", "admin")]),
    Group("主流程", [Step("call", "登录"), Step("wait", "0.5")]),
]
app._refresh_all(select_group=1)

results = []


def check(name, condition):
    print(("  PASS  " if condition else "  FAIL  ") + name)
    results.append(bool(condition))


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.02)


def find_menu():
    for child in app.winfo_children():
        if isinstance(child, gui_module.uikit.ContextMenu):
            return child
    return None


def left_click(x, y):
    user32 = ctypes.windll.user32
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.08)
    user32.mouse_event(0x0002, 0, 0, 0, 0)      # LEFTDOWN
    time.sleep(0.06)
    user32.mouse_event(0x0004, 0, 0, 0, 0)      # LEFTUP


def right_click(x, y):
    user32 = ctypes.windll.user32
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.08)
    user32.mouse_event(0x0008, 0, 0, 0, 0)      # RIGHTDOWN
    time.sleep(0.06)
    user32.mouse_event(0x0010, 0, 0, 0, 0)      # RIGHTUP


app.update()
app.lift()
app.attributes("-topmost", True)
pump(0.4)

# 1) 打开「运行」下拉：按钮高亮 + 菜单出现（自绘，风格与右键一致）
app._open_top_menu("运行")
pump(0.35)
menu = find_menu()
check("菜单栏下拉出现", menu is not None)
check("展开时按钮高亮", app._top_buttons["运行"].kind == "solid")
labels = [item.label for item in menu.items] if menu else []
check("含运行相关项", any("运行当前组" == text for text in labels), )
check("含快捷键提示", any(item.shortcut for item in menu.items))

from PIL import ImageGrab  # noqa: E402

path = os.path.join(os.path.dirname(__file__), "menubar_preview.png")
x, y = app.winfo_rootx(), app.winfo_rooty()
box = (x, y, x + app.winfo_width(), y + min(app.winfo_height(), app.s(300)))
pump(1.2)                       # 等界面画完再截图，避免抓到重绘中的画面
ImageGrab.grab(bbox=box).save(path)
print("  INFO  截图已保存 tests/menubar_preview.png")

# 2) 在窗口里的别处按左键 → 收起（真实鼠标事件，走 grab 路径）
left_click(app.winfo_rootx() + app.winfo_width() // 2,
           app.winfo_rooty() + app.winfo_height() - app.s(40))
pump(0.6)
check("窗口内别处左键关闭", find_menu() is None)
check("关闭后按钮恢复常态", app._top_buttons["运行"].kind == "ghost")

# 3) 点在别处（轮询机制，覆盖点到别的程序窗口的情况）
app._open_top_menu("视图")
pump(0.3)
menu = find_menu()
check("再次打开菜单", menu is not None)
if menu is not None:
    menu._armed = True
    menu._key_state = staticmethod(lambda: (0x8000, 0))  # 模拟：此刻左键正按着
    menu._inside = lambda: False
    menu._watch()
check("点其它地方（轮询）也会关闭", find_menu() is None)

# 3b) 真·点别的程序窗口（另起一个进程开窗口，鼠标真点过去）
import ctypes.wintypes as wintypes  # noqa: E402
import subprocess  # noqa: E402

SIBLING_TITLE = "MeowGo 邻居窗口"
sibling_code = (
    "import tkinter as tk\n"
    "r = tk.Tk()\n"
    f"r.title({SIBLING_TITLE!r})\n"
    "r.geometry('360x240+2150+60')\n"
    "tk.Label(r, text='别的程序窗口', font=('Microsoft YaHei UI', 16)).pack(expand=True)\n"
    "r.after(30000, r.destroy)\n"
    "r.mainloop()\n"
)
sibling = subprocess.Popen([sys.executable, "-c", sibling_code])

user32 = ctypes.windll.user32
EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
handles: list[int] = []


def _enum(hwnd, _lparam):
    length = user32.GetWindowTextLengthW(hwnd)
    if length:
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if SIBLING_TITLE in buffer.value and user32.IsWindowVisible(hwnd):
            handles.append(hwnd)
    return True


_enum_ref = EnumProc(_enum)
deadline = time.time() + 20
while time.time() < deadline and not handles:
    handles.clear()
    user32.EnumWindows(_enum_ref, 0)
    if not handles:
        time.sleep(0.3)

if handles:
    rect = wintypes.RECT()
    user32.GetWindowRect(handles[0], ctypes.byref(rect))
    app._open_top_menu("文件")
    pump(0.4)
    menu = find_menu()
    check("打开「文件」菜单", menu is not None)
    left_click((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
    pump(0.8)
    check("点别的程序窗口也会关闭", find_menu() is None)
    check("邻居窗口仍在（没被误关）", sibling.poll() is None)
else:
    check("邻居窗口出现", False)

sibling.terminate()
sibling.wait(timeout=10)

# 3c) 真·菜单外右键：收起原菜单，并就地弹出新的（右键菜单→右键菜单）
app._open_top_menu("文件")
pump(0.4)
old_menu = find_menu()
check("打开「文件」下拉", old_menu is not None)
sx = app.step_tree.winfo_rootx() + app.step_tree.winfo_width() // 2
sy = app.step_tree.winfo_rooty() + app.step_tree.winfo_height() // 2
right_click(sx, sy)
pump(0.7)
new_menu = find_menu()
check("菜单外右键后旧菜单已收起",
      new_menu is not None and new_menu is not old_menu)
check("就地弹出的是步骤菜单",
      new_menu is not None and new_menu.items[0].label.startswith("新增步骤"),
      )
check("旧菜单关闭后菜单栏按钮恢复",
      app._top_buttons["文件"].kind == "ghost")
# 第一次右键（打开菜单的那次）不该被自己吃掉：菜单还得在
check("新菜单没有被同一次右键误关",
      new_menu is not None and new_menu.winfo_exists())

# 3d) 空白处（没有右键菜单的地方）按右键：只收起
if new_menu is not None:
    hx = app.winfo_rootx() + app.s(120)
    hy = app.winfo_rooty() + app.s(12)
    right_click(hx, hy)
    pump(0.7)
    check("空白处右键只收起菜单", find_menu() is None)

# 3e) 轮询那条路也要能换菜单（点在菜单外时抓取不一定收得到）
app._open_top_menu("视图")
pump(0.35)
polled = find_menu()
if polled is not None:
    polled._armed = True
    polled._key_state = staticmethod(lambda: (0, 0x8000))   # 此刻右键正按着
    polled._inside = lambda: False
    polled._pointer_xy = lambda: (sx, sy)
    polled._watch()
    pump(0.25)
    after_poll = find_menu()
    check("轮询发现菜单外右键也会换菜单",
          after_poll is not None and after_poll is not polled)
    if after_poll is not None:
        after_poll.close()

# 4) 组右键菜单里可以直接运行这个组
row = app.group_tree.bbox(app.group_tree.get_children()[0])
captured = {}
original = gui_module.uikit.ContextMenu.popup


def fake_popup(self, x, y):
    captured["menu"] = self
    return self


gui_module.uikit.ContextMenu.popup = fake_popup
try:
    class Event:
        def __init__(self, y, x_root, y_root):
            self.y, self.x_root, self.y_root = y, x_root, y_root

    app._group_menu(Event(row[1] + row[3] // 2, 100, 100))
finally:
    gui_module.uikit.ContextMenu.popup = original
group_labels = [item.label for item in captured["menu"].items]
check("组右键可运行这个组", group_labels[0] == "运行这个组")
captured["menu"].close()

# 5) 窗口拖到副屏（主屏左边 = 负坐标）时，右键菜单要弹在副屏上
#    以前坐标被 max(0, ..) 拽回主屏，看起来就像“菜单没弹出来”
user32 = ctypes.windll.user32
left_edge = user32.GetSystemMetrics(76)      # SM_XVIRTUALSCREEN
virtual_top = user32.GetSystemMetrics(77)    # SM_YVIRTUALSCREEN
if left_edge < 0:
    app.geometry(f"{app.s(900)}x{app.s(620)}+{left_edge + 60}+{virtual_top + 120}")
    pump(0.8)
    tx = app.step_tree.winfo_rootx() + app.step_tree.winfo_width() // 2
    ty = app.step_tree.winfo_rooty() + app.step_tree.winfo_height() // 2
    check("测试窗口已到副屏（负坐标）", app.winfo_rootx() < 0,
          )
    right_click(tx, ty)
    pump(0.8)
    side_menu = find_menu()
    check("副屏上右键菜单能弹出", side_menu is not None)
    if side_menu is not None:
        mx, my = side_menu.winfo_rootx(), side_menu.winfo_rooty()
        check("菜单留在副屏（没被拽回主屏）", mx < 0)
        check("菜单就在点击位置", abs(mx - tx) <= app.s(12)
              and abs(my - ty) <= app.s(12))
        check("菜单完整落在副屏内",
              mx + side_menu._width <= 0 and my >= virtual_top)
        side_menu.close()
    app.geometry(f"+{app.s(40)}+{app.s(40)}")      # 挪回主屏，便于看预览图
    pump(0.5)
else:
    print("  INFO  只有一块屏幕，跳过副屏菜单检查")

app.destroy()
print(f"\n{sum(results)}/{len(results)} 通过")
sys.exit(0 if all(results) else 1)
