# -*- coding: utf-8 -*-
"""可选 UI Automation 控件定位。

Windows 用 uiautomation 包按控件 Name / ControlType 查找；
macOS 用系统自带的 AXUIElement（ApplicationServices）遍历前台应用的
辅助功能树。两者都未安装 / 未授权时给出明确提示。
"""
from __future__ import annotations

import os
import time

from . import platform_support

_UIA_WIN_HINT = (
    "未安装 UIA 组件，控件操作步骤无法运行。请执行：pip install uiautomation")
_UIA_MAC_DEP_HINT = (
    "未安装 macOS 控件访问组件，控件操作步骤无法运行。请执行："
    "pip install pyobjc-framework-ApplicationServices")
_UIA_MAC_AUTH_HINT = (
    "未获得 macOS「辅助功能」权限，控件点击无法运行。请在 系统设置 → "
    "隐私与安全性 → 辅助功能 中勾选运行 MeowGo 的终端 / Python，"
    "然后重新启动 MeowGo。")
UIA_INSTALL_HINT = _UIA_MAC_AUTH_HINT if platform_support.IS_MACOS \
    else _UIA_WIN_HINT

# 用户在步骤里写的控件类型（中英文）→ AXRole 去掉 AX 前缀后的写法
_ROLE_ALIASES = {
    "按钮": "button", "复选框": "checkbox", "单选框": "radio",
    "文本框": "textfield", "输入框": "textfield", "菜单项": "menuitem",
    "菜单": "menubar", "链接": "link", "标签": "statictext",
    "窗口": "window", "列表": "list", "表格": "table", "单元格": "cell",
    "下拉框": "popupbutton", "组合框": "combobox", "滑块": "slider",
}


class UiaUnavailable(RuntimeError):
    """UIA 依赖缺失 / 权限不足。"""


# --------------------------------------------------------------------- 对外
def available() -> bool:
    if platform_support.IS_MACOS:
        module = _mac_module_or_none()
        if module is None:
            return False
        try:
            return bool(module.AXIsProcessTrusted())
        except Exception:  # noqa: BLE001
            return False
    try:
        import uiautomation  # noqa: F401
    except Exception:  # noqa: BLE001 - 缺失 / COM 初始化失败都按不可用处理
        return False
    return True


def find_control(name: str, control_type: str = "", timeout: float = 0.5):
    """按控件名称（可选再按类型）找控件；找不到返回 None。"""
    if platform_support.IS_MACOS:
        return _mac_find_control(name, control_type, timeout)
    uia = _module()
    name = (name or "").strip()
    if not name:
        return None
    kwargs = {"Name": name}
    control_type = (control_type or "").strip()
    if control_type:
        kwargs["ControlTypeName"] = control_type
    try:
        control = uia.Control(**kwargs)
        if control.Exists(maxSearchSeconds=max(0.0, timeout)):
            return control
    except Exception as exc:  # noqa: BLE001 - 控件树异常按没找到处理
        raise UiaUnavailable(f"{UIA_INSTALL_HINT}\n（引擎返回：{exc}）") from exc
    return None


def click_control(name: str, control_type: str = "",
                  timeout: float = 0.5) -> bool:
    if platform_support.IS_MACOS:
        return _mac_click_control(name, control_type, timeout)
    control = find_control(name, control_type, timeout)
    if control is None:
        return False
    control.Click()
    return True


# ----------------------------------------------------------------- Windows
def _module():
    try:
        import uiautomation
    except Exception as exc:  # noqa: BLE001
        raise UiaUnavailable(UIA_INSTALL_HINT) from exc
    return uiautomation


# --------------------------------------------------------------------- macOS
def _mac_module_or_none():
    try:
        import ApplicationServices as module
    except Exception:  # noqa: BLE001 - 缺 pyobjc 时按不可用处理
        return None
    return module


def _mac_module():
    module = _mac_module_or_none()
    if module is None:
        raise UiaUnavailable(_UIA_MAC_DEP_HINT)
    return module


def _mac_attr(module, element, name):
    try:
        err, value = module.AXUIElementCopyAttributeValue(element, name, None)
    except Exception:  # noqa: BLE001
        return None
    return value if err == 0 else None


def _mac_role(module, element) -> str:
    return _normalize_role(_mac_attr(module, element, module.kAXRoleAttribute))


def _normalize_role(text: object) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    alias = _ROLE_ALIASES.get(raw, raw)
    if alias.lower().startswith("ax"):
        alias = alias[2:]
    return alias.casefold()


def _mac_text(value: object) -> str:
    return value.strip().casefold() if isinstance(value, str) else ""


def _mac_element_matches(module, element, target: str, role: str) -> bool:
    if role and _mac_role(module, element) != role:
        return False
    for attribute in (module.kAXTitleAttribute, module.kAXDescriptionAttribute,
                      module.kAXValueAttribute):
        if _mac_text(_mac_attr(module, element, attribute)) == target:
            return True
    return False


def _mac_is_element(module, value) -> bool:
    """只接受真正的 AXUIElement，避免把异常对象继续喂给辅助功能 API。"""
    try:
        from CoreFoundation import CFGetTypeID

        return int(CFGetTypeID(value)) == int(module.AXUIElementGetTypeID())
    except Exception:  # noqa: BLE001 - 类型信息取不到时保持旧行为
        return True


def _mac_children(module, element):
    value = _mac_attr(module, element, module.kAXChildrenAttribute)
    if not value:
        return []
    try:
        children = list(value)
    except TypeError:
        return []
    return [child for child in children if _mac_is_element(module, child)]


def _mac_search(module, root, target: str, role: str):
    """广度优先遍历辅助功能树；限制深度与节点数，避免卡在超大应用里。"""
    stack = [(root, 0)]
    visited = 0
    while stack and visited < 2500:
        element, depth = stack.pop()
        visited += 1
        if _mac_element_matches(module, element, target, role):
            return element
        if depth >= 8:
            continue
        children = _mac_children(module, element)
        for child in reversed(children[:200]):
            stack.append((child, depth + 1))
    return None


def _mac_application_roots(module, app):
    """应用的搜索入口：应用本身 + 菜单栏 + 窗口（菜单栏常不在 Children 里）。"""
    roots = [app]
    menu_bar = _mac_attr(module, app, module.kAXMenuBarAttribute)
    if menu_bar is not None and _mac_is_element(module, menu_bar):
        roots.append(menu_bar)
    windows = _mac_attr(module, app, module.kAXWindowsAttribute)
    if windows:
        try:
            roots.extend(child for child in list(windows)
                         if _mac_is_element(module, child))
        except TypeError:
            pass
    focused_window = _mac_attr(module, app, module.kAXFocusedWindowAttribute)
    if focused_window is not None and _mac_is_element(module, focused_window):
        roots.append(focused_window)
    return roots


def _mac_frontmost_is_self() -> bool:
    """MeowGo 自己在前台时不遍历自家 Tk 应用的辅助功能树。

    Tk 窗口的 AX 树不完整，强行遍历既找不到可用控件，也可能触发原生异常。
    正常运行前主窗会被最小化，目标应用才是前台。
    """
    try:
        from AppKit import NSWorkspace

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return bool(app and app.processIdentifier() == os.getpid())
    except Exception:  # noqa: BLE001
        return False


def _mac_application_pid(module, app) -> int:
    """取 AX 应用的进程号；取不到返回 -1。"""
    try:
        err, pid = module.AXUIElementGetPid(app, None)
        return int(pid) if err == 0 else -1
    except Exception:  # noqa: BLE001
        return -1


def _mac_focused_application(module):
    system = module.AXUIElementCreateSystemWide()
    app = _mac_attr(module, system, module.kAXFocusedApplicationAttribute)
    if app is None:
        try:
            from AppKit import NSWorkspace

            running = NSWorkspace.sharedWorkspace().frontmostApplication()
            if running is not None:
                app = module.AXUIElementCreateApplication(
                    running.processIdentifier())
        except Exception:  # noqa: BLE001
            app = None
    if app is None:
        return None
    pid = _mac_application_pid(module, app)
    if pid == os.getpid() or (pid < 0 and _mac_frontmost_is_self()):
        # 前台是 MeowGo 自己：Tk 的 AX 树不完整，跳过以免原生异常
        return None
    return app


def _mac_find_control(name: str, control_type: str, timeout: float):
    module = _mac_module()
    if not _mac_trusted(module):
        raise UiaUnavailable(UIA_INSTALL_HINT)
    target = (name or "").strip().casefold()
    if not target:
        return None
    role = _normalize_role(control_type)
    deadline = time.monotonic() + max(0.0, float(timeout or 0))
    while True:
        app = _mac_focused_application(module)
        if app is not None:
            for root in _mac_application_roots(module, app):
                found = _mac_search(module, root, target, role)
                if found is not None:
                    return found
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(0.1, remaining))


def _mac_trusted(module) -> bool:
    try:
        return bool(module.AXIsProcessTrusted())
    except Exception:  # noqa: BLE001
        return False


def _mac_click_control(name: str, control_type: str, timeout: float) -> bool:
    control = _mac_find_control(name, control_type, timeout)
    if control is None:
        return False
    module = _mac_module()
    try:
        err = module.AXUIElementPerformAction(control, module.kAXPressAction)
    except Exception:  # noqa: BLE001
        err = -1
    if err == 0:
        return True
    return _mac_click_center(module, control)


def _mac_click_center(module, control) -> bool:
    """AXPress 不可用时，按控件的屏幕位置点一下中心（与 Home/End 等一致）。"""
    position = _mac_attr(module, control, module.kAXPositionAttribute)
    size = _mac_attr(module, control, module.kAXSizeAttribute)
    if position is None or size is None:
        return False
    try:
        ok_point, point = module.AXValueGetValue(
            position, module.kAXValueCGPointType, None)
        ok_size, box = module.AXValueGetValue(
            size, module.kAXValueCGSizeType, None)
        if not (ok_point and ok_size) or box.width <= 0 or box.height <= 0:
            return False
        import pyautogui

        pyautogui.click(int(round(point.x + box.width / 2)),
                        int(round(point.y + box.height / 2)))
        return True
    except Exception:  # noqa: BLE001 - 位置信息缺失 / 点击被 failsafe 拦下
        return False
