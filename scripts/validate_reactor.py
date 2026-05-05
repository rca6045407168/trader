#!/usr/bin/env python3
"""v3.73.10 — Reactor signal validation script.

Computes per-signal forward returns (1d/5d/20d) for every row in
journal.earnings_signals, plus SPY's matching forward return, plus
the active return (alpha vs SPY). Persists to a new
reactor_signal_outcomes table for the dashboard to read.

Run as part of the daily orchestrator OR ad-hoc:
    python scripts/validate_reactor.py

The output answers the v3.73.4 DD's recommendation: "score every M3
signal against forward returns, decide whether the rule should stay
SHADOW or flip to LIVE."
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from trader.data import fetch_history  # noqa: E402

DB = ROOT / "data" / "journal.db"


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS reactor_signal_outcomes (
            id INTEGER PRIMARY KEY,
            signal_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            filed_at TEXT NOT NULL,
            direction TEXT,
            materiality INTEGER,
            ret_1d REAL,
            ret_5d REAL,
            ret_20d REAL,
            spy_ret_1d REAL,
            spy_ret_5d REAL,
            spy_ret_20d REAL,
            active_5d REAL,
            active_20d REAL,
            updated_at TEXT,
            UNIQUE(signal_id)
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_outcomes_dir_mat "
                "ON reactor_signal_outcomes(direction, materiality)")


def fwd_return(prices: pd.DataFrame, sym: str, t0: pd.Timestamp,
               ndays: int) -> float | None:
    if sym not in prices.columns:
        return None
    s = prices[sym].dropna()
    later = s[s.index > t0]
    if len(later) <= ndays:
        return None
    earlier = s[s.index <= t0]
    if earlier.empty:
        return None
    p0 = float(earlier.iloc[-1])
    p1 = float(later.iloc[ndays - 1])
    return (p1 / p0 - 1) * 100 if p0 > 0 else None


def main() -> None:
    con = sqlite3.connect(DB)
    ensure_schema(con)
    cur = con.cursor()
    rows = cur.execute(
        """SELECT id, symbol, filed_at, direction, materiality
           FROM earnings_signals
           WHERE direction IS NOT NULL AND filed_at IS NOT NULL"""
    ).fetchall()
    if not rows:
        print("No signals to validate.")
        return

    symbols = sorted(set(r[1] for r in rows)) + ["SPY"]
    prices = fetch_history(symbols, start="2025-01-01").dropna(axis=0, how="all")

    written = 0
    now = pd.Timestamp.utcnow().isoformat()
    for sid, sym, filed_at, direction, mat in rows:
        try:
            t0 = pd.Timestamp(filed_at).normalize()
        except Exception:
            continue
        r1 = fwd_return(prices, sym, t0, 1)
        r5 = fwd_return(prices, sym, t0, 5)
        r20 = fwd_return(prices, sym, t0, 20)
        s1 = fwd_return(prices, "SPY", t0, 1)
        s5 = fwd_return(prices, "SPY", t0, 5)
        s20 = fwd_return(prices, "SPY", t0, 20)
        a5 = (r5 - s5) if r5 is not None and s5 is not None else None
        a20 = (r20 - s20) if r20 is not None and s20 is not None else None

        cur.execute(
            """INSERT OR REPLACE INTO reactor_signal_outcomes
               (signal_id, symbol, filed_at, direction, materiality,
                ret_1d, ret_5d, ret_20d, spy_ret_1d, spy_ret_5d,
                spy_ret_20d, active_5d, active_20d, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid, sym, filed_at, direction, mat,
             r1, r5, r20, s1, s5, s20, a5, a20, now),
        )
        written += 1

    con.commit()
    con.close()
    print(f"Wrote {written} reactor_signal_outcomes rows.")


if __name__ == "__main__":
    main()
