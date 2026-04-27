"""True portfolio analytics: beta to SPY, true alpha (Jensen), tracking error.

Until v2.6 we reported 'alpha' as `our_return - SPY_return`. That's EXCESS
return, not alpha. True alpha (Jensen's alpha) requires a beta calculation:

    α_t = our_return_t − β × SPY_return_t

where β is computed from rolling-window covariance(our, SPY) / variance(SPY).

For a portfolio of momentum-tilted equities, β is typically 0.9-1.3. If our
β is 1.2 and SPY rallied 10%, we'd EXPECT 12% — beating SPY by 2% is just
beta exposure, not skill. True alpha tells us if we're earning real skill.
"""
from __future__ import annotations

from typing import Iterable
import math


def compute_beta_alpha(
    portfolio_returns: list[float],
    spy_returns: list[float],
) -> dict[str, float]:
    """Compute portfolio beta and Jensen's alpha vs SPY (per-period, not annualized).

    Args:
        portfolio_returns: aligned list of portfolio returns
        spy_returns: aligned list of SPY returns over the same periods

    Returns:
        {'beta': β, 'alpha': annual α, 'tracking_error': σ(p - β*spy), 'r_squared': R²,
         'n_obs': len, 'message': diagnostic}
    """
    if len(portfolio_returns) != len(spy_returns):
        return {"beta": float("nan"), "alpha": float("nan"), "n_obs": 0,
                "message": "input length mismatch"}
    n = len(portfolio_returns)
    if n < 5:
        return {"beta": float("nan"), "alpha": float("nan"), "n_obs": n,
                "message": "insufficient observations (need >= 5)"}

    mean_p = sum(portfolio_returns) / n
    mean_s = sum(spy_returns) / n
    covar = sum((portfolio_returns[i] - mean_p) * (spy_returns[i] - mean_s) for i in range(n)) / n
    var_s = sum((spy_returns[i] - mean_s) ** 2 for i in range(n)) / n
    if var_s == 0:
        return {"beta": float("nan"), "alpha": float("nan"), "n_obs": n,
                "message": "SPY variance is zero"}
    beta = covar / var_s
    alpha_per_period = mean_p - beta * mean_s

    residuals = [portfolio_returns[i] - beta * spy_returns[i] - alpha_per_period for i in range(n)]
    var_p = sum((portfolio_returns[i] - mean_p) ** 2 for i in range(n)) / n
    if var_p == 0:
        r_squared = float("nan")
    else:
        explained = (beta ** 2) * var_s
        r_squared = max(0.0, min(1.0, explained / var_p))
    tracking_error = math.sqrt(sum(r * r for r in residuals) / n) if n > 0 else 0.0

    return {
        "beta": float(beta),
        "alpha_per_period": float(alpha_per_period),
        "alpha_annualized": float(alpha_per_period * 252),  # daily series assumption
        "tracking_error": float(tracking_error),
        "r_squared": float(r_squared),
        "n_obs": n,
        "message": f"beta={beta:.2f}, alpha={alpha_per_period*252*100:+.2f}%/yr",
    }


def compute_drawdown_stats(equity_series: list[float]) -> dict[str, float]:
    """Return current drawdown, max drawdown over the series, and time underwater."""
    if not equity_series:
        return {"current_dd": 0.0, "max_dd": 0.0, "days_underwater": 0}
    peak = equity_series[0]
    max_dd = 0.0
    current_peak = equity_series[0]
    days_underwater = 0
    underwater = 0
    for eq in equity_series:
        if eq > current_peak:
            current_peak = eq
            underwater = 0
        else:
            underwater += 1
        peak = max(peak, eq)
        dd = (eq / peak) - 1
        if dd < max_dd:
            max_dd = dd
        days_underwater = max(days_underwater, underwater)
    current_dd = (equity_series[-1] / peak) - 1
    return {
        "current_dd": float(current_dd),
        "max_dd": float(max_dd),
        "days_underwater": int(underwater),
        "all_time_high": float(peak),
    }


def fetch_portfolio_and_spy_returns(days: int = 30) -> tuple[list[float], list[float]]:
    """From journal snapshots + yfinance, return aligned (portfolio, spy) daily returns."""
    from .journal import recent_snapshots
    from .data import fetch_history
    from datetime import datetime
    import pandas as pd

    snaps = recent_snapshots(days=days + 5)
    if len(snaps) < 5:
        return [], []
    snaps = sorted(snaps, key=lambda s: s["date"])
    eqs = [s["equity"] for s in snaps if s.get("equity")]
    if len(eqs) < 5:
        return [], []
    p_returns = [eqs[i + 1] / eqs[i] - 1 for i in range(len(eqs) - 1)]

    try:
        spy = fetch_history(["SPY"], start=(datetime.now() - pd.Timedelta(days=days + 30)).strftime("%Y-%m-%d"))["SPY"]
        spy_dates = [d.date().isoformat() for d in spy.index]
    except Exception:
        return p_returns, []

    # Align by snapshot dates
    snap_dates = [s["date"] for s in snaps]
    spy_indexed = dict(zip(spy_dates, spy.values))
    aligned_spy_prices = [spy_indexed.get(d) for d in snap_dates]
    if any(p is None for p in aligned_spy_prices):
        return p_returns, []
    s_returns = [aligned_spy_prices[i + 1] / aligned_spy_prices[i] - 1
                 for i in range(len(aligned_spy_prices) - 1)]
    return p_returns, s_returns
