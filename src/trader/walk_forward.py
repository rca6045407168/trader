"""[v3.59.4 — TESTING_PRACTICES Cat 1] Walk-forward backtest harness.

Two flavors per TESTING_PRACTICES.md §1:

  • ANCHORED walk-forward: train on [start, T], test on [T, T+test_days].
    Roll T forward by step_days. Aggregate test windows.
    "Anchored" = training window grows over time.

  • ROLLING walk-forward: train on [T-train_days, T], test on [T, T+test_days].
    Tests parameter stability under shifting training distributions.

This is the standard quant-fund OOS performance presentation. Replaces
the single-shot in-sample backtest.

Usage:
    from trader.walk_forward import run_anchored_walk_forward
    results = run_anchored_walk_forward(
        strategy_fn=lambda end_date: rank_momentum(universe, end_date=end_date),
        price_fn=lambda end_date: fetch_history(universe, end=end_date),
        start="2018-01-01",
        end="2025-12-31",
        test_days=63,    # ~1 quarter
        step_days=63,    # roll quarterly
    )
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional, Sequence


@dataclass
class WalkForwardWindow:
    """One out-of-sample test window."""
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    n_picks: int = 0
    picks: list[str] = field(default_factory=list)
    period_return: Optional[float] = None
    annualized_return: Optional[float] = None
    sharpe: Optional[float] = None
    max_drawdown: Optional[float] = None
    error: Optional[str] = None


@dataclass
class WalkForwardSummary:
    """Aggregated stats across all OOS windows."""
    n_windows: int
    mean_period_return: Optional[float]
    median_period_return: Optional[float]
    mean_annualized_return: Optional[float]
    mean_sharpe: Optional[float]
    median_sharpe: Optional[float]
    sharpe_stdev: Optional[float]
    pct_windows_positive: Optional[float]
    worst_window_return: Optional[float]
    best_window_return: Optional[float]
    windows: list[WalkForwardWindow] = field(default_factory=list)


def _date_range(start: str, end: str, step_days: int) -> list[str]:
    """Return ISO date strings stepping from start to end."""
    s = datetime.fromisoformat(start)
    e = datetime.fromisoformat(end)
    out = []
    cur = s
    while cur <= e:
        out.append(cur.date().isoformat())
        cur += timedelta(days=step_days)
    return out


def _portfolio_period_return(picks: list[str], price_panel: dict) -> tuple[Optional[float], list[float]]:
    """Equal-weight portfolio total return + daily-return list over the
    panel window. price_panel: {symbol: [(date, close), ...]} sorted asc."""
    if not picks:
        return None, []
    series_per_pick: list[list[float]] = []
    for sym in picks:
        rows = price_panel.get(sym, [])
        if len(rows) < 2:
            continue
        prices = [float(p) for _, p in rows]
        rets = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0:
                rets.append((prices[i] / prices[i - 1]) - 1)
        if rets:
            series_per_pick.append(rets)
    if not series_per_pick:
        return None, []
    # Equal-weight daily portfolio return = mean of available daily returns
    n = min(len(s) for s in series_per_pick)
    daily = [sum(s[i] for s in series_per_pick) / len(series_per_pick)
             for i in range(n)]
    cum = 1.0
    for r in daily:
        cum *= (1 + r)
    return cum - 1, daily


def _stats_from_daily(daily: Sequence[float],
                       periods_per_year: int = 252) -> dict:
    if len(daily) < 2:
        return {"sharpe": None, "max_dd": None, "annualized": None}
    mean = statistics.mean(daily)
    sd = statistics.stdev(daily)
    sharpe = (mean / sd) * math.sqrt(periods_per_year) if sd > 0 else 0.0
    cum, peak, mx = 1.0, 1.0, 0.0
    for r in daily:
        cum *= (1 + r); peak = max(peak, cum)
        mx = min(mx, cum / peak - 1)
    n = len(daily)
    annualized = (cum ** (periods_per_year / n)) - 1 if cum > 0 else 0
    return {"sharpe": sharpe, "max_dd": mx, "annualized": annualized}


def run_anchored_walk_forward(
    strategy_fn: Callable[[str], list],     # (asof) → list of pick objects with .ticker
    price_panel_fn: Callable[[str, str, list[str]], dict],
    train_start: str,
    train_end: str,                          # first T (anchored start of test grid)
    test_end: str,                           # final test-window end
    test_days: int = 63,
    step_days: int = 63,
) -> WalkForwardSummary:
    """Run anchored walk-forward.

    strategy_fn(asof) returns picks AS OF asof (must respect end_date).
    price_panel_fn(start, end, symbols) returns {sym: [(date, close), ...]}
    used to compute the OOS test-window returns.
    """
    return _run_walk_forward(
        strategy_fn, price_panel_fn,
        first_test_start=train_end,
        test_end=test_end,
        test_days=test_days,
        step_days=step_days,
        rolling_train_days=None,
        train_start=train_start,
    )


def run_rolling_walk_forward(
    strategy_fn: Callable[[str], list],
    price_panel_fn: Callable[[str, str, list[str]], dict],
    train_days: int,
    first_test_start: str,
    test_end: str,
    test_days: int = 63,
    step_days: int = 63,
) -> WalkForwardSummary:
    """Run rolling walk-forward (training window slides forward)."""
    return _run_walk_forward(
        strategy_fn, price_panel_fn,
        first_test_start=first_test_start,
        test_end=test_end,
        test_days=test_days,
        step_days=step_days,
        rolling_train_days=train_days,
        train_start=None,
    )


def _run_walk_forward(strategy_fn, price_panel_fn,
                       first_test_start: str, test_end: str,
                       test_days: int, step_days: int,
                       rolling_train_days: Optional[int],
                       train_start: Optional[str]) -> WalkForwardSummary:
    windows: list[WalkForwardWindow] = []
    grid = _date_range(first_test_start, test_end, step_days)
    for asof_str in grid:
        asof = datetime.fromisoformat(asof_str)
        win_end = asof + timedelta(days=test_days)
        win_end_str = win_end.date().isoformat()
        if win_end > datetime.fromisoformat(test_end):
            break

        # Determine training window
        if rolling_train_days is not None:
            ts = (asof - timedelta(days=rolling_train_days)).date().isoformat()
            te = asof_str
        else:
            ts = train_start or asof_str
            te = asof_str

        w = WalkForwardWindow(
            train_start=ts, train_end=te,
            test_start=asof_str, test_end=win_end_str,
        )
        try:
            cands = strategy_fn(asof_str)
            picks = [c.ticker for c in cands if hasattr(c, "ticker")]
            if not picks:
                w.error = "strategy returned no picks"
                windows.append(w)
                continue
            w.picks = picks
            w.n_picks = len(picks)
            panel = price_panel_fn(asof_str, win_end_str, picks)
            tot_ret, daily = _portfolio_period_return(picks, panel)
            if tot_ret is None:
                w.error = "no daily returns over test window"
            else:
                w.period_return = tot_ret
                stats = _stats_from_daily(daily)
                w.sharpe = stats["sharpe"]
                w.max_drawdown = stats["max_dd"]
                w.annualized_return = stats["annualized"]
        except Exception as e:
            w.error = f"{type(e).__name__}: {e}"
        windows.append(w)

    # Aggregate
    valid = [w for w in windows if w.period_return is not None]
    if not valid:
        return WalkForwardSummary(n_windows=len(windows),
                                    mean_period_return=None,
                                    median_period_return=None,
                                    mean_annualized_return=None,
                                    mean_sharpe=None, median_sharpe=None,
                                    sharpe_stdev=None,
                                    pct_windows_positive=None,
                                    worst_window_return=None,
                                    best_window_return=None,
                                    windows=windows)
    rets = [w.period_return for w in valid]
    sharpes = [w.sharpe for w in valid if w.sharpe is not None]
    return WalkForwardSummary(
        n_windows=len(windows),
        mean_period_return=statistics.mean(rets),
        median_period_return=statistics.median(rets),
        mean_annualized_return=statistics.mean(
            w.annualized_return for w in valid
            if w.annualized_return is not None) if valid else None,
        mean_sharpe=statistics.mean(sharpes) if sharpes else None,
        median_sharpe=statistics.median(sharpes) if sharpes else None,
        sharpe_stdev=statistics.stdev(sharpes) if len(sharpes) > 1 else None,
        pct_windows_positive=sum(1 for r in rets if r > 0) / len(rets),
        worst_window_return=min(rets),
        best_window_return=max(rets),
        windows=windows,
    )
