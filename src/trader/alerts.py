"""Critical alerts — bypass the daily summary, fire on anything material.

Distinct from notify() which is for routine daily reports. These functions
fire IMMEDIATELY when something is wrong:
  - Reconciliation halt
  - Kill switch trip
  - Position drops > 5% intraday
  - Order error / API failure
  - Big drawdown crossing
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .notify import notify


def alert_halt(reason: str, detail: dict[str, Any] | None = None) -> dict:
    """Fire a critical halt alert. Subject prefixed [trader/warn]."""
    body_lines = [
        f"HALT triggered: {reason}",
        "",
        "The daily run did not complete. No new trades placed.",
        "",
    ]
    if detail:
        body_lines.append("Detail:")
        for k, v in detail.items():
            body_lines.append(f"  {k}: {v}")
    body_lines.extend([
        "",
        "This is an automated alert. Investigate before next scheduled run.",
        "Run: python scripts/halt.py status   to check kill switch state.",
        "Run: python scripts/run_reconcile.py to check journal vs Alpaca.",
        f"Triggered at {datetime.now().isoformat(timespec='seconds')}",
    ])
    body = "\n".join(body_lines)
    return notify(body, level="warn", subject=f"HALT: {reason[:60]}")


def alert_position_move(symbol: str, pct_move: float, mkt_value: float,
                       direction: str = "down") -> dict:
    """Fire when a single position moves > threshold intraday."""
    body = (
        f"Large intraday move on {symbol}: {pct_move*100:+.2f}%\n\n"
        f"Position market value: ${mkt_value:,.2f}\n"
        f"Direction: {direction.upper()}\n\n"
        f"This is informational. No automatic action taken.\n"
        f"Consider checking news for {symbol} (earnings, M&A, regulatory).\n"
        f"Stop-loss is at -3.5 ATR for bottom-catch positions; momentum has no stop.\n\n"
        f"Triggered at {datetime.now().isoformat(timespec='seconds')}"
    )
    return notify(body, level="warn", subject=f"{symbol} {pct_move*100:+.2f}% intraday")


def alert_kill_switch(reasons: list[str]) -> dict:
    """Fire when the kill switch trips."""
    reason_lines = "\n".join(f"  - {r}" for r in reasons)
    body = (
        f"KILL SWITCH TRIPPED\n\n"
        f"Reasons:\n{reason_lines}\n\n"
        f"All new orders blocked until manual reset.\n"
        f"To clear: python scripts/halt.py off\n"
        f"To check: python scripts/halt.py status\n\n"
        f"Triggered at {datetime.now().isoformat(timespec='seconds')}"
    )
    return notify(body, level="warn", subject="KILL SWITCH ARMED")


def alert_drawdown(current_dd: float, threshold: float, equity: float) -> dict:
    """Fire when drawdown crosses a notable threshold."""
    body = (
        f"Drawdown alert: portfolio is {current_dd*100:+.2f}% from rolling peak.\n"
        f"Threshold crossed: {threshold*100:.0f}%\n"
        f"Current equity: ${equity:,.2f}\n\n"
        f"Reminder of pre-committed rules:\n"
        f"  - At -8% drawdown from 30d peak, kill switch fires automatically.\n"
        f"  - At -15% drawdown, manual halt strongly recommended.\n"
        f"  - At -20% drawdown, do NOT change parameters under stress; review only.\n\n"
        f"Triggered at {datetime.now().isoformat(timespec='seconds')}"
    )
    return notify(body, level="warn", subject=f"DRAWDOWN {current_dd*100:+.2f}% from peak")


def alert_api_failure(component: str, error: str) -> dict:
    """Fire when an external API (Alpaca, yfinance, Anthropic) fails persistently."""
    body = (
        f"API failure: {component}\n\n"
        f"Error: {error}\n\n"
        f"Daily run may not have completed correctly.\n"
        f"Check GitHub Actions logs.\n"
        f"If sustained, consider switching feed (e.g., yfinance -> Polygon).\n\n"
        f"Triggered at {datetime.now().isoformat(timespec='seconds')}"
    )
    return notify(body, level="error", subject=f"API failure: {component}")
