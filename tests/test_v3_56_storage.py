"""Tests for v3.56.0 copilot_storage."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_new_thread_has_unique_id():
    from trader.copilot_storage import new_thread
    a = new_thread()
    b = new_thread()
    assert a.id != b.id
    assert len(a.id) > 10
    assert a.title == "(new chat)"
    assert a.created_at
    assert a.messages == []


def test_auto_title_truncates_long_text():
    from trader.copilot_storage import auto_title
    short = "Why am I down today?"
    assert auto_title(short) == short
    long = "I am wondering if you can please explain in great detail why my portfolio is currently underperforming the SPY benchmark by 50 basis points"
    titled = auto_title(long)
    assert len(titled) <= 51  # 50 + ellipsis
    assert titled.endswith("…")


def test_auto_title_handles_empty():
    from trader.copilot_storage import auto_title
    assert auto_title("") == "(empty)"
    assert auto_title("   ") == "(empty)"


def test_auto_title_strips_leading_punctuation():
    from trader.copilot_storage import auto_title
    # Leading non-word chars should not bleed into title
    assert auto_title("🤖 hello there") == "hello there"


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    from trader import copilot_storage
    monkeypatch.setattr(copilot_storage, "CHATS_DIR", tmp_path / "chats")
    t = copilot_storage.new_thread()
    t.messages = [
        {"role": "user", "display_text": "hi", "content": "hi"},
        {"role": "assistant", "display_text": "hello", "api_content": []},
    ]
    p = copilot_storage.save_thread(t)
    assert p.exists()
    loaded = copilot_storage.load_thread(t.id)
    assert loaded is not None
    assert loaded.id == t.id
    assert len(loaded.messages) == 2
    assert loaded.messages[0]["content"] == "hi"


def test_save_auto_titles_from_first_message(tmp_path, monkeypatch):
    from trader import copilot_storage
    monkeypatch.setattr(copilot_storage, "CHATS_DIR", tmp_path / "chats")
    t = copilot_storage.new_thread()
    t.messages = [{"role": "user", "display_text": "Why am I down today?", "content": "x"}]
    copilot_storage.save_thread(t)
    loaded = copilot_storage.load_thread(t.id)
    assert "Why am I down today" in loaded.title


def test_list_threads_sorted_newest_first(tmp_path, monkeypatch):
    from trader import copilot_storage
    monkeypatch.setattr(copilot_storage, "CHATS_DIR", tmp_path / "chats")
    import time
    t1 = copilot_storage.new_thread()
    t1.title = "first"
    copilot_storage.save_thread(t1)
    time.sleep(0.01)
    t2 = copilot_storage.new_thread()
    t2.title = "second"
    copilot_storage.save_thread(t2)
    time.sleep(0.01)
    t3 = copilot_storage.new_thread()
    t3.title = "third"
    copilot_storage.save_thread(t3)
    threads = copilot_storage.list_threads()
    assert len(threads) == 3
    assert threads[0].title == "third"
    assert threads[2].title == "first"


def test_delete_thread_removes_file(tmp_path, monkeypatch):
    from trader import copilot_storage
    monkeypatch.setattr(copilot_storage, "CHATS_DIR", tmp_path / "chats")
    t = copilot_storage.new_thread()
    copilot_storage.save_thread(t)
    assert copilot_storage.delete_thread(t.id) is True
    assert copilot_storage.load_thread(t.id) is None
    # Deleting again returns False
    assert copilot_storage.delete_thread(t.id) is False


def test_load_nonexistent_returns_none(tmp_path, monkeypatch):
    from trader import copilot_storage
    monkeypatch.setattr(copilot_storage, "CHATS_DIR", tmp_path / "chats")
    assert copilot_storage.load_thread("nonexistent-id") is None


def test_path_for_sanitizes_thread_id():
    from trader.copilot_storage import _path_for, CHATS_DIR
    # Must not allow path traversal — output must stay inside CHATS_DIR
    p = _path_for("../../etc/passwd")
    # The slashes get stripped, leaving alphanumeric chars only.
    # Critical: result must be within CHATS_DIR (no escape via .. or /).
    assert str(p.parent).rstrip("/") == str(CHATS_DIR).rstrip("/")
    assert "/" not in p.name  # filename has no slashes
    assert ".." not in p.name  # no parent-traversal in filename
    # Must produce a .json file
    p2 = _path_for("abc-def-123")
    assert str(p2).endswith(".json")
    assert p2.name == "abc-def-123.json"
