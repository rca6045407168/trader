"""Intraday risk monitor — runs every 30 min during market hours.

Catches flash-crash scenarios in 30 min instead of 24 hours (current daily-only
loop). DEFENSIVE only — never places orders, never amplifies, never overrides
anything in main.py. Triggers freeze states which the next daily-run will see.

Two thresholds:
  1. Intraday DD vs day-open equity > INTRADAY_DD_FREEZE_PCT (default -8%)
     → triggers DAILY_LOSS_FREEZE (48h, same gate as risk_manager's daily check)
  2. Cumulative DD vs deployment_anchor > MAX_DEPLOY_DD_FREEZE_PCT (-25%)
     → triggers DEPLOY_DD_FREEZE (30 days, same gate as risk_manager)

Why two thresholds: the daily-run gate fires AFTER the close on the day a -6%
loss completed. The intraday gate fires DURING the day if a single-session move
is severe — closing positions can't help (we don't day-trade), but the freeze
prevents the next morning's rebalance from compounding the problem.

State written to data/intraday_risk_log.json + data/risk_freeze_state.json.
Reuses risk_manager's freeze infrastructure.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Optional

from .config import DATA_DIR


# Thresholds — tighter than daily-loss because intraday moves can amplify
INTRADAY_DD_FREEZE_PCT = 0.08   # -8% intraday vs day-open
INTRADAY_WARN_PCT = 0.04        # -4% emit warning (no freeze)

INTRADAY_LOG_PATH = DATA_DIR / "intraday_risk_log.json"


@dataclass
class IntradayCheck:
    """Result of one intraday risk evaluation."""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    equity_now: float = 0.0
    day_open_equity: Optional[float] = None
    intraday_pnl_pct: Optional[float] = None
    deploy_dd_pct: Optional[float] = None
    action: str = "ok"  # ok | warn | freeze_intraday | freeze_deploy_dd
    rationale: str = ""
    error: Optional[str] = None


def _load_log() -> list[dict]:
    if not INTRADAY_LOG_PATH.exists():
        return []
    try:
        return json.loads(INTRADAY_LOG_PATH.read_text())
    except Exception:
        return []


def _append_log(entry: dict) -> None:
    log = _load_log()
    log.append(entry)
    # Keep last 1000 entries (~3 weeks at 30-min cadence)
    log = log[-1000:]
    INTRADAY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    INTRADAY_LOG_PATH.write_text(json.dumps(log, indent=2))


def _fetch_day_open_equity_from_log() -> Optional[float]:
    """Walk back through log to find the FIRST entry from today (US Eastern).

    Heuristic: timestamp UTC → ET conversion implicit; we just take the
    earliest entry in the last 24h. Good enough for monthly-rebalance system.
    """
    log = _load_log()
    if not log:
        return None
    now = datetime.utcnow()
    today_str = now.date().isoformat()
    today_entries = [e for e in log if e.get("timestamp", "").startswith(today_str)]
    if not today_entries:
        # Fallback: most recent entry from yesterday (broker close)
        yest = log[-1]
        return yest.get("equity_now")
    return today_entries[0].get("equity_now")


def _fetch_broker_equity() -> tuple[Optional[float], Optional[str]]:
    """Pull current broker equity. Returns (equity, error_msg)."""
    try:
        from .execute import get_client
        client = get_client()
        return float(client.get_account().equity), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def check() -> IntradayCheck:
    """One intraday risk evaluation. Idempotent (writes log, may trigger freeze).

    Returns the IntradayCheck so the workflow can decide whether to alert.
    """
    out = IntradayCheck()

    equity, err = _fetch_broker_equity()
    if equity is None:
        out.error = err
        out.rationale = f"broker fetch failed: {err}"
        _append_log({"timestamp": out.timestamp, "error": err})
        return out

    out.equity_now = equity

    # Intraday DD vs day-open
    day_open = _fetch_day_open_equity_from_log()
    out.day_open_equity = day_open
    if day_open and day_open > 0:
        out.intraday_pnl_pct = (equity - day_open) / day_open
        if out.intraday_pnl_pct < -INTRADAY_DD_FREEZE_PCT:
            out.action = "freeze_intraday"
            out.rationale = (f"intraday DD {out.intraday_pnl_pct:.2%} vs day-open "
                             f"${day_open:.0f} exceeds -{INTRADAY_DD_FREEZE_PCT:.0%} "
                             f"threshold; firing 48h freeze")
            from .risk_manager import _trigger_daily_loss_freeze
            _trigger_daily_loss_freeze()
        elif out.intraday_pnl_pct < -INTRADAY_WARN_PCT:
            out.action = "warn"
            out.rationale = (f"intraday DD {out.intraday_pnl_pct:.2%} vs day-open "
                             f"${day_open:.0f}; warn threshold")

    # Cumulative DD vs deployment anchor
    try:
        from .deployment_anchor import drawdown_from_deployment
        from .risk_manager import (
            MAX_DEPLOY_DD_FREEZE_PCT, MAX_DEPLOY_DD_LIQUIDATION_PCT,
            _trigger_deploy_dd_freeze, _trigger_liquidation_gate,
        )
        deploy_dd, anchor = drawdown_from_deployment(equity)
        out.deploy_dd_pct = deploy_dd
        if deploy_dd < -MAX_DEPLOY_DD_LIQUIDATION_PCT:
            out.action = "freeze_liquidation"
            out.rationale = (f"deployment DD {deploy_dd:.2%} vs ${anchor.equity_at_deploy:.0f} "
                             f"exceeds liquidation threshold; tripping liquidation gate "
                             f"(written post-mortem required to clear)")
            _trigger_liquidation_gate()
        elif deploy_dd < -MAX_DEPLOY_DD_FREEZE_PCT:
            # Don't override an intraday-freeze action; concatenate
            extra = (f"deployment DD {deploy_dd:.2%} vs ${anchor.equity_at_deploy:.0f} "
                     f"exceeds -{MAX_DEPLOY_DD_FREEZE_PCT:.0%}; firing 30-day freeze")
            if out.action == "ok":
                out.action = "freeze_deploy_dd"
                out.rationale = extra
            else:
                out.rationale = f"{out.rationale}; ALSO {extra}"
            _trigger_deploy_dd_freeze()
    except Exception as e:
        # Non-fatal: deployment anchor not yet set, etc.
        if out.action == "ok":
            out.rationale = f"intraday OK; deploy-anchor unavailable ({type(e).__name__})"

    if out.action == "ok" and not out.rationale:
        out.rationale = (f"OK equity=${equity:.0f}"
                         + (f" intraday={out.intraday_pnl_pct:+.2%}" if out.intraday_pnl_pct is not None else "")
                         + (f" deploy_dd={out.deploy_dd_pct:+.2%}" if out.deploy_dd_pct is not None else ""))

    _append_log({
        "timestamp": out.timestamp,
        "equity_now": out.equity_now,
        "day_open_equity": out.day_open_equity,
        "intraday_pnl_pct": out.intraday_pnl_pct,
        "deploy_dd_pct": out.deploy_dd_pct,
        "action": out.action,
        "rationale": out.rationale,
    })
    return out
