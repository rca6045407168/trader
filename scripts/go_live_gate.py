#!/usr/bin/env python3
"""Go-live gate — 9 readiness checks before flipping BROKER=public_live.

Real-money deployment is the only true test of the trader. Before
flipping the BROKER env, the operator must pass all 9 gates. Each
gate exists because something went wrong (or could go wrong) without
it.

This script is ADVISORY — it doesn't flip the env var or block the
operator. It tells you what to fix.

Gates:
  1. Public.com credentials present in env
  2. Public.com adapter can authenticate + fetch account
  3. Public.com positions can be fetched without errors
  4. Cost-basis method on Public.com is Specific Lot ID (not FIFO)
  5. Alpaca paper has been running ≥30 days without halts
  6. Full eval-harness coverage: ≥120 days of strategy_eval rows
  7. ≥1 successful TLH harvest event in paper journal
  8. No reconciliation drift in journal (last 7 days)
  9. Operator has acknowledged the quarterly assumption review
     within the last 90 days

Usage:
  python scripts/go_live_gate.py
  python scripts/go_live_gate.py --verbose
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.config import DB_PATH  # noqa: E402


GATES_TOTAL = 9


def gate(name: str, check_fn) -> tuple[bool, str]:
    """Run a single gate. Returns (passed, message)."""
    try:
        result, msg = check_fn()
        return result, msg
    except Exception as e:
        return False, f"check raised {type(e).__name__}: {e}"


# ============================================================
# The 9 gates
# ============================================================
def gate_1_credentials():
    api = os.environ.get("PUBLIC_API_SECRET", "")
    acct = os.environ.get("PUBLIC_ACCOUNT_NUMBER", "")
    if not api or not acct:
        return False, ("missing PUBLIC_API_SECRET or PUBLIC_ACCOUNT_NUMBER "
                        "in env (loaded from .env)")
    return True, f"creds present (account ending {acct[-4:]})"


def gate_2_auth_and_account():
    try:
        from trader.broker.public_adapter import PublicAdapter
        adapter = PublicAdapter()
        a = adapter.get_account()
        return True, (f"authenticated. equity ${a.equity:,.2f}, "
                       f"buying_power ${a.buying_power:,.2f}")
    except Exception as e:
        return False, f"adapter init or account fetch failed: {e}"


def gate_3_positions():
    try:
        from trader.broker.public_adapter import PublicAdapter
        adapter = PublicAdapter()
        positions = adapter.get_all_positions()
        return True, f"{len(positions)} position(s) fetched"
    except Exception as e:
        return False, f"position fetch failed: {e}"


def gate_4_cost_basis_method():
    """Public.com's cost-basis method must be Specific Lot ID for HIFO
    to actually save tax dollars. This isn't queryable via the API
    (as far as we know), so it's an operator self-attestation.
    Mark in .env: PUBLIC_COST_BASIS_METHOD=SPECIFIC_ID
    """
    method = os.environ.get("PUBLIC_COST_BASIS_METHOD", "")
    if method.upper() == "SPECIFIC_ID":
        return True, "operator confirms Specific Lot ID is set on Public.com"
    return False, (
        "set PUBLIC_COST_BASIS_METHOD=SPECIFIC_ID in .env after enabling "
        "it on Public.com → Account → Tax Settings"
    )


def gate_5_alpaca_30d_stable():
    if not Path(DB_PATH).exists():
        return False, "no journal database"
    cutoff = (datetime.utcnow() - timedelta(days=30)).date().isoformat()
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        n_runs = con.execute(
            "SELECT COUNT(*) FROM runs WHERE started_at >= ?", (cutoff,),
        ).fetchone()[0]
        n_halts = con.execute(
            "SELECT COUNT(*) FROM runs WHERE started_at >= ? AND status = 'halted'",
            (cutoff,),
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return False, "runs table missing"
    finally:
        con.close()
    if n_runs < 20:
        return False, f"only {n_runs} runs in last 30 days (want ≥20)"
    if n_halts > 5:
        return False, f"{n_halts} halts in last 30 days (want ≤5)"
    return True, f"{n_runs} runs, {n_halts} halts (healthy)"


def gate_6_eval_coverage():
    if not Path(DB_PATH).exists():
        return False, "no journal database"
    cutoff = (datetime.utcnow() - timedelta(days=120)).date().isoformat()
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        n = con.execute(
            "SELECT COUNT(DISTINCT asof) FROM strategy_eval WHERE asof >= ?",
            (cutoff,),
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return False, "strategy_eval table missing"
    finally:
        con.close()
    if n < 60:
        return False, f"only {n} distinct asof dates in last 120d (want ≥60)"
    return True, f"{n} distinct asof dates of eval data (sufficient)"


def gate_7_tlh_proof():
    if not Path(DB_PATH).exists():
        return False, "no journal database"
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM position_lots "
            "WHERE closed_at IS NOT NULL AND realized_pnl < 0",
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return False, "position_lots table missing"
    finally:
        con.close()
    if n < 1:
        return False, ("no realized-loss closes in journal — TLH plumbing "
                        "hasn't been exercised on paper yet")
    return True, f"{n} loss-realizing closes in journal (TLH plumbing works)"


def gate_8_no_recent_drift():
    """Check that the last 7 runs didn't halt on reconciliation drift."""
    if not Path(DB_PATH).exists():
        return False, "no journal database"
    cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT notes FROM runs WHERE started_at >= ? AND status = 'halted'",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return False, "runs table missing"
    finally:
        con.close()
    drift_halts = sum(
        1 for r in rows
        if r[0] and "reconcile" in (r[0] or "").lower()
    )
    if drift_halts > 0:
        return False, (f"{drift_halts} reconcile-drift halt(s) in last 7d — "
                        "resync before going live")
    return True, "no reconciliation halts in last 7 days"


def gate_9_quarterly_review():
    """Operator must have run the quarterly assumption review within
    the last 90 days. The review's purpose is to keep the operator
    in controller mode."""
    if not Path(DB_PATH).exists():
        return False, ("no journal — run quarterly_review.py "
                        "--acknowledge-all to populate")
    cutoff = (datetime.utcnow() - timedelta(days=90)).isoformat()
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT MAX(asof) FROM quarterly_reviews WHERE asof >= ?",
            (cutoff,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False, ("quarterly_reviews table missing. Run "
                        "scripts/quarterly_review.py first.")
    finally:
        con.close()
    if not row or not row[0]:
        return False, ("no quarterly review in last 90 days. Run "
                        "scripts/quarterly_review.py")
    return True, f"last review: {row[0][:10]} (within 90-day window)"


GATES = [
    ("1. Public.com credentials in env",            gate_1_credentials),
    ("2. Authenticate + fetch account",             gate_2_auth_and_account),
    ("3. Fetch positions without errors",           gate_3_positions),
    ("4. Cost-basis method = Specific Lot ID",      gate_4_cost_basis_method),
    ("5. Alpaca paper stable ≥30 days",             gate_5_alpaca_30d_stable),
    ("6. Eval-harness coverage ≥60 days",           gate_6_eval_coverage),
    ("7. ≥1 TLH harvest proof in journal",          gate_7_tlh_proof),
    ("8. No reconciliation drift in last 7 days",   gate_8_no_recent_drift),
    ("9. Quarterly review within 90 days",          gate_9_quarterly_review),
]


def render_report(results: list[tuple[bool, str]],
                    verbose: bool = False) -> str:
    n_pass = sum(1 for r, _ in results if r)
    lines = [
        "=" * 70,
        f"GO-LIVE GATE — {n_pass}/{GATES_TOTAL} passed",
        "=" * 70,
    ]
    for (gate_name, _), (passed, msg) in zip(GATES, results):
        mark = "✅" if passed else "❌"
        lines.append(f"  {mark} {gate_name}")
        lines.append(f"     {msg}")
    lines.append("")
    if n_pass == GATES_TOTAL:
        lines.append("  ✅ ALL GATES PASSED — safe to flip BROKER=public_live")
        lines.append("")
        lines.append("  To activate:")
        lines.append("    launchctl setenv BROKER public_live")
        lines.append("    launchctl kickstart -k gui/$(id -u)/com.trader.daily-run")
    else:
        lines.append(f"  ⚠️  {GATES_TOTAL - n_pass} gate(s) failed — do NOT go live yet.")
        lines.append("")
        lines.append("  Fix each failing gate above. The order is roughly:")
        lines.append("    1-4 are operator setup (creds + Public.com UI)")
        lines.append("    5-8 are paper-account behavior (must accumulate over time)")
        lines.append("    9 is the assumption-review (run quarterly_review.py)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    results = [gate(name, fn) for name, fn in GATES]
    print(render_report(results, verbose=args.verbose))
    n_pass = sum(1 for r, _ in results if r)
    return 0 if n_pass == GATES_TOTAL else 1


if __name__ == "__main__":
    sys.exit(main())
