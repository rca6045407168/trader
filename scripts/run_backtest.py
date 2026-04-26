"""Backtest comparison: vanilla momentum vs walk-forward winner vs regime-filtered.

Runs three configurations side-by-side so we can SEE which configuration is
actually doing the work.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.backtest import backtest_momentum, plot_equity
from trader.universe import DEFAULT_LIQUID_50


CONFIGS = [
    {"name": "vanilla_6m_top10",       "lookback_months": 6,  "top_n": 10, "regime_filter": False},
    {"name": "walkfwd_winner_12m_top5", "lookback_months": 12, "top_n": 5,  "regime_filter": False},
    {"name": "with_regime_12m_top5",   "lookback_months": 12, "top_n": 5,  "regime_filter": True},
]


def main():
    print("=" * 78)
    print("BACKTEST COMPARISON  — liquid-50, monthly rebal, 2015-01 to 2025-04")
    print("=" * 78)
    print()

    rows = []
    for cfg in CONFIGS:
        print(f"Running {cfg['name']}...")
        r = backtest_momentum(
            universe=DEFAULT_LIQUID_50,
            start="2015-01-01", end="2025-04-30",
            lookback_months=cfg["lookback_months"],
            skip_months=1,
            top_n=cfg["top_n"],
            regime_filter=cfg["regime_filter"],
            initial_capital=100_000.0,
            slippage_bps=5.0,
        )
        s = r.stats()
        s["name"] = cfg["name"]
        rows.append(s)
        plot_equity(r, name=cfg["name"])

    print()
    print("-" * 78)
    cols = ["name", "cagr", "sharpe", "max_drawdown", "alpha", "win_rate", "final_equity"]
    fmt_h = "{:30s}  {:>7s}  {:>7s}  {:>9s}  {:>7s}  {:>8s}  {:>13s}"
    fmt_r = "{:30s}  {:>7.2%}  {:>7.2f}  {:>9.2%}  {:>+7.2%}  {:>8.2%}  ${:>12,.0f}"
    bench = rows[0]["benchmark_cagr"]
    print(fmt_h.format(*cols))
    for r in rows:
        print(fmt_r.format(
            r["name"], r["cagr"], r["sharpe"], r["max_drawdown"],
            r["alpha"], r["win_rate"], r["final_equity"],
        ))
    print("-" * 78)
    print(f"  SPY benchmark: CAGR {bench:.2%}, MaxDD {rows[0]['benchmark_max_drawdown']:.2%}")
    print()
    print(f"PNGs saved in: {ROOT / 'reports'}")


if __name__ == "__main__":
    main()
