"""Tests for the v6 final-cleanup ship.

Three items in scope:
  1. trader.slippage_stats — broker-aware slippage aggregation
  2. UNIVERSE_SIZE=sp500_500 routing in main.py
  3. quarterly_reviews table population (gate-9 advance)
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# slippage_stats
# ============================================================
def test_slippage_stats_returns_none_on_public_live(monkeypatch):
    """Public.com slippage tracking isn't implemented yet — return
    None so the caller renders the "not available" note."""
    monkeypatch.setenv("BROKER", "public_live")
    from trader.slippage_stats import compute_recent_slippage_stats
    assert compute_recent_slippage_stats() is None


def test_slippage_stats_returns_none_when_alpaca_unavailable(monkeypatch):
    """If Alpaca creds missing or client fails, return None cleanly."""
    monkeypatch.setenv("BROKER", "alpaca_paper")
    monkeypatch.setattr("trader.execute.ALPACA_KEY", "")
    # Reset cached Alpaca client singleton — other tests may have
    # populated it earlier in the run with valid keys
    monkeypatch.setattr("trader.execute._client", None)
    from trader.slippage_stats import compute_recent_slippage_stats
    # get_client() raises when keys missing; the catch should return None
    assert compute_recent_slippage_stats() is None


def test_format_slippage_section_handles_none():
    from trader.slippage_stats import format_slippage_section
    out = format_slippage_section(None, days=7)
    assert "unavailable" in out
    assert "Public.com" in out or "broker" in out.lower()


def test_format_slippage_section_renders_stats():
    from trader.slippage_stats import format_slippage_section
    stats = {
        "n_fills": 25,
        "mean_bps": 7.2,
        "median_bps": 5.0,
        "p95_bps": 25.0,
        "buy_mean_bps": 8.0,
        "sell_mean_bps": 6.5,
        "vs_5bp_assumption": "WORSE",
        "implication_bps_per_yr": 8.6,
        "broker": "alpaca_paper",
    }
    out = format_slippage_section(stats, days=7)
    assert "25" in out
    assert "7.2" in out
    assert "WORSE" in out
    # WORSE branch should warn about uplift being biased high
    assert "biased high" in out


def test_format_slippage_section_renders_better_case():
    from trader.slippage_stats import format_slippage_section
    stats = {
        "n_fills": 25,
        "mean_bps": 1.5,
        "median_bps": 1.0,
        "p95_bps": 8.0,
        "buy_mean_bps": 1.5,
        "sell_mean_bps": 1.5,
        "vs_5bp_assumption": "BETTER",
        "implication_bps_per_yr": 1.8,
        "broker": "alpaca_paper",
    }
    out = format_slippage_section(stats, days=7)
    assert "BETTER" in out
    assert "hidden buffer" in out


# ============================================================
# UNIVERSE_SIZE=sp500_500
# ============================================================
def test_main_supports_sp500_500_universe_routing():
    """Source-text check that main.py recognizes the sp500_500 value."""
    src = Path(__file__).resolve().parent.parent / "src" / "trader" / "main.py"
    txt = src.read_text()
    assert 'univ_size == "sp500_500"' in txt
    assert "sp500_tickers" in txt


def test_universe_sp500_tickers_returns_list():
    """The sp500_tickers helper is callable. (Wikipedia fetch may fail
    offline; just verify the function exists and returns a list — may
    be the fallback DEFAULT_LIQUID_50.)"""
    from trader.universe import sp500_tickers
    result = sp500_tickers()
    assert isinstance(result, list)
    assert len(result) >= 50  # at minimum the fallback


# ============================================================
# quarterly_reviews — gate 9 advance
# ============================================================
def test_quarterly_review_acknowledge_all_creates_journal_row(tmp_path):
    """Running --acknowledge-all writes a row that gate 9 reads."""
    import sys as _sys
    _sys.path.insert(0, str(
        Path(__file__).resolve().parent.parent / "scripts"
    ))
    from quarterly_review import main as qr_main

    db = tmp_path / "j.db"
    rc = qr_main(["--acknowledge-all", "--db", str(db)])
    assert rc == 0
    con = sqlite3.connect(str(db))
    rows = con.execute(
        "SELECT n_ack, n_flag, n_skip FROM quarterly_reviews"
    ).fetchall()
    con.close()
    assert len(rows) == 1
    n_ack, n_flag, n_skip = rows[0]
    assert n_ack > 0
    assert n_flag == 0
    assert n_skip == 0


def test_go_live_gate_9_passes_after_recent_review(tmp_path):
    """Gate 9 passes when quarterly_reviews has a row within 90 days."""
    import sys as _sys
    _sys.path.insert(0, str(
        Path(__file__).resolve().parent.parent / "scripts"
    ))
    db = tmp_path / "j.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE quarterly_reviews ("
        "id INTEGER PRIMARY KEY, asof TEXT, results_json TEXT, "
        "n_ack INTEGER, n_flag INTEGER, n_skip INTEGER, created_at TEXT"
        ")"
    )
    from datetime import datetime
    con.execute(
        "INSERT INTO quarterly_reviews (asof, results_json, "
        "n_ack, n_flag, n_skip, created_at) VALUES "
        "(?, '[]', 13, 0, 0, ?)",
        (datetime.utcnow().isoformat(), datetime.utcnow().isoformat()),
    )
    con.commit()
    con.close()
    import importlib
    from trader import config
    config.DB_PATH = db
    import go_live_gate
    importlib.reload(go_live_gate)
    passed, msg = go_live_gate.gate_9_quarterly_review()
    assert passed is True
