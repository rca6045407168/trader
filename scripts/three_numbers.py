"""Three numbers — the minimal weekly KPI dashboard.

Per best practices: don't read 30 metrics. Read 3.

  1. EXCESS CAGR over SPY  (the explicit KPI per Richard 2026-04-29)
  2. WORST DRAWDOWN from peak  (behavioral threshold check)
  3. SHARPE  (informational — return is the goal)

Designed to be a 30-second weekly read. No tables, no charts, no narrative.
Just three numbers.

Usage:
  python scripts/three_numbers.py

Run weekly (e.g., Sunday evening). If any number is meaningfully outside the
backtest band, that's when you actually look deeper.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from trader.journal import recent_snapshots
from trader.data import fetch_history


# Backtest expectation bands (PIT-corrected, post-v3.25)
EXPECTED_EXCESS_PP = 2.0   # Realistic edge over SPY: +2-4pp/yr per AQR live funds
EXPECTED_WORST_DD = -33.0  # PIT-honest worst-DD
EXPECTED_SHARPE = 0.96     # PIT-honest Sharpe


def main():
    snaps = recent_snapshots(days=365)
    if len(snaps) < 5:
        print(f"Only {len(snaps)} snapshots. Strategy still warming up.")
        return 0

    chrono = list(reversed(snaps))
    dates = [pd.Timestamp(s["date"]) for s in chrono]
    equities = [float(s["equity"]) for s in chrono if s["equity"]]
    if len(equities) < 5:
        print("Insufficient equity data.")
        return 0

    portfolio = pd.Series(equities, index=dates).sort_index()
    portfolio = portfolio[~portfolio.index.duplicated(keep="last")]

    # SPY benchmark
    start = portfolio.index.min().strftime("%Y-%m-%d")
    end = (portfolio.index.max() + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    try:
        spy_df = fetch_history(["SPY"], start=start, end=end)
        spy = spy_df["SPY"].dropna() if "SPY" in spy_df.columns else pd.Series(dtype=float)
    except Exception:
        spy = pd.Series(dtype=float)

    common = portfolio.index.intersection(spy.index)
    if len(common) < 5:
        print(f"Only {len(common)} overlapping days with SPY. Continue paper-test.")
        return 0

    p_aligned = portfolio.loc[common]
    s_aligned = spy.loc[common]

    p_rets = p_aligned.pct_change().dropna()
    s_rets = s_aligned.pct_change().dropna()
    common_rets = p_rets.index.intersection(s_rets.index)
    p_rets = p_rets.loc[common_rets]
    s_rets = s_rets.loc[common_rets]

    n_days = len(p_rets)
    n_years = max(n_days / 252.0, 0.01)
    p_total = float(p_aligned.iloc[-1] / p_aligned.iloc[0] - 1)
    s_total = float(s_aligned.iloc[-1] / s_aligned.iloc[0] - 1)
    p_cagr = (1 + p_total) ** (1 / n_years) - 1
    s_cagr = (1 + s_total) ** (1 / n_years) - 1
    excess_cagr_pp = (p_cagr - s_cagr) * 100

    # Worst drawdown
    cummax = p_aligned.cummax()
    dd_pct = (p_aligned / cummax - 1) * 100
    worst_dd_pp = float(dd_pct.min())

    # Sharpe
    if p_rets.std() > 0:
        sharpe = (p_rets.mean() * 252) / (p_rets.std() * math.sqrt(252))
    else:
        sharpe = 0.0

    # Output
    print()
    print(f"  Window: {p_aligned.index.min().date()} → {p_aligned.index.max().date()}  ({n_days} trading days)")
    print()
    print(f"  ┌─────────────────────────────────────────────────────────────┐")
    print(f"  │  THREE NUMBERS                                              │")
    print(f"  ├─────────────────────────────────────────────────────────────┤")
    excess_marker = "✓" if excess_cagr_pp > 0 else "⚠" if excess_cagr_pp > -5 else "✗"
    dd_marker = "✓" if worst_dd_pp > -15 else "⚠" if worst_dd_pp > -25 else "✗"
    sharpe_marker = "✓" if sharpe > 0.5 else "⚠" if sharpe > 0 else "✗"
    print(f"  │  {excess_marker}  Excess CAGR over SPY:  {excess_cagr_pp:>+8.2f} pp/yr  (target: ≥+2pp)   │")
    print(f"  │  {dd_marker}  Worst drawdown:        {worst_dd_pp:>+8.2f} pp     (limit: ≥-33pp)   │")
    print(f"  │  {sharpe_marker}  Sharpe:                {sharpe:>+8.2f}        (target: ≥+0.7)    │")
    print(f"  └─────────────────────────────────────────────────────────────┘")
    print()

    # Sanity check vs backtest band
    issues = []
    if n_days >= 60:
        if excess_cagr_pp < -10:
            issues.append(f"Excess CAGR {excess_cagr_pp:+.1f}pp is materially below backtest band — strategy may be decaying or in a bad regime.")
        if worst_dd_pp < EXPECTED_WORST_DD - 5:
            issues.append(f"Worst-DD {worst_dd_pp:+.1f}pp exceeds backtest worst-case ({EXPECTED_WORST_DD:+.1f}pp). Behavioral pre-commit triggered.")
        if sharpe < 0:
            issues.append(f"Sharpe {sharpe:+.2f} is negative over {n_days} days. Investigate.")

    if issues:
        print("  ⚠ ALERTS:")
        for i in issues:
            print(f"    - {i}")
    elif n_days < 60:
        print(f"  Only {n_days} days. Numbers stabilize after ~60 days.")
    else:
        print("  All three numbers within expected band. Continue paper-test.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
