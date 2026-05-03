"""Tests for v3.57.1 copilot_memory + workflow builder."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def isolated_mem(tmp_path, monkeypatch):
    """Redirect copilot_memory's file paths to a tmp dir for hermetic tests."""
    import trader.copilot_memory as mem
    monkeypatch.setattr(mem, "MEMORY_FILE", tmp_path / "copilot_memory.md")
    monkeypatch.setattr(mem, "WORKFLOWS_FILE", tmp_path / "copilot_workflows.json")
    return mem


def test_default_memory_is_nonempty(isolated_mem):
    mem = isolated_mem
    assert "Copilot memory" in mem.DEFAULT_MEMORY
    assert "User preferences" in mem.DEFAULT_MEMORY
    # read_memory falls back to default when file missing
    text = mem.read_memory()
    assert isinstance(text, str)
    assert len(text) > 100


def test_write_memory_round_trip(isolated_mem):
    mem = isolated_mem
    test_text = "# my custom memory\n\n- foo\n- bar\n"
    assert mem.write_memory(test_text) is True
    assert mem.read_memory() == test_text


def test_default_workflows_have_required_fields():
    from trader.copilot_memory import DEFAULT_WORKFLOWS
    assert len(DEFAULT_WORKFLOWS) >= 3
    for w in DEFAULT_WORKFLOWS:
        assert "name" in w
        assert "prompts" in w
        assert isinstance(w["prompts"], list)
        assert all(isinstance(p, str) and p.strip() for p in w["prompts"])


def test_workflow_add_and_delete(isolated_mem):
    mem = isolated_mem
    # First call seeds defaults
    initial = mem.list_workflows()
    n_initial = len(initial)
    assert n_initial >= 3

    # Add one
    assert mem.add_workflow("test-flow", ["What's the price of SPY?"]) is True
    after_add = mem.list_workflows()
    assert len(after_add) == n_initial + 1
    assert any(w["name"] == "test-flow" for w in after_add)

    # Delete it
    assert mem.delete_workflow("test-flow") is True
    after_del = mem.list_workflows()
    assert len(after_del) == n_initial
    assert not any(w["name"] == "test-flow" for w in after_del)

    # Deleting unknown returns False
    assert mem.delete_workflow("never-existed") is False


def test_reset_memory_to_default(isolated_mem):
    mem = isolated_mem
    mem.write_memory("garbage")
    assert mem.read_memory() == "garbage"
    mem.reset_memory_to_default()
    assert "Copilot memory" in mem.read_memory()


def test_system_prompt_includes_memory(monkeypatch, tmp_path):
    """The Copilot system prompt should append user memory when it exists."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-for-import")
    import trader.copilot_memory as mem
    monkeypatch.setattr(mem, "MEMORY_FILE", tmp_path / "copilot_memory.md")
    mem.write_memory("- always trade tech\n- never trade crypto\n")

    import trader.copilot as cp
    prompt = cp._build_system_prompt()
    assert "always trade tech" in prompt
    assert "never trade crypto" in prompt
    assert "USER MEMORY" in prompt
