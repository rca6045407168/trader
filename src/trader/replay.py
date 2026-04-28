"""Per-symbol forward-return replay for variant comparison.

Replaces the SPY-beta proxy in compare_variants.py. For each shadow decision:
  1. Look up actual yfinance forward returns of the picked tickers
  2. Compute the variant's hypothetical portfolio return on that day
  3. Build the equity curve and compute Sharpe / drawdown / vs SPY

This is the cleanest possible proxy — uses actual symbol-level moves rather
than assuming the basket equals SPY. Accuracy approaches a true backtest;
the only approximation is that we use close-to-close returns for forward periods,
not real fills (which would require a live shadow execution simulator).
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from .data import fetch_history


def _next_trading_day(target_date: pd.Timestamp, prices: pd.DataFrame) -> pd.Timestamp | None:
    """Return the next trading day in the prices index >= target_date."""
    idx = prices.index.searchsorted(target_date)
    if idx >= len(prices):
        return None
    return prices.index[idx]


def replay_decisions(
    decisions: list[dict],
    benchmark_symbol: str = "SPY",
) -> dict[str, Any]:
    """Replay a list of {ts, targets_json} decisions into a return series.

    Each decision is held until the NEXT decision (or end-of-window).
    Returns are computed per-symbol from yfinance, then aggregated by target weight.

    Returns:
        {
          "equity_curve": list[float],
          "daily_returns": list[float],   # daily portfolio returns
          "benchmark_returns": list[float], # SPY returns over same period
          "n_days": int,
          "stats": {sharpe, cagr, max_dd, alpha_excess_to_bench},
        }
    """
    if not decisions:
        return {"n_days": 0, "stats": {}, "equity_curve": [], "daily_returns": [], "benchmark_returns": []}

    decisions = sorted(decisions, key=lambda d: d["ts"])
    first_ts = pd.Timestamp(decisions[0]["ts"])
    last_ts = pd.Timestamp(decisions[-1]["ts"])

    # Pad window so we can replay the last decision's holding period too
    end_pad = last_ts + pd.Timedelta(days=45)
    start_pad = first_ts - pd.Timedelta(days=10)

    # Collect all unique tickers across decisions + benchmark
    all_tickers: set[str] = set()
    for d in decisions:
        targets = json.loads(d["targets_json"]) if isinstance(d["targets_json"], str) else d["targets_json"]
        all_tickers.update(targets.keys())
    all_tickers.add(benchmark_symbol)

    if not all_tickers:
        return {"n_days": 0, "stats": {}, "equity_curve": [], "daily_returns": [], "benchmark_returns": []}

    try:
        prices = fetch_history(
            sorted(all_tickers),
            start=start_pad.strftime("%Y-%m-%d"),
            end=end_pad.strftime("%Y-%m-%d"),
        )
    except Exception as e:
        return {"n_days": 0, "stats": {}, "error": f"price fetch failed: {e}"}

    prices = prices.dropna(how="all")
    daily_rets = prices.pct_change()

    # Build a daily target-weights frame by forward-filling decisions
    daily_index = prices.index
    weights_per_day = pd.DataFrame(0.0, index=daily_index, columns=prices.columns)

    for i, d in enumerate(decisions):
        ts = pd.Timestamp(d["ts"])
        next_trade_day = _next_trading_day(ts, prices)
        if next_trade_day is None:
            continue
        # Find the END of this decision's holding period (next decision or final day)
        if i + 1 < len(decisions):
            next_ts = pd.Timestamp(decisions[i + 1]["ts"])
            end_day = _next_trading_day(next_ts, prices)
            if end_day is None:
                end_day = daily_index[-1]
        else:
            end_day = daily_index[-1]

        targets = json.loads(d["targets_json"]) if isinstance(d["targets_json"], str) else d["targets_json"]
        # Mask: between next_trade_day and end_day (exclusive), positions held
        mask = (daily_index >= next_trade_day) & (daily_index < end_day)
        for sym, w in targets.items():
            if sym in weights_per_day.columns:
                weights_per_day.loc[mask, sym] = w

    # Daily portfolio return = sum(weight_t-1 * daily_ret_t) — we use weights set at start of day
    portfolio_returns = (weights_per_day.shift(1) * daily_rets).sum(axis=1).fillna(0)
    bench_returns = daily_rets[benchmark_symbol].fillna(0) if benchmark_symbol in daily_rets.columns else pd.Series([0]*len(daily_index), index=daily_index)

    eq = (1 + portfolio_returns).cumprod() * 100_000
    bench_eq = (1 + bench_returns).cumprod() * 100_000

    # Stats — portfolio
    rets = portfolio_returns.tolist()
    n_days = len(rets)
    if n_days < 5:
        return {"n_days": n_days, "stats": {}, "equity_curve": eq.tolist(), "daily_returns": rets,
                "benchmark_returns": bench_returns.tolist()}

    nonzero = [r for r in rets if r != 0]
    if len(nonzero) < 5:
        return {"n_days": n_days, "stats": {"insufficient_active_days": len(nonzero)},
                "equity_curve": eq.tolist(), "daily_returns": rets,
                "benchmark_returns": bench_returns.tolist()}

    mean_r = statistics.mean(rets)
    sd_r = statistics.stdev(rets)
    sharpe = (mean_r * 252) / (sd_r * math.sqrt(252)) if sd_r > 0 else 0
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (252 / n_days) - 1
    running_max = eq.cummax()
    max_dd = ((eq / running_max) - 1).min()

    # Alpha (excess) to benchmark
    bench_mean = statistics.mean(bench_returns.tolist())
    excess = mean_r - bench_mean
    alpha_annualized = excess * 252

    return {
        "equity_curve": eq.tolist(),
        "daily_returns": rets,
        "benchmark_returns": bench_returns.tolist(),
        "n_days": n_days,
        "stats": {
            "sharpe": float(sharpe),
            "cagr": float(cagr),
            "max_drawdown": float(max_dd),
            "alpha_excess_annualized": float(alpha_annualized),
            "n_active_days": len(nonzero),
            "final_equity": float(eq.iloc[-1]),
            "benchmark_final_equity": float(bench_eq.iloc[-1]),
        },
    }


def paired_test(returns_a: list[float], returns_b: list[float]) -> dict[str, float]:
    """Paired-t-test: is the mean(a) - mean(b) significantly different from 0?

    Returns t-statistic, p-value (two-sided), and a bootstrap 95% CI on the difference.
    """
    if len(returns_a) != len(returns_b):
        return {"t_stat": float("nan"), "p_value": float("nan"), "n": 0,
                "message": "length mismatch"}
    n = len(returns_a)
    if n < 10:
        return {"t_stat": float("nan"), "p_value": float("nan"), "n": n,
                "message": "need >=10 observations"}

    diffs = [a - b for a, b in zip(returns_a, returns_b)]
    mean_d = statistics.mean(diffs)
    sd_d = statistics.stdev(diffs)
    if sd_d == 0:
        return {"t_stat": 0.0, "p_value": 1.0, "n": n, "mean_diff": mean_d}
    t = mean_d / (sd_d / math.sqrt(n))

    # Approximate two-sided p-value using normal CDF (large-n approx)
    # For small n we'd use scipy.stats.t but stick with stdlib
    from math import erf, sqrt as _sqrt
    p = 2 * (1 - 0.5 * (1 + erf(abs(t) / _sqrt(2))))

    # Bootstrap 95% CI on mean difference (1000 resamples)
    import random
    rng = random.Random(42)
    boot_means = []
    for _ in range(1000):
        sample = [diffs[rng.randint(0, n - 1)] for _ in range(n)]
        boot_means.append(statistics.mean(sample))
    boot_means.sort()
    ci_lo = boot_means[24]  # 2.5%
    ci_hi = boot_means[974]  # 97.5%

    return {
        "t_stat": float(t),
        "p_value": float(p),
        "n": n,
        "mean_diff": float(mean_d),
        "mean_diff_annualized": float(mean_d * 252),
        "ci_95_lo": float(ci_lo),
        "ci_95_hi": float(ci_hi),
        "ci_95_lo_annualized": float(ci_lo * 252),
        "ci_95_hi_annualized": float(ci_hi * 252),
    }
