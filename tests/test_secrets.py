# -*- coding: utf-8 -*-
"""敏感配置加密存储：保存时移出明文，读取时回填。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpa import secrets  # noqa: E402


def test_protect_and_restore(monkeypatch):
    fake: dict[str, str] = {}
    monkeypatch.setattr(secrets, "store",
                        lambda key, value: fake.__setitem__(key, value) or True)
    monkeypatch.setattr(secrets, "load", lambda key: fake.get(key, ""))
    settings = {"webhook": "https://example.com/hook?token=abc",
                "theme": "dark", "token": "abc123"}
    protected = secrets.protect_settings(settings)
    assert protected["webhook"] == ""
    assert protected["token"] == ""
    assert protected["webhook_stored"] == secrets.MARKER
    assert protected["token_stored"] == secrets.MARKER
    assert protected["theme"] == "dark"

    restored = secrets.restore_settings(protected)
    assert restored["webhook"] == settings["webhook"]
    assert restored["token"] == "abc123"
    assert restored["theme"] == "dark"


def test_protect_keeps_plaintext_when_store_unavailable(monkeypatch):
    monkeypatch.setattr(secrets, "store", lambda key, value: False)
    protected = secrets.protect_settings({"webhook": "https://x"})
    assert protected["webhook"] == "https://x"
    assert "webhook_stored" not in protected


def test_clear_marker_deletes_secret(monkeypatch):
    deleted: list[str] = []
    monkeypatch.setattr(secrets, "delete", deleted.append)
    data = {"webhook": "", "webhook_stored": secrets.MARKER}
    cleaned = secrets.clear_marker(data)
    assert deleted == ["webhook"]
    assert "webhook_stored" not in cleaned
