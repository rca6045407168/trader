"""[v3.59.3 — TESTING_PRACTICES Cat 10] Transaction Cost Analysis.

Closes the slippage_log loop with statistics + alert thresholds.

After every trading day, journal.slippage_log has rows with
decision_mid + notional. Once slippage_reconcile.py fills in fill_price
+ slippage_bps, this module computes:

  • Rolling 30d / 90d / all-time average slippage in bps
  • Worst fills (top-N by absolute bps)
  • Per-symbol breakdown
  • Per-side (buy vs sell) breakdown
  • Distribution stats (median, 95th percentile, max)
  • Alert: rolling 30d > 2× the assumed slippage in backtest (5bps default)

These stats inform whether to switch order types (MOC, limit, TWAP) or
broker.

Usage:
    from trader.tca import compute_tca, alert_if_slippage_high
    stats = compute_tca()
    alert_if_slippage_high(stats, backtest_assumption_bps=5.0)
"""
from __future__ import annotations

import sqlite3
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .config import DATA_DIR


DB_PATH = DATA_DIR / "journal.db"
DEFAULT_BACKTEST_SLIPPAGE_BPS = 5.0


def _query(sql: str, params: tuple = ()) -> list[tuple]:
    if not DB_PATH.exists():
        return []
    try:
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as c:
            return c.execute(sql, params).fetchall()
    except Exception:
        return []


def compute_tca(window_days: int = 30) -> dict:
    """Compute TCA stats over the last `window_days` of fills."""
    cutoff = (datetime.utcnow() - timedelta(days=window_days)).isoformat()
    rows = _query(
        "SELECT ts, symbol, side, decision_mid, fill_price, "
        "slippage_bps, notional FROM slippage_log "
        "WHERE slippage_bps IS NOT NULL AND ts >= ? "
        "ORDER BY ts DESC",
        (cutoff,))
    if not rows:
        return {
            "ok": False, "n_fills": 0,
            "message": f"no fills with computed slippage_bps in last {window_days}d",
        }

    bps_all = [float(r[5]) for r in rows]
    n = len(bps_all)
    mean_bps = statistics.mean(bps_all)
    median_bps = statistics.median(bps_all)
    sd_bps = statistics.stdev(bps_all) if n > 1 else 0
    sorted_abs = sorted([abs(b) for b in bps_all])
    p95 = sorted_abs[int(0.95 * (n - 1))] if n > 1 else sorted_abs[0]
    worst = sorted([(abs(r[5]), r) for r in rows], reverse=True)[:5]

    # Per-side
    by_side = {"buy": [], "sell": []}
    for r in rows:
        side = (r[2] or "").lower()
        if side in by_side:
            by_side[side].append(float(r[5]))

    # Per-symbol
    by_symbol: dict[str, list[float]] = {}
    for r in rows:
        sym = r[1]
        by_symbol.setdefault(sym, []).append(float(r[5]))
    per_symbol_stats = [
        {"symbol": sym, "n": len(v),
         "mean_bps": statistics.mean(v),
         "max_abs_bps": max(abs(x) for x in v)}
        for sym, v in by_symbol.items()
    ]
    per_symbol_stats.sort(key=lambda d: d["mean_bps"], reverse=True)

    return {
        "ok": True,
        "window_days": window_days,
        "n_fills": n,
        "mean_bps": mean_bps,
        "median_bps": median_bps,
        "stdev_bps": sd_bps,
        "p95_abs_bps": p95,
        "worst_5": [
            {"ts": r[0], "symbol": r[1], "side": r[2],
             "decision_mid": r[3], "fill_price": r[4],
             "slippage_bps": r[5], "notional": r[6]}
            for _, r in worst
        ],
        "per_side": {
            "buy": {"n": len(by_side["buy"]),
                     "mean_bps": statistics.mean(by_side["buy"]) if by_side["buy"] else None},
            "sell": {"n": len(by_side["sell"]),
                      "mean_bps": statistics.mean(by_side["sell"]) if by_side["sell"] else None},
        },
        "per_symbol": per_symbol_stats[:20],
    }


def alert_if_slippage_high(tca: dict,
                             backtest_assumption_bps: float = DEFAULT_BACKTEST_SLIPPAGE_BPS,
                             multiplier: float = 2.0) -> dict:
    """Returns {alert: bool, severity, message}.

    Alerts if rolling-window mean abs slippage exceeds
    backtest_assumption × multiplier. Default 2× → assumption 5bp,
    threshold 10bp.
    """
    if not tca.get("ok"):
        return {"alert": False, "severity": "info",
                "message": "no TCA stats computed"}
    abs_mean = abs(tca.get("mean_bps", 0))
    threshold = backtest_assumption_bps * multiplier
    if abs_mean > threshold:
        return {
            "alert": True, "severity": "warn",
            "message": (f"30d mean abs slippage {abs_mean:.1f}bp > "
                         f"{threshold:.1f}bp ({multiplier}× backtest "
                         f"assumption {backtest_assumption_bps:.1f}bp). "
                         f"Consider MOC orders or broker change."),
        }
    return {
        "alert": False, "severity": "info",
        "message": (f"30d mean abs slippage {abs_mean:.1f}bp within "
                     f"{threshold:.1f}bp tolerance"),
    }
