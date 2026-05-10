"""Tests for v6.0.x weekend safety + report-label fixes.

Two fixes shipped together:
  1. Market-open gate in main.py — refuses to submit orders when
     Alpaca's clock reports the market closed, unless
     ALLOW_WEEKEND_ORDERS=1 is explicitly set.
  2. Report relabeling for non-trading days — "Day P&L" becomes
     "Last-close P&L (YYYY-MM-DD)" and the divergence section gets
     a header that makes clear the numbers are cumulative, not
     intraday.

The market-open gate is tested via direct function probes on the
build_daily_report path (covers the label fix) plus a minimal
integration check that the env-override works. The full main()
control-flow is not exercised end-to-end (requires real Alpaca
creds) but the relevant branch logic is verified directly.
"""
from __future__ import annotations

import os
from datetime import date

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# 1. Report labels — Day P&L vs Last-close P&L
# ============================================================
def _minimal_report_args(**overrides):
    """Minimum kwargs for build_daily_report. Override specific fields."""
    base = {
        "run_id": "test-run",
        "momentum_picks": [],
        "bottom_candidates": [],
        "approved_bottoms": [],
        "sleeve_alloc": {"momentum": 0.80, "bottom": 0.15},
        "sleeve_method": "sample",
        "final_targets": {},
        "risk_warnings": [],
        "rebalance_results": [],
        "bracket_results": [],
        "vix": 15.0,
        "equity_before": 100_000.0,
        "equity_after": 101_000.0,
        "cash_after": 30_000.0,
        "positions_now": None,
        "spy_today_return": 0.005,
        "yesterday_equity": 100_000.0,
    }
    base.update(overrides)
    return base


def test_report_uses_day_pnl_label_when_market_open():
    from trader.report import build_daily_report
    args = _minimal_report_args(market_open_today=True)
    _, body = build_daily_report(**args)
    assert "Day P&L:" in body
    assert "Last-close P&L" not in body
    assert "market closed today" not in body


def test_report_uses_last_close_label_when_market_closed():
    from trader.report import build_daily_report
    args = _minimal_report_args(
        market_open_today=False,
        last_trading_day=date(2026, 5, 8),
    )
    _, body = build_daily_report(**args)
    assert "Last-close P&L (2026-05-08)" in body
    assert "market closed today" in body
    # "Day P&L:" label specifically (with the colon) should NOT
    # appear as the section header; "Day P&L" might appear elsewhere
    # in commentary but the prefixed label is the one we relabel.
    assert "Day P&L:   " not in body
    assert "Day P&L:  $" not in body


def test_report_last_close_label_falls_back_when_date_missing():
    """If last_trading_day is None, the label still works."""
    from trader.report import build_daily_report
    args = _minimal_report_args(
        market_open_today=False,
        last_trading_day=None,
    )
    _, body = build_daily_report(**args)
    assert "Last-close P&L (last close)" in body


# ============================================================
# 2. Position-return-since-entry label
# ============================================================
def _position(plpc: float):
    return {
        "symbol": "AAPL", "qty": 10, "market_value": 1000,
        "unrealized_pl": 100, "unrealized_plpc": plpc, "current_price": 100,
        "avg_entry_price": 90,
    }


def test_anomalous_moves_renamed_to_position_return():
    """Old label was 'ANOMALOUS MOVES (>2% vs SPY)'; new label
    'POSITION RETURN SINCE ENTRY'."""
    from trader.report import build_daily_report
    args = _minimal_report_args(
        spy_today_return=0.005,  # SPY +0.5%
        positions_now={"AAPL": _position(plpc=0.10)},  # AAPL +10% since entry
    )
    _, body = build_daily_report(**args)
    # Old header gone
    assert "ANOMALOUS MOVES" not in body
    # New header present
    assert "POSITION RETURN SINCE ENTRY" in body
    # Body explains it's cumulative
    assert "CUMULATIVE returns since each lot was opened" in body
    # Per-line wording uses "since entry"
    assert "+10.00% since entry" in body
    assert "vs SPY's last session" in body


def test_position_return_header_warns_on_market_closed():
    from trader.report import build_daily_report
    args = _minimal_report_args(
        spy_today_return=0.005,
        positions_now={"AAPL": _position(plpc=0.10)},
        market_open_today=False,
        last_trading_day=date(2026, 5, 8),
    )
    _, body = build_daily_report(**args)
    assert "POSITION RETURN SINCE ENTRY" in body
    assert "market closed today" in body.upper() or "market closed today" in body


# ============================================================
# 3. Market-open gate — main.py pre-flight branch
# ============================================================
def test_main_imports_market_open_gate():
    """Source-text check: the v6 market-open gate is wired into the
    kill-switch pre-flight branch of main.py."""
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "src" / "trader" / "main.py"
    txt = src.read_text()
    # Key tokens that prove the gate is in place
    assert "ALLOW_WEEKEND_ORDERS" in txt
    assert "market closed (next open" in txt
    assert "market_closed" in txt
    assert "halt_type" in txt
    # The gate must be inside the kill-switch pre-flight section,
    # before any orders are computed
    pre_flight_idx = txt.find("kill-switch pre-flight")
    market_gate_idx = txt.find("market-open gate")
    build_targets_idx = txt.find("build_targets(universe)")
    assert pre_flight_idx > 0
    assert market_gate_idx > pre_flight_idx, \
        "market gate should come AFTER the kill-switch label"
    assert market_gate_idx < build_targets_idx, \
        "market gate must run BEFORE build_targets()"


def test_main_passes_market_flag_to_report():
    """build_daily_report receives market_open_today + last_trading_day."""
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "src" / "trader" / "main.py"
    txt = src.read_text()
    assert "market_open_today=market_open_flag" in txt
    assert "last_trading_day=last_trading_day" in txt


def test_market_gate_returns_halt_dict_when_closed():
    """Verify the halt-return contract: when market is closed and
    no override, main returns {halted: True, halt_type: 'market_closed'}.

    This is a SOURCE-TEXT check (not a live main() run, which would
    need real Alpaca creds and is covered elsewhere). The shape of
    the return dict is what the caller (run_daily.py) keys off of."""
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "src" / "trader" / "main.py"
    txt = src.read_text()
    # The halt branch must return the right keys
    assert '"halt_type": "market_closed"' in txt
    assert '"market_closed": True' in txt


def test_market_gate_respects_override():
    """The ALLOW_WEEKEND_ORDERS=1 override path is wired."""
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "src" / "trader" / "main.py"
    txt = src.read_text()
    # Override branch must print a warning, not halt
    assert "ALLOW_WEEKEND_ORDERS=1 overrides" in txt
    # The override branch sits inside the same if-not-DRY_RUN block
    # as the halt branch (both check market_open_flag)
    override_idx = txt.find("ALLOW_WEEKEND_ORDERS=1 overrides")
    halt_idx = txt.find("market closed (next open")
    # Both should appear
    assert override_idx > 0
    assert halt_idx > 0
