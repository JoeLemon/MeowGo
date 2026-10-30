# -*- coding: utf-8 -*-
"""可选 UI Automation 控件定位（uiautomation 包）。

和图片识别相比，UIA 直接读控件树：窗口缩放、换主题、换分辨率都不会
失效，也能拿到按钮的真实名称。未安装依赖时给出明确提示。
"""
from __future__ import annotations

UIA_INSTALL_HINT = (
    "未安装 UIA 组件，控件操作步骤无法运行。请执行：pip install uiautomation")


class UiaUnavailable(RuntimeError):
    """UIA 依赖缺失。"""


def available() -> bool:
    try:
        import uiautomation  # noqa: F401
    except Exception:  # noqa: BLE001 - 缺失 / COM 初始化失败都按不可用处理
        return False
    return True


def _module():
    try:
        import uiautomation
    except Exception as exc:  # noqa: BLE001
        raise UiaUnavailable(UIA_INSTALL_HINT) from exc
    return uiautomation


def find_control(name: str, control_type: str = "", timeout: float = 0.5):
    """按 UIA Name（可选再按控件类型）找控件；找不到返回 None。"""
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
    control = find_control(name, control_type, timeout)
    if control is None:
        return False
    control.Click()
    return True
