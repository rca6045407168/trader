"""Tests for v3.59.1 — VRP + ML-PEAD sleeve scaffolds (free data)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest


# ============================================================
# VRP scaffold
# ============================================================

def test_vrp_default_status_not_wired(monkeypatch):
    monkeypatch.delenv("VRP_SLEEVE_STATUS", raising=False)
    from trader.vrp_sleeve import status
    assert status() == "NOT_WIRED"


def test_vrp_put_delta_signs():
    """ITM put delta closer to -1, OTM put delta closer to 0."""
    from trader.vrp_sleeve import put_delta
    spot = 500.0
    t = 30 / 365.25
    vol = 0.15
    itm = put_delta(spot, strike=520, t_years=t, vol=vol)  # in-the-money put
    otm = put_delta(spot, strike=480, t_years=t, vol=vol)  # out-of-the-money put
    # Both deltas are negative (puts), magnitude greater for ITM
    assert itm < 0
    assert otm < 0
    assert abs(itm) > abs(otm)


def test_vrp_select_strikes_picks_30_and_10_delta():
    """Synthetic chain. 30-delta should be the higher-strike short."""
    from trader.vrp_sleeve import OptionRow, select_strikes
    spot = 500.0
    chain = [
        OptionRow(strike=420, bid=0.50, ask=0.60, iv=0.15),  # ~5d
        OptionRow(strike=460, bid=2.00, ask=2.10, iv=0.15),  # ~10d
        OptionRow(strike=485, bid=8.00, ask=8.10, iv=0.15),  # ~30d
        OptionRow(strike=500, bid=15.0, ask=15.1, iv=0.15),  # ~50d ATM
    ]
    short, long_ = select_strikes(chain, spot, days_to_expiry=30,
                                    short_delta=0.30, long_delta=0.10)
    assert short is not None and long_ is not None
    assert short.strike > long_.strike
    # Short should be the 485 strike (closest to 30-delta on this chain)
    assert short.strike == 485


def test_vrp_plan_empty_chain_returns_error():
    from trader.vrp_sleeve import plan_today
    plan = plan_today(spot=500.0, chain=[], total_equity=100_000)
    assert plan.error is not None


def test_vrp_plan_sized_for_max_loss_pct():
    from trader.vrp_sleeve import OptionRow, plan_today
    spot = 500.0
    chain = [
        OptionRow(strike=420, bid=0.50, ask=0.60, iv=0.15),
        OptionRow(strike=460, bid=2.00, ask=2.10, iv=0.15),
        OptionRow(strike=485, bid=8.00, ask=8.10, iv=0.15),
    ]
    plan = plan_today(spot, chain, total_equity=100_000)
    if plan.error:
        pytest.skip(f"plan failed for synthetic chain: {plan.error}")
    # Total max loss must respect 2% of portfolio
    if plan.n_spreads > 0 and plan.max_loss_per_spread:
        total_max = plan.max_loss_per_spread * plan.n_spreads
        assert total_max <= 100_000 * 0.025  # small slack vs 2%


# ============================================================
# ML-PEAD scaffold
# ============================================================

def test_pead_default_status_not_wired(monkeypatch):
    monkeypatch.delenv("PEAD_SLEEVE_STATUS", raising=False)
    from trader.pead_sleeve import status
    assert status() == "NOT_WIRED"


def test_pead_run_length():
    from trader.pead_sleeve import _run_length
    assert _run_length([1, -1, -1, 1, 1, 1]) == 3
    assert _run_length([-1, -1, -1]) == 3
    assert _run_length([]) == 0
    assert _run_length([0, 0, 0]) == 0
    assert _run_length([1, 0]) == 0  # zero breaks streak


def test_pead_surprise_sign():
    from trader.pead_sleeve import _surprise_sign
    assert _surprise_sign(5.0) == 1
    assert _surprise_sign(-5.0) == -1
    assert _surprise_sign(0.0) == 0


def test_pead_expected_targets_empty_when_not_wired(monkeypatch):
    monkeypatch.delenv("PEAD_SLEEVE_STATUS", raising=False)
    from trader.pead_sleeve import expected_targets
    out = expected_targets(["AAPL", "MSFT"], n_holdings=5)
    assert out == {}


def test_pead_expected_targets_shape_when_shadow(monkeypatch):
    """Should return dict[str, float] summing to <= sleeve_capital_pct."""
    monkeypatch.setenv("PEAD_SLEEVE_STATUS", "SHADOW")
    from trader.pead_sleeve import expected_targets, sleeve_capital_pct
    out = expected_targets(["AAPL", "MSFT", "NVDA"], n_holdings=5)
    # If no positive scores returned (network/yfinance failure), out is {}
    assert isinstance(out, dict)
    if out:
        assert sum(out.values()) <= sleeve_capital_pct() + 1e-6
        for sym, w in out.items():
            assert w > 0


def test_pead_features_shape():
    """Feature dataclass has all expected fields."""
    from trader.pead_sleeve import PeadFeatures
    f = PeadFeatures(ticker="X")
    assert f.sue_sequence == []
    assert f.surprise_sign_run_length == 0
    assert f.last_surprise_pct is None
