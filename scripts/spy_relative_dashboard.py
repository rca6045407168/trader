"""SPY-relative performance dashboard — the explicit KPI is "beat SPY net return".

Per Richard's directive 2026-04-29: "your goal is to beat sp500 on net return."

This script reframes performance reporting around SPY-relative metrics, not
absolute Sharpe. SPY total return is the bar. Excess CAGR over SPY (after
costs, before tax) is the headline number.

Realistic expectations (from research):
  - AQR live momentum funds: +2-3% annual excess over SPY net of fees
  - Top 15% of active managers (post-fee): +1-3% excess over SPY
  - Anything claiming +10%+ excess is overfit, leveraged, or short window

This dashboard reports:
  1. Current portfolio CAGR vs SPY CAGR (since journal start)
  2. Rolling 30/90-day excess return
  3. Sharpe vs SPY Sharpe (informational only — return is the goal)
  4. Win rate (% of weeks beating SPY)
  5. Max underperformance streak (consecutive months below SPY)
  6. Honest baseline projection: expected annual excess over SPY going forward
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


def main():
    print("=" * 80)
    print("SPY-RELATIVE DASHBOARD — explicit KPI: beat SPY on net return")
    print("=" * 80)
    print()

    snaps = recent_snapshots(days=365)
    if len(snaps) < 5:
        print(f"Only {len(snaps)} snapshots — too early to compare to SPY.")
        return 0

    # Build daily equity series (newest-first → reverse to chronological)
    snaps_chrono = list(reversed(snaps))
    dates = [pd.Timestamp(s["date"]) for s in snaps_chrono]
    equities = [float(s["equity"]) for s in snaps_chrono if s["equity"]]
    if len(equities) != len(dates):
        # Some snapshots have null equity — drop them
        pairs = [(d, e) for d, e in zip(dates, [s["equity"] for s in snaps_chrono]) if e]
        dates = [p[0] for p in pairs]
        equities = [float(p[1]) for p in pairs]

    if len(equities) < 5:
        print(f"Only {len(equities)} valid equity snapshots — insufficient.")
        return 0

    portfolio = pd.Series(equities, index=dates).sort_index()
    portfolio = portfolio[~portfolio.index.duplicated(keep="last")]

    # Fetch SPY over the same window (pad earlier for comparison)
    start = portfolio.index.min().strftime("%Y-%m-%d")
    end = (portfolio.index.max() + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    try:
        spy_df = fetch_history(["SPY"], start=start, end=end)
    except Exception as e:
        print(f"SPY fetch failed: {e}")
        return 1
    if spy_df.empty or "SPY" not in spy_df.columns:
        print("SPY data unavailable.")
        return 1
    spy = spy_df["SPY"].dropna()

    # Align portfolio & SPY on common dates
    common = portfolio.index.intersection(spy.index)
    if len(common) < 5:
        print(f"Only {len(common)} overlapping dates — insufficient.")
        return 0

    portfolio_aligned = portfolio.loc[common]
    spy_aligned = spy.loc[common]

    # Normalize both to start at 100
    portfolio_norm = portfolio_aligned / portfolio_aligned.iloc[0] * 100
    spy_norm = spy_aligned / spy_aligned.iloc[0] * 100

    # Returns
    p_rets = portfolio_aligned.pct_change().dropna()
    s_rets = spy_aligned.pct_change().dropna()
    common_rets = p_rets.index.intersection(s_rets.index)
    p_rets = p_rets.loc[common_rets]
    s_rets = s_rets.loc[common_rets]
    excess_rets = p_rets - s_rets

    # CAGR
    n_days = len(p_rets)
    n_years = n_days / 252.0
    p_total = portfolio_norm.iloc[-1] / 100 - 1
    s_total = spy_norm.iloc[-1] / 100 - 1
    p_cagr = (1 + p_total) ** (1 / max(n_years, 0.01)) - 1
    s_cagr = (1 + s_total) ** (1 / max(n_years, 0.01)) - 1

    # Sharpe
    def _sharpe(r):
        if r.std() == 0:
            return 0
        return (r.mean() * 252) / (r.std() * math.sqrt(252))
    p_sharpe = _sharpe(p_rets)
    s_sharpe = _sharpe(s_rets)

    # Win rate (weekly)
    weekly_p = (1 + p_rets).resample("W").prod() - 1
    weekly_s = (1 + s_rets).resample("W").prod() - 1
    common_weeks = weekly_p.index.intersection(weekly_s.index)
    weekly_p = weekly_p.loc[common_weeks]
    weekly_s = weekly_s.loc[common_weeks]
    win_rate = float((weekly_p > weekly_s).mean()) if len(weekly_p) > 0 else float("nan")

    # Max underperformance streak (consecutive weeks below SPY)
    underperf_weeks = (weekly_p < weekly_s).astype(int).values
    max_streak = 0
    current_streak = 0
    for u in underperf_weeks:
        if u:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0

    print(f"Window: {portfolio.index.min().date()} → {portfolio.index.max().date()}  "
          f"({n_days} trading days, ~{n_years:.2f} years)")
    print()
    print(f"{'METRIC':<35s} {'PORTFOLIO':>12s} {'SPY':>12s} {'EXCESS':>12s}")
    print("-" * 80)
    print(f"{'Total return':<35s} {p_total*100:>+11.2f}% {s_total*100:>+11.2f}% {(p_total-s_total)*100:>+11.2f}pp")
    print(f"{'CAGR (annualized)':<35s} {p_cagr*100:>+11.2f}% {s_cagr*100:>+11.2f}% {(p_cagr-s_cagr)*100:>+11.2f}pp")
    print(f"{'Sharpe':<35s} {p_sharpe:>+11.2f}  {s_sharpe:>+11.2f}  {p_sharpe-s_sharpe:>+11.2f}")
    print()
    print(f"Weekly win rate vs SPY:        {win_rate*100:>5.1f}%  ({(weekly_p > weekly_s).sum()}/{len(weekly_p)} weeks)")
    print(f"Max underperformance streak:   {max_streak} consecutive weeks below SPY")
    print()

    # Headline verdict
    if p_cagr > s_cagr:
        diff = (p_cagr - s_cagr) * 100
        print(f"✓ BEATING SPY by {diff:+.2f}pp annualized")
    else:
        diff = (s_cagr - p_cagr) * 100
        print(f"✗ TRAILING SPY by {diff:+.2f}pp annualized")

    # Honest expectations baseline
    print()
    print("HONEST BASELINE (from v3.x audit):")
    print("  - Realistic edge over SPY: +2-4% annual after costs (per AQR live momentum funds)")
    print("  - Worst-case underperformance window: ~6-12 months trailing")
    print("  - +20%+ annual excess is implausible for retail systematic strategies")
    print()

    if n_days < 60:
        print(f"⚠ ONLY {n_days} TRADING DAYS — these numbers are essentially noise.")
        print("  90+ days minimum before any meaningful conclusion.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
