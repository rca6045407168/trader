"""v0.8b — fixed crash test. Use 2 years prior history as warmup so weights are populated.

Compares strategy CAGR/MaxDD/Sharpe to SPY's during the crash + 6mo recovery,
so we can see whether momentum gets crushed worse, equal, or better than the index.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd
from trader.backtest import backtest_momentum
from trader.universe import DEFAULT_LIQUID_50


def _slice_period(result, start, end):
    """Pull just the equity slice within [start, end] for a fair crash-period stat."""
    eq = result.equity
    bench = result.benchmark_equity
    eq_p = eq[(eq.index >= start) & (eq.index <= end)]
    bench_p = bench[(bench.index >= start) & (bench.index <= end)]
    if len(eq_p) < 6:
        return None
    eq_p = eq_p / eq_p.iloc[0] * 100_000
    bench_p = bench_p / bench_p.iloc[0] * 100_000
    cagr = (eq_p.iloc[-1] / eq_p.iloc[0]) ** (12 / len(eq_p)) - 1
    bench_cagr = (bench_p.iloc[-1] / bench_p.iloc[0]) ** (12 / len(bench_p)) - 1
    dd = (eq_p / eq_p.cummax() - 1).min()
    bench_dd = (bench_p / bench_p.cummax() - 1).min()
    return {
        "cagr": cagr, "bench_cagr": bench_cagr,
        "maxdd": dd, "bench_maxdd": bench_dd,
        "final_strategy": eq_p.iloc[-1], "final_spy": bench_p.iloc[-1],
    }


def main():
    print("=" * 95)
    print("v0.8b — STRATEGY PERFORMANCE THROUGH HISTORICAL CRASHES (with 2y warmup)")
    print("=" * 95)

    crashes = [
        ("2015-Q3 China devaluation",  "2013-01-01", "2015-06-01", "2016-06-30"),
        ("2018-Q4 selloff",             "2016-01-01", "2018-08-01", "2019-06-30"),
        ("2020-Q1 COVID crash",         "2018-01-01", "2020-01-01", "2021-01-31"),
        ("2022 bear market",            "2020-01-01", "2022-01-01", "2023-06-30"),
        ("2025 tariff selloff",         "2023-01-01", "2024-09-01", "2025-04-30"),
    ]

    print(f"\n{'period':30s}  {'window':22s}  {'strat CAGR':>11s}  {'SPY CAGR':>10s}  {'strat MaxDD':>12s}  {'SPY MaxDD':>11s}  {'verdict':>20s}")
    print("-" * 130)
    for name, warmup_start, crash_start, end in crashes:
        try:
            r = backtest_momentum(
                DEFAULT_LIQUID_50,
                start=warmup_start, end=end,
                lookback_months=12, top_n=5,
            )
            s = _slice_period(r, crash_start, end)
            if s is None:
                print(f"  {name:30s}  insufficient data")
                continue
            verdict = "OUTPERFORMS" if s["cagr"] > s["bench_cagr"] else (
                "UNDERPERFORMS" if s["cagr"] < s["bench_cagr"] - 0.02 else "in-line"
            )
            print(
                f"  {name:30s}  {crash_start} → {end[:7]}  "
                f"{s['cagr']:>+10.2%}  {s['bench_cagr']:>+9.2%}  "
                f"{s['maxdd']:>+11.2%}  {s['bench_maxdd']:>+10.2%}  {verdict:>20s}"
            )
        except Exception as e:
            print(f"  {name:30s}  FAILED: {e}")

    print("\nKey context: a momentum strategy is a momentum-of-leaders trade.")
    print("It tends to outperform when leadership is concentrated and PERSISTENT,")
    print("and underperform during regime changes (ie when leaders rotate).")


if __name__ == "__main__":
    main()
