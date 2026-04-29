"""Realized slippage tracker.

Pulls Alpaca filled-order history, compares each fill price to the closing
price on the order's submission day. Reports realized slippage in bps so we
can compare against the 5bp assumption baked into v3.9 backtests.

Usage:
  python scripts/realized_slippage_tracker.py [--days 30]

Output:
  - Per-fill table (date, symbol, side, fill_price, close_price, slippage_bps)
  - Aggregate stats: mean, median, p95 slippage
  - Comparison vs 5bp backtest assumption: are we paying more or less?
  - Implication: at observed slippage, expected CAGR drag is X bps/yr

If realized slippage is materially higher than 5bp, our forward expected
returns need to be revised down. If it's materially lower, we have a hidden
buffer.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from trader.execute import get_client
from trader.data import fetch_history


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30, help="Days of fill history to analyze")
    args = p.parse_args()

    print("=" * 80)
    print("REALIZED SLIPPAGE TRACKER")
    print("=" * 80)
    print(f"Lookback: {args.days} days")
    print()

    client = get_client()

    # Pull filled orders. Alpaca SDK paginates; we use a generous limit.
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    after = datetime.now(timezone.utc) - timedelta(days=args.days)
    try:
        orders = client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500, after=after)
        )
    except Exception as e:
        print(f"Failed to fetch orders: {e}")
        return 1

    fills = [o for o in orders if str(o.status) == "OrderStatus.FILLED" or
             (hasattr(o.status, 'value') and o.status.value == 'filled')]
    if not fills:
        print(f"No filled orders in last {args.days} days. Nothing to analyze.")
        return 0

    print(f"Found {len(fills)} filled orders.\n")

    # Get unique tickers + the date range we need closing prices for
    symbols = sorted(set(f.symbol for f in fills))
    earliest = min(f.filled_at for f in fills if f.filled_at)
    latest = max(f.filled_at for f in fills if f.filled_at)
    earliest_str = (earliest - timedelta(days=2)).strftime("%Y-%m-%d")
    latest_str = (latest + timedelta(days=2)).strftime("%Y-%m-%d")

    try:
        prices = fetch_history(symbols, start=earliest_str, end=latest_str)
    except Exception as e:
        print(f"Failed to fetch closing prices: {e}")
        return 1

    if prices.empty:
        print("No closing-price data available.")
        return 1

    # Per-fill slippage
    rows = []
    for f in fills:
        if not f.filled_at or not f.filled_avg_price:
            continue
        sym = f.symbol
        side = (f.side.value if hasattr(f.side, "value") else str(f.side)).lower()
        fill_price = float(f.filled_avg_price)
        fill_date = pd.Timestamp(f.filled_at).normalize().tz_localize(None)
        # Get the close on the fill day (or nearest prior trading day)
        if sym not in prices.columns:
            continue
        sym_prices = prices[sym].dropna()
        # Find the close on or before the fill day
        try:
            close_idx = sym_prices.index.searchsorted(fill_date, side="right") - 1
            if close_idx < 0:
                continue
            close_price = float(sym_prices.iloc[close_idx])
        except Exception:
            continue
        if close_price <= 0:
            continue
        # Slippage: for buys, if we paid MORE than close, slippage is positive (cost)
        #          for sells, if we got LESS than close, slippage is positive (cost)
        if side == "buy":
            slippage_bps = (fill_price - close_price) / close_price * 10_000
        else:
            slippage_bps = (close_price - fill_price) / close_price * 10_000
        rows.append({
            "date": fill_date.date(),
            "symbol": sym,
            "side": side,
            "fill_price": fill_price,
            "close_price": close_price,
            "slippage_bps": slippage_bps,
        })

    if not rows:
        print("No analyzable fills (missing prices or fill_avg_price).")
        return 0

    df = pd.DataFrame(rows).sort_values("date")
    print(f"{'date':<12s} {'symbol':<8s} {'side':<5s} {'fill':>10s} {'close':>10s} {'slippage':>12s}")
    print("-" * 60)
    for _, r in df.iterrows():
        print(f"{str(r['date']):<12s} {r['symbol']:<8s} {r['side']:<5s} ${r['fill_price']:>9.2f} ${r['close_price']:>9.2f} {r['slippage_bps']:>+10.1f} bps")

    # Aggregate stats
    print()
    print("AGGREGATE SLIPPAGE STATS:")
    s = df["slippage_bps"]
    print(f"  count:    {len(s)}")
    print(f"  mean:     {s.mean():>+6.1f} bps")
    print(f"  median:   {s.median():>+6.1f} bps")
    print(f"  std:      {s.std():>+6.1f} bps")
    print(f"  p25:      {s.quantile(0.25):>+6.1f} bps")
    print(f"  p75:      {s.quantile(0.75):>+6.1f} bps")
    print(f"  p95:      {s.quantile(0.95):>+6.1f} bps")
    print(f"  worst:    {s.max():>+6.1f} bps  ({df.loc[s.idxmax(), 'symbol']} on {df.loc[s.idxmax(), 'date']})")
    print()

    # Comparison vs backtest assumption
    BACKTEST_ASSUMED_BPS = 5.0
    mean_slippage = float(s.mean())
    delta = mean_slippage - BACKTEST_ASSUMED_BPS

    print(f"vs BACKTEST ASSUMPTION ({BACKTEST_ASSUMED_BPS} bps in v3.9):")
    if abs(delta) < 2.0:
        print(f"  ✓ Realized {mean_slippage:+.1f} bps ≈ assumed {BACKTEST_ASSUMED_BPS:+.1f} bps. Backtest is realistic.")
    elif delta > 0:
        print(f"  ⚠ Realized {mean_slippage:+.1f} bps > assumed {BACKTEST_ASSUMED_BPS:+.1f} bps.")
        print(f"     Excess drag: ~{delta * 24 / 100:.2f}pp/yr at 24 trades/yr.")
        print(f"     Forward expected CAGR should be revised DOWN by this amount.")
    else:
        print(f"  ✓ Realized {mean_slippage:+.1f} bps < assumed {BACKTEST_ASSUMED_BPS:+.1f} bps.")
        print(f"     Hidden buffer: ~{abs(delta) * 24 / 100:.2f}pp/yr we're not modeling.")

    # Caveats
    print()
    print("CAVEATS:")
    print("  - Comparing fill_price to same-day CLOSE. If we order at open,")
    print("    open-to-close drift contaminates this — actual slippage vs the")
    print("    decision-time price requires order timestamp + intraday data.")
    print("  - This is paper trading — Alpaca paper sim may have less slippage")
    print("    than real execution. Real-world tends to be 2-3x worse.")
    print("  - Sample is small until we have months of fills.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
