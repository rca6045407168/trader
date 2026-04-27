"""v1.8 — backtest the anomalies I committed to test but didn't:
  - Year-end reversal (Reinganum 1983 claim: +200bps Jan small-cap loser bounce)
  - Sell-in-May / Halloween (Bouman-Jacobsen 2002 claim: -200bps May-Oct vs Nov-Apr)
  - Pre-holiday effect (Ariel 1990 claim: +12bps per pre-holiday day)

USING SPY for the calendar effects, IWM (small-cap proxy) for year-end reversal.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd
import numpy as np
from datetime import date
from trader.data import fetch_history

START = "2015-01-01"
END = "2025-04-30"

US_HOLIDAYS = [
    "2015-01-01", "2015-01-19", "2015-02-16", "2015-04-03", "2015-05-25", "2015-07-03", "2015-09-07",
    "2015-11-26", "2015-12-25",
    "2016-01-01", "2016-01-18", "2016-02-15", "2016-03-25", "2016-05-30", "2016-07-04", "2016-09-05",
    "2016-11-24", "2016-12-26",
    "2017-01-02", "2017-01-16", "2017-02-20", "2017-04-14", "2017-05-29", "2017-07-04", "2017-09-04",
    "2017-11-23", "2017-12-25",
    "2018-01-01", "2018-01-15", "2018-02-19", "2018-03-30", "2018-05-28", "2018-07-04", "2018-09-03",
    "2018-11-22", "2018-12-25",
    "2019-01-01", "2019-01-21", "2019-02-18", "2019-04-19", "2019-05-27", "2019-07-04", "2019-09-02",
    "2019-11-28", "2019-12-25",
    "2020-01-01", "2020-01-20", "2020-02-17", "2020-04-10", "2020-05-25", "2020-07-03", "2020-09-07",
    "2020-11-26", "2020-12-25",
    "2021-01-01", "2021-01-18", "2021-02-15", "2021-04-02", "2021-05-31", "2021-07-05", "2021-09-06",
    "2021-11-25", "2021-12-24",
    "2022-01-17", "2022-02-21", "2022-04-15", "2022-05-30", "2022-06-20", "2022-07-04", "2022-09-05",
    "2022-11-24", "2022-12-26",
    "2023-01-02", "2023-01-16", "2023-02-20", "2023-04-07", "2023-05-29", "2023-06-19", "2023-07-04",
    "2023-09-04", "2023-11-23", "2023-12-25",
    "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27", "2024-06-19", "2024-07-04",
    "2024-09-02", "2024-11-28", "2024-12-25",
]


def backtest_year_end_reversal():
    """Long IWM (small-cap) from Dec 20 close to Jan 31 close. Reinganum 1983 claim: +200bps."""
    print("\n=== YEAR-END REVERSAL (Reinganum 1983 claim: +200bps Jan small-cap)")
    iwm = fetch_history(["IWM"], start=START, end=END)["IWM"]
    iwm.index = pd.DatetimeIndex(iwm.index)
    returns = []
    for year in range(2015, 2025):
        entry_date = pd.Timestamp(f"{year}-12-20")
        exit_date = pd.Timestamp(f"{year+1}-01-31")
        try:
            entry_idx = iwm.index.get_indexer([entry_date], method="bfill")[0]
            exit_idx = iwm.index.get_indexer([exit_date], method="ffill")[0]
            if entry_idx < 0 or exit_idx <= entry_idx:
                continue
            r = iwm.iloc[exit_idx] / iwm.iloc[entry_idx] - 1
            returns.append(r)
        except Exception:
            continue
    s = pd.Series(returns)
    print(f"  N={len(s)} years  mean={s.mean()*1e4:+.0f}bps  median={s.median()*1e4:+.0f}bps  win={float((s>0).mean()):.0%}")
    if s.mean() > 0.005:
        print(f"  VERDICT: anomaly persists ({s.mean()*1e4:+.0f}bps avg)")
    elif s.mean() > 0:
        print(f"  VERDICT: weak ({s.mean()*1e4:+.0f}bps vs +200bps claim)")
    else:
        print(f"  VERDICT: NO EDGE in 2015-2025 — anomaly is dead")
    return s


def backtest_sell_in_may():
    """Long SPY May-Oct vs Nov-Apr. Bouman-Jacobsen claim: -200bps differential."""
    print("\n=== SELL-IN-MAY (Bouman-Jacobsen 2002 claim: Nov-Apr beats May-Oct by 200bps)")
    spy = fetch_history(["SPY"], start="2014-01-01", end=END)["SPY"]
    spy.index = pd.DatetimeIndex(spy.index)
    monthly = spy.resample("ME").last().pct_change().dropna()
    monthly.index = pd.DatetimeIndex(monthly.index)
    summer = monthly[monthly.index.month.isin([5, 6, 7, 8, 9, 10])]
    winter = monthly[monthly.index.month.isin([11, 12, 1, 2, 3, 4])]
    print(f"  N_summer={len(summer)} mean={summer.mean()*100:+.2f}%/mo  N_winter={len(winter)} mean={winter.mean()*100:+.2f}%/mo")
    print(f"  Differential: winter beats summer by {(winter.mean() - summer.mean())*100:+.2f}%/mo  (annualized {(winter.mean() - summer.mean())*6*100:+.2f}%)")
    if winter.mean() - summer.mean() > 0.005:
        print(f"  VERDICT: anomaly persists")
    else:
        print(f"  VERDICT: weak/dead in 2015-2025")
    return summer, winter


def backtest_pre_holiday():
    """Long SPY at close T-1 (day before holiday), exit at close T+1 (post-holiday). Ariel 1990 claim: +12bps avg."""
    print("\n=== PRE-HOLIDAY (Ariel 1990 claim: +12bps day before holiday)")
    spy = fetch_history(["SPY"], start=START, end=END)["SPY"]
    spy.index = pd.DatetimeIndex(spy.index)
    daily_ret = spy.pct_change().dropna()
    pre_holiday_returns = []
    for h in US_HOLIDAYS:
        h_date = pd.Timestamp(h)
        if h_date < daily_ret.index[0] or h_date > daily_ret.index[-1]:
            continue
        # Last trading day BEFORE the holiday
        idx = daily_ret.index.get_indexer([h_date], method="ffill")[0]
        if idx < 0:
            continue
        pre_holiday_returns.append(daily_ret.iloc[idx])
    s = pd.Series(pre_holiday_returns)
    print(f"  N={len(s)} pre-holiday days  mean={s.mean()*1e4:+.1f}bps  median={s.median()*1e4:+.1f}bps  win={float((s>0).mean()):.1%}")
    print(f"  vs random 1-day SPY mean: {daily_ret.mean()*1e4:+.1f}bps")
    delta = s.mean() - daily_ret.mean()
    if delta > 0.0005:
        print(f"  VERDICT: pre-holiday excess return {delta*1e4:+.1f}bps over baseline (anomaly persists)")
    else:
        print(f"  VERDICT: no excess return over baseline ({delta*1e4:+.1f}bps)")
    return s


def main():
    print("=" * 78)
    print("v1.8  —  EXTENDED ANOMALY BACKTESTS")
    print("=" * 78)
    backtest_year_end_reversal()
    backtest_sell_in_may()
    backtest_pre_holiday()


if __name__ == "__main__":
    main()
