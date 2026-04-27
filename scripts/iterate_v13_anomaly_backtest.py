"""v1.7 — actually backtest the anomaly detectors instead of trusting citations.

For each documented anomaly, run a 10-year SPY/IWM backtest with the precise
trade rule encoded in src/trader/anomalies.py. Compare measured effect to
the published claim. Update confidence ratings + expected_alpha_bps based on
what the data actually shows in 2015-2025.

If the empirical effect is materially smaller than published, the anomaly is
either (a) crowded out, (b) regime-dependent, or (c) the original paper was
overfit. In any case, the deployed system should de-rate it.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd
from datetime import date
from trader.data import fetch_history
from trader.anomalies import KNOWN_FOMC_DATES_2026, _third_friday_of_month

START = "2015-01-01"
END = "2025-04-30"


# Hand-curated FOMC announcement dates 2015-2025 (8 per year, second day of two-day meetings)
# Source: Federal Reserve historical FOMC schedule
FOMC_DATES = [
    "2015-01-28", "2015-03-18", "2015-04-29", "2015-06-17", "2015-07-29", "2015-09-17", "2015-10-28", "2015-12-16",
    "2016-01-27", "2016-03-16", "2016-04-27", "2016-06-15", "2016-07-27", "2016-09-21", "2016-11-02", "2016-12-14",
    "2017-02-01", "2017-03-15", "2017-05-03", "2017-06-14", "2017-07-26", "2017-09-20", "2017-11-01", "2017-12-13",
    "2018-01-31", "2018-03-21", "2018-05-02", "2018-06-13", "2018-08-01", "2018-09-26", "2018-11-08", "2018-12-19",
    "2019-01-30", "2019-03-20", "2019-05-01", "2019-06-19", "2019-07-31", "2019-09-18", "2019-10-30", "2019-12-11",
    "2020-01-29", "2020-03-15", "2020-04-29", "2020-06-10", "2020-07-29", "2020-09-16", "2020-11-05", "2020-12-16",
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16", "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15", "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19",
]


def backtest_pre_fomc(spy_close):
    """Trade: long SPY at close T-1 (day before FOMC), exit at close T (FOMC day).
    Lucca-Moench 2015 claim: +49bps avg.
    """
    print("\n=== PRE-FOMC DRIFT (Lucca-Moench 2015 claim: +49bps) ===")
    returns = []
    for fomc_str in FOMC_DATES:
        fomc = pd.Timestamp(fomc_str)
        # Find T-1 (last trading day before fomc) and T (close on fomc day)
        idx = spy_close.index.get_indexer([fomc], method="ffill")[0]
        if idx <= 0:
            continue
        try:
            entry = spy_close.iloc[idx - 1]
            exit_p = spy_close.iloc[idx]
            r = exit_p / entry - 1
            returns.append(r)
        except IndexError:
            continue
    s = pd.Series(returns)
    print(f"  N={len(s)} FOMC days  mean={s.mean()*1e4:+.1f}bps  median={s.median()*1e4:+.1f}bps  win={float((s>0).mean()):.1%}")
    print(f"  Sharpe (single-day, ann) = {s.mean()/s.std()*np.sqrt(252):.2f}")
    if s.mean() > 0.003:
        print(f"  VERDICT: published claim HOLDS in 2015-2025 (+{s.mean()*1e4:.0f}bps avg)")
    elif s.mean() > 0:
        print(f"  VERDICT: weakened but still positive ({s.mean()*1e4:+.0f}bps vs +49bps claim)")
    else:
        print(f"  VERDICT: anomaly absent in 2015-2025")
    return s


def backtest_turn_of_month(spy_close):
    """Trade: long SPY from close of last trading day of month T-1 to close of T+3
    (i.e. last day of month through 3rd trading day of next month).
    Etf 2008 claim: +70bps cumulative -1 to +3.
    """
    print("\n=== TURN-OF-MONTH (Etf 2008 claim: +70bps cumulative -1 to +3) ===")
    spy = spy_close.copy()
    spy.index = pd.DatetimeIndex(spy.index)
    daily_ret = spy.pct_change().dropna()

    # For each month-end, get the 5 trading days from -1 to +3
    monthly_ends = spy.resample("ME").last().index
    cum_returns = []
    for me in monthly_ends:
        # Find last trading day on or before me
        end_idx = spy.index.get_indexer([me], method="ffill")[0]
        # Window: end_idx (last of month) through end_idx + 3 (3 trading days into next month)
        if end_idx + 3 >= len(spy):
            continue
        window_returns = daily_ret.iloc[end_idx + 1: end_idx + 4]  # next 3 trading days
        cum_ret = (1 + window_returns).prod() - 1
        cum_returns.append(cum_ret)

    s = pd.Series(cum_returns)
    print(f"  N={len(s)} month-ends  mean={s.mean()*1e4:+.1f}bps  median={s.median()*1e4:+.1f}bps  win={float((s>0).mean()):.1%}")
    print(f"  vs random 3-day SPY return mean={daily_ret.mean()*3*1e4:+.1f}bps")
    if s.mean() > 0.001:
        print(f"  VERDICT: anomaly persists (+{s.mean()*1e4:.0f}bps for first 3 trading days of month)")
    else:
        print(f"  VERDICT: anomaly absent or negative")
    return s


def backtest_opex_week(spy_close):
    """Trade: long SPY from close Friday before OPEX week to close OPEX Friday.
    Stoll-Whaley 1987 claim: +20bps over the week (M-W positive, Th-F mixed).
    """
    print("\n=== OPEX WEEK (claim: +20bps Mon-Wed dealer flow) ===")
    spy = spy_close.copy()
    spy.index = pd.DatetimeIndex(spy.index)

    week_returns = []
    for year in range(2015, 2026):
        for month in range(1, 13):
            third_fri = _third_friday_of_month(year, month)
            third_fri = pd.Timestamp(third_fri)
            if third_fri > spy.index[-1] or third_fri < spy.index[0]:
                continue
            # Trade Mon-Thu of OPEX week (4 trading days)
            try:
                fri_idx = spy.index.get_indexer([third_fri], method="ffill")[0]
                # Mon = fri_idx - 4 (4 trading days before Friday)
                if fri_idx - 4 < 0:
                    continue
                entry = spy.iloc[fri_idx - 4]
                exit_p = spy.iloc[fri_idx - 1]  # Thu close (skip Friday volatility)
                r = exit_p / entry - 1
                week_returns.append(r)
            except IndexError:
                continue

    s = pd.Series(week_returns)
    print(f"  N={len(s)} OPEX weeks  Mon-Thu mean={s.mean()*1e4:+.1f}bps  median={s.median()*1e4:+.1f}bps  win={float((s>0).mean()):.1%}")
    if s.mean() > 0.001:
        print(f"  VERDICT: anomaly persists")
    else:
        print(f"  VERDICT: weak or absent in 2015-2025")
    return s


def backtest_baseline(spy_close):
    """For comparison: average daily SPY return."""
    daily_ret = spy_close.pct_change().dropna()
    print("\n=== BASELINE (daily SPY mean) ===")
    print(f"  Mean daily return: {daily_ret.mean()*1e4:+.1f}bps  ann ~{daily_ret.mean()*252*100:.1f}%")
    print(f"  Mean 3-day return: {daily_ret.mean()*3*1e4:+.1f}bps")
    print(f"  Mean 5-day return: {daily_ret.mean()*5*1e4:+.1f}bps")


def main():
    print("=" * 78)
    print("v1.7  —  ANOMALY BACKTESTS (verify published claims on 2015-2025 SPY)")
    print("=" * 78)

    spy = fetch_history(["SPY"], start=START, end=END)["SPY"]
    print(f"\nLoaded SPY {len(spy)} days {spy.index[0].date()} → {spy.index[-1].date()}")

    backtest_baseline(spy)
    backtest_pre_fomc(spy)
    backtest_turn_of_month(spy)
    backtest_opex_week(spy)

    print("\n" + "=" * 78)
    print("NEXT STEPS")
    print("=" * 78)
    print("  - For anomalies that BEAT baseline: keep deployed at published bps")
    print("  - For anomalies that MATCH baseline: de-rate to 'low' confidence")
    print("  - For anomalies BELOW baseline: remove from production scanner")


if __name__ == "__main__":
    main()
