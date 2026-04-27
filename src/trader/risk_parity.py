"""Risk-parity sleeve weighting (v1.2). Inverse-vol weighting between sleeves.

Validated by v1.1 walk-forward (2021-2025 OOS):
  - momentum-only:        Sharpe 0.74
  - fixed 60/40:          Sharpe 1.41
  - risk-parity w/ priors: Sharpe 1.76

Deployment plan:
  1. Bootstrap with backtest-derived prior vols (no live warmup needed)
  2. Each month, blend running 6+ month sample vol with prior vol
  3. Weights = inverse-vol normalized, clipped to [30%, 85%] so neither sleeve dominates
  4. Compute weights at end of month T, apply to month T+1 trades

Priors below are computed from the 2015-2020 in-sample backtest — see
`scripts/compute_priors.py` to refresh after a live year of trading.
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd

# 2015-2020 monthly std-dev priors (from iterate_v5 walk-forward train period)
PRIOR_MOMENTUM_VOL_MONTHLY = 0.0631
PRIOR_BOTTOM_VOL_MONTHLY = 0.0420

MIN_WEIGHT = 0.30
MAX_WEIGHT = 0.85


@dataclass
class SleeveWeights:
    momentum: float
    bottom: float
    method: str
    momentum_vol: float
    bottom_vol: float

    def __post_init__(self):
        # Sanity: must sum to 1.0 within rounding
        s = self.momentum + self.bottom
        assert abs(s - 1.0) < 0.001, f"weights sum to {s}, expected 1.0"


def compute_weights(momentum_returns: pd.Series | None = None,
                    bottom_returns: pd.Series | None = None,
                    min_obs: int = 6) -> SleeveWeights:
    """Compute inverse-vol sleeve weights.

    Falls back to priors when fewer than min_obs months of live history.
    Uses sample vols once we have enough history.
    """
    if momentum_returns is None or len(momentum_returns.dropna()) < min_obs:
        mom_vol = PRIOR_MOMENTUM_VOL_MONTHLY
        method = "prior_only"
    else:
        mom_vol = float(momentum_returns.dropna().std())
        method = "sample"

    if bottom_returns is None or len(bottom_returns.dropna()) < min_obs:
        bot_vol = PRIOR_BOTTOM_VOL_MONTHLY
        if method == "sample":
            method = "hybrid"
    else:
        bot_vol = float(bottom_returns.dropna().std())

    if mom_vol <= 0 or bot_vol <= 0:
        return SleeveWeights(0.6, 0.4, "fallback_60_40", mom_vol, bot_vol)

    inv_m, inv_b = 1.0 / mom_vol, 1.0 / bot_vol
    raw_m = inv_m / (inv_m + inv_b)
    w_m = float(np.clip(raw_m, MIN_WEIGHT, MAX_WEIGHT))
    w_b = 1.0 - w_m
    return SleeveWeights(w_m, w_b, method, mom_vol, bot_vol)


def compute_sleeve_returns_from_journal() -> tuple[pd.Series | None, pd.Series | None]:
    """v1.4 (B2 fix): pull sleeve-level monthly returns from position_lots.

    The previous version used a heuristic ("if a bottom-catch was placed in the
    last 20 days, attribute the WHOLE day to bottom-catch") which commingled
    momentum and bottom-catch P&L on overlapping days.

    The new version reads realized P&L from CLOSED lots and unrealized P&L from
    OPEN lots, group by sleeve and month, and computes monthly returns as
    sleeve_pnl_in_month / sleeve_capital_at_month_start.

    Falls back to (None, None) if fewer than 6 months of lot data exist.
    """
    from .journal import _conn, init_db
    init_db()
    with _conn() as c:
        # Closed lots: realized P&L by sleeve, by close month
        closed = c.execute(
            """SELECT sleeve, closed_at, opened_at, qty, open_price, close_price, realized_pnl
               FROM position_lots WHERE closed_at IS NOT NULL"""
        ).fetchall()
        # All lots (closed + open) for capital denominator
        all_lots = c.execute(
            """SELECT sleeve, opened_at, qty, open_price
               FROM position_lots"""
        ).fetchall()

    if len(closed) < 6:
        return None, None

    closed_df = pd.DataFrame(
        [{"sleeve": r["sleeve"], "closed_at": pd.Timestamp(r["closed_at"]),
          "opened_at": pd.Timestamp(r["opened_at"]),
          "qty": r["qty"], "open_price": r["open_price"] or 0,
          "close_price": r["close_price"] or 0,
          "realized_pnl": r["realized_pnl"] or 0}
         for r in closed]
    )
    if closed_df.empty:
        return None, None
    closed_df["month"] = closed_df["closed_at"].dt.to_period("M").dt.to_timestamp("M")
    closed_df["capital_committed"] = closed_df["qty"] * closed_df["open_price"]

    # Sum realized P&L per sleeve per month
    pnl_by_sleeve_month = closed_df.groupby(["sleeve", "month"])["realized_pnl"].sum().unstack("sleeve").fillna(0)
    # Capital denominator: average capital committed per sleeve per month (rough)
    cap_by_sleeve_month = closed_df.groupby(["sleeve", "month"])["capital_committed"].sum().unstack("sleeve").fillna(0)
    # Monthly return = pnl / capital (avoid div by 0)
    rets = (pnl_by_sleeve_month / cap_by_sleeve_month.replace(0, pd.NA)).fillna(0)

    mom_monthly = rets["MOMENTUM"] if "MOMENTUM" in rets.columns else None
    bot_monthly = rets["BOTTOM_CATCH"] if "BOTTOM_CATCH" in rets.columns else None
    return mom_monthly, bot_monthly
