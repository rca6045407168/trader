"""v3.58.3 — LowVolSleeve daily shadow runner.

Each call:
  1. Pull last 90 days of returns for the LIVE universe
  2. Run LowVolSleeve.select() to pick the 15 lowest-vol names
  3. Compute today's hypothetical equal-weight return
  4. Append (date, picks_csv, day_return, cum_equity) to data/low_vol_shadow.csv

The dashboard's Performance tab overlays this curve on the LIVE momentum
equity curve so you can see WHETHER the second sleeve is actually
delivering uncorrelated alpha BEFORE you wire it to LIVE.

Wire this in via cron (or run manually). It needs no broker creds — only
yfinance for return history. ~30s runtime.
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from trader.universe import DEFAULT_LIQUID_50  # noqa: E402
from trader.data import fetch_history  # noqa: E402
from trader.v358_world_class import LowVolSleeve  # noqa: E402


CSV_PATH = ROOT / "data" / "low_vol_shadow.csv"
HEADERS = ["date", "n_picks", "picks", "day_return", "cum_equity",
           "starting_equity"]


def _load_existing() -> tuple[list[dict], float]:
    """Return (rows, last_cum_equity). Empty file → ([], 1.0)."""
    if not CSV_PATH.exists():
        return [], 1.0
    rows = []
    with CSV_PATH.open() as f:
        for r in csv.DictReader(f):
            rows.append(r)
    if not rows:
        return [], 1.0
    try:
        last = float(rows[-1].get("cum_equity") or 1.0)
    except Exception:
        last = 1.0
    return rows, last


def main() -> int:
    print(f"=== LowVolSleeve shadow runner — {datetime.utcnow().isoformat()} ===")
    sleeve = LowVolSleeve(n_holdings=15, lookback_days=60)

    # Pull 90 days of close-price history for the universe
    end = datetime.utcnow().date()
    start = (end - timedelta(days=180)).strftime("%Y-%m-%d")
    print(f"  fetching returns: {start} → {end} for {len(DEFAULT_LIQUID_50)} symbols")
    try:
        prices = fetch_history(DEFAULT_LIQUID_50, start=start)
    except Exception as e:
        print(f"  fetch failed: {type(e).__name__}: {e}")
        return 1
    if prices.empty:
        print("  empty price history — aborting")
        return 1

    # Build return panel keyed by symbol
    returns_panel: dict[str, list[float]] = {}
    for sym in DEFAULT_LIQUID_50:
        if sym not in prices.columns:
            continue
        s = prices[sym].dropna().pct_change().dropna()
        returns_panel[sym] = s.tolist()

    if len(returns_panel) < 20:
        print(f"  too few symbols with data ({len(returns_panel)}); aborting")
        return 1

    picks = sleeve.select(returns_panel)
    print(f"  picks ({len(picks)}): {picks}")

    # Today's equal-weight return = mean of last available daily return for each pick
    last_returns = []
    for sym in picks:
        rs = returns_panel.get(sym, [])
        if rs:
            last_returns.append(rs[-1])
    if not last_returns:
        print("  no last-day returns available — aborting")
        return 1
    day_return = sum(last_returns) / len(last_returns)

    rows, last_eq = _load_existing()
    new_eq = last_eq * (1 + day_return)

    today_iso = end.isoformat()
    # Idempotency: if today's row already exists, replace it
    rows = [r for r in rows if r.get("date") != today_iso]
    rows.append({
        "date": today_iso,
        "n_picks": str(len(picks)),
        "picks": ",".join(picks),
        "day_return": f"{day_return:.6f}",
        "cum_equity": f"{new_eq:.6f}",
        "starting_equity": f"{rows[0].get('starting_equity', '1.0') if rows else '1.0'}",
    })
    rows.sort(key=lambda r: r["date"])

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader()
        w.writerows(rows)

    print(f"  wrote {len(rows)} rows → {CSV_PATH}")
    print(f"  today: day_return={day_return:+.4%} cum_equity={new_eq:.4f}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"runner failed: {type(e).__name__}: {e}")
        sys.exit(0)  # never block cron
