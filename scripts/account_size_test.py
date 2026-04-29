"""Account-size scenario test.

Backtests + paper-trading both implicitly assume fractional shares and zero
rounding error. Real Roth IRA accounts at $10-50k can run into trouble:

  - Whole-share rounding distorts target weights (a $500 stock at 26.7%
    of $10k = $2670 = 5.34 shares; rounded to 5 = 25.0% actual weight)
  - Some brokers (Schwab, Fidelity) only allow fractional ETFs, not stocks
  - Position-size minimums kick in below ~$50k — pattern-day-trader rule
    limits day-trades when equity < $25k

This script simulates a rebalance at $10k, $25k, $50k, $100k and reports:
  - Target vs actual weight per name
  - Total weight error (should be < 1% for fractional, can be 5-10% whole-share)
  - PDT rule warnings
  - Estimated bid-ask cost as % of position size
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from trader import variants  # noqa: F401  (registers)
from trader.ab import get_live
from trader.universe import DEFAULT_LIQUID_50
from trader.data import fetch_history


def latest_prices(tickers: list[str]) -> dict[str, float]:
    end = pd.Timestamp.today()
    start = (end - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    df = fetch_history(tickers, start=start)
    if df.empty:
        return {}
    last_row = df.dropna(how="all").iloc[-1]
    return {t: float(last_row[t]) for t in tickers if t in df.columns and not pd.isna(last_row[t])}


def simulate_rebalance(equity: float, targets: dict[str, float],
                        prices: dict[str, float], fractional: bool) -> dict:
    """Given target weights and current prices, compute actual fills + slippage."""
    actual_alloc = {}
    total_invested = 0.0
    for sym, weight in targets.items():
        target_dollars = equity * weight
        price = prices.get(sym)
        if price is None:
            actual_alloc[sym] = {"target_pct": weight, "actual_pct": 0.0,
                                 "shares": 0.0, "error_pct": -weight,
                                 "missing_price": True}
            continue
        if fractional:
            shares = target_dollars / price
        else:
            shares = int(target_dollars / price)  # whole-share rounding
        actual_dollars = shares * price
        actual_pct = actual_dollars / equity
        actual_alloc[sym] = {
            "target_pct": weight,
            "actual_pct": actual_pct,
            "shares": shares,
            "price": price,
            "actual_dollars": actual_dollars,
            "error_pct": actual_pct - weight,
        }
        total_invested += actual_dollars
    return {
        "actual_alloc": actual_alloc,
        "total_invested_pct": total_invested / equity,
        "cash_pct": 1.0 - total_invested / equity,
        "max_per_position_error": max(abs(d["error_pct"]) for d in actual_alloc.values()),
    }


def main():
    print("=" * 80)
    print("ACCOUNT-SIZE SCENARIO TEST")
    print("=" * 80)
    print()

    live = get_live()
    if live is None:
        print("No LIVE variant. Aborting.")
        return 1

    print(f"LIVE: {live.variant_id}\n")
    targets = live.fn(universe=DEFAULT_LIQUID_50, equity=100_000.0, account_state={})
    if not targets:
        print("LIVE returned empty targets — cannot simulate rebalance.")
        return 1

    print("Target allocation (from variant):")
    for sym, w in sorted(targets.items()):
        print(f"  {sym}: {w*100:.2f}%")
    print()

    prices = latest_prices(list(targets.keys()))
    print("Current prices:")
    for sym, p in sorted(prices.items()):
        print(f"  {sym}: ${p:.2f}")
    print()

    SCENARIOS = [
        ("$10k Roth IRA  (fractional broker)", 10_000.0, True),
        ("$10k Roth IRA  (whole-share only)",  10_000.0, False),
        ("$25k Roth IRA  (fractional)",         25_000.0, True),
        ("$25k Roth IRA  (whole-share)",        25_000.0, False),
        ("$50k Roth IRA  (fractional)",         50_000.0, True),
        ("$50k Roth IRA  (whole-share)",        50_000.0, False),
        ("$100k taxable (fractional)",         100_000.0, True),
    ]

    issues = []
    for name, equity, fractional in SCENARIOS:
        result = simulate_rebalance(equity, targets, prices, fractional)
        max_err = result["max_per_position_error"]
        cash_pct = result["cash_pct"]

        print(f"--- {name} ---")
        for sym, alloc in sorted(result["actual_alloc"].items()):
            err_pct = alloc["error_pct"] * 100
            if alloc.get("missing_price"):
                print(f"  {sym}: missing price — skipped")
                continue
            shares_str = f"{alloc['shares']:.4f}" if fractional else f"{int(alloc['shares']):>4d}"
            print(f"  {sym}: target {alloc['target_pct']*100:>5.2f}%  actual {alloc['actual_pct']*100:>5.2f}%  err {err_pct:>+5.2f}%  shares={shares_str}")
        print(f"  → invested {result['total_invested_pct']*100:.2f}% / cash {cash_pct*100:.2f}%  max-err {max_err*100:.2f}%")

        if equity < 25_000:
            issues.append(f"{name}: PDT rule limits day-trades (<$25k threshold)")
        if max_err > 0.05:
            issues.append(f"{name}: per-position weight error {max_err*100:.1f}% exceeds 5% — strategy mechanics degraded")
        if not fractional and max_err > 0.02:
            issues.append(f"{name}: whole-share rounding causes {max_err*100:.1f}% weight drift — consider fractional broker")
        print()

    print("=" * 80)
    print("ISSUES")
    print("=" * 80)
    if issues:
        for i in issues:
            print(f"  ⚠ {i}")
    else:
        print("  ✓ No structural issues. Strategy mechanics valid at all tested account sizes.")
    print()

    print("RECOMMENDATIONS:")
    print("  - Below $25k: Pattern Day Trader rule limits day-trades to 3 per 5 business days.")
    print("    Strategy is monthly-rebalance so should be fine, but watch for emergency exits.")
    print("  - Below $50k with whole-share-only broker: weight drift can exceed 5% per position.")
    print("    Use Alpaca / Robinhood / Fidelity fractional shares to avoid this.")
    print("  - Roth IRA contribution limit is $7,000 in 2026 ($8k if 50+).")
    print("    Going from $10k → $50k requires a few years of contributions or a rollover.")


if __name__ == "__main__":
    sys.exit(main())
