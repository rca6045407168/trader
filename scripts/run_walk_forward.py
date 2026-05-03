"""[v3.59.5] Run walk-forward backtest on the LIVE momentum strategy.

Wires the v3.59.4 walk_forward harness to the actual rank_momentum
strategy + LIVE universe. Outputs the OOS performance grid every
quant fund presents.

Usage:
  python scripts/run_walk_forward.py [--anchored | --rolling]
                                       [--test-days 63] [--step-days 63]
                                       [--start 2018-01-01]

Output:
  • prints per-window summary + aggregate to stdout
  • writes data/walk_forward_results.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _build_strategy_fn():
    from trader.universe import DEFAULT_LIQUID_50
    from trader.strategy import rank_momentum
    universe = DEFAULT_LIQUID_50
    def fn(asof: str):
        return rank_momentum(universe, lookback_months=12, skip_months=1,
                              top_n=15, end_date=asof)
    return fn


def _build_panel_fn():
    """Returns a closure that fetches close-price history for the given
    symbols over [start, end]. Caches per-symbol for the session."""
    cache: dict[str, dict] = {}
    def fn(start: str, end: str, symbols: list[str]) -> dict:
        try:
            import yfinance as yf
            from datetime import datetime as _dt
            out = {}
            for sym in symbols:
                key = f"{sym}|{start}|{end}"
                if key in cache:
                    out[sym] = cache[key]
                    continue
                df = yf.download(sym, start=start, end=end,
                                  progress=False, auto_adjust=True)
                if df is None or df.empty:
                    cache[key] = []
                    continue
                seq = []
                for idx in df.index:
                    v = df["Close"].loc[idx]
                    try:
                        price = float(v.iloc[0] if hasattr(v, "iloc") else v)
                        seq.append((idx.date(), price))
                    except Exception:
                        continue
                cache[key] = seq
                out[sym] = seq
            return out
        except Exception:
            return {}
    return fn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["anchored", "rolling"],
                     default="anchored")
    ap.add_argument("--start", default="2020-01-01",
                     help="train_start (anchored) or first historical date (rolling)")
    ap.add_argument("--first-test", default="2022-01-01",
                     help="first OOS test-window start")
    ap.add_argument("--end", default=None,
                     help="final test-window end (default: today - 90d)")
    ap.add_argument("--test-days", type=int, default=63)
    ap.add_argument("--step-days", type=int, default=63)
    ap.add_argument("--rolling-train-days", type=int, default=730)
    args = ap.parse_args()

    end = args.end or (datetime.utcnow().date() - timedelta(days=90)).isoformat()
    print("=" * 78)
    print(f"Walk-forward · {args.mode} · {args.first_test} → {end}")
    print(f"  test_days={args.test_days}  step_days={args.step_days}")
    print("=" * 78)

    strategy_fn = _build_strategy_fn()
    panel_fn = _build_panel_fn()

    if args.mode == "anchored":
        from trader.walk_forward import run_anchored_walk_forward
        summary = run_anchored_walk_forward(
            strategy_fn=strategy_fn, price_panel_fn=panel_fn,
            train_start=args.start, train_end=args.first_test,
            test_end=end,
            test_days=args.test_days, step_days=args.step_days,
        )
    else:
        from trader.walk_forward import run_rolling_walk_forward
        summary = run_rolling_walk_forward(
            strategy_fn=strategy_fn, price_panel_fn=panel_fn,
            train_days=args.rolling_train_days,
            first_test_start=args.first_test, test_end=end,
            test_days=args.test_days, step_days=args.step_days,
        )

    # Print per-window
    print(f"\nPer-window OOS results ({summary.n_windows} windows):")
    print(f"  {'test_start':<12} {'test_end':<12} {'return':>8} {'sharpe':>7} {'maxDD':>8} picks")
    print("  " + "-" * 70)
    for w in summary.windows:
        if w.error:
            print(f"  {w.test_start} {w.test_end}  ERROR: {w.error[:40]}")
            continue
        ret_s = f"{w.period_return*100:+.2f}%" if w.period_return is not None else "  n/a"
        sh_s = f"{w.sharpe:+.2f}" if w.sharpe is not None else "  n/a"
        dd_s = f"{w.max_drawdown*100:+.1f}%" if w.max_drawdown is not None else "  n/a"
        print(f"  {w.test_start} {w.test_end} {ret_s:>8} {sh_s:>7} {dd_s:>8}  "
              f"{','.join(w.picks[:5])}{'...' if len(w.picks) > 5 else ''}")

    print(f"\n=== Aggregate ===")
    print(f"  windows: {summary.n_windows}")
    print(f"  mean period return: {(summary.mean_period_return or 0)*100:+.2f}%")
    print(f"  median period return: {(summary.median_period_return or 0)*100:+.2f}%")
    print(f"  mean Sharpe (annualized): {summary.mean_sharpe or 0:+.2f}")
    print(f"  median Sharpe: {summary.median_sharpe or 0:+.2f}")
    print(f"  Sharpe stdev: {summary.sharpe_stdev or 0:+.2f}")
    print(f"  % windows positive: {(summary.pct_windows_positive or 0)*100:.0f}%")
    print(f"  worst window: {(summary.worst_window_return or 0)*100:+.2f}%")
    print(f"  best window: {(summary.best_window_return or 0)*100:+.2f}%")

    # Verdict
    print(f"\n=== Verdict ===")
    pos_pct = (summary.pct_windows_positive or 0) * 100
    mean_s = summary.mean_sharpe or 0
    if pos_pct >= 70 and mean_s > 0.5:
        print("  ✅ STRONG: >70% positive windows AND mean Sharpe > 0.5")
    elif pos_pct >= 55:
        print("  🟡 OK: 55-70% positive windows; edge is modest")
    else:
        print("  ❌ WEAK: <55% positive windows — strategy may not generalize OOS")

    out_path = ROOT / "data" / "walk_forward_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.utcnow().isoformat(),
        "args": vars(args),
        "summary": {
            k: v for k, v in asdict(summary).items()
            if k != "windows"
        },
        "windows": [asdict(w) for w in summary.windows],
    }
    with out_path.open("w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"\nWritten: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
