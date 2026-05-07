"""Tests for v3.64.0 — HANK persona + per-symbol summary + LLM audit log
+ self-eval postmortem auto-fire."""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# 1. HANK persona
# ============================================================
def test_system_prompt_introduces_hank():
    p = Path(__file__).resolve().parent.parent / "src" / "trader" / "copilot.py"
    text = p.read_text()
    # Must brand as HANK with the acronym expansion
    assert "HANK" in text
    assert "Honest Analytical Numerical Kopilot" in text


def test_dashboard_title_uses_hank():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "🤖 HANK" in text


# ============================================================
# 2. Per-symbol HANK summary helpers
# ============================================================
def test_hank_symbol_summary_helpers_defined():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "def _hank_symbol_summary" in text
    assert "def _hank_symbol_summary_cached" in text
    # Cache must have a TTL > 0
    assert "@st.cache_data(ttl=900" in text


def test_drill_down_modal_calls_summary():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    # The modal must invoke the summary helper
    assert "_hank_symbol_summary(symbol, pos)" in text
    assert "🧠 HANK summary" in text


# ============================================================
# 3. Email alerts UI fix
# ============================================================
def test_email_status_check_uses_smtp_creds():
    """The dashboard must check SMTP_USER+PASS, not SMTP_HOST (which has
    a default and was always-truthy)."""
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    # Old wrong check
    assert 'email_smtp = _os.getenv("SMTP_HOST", "")' not in text
    # New correct check
    assert 'SMTP_USER' in text and 'SMTP_PASS' in text


def test_email_test_button_present():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "Send test email" in text
    assert "alerts_test_email" in text


# ============================================================
# 5. LLM audit log
# ============================================================
def test_llm_audit_module_imports():
    from trader.llm_audit import (
        log_llm_call, recent, by_context, cost_summary,
        export_csv, estimate_cost,
    )
    assert callable(log_llm_call)


def test_estimate_cost_matches_pricing():
    from trader.llm_audit import estimate_cost
    # Sonnet 4.6: $3 input + $15 output per million
    cost = estimate_cost("claude-sonnet-4-6",
                          input_tokens=1_000_000,
                          output_tokens=1_000_000)
    assert cost == pytest.approx(3 + 15, abs=0.01)
    # Haiku is cheaper
    haiku_cost = estimate_cost("claude-haiku-4-5",
                                input_tokens=1_000_000,
                                output_tokens=1_000_000)
    assert haiku_cost < cost


def test_log_llm_call_returns_int(tmp_path, monkeypatch):
    """Best-effort logger returns int even when DB write succeeds."""
    monkeypatch.setattr("trader.llm_audit.DB", tmp_path / "j.db")
    from trader.llm_audit import log_llm_call
    rid = log_llm_call(
        context="test", user_input="hello",
        response_text="world", model="claude-sonnet-4-6",
    )
    assert isinstance(rid, int)
    assert rid > 0


def test_recent_returns_list(tmp_path, monkeypatch):
    monkeypatch.setattr("trader.llm_audit.DB", tmp_path / "j.db")
    from trader.llm_audit import log_llm_call, recent
    log_llm_call(context="test1", user_input="a", response_text="b",
                  model="claude-sonnet-4-6")
    log_llm_call(context="test2", user_input="c", response_text="d",
                  model="claude-sonnet-4-6")
    rows = recent(n=10)
    assert len(rows) == 2
    # Newest first
    assert rows[0]["context"] == "test2"


def test_cost_summary_per_context(tmp_path, monkeypatch):
    monkeypatch.setattr("trader.llm_audit.DB", tmp_path / "j.db")
    from trader.llm_audit import log_llm_call, cost_summary
    log_llm_call(context="copilot_chat", user_input="a", response_text="b",
                  model="claude-sonnet-4-6",
                  input_tokens=10_000, output_tokens=5_000)
    log_llm_call(context="postmortem", user_input="c", response_text="d",
                  model="claude-sonnet-4-6",
                  input_tokens=20_000, output_tokens=8_000)
    s = cost_summary(window_days=7)
    assert s["n_calls"] == 2
    assert s["total_cost_usd"] > 0
    assert "copilot_chat" in s["by_context"]
    assert "postmortem" in s["by_context"]


def test_log_llm_call_failsafe(tmp_path, monkeypatch):
    """If the DB is unwritable, log_llm_call must return -1 not raise."""
    # Point at a path that will fail to mkdir
    bad = Path("/dev/null/cannot_write_here.db")
    monkeypatch.setattr("trader.llm_audit.DB", bad)
    from trader.llm_audit import log_llm_call
    rid = log_llm_call(context="test", user_input="x", response_text="y",
                        model="claude-sonnet-4-6")
    assert rid == -1


# ============================================================
# 6. Self-evaluating postmortem auto-fire from prewarm
# ============================================================
def test_prewarm_includes_postmortem_section():
    p = Path(__file__).resolve().parent.parent / "scripts" / "prewarm.py"
    text = p.read_text()
    assert "self-eval postmortem" in text
    # Idempotent: must check journal for today's row before re-running
    assert "WHERE date = ?" in text
    assert "from trader.postmortem import run_postmortem" in text


# ============================================================
# Productization roadmap
# ============================================================
def test_copilot_logs_to_audit():
    p = Path(__file__).resolve().parent.parent / "src" / "trader" / "copilot.py"
    text = p.read_text()
    # The completion path must call log_llm_call
    assert "from .llm_audit import log_llm_call" in text
    assert 'context="copilot_chat"' in text
