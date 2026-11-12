# -*- coding: utf-8 -*-
"""项目文件（.mgproj）：保存 / 读取 / 最近列表。"""
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa import project_io  # noqa: E402
from rpa.engine import Group, Project, Step  # noqa: E402

pytestmark = pytest.mark.engine


def _project() -> Project:
    return Project(groups=[
        Group("登录", [Step("click", "按钮.png"), Step("wait", "1")]),
        Group("主流程", [Step("call", "登录")]),
    ], image_dir="/tmp/imgs")


def test_save_and_load_roundtrip():
    tmp = tempfile.mkdtemp(prefix="mgproj_")
    try:
        path = os.path.join(tmp, "流程")
        saved = project_io.save_project(path, _project())
        assert saved.endswith(".mgproj")
        assert os.path.exists(saved)

        project, meta = project_io.load_project(saved)
        assert project.names() == ["登录", "主流程"]
        assert project.groups[0].steps[0].content == "按钮.png"
        assert project.image_dir == "/tmp/imgs"
        assert meta["image_dir"] == "/tmp/imgs"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_load_legacy_single_group_list():
    tmp = tempfile.mkdtemp(prefix="mgproj_legacy_")
    try:
        path = os.path.join(tmp, "old.mgproj")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"steps": [{"type": "wait", "content": "1"}]}')
        project, _meta = project_io.load_project(path)
        assert project.names() == ["默认组"]
        assert project.groups[0].steps[0].type == "wait"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_load_rejects_broken_file():
    tmp = tempfile.mkdtemp(prefix="mgproj_bad_")
    try:
        path = os.path.join(tmp, "bad.mgproj")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        with pytest.raises(ValueError):
            project_io.load_project(path)
        with pytest.raises(ValueError):
            project_io.load_project(os.path.join(tmp, "missing.mgproj"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_recent_projects_filters_missing_and_dedupes():
    tmp = tempfile.mkdtemp(prefix="mgproj_recent_")
    try:
        first = project_io.save_project(os.path.join(tmp, "a"), _project())
        second = project_io.save_project(os.path.join(tmp, "b"), _project())
        settings: dict = {}
        project_io.remember_project(settings, first)
        project_io.remember_project(settings, second)
        project_io.remember_project(settings, first)
        assert project_io.recent_projects(settings) == [first, second]
        assert project_io.project_name(first) == "a"

        os.remove(second)
        assert project_io.recent_projects(settings) == [first]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_project_stores_relative_image_dir():
    """图片目录在项目旁边时存相对路径：整个文件夹搬走仍然有效。"""
    import json

    tmp = tempfile.mkdtemp(prefix="mgproj_rel_")
    try:
        folder = os.path.join(tmp, "流程")
        images = os.path.join(folder, "images")
        os.makedirs(images)
        project = _project()
        project.image_dir = images
        path = project_io.save_project(os.path.join(folder, "p.mgproj"),
                                       project, images)
        with open(path, encoding="utf-8") as fh:
            assert json.load(fh)["image_dir"] == "images"

        # 模拟换机器 / 换目录：整包搬走，记录的相对路径照样能解析
        moved = os.path.join(tmp, "别人的电脑")
        shutil.copytree(folder, moved)
        moved_path = os.path.join(moved, "p.mgproj")
        _project2, meta = project_io.load_project(moved_path)
        assert meta["image_dir"] == "images"
        assert project_io.resolve_image_dir(
            meta["image_dir"], moved_path) == os.path.abspath(
                os.path.join(moved, "images"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_resolve_image_dir_falls_back_to_sibling_images():
    """记录的目录不存在时，回退到项目同级的 images/ 而不是直接报错。"""
    tmp = tempfile.mkdtemp(prefix="mgproj_fb_")
    try:
        folder = os.path.join(tmp, "proj")
        os.makedirs(os.path.join(folder, "images"))
        path = os.path.join(folder, "p.mgproj")
        assert project_io.resolve_image_dir(
            os.path.join(tmp, "别的机器", "images"),
            path) == os.path.abspath(os.path.join(folder, "images"))

        empty = os.path.join(tmp, "empty")
        os.makedirs(empty)
        assert project_io.resolve_image_dir(
            "", os.path.join(empty, "p.mgproj")) == ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_share_package_roundtrip_carries_used_images():
    """可分享包：只带被引用的图片，解压到别处后图片路径自动对上。"""
    tmp = tempfile.mkdtemp(prefix="mgproj_share_")
    try:
        images = os.path.join(tmp, "src", "images")
        os.makedirs(images)
        for name in ("a.png", "b.png", "没人用的.png"):
            with open(os.path.join(images, name), "wb") as fh:
                fh.write(b"fake-png")
        project = Project(groups=[Group("主", [
            Step("click", "a.png@0,0,10,10"),
            Step("wait_image", "b.png;备用.png"),
            Step("drag", "a.png|b.png"),
        ])], image_dir=images)

        bundle = os.path.join(tmp, "分享", "流程")
        info = project_io.export_package(bundle, project, images)
        assert os.path.isfile(info["path"])
        assert info["path"].endswith(".mgzip")
        assert info["images"] == 2, "只打包 a.png / b.png"
        assert info["missing"] == ["备用.png"]

        loaded = project_io.import_package(info["path"])
        assert loaded["images"] == 2
        assert loaded["project"].groups[0].steps[0].content == \
            "a.png@0,0,10,10"
        assert project_io.resolve_image_dir(
            loaded["meta"]["image_dir"],
            loaded["path"]) == os.path.abspath(
                os.path.join(loaded["dir"], "images"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_import_package_rejects_non_zip():
    tmp = tempfile.mkdtemp(prefix="mgproj_badzip_")
    try:
        path = os.path.join(tmp, "假包.mgzip")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("not a zip")
        with pytest.raises(ValueError):
            project_io.import_package(path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
