"""Walk-forward the Pre-FOMC sleeve.

In-sample (TRAIN): 2015-01 to 2020-12 — ~48 FOMC events
Out-of-sample (TEST): 2021-01 to 2025-04 — ~34 FOMC events

For each FOMC date, simulate buying SPY at close T-1 and selling at close T.
Report: in-sample mean / Sharpe, out-sample mean / Sharpe, decay.

Deploy if:
  - OOS mean > +10bps
  - OOS Sharpe (annualized 1-day) > 1.0
  - Decay < 50%
  - Win rate > 55%
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd
import numpy as np
from trader.data import fetch_history

# Same FOMC list used in iterate_v13_anomaly_backtest.py (verified)
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


def compute_prefomc_returns(spy_close: pd.Series, dates: list[str]) -> pd.Series:
    """For each FOMC date, simulate buying at close T-1 (last trading day before)
    and selling at close T. Returns a Series of single-event returns."""
    out = []
    for d in dates:
        ts = pd.Timestamp(d)
        if ts < spy_close.index[0] or ts > spy_close.index[-1]:
            continue
        idx = spy_close.index.get_indexer([ts], method="ffill")[0]
        if idx < 1:
            continue
        entry = spy_close.iloc[idx - 1]
        exit_p = spy_close.iloc[idx]
        out.append(exit_p / entry - 1)
    return pd.Series(out)


def stats(returns: pd.Series, label: str) -> dict:
    n = len(returns)
    if n == 0:
        return {}
    mean = returns.mean()
    std = returns.std()
    win = (returns > 0).mean()
    sharpe_1d = (mean / std * np.sqrt(252)) if std > 0 else 0
    print(f"  {label}: n={n}  mean={mean*1e4:+.1f}bps  win={float(win):.1%}  std={std*1e4:.1f}bps  Sharpe(ann)={sharpe_1d:.2f}")
    return {"n": n, "mean": mean, "std": std, "win": win, "sharpe": sharpe_1d}


def main():
    print("=" * 78)
    print("WALK-FORWARD: PRE-FOMC SLEEVE")
    print("=" * 78)

    spy = fetch_history(["SPY"], start="2014-12-01", end="2025-04-30")["SPY"]
    spy.index = pd.DatetimeIndex(spy.index)

    train_dates = [d for d in FOMC_DATES if d < "2021-01-01"]
    test_dates = [d for d in FOMC_DATES if d >= "2021-01-01"]

    print(f"\nTrain: {len(train_dates)} FOMC events ({train_dates[0]} → {train_dates[-1]})")
    print(f"Test:  {len(test_dates)} FOMC events ({test_dates[0]} → {test_dates[-1]})")

    print("\n--- IN-SAMPLE (TRAIN) ---")
    train_ret = compute_prefomc_returns(spy, train_dates)
    train_s = stats(train_ret, "24h pre-FOMC drift")

    print("\n--- OUT-OF-SAMPLE (TEST) ---")
    test_ret = compute_prefomc_returns(spy, test_dates)
    test_s = stats(test_ret, "24h pre-FOMC drift")

    print("\n--- DECAY ANALYSIS ---")
    decay = (train_s["sharpe"] - test_s["sharpe"]) / train_s["sharpe"] if train_s["sharpe"] != 0 else float("nan")
    print(f"  Sharpe decay: {decay:+.1%}")
    mean_decay = (train_s["mean"] - test_s["mean"]) / train_s["mean"] if train_s["mean"] != 0 else float("nan")
    print(f"  Mean decay:   {mean_decay:+.1%}")

    print("\n--- DEPLOY DECISION ---")
    deploy = (
        test_s["mean"] > 0.001
        and test_s["sharpe"] > 1.0
        and abs(decay) < 0.5
        and test_s["win"] > 0.55
    )
    if deploy:
        print("  ✅ DEPLOY: OOS mean > 10bps AND Sharpe > 1.0 AND decay < 50% AND win > 55%")
        print(f"  Recommended size: 5% of equity per FOMC event (≈8 events/yr = ~40% rebalance turnover/yr)")
        print(f"  Annual contribution: {test_s['mean']*1e4*8:.0f}bps")
    else:
        print("  ⚠️  DO NOT DEPLOY — fails one or more thresholds:")
        print(f"     OOS mean > 10bps:  {test_s['mean']*1e4 > 10}  ({test_s['mean']*1e4:+.1f}bps)")
        print(f"     OOS Sharpe > 1.0:  {test_s['sharpe'] > 1.0}  ({test_s['sharpe']:+.2f})")
        print(f"     Decay < 50%:       {abs(decay) < 0.5}  ({decay:+.1%})")
        print(f"     Win rate > 55%:    {test_s['win'] > 0.55}  ({float(test_s['win']):.1%})")


if __name__ == "__main__":
    main()
