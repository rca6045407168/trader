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
    """Pull historical sleeve P&L from the journal.

    Reconstructs monthly returns per sleeve by joining decisions (which tag style)
    with daily snapshots (which contain equity over time). Returns None if not
    enough data for either sleeve.
    """
    from .journal import _conn
    with _conn() as c:
        snaps = c.execute("SELECT date, equity FROM daily_snapshot ORDER BY date").fetchall()
        decisions = c.execute(
            "SELECT date(ts) as d, style, ticker FROM decisions WHERE style IN ('MOMENTUM', 'BOTTOM_CATCH')"
        ).fetchall()
    if len(snaps) < 30:
        return None, None

    eq = pd.Series({pd.Timestamp(s["date"]): s["equity"] for s in snaps}).sort_index()
    daily_ret = eq.pct_change().dropna()

    # Tag each day by which sleeve had open positions.
    # Heuristic: a day is "BOTTOM_CATCH active" if any bottom-catch decision
    # happened in the prior 20 days. Otherwise it's pure MOMENTUM days.
    bot_active = pd.Series(False, index=daily_ret.index)
    bot_dates = sorted({pd.Timestamp(d["d"]) for d in decisions if d["style"] == "BOTTOM_CATCH"})
    for bd in bot_dates:
        # 20-business-day window after bd
        for offset in range(1, 21):
            day = bd + pd.tseries.offsets.BDay(offset)
            if day in bot_active.index:
                bot_active.loc[day] = True

    mom_daily = daily_ret[~bot_active]
    bot_daily = daily_ret[bot_active]

    mom_monthly = mom_daily.resample("ME").apply(lambda s: (1 + s).prod() - 1) if len(mom_daily) > 0 else None
    bot_monthly = bot_daily.resample("ME").apply(lambda s: (1 + s).prod() - 1) if len(bot_daily) > 0 else None
    return mom_monthly, bot_monthly
