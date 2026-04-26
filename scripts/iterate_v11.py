"""v1.1 — last two ideas before declaring convergence.

F1: Momentum acceleration filter (require BOTH 3m and 12m returns > 0)
    Hypothesis: kills bear-trap entries where 12m is positive but 3m is rolling over.

F2: Risk-parity with backtest-derived priors (no 12-month warmup needed)
    Use historical 2015-2020 vols as the starting weights, update with live data
    each month thereafter. This is the v0.4 winner made deployable.

Walk-forward both. Deploy if OOS Sharpe > 0.85 with decay < 50%.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd
from trader.data import fetch_history
from trader.universe import DEFAULT_LIQUID_50

REPORTS = ROOT / "reports"


def accel_momentum_returns(start, end, lookback_long=12, lookback_short=3, top_n=5):
    """Top-N momentum that ALSO requires 3-month momentum > 0."""
    prices = fetch_history(DEFAULT_LIQUID_50, start=start, end=end)
    prices = prices.dropna(axis=1, thresh=int(len(prices) * 0.5))
    monthly = prices.resample("ME").last().ffill(limit=2)
    monthly_ret = monthly.pct_change()

    long_lookback = monthly.shift(1) / monthly.shift(1 + lookback_long) - 1
    short_lookback = monthly.shift(1) / monthly.shift(1 + lookback_short) - 1

    weights = pd.DataFrame(0.0, index=monthly.index, columns=monthly.columns)
    for d in monthly.index:
        long_scores = long_lookback.loc[d].dropna()
        short_scores = short_lookback.loc[d].dropna()
        # filter: both 12m > 0 and 3m > 0 (acceleration)
        eligible = long_scores[(long_scores > 0) & (short_scores.reindex(long_scores.index) > 0)]
        if len(eligible) < 1:
            continue
        winners = eligible.nlargest(min(top_n, len(eligible))).index
        for w in winners:
            weights.loc[d, w] = 1.0 / len(winners)

    return (weights.shift(1) * monthly_ret).sum(axis=1).fillna(0)


def baseline_momentum_returns(start, end, top_n=5, lookback_months=12):
    from trader.backtest import backtest_momentum
    return backtest_momentum(DEFAULT_LIQUID_50, start, end, lookback_months, 1, top_n).monthly_returns


def _stats(rets, label):
    eq = (1 + rets.fillna(0)).cumprod() * 100_000
    if len(eq) < 6:
        return None
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
    sharpe = rets.mean() * 12 / (rets.std() * np.sqrt(12)) if rets.std() > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    return {"label": label, "cagr": float(cagr), "sharpe": float(sharpe), "maxdd": float(dd)}


def _bot_monthly(start, end, score_min=0.65):
    df = pd.read_csv(REPORTS / "bottom_catch_triggers.csv", parse_dates=["date"])
    df = df[(df["date"] >= start) & (df["date"] < end) & (df["score"] >= score_min)]
    df["month"] = df["date"].dt.to_period("M").dt.to_timestamp("M")
    return df.groupby("month")["ret_20d"].mean()


def f1_acceleration(period_name, start, end):
    print("\n" + "=" * 80)
    print(f"F1 — MOMENTUM ACCELERATION FILTER ({period_name})")
    print("=" * 80)
    base = baseline_momentum_returns(start, end)
    accel = accel_momentum_returns(start, end)
    rows = [_stats(base, "baseline 12m/top-5"), _stats(accel, "accel: 12m+3m both >0")]
    print(f"\n{'config':32s}  {'CAGR':>8s}  {'Sharpe':>7s}  {'MaxDD':>8s}")
    for r in rows:
        if r:
            print(f"  {r['label']:32s}  {r['cagr']:>+8.2%}  {r['sharpe']:>+7.2f}  {r['maxdd']:>+8.2%}")
    return rows


def f2_risk_parity_with_priors(period_name, start, end):
    """Use 2015-2020 historical vols as priors for the first 12 months, then update."""
    print("\n" + "=" * 80)
    print(f"F2 — RISK-PARITY WITH BACKTEST PRIORS ({period_name})")
    print("=" * 80)
    mom_full = baseline_momentum_returns("2015-01-01", end)
    bot_full_index = baseline_momentum_returns("2015-01-01", end).index
    bot_full = _bot_monthly("2015-01-01", end).reindex(bot_full_index).fillna(0)

    # Compute prior vols from 2015-2020 only
    prior_window = (mom_full.index < pd.Timestamp("2021-01-01"))
    prior_mom_vol = mom_full[prior_window].std()
    prior_bot_vol = bot_full[prior_window].std() if bot_full[prior_window].std() > 0 else mom_full[prior_window].std()

    # For each month, weight inversely to running vol estimate (priors blended in early)
    mom_vol = mom_full.expanding(min_periods=6).std()
    bot_vol = bot_full.expanding(min_periods=6).std().replace(0, np.nan)
    inv_m = 1 / mom_vol.fillna(prior_mom_vol)
    inv_b = 1 / bot_vol.fillna(prior_bot_vol)
    s = inv_m + inv_b
    w_m = (inv_m / s).clip(0.3, 0.85)  # never let one sleeve dominate completely
    w_b = 1 - w_m

    rp = w_m.shift(1) * mom_full + w_b.shift(1) * bot_full
    fixed = 0.6 * mom_full + 0.4 * bot_full  # current deployment baseline

    # Restrict to test period
    rp_period = rp[(rp.index >= start) & (rp.index < end)]
    fixed_period = fixed[(fixed.index >= start) & (fixed.index < end)]
    mom_period = mom_full[(mom_full.index >= start) & (mom_full.index < end)]

    rows = [
        _stats(mom_period, "momentum-only"),
        _stats(fixed_period, "fixed 60/40 (deployed)"),
        _stats(rp_period, "risk-parity w/ priors"),
    ]
    print(f"\n{'config':32s}  {'CAGR':>8s}  {'Sharpe':>7s}  {'MaxDD':>8s}")
    for r in rows:
        if r:
            print(f"  {r['label']:32s}  {r['cagr']:>+8.2%}  {r['sharpe']:>+7.2f}  {r['maxdd']:>+8.2%}")
    return rows


def main():
    f1_acceleration("TRAIN", "2015-01-01", "2020-12-31")
    f1_train = f1_acceleration("TEST OOS", "2021-01-01", "2025-04-30")

    f2_risk_parity_with_priors("TEST OOS", "2021-01-01", "2025-04-30")


if __name__ == "__main__":
    main()
