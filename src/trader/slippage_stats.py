"""Aggregate slippage statistics for the weekly digest.

The standalone `scripts/realized_slippage_tracker.py` produces a
verbose per-fill report. This module wraps the core calculation in a
function that returns a compact dict — suitable for embedding in the
weekly digest's body without overwhelming the operator.

Currently Alpaca-only: queries `GetOrdersRequest(status=ALL)` for
filled orders, compares each fill's avg-price to the closing price on
the fill day. When BROKER=public_live, this returns None with an
explanatory note — Public.com's history API needs a separate
implementation that hasn't shipped yet.

The "5 bps assumption" baked into our v3.9 backtest cost model is the
benchmark. If realized slippage materially exceeds 5 bps, the
expected-uplift estimate in `uplift_monte_carlo.py` is biased high.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Optional


def compute_recent_slippage_stats(
    days: int = 7,
) -> Optional[dict]:
    """Compute per-side slippage stats from the last `days` of fills.

    Returns a dict like:
        {
          "n_fills": int,
          "mean_bps": float,
          "median_bps": float,
          "p95_bps": float,
          "buy_mean_bps": float,
          "sell_mean_bps": float,
          "vs_5bp_assumption": str,   # "BETTER" | "WORSE" | "ON_TARGET"
          "implication_bps_per_yr": float,
        }

    Returns None if:
      - BROKER is not alpaca_* (Public.com path not yet implemented)
      - No fills in the window
      - Alpaca API call fails
    """
    broker = os.environ.get("BROKER", "alpaca_paper").lower()
    if broker not in ("alpaca_paper", "alpaca_live"):
        return None  # caller renders "not available for this broker"

    try:
        from .execute import get_client
        from .data import fetch_history
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        import pandas as pd
        client = get_client()
        after = datetime.now(timezone.utc) - timedelta(days=days)
        orders = client.get_orders(
            filter=GetOrdersRequest(
                status=QueryOrderStatus.ALL, limit=500, after=after,
            ),
        )
    except Exception:
        return None

    # Filter to filled orders with both fill_price and filled_at
    fills = []
    for o in orders:
        status_str = (
            o.status.value if hasattr(o.status, "value") else str(o.status)
        ).lower()
        if "filled" not in status_str:
            continue
        if not o.filled_at or not o.filled_avg_price:
            continue
        fills.append(o)
    if not fills:
        return None

    symbols = sorted({f.symbol for f in fills})
    try:
        earliest = min(f.filled_at for f in fills).replace(tzinfo=None) - timedelta(days=2)
        latest = max(f.filled_at for f in fills).replace(tzinfo=None) + timedelta(days=2)
        prices = fetch_history(
            symbols, start=earliest.strftime("%Y-%m-%d"),
            end=latest.strftime("%Y-%m-%d"),
        )
    except Exception:
        return None
    if prices.empty:
        return None

    buy_bps: list[float] = []
    sell_bps: list[float] = []
    for f in fills:
        sym = f.symbol
        if sym not in prices.columns:
            continue
        side = (
            f.side.value if hasattr(f.side, "value") else str(f.side)
        ).lower()
        try:
            fill_price = float(f.filled_avg_price)
            fill_date = pd.Timestamp(f.filled_at).normalize().tz_localize(None)
            sym_prices = prices[sym].dropna()
            close_idx = sym_prices.index.searchsorted(fill_date, side="right") - 1
            if close_idx < 0:
                continue
            close_price = float(sym_prices.iloc[close_idx])
            if close_price <= 0:
                continue
        except Exception:
            continue
        # Positive bps = cost to us. Buys pay more than close = positive.
        if side == "buy":
            bps = (fill_price - close_price) / close_price * 10_000
            buy_bps.append(bps)
        else:
            bps = (close_price - fill_price) / close_price * 10_000
            sell_bps.append(bps)

    all_bps = buy_bps + sell_bps
    if not all_bps:
        return None

    all_sorted = sorted(all_bps)
    n = len(all_sorted)
    mean = sum(all_sorted) / n
    median = all_sorted[n // 2]
    p95 = all_sorted[min(n - 1, int(n * 0.95))]

    # 5 bps is the v3.9 backtest cost assumption (per side).
    diff_vs_5bp = mean - 5.0
    if abs(diff_vs_5bp) < 1.0:
        vs5 = "ON_TARGET"
    elif diff_vs_5bp > 0:
        vs5 = "WORSE"
    else:
        vs5 = "BETTER"

    # Implication: at observed slippage, expected CAGR drag is
    # ~mean_bps × 2 (round-trip) × turnover. We assume ~60% annual
    # turnover (the system's typical monthly-rebalance footprint).
    implication = mean * 2 * 0.60

    return {
        "n_fills": n,
        "mean_bps": round(mean, 1),
        "median_bps": round(median, 1),
        "p95_bps": round(p95, 1),
        "buy_mean_bps": round(sum(buy_bps) / len(buy_bps), 1) if buy_bps else None,
        "sell_mean_bps": round(sum(sell_bps) / len(sell_bps), 1) if sell_bps else None,
        "vs_5bp_assumption": vs5,
        "implication_bps_per_yr": round(implication, 1),
        "broker": broker,
    }


def format_slippage_section(stats: Optional[dict], days: int = 7) -> str:
    """Render slippage stats as a section of the weekly digest."""
    if stats is None:
        return (
            f"  Slippage stats unavailable for the current broker. "
            f"(Public.com history-based slippage tracking is a future port; "
            f"on Alpaca it requires recent fills in the {days}-day window.)"
        )
    lines = [
        f"  Fills analyzed:        {stats['n_fills']}",
        f"  Mean slippage:         {stats['mean_bps']:+.1f} bps",
        f"  Median:                {stats['median_bps']:+.1f} bps",
        f"  95th percentile:       {stats['p95_bps']:+.1f} bps",
    ]
    if stats.get("buy_mean_bps") is not None:
        lines.append(f"  Buy-side mean:         {stats['buy_mean_bps']:+.1f} bps")
    if stats.get("sell_mean_bps") is not None:
        lines.append(f"  Sell-side mean:        {stats['sell_mean_bps']:+.1f} bps")
    lines.append(f"  vs 5 bps assumption:   {stats['vs_5bp_assumption']}")
    lines.append(
        f"  Implied annual drag:   ~{stats['implication_bps_per_yr']:+.1f} bps/yr"
    )
    if stats["vs_5bp_assumption"] == "WORSE":
        lines.append(
            "\n  ⚠️  Slippage exceeds the 5-bp backtest assumption. The"
            " uplift estimate\n  in RUNBOOK_MAX_RETURN.md is biased high"
            " by ~{:.1f} bps/yr.".format(
                stats["implication_bps_per_yr"] - 6.0,
            )
        )
    elif stats["vs_5bp_assumption"] == "BETTER":
        lines.append(
            "\n  ✅ Slippage is BELOW the 5-bp backtest assumption."
            " Small hidden buffer."
        )
    return "\n".join(lines)
