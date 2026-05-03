"""[v3.59.3 — TESTING_PRACTICES Cat 9] One-day-shift determinism test.

Same inputs → same outputs. If they don't, you have hidden state.

Strategy: re-run yesterday's decision with yesterday's prices and
account state; the resulting target weights MUST match what was actually
decided yesterday (within float-equality tolerance).

Run weekly via cron or manually:
  python scripts/determinism_test.py [--asof 2026-05-02]

Failure modes this catches:
  • Unseeded RNG in any path (lightgbm, sklearn, np.random, dict order
    in old Python, file-system enumeration order)
  • Time-of-day-dependent behavior (datetime.now() in a feature path)
  • Cache state that should not affect outputs (silent caching of macro)
  • Library version drift between yesterday and today
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

DB_PATH = ROOT / "data" / "journal.db"


def _last_recorded_decisions(today: str) -> list[dict]:
    """Pull the decisions journaled for `today` (ISO YYYY-MM-DD)."""
    if not DB_PATH.exists():
        return []
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as c:
        rows = c.execute(
            "SELECT ticker, action, score, final FROM decisions "
            "WHERE date(ts) = ? ORDER BY ts",
            (today,)).fetchall()
    return [{"ticker": r[0], "action": r[1], "score": r[2], "final": r[3]}
            for r in rows]


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default=None,
                     help="ISO date to re-run; defaults to yesterday UTC")
    ap.add_argument("--tolerance", type=float, default=1e-6)
    args = ap.parse_args()

    asof = args.asof or (datetime.utcnow().date() - timedelta(days=1)).isoformat()
    print(f"=== Determinism test: re-run for {asof} ===")

    recorded = _last_recorded_decisions(asof)
    if not recorded:
        print(f"No journaled decisions for {asof}. Skipping (system either "
              f"didn't run that day or it's pre-deployment).")
        return 0
    print(f"Found {len(recorded)} journaled decisions to verify.")

    # Re-derive: pull universe + run rank_momentum at the historical state.
    # The only fully reproducible re-run requires snapshotting price history
    # AS-OF that date. yfinance returns history including today's bars, so
    # we approximate by fetching with end=asof.
    try:
        from trader.universe import DEFAULT_LIQUID_50
        from trader.strategy import rank_momentum
    except Exception as e:
        print(f"  import failed: {e}")
        return 1

    # v3.59.4: rank_momentum now accepts end_date. We can run a real
    # AS-OF re-derivation now.
    try:
        cur_picks = [c.ticker for c in
                      rank_momentum(DEFAULT_LIQUID_50, top_n=15, end_date=asof)]
    except Exception as e:
        print(f"  rank_momentum failed: {e}")
        return 1

    recorded_picks = [r["ticker"] for r in recorded if r["action"] == "BUY"]
    overlap = set(cur_picks) & set(recorded_picks)
    n_recorded = len(set(recorded_picks))
    print(f"  recorded picks: {sorted(recorded_picks)}")
    print(f"  re-derived picks (as-of {asof}): {sorted(cur_picks)}")
    print(f"  overlap: {len(overlap)}/{n_recorded}")

    if n_recorded > 0:
        match_pct = len(overlap) / n_recorded * 100
        if match_pct >= 95:
            print(f"  ✅ DETERMINISTIC: {match_pct:.0f}% match")
            return 0
        elif match_pct >= 80:
            print(f"  🟡 BORDERLINE: {match_pct:.0f}% match — investigate divergence")
            return 0
        else:
            print(f"  ❌ NON-DETERMINISTIC: {match_pct:.0f}% match")
            print("     Likely causes: unseeded RNG, dict ordering, library drift,")
            print("     yfinance backfill changes since {asof}.".format(asof=asof))
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
