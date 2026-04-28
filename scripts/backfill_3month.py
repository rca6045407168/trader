"""Simulate each registered variant's decisions over the last 3 months and replay.

This bypasses the shadow_decisions table (which is empty until live runs accumulate)
and instead generates would-have-been decisions from historical yfinance data.

Output: per-variant Sharpe, CAGR, MaxDD vs SPY, statistical significance vs live.

Usage: python scripts/backfill_3month.py
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import math
import statistics
from datetime import date, timedelta
import pandas as pd
import numpy as np

from trader.data import fetch_history
from trader.universe import DEFAULT_LIQUID_50
from trader.sectors import get_sector
from trader.anomalies import scan_anomalies


END_DATE = pd.Timestamp.today().normalize()
START_DATE = END_DATE - pd.Timedelta(days=95)  # ~3 months


def _momentum_picks_as_of(as_of: pd.Timestamp, top_n: int = 5,
                          lookback_months: int = 12, skip_months: int = 1) -> list[str]:
    """Ranks DEFAULT_LIQUID_50 by 12m momentum as-of a date and returns top_n tickers."""
    L = lookback_months * 21  # trading days
    S = skip_months * 21
    start_pad = as_of - pd.Timedelta(days=int((L + S + 21) * 1.6))
    try:
        prices = fetch_history(DEFAULT_LIQUID_50, start=start_pad.strftime("%Y-%m-%d"),
                               end=as_of.strftime("%Y-%m-%d"))
    except Exception:
        return []
    if prices.empty or len(prices) < L + S:
        return []
    end_idx = -1 - S if S > 0 else -1
    start_idx = -(L + S) - 1
    rets = (prices.iloc[end_idx] / prices.iloc[start_idx] - 1).dropna()
    return rets.nlargest(top_n).index.tolist()


def variant_momentum_top5_eq(as_of: pd.Timestamp) -> dict[str, float]:
    picks = _momentum_picks_as_of(as_of, top_n=5)
    return {p: 0.40 / len(picks) for p in picks} if picks else {}


def variant_momentum_top5_sector_capped(as_of: pd.Timestamp) -> dict[str, float]:
    """Top-5 with 1-per-sector constraint: take broader candidate pool, take top-1 per sector."""
    candidates = _momentum_picks_as_of(as_of, top_n=20)
    selected = []
    used = set()
    for ticker in candidates:
        s = get_sector(ticker)
        if s in used:
            continue
        used.add(s)
        selected.append(ticker)
        if len(selected) >= 5:
            break
    return {p: 0.40 / len(selected) for p in selected} if selected else {}


def variant_momentum_top10_diluted(as_of: pd.Timestamp) -> dict[str, float]:
    picks = _momentum_picks_as_of(as_of, top_n=10)
    return {p: 0.40 / len(picks) for p in picks} if picks else {}


def variant_calendar_anomalies(as_of: pd.Timestamp) -> dict[str, float]:
    triggered = scan_anomalies(as_of.date())
    if not triggered:
        return {}
    weight_for_conf = {"high": 0.10, "medium": 0.05, "low": 0.02}
    targets: dict[str, float] = {}
    for a in triggered:
        w = weight_for_conf.get(a.confidence, 0)
        if w <= 0:
            continue
        sym = "IWM" if a.target_symbol == "IWM" else "SPY"
        targets[sym] = targets.get(sym, 0) + w
    total = sum(targets.values())
    if total > 0.20:
        scale = 0.20 / total
        targets = {k: v * scale for k, v in targets.items()}
    return targets


def variant_momentum_top3_concentrated(as_of: pd.Timestamp) -> dict[str, float]:
    """SHADOW: top-3 momentum, more concentrated, same 40% sleeve."""
    picks = _momentum_picks_as_of(as_of, top_n=3)
    return {p: 0.40 / len(picks) for p in picks} if picks else {}


def variant_momentum_full_allocation(as_of: pd.Timestamp) -> dict[str, float]:
    """SHADOW: top-5 momentum but DEPLOY 80% (vs risk-parity priors of 40%).
    Reverts to v0.5 fixed 80/20 weighting. Less cash drag in mom-friendly regimes."""
    picks = _momentum_picks_as_of(as_of, top_n=5)
    return {p: 0.80 / len(picks) for p in picks} if picks else {}


def variant_momentum_top3_full(as_of: pd.Timestamp) -> dict[str, float]:
    """SHADOW: top-3 + full 80% allocation. Most aggressive: high concentration + high deployment."""
    picks = _momentum_picks_as_of(as_of, top_n=3)
    return {p: 0.80 / len(picks) for p in picks} if picks else {}


VARIANTS = {
    "momentum_top5_eq_v1 (LIVE)": variant_momentum_top5_eq,
    "momentum_top5_sector_capped_v1": variant_momentum_top5_sector_capped,
    "momentum_top10_diluted_v1": variant_momentum_top10_diluted,
    "calendar_anomalies_v1": variant_calendar_anomalies,
    "momentum_top3_concentrated": variant_momentum_top3_concentrated,
    "momentum_full_allocation": variant_momentum_full_allocation,
    "momentum_top3_full": variant_momentum_top3_full,
}


def replay_variant(variant_name: str, variant_fn) -> dict:
    """Walk daily through last 3 months, generate decisions, replay against actual returns."""
    print(f"\n--- {variant_name} ---")
    bdays = pd.bdate_range(START_DATE, END_DATE)

    # For momentum variants: rebalance ONLY at month-end
    is_momentum = "momentum" in variant_name
    is_calendar = "calendar" in variant_name

    decisions = []  # [(decision_date, target_dict)]
    if is_momentum:
        for d in bdays:
            # Fire only on the last business day of each month
            next_d = d + pd.Timedelta(days=1)
            if next_d.month != d.month:
                targets = variant_fn(d)
                if targets:
                    decisions.append((d, targets))
    elif is_calendar:
        for d in bdays:
            targets = variant_fn(d)
            if targets:
                decisions.append((d, targets))

    if not decisions:
        return {"n_decisions": 0, "message": "no decisions in window"}

    # Collect tickers needed + SPY benchmark
    all_tickers = {"SPY"}
    for _, t in decisions:
        all_tickers.update(t.keys())
    try:
        prices = fetch_history(sorted(all_tickers),
                              start=(START_DATE - pd.Timedelta(days=10)).strftime("%Y-%m-%d"),
                              end=(END_DATE + pd.Timedelta(days=2)).strftime("%Y-%m-%d"))
    except Exception as e:
        return {"n_decisions": len(decisions), "error": f"price fetch failed: {e}"}

    daily_rets = prices.pct_change().fillna(0)
    daily_idx = prices.index

    # Build daily weights series via forward-fill from each decision
    weights = pd.DataFrame(0.0, index=daily_idx, columns=prices.columns)
    for i, (d, targets) in enumerate(decisions):
        try:
            entry_idx = daily_idx.searchsorted(d) + 1  # next trading day
            if entry_idx >= len(daily_idx):
                continue
        except Exception:
            continue
        if i + 1 < len(decisions):
            next_d = decisions[i + 1][0]
            exit_idx = daily_idx.searchsorted(next_d) + 1
            if exit_idx >= len(daily_idx):
                exit_idx = len(daily_idx)
        else:
            exit_idx = len(daily_idx)
        for sym, w in targets.items():
            if sym in weights.columns:
                weights.iloc[entry_idx:exit_idx, weights.columns.get_loc(sym)] = w

    portfolio_rets = (weights.shift(1) * daily_rets).sum(axis=1).fillna(0)
    portfolio_rets = portfolio_rets[portfolio_rets.index >= START_DATE]
    bench_rets = daily_rets["SPY"][daily_rets["SPY"].index >= START_DATE].fillna(0)

    n = len(portfolio_rets)
    if n < 5:
        return {"n_decisions": len(decisions), "n_days": n, "message": "insufficient days"}

    eq = (1 + portfolio_rets).cumprod() * 100_000
    bench_eq = (1 + bench_rets).cumprod() * 100_000

    mean_r = float(portfolio_rets.mean())
    sd_r = float(portfolio_rets.std())
    sharpe = (mean_r * 252) / (sd_r * math.sqrt(252)) if sd_r > 0 else 0
    cagr = (float(eq.iloc[-1]) / float(eq.iloc[0])) ** (252 / n) - 1
    max_dd = float((eq / eq.cummax() - 1).min())
    bench_cagr = (float(bench_eq.iloc[-1]) / float(bench_eq.iloc[0])) ** (252 / n) - 1
    excess_annualized = (mean_r - float(bench_rets.mean())) * 252
    final_eq = float(eq.iloc[-1])
    bench_final = float(bench_eq.iloc[-1])

    print(f"  Decisions: {len(decisions)} ({n} days)")
    print(f"  Total return: {(final_eq/100000 - 1)*100:+.2f}%  vs SPY {(bench_final/100000 - 1)*100:+.2f}%")
    print(f"  Annualized: CAGR {cagr*100:+.1f}% (SPY {bench_cagr*100:+.1f}%, excess {excess_annualized*100:+.2f}%)")
    print(f"  Sharpe (annualized): {sharpe:+.2f}")
    print(f"  Max drawdown: {max_dd*100:+.2f}%")
    print(f"  Final equity: ${final_eq:,.0f}  (SPY: ${bench_final:,.0f})")

    return {
        "n_decisions": len(decisions),
        "n_days": n,
        "portfolio_returns": portfolio_rets.tolist(),
        "benchmark_returns": bench_rets.tolist(),
        "sharpe": sharpe,
        "cagr": cagr,
        "max_dd": max_dd,
        "final_equity": final_eq,
        "excess_annualized": excess_annualized,
    }


def paired_test(a: list[float], b: list[float]) -> dict:
    if len(a) != len(b) or len(a) < 10:
        return {"message": "insufficient data"}
    diffs = [x - y for x, y in zip(a, b)]
    mean_d = statistics.mean(diffs)
    sd_d = statistics.stdev(diffs)
    if sd_d == 0:
        return {"t_stat": 0, "p_value": 1.0, "mean_diff_annualized": 0}
    t = mean_d / (sd_d / math.sqrt(len(a)))
    from math import erf, sqrt as _sqrt
    p = 2 * (1 - 0.5 * (1 + erf(abs(t) / _sqrt(2))))
    return {"t_stat": t, "p_value": p, "mean_diff_annualized": mean_d * 252,
            "n": len(a)}


def main():
    print("=" * 78)
    print(f"BACKFILL & REPLAY — {START_DATE.date()} to {END_DATE.date()} (~3 months)")
    print("=" * 78)

    results = {}
    for name, fn in VARIANTS.items():
        try:
            results[name] = replay_variant(name, fn)
        except Exception as e:
            print(f"\n--- {name} ---")
            print(f"  FAILED: {type(e).__name__}: {e}")

    # Pairwise tests vs LIVE
    live_key = next((k for k in results if "(LIVE)" in k), None)
    if live_key and "portfolio_returns" in results[live_key]:
        live_rets = results[live_key]["portfolio_returns"]
        print("\n" + "=" * 78)
        print("STATISTICAL: shadow vs LIVE (paired-t)")
        print("=" * 78)
        for name, r in results.items():
            if name == live_key or "portfolio_returns" not in r:
                continue
            other_rets = r["portfolio_returns"]
            n_min = min(len(live_rets), len(other_rets))
            test = paired_test(other_rets[-n_min:], live_rets[-n_min:])
            print(f"\n{name}:")
            for k, v in test.items():
                print(f"  {k}: {v}")

    print("\n" + "=" * 78)
    print("VERDICT (3-month sample, LOW POWER — for direction only):")
    print("=" * 78)
    if live_key and "sharpe" in results[live_key]:
        live_s = results[live_key]["sharpe"]
        for name, r in results.items():
            if name == live_key or "sharpe" not in r:
                continue
            delta = r["sharpe"] - live_s
            print(f"  {name:50s}  Sharpe Δ {delta:+.2f}  CAGR Δ {(r['cagr']-results[live_key]['cagr'])*100:+.1f}%")
    print("\nNote: 3 months is INSUFFICIENT for promotion. Need >=30 distinct decisions.")
    print("This is direction-only; full evaluation requires 90+ days of LIVE shadow data.")


if __name__ == "__main__":
    main()
