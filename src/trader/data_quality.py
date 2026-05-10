"""Data-quality monitoring for the yfinance fetch path.

Silent data corruption is the #1 invisible failure mode. yfinance
occasionally returns sentinel-value zeros, repeated last-known-good
prices for halted names, or sudden jumps that aren't real splits.
A blind strategy run on bad data produces real orders against
phantom signals.

This module surfaces three sanity checks the operator can run before
build_targets() to catch obvious corruption:

  1. last_row_freshness    — most-recent row is within N business
                              days of `asof`
  2. extreme_jump_check    — no name moved more than +/- 20% day-over-
                              day without a corresponding SPY move
  3. dead_zero_check       — no name has a zero or NaN price in the
                              last 5 rows (yfinance occasionally
                              returns 0.0 for halted/de-listed names)

Output: a list of (severity, sym, message) tuples. Severity is
"HALT" or "WARN". Caller decides whether to halt or just log.

Wired into main.py pre-flight (after the kill-switch clear, before
build_targets). Env-gated by DATA_QUALITY_HALT_ENABLED (default 1).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd


@dataclass
class QualityIssue:
    severity: str   # "HALT" | "WARN"
    sym: str
    check: str
    message: str

    def __str__(self) -> str:
        return f"  {self.severity}: {self.sym} ({self.check}) — {self.message}"


def check_freshness(prices: pd.DataFrame,
                     asof: date,
                     max_stale_business_days: int = 3) -> list[QualityIssue]:
    """Flag if the most-recent row is more than N business days behind
    `asof`. yfinance lags ~1 trading day; if it's 3+ days late, the
    fetch likely failed silently."""
    issues = []
    if prices is None or prices.empty:
        return [QualityIssue("HALT", "_panel", "freshness",
                              "price panel is empty")]
    last_idx = prices.index[-1]
    if hasattr(last_idx, "date"):
        last_d = last_idx.date()
    else:
        last_d = last_idx
    # Count business days between last row and asof
    business_days_lag = len(pd.bdate_range(last_d, asof)) - 1
    if business_days_lag > max_stale_business_days:
        issues.append(QualityIssue(
            "HALT", "_panel", "freshness",
            f"latest row is {business_days_lag} business days old "
            f"(last={last_d}, asof={asof}, max={max_stale_business_days})",
        ))
    return issues


def check_extreme_jumps(prices: pd.DataFrame,
                         spy_col: str = "SPY",
                         pct_threshold: float = 0.20) -> list[QualityIssue]:
    """Flag any name that moved more than `pct_threshold` day-over-day
    on the last fetched bar, UNLESS SPY moved a comparable amount
    (in which case the move is real, e.g., flash crash day)."""
    issues = []
    if prices is None or len(prices) < 2:
        return issues
    last = prices.iloc[-1]
    prev = prices.iloc[-2]
    # SPY move for context
    spy_move = 0.0
    if spy_col in prices.columns and not pd.isna(last.get(spy_col)) \
            and not pd.isna(prev.get(spy_col)) and prev[spy_col] > 0:
        spy_move = float(last[spy_col] / prev[spy_col] - 1)
    for sym in prices.columns:
        if pd.isna(last[sym]) or pd.isna(prev[sym]) or prev[sym] <= 0:
            continue
        ret = float(last[sym] / prev[sym] - 1)
        if abs(ret) > pct_threshold:
            # Real moves track SPY direction reasonably; flag only
            # moves that DIVERGE from SPY by > 15 % (likely data error)
            divergence = abs(ret - spy_move)
            if divergence > 0.15:
                issues.append(QualityIssue(
                    "WARN", sym, "extreme_jump",
                    f"day-over-day move {ret*100:+.1f}% diverges from "
                    f"SPY's {spy_move*100:+.1f}% by {divergence*100:.1f}% — "
                    "verify before trading",
                ))
    return issues


def check_dead_zeros(prices: pd.DataFrame,
                      lookback_rows: int = 5) -> list[QualityIssue]:
    """Flag any name with zero or NaN in the last N rows. yfinance
    returns 0.0 for halted / delisted / data-missing names."""
    issues = []
    if prices is None or len(prices) == 0:
        return issues
    recent = prices.tail(lookback_rows)
    for sym in recent.columns:
        col = recent[sym]
        n_zero = int((col == 0).sum())
        n_nan = int(col.isna().sum())
        if n_zero > 0:
            issues.append(QualityIssue(
                "WARN", sym, "dead_zero",
                f"{n_zero} zero price(s) in last {lookback_rows} rows "
                "— likely halted/delisted/data-missing in feed",
            ))
        if n_nan == lookback_rows:
            issues.append(QualityIssue(
                "HALT", sym, "dead_nan",
                f"all {lookback_rows} recent rows are NaN — no data",
            ))
    return issues


def run_all_checks(prices: pd.DataFrame,
                    asof: Optional[date] = None,
                    spy_col: str = "SPY") -> list[QualityIssue]:
    """Run every check, return concatenated issue list."""
    if asof is None:
        asof = date.today()
    issues = []
    issues.extend(check_freshness(prices, asof=asof))
    issues.extend(check_extreme_jumps(prices, spy_col=spy_col))
    issues.extend(check_dead_zeros(prices))
    return issues


def should_halt(issues: list[QualityIssue]) -> bool:
    """True iff at least one HALT-severity issue, AND env hasn't
    explicitly opted out."""
    if os.environ.get("DATA_QUALITY_HALT_ENABLED", "1") != "1":
        return False
    return any(i.severity == "HALT" for i in issues)


def format_issues(issues: list[QualityIssue]) -> str:
    if not issues:
        return "data quality: all checks pass"
    halts = [i for i in issues if i.severity == "HALT"]
    warns = [i for i in issues if i.severity == "WARN"]
    lines = []
    if halts:
        lines.append(f"  {len(halts)} HALT issue(s):")
        for h in halts:
            lines.append(str(h))
    if warns:
        lines.append(f"  {len(warns)} WARN issue(s):")
        for w in warns[:10]:  # cap at 10 to avoid spam
            lines.append(str(w))
        if len(warns) > 10:
            lines.append(f"  ... and {len(warns) - 10} more")
    return "\n".join(lines)
