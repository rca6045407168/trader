"""Tests for v3.52.0/v3.52.1 modules:
  - positions_live (LivePosition / LivePortfolio dataclasses)
  - portfolio_heatmap (heatmap_dataframe_dict + sector_summary)
  - events_calendar (FOMC + OPEX + per-symbol)
  - brinson_attribution (single-period decomposition math)

Light tests — deeper integration is mocked because positions_live + events
require broker / yfinance access.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pytest


# -------------------- positions_live --------------------

def test_live_position_dataclass_fields():
    from trader.positions_live import LivePosition
    p = LivePosition(symbol="AAPL", qty=10, avg_cost=150.0)
    assert p.symbol == "AAPL"
    assert p.qty == 10
    assert p.avg_cost == 150.0
    assert p.last_price is None
    assert p.weight_of_book is None


def test_live_portfolio_to_dict_serializable():
    import json
    from trader.positions_live import LivePortfolio, LivePosition
    pf = LivePortfolio(equity=100000, cash=20000)
    pf.positions.append(LivePosition(symbol="AAPL", qty=10, avg_cost=150.0,
                                       last_price=160.0, market_value=1600,
                                       unrealized_pl=100, sector="Technology"))
    d = pf.to_dict()
    assert d["equity"] == 100000
    assert len(d["positions"]) == 1
    assert d["positions"][0]["symbol"] == "AAPL"
    json.dumps(d, default=str)  # must serialize


def test_fetch_live_portfolio_handles_broker_failure():
    """When broker fetch errors, fetch_live_portfolio returns a populated
    LivePortfolio with .error set, NOT raises."""
    from trader import positions_live
    with patch.object(positions_live, "_yesterday_closes", return_value={}):
        # simulate execute.get_client raising via patching the import
        with patch("trader.execute.get_client", side_effect=RuntimeError("no keys")):
            pf = positions_live.fetch_live_portfolio()
            assert pf.error is not None
            assert "broker fetch" in pf.error or "no keys" in pf.error


# -------------------- portfolio_heatmap --------------------

def test_heatmap_dict_handles_empty_input():
    from trader.portfolio_heatmap import heatmap_dataframe_dict
    rows = heatmap_dataframe_dict([])
    assert isinstance(rows, dict)
    assert rows["symbol"] == []
    assert rows["sector"] == []


def test_heatmap_dict_from_live_positions():
    from trader.positions_live import LivePosition
    from trader.portfolio_heatmap import heatmap_dataframe_dict
    positions = [
        LivePosition(symbol="AAPL", qty=10, avg_cost=150,
                      last_price=160, market_value=1600,
                      unrealized_pl=100, unrealized_pl_pct=0.066,
                      day_pl_dollar=20, day_pl_pct=0.013,
                      weight_of_book=0.16, sector="Technology"),
        LivePosition(symbol="JPM", qty=5, avg_cost=200,
                      last_price=190, market_value=950,
                      unrealized_pl=-50, unrealized_pl_pct=-0.05,
                      day_pl_dollar=-15, day_pl_pct=-0.015,
                      weight_of_book=0.10, sector="Financials"),
    ]
    rows = heatmap_dataframe_dict(positions)
    assert rows["symbol"] == ["AAPL", "JPM"]
    assert rows["sector"] == ["Technology", "Financials"]
    # weight stored as percent
    assert rows["weight"][0] == pytest.approx(16.0, abs=0.1)
    assert rows["day_pl_pct"][0] == pytest.approx(1.3, abs=0.1)


def test_sector_summary_aggregates_correctly():
    from trader.positions_live import LivePosition
    from trader.portfolio_heatmap import sector_summary
    positions = [
        LivePosition(symbol="AAPL", qty=10, avg_cost=150,
                      weight_of_book=0.10, day_pl_pct=0.02,
                      sector="Technology"),
        LivePosition(symbol="NVDA", qty=5, avg_cost=300,
                      weight_of_book=0.06, day_pl_pct=0.03,
                      sector="Technology"),
        LivePosition(symbol="JPM", qty=8, avg_cost=200,
                      weight_of_book=0.08, day_pl_pct=-0.01,
                      sector="Financials"),
    ]
    ss = sector_summary(positions)
    # 2 sectors
    assert len(ss) == 2
    # Technology has 2 names, weight 16%, weighted day P&L is
    # (0.02*0.10 + 0.03*0.06) / 0.16 = 0.0025/0.16 = 0.015625 → 1.5625%
    tech = next(s for s in ss if s["sector"] == "Technology")
    assert tech["n_positions"] == 2
    assert tech["total_weight_pct"] == pytest.approx(16.0, abs=0.1)
    assert tech["weighted_day_pl_pct"] == pytest.approx(2.375, abs=0.01)


# -------------------- events_calendar --------------------

def test_fomc_dates_constant_present_in_2026():
    from trader.events_calendar import FOMC_DATES_2026
    assert len(FOMC_DATES_2026) == 8  # Fed has 8 meetings/year
    assert all(d.year == 2026 for d in FOMC_DATES_2026)


def test_compute_upcoming_finds_fomc():
    from trader.events_calendar import compute_upcoming_events
    # Pick a date with a known FOMC ahead within 30 days
    today = date(2026, 4, 1)  # April 29 FOMC is within 30 days
    events = compute_upcoming_events(symbols=[], today=today, days_ahead=35)
    assert any(e.event_type == "fomc" for e in events)


def test_compute_upcoming_finds_opex():
    from trader.events_calendar import compute_upcoming_events
    today = date(2026, 5, 1)
    events = compute_upcoming_events(symbols=[], today=today, days_ahead=20)
    assert any(e.event_type == "opex" for e in events)


def test_compute_upcoming_handles_empty_symbols():
    """With no held names, only FOMC + OPEX events appear."""
    from trader.events_calendar import compute_upcoming_events
    today = date(2026, 5, 1)
    events = compute_upcoming_events(symbols=[], today=today, days_ahead=60)
    assert all(e.event_type in ("fomc", "opex") for e in events)
    # All events should have days_until ≥ 0 (no past events)
    assert all(e.days_until >= 0 for e in events)
    # Sorted by date
    assert all(events[i].date <= events[i + 1].date for i in range(len(events) - 1))


def test_third_friday_helper():
    from trader.events_calendar import _next_third_friday
    # April 2026: third Friday is April 17
    fridays = _next_third_friday(date(2026, 4, 1), n_months=3)
    assert len(fridays) == 3
    assert fridays[0] == date(2026, 4, 17)
    assert fridays[1] == date(2026, 5, 15)
    assert fridays[2] == date(2026, 6, 19)


# -------------------- brinson_attribution --------------------

def test_brinson_zero_when_portfolio_matches_benchmark():
    """If portfolio = benchmark, all three effects should be zero."""
    from trader.brinson_attribution import compute_brinson
    weights = {"Tech": 0.5, "Fin": 0.3, "Hlth": 0.2}
    rets = {"Tech": 0.02, "Fin": 0.01, "Hlth": -0.005}
    rep = compute_brinson(weights, rets, weights, rets)
    assert abs(rep.sum_allocation) < 1e-12
    assert abs(rep.sum_selection) < 1e-12
    assert abs(rep.sum_interaction) < 1e-12
    assert abs(rep.active_return) < 1e-12


def test_brinson_allocation_effect():
    """Overweight a winner, underweight a loser — positive allocation effect."""
    from trader.brinson_attribution import compute_brinson
    p_w = {"Tech": 0.7, "Fin": 0.3}
    b_w = {"Tech": 0.5, "Fin": 0.5}
    # Same returns within sector for both portfolio and benchmark (no selection effect)
    p_r = {"Tech": 0.02, "Fin": -0.01}
    b_r = {"Tech": 0.02, "Fin": -0.01}
    rep = compute_brinson(p_w, p_r, b_w, b_r)
    # Allocation: (0.7-0.5)*0.02 + (0.3-0.5)*-0.01 = 0.004 + 0.002 = 0.006
    assert rep.sum_allocation == pytest.approx(0.006, abs=1e-9)
    # Selection should be 0 (same returns)
    assert rep.sum_selection == pytest.approx(0.0, abs=1e-9)


def test_brinson_selection_effect():
    """Same weights but better in-sector picks — positive selection."""
    from trader.brinson_attribution import compute_brinson
    p_w = {"Tech": 0.5}
    b_w = {"Tech": 0.5}
    p_r = {"Tech": 0.04}  # our Tech names returned 4%
    b_r = {"Tech": 0.02}  # XLK returned 2%
    rep = compute_brinson(p_w, p_r, b_w, b_r)
    # Selection: 0.5 * (0.04 - 0.02) = 0.01
    assert rep.sum_selection == pytest.approx(0.01, abs=1e-9)
    assert rep.sum_allocation == pytest.approx(0.0, abs=1e-9)


def test_brinson_total_active_equals_sum_of_effects():
    """Active return = allocation + selection + interaction."""
    from trader.brinson_attribution import compute_brinson
    p_w = {"Tech": 0.6, "Fin": 0.4}
    b_w = {"Tech": 0.5, "Fin": 0.5}
    p_r = {"Tech": 0.03, "Fin": 0.0}
    b_r = {"Tech": 0.02, "Fin": -0.01}
    rep = compute_brinson(p_w, p_r, b_w, b_r)
    sum_effects = rep.sum_allocation + rep.sum_selection + rep.sum_interaction
    assert sum_effects == pytest.approx(rep.active_return, abs=1e-9)


def test_brinson_report_dict_serializable():
    import json
    from trader.brinson_attribution import compute_brinson
    rep = compute_brinson({"A": 1.0}, {"A": 0.01}, {"A": 1.0}, {"A": 0.01})
    d = rep.to_dict()
    json.dumps(d, default=str)
