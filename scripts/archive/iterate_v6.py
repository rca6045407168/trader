"""v0.6 — reality-check the deployed strategy.

H8: Does the bottom-catch return survive bracket-order exits (stop/take/trail)
    instead of the idealized 20-day hold? Backtest assumed full hold; live uses
    bracket exits. Expected: lower mean (capped winners), higher win rate (cut losers).

H9: Signal-strength-weighted momentum (weight by trailing 12m return) vs equal-weight.
    Hypothesis: concentrating in strongest names captures more momentum but also
    more idiosyncratic risk — likely lower Sharpe, possibly higher CAGR.

H10: Are bottom-catch returns front-loaded? If first 5 days capture most of the
     20-day return, brackets will hurt less than expected.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd
from trader.data import fetch_ohlcv, fetch_history
from trader.signals import bottom_catch_score, atr
from trader.universe import DEFAULT_LIQUID_50

REPORTS = ROOT / "reports"


def simulate_bracket(ohlc, entry_idx, atr_dollar, stop_mult=1.5, take_mult=3.0,
                     trail_mult=1.0, trail_activation_mult=2.0, max_hold_days=20):
    """Walk forward day-by-day from entry, exit on whichever fires first.

    Returns (exit_reason, days_held, return_pct).
    """
    entry_price = float(ohlc["Close"].iloc[entry_idx])
    stop = entry_price - stop_mult * atr_dollar
    take = entry_price + take_mult * atr_dollar
    trail_active = False
    trail_stop = stop
    high_water = entry_price

    for d in range(1, max_hold_days + 1):
        if entry_idx + d >= len(ohlc):
            break
        bar_high = float(ohlc["High"].iloc[entry_idx + d])
        bar_low = float(ohlc["Low"].iloc[entry_idx + d])
        bar_close = float(ohlc["Close"].iloc[entry_idx + d])

        # Stop fires if intraday low <= effective stop
        effective_stop = max(stop, trail_stop) if trail_active else stop
        if bar_low <= effective_stop:
            exit_price = effective_stop
            return ("stop_or_trail", d, exit_price / entry_price - 1)

        # Take fires if intraday high >= take
        if bar_high >= take:
            return ("take", d, take_mult * atr_dollar / entry_price)

        # Update trailing stop
        if bar_high > high_water:
            high_water = bar_high
        if not trail_active and high_water >= entry_price + trail_activation_mult * atr_dollar:
            trail_active = True
        if trail_active:
            new_trail = high_water - trail_mult * atr_dollar
            if new_trail > trail_stop:
                trail_stop = new_trail

    # Time exit
    final_close = float(ohlc["Close"].iloc[entry_idx + max_hold_days])
    return ("time", max_hold_days, final_close / entry_price - 1)


def h8_bracket_realism():
    print("\n" + "=" * 78)
    print("H8 — BRACKET-EXIT REALISM FOR BOTTOM-CATCH")
    print("=" * 78)

    triggers = pd.read_csv(REPORTS / "bottom_catch_triggers.csv", parse_dates=["date"])
    triggers = triggers[triggers["score"] >= 0.65].copy()

    sim_returns = []
    sim_exits = []
    print(f"Simulating brackets for {len(triggers)} triggers...")
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
            reason, days, ret = simulate_bracket(ohlc, idx, a)
            sim_returns.append(ret)
            sim_exits.append(reason)

    sr = pd.Series(sim_returns)
    se = pd.Series(sim_exits)

    print(f"\nBracket-exit results across {len(sr)} trades:")
    print(f"  Mean return:  {sr.mean():+.2%}")
    print(f"  Median:       {sr.median():+.2%}")
    print(f"  Win rate:     {(sr > 0).mean():.1%}")
    print(f"  Std dev:      {sr.std():.2%}")
    print(f"\n  Idealized 20-day hold (baseline): mean +2.29%, win 62.5%")
    print(f"\nExit-reason breakdown:")
    print(se.value_counts().to_string())
    avg_days = pd.Series([len(sim_exits)]).mean()

    delta_mean = sr.mean() - 0.0229
    print(f"\nDelta vs idealized: {delta_mean:+.2%} per trade")
    if delta_mean > -0.005:
        print("  VERDICT: brackets preserve the edge (within 50bps).")
    elif delta_mean > -0.015:
        print("  VERDICT: brackets cost ~1% per trade. Acceptable for the risk reduction.")
    else:
        print("  VERDICT: brackets significantly hurt edge. Consider wider takes.")


def h9_signal_weighted_momentum():
    print("\n" + "=" * 78)
    print("H9 — SIGNAL-STRENGTH-WEIGHTED MOMENTUM")
    print("=" * 78)
    from trader.data import fetch_history
    universe = DEFAULT_LIQUID_50
    prices = fetch_history(universe, start="2015-01-01", end="2025-04-30")
    prices = prices.dropna(axis=1, thresh=int(len(prices) * 0.5))

    monthly = prices.resample("ME").last().ffill(limit=2)
    monthly_ret = monthly.pct_change()
    L, S, N = 12, 1, 5
    lookback = monthly.shift(S) / monthly.shift(S + L) - 1

    eq_w = pd.DataFrame(0.0, index=monthly.index, columns=monthly.columns)
    sw_w = pd.DataFrame(0.0, index=monthly.index, columns=monthly.columns)

    for d in monthly.index:
        scores = lookback.loc[d].dropna()
        if len(scores) < N:
            continue
        winners = scores.nlargest(N)
        eq_w.loc[d, winners.index] = 1.0 / N
        # Signal-strength weighting: weight = score / sum(scores)
        # Clip negative scores to 0 (don't short)
        clipped = winners.clip(lower=0.001)
        sw_w.loc[d, clipped.index] = clipped / clipped.sum()

    eq_ret = (eq_w.shift(1) * monthly_ret).sum(axis=1)
    sw_ret = (sw_w.shift(1) * monthly_ret).sum(axis=1)

    for label, series in [("Equal-weight", eq_ret), ("Signal-strength", sw_ret)]:
        eq = (1 + series.fillna(0)).cumprod() * 100_000
        years = (eq.index[-1] - eq.index[0]).days / 365.25
        cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
        sharpe = series.mean() * 12 / (series.std() * np.sqrt(12))
        dd = (eq / eq.cummax() - 1).min()
        print(f"  {label:18s}  CAGR {cagr:>7.2%}  Sharpe {sharpe:>5.2f}  MaxDD {dd:>7.2%}")


def h10_return_arrival():
    print("\n" + "=" * 78)
    print("H10 — BOTTOM-CATCH RETURN ARRIVAL TIMING")
    print("=" * 78)
    df = pd.read_csv(REPORTS / "bottom_catch_triggers.csv", parse_dates=["date"])
    df = df[df["score"] >= 0.65]
    print(f"\nMean cumulative return at each horizon (n={len(df)}):")
    for h in (5, 10, 20, 60):
        m = df[f"ret_{h}d"].mean()
        print(f"  {h:>3d}d:  mean {m:+.2%}")
    pct_5_of_20 = df["ret_5d"].mean() / df["ret_20d"].mean() if df["ret_20d"].mean() != 0 else 0
    pct_10_of_20 = df["ret_10d"].mean() / df["ret_20d"].mean() if df["ret_20d"].mean() != 0 else 0
    print(f"\n  First 5d captures {pct_5_of_20:.0%} of the 20d return")
    print(f"  First 10d captures {pct_10_of_20:.0%} of the 20d return")
    if pct_10_of_20 > 0.7:
        print("  VERDICT: returns arrive early. Brackets should largely preserve edge.")
    else:
        print("  VERDICT: returns build over full window. Brackets may give back gains.")


def main():
    h10_return_arrival()  # cheap, run first
    h9_signal_weighted_momentum()
    h8_bracket_realism()  # expensive, run last


if __name__ == "__main__":
    main()
