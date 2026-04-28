"""Barroso–Santa-Clara strategy-realized-vol scaling on the realistic momentum.

Empirical question: does scaling exposure by the inverse of trailing 6-month
*strategy* realized vol (NOT VIX, NOT MA-of-market filter) reduce momentum-
crash months without killing return in calm periods?

Method:
  1. Run backtest_momentum_realistic(2015-2025, top-5, 12m lookback).
  2. Take net monthly returns.
  3. Compute trailing 6-month realized vol of strategy returns
     (sigma_t = std of last 6 monthly returns * sqrt(12)).
  4. target_vol = mean realized_vol over the full period.
  5. leverage_t = target_vol / sigma_{t-1}    (use lagged so it's tradable)
  6. Cap leverage at [0, 2.0] to keep it realistic.
  7. scaled_ret_t = leverage_t * unscaled_ret_t
  8. Compare CAGR / Sharpe / MaxDD / max-month / min-month.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import math
import numpy as np
import pandas as pd

from trader.backtest import backtest_momentum_realistic
from trader.universe import DEFAULT_LIQUID_50


def stats_from_monthly(ret: pd.Series, label: str) -> dict:
    ret = ret.dropna()
    if len(ret) == 0:
        return {}
    equity = (1 + ret).cumprod()
    years = len(ret) / 12.0
    cagr = equity.iloc[-1] ** (1 / years) - 1
    ann_vol = ret.std() * math.sqrt(12)
    sharpe = (ret.mean() * 12) / ann_vol if ann_vol > 0 else 0.0
    running_max = equity.cummax()
    drawdown = equity / running_max - 1
    max_dd = drawdown.min()
    return {
        "label": label,
        "n_months": len(ret),
        "cagr": float(cagr),
        "ann_vol": float(ann_vol),
        "sharpe": float(sharpe),
        "max_dd": float(max_dd),
        "max_month": float(ret.max()),
        "min_month": float(ret.min()),
        "min_month_date": str(ret.idxmin().date()) if len(ret) else "",
        "max_month_date": str(ret.idxmax().date()) if len(ret) else "",
        "worst_3_months": [
            (str(d.date()), float(v)) for d, v in ret.nsmallest(3).items()
        ],
    }


def main():
    print("=" * 78)
    print("BARROSO-SANTA-CLARA strategy-realized-vol scaling")
    print("  Universe: liquid_50, 2015-01-01 to 2025-04-30, top-5, 12m lookback")
    print("=" * 78)

    res = backtest_momentum_realistic(
        DEFAULT_LIQUID_50,
        start="2015-01-01",
        end="2025-04-30",
        lookback_months=12,
        top_n=5,
        slippage_bps=5.0,
    )
    ret = res.monthly_returns.copy()
    # drop the leading zero before first real return
    ret = ret[ret.index >= ret[ret != 0].index[0]]
    print(f"\nRealistic monthly returns: n={len(ret)} months from {ret.index[0].date()} to {ret.index[-1].date()}")

    # Trailing 6-month realized vol of STRATEGY returns (annualized)
    realized_vol = ret.rolling(6).std() * math.sqrt(12)
    target_vol = realized_vol.mean()  # full-period mean as target
    print(f"Strategy realized vol (6m, annualized) — mean: {target_vol:.3f}, median: {realized_vol.median():.3f}, max: {realized_vol.max():.3f}, min: {realized_vol.min():.3f}")

    # Lagged leverage (tradable: use last month's realized vol to size this month)
    leverage_raw = target_vol / realized_vol.shift(1)
    leverage = leverage_raw.clip(0.0, 2.0)
    print(f"Leverage stats — mean: {leverage.mean():.2f}, median: {leverage.median():.2f}, min: {leverage.min():.2f}, max: {leverage.max():.2f}")
    print(f"Months at cap (lev=2.0): {(leverage >= 1.999).sum()}, months below 0.5: {(leverage < 0.5).sum()}")

    # Scaled returns (drop NaN warmup)
    scaled_ret = (leverage * ret).dropna()
    unscaled_ret = ret.loc[scaled_ret.index]  # align same window

    s_un = stats_from_monthly(unscaled_ret, "UNSCALED (realistic)")
    s_sc = stats_from_monthly(scaled_ret, "BSC-SCALED (6m strat vol)")

    print(f"\n{'metric':22s}  {'unscaled':>12s}  {'B-SC scaled':>12s}  {'delta':>10s}")
    print("-" * 62)
    for k in ("cagr", "ann_vol", "sharpe", "max_dd", "max_month", "min_month"):
        u, s = s_un[k], s_sc[k]
        fmt = "{:>12.4f}"
        print(f"{k:22s}  {fmt.format(u)}  {fmt.format(s)}  {f'{s-u:+.4f}':>10s}")

    print(f"\nWorst 3 months UNSCALED:")
    for d, v in s_un["worst_3_months"]:
        print(f"  {d}  {v:+.4f}")
    print(f"Worst 3 months B-SC SCALED:")
    for d, v in s_sc["worst_3_months"]:
        print(f"  {d}  {v:+.4f}")

    # Calm-period sanity: pick low-vol months (sigma < median) and compare returns there
    low_vol_mask = realized_vol.shift(1) < realized_vol.median()
    high_vol_mask = realized_vol.shift(1) >= realized_vol.median()
    low_un = unscaled_ret[low_vol_mask.reindex(unscaled_ret.index, fill_value=False)]
    low_sc = scaled_ret[low_vol_mask.reindex(scaled_ret.index, fill_value=False)]
    high_un = unscaled_ret[high_vol_mask.reindex(unscaled_ret.index, fill_value=False)]
    high_sc = scaled_ret[high_vol_mask.reindex(scaled_ret.index, fill_value=False)]

    def _ann(r):
        if len(r) == 0:
            return float("nan")
        return float(r.mean() * 12)

    print(f"\nLOW-VOL months (n={len(low_un)}):    unscaled mean*12 = {_ann(low_un):+.4f}  vs  scaled = {_ann(low_sc):+.4f}")
    print(f"HIGH-VOL months (n={len(high_un)}):  unscaled mean*12 = {_ann(high_un):+.4f}  vs  scaled = {_ann(high_sc):+.4f}")

    # Crash-month shrinkage: how does the BSC version size the worst unscaled month?
    worst_d = unscaled_ret.idxmin()
    print(f"\nWorst unscaled month {worst_d.date()}: ret={unscaled_ret.loc[worst_d]:+.4f}")
    print(f"  Leverage applied that month (lagged 6m vol): {leverage.loc[worst_d]:.3f}")
    print(f"  Scaled return that month: {scaled_ret.loc[worst_d]:+.4f}")

    # Compare to a quick VIX-style benchmark: if we use a pure 1m realized vol
    # of strategy (different lookback) we mimic short-window vol filters
    realized_vol_1m_proxy = ret.rolling(2).std() * math.sqrt(12)  # min window 2
    lev_1m = (target_vol / realized_vol_1m_proxy.shift(1)).clip(0, 2.0)
    scaled_1m = (lev_1m * ret).dropna()
    s_1m = stats_from_monthly(scaled_1m, "short-window vol scaled")
    print(f"\nFor reference — short-window (~1m) vol-scaled (proxy for VIX-style reactivity):")
    print(f"  CAGR={s_1m['cagr']:.4f}  Sharpe={s_1m['sharpe']:.3f}  MaxDD={s_1m['max_dd']:.4f}  min_month={s_1m['min_month']:.4f}")


if __name__ == "__main__":
    main()
