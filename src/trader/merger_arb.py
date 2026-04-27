"""Merger arbitrage scanner.

For each announced M&A deal in our deals registry, compute:
  - current spread (deal_price - market_price) / market_price
  - annualized return assuming deal closes on expected_close_date
  - implied break probability given historical 5-8% break rate
  - net expected value after break risk

Merger arb is one of the few near-riskless retail strategies. Historical:
  - HFRI Merger Arb Index: ~5-8%/yr long-run, low correlation to equities
  - Best deals: hostile takeovers (close fast), strategic acquisitions (low break risk)
  - Worst: mega-deals with regulatory risk (long timelines, antitrust risk)

This module provides the FRAMEWORK. Deals are loaded from a JSON registry that
you maintain by hand (or via a paid feed: InsideArbitrage, ArbLens, etc).
"""
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal


@dataclass
class MergerDeal:
    acquirer: str
    target_symbol: str
    deal_price: float           # cash component or implied value
    deal_type: Literal["all_cash", "all_stock", "mixed"]
    announced_date: date
    expected_close: date
    break_risk_estimate: float  # 0.0–1.0; historical retail-detectable: 0.05–0.20
    notes: str = ""


@dataclass
class DealAnalysis:
    deal: MergerDeal
    market_price: float
    spread_abs: float           # deal_price - market_price
    spread_pct: float           # spread / market_price
    days_to_close: int
    annualized_yield: float     # gross
    expected_value: float       # net of break risk
    rank_score: float           # higher = more attractive
    verdict: Literal["BUY", "WATCH", "SKIP"]


def load_deals(path: Path | str) -> list[MergerDeal]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open() as f:
        raw = json.load(f)
    deals = []
    for r in raw:
        deals.append(MergerDeal(
            acquirer=r["acquirer"],
            target_symbol=r["target_symbol"],
            deal_price=float(r["deal_price"]),
            deal_type=r["deal_type"],
            announced_date=date.fromisoformat(r["announced_date"]),
            expected_close=date.fromisoformat(r["expected_close"]),
            break_risk_estimate=float(r["break_risk_estimate"]),
            notes=r.get("notes", ""),
        ))
    return deals


def analyze_deal(deal: MergerDeal, market_price: float, asof: date | None = None) -> DealAnalysis:
    """Compute the merger-arb economics for one deal."""
    asof = asof or date.today()
    days_to_close = max((deal.expected_close - asof).days, 1)
    spread_abs = deal.deal_price - market_price
    spread_pct = spread_abs / market_price if market_price > 0 else 0.0

    # Annualized GROSS yield assuming deal closes
    annualized = (1 + spread_pct) ** (365 / days_to_close) - 1

    # Net expected value: P(close) * deal_return + P(break) * break_loss
    # Assume break loss = -25% of pre-deal price (typical "deal-pop" reversal)
    p_close = 1 - deal.break_risk_estimate
    break_return = -0.25  # rough; adjust by deal-specific knowledge
    expected_return = p_close * spread_pct + deal.break_risk_estimate * break_return
    expected_annualized = (1 + expected_return) ** (365 / days_to_close) - 1

    # Rank: prefer high net annualized yield, short timeline, low break risk
    rank = expected_annualized * (1 - deal.break_risk_estimate) * (60 / days_to_close)

    if expected_annualized > 0.06 and deal.break_risk_estimate < 0.10:
        verdict = "BUY"
    elif expected_annualized > 0.03:
        verdict = "WATCH"
    else:
        verdict = "SKIP"

    return DealAnalysis(
        deal=deal,
        market_price=market_price,
        spread_abs=spread_abs,
        spread_pct=spread_pct,
        days_to_close=days_to_close,
        annualized_yield=expected_annualized,
        expected_value=expected_return,
        rank_score=rank,
        verdict=verdict,
    )


def scan_deals(deals: list[MergerDeal], price_fetcher) -> list[DealAnalysis]:
    """Run analyze_deal on every deal; rank descending by attractiveness.

    price_fetcher: callable(symbol) -> float (current market price)
    """
    results = []
    for d in deals:
        try:
            mkt = float(price_fetcher(d.target_symbol))
            results.append(analyze_deal(d, mkt))
        except Exception:
            continue
    results.sort(key=lambda a: -a.rank_score)
    return results
