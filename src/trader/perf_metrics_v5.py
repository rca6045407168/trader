"""[v3.59.2] Extended performance metrics per BLINDSPOTS.md §6.

The existing analytics.py has Sharpe / Sortino / Calmar / IR / Beta. This
module adds the metrics BLINDSPOTS specifically called out as missing or
under-emphasized:

  • Omega ratio        — full distribution-aware (Keating-Shadwick 2002)
  • CVaR / ES at 95/99/99.5%  — worst-tail expected shortfall
  • Time underwater    — avg + max days from peak before recovery
  • Maximum runup before drawdown — symmetric counterpart to max DD
  • Tracking error vs SPY  — sleep-test for benchmark-deviation tolerance

All functions take a numeric daily-return list and return primitives.
Pure functions; safe to import everywhere.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Optional, Sequence


@dataclass
class ExtendedMetrics:
    n: int = 0
    sortino: Optional[float] = None
    calmar: Optional[float] = None
    omega_ratio: Optional[float] = None
    cvar_95: Optional[float] = None      # Average loss in worst 5% of days
    cvar_99: Optional[float] = None
    cvar_99_5: Optional[float] = None
    avg_days_underwater: Optional[float] = None
    max_days_underwater: Optional[int] = None
    max_runup_pct: Optional[float] = None
    tracking_error_pct: Optional[float] = None  # vs SPY


def sortino_ratio(daily_returns: Sequence[float],
                   target: float = 0.0,
                   periods_per_year: int = 252) -> Optional[float]:
    """Sortino: Sharpe but only penalizes downside vol.
    target is the MAR (minimum acceptable return), default 0.
    """
    if len(daily_returns) < 2:
        return None
    mean = statistics.mean(daily_returns) - target / periods_per_year
    downside = [min(r - target / periods_per_year, 0) for r in daily_returns]
    downside_var = sum(d ** 2 for d in downside) / len(downside)
    downside_std = math.sqrt(downside_var)
    if downside_std == 0:
        return float("inf") if mean > 0 else 0.0
    return (mean / downside_std) * math.sqrt(periods_per_year)


def calmar_ratio(daily_returns: Sequence[float],
                  periods_per_year: int = 252) -> Optional[float]:
    """Calmar: CAGR / |max DD|. Behaviorally relevant for retail."""
    if len(daily_returns) < 2:
        return None
    cum, peak, max_dd = 1.0, 1.0, 0.0
    for r in daily_returns:
        cum *= (1 + r)
        peak = max(peak, cum)
        max_dd = min(max_dd, cum / peak - 1)
    if max_dd == 0:
        return float("inf") if cum > 1 else 0.0
    n = len(daily_returns)
    cagr = cum ** (periods_per_year / n) - 1
    return cagr / abs(max_dd)


def omega_ratio(daily_returns: Sequence[float],
                 threshold: float = 0.0) -> Optional[float]:
    """Omega: ratio of probability-weighted gains above threshold to
    probability-weighted losses below it. Keating-Shadwick (2002)."""
    if len(daily_returns) < 2:
        return None
    gains = sum(max(r - threshold, 0) for r in daily_returns)
    losses = sum(max(threshold - r, 0) for r in daily_returns)
    if losses == 0:
        return float("inf") if gains > 0 else 1.0
    return gains / losses


def cvar(daily_returns: Sequence[float], confidence: float = 0.95) -> Optional[float]:
    """Conditional VaR / Expected Shortfall at given confidence.
    Returns the average loss in the worst (1 - confidence) of days.
    Returned as a NEGATIVE number (loss). 0.95 → average loss in worst 5%.
    """
    if len(daily_returns) < 20:  # need enough samples for a tail
        return None
    sorted_rets = sorted(daily_returns)
    cutoff = max(int(len(sorted_rets) * (1 - confidence)), 1)
    worst = sorted_rets[:cutoff]
    if not worst:
        return None
    return sum(worst) / len(worst)


def time_underwater(daily_returns: Sequence[float]) -> tuple[Optional[float], Optional[int]]:
    """Returns (avg_days_underwater, max_days_underwater).

    A day is "underwater" if cumulative equity is below its all-time peak.
    Avg = mean of consecutive-underwater-streak lengths.
    Max = longest consecutive streak.
    """
    if len(daily_returns) < 2:
        return None, None
    cum, peak = 1.0, 1.0
    streaks = []
    cur_streak = 0
    for r in daily_returns:
        cum *= (1 + r)
        if cum >= peak:
            peak = cum
            if cur_streak > 0:
                streaks.append(cur_streak)
            cur_streak = 0
        else:
            cur_streak += 1
    if cur_streak > 0:
        streaks.append(cur_streak)
    if not streaks:
        return 0.0, 0
    return sum(streaks) / len(streaks), max(streaks)


def max_runup(daily_returns: Sequence[float]) -> Optional[float]:
    """Max trough-to-peak gain (pct). Symmetric counterpart to max DD.
    Captures the overconfidence-risk side of the distribution."""
    if len(daily_returns) < 2:
        return None
    cum = 1.0
    trough = 1.0
    max_ru = 0.0
    for r in daily_returns:
        cum *= (1 + r)
        trough = min(trough, cum)
        if trough > 0:
            ru = cum / trough - 1
            max_ru = max(max_ru, ru)
    return max_ru * 100


def tracking_error(daily_returns: Sequence[float],
                    benchmark_returns: Sequence[float],
                    periods_per_year: int = 252) -> Optional[float]:
    """Annualized stdev of (portfolio - benchmark) daily returns.
    Returns percentage."""
    if len(daily_returns) != len(benchmark_returns) or len(daily_returns) < 2:
        return None
    diffs = [a - b for a, b in zip(daily_returns, benchmark_returns)]
    sd = statistics.stdev(diffs) if len(diffs) > 1 else 0
    return sd * math.sqrt(periods_per_year) * 100


def extended_metrics(daily_returns: Sequence[float],
                      benchmark_returns: Optional[Sequence[float]] = None
                      ) -> ExtendedMetrics:
    """Compute all extended metrics in one shot."""
    avg_uw, max_uw = time_underwater(daily_returns)
    return ExtendedMetrics(
        n=len(daily_returns),
        sortino=sortino_ratio(daily_returns),
        calmar=calmar_ratio(daily_returns),
        omega_ratio=omega_ratio(daily_returns),
        cvar_95=cvar(daily_returns, 0.95),
        cvar_99=cvar(daily_returns, 0.99),
        cvar_99_5=cvar(daily_returns, 0.995),
        avg_days_underwater=avg_uw,
        max_days_underwater=max_uw,
        max_runup_pct=max_runup(daily_returns),
        tracking_error_pct=(
            tracking_error(daily_returns, benchmark_returns)
            if benchmark_returns is not None else None
        ),
    )
