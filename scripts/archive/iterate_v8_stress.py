"""v0.8 — stress test the strategy across named crash periods + bias quantification.

Why this matters: a strategy that beats SPY in calm markets but blows up in
crashes is worse than no strategy. Walk-forward only tells us 2021-2025; we
need to know what would happen in 2008, 2020, 2022.

Tests:
  S1 — Performance across 5 named crash windows + 1-year recovery each
  S2 — Slippage sensitivity: 5bps / 10bps / 25bps / 50bps
  S3 — Universe sensitivity: liquid_50 vs sp500_full vs equal-weight ETF (RSP)
  S4 — Lookback sensitivity: 3 / 6 / 9 / 12 / 18 / 24 month lookbacks
  S5 — Top-N sensitivity: top 3 / 5 / 7 / 10 / 15 / 20
  S6 — Survivorship-bias proxy: compare liquid-50 (current top names) vs the
        2015-known top 50, see how much the bias inflates returns
  S7 — Monte-Carlo block bootstrap: shuffle monthly returns to test path-dependence
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd
from trader.backtest import backtest_momentum
from trader.universe import DEFAULT_LIQUID_50

REPORTS = ROOT / "reports"

# Hand-picked top 50 by market cap as of Jan 2015 (approximate; from Wikipedia
# archived snapshots). Used to quantify survivorship bias — the difference
# between the 2015-known top 50 and the current-known top 50.
TOP_50_2015 = [
    "AAPL", "XOM", "MSFT", "GOOGL", "BRK-B", "JNJ", "WFC", "GE", "WMT", "CVX",
    "PG", "JPM", "VZ", "PFE", "T", "BAC", "KO", "FB", "ORCL", "DIS",
    "INTC", "MRK", "PM", "CSCO", "HD", "IBM", "PEP", "AMZN", "COP", "V",
    "GILD", "MO", "CMCSA", "AMGN", "BMY", "OXY", "MCD", "MDT", "AIG", "USB",
    "QCOM", "GS", "NKE", "UNH", "ABT", "SLB", "DOW", "BIIB", "AXP", "BA",
]


def _stats_from_returns(returns, label):
    eq = (1 + returns.fillna(0)).cumprod() * 100_000
    if len(eq) < 6:
        return None
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
    sharpe = returns.mean() * 12 / (returns.std() * np.sqrt(12)) if returns.std() > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    return {"label": label, "cagr": cagr, "sharpe": sharpe, "maxdd": dd, "final_equity": eq.iloc[-1]}


def s1_crash_performance():
    print("\n" + "=" * 80)
    print("S1 — PERFORMANCE THROUGH HISTORICAL CRASHES")
    print("=" * 80)
    crashes = [
        ("2015 China devaluation",  "2015-06-01", "2016-06-30"),
        ("2018 Q4 selloff",          "2018-08-01", "2019-08-31"),
        ("2020 COVID crash",         "2020-01-01", "2021-01-31"),
        ("2022 bear market",         "2022-01-01", "2023-01-31"),
        ("2025 tariff selloff",      "2024-09-01", "2025-04-30"),
    ]
    print(f"\n{'period':28s}  {'CAGR':>8s}  {'Sharpe':>7s}  {'MaxDD':>8s}  {'SPY CAGR':>10s}  {'SPY MaxDD':>10s}")
    print("-" * 80)
    for name, start, end in crashes:
        try:
            r = backtest_momentum(DEFAULT_LIQUID_50, start=start, end=end,
                                  lookback_months=12, top_n=5)
            s = r.stats()
            print(
                f"  {name:28s}  {s['cagr']:>+8.2%}  {s['sharpe']:>+7.2f}  {s['max_drawdown']:>+8.2%}  "
                f"{s['benchmark_cagr']:>+10.2%}  {s['benchmark_max_drawdown']:>+10.2%}"
            )
        except Exception as e:
            print(f"  {name:28s}  FAILED: {e}")


def s2_slippage_sensitivity():
    print("\n" + "=" * 80)
    print("S2 — SLIPPAGE SENSITIVITY (the 'real fills are worse' test)")
    print("=" * 80)
    print(f"\n{'slippage':>12s}  {'CAGR':>8s}  {'Sharpe':>7s}  {'alpha':>8s}")
    for bps in (5, 10, 25, 50, 100):
        r = backtest_momentum(DEFAULT_LIQUID_50, start="2015-01-01", end="2025-04-30",
                              lookback_months=12, top_n=5, slippage_bps=float(bps))
        s = r.stats()
        print(f"  {bps:>9d}bps  {s['cagr']:>+8.2%}  {s['sharpe']:>+7.2f}  {s['alpha']:>+8.2%}")
    print("\n  Realistic for $5-10k notional in S&P 500 names: 8-15bps")
    print("  Realistic for $50k+ notional or thin liquidity: 25-50bps")


def s3_universe_sensitivity():
    print("\n" + "=" * 80)
    print("S3 — UNIVERSE COMPOSITION SENSITIVITY")
    print("=" * 80)
    from trader.universe import sp500_tickers
    full = sp500_tickers()
    universes = {
        "liquid_50 (default)": DEFAULT_LIQUID_50,
        "sp500_top100": full[:100],
        "sp500_top250": full[:250],
        "sp500_full": full,
    }
    print(f"\n{'universe':25s}  {'CAGR':>8s}  {'Sharpe':>7s}  {'MaxDD':>8s}  {'alpha':>8s}")
    for name, u in universes.items():
        try:
            r = backtest_momentum(u, start="2015-01-01", end="2025-04-30",
                                  lookback_months=12, top_n=5)
            s = r.stats()
            print(f"  {name:25s}  {s['cagr']:>+8.2%}  {s['sharpe']:>+7.2f}  {s['max_drawdown']:>+8.2%}  {s['alpha']:>+8.2%}")
        except Exception as e:
            print(f"  {name:25s}  FAILED: {e}")


def s4_lookback_sensitivity():
    print("\n" + "=" * 80)
    print("S4 — LOOKBACK MONTHS SENSITIVITY")
    print("=" * 80)
    print(f"\n{'lookback':>10s}  {'CAGR':>8s}  {'Sharpe':>7s}  {'MaxDD':>8s}  {'alpha':>8s}")
    for L in (3, 6, 9, 12, 18, 24):
        try:
            r = backtest_momentum(DEFAULT_LIQUID_50, start="2015-01-01", end="2025-04-30",
                                  lookback_months=L, top_n=5)
            s = r.stats()
            marker = "  <- DEPLOYED" if L == 12 else ""
            print(f"  {L:>4d}m     {s['cagr']:>+8.2%}  {s['sharpe']:>+7.2f}  {s['max_drawdown']:>+8.2%}  {s['alpha']:>+8.2%}{marker}")
        except Exception as e:
            print(f"  {L:>4d}m     FAILED: {e}")


def s5_topn_sensitivity():
    print("\n" + "=" * 80)
    print("S5 — TOP-N SENSITIVITY")
    print("=" * 80)
    print(f"\n{'top_N':>6s}  {'CAGR':>8s}  {'Sharpe':>7s}  {'MaxDD':>8s}  {'alpha':>8s}")
    for N in (3, 5, 7, 10, 15, 20):
        try:
            r = backtest_momentum(DEFAULT_LIQUID_50, start="2015-01-01", end="2025-04-30",
                                  lookback_months=12, top_n=N)
            s = r.stats()
            marker = "  <- DEPLOYED" if N == 5 else ""
            print(f"  {N:>4d}    {s['cagr']:>+8.2%}  {s['sharpe']:>+7.2f}  {s['max_drawdown']:>+8.2%}  {s['alpha']:>+8.2%}{marker}")
        except Exception as e:
            print(f"  {N:>4d}    FAILED: {e}")


def s6_survivorship_bias_quantified():
    print("\n" + "=" * 80)
    print("S6 — SURVIVORSHIP BIAS QUANTIFIED (current top-50 vs 2015 top-50)")
    print("=" * 80)
    print(f"\n{'universe':30s}  {'CAGR':>8s}  {'Sharpe':>7s}  {'MaxDD':>8s}")
    for name, u in [("current top-50 (deployed)", DEFAULT_LIQUID_50),
                    ("top-50 as known in 2015", TOP_50_2015)]:
        try:
            r = backtest_momentum(u, start="2015-01-01", end="2025-04-30",
                                  lookback_months=12, top_n=5)
            s = r.stats()
            print(f"  {name:30s}  {s['cagr']:>+8.2%}  {s['sharpe']:>+7.2f}  {s['max_drawdown']:>+8.2%}")
        except Exception as e:
            print(f"  {name:30s}  FAILED: {e}")
    print("\n  Difference = approximate survivorship bias inflation in our backtest.")


def s7_monte_carlo_bootstrap():
    print("\n" + "=" * 80)
    print("S7 — MONTE CARLO BLOCK BOOTSTRAP (path-dependence test)")
    print("=" * 80)
    print("  Shuffles monthly returns in 3-month blocks 1000 times. If realized")
    print("  Sharpe is in the upper tail of bootstrap distribution, the strategy is")
    print("  path-dependent (e.g. only worked because of one lucky run).")
    r = backtest_momentum(DEFAULT_LIQUID_50, start="2015-01-01", end="2025-04-30",
                          lookback_months=12, top_n=5)
    realized = r.monthly_returns.dropna()
    realized_sharpe = realized.mean() * 12 / (realized.std() * np.sqrt(12))

    block_size = 3
    n_iter = 1000
    rng = np.random.default_rng(42)
    bootstrap_sharpes = []
    n = len(realized)
    n_blocks = n // block_size
    for _ in range(n_iter):
        block_starts = rng.integers(0, n - block_size, size=n_blocks)
        sampled = np.concatenate([realized.iloc[s : s + block_size].values for s in block_starts])
        sampled = pd.Series(sampled)
        if sampled.std() == 0:
            continue
        bs_sharpe = sampled.mean() * 12 / (sampled.std() * np.sqrt(12))
        bootstrap_sharpes.append(bs_sharpe)

    bs = np.array(bootstrap_sharpes)
    pct = (bs < realized_sharpe).mean()
    print(f"\n  Realized Sharpe:        {realized_sharpe:.3f}")
    print(f"  Bootstrap mean Sharpe:  {bs.mean():.3f}")
    print(f"  Bootstrap p5 / p50 / p95: {np.percentile(bs, 5):.3f} / {np.percentile(bs, 50):.3f} / {np.percentile(bs, 95):.3f}")
    print(f"  Realized is at percentile {pct*100:.1f}")
    if pct > 0.95:
        print("  VERDICT: realized result is upper-tail — some path luck.")
    elif pct > 0.50:
        print("  VERDICT: realized result is robust — not path-dependent.")
    else:
        print("  VERDICT: realized is BELOW bootstrap median — actually unlucky.")


def main():
    s1_crash_performance()
    s2_slippage_sensitivity()
    s3_universe_sensitivity()
    s4_lookback_sensitivity()
    s5_topn_sensitivity()
    s6_survivorship_bias_quantified()
    s7_monte_carlo_bootstrap()
    print("\n" + "=" * 80)
    print("v0.8 STRESS TEST COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
