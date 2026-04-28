"""Strategy variants registered with the A/B framework.

Pattern: each variant is a function returning {ticker: portfolio_pct} target weights.
Registration happens at module import.

CURRENT REGISTRATIONS (v2.9):
  - momentum_top5_eq_v1 — LIVE — 12-month momentum, top-5 from liquid-50, equal-weight
  - momentum_top5_sector_capped_v1 — SHADOW — same picks but max 25% per GICS sector
  - momentum_top10_diluted_v1 — SHADOW — top-10 instead of top-5 (less concentrated)

Why these shadows: the 20-agent debate showed sector cap was a real trade-off
(reduces drawdown but lowers CAGR). Shadow lets us measure on LIVE data over 30+ days
before committing capital. Top-10 dilution is a simpler diversification play.

Each shadow is logged but emits NO orders. After 30+ days of evidence,
scripts/compare_variants.py determines which (if any) to promote.
"""
from __future__ import annotations

from typing import Any

from .ab import register_variant
from .strategy import rank_momentum
from .sectors import get_sector


def momentum_top5_eq(universe: list[str], equity: float,
                    account_state: dict[str, Any], **kwargs) -> dict[str, float]:
    """OLD live (now retired): top-5 by 12m momentum, equal-weight at 40%."""
    picks = rank_momentum(universe, top_n=5)
    if not picks:
        return {}
    weight = 0.40 / len(picks)
    return {c.ticker: weight for c in picks}


def momentum_top3_aggressive(universe: list[str], equity: float,
                              account_state: dict[str, Any], **kwargs) -> dict[str, float]:
    """LIVE v3.1: top-3 momentum at 80% allocation. Promoted after 5-regime stress test
    showed top-3 dominates top-5 by Sharpe in EVERY regime tested (2018Q4, 2020Q1,
    2022, 2023, recent). Mean Sharpe 1.65 vs top-5 1.53. CAGR scales linearly with
    allocation; 80% chosen for max-profit objective. Worst observed MaxDD across
    regimes: -26.3% (2020 COVID). Kill switch fires at -8% from 30d peak.
    """
    picks = rank_momentum(universe, top_n=3)
    if not picks:
        return {}
    weight = 0.80 / len(picks)  # 80%/3 ≈ 26.7% per name
    return {c.ticker: weight for c in picks}


def momentum_top5_sector_capped(universe: list[str], equity: float,
                                 account_state: dict[str, Any], **kwargs) -> dict[str, float]:
    """SHADOW: top-5 momentum but max 1 per GICS sector (effective 20% sector cap)."""
    candidates = rank_momentum(universe, top_n=20)
    selected: list = []
    sectors_used: set[str] = set()
    for c in candidates:
        sec = get_sector(c.ticker)
        if sec in sectors_used:
            continue
        sectors_used.add(sec)
        selected.append(c)
        if len(selected) >= 5:
            break
    if not selected:
        return {}
    weight = 0.40 / len(selected)
    return {c.ticker: weight for c in selected}


def momentum_top10_diluted(universe: list[str], equity: float,
                            account_state: dict[str, Any], **kwargs) -> dict[str, float]:
    """SHADOW: top-10 instead of top-5 — naive diversification."""
    picks = rank_momentum(universe, top_n=10)
    if not picks:
        return {}
    weight = 0.40 / len(picks)
    return {c.ticker: weight for c in picks}


def calendar_anomalies(universe: list[str], equity: float,
                       account_state: dict[str, Any], **kwargs) -> dict[str, float]:
    """SHADOW: SPY-only bets on empirically-validated calendar anomalies.

    Combines 4 effects from v1.7 / v1.8 empirical retests (sources cited
    in CAVEATS.md):
      - Pre-FOMC drift: long SPY 1 day before FOMC (8 events/yr).
        Lucca-Moench claim +49bps; our 2015-2025: +21.5bps, Sharpe 2.35.
      - Pre-holiday drift: long SPY day before US market holidays (9/yr).
        Ariel 1990 claim +12bps; our 2015-2025: +11.8bps excess. REPLICATED.
      - OPEX week: long SPY Mon-Thu of third Friday week (12/yr).
        Stoll-Whaley 1987 claim +20bps; our 2015-2025: +10.5bps.
      - Year-end reversal: long IWM Dec 20 → Jan 31.
        Reinganum 1983 claim +200bps; our 2015-2025: +139bps.

    Does NOTHING on days that don't match a fired anomaly. Otherwise allocates
    a fraction of equity to SPY (or IWM for year-end). All advisory until 30+
    days of evidence accumulate via the A/B framework.
    """
    from datetime import date
    from .anomalies import (
        scan_anomalies, detect_pre_fomc, detect_pre_holiday,
        detect_opex_week, detect_year_end_reversal,
    )

    today = date.today()
    triggered = scan_anomalies(today)
    if not triggered:
        return {}

    # Allocation: weight by confidence + alpha estimate, cap total at 20%
    weight_for_conf = {"high": 0.10, "medium": 0.05, "low": 0.02}
    targets: dict[str, float] = {}
    for a in triggered:
        w = weight_for_conf.get(a.confidence, 0)
        if w <= 0:
            continue
        # use IWM for year-end (tax-loss reversal); SPY for the rest
        sym = "IWM" if a.target_symbol == "IWM" else "SPY"
        targets[sym] = targets.get(sym, 0) + w
    # Cap total at 20% of capital so we don't overfit a noisy day
    total = sum(targets.values())
    if total > 0.20:
        scale = 0.20 / total
        targets = {k: v * scale for k, v in targets.items()}
    return targets


# (live + 2 prior shadows registered above)
register_variant(
    variant_id="calendar_anomalies_v1",
    name="calendar_anomalies",
    version="1.0",
    status="shadow",
    fn=calendar_anomalies,
    description="Pre-FOMC + pre-holiday + OPEX + year-end reversal sleeve. "
                "All 4 components empirically retested; alpha ~+1.8% (FOMC) + "
                "+1.1% (holidays) + +1.2% (OPEX) + +1.4% (year-end IWM) annual. "
                "Total stackable ~5-6% per yr if uncorrelated. "
                "3-month backfill (Jan-Apr 2026): +0.05% — most of window had no triggers.",
    params={"max_alloc": 0.20, "events": ["pre_fomc", "pre_holiday", "opex", "year_end"]},
)


# v3.0 aggressive variants — registered as shadows after 3-month backfill showed
# they crushed live in mom-friendly regime. Need 30+ days of live evidence before promotion.
def momentum_top3_concentrated(universe: list[str], equity: float,
                                 account_state: dict[str, Any], **kwargs) -> dict[str, float]:
    """SHADOW: top-3 momentum (more concentrated). 3-mo backfill +16.66% vs LIVE +10.58%."""
    picks = rank_momentum(universe, top_n=3)
    if not picks:
        return {}
    weight = 0.40 / len(picks)  # keep same sleeve allocation for fair comparison
    return {c.ticker: weight for c in picks}


def momentum_full_allocation(universe: list[str], equity: float,
                              account_state: dict[str, Any], **kwargs) -> dict[str, float]:
    """SHADOW: top-5 momentum at FULL 80% allocation (fix cash drag)."""
    picks = rank_momentum(universe, top_n=5)
    if not picks:
        return {}
    weight = 0.80 / len(picks)
    return {c.ticker: weight for c in picks}


def momentum_top3_full(universe: list[str], equity: float,
                        account_state: dict[str, Any], **kwargs) -> dict[str, float]:
    """SHADOW: top-3 + 80% allocation. Most aggressive. Highest expected return + drawdown."""
    picks = rank_momentum(universe, top_n=3)
    if not picks:
        return {}
    weight = 0.80 / len(picks)
    return {c.ticker: weight for c in picks}


register_variant(
    variant_id="momentum_top3_concentrated_v1",
    name="momentum_top3_concentrated",
    version="1.0",
    status="shadow",
    fn=momentum_top3_concentrated,
    description="Top-3 momentum, same 40% sleeve. 3-mo backfill: +16.66% vs LIVE +10.58%, "
                "Sharpe 4.82, MaxDD -0.85% (vs -0.59% LIVE). Statistically better in 3-mo "
                "sample (p=0.026). Watch for regime-change drawdown (top-3 had -38% MaxDD in "
                "10-yr backtest vs top-5 -32%).",
    params={"top_n": 3, "alloc": 0.40},
)

register_variant(
    variant_id="momentum_full_allocation_v1",
    name="momentum_full_allocation",
    version="1.0",
    status="shadow",
    fn=momentum_full_allocation,
    description="Top-5 at 80% allocation (vs LIVE's risk-parity 40%). 3-mo backfill: +22.08% "
                "vs LIVE +10.58%, Sharpe 4.90, MaxDD -1.18%. Eliminates cash drag. Same picks, "
                "more capital deployed.",
    params={"top_n": 5, "alloc": 0.80},
)

register_variant(
    variant_id="momentum_top3_full_v1",
    name="momentum_top3_full",
    version="1.0",
    status="shadow",
    fn=momentum_top3_full,
    description="Top-3 + 80% — most aggressive. 3-mo backfill: +35.55% vs LIVE +10.58%, "
                "Sharpe 4.82, MaxDD -1.69%. Stat-significant outperformance (p<0.01). "
                "Doubles concentration risk. Track over 30+ days before promotion.",
    params={"top_n": 3, "alloc": 0.80},
)


# Register variants on import
register_variant(
    variant_id="momentum_top5_eq_v1",
    name="momentum_top5_eq",
    version="1.0",
    status="retired",
    fn=momentum_top5_eq,
    description="RETIRED v3.1: 12m momentum top-5 equal-weight at 40% sleeve. "
                "Replaced by momentum_top3_aggressive_v1 after 5-regime stress test "
                "showed top-3 dominates top-5 by Sharpe in every regime.",
    params={"top_n": 5, "lookback_months": 12, "weighting": "equal", "alloc": 0.40},
)

register_variant(
    variant_id="momentum_top3_aggressive_v1",
    name="momentum_top3_aggressive",
    version="1.0",
    status="live",
    fn=momentum_top3_aggressive,
    description="LIVE v3.1: 12m momentum top-3 equal-weight at 80% sleeve. "
                "Promoted after regime stress test (2018Q4 / 2020Q1 / 2022 / 2023 / "
                "recent): mean Sharpe 1.65 (vs top-5's 1.53), mean CAGR 73.7%, worst "
                "MaxDD -26.3%. ~26.7% per-name; requires MAX_POSITION_PCT >= 0.27.",
    params={"top_n": 3, "lookback_months": 12, "weighting": "equal", "alloc": 0.80},
)

register_variant(
    variant_id="momentum_top5_sector_capped_v1",
    name="momentum_top5_sector_capped",
    version="1.0",
    status="shadow",
    fn=momentum_top5_sector_capped,
    description="Same top-5 momentum, but 1-per-sector. Backtest 2015-2025 showed -4.4% CAGR / Sharpe-neutral / -8.4% MaxDD better. Trade-off; testing live.",
    params={"top_n": 5, "max_per_sector": 1, "alloc": 0.40},
)

register_variant(
    variant_id="momentum_top10_diluted_v1",
    name="momentum_top10_diluted",
    version="1.0",
    status="shadow",
    fn=momentum_top10_diluted,
    description="Top-10 instead of top-5 — naive diversification across more names.",
    params={"top_n": 10, "weighting": "equal", "alloc": 0.40},
)
