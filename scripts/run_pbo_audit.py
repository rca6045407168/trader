"""Run PBO on the deployed strategy variants.

Returns matrix is (months, strategy_variants). We compare:
  - 6m / top-5
  - 6m / top-10
  - 9m / top-5
  - 12m / top-5  (deployed)
  - 12m / top-10
  - 18m / top-5
  - 24m / top-5

All on the same liquid-50 universe. PBO answers: of these 7, was the choice
of '12m / top-5' justified or just lucky?
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd
from trader.backtest import backtest_momentum
from trader.universe import DEFAULT_LIQUID_50
from trader.pbo import pbo_from_returns


def main():
    print("=" * 78)
    print("PROBABILITY OF BACKTEST OVERFITTING (PBO) AUDIT")
    print("=" * 78)

    configs = [(6, 5), (6, 10), (9, 5), (12, 5), (12, 10), (18, 5), (24, 5)]
    columns = []
    series_dict = {}
    for L, N in configs:
        label = f"L{L}_N{N}"
        r = backtest_momentum(
            DEFAULT_LIQUID_50, start="2015-01-01", end="2025-04-30",
            lookback_months=L, top_n=N,
        )
        series_dict[label] = r.monthly_returns
        columns.append(label)

    df = pd.DataFrame(series_dict).dropna()
    print(f"\nReturns matrix: {df.shape[0]} months × {df.shape[1]} strategies")
    print(f"Columns: {list(df.columns)}")

    print()
    result = pbo_from_returns(df, n_partitions=8)
    for k, v in result.items():
        print(f"  {k:20s}  {v}")

    print()
    if result["verdict"] == "OK":
        print("  ✅ PBO < 20%: strategy selection was robust, not overfit")
    elif result["verdict"] == "caution":
        print("  ⚠️  PBO 20-50%: borderline. Selection adds some real value.")
    elif result["verdict"] == "overfit":
        print("  ❌ PBO > 50%: best in-sample tends to underperform median OOS — overfit.")
    else:
        print(f"  PBO computation could not complete: {result['verdict']}")


if __name__ == "__main__":
    main()
