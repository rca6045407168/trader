"""Strategy decay early warning: compares LIVE vs shadow performance.

If LIVE is decaying (alpha disappearing, picks no longer working), shadow
variants will start beating it BEFORE we notice via headline returns. This
script checks: do any shadows have a Sharpe > LIVE_Sharpe + 0.2 over the
last N days?

Triggers when shadow_decisions table has ≥30 days of evidence per shadow.
Currently shadow_decisions is empty (just enabled in v3.4 + v3.6); this
script will be useful in ~30 days when we have enough live data.

Designed to be cron-runnable. Outputs:
  - Per-shadow vs LIVE comparison
  - "Decay detected" alert if any shadow significantly outperforms
  - Plain-English summary suitable for email
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from trader import variants  # noqa: F401  (registers variants on import)
from trader.ab import get_live, get_shadows
from trader.journal import _conn

# Decay alert thresholds
SHADOW_OUTPERFORM_THRESHOLD = 0.2  # Sharpe edge needed to flag
MIN_DAYS_OF_EVIDENCE = 30          # need at least this many days
LOOKBACK_DAYS = 90                  # how far back to compare


def compute_variant_sharpe(variant_id: str, lookback_days: int = 90) -> dict:
    """Compute realized Sharpe for a variant from its decisions in the journal.

    For LIVE: pulls from `decisions` table (executed orders).
    For shadows: pulls from `shadow_decisions` table.

    Returns dict with sharpe, mean_return, n_days, and the decisions analyzed.
    Returns None if insufficient data.
    """
    con = _conn()
    cur = con.cursor()

    # Determine table + query based on variant_id
    live = get_live()
    is_live = (live is not None and variant_id == live.variant_id)

    if is_live:
        # LIVE: use daily_snapshot equity changes
        cur.execute(
            """SELECT date, equity FROM daily_snapshot
               ORDER BY date DESC LIMIT ?""",
            (lookback_days,)
        )
        rows = cur.fetchall()
        if len(rows) < MIN_DAYS_OF_EVIDENCE:
            return {"variant_id": variant_id, "n_days": len(rows),
                    "insufficient": True, "is_live": True}
        rows.reverse()
        equities = [float(r[1]) for r in rows if r[1]]
        if len(equities) < 2:
            return {"variant_id": variant_id, "insufficient": True, "is_live": True}
        rets = [equities[i] / equities[i-1] - 1 for i in range(1, len(equities))]
    else:
        # SHADOW: use shadow_decisions to reconstruct hypothetical equity curve.
        # Each shadow_decisions row has targets_json (the picks). We replay
        # forward-returns the same way replay.py does.
        from trader.replay import replay_decisions
        try:
            shadow_eq = replay_decisions(variant_id, lookback_days=lookback_days)
        except Exception as e:
            return {"variant_id": variant_id, "error": f"replay failed: {e}"}
        if shadow_eq is None or len(shadow_eq) < MIN_DAYS_OF_EVIDENCE:
            return {"variant_id": variant_id, "n_days": len(shadow_eq) if shadow_eq is not None else 0,
                    "insufficient": True, "is_live": False}
        rets = [shadow_eq[i] / shadow_eq[i-1] - 1 for i in range(1, len(shadow_eq))]

    if len(rets) < 5:
        return {"variant_id": variant_id, "insufficient": True}
    n = len(rets)
    mean = sum(rets) / n
    variance = sum((r - mean) ** 2 for r in rets) / max(1, n - 1)
    std = variance ** 0.5
    sharpe = (mean * 252) / (std * math.sqrt(252)) if std > 0 else 0.0
    return {
        "variant_id": variant_id,
        "n_days": n,
        "sharpe": sharpe,
        "mean_return": mean,
        "is_live": is_live,
    }


def main():
    print("=" * 80)
    print("STRATEGY DECAY EARLY-WARNING CHECK")
    print("=" * 80)
    print(f"Lookback: {LOOKBACK_DAYS} days. Min evidence: {MIN_DAYS_OF_EVIDENCE} days.")
    print(f"Alert threshold: shadow Sharpe > LIVE Sharpe + {SHADOW_OUTPERFORM_THRESHOLD}")
    print()

    live = get_live()
    if live is None:
        print("No LIVE variant registered. Aborting.")
        return 1

    print(f"LIVE: {live.variant_id}")
    live_metrics = compute_variant_sharpe(live.variant_id, LOOKBACK_DAYS)
    if live_metrics.get("insufficient"):
        print(f"  insufficient data ({live_metrics.get('n_days', 0)} days < {MIN_DAYS_OF_EVIDENCE})")
        print("\nNeed more days of accumulated journal data before decay check is meaningful.")
        return 0
    print(f"  n_days={live_metrics['n_days']}  Sharpe={live_metrics['sharpe']:>+5.2f}")
    live_sharpe = live_metrics["sharpe"]

    print(f"\nSHADOWS ({len(get_shadows())}):")
    decay_signals = []
    for v in get_shadows():
        m = compute_variant_sharpe(v.variant_id, LOOKBACK_DAYS)
        if m.get("error"):
            print(f"  {v.variant_id:40s}  ERROR: {m['error']}")
            continue
        if m.get("insufficient"):
            print(f"  {v.variant_id:40s}  insufficient ({m.get('n_days', 0)} days)")
            continue
        edge = m["sharpe"] - live_sharpe
        flag = "  ⚠ DECAY" if edge > SHADOW_OUTPERFORM_THRESHOLD else ""
        print(f"  {v.variant_id:40s}  n={m['n_days']:>4}  Sharpe={m['sharpe']:>+5.2f}  edge={edge:>+5.2f}{flag}")
        if edge > SHADOW_OUTPERFORM_THRESHOLD:
            decay_signals.append((v.variant_id, m["sharpe"], edge))

    print()
    if decay_signals:
        print(f"⚠ DECAY DETECTED: {len(decay_signals)} shadow(s) outperforming LIVE by > {SHADOW_OUTPERFORM_THRESHOLD} Sharpe")
        for vid, s, edge in decay_signals:
            print(f"  - {vid}: Sharpe {s:+.2f} (edge {edge:+.2f})")
        print("\nRecommendation: review whether LIVE strategy needs updating.")
        print("Promotion gate: shadow must dominate over ≥30 days AND pass paired_test.")
        return 2  # exit code 2 = decay flag
    else:
        print("✓ No shadow significantly outperforms LIVE. Strategy not decaying.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
