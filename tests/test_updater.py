# -*- coding: utf-8 -*-
"""版本更新检查：版本号比较与发布信息解析。"""
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa import updater  # noqa: E402


def test_parse_and_compare_versions():
    assert updater.parse_version("v2.1.0") == (2, 1, 0)
    assert updater.parse_version("2.1") == (2, 1)
    assert updater.is_newer("2.0.9", "2.1.0")
    assert updater.is_newer("2.1", "2.1.1")
    assert not updater.is_newer("2.1.1", "2.1")
    assert not updater.is_newer("2.1.0", "v2.1.0")
    assert not updater.is_newer("2.2.0", "2.1.9")


def test_fetch_latest_parses_github_response(monkeypatch):
    payload = {"tag_name": "v3.0.0",
               "html_url": "https://github.com/JoeLemon/MeowGo/releases",
               "body": "新版本说明"}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        updater, "urlopen",
        lambda _request, timeout=0: Response(
            json.dumps(payload).encode("utf-8")))
    info = updater.fetch_latest()
    assert info["version"] == "3.0.0"
    assert info["url"].endswith("/releases")
    assert info["notes"] == "新版本说明"


def test_fetch_latest_rejects_bad_payload(monkeypatch):
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        updater, "urlopen",
        lambda _request, timeout=0: Response(b"[]"))
    with pytest.raises(ValueError):
        updater.fetch_latest()
