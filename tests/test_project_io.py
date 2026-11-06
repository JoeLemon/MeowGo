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
