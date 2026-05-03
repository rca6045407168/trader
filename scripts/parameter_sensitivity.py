"""[v3.59.4 — TESTING_PRACTICES Cat 5] Parameter sensitivity grid.

Vary each numeric strategy parameter ±20% and observe whether results
are stable or fragile. A strategy that earns +1.5 Sharpe at top_n=15
but +0.4 Sharpe at top_n=13 is overfit to the parameter.

Per TESTING_PRACTICES.md §5: strategy passes if the Sharpe surface is
roughly flat (±10%) over the central plateau. Spike at exactly one
parameter combination = sample-fit, not real signal.

Run:
  python scripts/parameter_sensitivity.py [--n-windows 8] [--start 2018-01-01]

Output:
  • prints a grid table to stdout (top_n × lookback_months)
  • writes data/parameter_sensitivity.json
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# Default sensitivity ranges (±20% of canonical settings)
TOP_N_GRID = [12, 13, 14, 15, 16, 17, 18]            # ±20% around 15
LOOKBACK_GRID = [10, 11, 12, 13, 14]                  # ±20% around 12
SKIP_GRID = [0, 1, 2]                                  # 0 = no skip


def fetch_panel(symbols: list[str], start: str, end: str) -> dict:
    """Returns {symbol: [(date, close), ...]} sorted ascending."""
    try:
        import yfinance as yf
        df = yf.download(symbols, start=start, end=end,
                          progress=False, auto_adjust=True)
        if df is None or df.empty:
            return {}
        out: dict[str, list] = {}
        if "Close" in df.columns.get_level_values(0):
            close = df["Close"]
        else:
            close = df  # single-symbol shape
        for sym in close.columns if hasattr(close, "columns") else [symbols[0]]:
            try:
                series = close[sym].dropna() if hasattr(close, "columns") else close.dropna()
                out[sym] = [(idx.date(), float(v)) for idx, v in series.items()]
            except Exception:
                continue
        return out
    except Exception as e:
        print(f"  fetch_panel error: {e}")
        return {}


def evaluate_window(picks: list[str], panel: dict,
                      win_start: str, win_end: str) -> dict:
    """Compute total return + Sharpe for an equal-weight portfolio
    over [win_start, win_end]."""
    win_s = datetime.fromisoformat(win_start).date()
    win_e = datetime.fromisoformat(win_end).date()
    daily_returns: list[float] = []
    # For each trading day in the window, compute mean of the picks' returns
    all_dates = sorted(set(
        d for sym in picks if sym in panel
        for d, _ in panel[sym] if win_s <= d <= win_e
    ))
    by_sym = {sym: dict(panel[sym]) for sym in picks if sym in panel}
    for i in range(1, len(all_dates)):
        prev_d, cur_d = all_dates[i - 1], all_dates[i]
        rs = []
        for sym, m in by_sym.items():
            if prev_d in m and cur_d in m and m[prev_d] > 0:
                rs.append((m[cur_d] / m[prev_d]) - 1)
        if rs:
            daily_returns.append(sum(rs) / len(rs))
    if not daily_returns:
        return {"sharpe": None, "return_pct": None, "n_days": 0}
    mean = statistics.mean(daily_returns)
    sd = statistics.stdev(daily_returns) if len(daily_returns) > 1 else 0
    sharpe = (mean / sd) * math.sqrt(252) if sd > 0 else 0
    cum = 1.0
    for r in daily_returns:
        cum *= (1 + r)
    return {"sharpe": sharpe, "return_pct": (cum - 1) * 100,
            "n_days": len(daily_returns)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-windows", type=int, default=6)
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--window-days", type=int, default=63)
    args = ap.parse_args()

    end = args.end or datetime.utcnow().date().isoformat()
    print("=" * 78)
    print(f"Parameter sensitivity grid · {args.start} → {end}")
    print("=" * 78)

    from trader.universe import DEFAULT_LIQUID_50
    from trader.strategy import rank_momentum

    universe = DEFAULT_LIQUID_50
    panel = fetch_panel(universe, args.start, end)
    if not panel:
        print("ERROR: could not fetch price panel")
        return 1
    print(f"  Universe: {len(universe)} symbols, {len(panel)} with data")

    # Pick N evenly-spaced as-of dates from start through end-window_days
    e_dt = datetime.fromisoformat(end)
    s_dt = datetime.fromisoformat(args.start) + timedelta(days=400)  # need history for momentum
    span = (e_dt - timedelta(days=args.window_days)) - s_dt
    asof_dates = [
        (s_dt + timedelta(days=int(span.days * i / max(args.n_windows - 1, 1)))).date().isoformat()
        for i in range(args.n_windows)
    ]
    print(f"  As-of dates: {asof_dates}")

    results = []
    for top_n in TOP_N_GRID:
        for lookback in LOOKBACK_GRID:
            sharpes = []
            returns = []
            for asof in asof_dates:
                win_start = asof
                win_end_dt = datetime.fromisoformat(asof) + timedelta(days=args.window_days)
                win_end = win_end_dt.date().isoformat()
                try:
                    cands = rank_momentum(universe,
                                           lookback_months=lookback,
                                           top_n=top_n,
                                           end_date=asof)
                    picks = [c.ticker for c in cands]
                except Exception as e:
                    continue
                if not picks:
                    continue
                stats = evaluate_window(picks, panel, win_start, win_end)
                if stats["sharpe"] is not None:
                    sharpes.append(stats["sharpe"])
                if stats["return_pct"] is not None:
                    returns.append(stats["return_pct"])
            results.append({
                "top_n": top_n,
                "lookback_months": lookback,
                "mean_sharpe": statistics.mean(sharpes) if sharpes else None,
                "median_sharpe": statistics.median(sharpes) if sharpes else None,
                "stdev_sharpe": statistics.stdev(sharpes) if len(sharpes) > 1 else None,
                "mean_return_pct": statistics.mean(returns) if returns else None,
                "n_windows": len(sharpes),
            })

    # Summary
    print("\n=== Mean Sharpe across windows by (top_n, lookback) ===")
    print(f"  {'top_n':<8} | " + " | ".join(f"{lb:>6}m" for lb in LOOKBACK_GRID))
    print("  " + "-" * (8 + 9 * len(LOOKBACK_GRID)))
    for top_n in TOP_N_GRID:
        cells = []
        for lb in LOOKBACK_GRID:
            r = next((x for x in results if x["top_n"] == top_n and x["lookback_months"] == lb), None)
            if r and r["mean_sharpe"] is not None:
                cells.append(f"{r['mean_sharpe']:>+5.2f}")
            else:
                cells.append("  n/a")
        print(f"  {top_n:<8} | " + " | ".join(cells))

    # Stability verdict
    valid = [r["mean_sharpe"] for r in results if r["mean_sharpe"] is not None]
    if valid:
        max_s, min_s = max(valid), min(valid)
        median_s = statistics.median(valid)
        spread = max_s - min_s
        rel_spread = spread / abs(median_s) if median_s != 0 else float("inf")
        print(f"\n  Median Sharpe across grid: {median_s:.2f}")
        print(f"  Best Sharpe: {max_s:.2f}  Worst: {min_s:.2f}  Spread: {spread:.2f}")
        print(f"  Spread / |median| = {rel_spread:.2%}")
        if rel_spread < 0.30:
            print("  ✅ Sharpe surface is FLAT (spread < 30% of median) — robust to params")
        elif rel_spread < 0.60:
            print("  🟡 Sharpe surface is BUMPY (30-60% spread) — borderline overfit")
        else:
            print("  ❌ Sharpe surface is FRAGILE (>60% spread) — likely overfit to one param")

    out = ROOT / "data" / "parameter_sensitivity.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        json.dump({"generated_at": datetime.utcnow().isoformat(),
                   "results": results,
                   "grid": {"top_n": TOP_N_GRID, "lookback": LOOKBACK_GRID}},
                  f, indent=2, default=str)
    print(f"\nWritten: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
