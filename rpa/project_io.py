# -*- coding: utf-8 -*-
"""项目文件（.mgproj）：把执行组单独存成可分享、可切换的流程文件。

状态文件 ``rpa_settings.json`` 仍然保存界面偏好与最近打开的项目；
项目文件只关心流程本身（执行组 + 图片目录），方便多套流程并存。
"""
from __future__ import annotations

import json
import os
import time
import zipfile
from typing import Optional

from . import assets
from .engine import Project, image_references

PROJECT_EXT = ".mgproj"
PROJECT_VERSION = 1
RECENT_LIMIT = 8
FILE_TYPES = (("MeowGo 项目", "*" + PROJECT_EXT), ("所有文件", "*.*"))
# 可分享包：zip 里放 project.mgproj + images/，换机器/发给同事都能直接跑
SHARE_EXT = ".mgzip"
SHARE_FILE_TYPES = (("MeowGo 可分享包", "*" + SHARE_EXT), ("所有文件", "*.*"))
SHARE_PROJECT_NAME = "project" + PROJECT_EXT
SHARE_IMAGE_DIR = "images"


def ensure_extension(path: str) -> str:
    """没写扩展名时补上 .mgproj。"""
    path = (path or "").strip()
    if path and not path.lower().endswith(PROJECT_EXT):
        return path + PROJECT_EXT
    return path


def ensure_share_extension(path: str) -> str:
    """没写扩展名时补上 .mgzip。"""
    path = (path or "").strip()
    if path and not path.lower().endswith(SHARE_EXT):
        return path + SHARE_EXT
    return path


def portable_image_dir(image_dir: str, project_path: str) -> str:
    """把图片目录写成「能跟着项目文件一起搬走」的形式。

    在项目目录内（或同级）就存相对路径，否则仍存绝对路径。
    否则把 .mgproj 发给同事后，里面的绝对路径在他的机器上根本不存在。
    """
    if not image_dir:
        return ""
    base = os.path.dirname(os.path.abspath(project_path or "."))
    target = os.path.abspath(image_dir)
    try:
        relative = os.path.relpath(target, base)
    except ValueError:          # Windows：跨盘符没有相对路径
        return target
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return target
    return relative.replace(os.sep, "/")


def resolve_image_dir(value: str, project_path: str) -> str:
    """项目里记的图片目录 → 本机可用目录；都找不到返回空串。

    回退顺序：记录值（绝对 / 相对项目文件） → 项目同级 images/。
    """
    base = os.path.dirname(os.path.abspath(project_path or "."))
    candidates: list[str] = []
    recorded = (value or "").strip()
    if recorded:
        candidates.append(recorded if os.path.isabs(recorded)
                          else os.path.join(base, recorded))
    candidates.append(os.path.join(base, SHARE_IMAGE_DIR))
    for candidate in candidates:
        if os.path.isdir(candidate):
            return os.path.abspath(candidate)
    return ""


def project_payload(project: Project, image_dir: str = "",
                    name: str = "", path: str = "") -> dict:
    """项目文件内容：流程 + 图片目录 + 保存时间。"""
    source = image_dir or project.image_dir
    return {
        "version": PROJECT_VERSION,
        "name": name or "",
        "image_dir": (portable_image_dir(source, path) if path else source),
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
    payload = json.dumps(project_payload(project, image_dir, name, path),
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


def export_package(path: str, project: Project, image_dir: str = "",
                   name: str = "") -> dict:
    """把流程 + 它用到的图片打成一个 .mgzip（可直接发给同事）。

    返回 ``{"path", "images", "missing"}``：打包张数与缺失的图片名。
    只打真正被引用的图片——图片目录里翻历史留下的几百张垃圾不该跟着走。
    """
    path = ensure_share_extension(path)
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    payload = project_payload(
        project, os.path.join(folder, SHARE_IMAGE_DIR), name,
        os.path.join(folder, SHARE_PROJECT_NAME))
    written: set[str] = set()
    missing: list[str] = []
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(SHARE_PROJECT_NAME,
                        json.dumps(payload, ensure_ascii=False, indent=2))
        for image_name, _group, _index in image_references(project.groups):
            base = os.path.basename(image_name)
            if base in written:
                continue
            source = assets.resolve_image(image_name,
                                          image_dir or project.image_dir)
            if not os.path.isfile(source):
                missing.append(base)
                continue
            bundle.write(source, f"{SHARE_IMAGE_DIR}/{base}")
            written.add(base)
    os.replace(tmp, path)
    return {"path": path, "images": len(written), "missing": missing}


def import_package(path: str, target_dir: str = "") -> dict:
    """解压 .mgzip 并读取其中的项目。

    返回 ``{"project", "meta", "path", "dir", "images"}``。
    默认解压到「与压缩包同名」的文件夹，避免和已有文件混在一起。
    """
    path = os.path.abspath(path)
    if not zipfile.is_zipfile(path):
        raise ValueError("不是有效的可分享包（.mgzip）")
    folder = target_dir or os.path.join(
        os.path.dirname(path),
        os.path.splitext(os.path.basename(path))[0])
    os.makedirs(folder, exist_ok=True)
    with zipfile.ZipFile(path) as bundle:
        for info in bundle.infolist():
            name = info.filename.replace("\\", "/")
            # 防 zip slip：绝对路径与 .. 一律跳过
            if name.startswith("/") or ".." in name.split("/"):
                continue
            bundle.extract(info, folder)
    project_path = os.path.join(folder, SHARE_PROJECT_NAME)
    if not os.path.isfile(project_path):
        raise ValueError("可分享包里找不到项目文件")
    project, meta = load_project(project_path)
    images = os.path.join(folder, SHARE_IMAGE_DIR)
    return {"project": project, "meta": meta, "path": project_path,
            "dir": folder,
            "images": len(os.listdir(images)) if os.path.isdir(images) else 0}


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
