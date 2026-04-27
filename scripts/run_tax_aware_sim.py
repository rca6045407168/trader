"""Tax-aware sim: how much edge survives 47% STCG vs Roth IRA?

Richard's bracket: 37% federal + ~10% CA = 47% effective short-term cap gains.
A monthly-rotation strategy realizes 100% short-term gains. Real after-tax
return is roughly half of pretax.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd
import numpy as np
from trader.backtest import backtest_momentum_realistic
from trader.universe import DEFAULT_LIQUID_50

FED_STCG = 0.37
CA_STCG = 0.10
TOTAL_STCG = FED_STCG + CA_STCG


def simulate_taxed_returns(monthly_returns, tax_rate):
    after_tax = monthly_returns.copy()
    pos = monthly_returns > 0
    after_tax[pos] = monthly_returns[pos] * (1 - tax_rate)
    return after_tax


def stats(rets, label, capital=100_000):
    eq = (1 + rets.fillna(0)).cumprod() * capital
    if len(eq) < 6:
        return {}
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
    sharpe = rets.mean() * 12 / (rets.std() * np.sqrt(12))
    dd = (eq / eq.cummax() - 1).min()
    print(f"  {label:35s}  CAGR {cagr:>+7.2%}  Sharpe {sharpe:>5.2f}  MaxDD {dd:>7.2%}  final ${eq.iloc[-1]:>11,.0f}")
    return {"cagr": cagr, "sharpe": sharpe, "maxdd": dd, "final": eq.iloc[-1]}


def main():
    print("=" * 78)
    print("TAX-AWARE BACKTEST  —  what survives 47% STCG vs Roth IRA")
    print("=" * 78)

    r = backtest_momentum_realistic(
        DEFAULT_LIQUID_50, start="2015-01-01", end="2025-04-30",
        lookback_months=12, top_n=5,
    )
    pretax = r.monthly_returns

    print(f"\nPeriod: {pretax.index[0].date()} → {pretax.index[-1].date()}")
    print(f"Bracket: 37% federal + 10% CA = {TOTAL_STCG:.0%} STCG\n")

    print("--- Comparison ---")
    pretax_stats = stats(pretax, "Pretax (taxable account)")
    after_tax = simulate_taxed_returns(pretax, TOTAL_STCG)
    after_tax_stats = stats(after_tax, "After-tax (taxable, 47% STCG)")
    stats(pretax, "Roth IRA (0% tax = same as pretax)")

    drag = pretax_stats["cagr"] - after_tax_stats["cagr"]
    print(f"\nTax drag: {drag*100:.2f}% CAGR ({drag/pretax_stats['cagr']*100:.0f}% of pretax)")
    print(f"Wealth diff on $100k: ${pretax_stats['final'] - after_tax_stats['final']:,.0f}")
    print()
    print("=== KEY TAKEAWAY ===")
    print(f"In a TAXABLE account, this strategy yields {after_tax_stats['cagr']*100:.1f}% CAGR.")
    print(f"In a ROTH IRA, same strategy yields {pretax_stats['cagr']*100:.1f}% CAGR.")
    print(f"\nDecision: run this in a Roth, not taxable. Buy SPY in your taxable.")


if __name__ == "__main__":
    main()
