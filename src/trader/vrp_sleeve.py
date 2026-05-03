"""[v3.59.1 — V5 Sleeve A SCAFFOLD] Variance Risk Premium sleeve.

Systematically sells defined-risk SPY put-spreads (~30 delta short put,
~10 delta long put, 30 days to expiry) to capture the gap between
implied and realized volatility. Per Carr-Wu (2009), Bondarenko (2014),
AQR vol-premium work — Sharpe 0.5-1.0 globally.

⚠️  This is a SCAFFOLD. Status defaults to NOT_WIRED. Promotion requires:
   1. Backtest on 2018-Q1 (Volmageddon) and 2020-Q1 (COVID) regimes
   2. 60-day shadow validation (longer than other sleeves due to tail)
   3. Sleeve-level kill switch wired (-25% in 5d → freeze)
   4. Adversarial review pass
   5. Behavioral pre-commit signed

This module's compute_signal() returns a *plan* (which strikes to write,
which to buy, what credit, what max-loss). plan_today() never submits
orders. To execute (when LIVE), pair with vrp_executor.py (not yet
written; explicitly out of scope until shadow validation is done).

Free-tier data adapter:
  • Uses yfinance options chain (Ticker.option_chain). Free, but limited:
    delta is computed by us from Black-Scholes, not provided. Risk-free
    rate hardcoded at 5% (FRED 3M T-bill, refresh annually).
  • For backtest: yfinance lacks historical options snapshots. Use the
    Cboe Datashop free quarterly samples for true validation, or treat
    backtest as forward-only (90 days in shadow before any LIVE flip).

Defined-risk by construction:
  • Always SELL one put + BUY one further-OTM put. Maximum loss per
    cycle = (short_strike - long_strike) - credit. Sized to keep
    sleeve_max_loss < 2% of total portfolio per trade.
  • Never sell naked. Never sell on individual names (SPY/SPX only).

Roll cadence: monthly. Open ~30 DTE; close at 7 DTE or 50% profit.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional


SLEEVE_ALLOCATION_PCT_DEFAULT = 0.15  # 15% of capital reserved
RISK_FREE_RATE = 0.05                  # 3M T-bill — refresh annually
DAYS_TO_EXPIRY_TARGET = 30
SHORT_DELTA_TARGET = 0.30              # 30-delta short put
LONG_DELTA_TARGET = 0.10               # 10-delta long put (further OTM)
MAX_LOSS_PCT_PORTFOLIO = 0.02          # per-cycle


@dataclass
class VrpPlan:
    """One cycle's plan."""
    underlying: str = "SPY"
    expiry: Optional[date] = None
    short_strike: Optional[float] = None
    long_strike: Optional[float] = None
    short_delta: Optional[float] = None
    long_delta: Optional[float] = None
    short_premium: Optional[float] = None
    long_premium: Optional[float] = None
    credit: Optional[float] = None
    max_loss_per_spread: Optional[float] = None
    n_spreads: int = 0
    sleeve_capital: float = 0.0
    rationale: str = ""
    error: Optional[str] = None


def status() -> str:
    """NOT_WIRED (default) / SHADOW / LIVE.

    NOT_WIRED: no compute_signal call from cron.
    SHADOW: compute + log; no broker call.
    LIVE: compute + execute via vrp_executor (not yet written).
    """
    return os.getenv("VRP_SLEEVE_STATUS", "NOT_WIRED").upper()


def sleeve_capital_pct() -> float:
    try:
        return float(os.getenv("VRP_SLEEVE_PCT",
                                str(SLEEVE_ALLOCATION_PCT_DEFAULT)))
    except Exception:
        return SLEEVE_ALLOCATION_PCT_DEFAULT


# ---------------- Black-Scholes for delta computation ----------------
def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _d1(spot: float, strike: float, t_years: float, vol: float,
         r: float = RISK_FREE_RATE) -> float:
    if t_years <= 0 or vol <= 0:
        return float("nan")
    return (math.log(spot / strike) + (r + 0.5 * vol * vol) * t_years) / (
        vol * math.sqrt(t_years))


def put_delta(spot: float, strike: float, t_years: float, vol: float,
                r: float = RISK_FREE_RATE) -> float:
    """Black-Scholes put delta — negative number in (-1, 0).
    For "30-delta short put" we look for strikes where abs(delta) ≈ 0.30."""
    d1 = _d1(spot, strike, t_years, vol, r)
    if math.isnan(d1):
        return float("nan")
    return _norm_cdf(d1) - 1


# ---------------- Strike selection from a chain ----------------
@dataclass
class OptionRow:
    strike: float
    bid: float
    ask: float
    iv: float
    last: float = 0.0


def select_strikes(chain: list[OptionRow], spot: float, days_to_expiry: int,
                    short_delta: float = SHORT_DELTA_TARGET,
                    long_delta: float = LONG_DELTA_TARGET
                    ) -> tuple[Optional[OptionRow], Optional[OptionRow]]:
    """Return (short_put_row, long_put_row) closest to target deltas.
    Returns (None, None) on insufficient data."""
    if not chain or days_to_expiry <= 0:
        return None, None
    t = days_to_expiry / 365.25
    enriched = []
    for row in chain:
        if row.iv <= 0 or row.bid <= 0:
            continue
        d = put_delta(spot, row.strike, t, row.iv)
        if math.isnan(d):
            continue
        enriched.append((abs(d), row))
    if len(enriched) < 4:
        return None, None
    short = min(enriched, key=lambda t: abs(t[0] - short_delta))[1]
    long_ = min(enriched, key=lambda t: abs(t[0] - long_delta))[1]
    if short.strike <= long_.strike:
        return None, None  # spread must be short>long
    return short, long_


# ---------------- Plan ----------------
def plan_today(spot: float, chain: list[OptionRow], total_equity: float,
                today: Optional[date] = None) -> VrpPlan:
    """Pure: compute today's spread plan. No broker call."""
    today = today or datetime.utcnow().date()
    expiry = today + timedelta(days=DAYS_TO_EXPIRY_TARGET)
    sleeve_cap = total_equity * sleeve_capital_pct()
    if sleeve_cap <= 100:
        return VrpPlan(error="sleeve capital too small (<$100)")

    short, long_ = select_strikes(chain, spot, DAYS_TO_EXPIRY_TARGET)
    if not short or not long_:
        return VrpPlan(error="strike selection failed (chain empty or sparse)")

    # Mid-price approximations for premia
    short_premium = (short.bid + short.ask) / 2 if short.bid and short.ask else short.last
    long_premium = (long_.bid + long_.ask) / 2 if long_.bid and long_.ask else long_.last
    credit_per_spread = (short_premium - long_premium) * 100  # 100 multiplier
    width = (short.strike - long_.strike) * 100
    max_loss_per_spread = width - credit_per_spread

    if max_loss_per_spread <= 0:
        return VrpPlan(error="negative max loss — chain prices inverted")

    max_dollar_loss_total = total_equity * MAX_LOSS_PCT_PORTFOLIO
    n_spreads = max(int(max_dollar_loss_total / max_loss_per_spread), 0)
    n_spreads = min(n_spreads, int(sleeve_cap // max_loss_per_spread))

    # Compute deltas on selected strikes for the rationale
    t = DAYS_TO_EXPIRY_TARGET / 365.25
    sd = put_delta(spot, short.strike, t, short.iv)
    ld = put_delta(spot, long_.strike, t, long_.iv)

    return VrpPlan(
        underlying="SPY",
        expiry=expiry,
        short_strike=short.strike,
        long_strike=long_.strike,
        short_delta=sd, long_delta=ld,
        short_premium=short_premium, long_premium=long_premium,
        credit=credit_per_spread,
        max_loss_per_spread=max_loss_per_spread,
        n_spreads=n_spreads,
        sleeve_capital=sleeve_cap,
        rationale=(
            f"Sell SPY {expiry} {short.strike:.0f}P / buy {long_.strike:.0f}P, "
            f"{n_spreads} spreads. Net credit ${credit_per_spread:.2f}/spread × "
            f"{n_spreads} = ${credit_per_spread*n_spreads:.0f}. Max loss "
            f"${max_loss_per_spread*n_spreads:.0f} (~{max_loss_per_spread*n_spreads/total_equity*100:.1f}% of equity)."
        ),
    )


def fetch_chain_yfinance(ticker: str = "SPY",
                          days_to_expiry: int = DAYS_TO_EXPIRY_TARGET
                          ) -> tuple[float, list[OptionRow]]:
    """Free-tier chain fetch. Returns (spot, [OptionRow...] for puts).

    Uses yfinance Ticker.option_chain. Limitations: yfinance only
    returns expiries the broker explicitly lists; we pick the expiry
    closest to days_to_expiry from today.
    """
    try:
        import yfinance as yf  # type: ignore
        t = yf.Ticker(ticker)
        spot = float(t.history(period="1d")["Close"].iloc[-1])
        target = datetime.utcnow().date() + timedelta(days=days_to_expiry)
        expiries = t.options or []
        if not expiries:
            return spot, []
        # Pick closest expiry
        chosen = min(expiries, key=lambda d: abs(
            (datetime.fromisoformat(d).date() - target).days))
        opt = t.option_chain(chosen)
        puts = opt.puts
        rows: list[OptionRow] = []
        for _, r in puts.iterrows():
            try:
                rows.append(OptionRow(
                    strike=float(r["strike"]),
                    bid=float(r.get("bid", 0) or 0),
                    ask=float(r.get("ask", 0) or 0),
                    iv=float(r.get("impliedVolatility", 0) or 0),
                    last=float(r.get("lastPrice", 0) or 0),
                ))
            except Exception:
                continue
        return spot, rows
    except Exception:
        return 0.0, []
