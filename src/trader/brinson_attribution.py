"""Brinson-style PnL attribution (Bloomberg PORT-style).

Given a portfolio's daily returns + per-sector weights vs a benchmark,
decomposes total active return into three Brinson contributions:

  - **Allocation effect** — P&L from over/underweighting sectors that
    outperformed/underperformed the benchmark
  - **Selection effect** — P&L from picking outperforming names within a
    sector vs the sector benchmark
  - **Interaction effect** — cross-term capturing both effects together

Reference: Brinson, Hood, Beebower (1986) *Determinants of Portfolio
Performance*, Financial Analysts Journal. The classic decomposition.

We use a simplified single-period version. For multi-period (geometric
linking), see Carino (1999). At our monthly-rebalance cadence, single-
period chained gives sensible results without the linking complexity.

We don't have realized sector benchmark returns easily available, so this
module provides the PURE FRAMEWORK + a pragmatic version that uses
portfolio name-level returns weighted by sector. The interpretation is:
"if my SECTOR weights matched SPY's, would I have made/lost the same?"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class SectorAttribution:
    sector: str
    portfolio_weight: float       # avg weight of this sector in our book
    benchmark_weight: float        # avg weight of this sector in SPY
    portfolio_sector_return: float # weighted avg return of OUR names in this sector
    benchmark_sector_return: float # SPY-sector ETF return (XLF, XLK, etc.)
    allocation_effect: float       # (w_p - w_b) * r_b
    selection_effect: float        # w_b * (r_p - r_b)
    interaction_effect: float      # (w_p - w_b) * (r_p - r_b)
    total_active: float            # allocation + selection + interaction


@dataclass
class BrinsonReport:
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    portfolio_total_return: float = 0.0
    benchmark_total_return: float = 0.0
    active_return: float = 0.0
    by_sector: list[SectorAttribution] = field(default_factory=list)
    sum_allocation: float = 0.0
    sum_selection: float = 0.0
    sum_interaction: float = 0.0
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "period_start": str(self.period_start) if self.period_start else None,
            "period_end": str(self.period_end) if self.period_end else None,
            "portfolio_total_return": self.portfolio_total_return,
            "benchmark_total_return": self.benchmark_total_return,
            "active_return": self.active_return,
            "by_sector": [vars(s) for s in self.by_sector],
            "sum_allocation": self.sum_allocation,
            "sum_selection": self.sum_selection,
            "sum_interaction": self.sum_interaction,
            "notes": self.notes,
        }


# Sector ETF mapping for benchmark returns (SPDR sector ETFs)
SECTOR_ETF_MAP = {
    "Technology": "XLK",
    "Health Care": "XLV",
    "Financials": "XLF",
    "Communication Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Industrials": "XLI",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Materials": "XLB",
}


def compute_brinson(
    portfolio_weights: dict[str, float],
    portfolio_sector_returns: dict[str, float],
    benchmark_weights: dict[str, float],
    benchmark_sector_returns: dict[str, float],
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
) -> BrinsonReport:
    """Single-period Brinson attribution.

    Args:
        portfolio_weights: {sector_name: avg_weight_in_book}
        portfolio_sector_returns: {sector_name: weighted_return_of_our_names_in_sector}
        benchmark_weights: {sector_name: avg_weight_in_SPY}
        benchmark_sector_returns: {sector_name: sector_etf_return}

    Returns:
        BrinsonReport with per-sector decomposition + totals
    """
    rep = BrinsonReport(period_start=period_start, period_end=period_end)

    all_sectors = (set(portfolio_weights) | set(portfolio_sector_returns)
                   | set(benchmark_weights) | set(benchmark_sector_returns))

    p_total = 0.0
    b_total = 0.0

    for s in sorted(all_sectors):
        wp = portfolio_weights.get(s, 0.0)
        wb = benchmark_weights.get(s, 0.0)
        rp = portfolio_sector_returns.get(s, 0.0)
        rb = benchmark_sector_returns.get(s, 0.0)

        alloc = (wp - wb) * rb
        select = wb * (rp - rb)
        interact = (wp - wb) * (rp - rb)
        total = alloc + select + interact  # also = wp*rp - wb*rb

        rep.by_sector.append(SectorAttribution(
            sector=s, portfolio_weight=wp, benchmark_weight=wb,
            portfolio_sector_return=rp, benchmark_sector_return=rb,
            allocation_effect=alloc,
            selection_effect=select,
            interaction_effect=interact,
            total_active=alloc + select,  # report active without interaction
        ))
        rep.sum_allocation += alloc
        rep.sum_selection += select
        rep.sum_interaction += interact
        p_total += wp * rp
        b_total += wb * rb

    rep.portfolio_total_return = p_total
    rep.benchmark_total_return = b_total
    rep.active_return = p_total - b_total
    return rep
