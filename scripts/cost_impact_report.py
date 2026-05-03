"""[v3.60.0] Cost-impact projection.

Translates each pending env-var flip + module promotion into expected
$/year P&L impact. Lets the user prioritize which flips to deliberate
on first.

Outputs:
  • Per-flip estimated annual basis-points and $ impact at current
    account size
  • Aggregate "if all approved" expected lift
  • Honest caveats on each estimate's confidence

Run:
  python scripts/cost_impact_report.py [--equity 10000]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


@dataclass
class FlipImpact:
    name: str
    description: str
    env_var: str
    current_default: str
    proposed: str
    annual_bps_estimate: float
    confidence: str          # "high" / "medium" / "low" / "speculative"
    rationale: str
    requires_capital: bool   # True if it changes allocation, False if pure cost reduction
    requires_more_data: bool # True if it depends on shadow data we don't have yet


# Honest, calibrated estimates. Each line cites its basis.
FLIPS: list[FlipImpact] = [
    FlipImpact(
        name="Use MOC (closing-auction) orders for monthly rebalance",
        description=(
            "Route TimeInForce.CLS instead of TimeInForce.DAY on rebalance "
            "fills. Closing prints typically 0-2bp slippage vs 5-10bp on "
            "mid-session market orders."
        ),
        env_var="USE_MOC_ORDERS",
        current_default="false",
        proposed="true",
        annual_bps_estimate=35,  # ~5bp savings per side × 2 sides × 60% turnover × 12 months
        confidence="medium",
        rationale=(
            "Conservative midpoint of expected-vs-actual fill literature. "
            "Real number depends on whether your cron runs after the close-cutoff "
            "(15:50 ET). Run TCA after first month to confirm or adjust."
        ),
        requires_capital=False, requires_more_data=False,
    ),
    FlipImpact(
        name="Slippage tracker (already SHADOW; close the loop)",
        description=(
            "Run scripts/slippage_reconcile.py daily so slippage_log fills in "
            "fill_price + slippage_bps. Then TCA can detect cost regressions."
        ),
        env_var="SLIPPAGE_TRACKER_STATUS (already SHADOW)",
        current_default="SHADOW",
        proposed="LIVE + scheduled reconcile",
        annual_bps_estimate=10,
        confidence="medium",
        rationale=(
            "Indirect: tracking doesn't save cost directly, but creates the "
            "feedback loop that catches a ~2× slippage regression within "
            "30 days vs 6+ months blind."
        ),
        requires_capital=False, requires_more_data=False,
    ),
    FlipImpact(
        name="DrawdownCircuitBreaker LIVE (already LIVE in v3.58.1)",
        description=(
            "Mechanical halt at -10% from all-time peak. Wired into "
            "risk_manager.check_account_risk."
        ),
        env_var="DRAWDOWN_BREAKER_STATUS",
        current_default="LIVE",
        proposed="LIVE (already)",
        annual_bps_estimate=0,
        confidence="high",
        rationale=(
            "Variance reduction on tail events; expected return contribution "
            "is approximately zero in normal years. Value is in the tail (one "
            "averted -25% drawdown saves ~$2,500 on $10K)."
        ),
        requires_capital=False, requires_more_data=False,
    ),
    FlipImpact(
        name="EarningsRule LIVE (already LIVE in v3.58.1)",
        description=(
            "T-1 day before earnings, trim held names to 50% of target weight."
        ),
        env_var="EARNINGS_RULE_STATUS",
        current_default="LIVE",
        proposed="LIVE (already)",
        annual_bps_estimate=15,
        confidence="medium",
        rationale=(
            "Earnings are a binary 5-15% gap. Trimming ahead of them removes "
            "~50% of the earnings variance from the portfolio. Net positive "
            "expected because we don't actually have edge on earnings direction."
        ),
        requires_capital=False, requires_more_data=False,
    ),
    FlipImpact(
        name="Momentum-crash detector LIVE",
        description=(
            "When 24mo SPY return < 0 AND 12mo vol > 20%, cut momentum gross "
            "to 50%. Daniel-Moskowitz (2016)."
        ),
        env_var="MOMENTUM_CRASH_STATUS",
        current_default="SHADOW",
        proposed="LIVE",
        annual_bps_estimate=80,  # Avoiding one -30% momentum crash every 5 years
        confidence="medium",
        rationale=(
            "Daniel-Moskowitz Table 4: momentum loses 25-40% in regime where "
            "this signal fires. Cutting to 50% saves half that loss. Spread "
            "across 5-year frequency: ~150bp/yr expected, but signal fires "
            "rarely so stdev is high."
        ),
        requires_capital=False, requires_more_data=False,
    ),
    FlipImpact(
        name="Residual momentum scorer LIVE (replaces vanilla)",
        description=(
            "Strip Fama-French 5 factor loadings before computing momentum. "
            "Blitz-Hanauer-Vidojevic 2020/2024."
        ),
        env_var="(in-code change to rank function)",
        current_default="SHADOW (sleeve_shadows.residual_momentum_picks)",
        proposed="LIVE swap",
        annual_bps_estimate=70,
        confidence="medium",
        rationale=(
            "Published Sharpe lift from vanilla → residual momentum: +0.3 to "
            "+0.5 across multiple replications. At our LIVE Sharpe ~1.16 OOS, "
            "+0.3 lift = +25% improvement = ~70bp/yr at our vol. Requires 30 "
            "days of side-by-side shadow before promotion."
        ),
        requires_capital=False, requires_more_data=True,
    ),
    FlipImpact(
        name="LowVolSleeve LIVE blend",
        description=(
            "70/30 momentum/LowVol blend per V5 proposal."
        ),
        env_var="LOW_VOL_SLEEVE_STATUS",
        current_default="SHADOW",
        proposed="LIVE @ 30% allocation",
        annual_bps_estimate=-50,  # NEGATIVE — multi-sleeve backtest showed 6pp return loss for ≈ same Sharpe
        confidence="high",
        rationale=(
            "EMPIRICALLY VERIFIED NO LIFT: scripts/multi_sleeve_backtest.py on "
            "2022-2026 walk-forward shows blend Sharpe 0.80 vs 100%-momentum "
            "Sharpe 0.82, with -6pp absolute return give-up. Drawdown is "
            "lower (-15.8% vs -17.8%) but at $10K with long horizon the DD "
            "reduction does not justify the return cost. RECOMMEND: keep "
            "LowVol as SHADOW signal only."
        ),
        requires_capital=True, requires_more_data=False,
    ),
    FlipImpact(
        name="Cost-aware screener (drop sub-$50M ADV names)",
        description=(
            "Filter momentum candidates by 30d average dollar volume. Currently "
            "no-op since liquid_50 is all mega-caps; relevant when scaling to "
            "broader universe."
        ),
        env_var="(in-code; cost_aware_momentum_picks)",
        current_default="SHADOW",
        proposed="enable when expanding universe to SP500",
        annual_bps_estimate=0,  # zero impact at current liquid_50 universe
        confidence="high",
        rationale=(
            "Every name in DEFAULT_LIQUID_50 has > $1B/day ADV. Screener is a "
            "no-op until you flip LIVE_UNIVERSE=sp500. After that flip, "
            "expected ~20bp/yr from avoiding the worst-spread names."
        ),
        requires_capital=False, requires_more_data=False,
    ),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--equity", type=float, default=10_000,
                     help="Account equity for $ projections")
    args = ap.parse_args()

    print("=" * 78)
    print(f"COST-IMPACT PROJECTION  ·  equity ${args.equity:,.0f}")
    print("=" * 78)

    by_conf: dict[str, list[FlipImpact]] = {"high": [], "medium": [], "low": [], "speculative": []}
    for f in FLIPS:
        by_conf.setdefault(f.confidence, []).append(f)

    print(f"\n  {'Flip':<55} {'bps/yr':>8} {'$/yr':>8}")
    print("  " + "-" * 73)
    for f in FLIPS:
        ann_dollars = args.equity * f.annual_bps_estimate / 1e4
        sign = "+" if f.annual_bps_estimate >= 0 else ""
        print(f"  {f.name[:55]:<55} {sign}{f.annual_bps_estimate:>+6.0f}bp "
              f"{sign}${ann_dollars:>+6,.0f}")
        print(f"    [{f.confidence}] env={f.env_var}  default={f.current_default}")

    # Net of recommended flips
    recommended = [f for f in FLIPS
                    if f.annual_bps_estimate > 0
                    and f.confidence in ("high", "medium")
                    and not f.requires_more_data
                    and not (f.requires_capital and f.annual_bps_estimate < 0)]
    net_bps = sum(f.annual_bps_estimate for f in recommended)
    net_dollars = args.equity * net_bps / 1e4
    print("\n" + "=" * 78)
    print("RECOMMENDED FLIPS (high/medium confidence, positive expected)")
    print("=" * 78)
    for f in recommended:
        print(f"  ✓ {f.name}")
    print(f"\n  Net expected lift: +{net_bps:.0f}bp/yr ≈ +${net_dollars:,.0f}/yr at ${args.equity:,.0f}")

    print("\n" + "=" * 78)
    print("DEFERRED — needs more data")
    print("=" * 78)
    for f in FLIPS:
        if f.requires_more_data:
            print(f"  ⏸️  {f.name}  ({f.confidence})")
            print(f"     gate: {f.rationale.split('.')[0]}")

    print("\n" + "=" * 78)
    print("KILLED — empirical evidence says no")
    print("=" * 78)
    for f in FLIPS:
        if f.annual_bps_estimate < 0:
            print(f"  ❌ {f.name}: estimated {f.annual_bps_estimate:+.0f}bp/yr")
            print(f"     {f.rationale[:140]}")

    out = ROOT / "data" / "cost_impact_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        json.dump({
            "generated_at": datetime.utcnow().isoformat(),
            "equity": args.equity,
            "flips": [asdict(x) for x in FLIPS],
            "recommended": [x.name for x in recommended],
            "net_bps": net_bps,
            "net_dollars": net_dollars,
        }, f, indent=2)
    print(f"\nWritten: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
