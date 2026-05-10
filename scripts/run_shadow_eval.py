#!/usr/bin/env python3
"""v6.0.x: shadow evaluation harness — records picks for ALL
strategies daily, regardless of which are live-deployed.

Solves the cold-start problem: the auto-router's eligibility filter
requires ≥6 months of OOS evidence before promoting a strategy to
live. New v6 strategies (xs_top10_insider_buy, _insider_edgar_30d,
_pead_5d) are env-gated OFF in production until the operator opts
in. Without this script, they accumulate zero evidence and can
never be promoted.

This script:
  1. Force-enables the v6 env gates for the duration of this process
     (so shadow evaluation works even before the operator activates
     them in production).
  2. Calls eval_runner.evaluate_at(today, DEFAULT_LIQUID_EXPANDED)
     which runs every registered strategy and journals picks to the
     strategy_eval table.
  3. The recorded picks feed the leaderboard, which feeds the auto-
     router's eligibility filter. After ~120 trading days, new
     strategies have enough evidence to be promotable.

Run once daily via the launchd plist com.trader.shadow-eval (4:30 PM
ET — after market close + after the daily orchestrator). Idempotent
on (asof, strategy) — the strategy_eval table has a UNIQUE constraint
so re-runs are safe.

NOTE: This is *shadow* evaluation. It does NOT submit any orders.
Picks land in strategy_eval (the leaderboard data) only. Actual
order submission still happens in main.py and respects all the
production env gates (INSIDER_SIGNAL_ENABLED, PEAD_ENABLED, etc.).

Usage:
  python scripts/run_shadow_eval.py             # today
  python scripts/run_shadow_eval.py 2026-05-10  # specific date
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Force-enable v6 strategy env gates BEFORE importing trader modules.
# These mirror the production env knobs but stay scoped to this
# process — they don't affect launchd's other daemons.
os.environ.setdefault("INSIDER_SIGNAL_ENABLED", "1")
os.environ.setdefault("INSIDER_EDGAR_ENABLED", "1")
os.environ.setdefault("PEAD_ENABLED", "1")
# SEC EDGAR mandates a real User-Agent for Form-4 fetches
os.environ.setdefault(
    "SEC_USER_AGENT",
    "trader-shadow-eval richard.chen@flexhaul.ai",
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from trader import eval_runner  # noqa: E402
from trader.universe import DEFAULT_LIQUID_EXPANDED  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if argv:
        asof = pd.Timestamp(argv[0])
    else:
        asof = pd.Timestamp(pd.Timestamp.today().date())
    universe = DEFAULT_LIQUID_EXPANDED
    print(f"[{pd.Timestamp.now()}] shadow eval: asof={asof.date()}, "
           f"universe={len(universe)} names")
    n = eval_runner.evaluate_at(asof, universe)
    print(f"  recorded {n} new strategy_eval rows "
           f"(UNIQUE constraint deduplicates re-runs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
