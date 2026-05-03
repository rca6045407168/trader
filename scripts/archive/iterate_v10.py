"""v1.0 — three strategy enhancements tested side by side.

E1: Sector-neutral momentum (1 per sector, then top-5 sectors)
    Hypothesis: less concentrated, better Sharpe, lower CAGR
E2: Vol-targeted exposure (scale portfolio to target 15% annualized vol)
    Hypothesis: better Sharpe, similar CAGR, big MaxDD reduction
E3: Tail hedge (allocate 10% to TLT when VIX > 25)
    Hypothesis: drag in calm markets, save during crashes; net positive Sharpe

Walk-forward all three (train 2015-2020, test 2021-2025) before deploying any.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd
from trader.data import fetch_history
from trader.universe import DEFAULT_LIQUID_50
from trader.sectors import get_sector


def sector_neutral_momentum_backtest(start, end, top_sectors=5, lookback_months=12):
    """Pick the highest-momentum stock from each of the top-5 sectors."""
    universe = DEFAULT_LIQUID_50
    prices = fetch_history(universe, start=start, end=end)
    # Drop tickers without at least half the period's history
    prices = prices.dropna(axis=1, thresh=int(len(prices) * 0.5))
    monthly = prices.resample("ME").last().ffill(limit=2)
    monthly_ret = monthly.pct_change()
    L, S = lookback_months, 1
    lookback = monthly.shift(S) / monthly.shift(S + L) - 1

    weights = pd.DataFrame(0.0, index=monthly.index, columns=monthly.columns)
    for d in monthly.index:
        scores = lookback.loc[d].dropna()
        if len(scores) < top_sectors:
            continue
        # group by sector, take best per sector
        by_sector: dict[str, tuple[str, float]] = {}
        for ticker, score in scores.items():
            sec = get_sector(ticker)
            if sec not in by_sector or score > by_sector[sec][1]:
                by_sector[sec] = (ticker, score)
        # sort sectors by their best stock's score, take top_sectors
        ranked_sectors = sorted(by_sector.values(), key=lambda x: -x[1])[:top_sectors]
        winners = [t for t, _ in ranked_sectors]
        for w in winners:
            weights.loc[d, w] = 1.0 / len(winners)

    gross = (weights.shift(1) * monthly_ret).sum(axis=1)
    return gross.fillna(0)


def vanilla_momentum_returns(start, end, lookback_months=12, top_n=5):
    from trader.backtest import backtest_momentum
    return backtest_momentum(DEFAULT_LIQUID_50, start, end, lookback_months, 1, top_n).monthly_returns


def vol_targeted_returns(returns, target_vol_annual=0.15, lookback=12):
    """Scale next month's exposure such that trailing vol = target."""
    realized_vol = returns.rolling(lookback).std() * np.sqrt(12)
    scale = (target_vol_annual / realized_vol).clip(0.2, 1.5).shift(1)  # use lagged scale
    return scale.fillna(1.0) * returns


def tail_hedge_returns(strategy_returns, start, end, vix_threshold=25, hedge_alloc=0.10):
    """Allocate hedge_alloc to TLT when VIX > threshold (rolling 30d max)."""
    spy = fetch_history(["SPY"], start=start, end=end)["SPY"]
    try:
        tlt = fetch_history(["TLT"], start=start, end=end)["TLT"]
        vix = fetch_history(["^VIX"], start=start, end=end)["^VIX"]
    except Exception:
        print("  TLT or VIX fetch failed; returning unhedged")
        return strategy_returns

    vix_monthly = vix.resample("ME").last()
    tlt_monthly = tlt.resample("ME").last()
    tlt_ret = tlt_monthly.pct_change()
    hedge_on = (vix_monthly > vix_threshold).astype(float).shift(1).fillna(0)

    aligned_hedge = hedge_on.reindex(strategy_returns.index).fillna(0)
    aligned_tlt = tlt_ret.reindex(strategy_returns.index).fillna(0)

    return (1 - hedge_alloc * aligned_hedge) * strategy_returns + hedge_alloc * aligned_hedge * aligned_tlt


def _stats(rets, label):
    eq = (1 + rets.fillna(0)).cumprod() * 100_000
    if len(eq) < 6:
        return None
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
    sharpe = rets.mean() * 12 / (rets.std() * np.sqrt(12)) if rets.std() > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    return {"label": label, "cagr": float(cagr), "sharpe": float(sharpe), "maxdd": float(dd)}


def run_period(period_name, start, end):
    print("\n" + "=" * 80)
    print(f"{period_name}: {start} to {end}")
    print("=" * 80)

    base = vanilla_momentum_returns(start, end)
    sector = sector_neutral_momentum_backtest(start, end)
    vol_targeted = vol_targeted_returns(base, target_vol_annual=0.15)
    hedged = tail_hedge_returns(base, start, end)
    combined = vol_targeted_returns(tail_hedge_returns(sector, start, end), target_vol_annual=0.15)

    rows = [
        _stats(base, "baseline 12m/top-5"),
        _stats(sector, "sector-neutral 5"),
        _stats(vol_targeted, "baseline + vol-target 15%"),
        _stats(hedged, "baseline + tail hedge"),
        _stats(combined, "sector + hedge + vol-target"),
    ]
    print(f"\n{'config':30s}  {'CAGR':>8s}  {'Sharpe':>7s}  {'MaxDD':>8s}")
    for r in rows:
        if r:
            print(f"  {r['label']:30s}  {r['cagr']:>+8.2%}  {r['sharpe']:>+7.2f}  {r['maxdd']:>+8.2%}")
    return rows


def main():
    train = run_period("TRAIN (in-sample)", "2015-01-01", "2020-12-31")
    test = run_period("TEST (out-of-sample)", "2021-01-01", "2025-04-30")

    print("\n" + "=" * 80)
    print("DECAY ANALYSIS")
    print("=" * 80)
    print(f"\n{'config':30s}  {'in_sharpe':>9s}  {'out_sharpe':>10s}  {'decay':>7s}  {'verdict':>20s}")
    for ti, te in zip(train, test):
        if ti is None or te is None:
            continue
        decay = (ti["sharpe"] - te["sharpe"]) / ti["sharpe"] if ti["sharpe"] != 0 else float("nan")
        if te["sharpe"] > 1.0 and decay < 0.4:
            v = "DEPLOY"
        elif te["sharpe"] > 0.5:
            v = "caution"
        else:
            v = "REJECT"
        print(f"  {ti['label']:30s}  {ti['sharpe']:>9.2f}  {te['sharpe']:>10.2f}  {decay:>+6.1%}  {v:>20s}")


if __name__ == "__main__":
    main()
