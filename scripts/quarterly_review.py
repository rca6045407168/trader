#!/usr/bin/env python3
"""Quarterly forced-review tool.

Stop-rules don't hold. The v4.0.0 freeze was renegotiated three times
in four weeks (§11 of ARCHITECTURE.md is the record). The replacement
is a *forced review*, not a stop-rule: every quarter, print every
assumption the platform is making and require the operator to
acknowledge each one in writing (via the journal).

The platform has 8 stacked edges, 32 strategies, 138 names, 5
daemons. The operator must STAY a controller. This script makes the
control deliberate.

Run quarterly (Jan/Apr/Jul/Oct) via launchd. Operator's job: read
the assumptions, confirm each one or flag for revision, append the
result to a journaled review log.

Usage:
  python scripts/quarterly_review.py            # interactive
  python scripts/quarterly_review.py --print    # print only, no prompts
  python scripts/quarterly_review.py --acknowledge-all  # log a blanket OK
                                                  # (don't make this a habit)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.config import DB_PATH  # noqa: E402


@dataclass
class Assumption:
    key: str               # short identifier (logged)
    statement: str         # the operator-facing claim
    last_verified: str     # human reference for "how do we know"


# The assumptions the platform is making. Adding or removing an
# assumption here is itself a structural decision — should be reviewed
# every cycle. Order roughly: most-fragile first.
ASSUMPTIONS: list[Assumption] = [
    Assumption(
        "tlh_economic_value",
        "TLH still has economic value for the operator's tax situation.",
        "Depends on (a) operator's marginal rate, (b) whether account is "
        "still taxable (not 401k/IRA), (c) wash-sale rules unchanged.",
    ),
    Assumption(
        "momentum_substrate_works",
        "Cross-sectional momentum still works in US large-cap monthly.",
        "Asness-Frazzini-Pedersen 2014, Moskowitz-Ooi-Pedersen 2012. "
        "Some post-publication decay since 2010 per McLean-Pontiff. The "
        "auto-router's eligibility filter protects against extreme decay.",
    ),
    Assumption(
        "quality_factor_alive",
        "Novy-Marx quality factor (gross-profitability-to-assets) "
        "still adds 0.3-0.7%/yr.",
        "Novy-Marx 2013. AQR replicated 2019. Has NOT shown the value "
        "factor's post-publication decay.",
    ),
    Assumption(
        "insider_signal_decoded",
        "Insider cluster-buying still produces 1-3%/yr long-only alpha.",
        "Cohen-Malloy-Pomorski 2012. Likely decayed since publication; "
        "EDGAR-based fresher signal partially compensates.",
    ),
    Assumption(
        "pead_drift_alive",
        "PEAD (post-earnings drift) still produces 1-2%/yr.",
        "Bernard-Thomas 1989, replicated repeatedly. ~30% decay post-"
        "2010 per Chordia-Shivakumar 2006 follow-ups. Most fragile of "
        "the v6 alpha sources.",
    ),
    Assumption(
        "vol_target_18pct",
        "18% annualized portfolio vol is the right target.",
        "Heuristic, not data-derived. Operator should revise based on "
        "personal risk tolerance.",
    ),
    Assumption(
        "ddaware_floor_70pct",
        "0.70× gross floor under drawdown-aware overlay is conservative "
        "enough.",
        "Hand-picked. A 30% degrossing during a -10% DD is meaningful but "
        "not extreme.",
    ),
    Assumption(
        "138_name_universe_right",
        "The 138-name expanded universe is the right scope.",
        "Hand-curated. Adds Utilities + Real Estate vs the 50-name set. "
        "Full S&P 500 would 3.6× the scope further but adds liquidity "
        "concerns at small-cap end.",
    ),
    Assumption(
        "alpaca_paper_proxy",
        "Alpaca paper P&L is a reasonable proxy for real-money "
        "performance.",
        "True for: position tracking, signal fidelity. False for: "
        "slippage, partial fills, market-on-close auctions, tax "
        "events. Real-money deployment is the only true test.",
    ),
    Assumption(
        "auto_router_eligibility_filter",
        "Eligibility filter (MIN_EVIDENCE_MONTHS≥6, MAX_BETA≤1.20, "
        "MIN_DD≥-25%) is the right gate.",
        "Heuristic. 6 months is shorter than academic standard (3yr) "
        "but matches our paper-account timeframe.",
    ),
    Assumption(
        "calendar_overlay_damped",
        "Calendar overlay (turn-of-month / OPEX / FOMC / pre-holiday) "
        "still produces 30-50 bps/yr stacked.",
        "Halloween/turn-of-month show post-publication decay. FOMC "
        "drift (Lucca-Moench) is the strongest still-alive.",
    ),
    Assumption(
        "monte_carlo_assumptions",
        "Component-edge correlations to equity stress in uplift "
        "Monte Carlo are roughly right.",
        "Hand-set. The TRUE correlations might be larger in tail "
        "events (factor crashes happen together).",
    ),
    Assumption(
        "no_alpha_decay_acceleration",
        "Post-publication alpha decay is not accelerating.",
        "McLean-Pontiff 2016 saw ~32% decay/yr. If acceleration is "
        "happening (e.g., AI-driven trading commoditizing all factors), "
        "the uplift estimate is biased high.",
    ),
]


def print_review(asof: datetime) -> None:
    print()
    print("=" * 72)
    print(f"  QUARTERLY ASSUMPTION REVIEW — {asof:%Y-%m-%d}")
    print("=" * 72)
    print(
        "  Each assumption below is a SEPARATE decision. Read each one; "
        "if the\n  evidence has shifted, decide whether to (a) leave the "
        "platform unchanged,\n  (b) tune a parameter, or (c) disable an "
        "edge. The point is not to vote\n  yes on everything — it's to "
        "stay the controller."
    )
    print()
    for i, a in enumerate(ASSUMPTIONS, 1):
        print(f"  [{i:2}] {a.key}")
        print(f"       Claim:     {a.statement}")
        print(f"       Evidence:  {a.last_verified}")
        print()


def interactive_review(asof: datetime, db_path: Path) -> list[dict]:
    """Prompt for each assumption. Returns a list of {key, status,
    note}. The status is 'ack' (acknowledged true), 'flag' (operator
    flagged for revision), or 'skip' (deferred to next review)."""
    print_review(asof)
    print("=" * 72)
    print("  ACKNOWLEDGEMENT")
    print("=" * 72)
    print("  For each assumption above, respond:")
    print("    'a' = acknowledge (assumption holds)")
    print("    'f' = flag for revision (assumption may be wrong)")
    print("    's' = skip (defer)")
    print("    'q' = quit (abort review)")
    print()
    results = []
    for a in ASSUMPTIONS:
        while True:
            try:
                resp = input(f"  [{a.key}] (a/f/s/q): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n  Review aborted.")
                return results
            if resp == "q":
                print("  Review aborted.")
                return results
            if resp in ("a", "f", "s"):
                note = ""
                if resp == "f":
                    try:
                        note = input("    Why are you flagging it? ").strip()
                    except (EOFError, KeyboardInterrupt):
                        note = "(no detail given)"
                results.append({
                    "key": a.key,
                    "status": {"a": "ack", "f": "flag", "s": "skip"}[resp],
                    "note": note,
                })
                break
            print("    Please enter 'a', 'f', 's', or 'q'.")
    return results


def log_to_journal(asof: datetime, results: list[dict], db_path: Path) -> None:
    """Append review results to a quarterly_reviews table. Creates the
    table on first run."""
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            """CREATE TABLE IF NOT EXISTS quarterly_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asof TEXT NOT NULL,
                results_json TEXT NOT NULL,
                n_ack INTEGER, n_flag INTEGER, n_skip INTEGER,
                created_at TEXT NOT NULL
            )"""
        )
        n_ack = sum(1 for r in results if r["status"] == "ack")
        n_flag = sum(1 for r in results if r["status"] == "flag")
        n_skip = sum(1 for r in results if r["status"] == "skip")
        con.execute(
            "INSERT INTO quarterly_reviews "
            "(asof, results_json, n_ack, n_flag, n_skip, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (asof.isoformat(), json.dumps(results),
             n_ack, n_flag, n_skip,
             datetime.utcnow().isoformat()),
        )
        con.commit()
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true",
                     help="Print assumptions only, no prompts.")
    ap.add_argument("--acknowledge-all", action="store_true",
                     help="Log a blanket ack without prompts. Use sparingly.")
    ap.add_argument("--db", type=Path, default=Path(DB_PATH))
    args = ap.parse_args(argv)

    asof = datetime.utcnow()
    if args.print:
        print_review(asof)
        return 0
    if args.acknowledge_all:
        results = [{"key": a.key, "status": "ack",
                     "note": "blanket-ack via --acknowledge-all"}
                    for a in ASSUMPTIONS]
        log_to_journal(asof, results, args.db)
        print(f"Logged blanket ack of {len(results)} assumptions to journal.")
        return 0

    results = interactive_review(asof, args.db)
    if results:
        log_to_journal(asof, results, args.db)
        n_ack = sum(1 for r in results if r["status"] == "ack")
        n_flag = sum(1 for r in results if r["status"] == "flag")
        n_skip = sum(1 for r in results if r["status"] == "skip")
        print(f"\nLogged: {n_ack} ack, {n_flag} flag, {n_skip} skip.")
        if n_flag:
            print("\nFLAGGED assumptions — investigate before next run:")
            for r in results:
                if r["status"] == "flag":
                    print(f"  - {r['key']}: {r['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
