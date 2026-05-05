"""Replay the v3.69.0 ReactorSignalRule across history (v3.72.0).

Usage:
    python scripts/reactor_backtest.py                    # default config
    python scripts/reactor_backtest.py --sweep            # parameter grid
    python scripts/reactor_backtest.py --m 3 --trim 0.25  # specific config
    python scripts/reactor_backtest.py --no-prices        # skip yfinance

For each historical rebalance, applies the rule's threshold + trim
logic to the period's signals, then computes counterfactual P&L
impact via forward returns (yfinance close-to-close, T+5/10/20 days).

When the journal has insufficient data (no rebalances yet, or zero
material signals at the threshold), the output says so explicitly —
not silently zero.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _print_result(r, verbose: bool = False) -> None:
    cfg = r.config
    print(f"\n=== M≥{cfg['min_materiality']} · "
          f"trim to {cfg['trim_pct']*100:.0f}% · "
          f"lookback {cfg['lookback_days']}d ===")
    print(f"  {r.summary()}")
    if r.n_trims_triggered > 0:
        print(f"  rebalances analyzed: {r.n_rebalances_analyzed}")
        print(f"  signals seen:        {r.n_signals_in_window}")
        print(f"  trims triggered:     {r.n_trims_triggered}")
        if r.total_pnl_impact_pct is not None:
            print(f"  total P&L impact:    "
                  f"{r.total_pnl_impact_pct*100:+.3f}%")
        if r.avg_fwd_return_5d is not None:
            print(f"  avg fwd return 5d:   "
                  f"{r.avg_fwd_return_5d*100:+.2f}%")
        if r.avg_fwd_return_20d is not None:
            print(f"  avg fwd return 20d:  "
                  f"{r.avg_fwd_return_20d*100:+.2f}%")
        if verbose:
            print(f"  trim events:")
            for e in r.trim_events:
                fwd = (f"5d={e.fwd_return_5d*100:+.2f}% "
                       f"20d={e.fwd_return_20d*100:+.2f}%"
                       if e.fwd_return_5d is not None else "fwd=n/a")
                impact = (f"impact={e.pnl_impact_pct*100:+.3f}%"
                          if e.pnl_impact_pct is not None else "impact=n/a")
                print(f"    [{e.materiality}/{e.direction}] {e.symbol} "
                      f"{e.filed_at}: "
                      f"{e.original_target_weight*100:.2f}% → "
                      f"{e.counterfactual_target_weight*100:.2f}%  "
                      f"{fwd}  {impact}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sweep", action="store_true",
                         help="Sweep across (M × trim_pct) grid (default: just current config)")
    parser.add_argument("--m", type=int, default=4,
                         help="min_materiality threshold (default 4)")
    parser.add_argument("--trim", type=float, default=0.5,
                         help="trim_to_pct (default 0.5)")
    parser.add_argument("--lookback", type=int, default=14,
                         help="signal lookback days (default 14)")
    parser.add_argument("--since", default=None,
                         help="Only count rebalances on/after this ISO date")
    parser.add_argument("--no-prices", action="store_true",
                         help="Skip yfinance forward-price pulls (fast)")
    parser.add_argument("--json", action="store_true",
                         help="Emit JSON instead of human-readable")
    parser.add_argument("--verbose", "-v", action="store_true",
                         help="Show per-event detail lines")
    args = parser.parse_args()

    pull_prices = not args.no_prices

    if args.sweep:
        from trader.reactor_backtest import parameter_sweep
        results = parameter_sweep(
            lookback_days=args.lookback,
            since_date=args.since,
            pull_forward_prices=pull_prices,
        )
        if args.json:
            print(json.dumps([r.to_dict() for r in results], indent=2))
            return 0
        print(f"=== ReactorSignalRule parameter sweep "
              f"({len(results)} configs) ===")
        for r in results:
            _print_result(r, verbose=args.verbose)
        return 0

    from trader.reactor_backtest import replay
    r = replay(
        min_materiality=args.m,
        trim_pct=args.trim,
        lookback_days=args.lookback,
        since_date=args.since,
        pull_forward_prices=pull_prices,
    )
    if args.json:
        print(json.dumps(r.to_dict(), indent=2))
        return 0
    _print_result(r, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)
