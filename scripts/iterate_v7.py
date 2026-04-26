"""v0.7 — fix the bottom-catch exit logic.

H8 showed current brackets (stop -1.5ATR, take +3ATR, trail 1ATR) give back
36% of the edge. Test 4 alternatives:

  A) Time-only 20d, no brackets at all (what the +2.29% baseline assumes)
  B) Time 20d + wide catastrophic stop -3.5 ATR (basically only fires on tail risk)
  C) Time 20d + signal exit when RSI crosses 50 OR price >= 20-day MA
  D) Time 20d + take +4 ATR (cap winners only, no stop)

Pick the highest mean-return-per-trade among A-D.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd
from trader.data import fetch_ohlcv
from trader.signals import atr, rsi
from trader.universe import DEFAULT_LIQUID_50

REPORTS = ROOT / "reports"


def simulate(ohlc, entry_idx, atr_dollar, max_hold=20, mode="A"):
    entry = float(ohlc["Close"].iloc[entry_idx])
    closes_window = ohlc["Close"].iloc[max(0, entry_idx - 20): entry_idx + max_hold + 1]

    for d in range(1, max_hold + 1):
        if entry_idx + d >= len(ohlc):
            return ("time_truncated", d, float(ohlc["Close"].iloc[-1]) / entry - 1)
        bar_low = float(ohlc["Low"].iloc[entry_idx + d])
        bar_high = float(ohlc["High"].iloc[entry_idx + d])
        bar_close = float(ohlc["Close"].iloc[entry_idx + d])

        if mode == "B":  # wide cat-stop only
            stop = entry - 3.5 * atr_dollar
            if bar_low <= stop:
                return ("cat_stop", d, stop / entry - 1)
        elif mode == "C":  # signal exit
            window = ohlc["Close"].iloc[: entry_idx + d + 1]
            ma20 = float(window.rolling(20).mean().iloc[-1])
            if bar_close >= ma20:
                return ("ma_recover", d, bar_close / entry - 1)
            r = rsi(window)
            if r > 50:
                return ("rsi_recover", d, bar_close / entry - 1)
        elif mode == "D":  # take only
            take = entry + 4.0 * atr_dollar
            if bar_high >= take:
                return ("take", d, 4.0 * atr_dollar / entry)

    final = float(ohlc["Close"].iloc[entry_idx + max_hold])
    return ("time", max_hold, final / entry - 1)


def main():
    print("=" * 78)
    print("v0.7 — ALTERNATIVE EXIT RULES FOR BOTTOM-CATCH")
    print("=" * 78)

    triggers = pd.read_csv(REPORTS / "bottom_catch_triggers.csv", parse_dates=["date"])
    triggers = triggers[triggers["score"] >= 0.65].copy()

    results = {m: [] for m in ("A", "B", "C", "D")}
    print(f"Simulating 4 exit modes across {len(triggers)} triggers...")
    for ticker in triggers["ticker"].unique():
        try:
            ohlc = fetch_ohlcv(ticker, start="2014-01-01", end="2025-04-30")
        except Exception:
            continue
        date_to_idx = {d.date(): i for i, d in enumerate(ohlc.index)}
        for _, row in triggers[triggers["ticker"] == ticker].iterrows():
            idx = date_to_idx.get(row["date"].date())
            if idx is None or idx + 25 >= len(ohlc):
                continue
            a = atr(ohlc.iloc[: idx + 1])
            if a <= 0:
                continue
            for m in ("A", "B", "C", "D"):
                _, _, ret = simulate(ohlc, idx, a, mode=m)
                results[m].append(ret)

    print("\n" + "-" * 78)
    print(f"{'mode':5s}  {'description':45s}  {'mean':>8s}  {'win':>6s}  {'std':>6s}")
    print("-" * 78)
    descriptions = {
        "A": "time-only 20d (no brackets)",
        "B": "time + wide cat-stop -3.5 ATR",
        "C": "time + signal exit (MA20 or RSI>50)",
        "D": "time + take +4 ATR (no stop)",
    }
    rows = []
    for m in ("A", "B", "C", "D"):
        s = pd.Series(results[m])
        rows.append((m, descriptions[m], s.mean(), (s > 0).mean(), s.std()))
        print(f"  {m}    {descriptions[m]:45s}  {s.mean():>+7.2%}  {(s > 0).mean():>5.1%}  {s.std():>5.2%}")
    print("-" * 78)
    print(f"  CURRENT (stop-1.5 + take+3 + trail-1):  +1.47%  57.1%  6.39%")
    print(f"  Idealized 20d hold benchmark:           +2.29%  62.5%")

    best = max(rows, key=lambda r: r[2])
    print(f"\n  WINNER: mode {best[0]} — {best[1]}  (mean {best[2]:+.2%}, win {best[3]:.1%})")


if __name__ == "__main__":
    main()
