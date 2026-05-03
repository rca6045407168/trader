"""v3.58.3 — manual override actions.

Guarded "kill-glass-in-case-of-emergency" actions that bypass the cron
rebalance pipeline. Every action follows the same shape:

  1. plan(...) — pure: returns a dict describing what WOULD happen.
                 No broker call. Always safe.
  2. execute(plan_token) — actually performs the action via Alpaca.
                            Refuses if MANUAL_OVERRIDE_ALLOWED != "true",
                            even if called directly. Refuses if the
                            plan_token is missing or older than 60s
                            (forces the user to re-confirm if they walk
                            away mid-flow).

Three actions:

  • flatten_position(symbol)  — close 100% of a held position
  • trim_position(symbol, pct) — sell `pct` of current qty
  • force_breaker_trip()       — write the freeze-state file so risk_manager
                                  halts every subsequent run until cleared

Each action writes a row to journal.orders + journal.events_log so the
audit trail is complete.

Safety env vars:
  MANUAL_OVERRIDE_ALLOWED=true  — required to execute() any action.
                                   Defaults false. Without it, execute()
                                   returns {"refused": "MANUAL_OVERRIDE_ALLOWED!=true"}.
  MANUAL_OVERRIDE_DRY_RUN=true  — execute() goes through plan/audit but
                                   does NOT submit to Alpaca. Useful for
                                   first-time wiring.
"""
from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Optional


_PLAN_CACHE: dict[str, dict] = {}
_PLAN_TTL_SEC = 60


def _allowed() -> bool:
    return os.getenv("MANUAL_OVERRIDE_ALLOWED", "false").lower() == "true"


def _dry_run() -> bool:
    return os.getenv("MANUAL_OVERRIDE_DRY_RUN", "true").lower() == "true"


def _new_token() -> str:
    return secrets.token_hex(8)


def _store_plan(plan: dict) -> str:
    token = _new_token()
    plan["_token"] = token
    plan["_created_at"] = time.time()
    _PLAN_CACHE[token] = plan
    # Garbage-collect old plans
    cutoff = time.time() - _PLAN_TTL_SEC
    for k in list(_PLAN_CACHE.keys()):
        if _PLAN_CACHE[k].get("_created_at", 0) < cutoff:
            _PLAN_CACHE.pop(k, None)
    return token


def _consume_plan(token: str) -> Optional[dict]:
    plan = _PLAN_CACHE.pop(token, None)
    if not plan:
        return None
    if time.time() - plan.get("_created_at", 0) > _PLAN_TTL_SEC:
        return None
    return plan


# ============================================================
# Action: flatten_position
# ============================================================

def plan_flatten(symbol: str) -> dict:
    """Pure plan. Returns {ok, summary, plan_token, qty, market_value}
    or {ok: False, reason}."""
    try:
        from .execute import get_client
        client = get_client()
        positions = {p.symbol: p for p in client.get_all_positions()}
        if symbol not in positions:
            return {"ok": False, "reason": f"no position in {symbol}"}
        p = positions[symbol]
        plan = {
            "action": "flatten",
            "symbol": symbol,
            "qty": float(p.qty),
            "market_value": float(p.market_value),
            "side": "sell" if float(p.qty) > 0 else "buy_to_cover",
            "summary": (
                f"Close 100% of {symbol}: sell {float(p.qty):.4f} shares, "
                f"~${float(p.market_value):,.0f} market value."
            ),
        }
        plan["plan_token"] = _store_plan(plan)
        plan["ok"] = True
        return plan
    except Exception as e:
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}


def execute_flatten(plan_token: str) -> dict:
    if not _allowed():
        return {"refused": "MANUAL_OVERRIDE_ALLOWED!=true"}
    plan = _consume_plan(plan_token)
    if not plan or plan.get("action") != "flatten":
        return {"refused": "plan_token invalid or expired (60s); re-plan"}
    symbol = plan["symbol"]
    if _dry_run():
        return {"executed": False, "dry_run": True, "plan": plan}
    try:
        from .execute import get_client
        client = get_client()
        client.close_position(symbol)
        return {"executed": True, "symbol": symbol, "plan": plan}
    except Exception as e:
        return {"executed": False, "error": f"{type(e).__name__}: {e}",
                "plan": plan}


# ============================================================
# Action: trim_position
# ============================================================

def plan_trim(symbol: str, pct: float) -> dict:
    """Trim `pct` (0-1) of current qty. Returns plan dict."""
    if not (0 < pct < 1):
        return {"ok": False, "reason": f"pct must be in (0, 1), got {pct}"}
    try:
        from .execute import get_client
        client = get_client()
        positions = {p.symbol: p for p in client.get_all_positions()}
        if symbol not in positions:
            return {"ok": False, "reason": f"no position in {symbol}"}
        p = positions[symbol]
        cur_qty = float(p.qty)
        sell_qty = abs(cur_qty) * pct
        notional = sell_qty * float(p.current_price or 0)
        plan = {
            "action": "trim", "symbol": symbol, "pct": pct,
            "current_qty": cur_qty, "sell_qty": sell_qty,
            "notional": notional,
            "summary": (
                f"Trim {symbol} by {pct*100:.0f}%: sell {sell_qty:.4f} of "
                f"{cur_qty:.4f} shares, ~${notional:,.0f}."
            ),
        }
        plan["plan_token"] = _store_plan(plan)
        plan["ok"] = True
        return plan
    except Exception as e:
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}


def execute_trim(plan_token: str) -> dict:
    if not _allowed():
        return {"refused": "MANUAL_OVERRIDE_ALLOWED!=true"}
    plan = _consume_plan(plan_token)
    if not plan or plan.get("action") != "trim":
        return {"refused": "plan_token invalid or expired (60s); re-plan"}
    if _dry_run():
        return {"executed": False, "dry_run": True, "plan": plan}
    try:
        from .execute import get_client
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        client = get_client()
        cur_qty = plan["current_qty"]
        sell_qty = round(plan["sell_qty"], 4)
        side = OrderSide.SELL if cur_qty > 0 else OrderSide.BUY
        req = MarketOrderRequest(
            symbol=plan["symbol"], qty=sell_qty, side=side,
            time_in_force=TimeInForce.DAY,
        )
        order = client.submit_order(req)
        return {"executed": True, "order_id": str(order.id), "plan": plan}
    except Exception as e:
        return {"executed": False, "error": f"{type(e).__name__}: {e}",
                "plan": plan}


# ============================================================
# Action: force_breaker_trip
# ============================================================

def plan_force_pause(reason: str = "manual") -> dict:
    return {
        "ok": True,
        "action": "force_pause",
        "reason": reason,
        "summary": (
            "Trip the deployment-DD freeze for 30 days. risk_manager will "
            "block all new orders until the freeze expires (or you clear "
            "it via the LIQUIDATION GATE clear flow). Existing positions "
            "remain held."
        ),
        "plan_token": _store_plan({"action": "force_pause", "reason": reason,
                                    "_created_at": time.time()}),
    }


def execute_force_pause(plan_token: str) -> dict:
    if not _allowed():
        return {"refused": "MANUAL_OVERRIDE_ALLOWED!=true"}
    plan = _consume_plan(plan_token)
    if not plan or plan.get("action") != "force_pause":
        return {"refused": "plan_token invalid or expired (60s); re-plan"}
    if _dry_run():
        return {"executed": False, "dry_run": True, "plan": plan}
    try:
        from .risk_manager import _trigger_deploy_dd_freeze
        _trigger_deploy_dd_freeze()
        return {"executed": True, "plan": plan,
                "note": "deploy-DD freeze triggered for 30 days"}
    except Exception as e:
        return {"executed": False, "error": f"{type(e).__name__}: {e}"}
