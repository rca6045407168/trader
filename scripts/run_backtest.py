"""Backtest matrix: 4 universes × 4 regime filters. Finds the most robust config."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.backtest import backtest_momentum, plot_equity
from trader.universe import DEFAULT_LIQUID_50, sp500_tickers


def main():
    print("=" * 95)
    print("STRATEGY ITERATION  —  4 universes × 4 regime filters × 12m / top-5")
    print("=" * 95)

    print("\nFetching S&P 500 ticker list from Wikipedia...")
    full_sp500 = sp500_tickers()
    print(f"  -> {len(full_sp500)} tickers")

    universes = {
        "liquid_50": DEFAULT_LIQUID_50,
        "sp500_top100": full_sp500[:100],
        "sp500_top250": full_sp500[:250],
        "sp500_full": full_sp500,
    }
    regime_filters = [None, "slow_ma", "cross", "smooth"]

    rows = []
    for u_name, universe in universes.items():
        for rf in regime_filters:
            label = f"{u_name} / regime={rf or 'none'}"
            print(f"\nRunning {label} ...")
            try:
                r = backtest_momentum(
                    universe=universe,
                    start="2015-01-01", end="2025-04-30",
                    lookback_months=12, skip_months=1, top_n=5,
                    regime_filter=rf,
                )
                s = r.stats()
                s["label"] = label
                rows.append(s)
                if rf is None and u_name == "sp500_full":
                    plot_equity(r, name="sp500_full_no_regime")
            except Exception as e:
                print(f"  FAILED: {e}")

    print("\n" + "=" * 95)
    print(f"{'config':38s} {'CAGR':>8s} {'Sharpe':>7s} {'MaxDD':>9s} {'Alpha':>7s} {'Win%':>6s} {'Final $':>13s}")
    print("-" * 95)
    rows_sorted = sorted(rows, key=lambda r: -r["sharpe"])
    for r in rows_sorted:
        print(
            f"{r['label']:38s} {r['cagr']:>8.2%} {r['sharpe']:>7.2f} "
            f"{r['max_drawdown']:>9.2%} {r['alpha']:>+7.2%} {r['win_rate']:>6.1%} ${r['final_equity']:>12,.0f}"
        )
    print("-" * 95)
    if rows:
        print(f"  SPY benchmark: CAGR {rows[0]['benchmark_cagr']:.2%}, MaxDD {rows[0]['benchmark_max_drawdown']:.2%}")
        winner = rows_sorted[0]
        print(f"\nWINNER: {winner['label']}  —  Sharpe {winner['sharpe']:.2f}, alpha {winner['alpha']:+.2%}")


if __name__ == "__main__":
    main()
