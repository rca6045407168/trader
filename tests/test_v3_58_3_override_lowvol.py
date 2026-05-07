"""Tests for v3.58.3 — manual override + LowVolSleeve runner."""
from __future__ import annotations

import os
from pathlib import Path

import pytest


# ============================================================
# Manual override safety
# ============================================================

def test_manual_override_default_is_disallowed(monkeypatch):
    monkeypatch.delenv("MANUAL_OVERRIDE_ALLOWED", raising=False)
    from trader.manual_override import _allowed
    assert _allowed() is False


def test_manual_override_default_is_dry_run(monkeypatch):
    monkeypatch.delenv("MANUAL_OVERRIDE_DRY_RUN", raising=False)
    from trader.manual_override import _dry_run
    assert _dry_run() is True


def test_execute_refuses_without_env(monkeypatch):
    """Even with a valid plan token, execute() refuses if env not set."""
    monkeypatch.delenv("MANUAL_OVERRIDE_ALLOWED", raising=False)
    from trader import manual_override as mo
    # Manually inject a plan
    token = mo._store_plan({"action": "force_pause", "reason": "test"})
    out = mo.execute_force_pause(token)
    assert out.get("refused") == "MANUAL_OVERRIDE_ALLOWED!=true"


def test_plan_token_expires(monkeypatch):
    """A token older than 60s is rejected even if env is set."""
    monkeypatch.setenv("MANUAL_OVERRIDE_ALLOWED", "true")
    monkeypatch.setenv("MANUAL_OVERRIDE_DRY_RUN", "true")
    import time
    from trader import manual_override as mo
    token = mo._store_plan({"action": "force_pause", "reason": "test"})
    # Backdate the plan
    mo._PLAN_CACHE[token]["_created_at"] = time.time() - 120
    out = mo.execute_force_pause(token)
    assert "refused" in out


def test_plan_token_invalid(monkeypatch):
    monkeypatch.setenv("MANUAL_OVERRIDE_ALLOWED", "true")
    from trader import manual_override as mo
    out = mo.execute_force_pause("not-a-real-token")
    assert out.get("refused") == "plan_token invalid or expired (60s); re-plan"


def test_plan_token_consumed_once(monkeypatch):
    """Second use of a token after a successful execute returns refused."""
    monkeypatch.setenv("MANUAL_OVERRIDE_ALLOWED", "true")
    monkeypatch.setenv("MANUAL_OVERRIDE_DRY_RUN", "true")
    from trader import manual_override as mo
    token = mo._store_plan({"action": "force_pause", "reason": "test"})
    out1 = mo.execute_force_pause(token)
    assert out1.get("dry_run") is True
    out2 = mo.execute_force_pause(token)
    assert "refused" in out2


def test_action_type_mismatch(monkeypatch):
    """Token for flatten cannot be used to execute a trim."""
    monkeypatch.setenv("MANUAL_OVERRIDE_ALLOWED", "true")
    from trader import manual_override as mo
    token = mo._store_plan({"action": "flatten", "symbol": "AAPL"})
    out = mo.execute_trim(token)
    assert "refused" in out


def test_trim_pct_must_be_in_range():
    from trader import manual_override as mo
    out = mo.plan_trim("AAPL", 1.5)
    assert out.get("ok") is False
    out = mo.plan_trim("AAPL", -0.1)
    assert out.get("ok") is False
    out = mo.plan_trim("AAPL", 0.0)
    assert out.get("ok") is False


# ============================================================
# LowVolSleeve runner shape
# ============================================================

def test_dashboard_wires_manual_override_view():
    from pathlib import Path as _P
    src = _P(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = src.read_text()
    assert "def view_manual_override" in text
    assert '"manual_override": view_manual_override' in text
    assert "🛑 Manual override" in text


def test_dashboard_lowvol_overlay_present():
    from pathlib import Path as _P
    src = _P(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = src.read_text()
    assert "low_vol_shadow.csv" in text
    assert "LowVolSleeve shadow vs LIVE momentum" in text
