"""Tests for the v6.1.0 cash-park overlay.

Behaviour matrix:
  CASH_PARK_TICKER  drawdown tier  residual > buffer  → active?
      unset           GREEN              True              NO (disabled)
      "SPY"           GREEN              True              YES, park_pct = residual - buffer
      "SPY"           GREEN              False             NO (nothing to park)
      "SPY"           YELLOW             True              NO (drawdown suppresses)
      "SPY"           RED                True              NO
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

from trader.cash_park import plan_cash_park


def test_default_disabled(monkeypatch):
    """No env var set → overlay inert. The 60% deployed / 40% cash
    scenario (today's actual state) returns active=False."""
    monkeypatch.delenv("CASH_PARK_TICKER", raising=False)
    plan = plan_cash_park({"AAPL": 0.3, "MSFT": 0.3}, drawdown_pct=0.0)
    assert plan.active is False
    assert plan.ticker == ""
    assert "unset" in plan.reason


def test_active_in_green_with_residual(monkeypatch):
    """Operator sets CASH_PARK_TICKER=SPY, no drawdown, 60% deployed.
    Expect overlay to park (40% - 5% buffer) = 35% in SPY."""
    monkeypatch.setenv("CASH_PARK_TICKER", "SPY")
    plan = plan_cash_park(
        {"AAPL": 0.3, "MSFT": 0.3}, drawdown_pct=0.0
    )
    assert plan.active is True
    assert plan.ticker == "SPY"
    assert plan.park_pct == pytest.approx(0.35, abs=1e-9)


def test_explicit_arg_overrides_env(monkeypatch):
    """Passing cash_park_ticker= takes precedence over env (for tests)."""
    monkeypatch.delenv("CASH_PARK_TICKER", raising=False)
    plan = plan_cash_park(
        {"AAPL": 0.3}, drawdown_pct=0.0, cash_park_ticker="IVV"
    )
    assert plan.active is True
    assert plan.ticker == "IVV"
    assert plan.park_pct == pytest.approx(0.65, abs=1e-9)


def test_yellow_drawdown_suppresses(monkeypatch):
    """-7% drawdown puts us in YELLOW. Cash IS the protection — skip overlay."""
    monkeypatch.setenv("CASH_PARK_TICKER", "SPY")
    plan = plan_cash_park(
        {"AAPL": 0.3, "MSFT": 0.3}, drawdown_pct=-0.07
    )
    assert plan.active is False
    assert "YELLOW" in plan.reason or "suppressed" in plan.reason


def test_red_drawdown_suppresses(monkeypatch):
    """-9% drawdown crosses into RED. No cash-park."""
    monkeypatch.setenv("CASH_PARK_TICKER", "SPY")
    plan = plan_cash_park(
        {"AAPL": 0.3}, drawdown_pct=-0.09
    )
    assert plan.active is False


def test_residual_below_buffer_not_active(monkeypatch):
    """If deployed = 96% (residual 4%), park_pct would be negative —
    overlay declines."""
    monkeypatch.setenv("CASH_PARK_TICKER", "SPY")
    plan = plan_cash_park(
        {"AAPL": 0.5, "MSFT": 0.46}, drawdown_pct=0.0
    )
    assert plan.active is False
    assert plan.park_pct == 0.0


def test_residual_exactly_at_buffer_not_active(monkeypatch):
    """Edge case: residual == buffer. Don't park (avoid the
    zero-allocation case)."""
    monkeypatch.setenv("CASH_PARK_TICKER", "SPY")
    plan = plan_cash_park(
        {"AAPL": 0.95}, drawdown_pct=0.0
    )
    assert plan.active is False


def test_custom_min_buffer(monkeypatch):
    """Operator can tighten/loosen the kept-liquid buffer."""
    monkeypatch.setenv("CASH_PARK_TICKER", "SPY")
    plan = plan_cash_park(
        {"AAPL": 0.6}, drawdown_pct=0.0, min_buffer=0.10
    )
    # residual 40% - 10% buffer = 30% park
    assert plan.active is True
    assert plan.park_pct == pytest.approx(0.30, abs=1e-9)


def test_lowercase_ticker_normalized(monkeypatch):
    """env values like 'spy' get uppercased so downstream matches."""
    monkeypatch.setenv("CASH_PARK_TICKER", "spy")
    plan = plan_cash_park({"AAPL": 0.3}, drawdown_pct=0.0)
    assert plan.ticker == "SPY"


def test_today_scenario_recovers_drag(monkeypatch):
    """Today (2026-05-14): 9 names @ 6.83% = 61.5% deployed, 38.5% cash,
    drawdown ~0 (we're near peak). Cash-park should activate at
    38.5% - 5% = 33.5% allocated to SPY."""
    monkeypatch.setenv("CASH_PARK_TICKER", "SPY")
    targets = {f"NAME{i}": 0.0683 for i in range(9)}
    plan = plan_cash_park(targets, drawdown_pct=0.0)
    assert plan.active is True
    deployed = sum(targets.values())
    expected = (1.0 - deployed) - 0.05
    assert plan.park_pct == pytest.approx(expected, abs=1e-6)
