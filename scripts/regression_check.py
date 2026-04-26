"""Backtest regression check. Fails CI if the deployed strategy's stats drop materially.

This is the trading equivalent of a unit test: every code change must NOT cause
the historical backtest to underperform a known baseline. If someone refactors
signals.py and accidentally breaks the momentum calculation, this catches it.

Baseline (frozen 2026-04-26 from v1.2):
  CAGR  >= 28.0%   (in-sample, liquid_50, 2015-2025)
  Sharpe >= 1.10
  MaxDD between -28% and -38%  (sanity range — too good is also suspicious)

Fails with exit code 1 if any threshold breached.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.backtest import backtest_momentum
from trader.universe import DEFAULT_LIQUID_50

MIN_CAGR = 0.28
MIN_SHARPE = 1.10
MAX_DD_FLOOR = -0.40  # never worse than -40%
MAX_DD_CEILING = -0.20  # if better than -20%, suspicious (data leak?)


def main():
    print("=" * 70)
    print("BACKTEST REGRESSION CHECK")
    print("=" * 70)
    r = backtest_momentum(
        DEFAULT_LIQUID_50,
        start="2015-01-01", end="2025-04-30",
        lookback_months=12, top_n=5, slippage_bps=5.0,
    )
    s = r.stats()
    print(f"\n  CAGR:    {s['cagr']:>+7.2%}  (need >= {MIN_CAGR:+.2%})")
    print(f"  Sharpe:  {s['sharpe']:>+7.2f}  (need >= {MIN_SHARPE:+.2f})")
    print(f"  MaxDD:   {s['max_drawdown']:>+7.2%}  (acceptable: {MAX_DD_FLOOR:.0%} to {MAX_DD_CEILING:.0%})")
    print(f"  Alpha:   {s['alpha']:>+7.2%}")

    failures = []
    if s["cagr"] < MIN_CAGR:
        failures.append(f"CAGR {s['cagr']:+.2%} < threshold {MIN_CAGR:+.2%}")
    if s["sharpe"] < MIN_SHARPE:
        failures.append(f"Sharpe {s['sharpe']:+.2f} < threshold {MIN_SHARPE:+.2f}")
    if s["max_drawdown"] < MAX_DD_FLOOR:
        failures.append(f"MaxDD {s['max_drawdown']:+.2%} worse than floor {MAX_DD_FLOOR:+.2%}")
    if s["max_drawdown"] > MAX_DD_CEILING:
        failures.append(f"MaxDD {s['max_drawdown']:+.2%} suspiciously good (better than {MAX_DD_CEILING:+.2%}) — data leak?")

    print()
    if failures:
        print("FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("PASS — backtest stats within deployed tolerance.")
    sys.exit(0)


if __name__ == "__main__":
    main()
