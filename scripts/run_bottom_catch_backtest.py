"""Edge-test the bottom-catch signal.

For every (stock, date) where the bottom-catch composite score crosses
`min_score`, record the forward return over N days. Aggregate to test whether
the signal carries actual edge — not a strategy backtest, a *signal validity* test.

If forward returns are positive on average AND positive in a majority of
trials, the signal has edge. If returns are 50/50, it's noise.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd
from trader.data import fetch_ohlcv
from trader.signals import bottom_catch_score
from trader.universe import DEFAULT_LIQUID_50


def forward_returns_at_triggers(
    ticker: str, start: str, end: str,
    min_score: float = 0.55, horizons_days: tuple[int, ...] = (5, 10, 20, 60),
) -> list[dict]:
    """Walk OHLCV day-by-day, fire the signal, record forward returns."""
    try:
        df = fetch_ohlcv(ticker, start=start, end=end)
    except Exception as e:
        return []
    if len(df) < 252 + max(horizons_days):
        return []

    out = []
    # Walk forward: at each date d (need 220+ days of history), compute the
    # signal using only data up to and INCLUDING d. No look-ahead.
    closes = df["Close"]
    for i in range(252, len(df) - max(horizons_days)):
        window = df.iloc[: i + 1]
        score, comp = bottom_catch_score(window)
        if score < min_score:
            continue
        entry_price = float(closes.iloc[i])
        rec = {"ticker": ticker, "date": df.index[i], "score": score, **comp,
               "entry": entry_price}
        for h in horizons_days:
            exit_price = float(closes.iloc[i + h])
            rec[f"ret_{h}d"] = (exit_price / entry_price) - 1
        out.append(rec)
    return out


def main():
    print("=" * 78)
    print("BOTTOM-CATCH SIGNAL EDGE TEST  — forward returns when the trigger fires")
    print("  Universe: liquid 50  |  Period: 2015-2025  |  min_score=0.55")
    print("=" * 78)

    all_triggers = []
    for t in DEFAULT_LIQUID_50:
        recs = forward_returns_at_triggers(t, start="2014-01-01", end="2025-04-30")
        if recs:
            print(f"  {t:6s}: {len(recs)} triggers")
        all_triggers.extend(recs)

    if not all_triggers:
        print("\nNo triggers across the universe. Signal threshold may be too strict.")
        return

    df = pd.DataFrame(all_triggers)
    print(f"\nTotal triggers across universe: {len(df)}")
    print(f"Trigger rate: {len(df) / (len(DEFAULT_LIQUID_50) * 252 * 10):.4%} of (stock, day) pairs")

    print("\n--- FORWARD RETURNS (averaged across all triggers) ---")
    print(f"{'horizon':>8s}  {'mean':>8s}  {'median':>8s}  {'win_rate':>10s}  {'sharpe(ann)':>13s}")
    for h in (5, 10, 20, 60):
        col = f"ret_{h}d"
        mean = df[col].mean()
        median = df[col].median()
        win = (df[col] > 0).mean()
        ann_factor = (252 / h) ** 0.5
        sharpe = (mean / df[col].std()) * ann_factor if df[col].std() > 0 else 0
        print(f"  {h:>3d}d   {mean:>+7.2%}  {median:>+7.2%}  {win:>9.1%}   {sharpe:>+12.2f}")

    print("\n--- BREAKDOWN BY SCORE BUCKET (20-day forward) ---")
    df["bucket"] = pd.cut(df["score"], bins=[0.55, 0.65, 0.75, 0.85, 1.0])
    g = df.groupby("bucket", observed=True)["ret_20d"].agg(["mean", "median", "count"])
    g["win_rate"] = df.groupby("bucket", observed=True)["ret_20d"].apply(lambda s: (s > 0).mean())
    print(g.to_string())

    print("\n--- VERDICT ---")
    mean20 = df["ret_20d"].mean()
    win20 = (df["ret_20d"] > 0).mean()
    if mean20 > 0.005 and win20 > 0.55:
        print(f"  PASS: 20-day mean {mean20:+.2%}, win rate {win20:.1%} — signal carries edge.")
    elif mean20 > 0:
        print(f"  WEAK: 20-day mean {mean20:+.2%}, win rate {win20:.1%} — borderline, watch out for survivorship.")
    else:
        print(f"  FAIL: 20-day mean {mean20:+.2%}, win rate {win20:.1%} — signal is noise on this universe.")

    out_csv = ROOT / "reports" / "bottom_catch_triggers.csv"
    df.to_csv(out_csv, index=False)
    print(f"\nFull trigger dataset: {out_csv}")


if __name__ == "__main__":
    main()
