"""Tests for scripts/tlh_year_end.py.

Covers the four pure-function building blocks (no Streamlit / argparse
surface required to verify):

  1. fetch_loss_closes()         — query against a temp SQLite
  2. find_wash_sale_flags()      — 31-day window detection
  3. estimate_tax_savings()      — IRS rule math
  4. aggregate_by_symbol()       — per-ticker rollup
  5. write_csv()                  — file format
  6. render_report()              — empty-state + populated rendering

The view function in dashboard.py is *not* tested here (requires a
Streamlit runtime context). The cached helper `_cached_tlh_year`
delegates to fetch_loss_closes + find_wash_sale_flags, which ARE
tested here, so coverage of the dashboard widget is indirect but
real.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def _make_db(tmp_path: Path) -> Path:
    """Spin up a temp SQLite with the position_lots schema."""
    db = tmp_path / "journal.db"
    con = sqlite3.connect(str(db))
    con.execute("""
        CREATE TABLE position_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            sleeve TEXT NOT NULL,
            opened_at TEXT NOT NULL,
            qty REAL NOT NULL,
            open_price REAL,
            open_order_id TEXT,
            closed_at TEXT,
            close_price REAL,
            close_order_id TEXT,
            realized_pnl REAL
        )
    """)
    con.commit()
    con.close()
    return db


def _insert_lot(db: Path, **kw):
    con = sqlite3.connect(str(db))
    cols = ",".join(kw.keys())
    placeholders = ",".join("?" * len(kw))
    con.execute(
        f"INSERT INTO position_lots ({cols}) VALUES ({placeholders})",
        tuple(kw.values()),
    )
    con.commit()
    con.close()


# ============================================================
# fetch_loss_closes
# ============================================================
def test_fetch_loss_closes_empty_db(tmp_path):
    from tlh_year_end import fetch_loss_closes
    db = _make_db(tmp_path)
    assert fetch_loss_closes(db, 2026) == []


def test_fetch_loss_closes_skips_open_lots(tmp_path):
    from tlh_year_end import fetch_loss_closes
    db = _make_db(tmp_path)
    _insert_lot(db, symbol="AAPL", sleeve="direct_index_core",
                opened_at="2026-01-15T10:00:00", qty=10, open_price=180.0)
    # No closed_at → not eligible
    assert fetch_loss_closes(db, 2026) == []


def test_fetch_loss_closes_skips_gains(tmp_path):
    from tlh_year_end import fetch_loss_closes
    db = _make_db(tmp_path)
    _insert_lot(db, symbol="AAPL", sleeve="direct_index_core",
                opened_at="2026-01-15T10:00:00", qty=10, open_price=180.0,
                closed_at="2026-03-15T15:00:00", close_price=200.0,
                realized_pnl=200.0)  # GAIN, not loss
    assert fetch_loss_closes(db, 2026) == []


def test_fetch_loss_closes_pulls_loss_in_year(tmp_path):
    from tlh_year_end import fetch_loss_closes
    db = _make_db(tmp_path)
    _insert_lot(db, symbol="AAPL", sleeve="direct_index_core",
                opened_at="2026-01-15T10:00:00", qty=10, open_price=180.0,
                closed_at="2026-03-15T15:00:00", close_price=160.0,
                realized_pnl=-200.0)
    closes = fetch_loss_closes(db, 2026)
    assert len(closes) == 1
    ev = closes[0]
    assert ev.symbol == "AAPL"
    assert ev.realized_pnl == -200.0
    assert ev.qty == 10
    assert ev.holding_period_days == 59  # Jan 15 -> Mar 15


def test_fetch_loss_closes_year_boundary(tmp_path):
    """A close on Dec 31, 2025 23:59 should NOT appear in the 2026 report."""
    from tlh_year_end import fetch_loss_closes
    db = _make_db(tmp_path)
    _insert_lot(db, symbol="AAPL", sleeve="direct_index_core",
                opened_at="2025-12-01T10:00:00", qty=10, open_price=180.0,
                closed_at="2025-12-31T23:59:00", close_price=160.0,
                realized_pnl=-200.0)
    _insert_lot(db, symbol="MSFT", sleeve="direct_index_core",
                opened_at="2026-01-02T10:00:00", qty=5, open_price=400.0,
                closed_at="2026-12-15T15:00:00", close_price=380.0,
                realized_pnl=-100.0)
    closes_2025 = fetch_loss_closes(db, 2025)
    closes_2026 = fetch_loss_closes(db, 2026)
    assert {c.symbol for c in closes_2025} == {"AAPL"}
    assert {c.symbol for c in closes_2026} == {"MSFT"}


def test_long_term_short_term_classification(tmp_path):
    from tlh_year_end import fetch_loss_closes
    db = _make_db(tmp_path)
    # 100 days held → ST
    _insert_lot(db, symbol="A", sleeve="direct_index_core",
                opened_at="2026-01-01T10:00:00", qty=1, open_price=100,
                closed_at="2026-04-11T10:00:00", close_price=90,
                realized_pnl=-10.0)
    # 400 days held → LT
    _insert_lot(db, symbol="B", sleeve="direct_index_core",
                opened_at="2025-01-01T10:00:00", qty=1, open_price=100,
                closed_at="2026-02-05T10:00:00", close_price=90,
                realized_pnl=-10.0)
    closes = fetch_loss_closes(db, 2026)
    by_sym = {c.symbol: c for c in closes}
    assert by_sym["A"].is_long_term is False
    assert by_sym["B"].is_long_term is True


# ============================================================
# find_wash_sale_flags
# ============================================================
def test_wash_sale_flag_within_31_days(tmp_path):
    from tlh_year_end import fetch_loss_closes, find_wash_sale_flags
    db = _make_db(tmp_path)
    # Loss close
    _insert_lot(db, symbol="AAPL", sleeve="direct_index_core",
                opened_at="2026-01-15T10:00:00", qty=10, open_price=180,
                closed_at="2026-03-15T15:00:00", close_price=160,
                realized_pnl=-200.0)
    # Re-buy 5 days later — wash sale
    _insert_lot(db, symbol="AAPL", sleeve="direct_index_core",
                opened_at="2026-03-20T10:00:00", qty=10, open_price=165)
    closes = fetch_loss_closes(db, 2026)
    flags = find_wash_sale_flags(db, closes)
    assert len(flags) == 1
    assert flags[0].symbol == "AAPL"
    assert flags[0].days_between == 5


def test_wash_sale_no_flag_outside_31_days(tmp_path):
    from tlh_year_end import fetch_loss_closes, find_wash_sale_flags
    db = _make_db(tmp_path)
    _insert_lot(db, symbol="AAPL", sleeve="direct_index_core",
                opened_at="2026-01-15T10:00:00", qty=10, open_price=180,
                closed_at="2026-03-15T15:00:00", close_price=160,
                realized_pnl=-200.0)
    # Re-buy 35 days later — clear
    _insert_lot(db, symbol="AAPL", sleeve="direct_index_core",
                opened_at="2026-04-20T10:00:00", qty=10, open_price=165)
    closes = fetch_loss_closes(db, 2026)
    flags = find_wash_sale_flags(db, closes)
    assert flags == []


def test_wash_sale_window_runs_both_directions(tmp_path):
    """IRS rule: 30 days BEFORE and AFTER the loss-realizing sale."""
    from tlh_year_end import fetch_loss_closes, find_wash_sale_flags
    db = _make_db(tmp_path)
    # Buy 10 days BEFORE the loss-close — also a wash sale
    _insert_lot(db, symbol="MSFT", sleeve="direct_index_core",
                opened_at="2026-03-05T10:00:00", qty=5, open_price=405)
    _insert_lot(db, symbol="MSFT", sleeve="direct_index_core",
                opened_at="2026-01-15T10:00:00", qty=10, open_price=400,
                closed_at="2026-03-15T15:00:00", close_price=380,
                realized_pnl=-200.0)
    closes = fetch_loss_closes(db, 2026)
    flags = find_wash_sale_flags(db, closes)
    assert len(flags) == 1
    assert flags[0].symbol == "MSFT"


# ============================================================
# estimate_tax_savings
# ============================================================
def test_savings_zero_loss():
    from tlh_year_end import estimate_tax_savings
    r = estimate_tax_savings(0.0, federal_rate=0.32, state_rate=0.05)
    assert r["total_savings"] == 0.0
    assert r["carry_forward"] == 0.0


def test_savings_small_loss_ordinary_only():
    """$2k loss + zero cap gains → all offsets ordinary → $2k × 37%."""
    from tlh_year_end import estimate_tax_savings
    r = estimate_tax_savings(
        total_loss=-2000.0,
        federal_rate=0.32, state_rate=0.05,
        capital_gains_offset=0.0,
    )
    assert r["cg_offset"] == 0.0
    assert r["ordinary_offset"] == 2000.0
    assert r["carry_forward"] == 0.0
    assert abs(r["total_savings"] - 740.0) < 0.01  # 2000 * 0.37


def test_savings_big_loss_caps_ordinary_at_3k():
    """$10k loss + zero gains → $3k ordinary, $7k carry-forward."""
    from tlh_year_end import estimate_tax_savings
    r = estimate_tax_savings(
        total_loss=-10000.0,
        federal_rate=0.32, state_rate=0.05,
    )
    assert r["ordinary_offset"] == 3000.0
    assert r["carry_forward"] == 7000.0
    # Only ordinary-offset bucket has tax savings this year
    assert abs(r["total_savings"] - 3000 * 0.37) < 0.01


def test_savings_offsets_capital_gains_first():
    """$10k loss + $5k cap gains → $5k offsets gains, $3k ordinary,
    $2k carry-forward. Total saved = $8k × 37%."""
    from tlh_year_end import estimate_tax_savings
    r = estimate_tax_savings(
        total_loss=-10000.0,
        federal_rate=0.32, state_rate=0.05,
        capital_gains_offset=5000.0,
    )
    assert r["cg_offset"] == 5000.0
    assert r["ordinary_offset"] == 3000.0
    assert r["carry_forward"] == 2000.0
    # 8000 used this year × 0.37 = 2960
    assert abs(r["total_savings"] - 2960.0) < 0.01


# ============================================================
# aggregate_by_symbol
# ============================================================
def test_aggregate_groups_multiple_closes():
    from tlh_year_end import aggregate_by_symbol, CloseEvent
    closes = [
        CloseEvent("AAPL", "direct_index_core", "2026-01-01", "2026-03-01",
                   10, 180, 160, -200.0, 59),
        CloseEvent("AAPL", "direct_index_core", "2026-04-01", "2026-06-01",
                   5, 170, 160, -50.0, 61),
        CloseEvent("MSFT", "direct_index_core", "2026-01-01", "2026-05-01",
                   3, 400, 380, -60.0, 120),
    ]
    rows = aggregate_by_symbol(closes)
    by_sym = {r["symbol"]: r for r in rows}
    assert by_sym["AAPL"]["count"] == 2
    assert by_sym["AAPL"]["total_loss"] == -250.0
    assert by_sym["MSFT"]["count"] == 1
    # Sorted by most-negative first
    assert rows[0]["symbol"] == "AAPL"


def test_aggregate_separates_st_vs_lt():
    from tlh_year_end import aggregate_by_symbol, CloseEvent
    closes = [
        CloseEvent("AAPL", "direct_index_core", "2026-01-01", "2026-03-01",
                   10, 180, 160, -200.0, 59),    # ST
        CloseEvent("AAPL", "direct_index_core", "2024-04-01", "2026-04-01",
                   5, 170, 160, -50.0, 730),     # LT
    ]
    rows = aggregate_by_symbol(closes)
    assert rows[0]["st_count"] == 1
    assert rows[0]["lt_count"] == 1


# ============================================================
# write_csv
# ============================================================
def test_write_csv_columns_and_count(tmp_path):
    from tlh_year_end import write_csv, CloseEvent
    closes = [
        CloseEvent("AAPL", "direct_index_core", "2026-01-15T10:00:00",
                   "2026-03-15T15:00:00", 10, 180.0, 160.0, -200.0, 59),
        CloseEvent("MSFT", "direct_index_core", "2025-01-15T10:00:00",
                   "2026-04-15T15:00:00", 5, 400.0, 380.0, -100.0, 455),
    ]
    p = tmp_path / "out.csv"
    n = write_csv(closes, p)
    assert n == 2
    text = p.read_text()
    # Header
    assert "symbol,sleeve,date_acquired,date_sold" in text
    # AAPL row (ST)
    assert "AAPL,direct_index_core,2026-01-15,2026-03-15" in text
    assert ",ST" in text
    # MSFT row (LT)
    assert "MSFT,direct_index_core,2025-01-15,2026-04-15" in text
    assert ",LT" in text


# ============================================================
# render_report
# ============================================================
def test_render_report_empty_state():
    from tlh_year_end import render_report
    out = render_report(
        closes=[], flags=[], year=2026,
        federal_rate=0.32, state_rate=0.05,
    )
    assert "No loss-realizing closes recorded" in out
    assert "TLH_ENABLED was false" in out


def test_render_report_populated():
    from tlh_year_end import render_report, CloseEvent
    closes = [
        CloseEvent("AAPL", "direct_index_core", "2026-01-15T10:00:00",
                   "2026-03-15T15:00:00", 10, 180.0, 160.0, -200.0, 59),
    ]
    out = render_report(
        closes=closes, flags=[], year=2026,
        federal_rate=0.32, state_rate=0.05,
        capital_gains_offset=0.0,
    )
    assert "TAX YEAR 2026" in out
    assert "AAPL" in out
    assert "$-200" in out or "-200.00" in out
    assert "Estimated $ saved" in out
    # 200 ordinary offset × 0.37 = 74
    assert "74" in out


def test_render_report_flags_wash_sales():
    from tlh_year_end import render_report, CloseEvent, WashSaleFlag
    closes = [
        CloseEvent("AAPL", "direct_index_core", "2026-01-15T10:00:00",
                   "2026-03-15T15:00:00", 10, 180.0, 160.0, -200.0, 59),
    ]
    flags = [WashSaleFlag(
        symbol="AAPL", loss_closed_at="2026-03-15T15:00:00",
        loss_amount=-200.0, repurchase_at="2026-03-20T10:00:00",
        days_between=5,
    )]
    out = render_report(
        closes=closes, flags=flags, year=2026,
        federal_rate=0.32, state_rate=0.05,
    )
    assert "ACCOUNTANT" in out
    assert "AAPL" in out
    assert "5" in out  # days_between
