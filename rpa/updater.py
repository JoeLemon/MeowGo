# -*- coding: utf-8 -*-
"""检查新版本：从 GitHub Releases 读取最新版本号并与当前版本比较。

只做「查询 + 提示 + 打开下载页」，不自动下载安装，避免在用户不知情时
改动程序目录。仓库地址固定为本项目；可用设置项 ``update_url`` 覆盖。
"""
from __future__ import annotations

import json
import re
from urllib.request import Request, urlopen

RELEASE_URL = "https://api.github.com/repos/JoeLemon/MeowGo/releases/latest"


def parse_version(text: str) -> tuple[int, ...]:
    """把 v2.1.0 / 2.1 / 2.1.0-beta 转成可比较的数字元组。"""
    numbers = re.findall(r"\d+", str(text or ""))
    return tuple(int(item) for item in numbers) or (0,)


def is_newer(current: str, latest: str) -> bool:
    """latest 是否比 current 新（按数字段比较，缺位补 0）。"""
    current_version = parse_version(current)
    latest_version = parse_version(latest)
    length = max(len(current_version), len(latest_version))
    current_version += (0,) * (length - len(current_version))
    latest_version += (0,) * (length - len(latest_version))
    return latest_version > current_version


def fetch_latest(url: str = "", timeout: float = 8) -> dict:
    """读取最新发布信息，返回 {version, url, notes}。

    GitHub API 失败（私有仓库 / 无网络 / 限流）时抛异常，由界面提示。
    """
    request = Request(
        url or RELEASE_URL,
        headers={"User-Agent": "MeowGo-Updater",
                 "Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310
        data = json.loads(response.read().decode("utf-8", "replace"))
    if not isinstance(data, dict):
        raise ValueError("更新信息格式不正确")
    tag = str(data.get("tag_name") or data.get("name") or "").strip()
    if not tag:
        raise ValueError("更新信息里没有版本号")
    return {
        "version": tag.lstrip("vV"),
        "url": str(data.get("html_url") or ""),
        "notes": str(data.get("body") or ""),
    }
