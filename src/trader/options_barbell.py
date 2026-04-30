"""OTM call barbell sleeve.

Take 10% of capital and buy 6-month 25%-OTM calls on top-3 momentum names.
Math:
  - Max loss: 10% of capital (calls expire worthless)
  - Max gain: unlimited (each call is theoretically infinite upside)
  - Asymmetric: capped downside, uncapped upside

Why this works:
  - Momentum stocks have higher kurtosis than implied by Black-Scholes
    (Cont 2001, Bouchaud-Potters): real-world fat right tails are
    underpriced by Black-Scholes-derived option premiums.
  - The cap on losses (10%) is structural, not a stop-loss — can't be
    panicked out of.
  - One 5x'er over 5 years pays for everything.

Pricing: Black-Scholes for cost estimation. Real execution will use Alpaca
options API (which uses live IV from the chain).

Strategy:
  - Every quarter, check expiring calls
  - Buy new calls: 6-month maturity, 25%-OTM (strike = spot × 1.25)
  - Underlying: top-3 momentum names from the SAME signal LIVE uses
  - Sizing: 10% of capital total / 3 names = 3.33% per call

Risk disclosure:
  - Call premiums are NOT the same as stock — they decay (theta)
  - At 6-month 25%-OTM, premiums are typically 3-8% of spot
  - For a $1000 stock at 25%-OTM: $30-80 per contract
  - At $100k account, 3.33% per name = $3,333 per name = ~30-100 contracts

Theory references:
  - Cont, R. (2001) "Empirical properties of asset returns: stylized facts
    and statistical issues" — fat tails in equity returns
  - Coval & Shumway (2001) "Expected Option Returns" — OTM call premium
    too low historically
  - Bondarenko (2014) "Why Are Put Options So Expensive?" — converse for
    puts; suggests OTM calls relatively underpriced
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import math
import numpy as np


@dataclass
class CallOption:
    underlying: str
    spot: float
    strike: float
    days_to_expiry: int
    premium_estimate: float  # per share
    contracts: int           # number of 100-share contracts


def black_scholes_call(spot: float, strike: float, days: int,
                        vol_annual: float, risk_free: float = 0.04) -> float:
    """Black-Scholes call price (per share).

    Args:
        spot: current price
        strike: strike price
        days: days to expiry (calendar)
        vol_annual: annualized volatility (decimal, e.g. 0.30 for 30%)
        risk_free: annualized risk-free rate
    """
    if days <= 0 or spot <= 0 or strike <= 0 or vol_annual <= 0:
        return max(spot - strike, 0.0)
    T = days / 365.25
    sqrtT = math.sqrt(T)
    d1 = (math.log(spot / strike) + (risk_free + 0.5 * vol_annual ** 2) * T) / (vol_annual * sqrtT)
    d2 = d1 - vol_annual * sqrtT
    # Standard normal CDF via erf
    N = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    return spot * N(d1) - strike * math.exp(-risk_free * T) * N(d2)


def select_otm_calls(picks: list[tuple[str, float, float]],
                     equity: float,
                     allocation: float = 0.10,
                     otm_pct: float = 0.25,
                     dte_target: int = 180) -> list[CallOption]:
    """Build the call barbell allocation.

    Args:
        picks: list of (ticker, spot, realized_vol_annual) for each pick
        equity: portfolio equity in dollars
        allocation: fraction of equity to allocate to barbell (default 10%)
        otm_pct: how far OTM (default 25%)
        dte_target: target days to expiry (default 180)

    Returns:
        List of CallOption recommendations.
    """
    if not picks or allocation <= 0:
        return []
    capital_for_barbell = equity * allocation
    capital_per_name = capital_for_barbell / len(picks)
    out = []
    for ticker, spot, vol in picks:
        if spot <= 0 or vol <= 0:
            continue
        strike = round(spot * (1 + otm_pct), 2)
        premium = black_scholes_call(spot, strike, dte_target, vol)
        if premium <= 0:
            continue
        # Number of 100-share contracts we can afford
        cost_per_contract = premium * 100
        contracts = int(capital_per_name / cost_per_contract)
        if contracts < 1:
            continue
        out.append(CallOption(
            underlying=ticker,
            spot=spot,
            strike=strike,
            days_to_expiry=dte_target,
            premium_estimate=premium,
            contracts=contracts,
        ))
    return out


def simulate_call_payoff(option: CallOption, terminal_spot: float) -> float:
    """Compute total $ payoff at expiry for the position.

    Returns: total $ pnl (positive = profit, negative = loss)
    Each contract = 100 shares.
    """
    payoff_per_share = max(terminal_spot - option.strike, 0.0)
    payoff_per_contract = payoff_per_share * 100
    cost = option.premium_estimate * 100
    pnl_per_contract = payoff_per_contract - cost
    return pnl_per_contract * option.contracts


def backtest_barbell_sleeve(spot_history: dict[str, "pd.Series"],
                             equity: float,
                             rebalance_dates: list,
                             allocation: float = 0.10,
                             otm_pct: float = 0.25,
                             dte_target: int = 180) -> dict:
    """Simulate the barbell sleeve over a date range.

    Args:
        spot_history: ticker → daily price series
        equity: starting equity (constant — barbell is rebalanced each cycle)
        rebalance_dates: list of pd.Timestamps (typically quarterly)

    Returns:
        Dict with cycle-by-cycle pnl, total return, mean cycle return, etc.
    """
    import pandas as pd
    cycle_pnls = []
    cycle_details = []
    for d in rebalance_dates:
        # Get current spot + 60-day realized vol for top picks
        candidates = []
        for ticker, hist in spot_history.items():
            sub = hist[hist.index <= d].dropna()
            if len(sub) < 252:
                continue
            try:
                spot = float(sub.iloc[-1])
                rets_60d = sub.pct_change().dropna().iloc[-60:]
                vol = float(rets_60d.std() * np.sqrt(252))
                # Compute 12-1 momentum
                ret_12_1 = float(sub.iloc[-21] / sub.iloc[-252] - 1)
                candidates.append((ret_12_1, ticker, spot, vol))
            except Exception:
                continue
        # Pick top-3 by 12-1 momentum
        candidates.sort(key=lambda x: -x[0])
        top3 = [(t, s, v) for _, t, s, v in candidates[:3]]
        if not top3:
            continue
        options = select_otm_calls(top3, equity, allocation, otm_pct, dte_target)
        if not options:
            continue
        # Simulate to expiry: dte_target days forward, what's the spot?
        cycle_pnl = 0.0
        details = []
        for opt in options:
            hist = spot_history.get(opt.underlying)
            if hist is None:
                continue
            terminal_idx = hist.index.searchsorted(d + pd.Timedelta(days=dte_target),
                                                    side="right") - 1
            if terminal_idx < 0 or terminal_idx >= len(hist):
                continue
            terminal_spot = float(hist.iloc[terminal_idx])
            pnl = simulate_call_payoff(opt, terminal_spot)
            cycle_pnl += pnl
            details.append({
                "ticker": opt.underlying,
                "spot_at_entry": opt.spot,
                "strike": opt.strike,
                "premium": opt.premium_estimate,
                "contracts": opt.contracts,
                "terminal_spot": terminal_spot,
                "pnl": pnl,
            })
        cycle_pnls.append({"date": d, "pnl": cycle_pnl, "details": details})
        cycle_details.append((d, cycle_pnl))
    if not cycle_pnls:
        return {"error": "no cycles completed"}
    total_pnl = sum(c["pnl"] for c in cycle_pnls)
    mean_cycle_pnl = total_pnl / len(cycle_pnls)
    win_rate = sum(1 for c in cycle_pnls if c["pnl"] > 0) / len(cycle_pnls)
    return {
        "n_cycles": len(cycle_pnls),
        "total_pnl": total_pnl,
        "mean_cycle_pnl": mean_cycle_pnl,
        "win_rate": win_rate,
        "best_cycle_pnl": max(c["pnl"] for c in cycle_pnls),
        "worst_cycle_pnl": min(c["pnl"] for c in cycle_pnls),
        "cycle_details": cycle_pnls,
    }
