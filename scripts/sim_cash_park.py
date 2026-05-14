"""Simulation: quantify the cash-park overlay's impact on a 5-year
momentum backtest. Compares:
  (A) baseline — N% deployed, residual cash earns 0
  (B) cash-park — N% deployed, residual (- 5% buffer) in SPY

Outputs CAGR, vol, Sharpe, max drawdown for each + the delta.

Run:  /Users/richardchen/trader/.venv/bin/python /Users/richardchen/trader/scripts/sim_cash_park.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make `trader.*` importable from src/ when run via venv interp
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import numpy as np
import pandas as pd

from trader.data import fetch_history
from trader.universe import DEFAULT_LIQUID_50


def compute_stats(returns: pd.Series, label: str) -> dict:
    """Return CAGR / vol / Sharpe / maxDD from a daily-return series."""
    equity = (1 + returns.fillna(0)).cumprod()
    years = max(len(returns) / 252.0, 1e-9)
    cagr = equity.iloc[-1] ** (1 / years) - 1
    vol = float(returns.std()) * np.sqrt(252)
    mean_excess = float(returns.mean()) * 252  # risk-free assumed 0
    sharpe = mean_excess / vol if vol > 1e-9 else 0.0
    peak = equity.cummax()
    max_dd = float((equity / peak - 1).min())
    return {
        "label": label,
        "cagr": cagr,
        "vol": vol,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "final_equity": float(equity.iloc[-1]),
    }


def simulate(
    universe: list[str],
    start: str = "2021-01-01",
    end: str = "2026-05-01",
    top_n: int = 5,
    lookback_months: int = 12,
    deployed_gross: float = 0.62,
    min_buffer: float = 0.05,
) -> tuple[dict, dict, pd.DataFrame]:
    """Run baseline + cash-park sims. Returns (baseline_stats,
    cashpark_stats, equity_df)."""
    # 1. Pull daily prices for universe + SPY
    prices = fetch_history(universe + ["SPY"], start=start, end=end)
    prices = prices.dropna(axis=1, thresh=int(len(prices) * 0.5))
    spy = prices["SPY"]
    pool = [c for c in prices.columns if c != "SPY"]
    px = prices[pool]

    # 2. Monthly momentum picks
    monthly = px.resample("ME").last().ffill(limit=2)
    lookback = monthly.shift(1) / monthly.shift(1 + lookback_months) - 1
    weights = pd.DataFrame(0.0, index=monthly.index, columns=monthly.columns)
    for d in monthly.index:
        scores = lookback.loc[d].dropna()
        if len(scores) < top_n:
            continue
        winners = scores.nlargest(top_n).index
        # Equal-weight to top_n, sized to deployed_gross total
        weights.loc[d, winners] = deployed_gross / top_n

    # 3. Daily portfolio: reindex monthly weights to daily, lagged by 1
    #    trading day to avoid look-ahead.
    daily_weights = weights.reindex(px.index, method="ffill").shift(1).fillna(0)
    daily_ret = px.pct_change().fillna(0)
    deployed_daily_ret = (daily_weights * daily_ret).sum(axis=1)

    # 4. Cash bucket
    spy_daily_ret = spy.pct_change().fillna(0)
    deployed_pct_daily = daily_weights.sum(axis=1)
    cash_pct_daily = (1.0 - deployed_pct_daily).clip(lower=0)
    park_pct_daily = (cash_pct_daily - min_buffer).clip(lower=0)

    # Baseline: cash earns 0
    baseline_daily = deployed_daily_ret
    # Cash-park: residual cash earns SPY
    cashpark_daily = deployed_daily_ret + park_pct_daily * spy_daily_ret

    base_stats = compute_stats(baseline_daily, "baseline (cash @ 0%)")
    park_stats = compute_stats(cashpark_daily, "cash-park (residual → SPY)")
    spy_only_stats = compute_stats(spy_daily_ret, "SPY buy-and-hold")

    equity_df = pd.DataFrame({
        "baseline": (1 + baseline_daily.fillna(0)).cumprod(),
        "cashpark": (1 + cashpark_daily.fillna(0)).cumprod(),
        "spy": (1 + spy_daily_ret.fillna(0)).cumprod(),
    })

    return base_stats, park_stats, equity_df, spy_only_stats


def fmt(d: dict) -> str:
    return (
        f"  {d['label']:36s}  CAGR {d['cagr']:+.2%}  "
        f"vol {d['vol']:.1%}  Sharpe {d['sharpe']:.2f}  "
        f"maxDD {d['max_dd']:+.1%}  equity ${d['final_equity']:.3f}"
    )


def main():
    print("=" * 78)
    print("CASH-PARK OVERLAY SIMULATION")
    print("=" * 78)
    print()
    print("Setup:")
    print(f"  universe:           liquid_50 ({len(DEFAULT_LIQUID_50)} names)")
    print(f"  window:             2021-01-01 → 2026-05-01 (~5 years)")
    print(f"  strategy:           top-5 12m momentum, monthly rebal")
    print(f"  deployed gross:     62% (matches today's actual)")
    print(f"  cash buffer:        5% (always-liquid)")
    print(f"  cash-park sleeve:   residual - 5% → SPY")
    print()

    base, park, eq, spy = simulate(DEFAULT_LIQUID_50)

    print("Results (baseline = current behavior, cash sits at 0):")
    print(fmt(base))
    print(fmt(park))
    print(fmt(spy))
    print()
    print("Deltas (cash-park vs baseline):")
    print(f"  ΔCAGR:    {(park['cagr'] - base['cagr'])*100:+.2f} pp")
    print(f"  Δvol:     {(park['vol'] - base['vol'])*100:+.2f} pp")
    print(f"  ΔSharpe:  {park['sharpe'] - base['sharpe']:+.3f}")
    print(f"  ΔmaxDD:   {(park['max_dd'] - base['max_dd'])*100:+.2f} pp")
    print(f"  Δequity:  {(park['final_equity']/base['final_equity']-1)*100:+.2f}% more terminal $$")
    print()
    print("Alpha vs SPY:")
    print(f"  baseline CAGR vs SPY:  {(base['cagr']-spy['cagr'])*100:+.2f} pp")
    print(f"  cash-park CAGR vs SPY: {(park['cagr']-spy['cagr'])*100:+.2f} pp")
    print()
    out = Path(__file__).resolve().parent.parent / "data" / "reports" / "cash_park_sim.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    eq.to_csv(out)
    print(f"Equity curves → {out}")


if __name__ == "__main__":
    main()
