"""[v3.59.2] Backtest the pre-FOMC drift sleeve historically.

Lucca-Moench (2015) original paper: 1994-2011, +49bps SPX from FOMC eve
close → 2pm ET FOMC day. Richard's v1.7 retest 2015-2025: +22bps with
Sharpe 2.35.

This script reproduces the v1.7 retest using yfinance close-to-close
returns on SPY (a slight approximation — paper used intra-day 2pm ET).
Output: per-event return distribution, summary stats, and a 3-gate
report card.

Run:
  python scripts/backtest_fomc_drift.py

Output:
  • prints summary to stdout
  • writes data/fomc_drift_backtest.json with per-event details
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# Historical FOMC dates 2015-2025 (the period covered by the v1.7 retest).
# Source: federalreserve.gov/monetarypolicy/fomccalendars.htm
HISTORICAL_FOMC = [
    date(2015, 1, 28), date(2015, 3, 18), date(2015, 4, 29), date(2015, 6, 17),
    date(2015, 7, 29), date(2015, 9, 17), date(2015, 10, 28), date(2015, 12, 16),
    date(2016, 1, 27), date(2016, 3, 16), date(2016, 4, 27), date(2016, 6, 15),
    date(2016, 7, 27), date(2016, 9, 21), date(2016, 11, 2), date(2016, 12, 14),
    date(2017, 2, 1), date(2017, 3, 15), date(2017, 5, 3), date(2017, 6, 14),
    date(2017, 7, 26), date(2017, 9, 20), date(2017, 11, 1), date(2017, 12, 13),
    date(2018, 1, 31), date(2018, 3, 21), date(2018, 5, 2), date(2018, 6, 13),
    date(2018, 8, 1), date(2018, 9, 26), date(2018, 11, 8), date(2018, 12, 19),
    date(2019, 1, 30), date(2019, 3, 20), date(2019, 5, 1), date(2019, 6, 19),
    date(2019, 7, 31), date(2019, 9, 18), date(2019, 10, 30), date(2019, 12, 11),
    date(2020, 1, 29), date(2020, 3, 18), date(2020, 4, 29), date(2020, 6, 10),
    date(2020, 7, 29), date(2020, 9, 16), date(2020, 11, 5), date(2020, 12, 16),
    date(2021, 1, 27), date(2021, 3, 17), date(2021, 4, 28), date(2021, 6, 16),
    date(2021, 7, 28), date(2021, 9, 22), date(2021, 11, 3), date(2021, 12, 15),
    date(2022, 1, 26), date(2022, 3, 16), date(2022, 5, 4), date(2022, 6, 15),
    date(2022, 7, 27), date(2022, 9, 21), date(2022, 11, 2), date(2022, 12, 14),
    date(2023, 2, 1), date(2023, 3, 22), date(2023, 5, 3), date(2023, 6, 14),
    date(2023, 7, 26), date(2023, 9, 20), date(2023, 11, 1), date(2023, 12, 13),
    date(2024, 1, 31), date(2024, 3, 20), date(2024, 5, 1), date(2024, 6, 12),
    date(2024, 7, 31), date(2024, 9, 18), date(2024, 11, 7), date(2024, 12, 18),
    date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7), date(2025, 6, 18),
    date(2025, 7, 30), date(2025, 9, 17), date(2025, 10, 29), date(2025, 12, 10),
]


def fetch_spy_history(start: str, end: str):
    """Returns DataFrame indexed by date with Close column."""
    import yfinance as yf
    df = yf.download("SPY", start=start, end=end,
                      progress=False, auto_adjust=True)
    if df is None or df.empty:
        return None
    return df


def main():
    print("=== Pre-FOMC drift backtest: 2015-2025 ===")
    df = fetch_spy_history("2014-12-01", "2026-01-15")
    if df is None:
        print("ERROR: yfinance download failed")
        return 1
    closes = df["Close"]
    # Build map: trading_date → close
    closes_dict = {idx.date(): float(closes.loc[idx].iloc[0] if hasattr(closes.loc[idx], 'iloc') else closes.loc[idx])
                    for idx in closes.index}

    def prev_trading_close(d: date) -> tuple[date, float] | None:
        # Walk back up to 5 days to find a trading day
        for delta in range(1, 6):
            cand = d - timedelta(days=delta)
            if cand in closes_dict:
                return cand, closes_dict[cand]
        return None

    def trading_close(d: date) -> tuple[date, float] | None:
        if d in closes_dict:
            return d, closes_dict[d]
        # If FOMC day was a half-day or weekend, walk forward
        for delta in range(1, 4):
            cand = d + timedelta(days=delta)
            if cand in closes_dict:
                return cand, closes_dict[cand]
        return None

    events = []
    for fomc in HISTORICAL_FOMC:
        eve = prev_trading_close(fomc)
        cls = trading_close(fomc)
        if eve is None or cls is None:
            continue
        eve_d, eve_p = eve
        cls_d, cls_p = cls
        ret = (cls_p / eve_p) - 1
        events.append({
            "fomc_date": fomc.isoformat(),
            "eve_close_date": eve_d.isoformat(),
            "eve_close_price": eve_p,
            "fomc_close_date": cls_d.isoformat(),
            "fomc_close_price": cls_p,
            "return_pct": ret * 100,
        })

    if not events:
        print("ERROR: no FOMC events matched against price history")
        return 1

    rets = [e["return_pct"] for e in events]
    n = len(rets)
    mean_pct = statistics.mean(rets)
    median_pct = statistics.median(rets)
    std_pct = statistics.stdev(rets) if n > 1 else 0
    win_rate = sum(1 for r in rets if r > 0) / n
    # Single-event Sharpe (per-event mean / per-event stdev — daily frequency)
    # NOT annualized because each event is a one-day position
    sharpe_per_event = mean_pct / std_pct if std_pct > 0 else 0
    # Sleeve-level annualized: 8 events per year, mean per-event return
    # Annual return = mean × 8 (binary 1-day exposures)
    annual_return_pct = mean_pct * 8
    # Annual vol = std × sqrt(8) (independent events)
    annual_vol_pct = std_pct * math.sqrt(8)
    annual_sharpe = annual_return_pct / annual_vol_pct if annual_vol_pct > 0 else 0

    print(f"\nN events: {n}")
    print(f"Mean per-event return: {mean_pct:+.3f}%")
    print(f"Median: {median_pct:+.3f}%")
    print(f"Stdev: {std_pct:.3f}%")
    print(f"Win rate: {win_rate*100:.1f}%")
    print(f"Single-event Sharpe (mean/std): {sharpe_per_event:.2f}")
    print(f"Sleeve annualized return (8 events/yr): {annual_return_pct:+.2f}%")
    print(f"Sleeve annualized vol: {annual_vol_pct:.2f}%")
    print(f"Sleeve annualized Sharpe: {annual_sharpe:.2f}")

    # 3-gate-style verdict
    print("\n=== 3-gate verdict ===")
    gate1 = mean_pct > 5  # at least 5bp expected per event
    gate2 = win_rate > 0.55  # win rate above coin flip
    gate3 = annual_sharpe > 0.5  # sleeve-level Sharpe above noise floor
    print(f"  Gate 1 (mean > 5bp/event):   {'✅ PASS' if gate1 else '❌ FAIL'}  ({mean_pct*100:.1f}bp)")
    print(f"  Gate 2 (win_rate > 55%):     {'✅ PASS' if gate2 else '❌ FAIL'}  ({win_rate*100:.1f}%)")
    print(f"  Gate 3 (annual Sharpe > 0.5): {'✅ PASS' if gate3 else '❌ FAIL'}  ({annual_sharpe:.2f})")

    out_path = ROOT / "data" / "fomc_drift_backtest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        json.dump({
            "n_events": n,
            "mean_pct": mean_pct,
            "median_pct": median_pct,
            "std_pct": std_pct,
            "win_rate": win_rate,
            "single_event_sharpe": sharpe_per_event,
            "annual_return_pct": annual_return_pct,
            "annual_vol_pct": annual_vol_pct,
            "annual_sharpe": annual_sharpe,
            "gate1_mean_above_5bp": gate1,
            "gate2_win_rate_above_55": gate2,
            "gate3_annual_sharpe_above_0_5": gate3,
            "all_pass": gate1 and gate2 and gate3,
            "events": events,
        }, f, indent=2)
    print(f"\nWritten: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
