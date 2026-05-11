"""Tests for v6.0.x Public.com manual-execution workflow.

Two new operator-facing tools:
  1. scripts/weekly_digest.py — Friday signal digest
  2. scripts/import_public_positions.py — CSV-driven reconciliation
"""
from __future__ import annotations

import csv
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


# ============================================================
# Weekly digest
# ============================================================
def _seed_decisions(db_path: Path, n: int = 5):
    """Build a minimal journal with decisions + runs + position_lots."""
    con = sqlite3.connect(str(db_path))
    con.executescript("""
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, ticker TEXT, action TEXT, style TEXT,
            score REAL, rationale_json TEXT, bull TEXT, bear TEXT,
            risk_decision TEXT, final TEXT
        );
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY, started_at TEXT, completed_at TEXT,
            status TEXT, notes TEXT
        );
        CREATE TABLE IF NOT EXISTS position_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, sleeve TEXT, opened_at TEXT, qty REAL,
            open_price REAL, open_order_id TEXT, closed_at TEXT,
            close_price REAL, close_order_id TEXT, realized_pnl REAL
        );
    """)
    now = datetime.utcnow()
    base_ts = now.isoformat()
    for i, sym in enumerate(["AAPL", "MSFT", "JNJ", "WMT", "INTC"][:n]):
        con.execute(
            "INSERT INTO decisions (ts, ticker, action, style, score, "
            "rationale_json, final) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ((now - timedelta(seconds=i)).isoformat(), sym, "BUY", "MOMENTUM",
             0.5 + i * 0.1, "{}",
             "LIVE_AUTO_BUY @ 8.9% (selected=vertical_winner)"),
        )
    con.execute(
        "INSERT INTO runs (run_id, started_at, status, notes) "
        "VALUES ('test-1', ?, 'completed', '5 targets')",
        (base_ts,),
    )
    con.commit()
    con.close()


def test_weekly_digest_renders_target_book(tmp_path):
    db = tmp_path / "j.db"
    _seed_decisions(db, n=5)
    from weekly_digest import build_digest
    out = build_digest(db, days=7)
    assert "TARGET BOOK FOR THIS WEEK" in out
    assert "AAPL" in out
    assert "8.90%" in out
    assert "TOTAL" in out


def test_weekly_digest_includes_public_checklist(tmp_path):
    db = tmp_path / "j.db"
    _seed_decisions(db, n=3)
    from weekly_digest import build_digest
    out = build_digest(db)
    assert "PUBLIC.COM EXECUTION CHECKLIST" in out
    assert "Public.com" in out
    assert "import_public_positions.py" in out


def test_weekly_digest_handles_missing_db(tmp_path):
    """Should not crash on missing journal."""
    from weekly_digest import build_digest
    out = build_digest(tmp_path / "missing.db")
    assert "no recent orchestrator runs" in out


def test_weekly_digest_csv_export(tmp_path):
    db = tmp_path / "j.db"
    _seed_decisions(db, n=3)
    from weekly_digest import (
        get_latest_decisions, extract_target_weights, export_csv,
    )
    decs = get_latest_decisions(db, days=7)
    targets = extract_target_weights(decs)
    csv_path = tmp_path / "out.csv"
    export_csv(targets, csv_path)
    rows = list(csv.DictReader(csv_path.open()))
    assert len(rows) == 3
    assert {"ticker", "target_weight_pct", "style"} <= set(rows[0].keys())


def test_weekly_digest_extracts_weight_from_final_string():
    from weekly_digest import extract_target_weights
    decs = [{
        "ts": datetime.utcnow().isoformat(),
        "ticker": "AAPL", "action": "BUY", "style": "MOMENTUM",
        "score": 1.0, "rationale_json": "{}",
        "final": "LIVE_AUTO_BUY @ 8.9% (selected=vertical_winner)",
    }]
    out = extract_target_weights(decs)
    assert "AAPL" in out
    assert abs(out["AAPL"]["weight"] - 0.089) < 1e-6


# ============================================================
# Public.com CSV importer
# ============================================================
def _write_public_csv(path: Path, rows: list[dict]):
    """Write a Public.com-style holdings CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Symbol", "Quantity", "Last Price", "Cost Basis"])
        for r in rows:
            w.writerow([r["symbol"], r["qty"], r["price"], r["cost"]])


def test_import_public_csv_parses_standard_format(tmp_path):
    from import_public_positions import parse_public_csv
    csv_path = tmp_path / "Holdings.csv"
    _write_public_csv(csv_path, [
        {"symbol": "AAPL", "qty": 25, "price": 200.50, "cost": 180.00},
        {"symbol": "MSFT", "qty": 10, "price": 400.00, "cost": 350.00},
    ])
    positions = parse_public_csv(csv_path)
    assert len(positions) == 2
    syms = {p["symbol"] for p in positions}
    assert syms == {"AAPL", "MSFT"}


def test_import_public_csv_tolerates_alternate_column_names(tmp_path):
    """Public.com sometimes exports 'Shares' instead of 'Quantity'."""
    from import_public_positions import parse_public_csv
    csv_path = tmp_path / "Holdings.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Ticker", "Shares", "Price", "Avg Cost"])
        w.writerow(["AAPL", "25", "200.50", "180.00"])
    positions = parse_public_csv(csv_path)
    assert positions[0]["symbol"] == "AAPL"
    assert positions[0]["qty"] == 25


def test_import_public_csv_drops_zero_quantity_rows(tmp_path):
    from import_public_positions import parse_public_csv
    csv_path = tmp_path / "Holdings.csv"
    _write_public_csv(csv_path, [
        {"symbol": "AAPL", "qty": 0, "price": 200, "cost": 180},
        {"symbol": "MSFT", "qty": 10, "price": 400, "cost": 350},
    ])
    positions = parse_public_csv(csv_path)
    assert len(positions) == 1
    assert positions[0]["symbol"] == "MSFT"


def test_import_public_csv_missing_required_column_raises(tmp_path):
    from import_public_positions import parse_public_csv
    csv_path = tmp_path / "Holdings.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Name", "Notional"])
        w.writerow(["Apple", "5000"])
    with pytest.raises(ValueError):
        parse_public_csv(csv_path)


def test_compute_drift_classifies_correctly():
    from import_public_positions import compute_drift
    public = [
        {"symbol": "AAPL", "qty": 25, "price": 200, "cost_basis": 180},
        {"symbol": "MSFT", "qty": 10, "price": 400, "cost_basis": 350},
        {"symbol": "NEW", "qty": 5, "price": 100, "cost_basis": 100},
    ]
    journal = [
        {"id": 1, "symbol": "AAPL", "sleeve": "M", "opened_at": "x",
         "qty": 25, "open_price": 180},
        {"id": 2, "symbol": "MSFT", "sleeve": "M", "opened_at": "x",
         "qty": 8, "open_price": 350},  # drift: journal has 8, public has 10
        {"id": 3, "symbol": "STALE", "sleeve": "M", "opened_at": "x",
         "qty": 5, "open_price": 100},  # journal-only
    ]
    result = compute_drift(public, journal)
    assert "AAPL" in result["matched"]
    assert "MSFT" in result["drift"]
    assert abs(result["drift"]["MSFT"]["diff"] - 2.0) < 1e-6
    assert ("NEW", 5) in result["public_only"]
    assert ("STALE", 5) in result["journal_only"]


def test_apply_resync_closes_and_reopens(tmp_path):
    """After resync, journal should match Public.com exactly."""
    from import_public_positions import (
        parse_public_csv, fetch_journal_open_lots,
        compute_drift, apply_resync,
    )
    # Seed journal with stale lots
    db = tmp_path / "j.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE position_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, sleeve TEXT, opened_at TEXT, qty REAL,
            open_price REAL, open_order_id TEXT, closed_at TEXT,
            close_price REAL, close_order_id TEXT, realized_pnl REAL
        );
    """)
    con.execute(
        "INSERT INTO position_lots (symbol, sleeve, opened_at, qty, open_price) "
        "VALUES ('OLD', 'M', 'x', 100, 50)",
    )
    con.commit()
    con.close()

    # Write Public CSV with different positions
    csv_path = tmp_path / "Holdings.csv"
    _write_public_csv(csv_path, [
        {"symbol": "AAPL", "qty": 25, "price": 200, "cost": 180},
    ])

    positions = parse_public_csv(csv_path)
    initial_lots = fetch_journal_open_lots(db)
    apply_resync(db, positions, initial_lots)

    # After resync: only AAPL should be open
    final_lots = fetch_journal_open_lots(db)
    assert len(final_lots) == 1
    assert final_lots[0]["symbol"] == "AAPL"
    assert abs(final_lots[0]["qty"] - 25) < 1e-6
