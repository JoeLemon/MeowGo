# -*- coding: utf-8 -*-
"""诊断信息导出：敏感字段打码、配置摘要、日志打包。"""
import json
import os
import zipfile

from rpa import diagnostics


def test_mask_secret():
    masked = diagnostics.mask_secret(
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc123")
    assert "abc123" not in masked
    assert masked == "https://qyapi.weixin.qq.com/***"
    assert diagnostics.mask_secret("") == ""
    assert "secret" not in diagnostics.mask_secret("supersecret")


def test_sanitize_settings_drops_project_data():
    clean = diagnostics.sanitize_settings({
        "theme": "dark",
        "webhook": "https://example.com/hook?token=abc",
        "groups": [{"name": "g", "steps": [{"type": "wait"}]}],
        "nested": {"api_key": "abcdefghijk"},
    })
    assert clean["theme"] == "dark"
    assert "groups" not in clean
    assert "abc" not in clean["webhook"]
    assert "abcdefghijk" not in clean["nested"]["api_key"]


def test_collect_and_write_zip(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text("hello app log\n", encoding="utf-8")
    (log_dir / "crash.log").write_text("no crash\n", encoding="utf-8")
    (log_dir / "run_20260101_120000.log").write_text(
        "run detail\n", encoding="utf-8")
    settings = {
        "theme": "dark",
        "webhook": "https://example.com/hook?key=abc123",
        "groups": [{"name": "g", "steps": [
            {"type": "wait", "content": "1"},
            {"type": "wait", "content": "2", "enabled": False},
        ]}],
    }
    text = diagnostics.collect(
        settings, app_dir=str(tmp_path), image_dir=str(tmp_path / "images"),
        report_dir=str(tmp_path / "reports"), log_dir=str(log_dir),
        version="9.9.9")
    assert "9.9.9" in text
    assert "执行组: 1，步骤: 2（禁用 1）" in text
    assert "hello app log" in text
    assert "abc123" not in text

    path = tmp_path / "diag.zip"
    diagnostics.write_zip(
        str(path), settings, app_dir=str(tmp_path),
        image_dir=str(tmp_path / "images"),
        report_dir=str(tmp_path / "reports"), log_dir=str(log_dir),
        version="9.9.9")
    assert os.path.isfile(path)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        assert "diagnostics.txt" in names
        assert "logs/app.log" in names
        assert "logs/crash.log" in names
        assert "logs/run_20260101_120000.log" in names
        sanitized = json.loads(
            archive.read("settings.sanitized.json").decode("utf-8"))
        assert sanitized["theme"] == "dark"
        assert "groups" not in sanitized
        assert "abc123" not in json.dumps(sanitized, ensure_ascii=False)
