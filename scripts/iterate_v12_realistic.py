"""v1.4 — quantify the open-vs-close fill gap (B4 fix).

Runs close-to-close (the original, optimistic) backtest vs the realistic
open-fill backtest on the same universe + parameters. The difference is the
actual slippage we've been ignoring.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.backtest import backtest_momentum, backtest_momentum_realistic
from trader.universe import DEFAULT_LIQUID_50


def main():
    print("=" * 78)
    print("v1.4  —  REALISTIC FILL MODEL (B4 fix)")
    print("  Old:  decide at T close, trade AT T close. (impossible)")
    print("  New:  decide at T close, trade at T+1 open. (what really happens)")
    print("=" * 78)

    period = ("2015-01-01", "2025-04-30")
    print(f"\nPeriod: {period[0]} to {period[1]}, liquid_50, 12m/top-5\n")

    optimistic = backtest_momentum(
        DEFAULT_LIQUID_50, start=period[0], end=period[1],
        lookback_months=12, top_n=5, slippage_bps=5.0,
    )
    realistic = backtest_momentum_realistic(
        DEFAULT_LIQUID_50, start=period[0], end=period[1],
        lookback_months=12, top_n=5, slippage_bps=5.0,
    )

    o, r = optimistic.stats(), realistic.stats()
    print(f"{'metric':25s}  {'optimistic':>12s}  {'realistic':>12s}  {'delta':>10s}")
    print("-" * 70)
    for key in ("cagr", "sharpe", "max_drawdown", "alpha", "win_rate", "final_equity"):
        o_val = o[key]
        r_val = r[key]
        delta = r_val - o_val
        print(
            f"{key:25s}  {o_val:>12.4f}  {r_val:>12.4f}  {delta:>+10.4f}"
        )
    print("-" * 70)

    # Test on out-of-sample 2021-2025 too
    print("\n\nOUT-OF-SAMPLE 2021-01 to 2025-04:")
    o_oos = backtest_momentum(
        DEFAULT_LIQUID_50, start="2021-01-01", end="2025-04-30",
        lookback_months=12, top_n=5,
    ).stats()
    r_oos = backtest_momentum_realistic(
        DEFAULT_LIQUID_50, start="2021-01-01", end="2025-04-30",
        lookback_months=12, top_n=5,
    ).stats()
    print(f"  Optimistic OOS:  CAGR {o_oos['cagr']:+.2%}  Sharpe {o_oos['sharpe']:.2f}")
    print(f"  Realistic OOS:   CAGR {r_oos['cagr']:+.2%}  Sharpe {r_oos['sharpe']:.2f}")
    print(f"  Slippage cost:   CAGR {r_oos['cagr'] - o_oos['cagr']:+.2%}")

    # Final honest expectation update
    cagr_drag = r['cagr'] - o['cagr']
    sharpe_drag = r['sharpe'] - o['sharpe']
    print(f"\n=== UPDATED REAL EXPECTATION ===")
    print(f"In-sample CAGR drag from open-fill: {cagr_drag:+.2%}")
    print(f"In-sample Sharpe drag from open-fill: {sharpe_drag:+.2f}")
    print(f"\nPrevious 'real expectation' (CAVEATS): 17% CAGR, 0.83 Sharpe")
    print(f"After B4 correction: ~{0.17 + cagr_drag:.2%} CAGR, ~{0.83 + sharpe_drag:.2f} Sharpe")


if __name__ == "__main__":
    main()
