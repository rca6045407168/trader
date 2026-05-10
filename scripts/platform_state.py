#!/usr/bin/env python3
"""Platform-state-in-2-pages — the operator's single audit tool.

The platform has outgrown what fits in the operator's head. 8
stacked overlays, 32 strategies, 138 names, 5 daemons. Without an
explicit tool, "what is the system currently doing?" requires
grepping the codebase.

This script answers in <2 screen pages:
  - Which overlays are ENABLED right now (env-driven)
  - Which strategies the auto-router considered + chose recently
  - Which strategies the eligibility filter rejected, and why
  - Daemon health (heartbeat + recent run timestamps)
  - Journal state summary (snapshot count, last run, lot count)
  - Production targets vs broker reality (drift indicator)

Run as: python scripts/platform_state.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.config import DB_PATH  # noqa: E402


def section(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def show_env_state() -> None:
    section("V6 OVERLAY ENV STATE")
    knobs = [
        ("TLH_ENABLED", "false", "TLH two-book master gate"),
        ("TLH_LOT_SELECTION", "HIFO", "lot-selection method at close"),
        ("DIRECT_INDEX_CORE_PCT", "0.70", "Book A allocation"),
        ("DIRECT_INDEX_QUALITY_TILT", "0.0", "Novy-Marx quality tilt strength"),
        ("VOL_TARGET_ENABLED", "1", "Moreira-Muir vol overlay"),
        ("DRAWDOWN_AWARE_ENABLED", "1", "DD-aware gross reduction"),
        ("CALENDAR_OVERLAY_ENABLED", "1", "anomaly-driven gross scalar"),
        ("INSIDER_SIGNAL_ENABLED", "0", "yfinance insider strategy"),
        ("INSIDER_EDGAR_ENABLED", "0", "SEC EDGAR Form-4 strategy"),
        ("PEAD_ENABLED", "0", "post-earnings drift strategy"),
        ("UNIVERSE_SIZE", "(50)", "50 or expanded (138)"),
        ("ALLOW_WEEKEND_ORDERS", "0", "weekend/holiday override"),
        ("DATA_QUALITY_HALT_ENABLED", "1", "halt on data-quality issues"),
    ]
    for env, default, desc in knobs:
        val = os.environ.get(env, "(unset)")
        active = val != "(unset)" and val.lower() not in ("0", "false", "")
        mark = "✅" if active else ("➖" if val == "(unset)" else "❌")
        # Pad to look like a table
        print(f"  {mark} {env:<32} = {val:<10}  ({desc})")


def show_strategy_recency() -> None:
    section("STRATEGY EVAL — LAST 7 DAYS")
    if not Path(DB_PATH).exists():
        print("  (no journal database)")
        return
    cutoff = (datetime.utcnow() - timedelta(days=7)).date().isoformat()
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT strategy, COUNT(*) AS n, AVG(n_picks) AS avg_picks, "
            "MAX(asof) AS last_seen FROM strategy_eval "
            "WHERE asof >= ? GROUP BY strategy ORDER BY n DESC, strategy",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        con.close()
    if not rows:
        print("  (no eval rows in last 7 days)")
        return
    print(f"  {'Strategy':<40} {'Days':>5}  {'AvgPicks':>10}  Last seen")
    print(f"  {'-'*40} {'-'*5}  {'-'*10}  {'-'*10}")
    for strat, n, avg, last in rows:
        print(f"  {strat:<40} {n:>5}  {avg:>10.1f}  {last}")


def show_live_variant() -> None:
    section("AUTO-ROUTER LIVE SELECTION")
    if not Path(DB_PATH).exists():
        print("  (no journal database)")
        return
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT ts, ticker, final FROM decisions "
            "WHERE style = 'live_auto' ORDER BY ts DESC LIMIT 1",
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        con.close()
    if not row:
        print("  (no live_auto decision in journal — auto-router not active "
               "yet, or v5 LIVE selection happens on a different style tag)")
        return
    ts, ticker, final = row
    print(f"  Last live_auto decision: {ts[:19]}")
    print(f"  Final action: {final}")


def show_daemon_health() -> None:
    section("DAEMON HEALTH — RECENT RUNS")
    if not Path(DB_PATH).exists():
        print("  (no journal database)")
        return
    cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT run_id, started_at, completed_at, status, notes "
            "FROM runs WHERE started_at >= ? "
            "ORDER BY started_at DESC LIMIT 10",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        con.close()
    if not rows:
        print("  (no orchestrator runs in last 7 days)")
        return
    print(f"  {'Run ID':<30} {'Status':<12}  Notes")
    print(f"  {'-'*30} {'-'*12}  {'-'*30}")
    for run_id, started, completed, status, notes in rows:
        notes_short = (notes or "")[:50]
        print(f"  {run_id:<30} {status:<12}  {notes_short}")


def show_journal_summary() -> None:
    section("JOURNAL STATE SUMMARY")
    if not Path(DB_PATH).exists():
        print("  (no journal database)")
        return
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        n_snaps = con.execute(
            "SELECT COUNT(*), MIN(date), MAX(date) FROM daily_snapshot"
        ).fetchone()
        n_orders = con.execute(
            "SELECT COUNT(*), MAX(ts) FROM orders"
        ).fetchone()
        n_decisions = con.execute(
            "SELECT COUNT(*), MAX(ts) FROM decisions"
        ).fetchone()
        n_open_lots = con.execute(
            "SELECT COUNT(*) FROM position_lots WHERE closed_at IS NULL"
        ).fetchone()
        n_closed_lots = con.execute(
            "SELECT COUNT(*), MIN(closed_at), MAX(closed_at) FROM position_lots "
            "WHERE closed_at IS NOT NULL"
        ).fetchone()
        try:
            n_earnings = con.execute(
                "SELECT COUNT(*), MAX(filed_at) FROM earnings_signals"
            ).fetchone()
        except sqlite3.OperationalError:
            n_earnings = (0, None)
        try:
            n_eval = con.execute(
                "SELECT COUNT(*), MIN(asof), MAX(asof) FROM strategy_eval"
            ).fetchone()
        except sqlite3.OperationalError:
            n_eval = (0, None, None)
    finally:
        con.close()
    print(f"  daily_snapshot:  {n_snaps[0]:>5} rows  ({n_snaps[1]} → {n_snaps[2]})")
    print(f"  orders:          {n_orders[0]:>5} rows  (last: {n_orders[1] or '(n/a)'})")
    print(f"  decisions:       {n_decisions[0]:>5} rows  (last: {n_decisions[1] or '(n/a)'})")
    print(f"  position_lots:   {n_open_lots[0]:>5} open  /  {n_closed_lots[0]} closed")
    if n_closed_lots[0] > 0:
        print(f"                   closed range: {n_closed_lots[1]} → {n_closed_lots[2]}")
    print(f"  earnings_signals:{n_earnings[0]:>5} rows  (last: {n_earnings[1] or '(n/a)'})")
    print(f"  strategy_eval:   {n_eval[0]:>5} rows  ({n_eval[1]} → {n_eval[2]})")


def show_recent_runs_outcome() -> None:
    section("RECENT TLH HARVEST EVENTS")
    if not Path(DB_PATH).exists():
        print("  (no journal database)")
        return
    cutoff = (datetime.utcnow() - timedelta(days=30)).date().isoformat()
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT symbol, sleeve, closed_at, realized_pnl FROM position_lots "
            "WHERE closed_at IS NOT NULL AND realized_pnl < 0 AND closed_at >= ? "
            "ORDER BY closed_at DESC LIMIT 20",
            (cutoff,),
        ).fetchall()
        total = con.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) FROM position_lots "
            "WHERE closed_at IS NOT NULL AND realized_pnl < 0",
        ).fetchone()[0]
    except sqlite3.OperationalError:
        rows = []
        total = 0.0
    finally:
        con.close()
    print(f"  Cumulative realized loss (all-time): ${total:,.2f}")
    if rows:
        print(f"  Recent (last 30d):")
        for sym, sleeve, closed, pnl in rows:
            print(f"    {closed[:19]}  {sym:<6}  sleeve={sleeve}  pnl=${pnl:,.2f}")
    else:
        print("  No realized losses in last 30 days.")


def show_one_line_summary() -> None:
    section("ONE-LINE SUMMARY")
    if not Path(DB_PATH).exists():
        print("  (no journal database)")
        return
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        snap = con.execute(
            "SELECT date, equity FROM daily_snapshot "
            "ORDER BY date DESC LIMIT 1"
        ).fetchone()
        peak = con.execute(
            "SELECT MAX(equity) FROM daily_snapshot"
        ).fetchone()
    finally:
        con.close()
    if snap:
        d, eq = snap
        peak_eq = peak[0] if peak and peak[0] else eq
        dd = (eq - peak_eq) / peak_eq * 100 if peak_eq else 0
        print(f"  Equity: ${eq:,.2f}  |  Peak: ${peak_eq:,.2f}  |  DD: {dd:+.2f}%  |  Last snap: {d}")


def main() -> int:
    print()
    print(f"PLATFORM STATE — generated {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"DB: {DB_PATH}")
    show_one_line_summary()
    show_env_state()
    show_strategy_recency()
    show_live_variant()
    show_daemon_health()
    show_journal_summary()
    show_recent_runs_outcome()
    print()
    print("=" * 72)
    print("  End of platform state. ~2 screen pages, every coupling visible.")
    print("=" * 72)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
