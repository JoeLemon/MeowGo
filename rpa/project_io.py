# -*- coding: utf-8 -*-
"""项目文件（.mgproj）：把执行组单独存成可分享、可切换的流程文件。

状态文件 ``rpa_settings.json`` 仍然保存界面偏好与最近打开的项目；
项目文件只关心流程本身（执行组 + 图片目录），方便多套流程并存。
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

from .engine import Project

PROJECT_EXT = ".mgproj"
PROJECT_VERSION = 1
RECENT_LIMIT = 8
FILE_TYPES = (("MeowGo 项目", "*" + PROJECT_EXT), ("所有文件", "*.*"))


def ensure_extension(path: str) -> str:
    """没写扩展名时补上 .mgproj。"""
    path = (path or "").strip()
    if path and not path.lower().endswith(PROJECT_EXT):
        return path + PROJECT_EXT
    return path


def project_payload(project: Project, image_dir: str = "",
                    name: str = "") -> dict:
    """项目文件内容：流程 + 图片目录 + 保存时间。"""
    return {
        "version": PROJECT_VERSION,
        "name": name or "",
        "image_dir": image_dir or project.image_dir,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "groups": [group.to_dict() for group in project.groups],
    }


def save_project(path: str, project: Project, image_dir: str = "",
                 name: str = "") -> str:
    """原子写入项目文件；返回实际路径。"""
    path = ensure_extension(path)
    if not path:
        raise ValueError("项目路径不能为空")
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    payload = json.dumps(project_payload(project, image_dir, name),
                         ensure_ascii=False, indent=2)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path


def load_project(path: str) -> tuple[Project, dict]:
    """读取项目文件，返回 (项目, 元信息)。文件损坏时抛 ValueError。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise ValueError(f"无法读取项目文件：{exc}") from None
    except ValueError as exc:
        raise ValueError(f"项目文件不是合法 JSON：{exc}") from None
    if not isinstance(data, dict):
        raise ValueError("项目文件格式不正确：顶层必须是对象")
    groups_raw = data.get("groups")
    if groups_raw is None and data.get("steps"):
        groups_raw = [{"name": "默认组", "steps": data["steps"]}]
    if not isinstance(groups_raw, list):
        raise ValueError("项目文件格式不正确：缺少 groups 列表")
    project = Project.from_dict({"groups": groups_raw,
                                 "image_dir": data.get("image_dir", "")})
    meta = {
        "name": str(data.get("name") or ""),
        "image_dir": str(data.get("image_dir") or ""),
        "saved_at": str(data.get("saved_at") or ""),
    }
    return project, meta


def recent_projects(settings: Optional[dict]) -> list[str]:
    """设置里记录的最近项目（过滤掉已不存在的文件）。"""
    raw = (settings or {}).get("recent_projects")
    if not isinstance(raw, list):
        return []
    paths = [str(item) for item in raw if str(item).strip()]
    return [path for path in paths if os.path.isfile(path)][:RECENT_LIMIT]


def remember_project(settings: dict, path: str) -> str:
    """把项目放到最近列表首位（去重、限长）；返回实际路径。"""
    path = os.path.abspath(path)
    paths = [item for item in recent_projects(settings)
             if os.path.abspath(item) != path]
    paths.insert(0, path)
    settings["recent_projects"] = paths[:RECENT_LIMIT]
    return path


def project_name(path: str) -> str:
    """界面上显示的项目名（不带扩展名）。"""
    return os.path.splitext(os.path.basename(path or ""))[0]
