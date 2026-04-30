"""GARCH(1,1) conditional volatility forecasting for risk-targeting.

Engle (1982) ARCH and Bollerslev (1986) GARCH model conditional variance
of returns. The classic GARCH(1,1):

    σ²_{t+1} = ω + α·ε²_t + β·σ²_t

where ε_t is the previous period's return shock, σ_t is the previous
period's volatility, and ω, α, β are estimated via maximum likelihood.

Why GARCH for risk targeting:
  - Volatility clusters (high vol begets high vol; low vol begets low)
  - GARCH captures this autocorrelation explicitly
  - Predictions are FORWARD-looking (uses today to predict tomorrow)
  - Distinct from drawdown-scaling (which is BACKWARD — fires after damage)

Strategy: scale gross exposure inversely to GARCH forecast. When vol is
predicted to be high tomorrow, reduce exposure proactively.

References:
  - Engle, R.F. (1982) "Autoregressive Conditional Heteroskedasticity"
  - Bollerslev, T. (1986) "Generalized Autoregressive Conditional Heteroskedasticity"
  - Moreira, A. & Muir, T. (2017) "Volatility-Managed Portfolios"
    — formalizes the vol-targeting alpha (Sharpe ~ 0.7 historical)
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
from arch import arch_model


def fit_garch(returns: pd.Series, p: int = 1, q: int = 1,
              dist: str = "normal") -> tuple[object, float]:
    """Fit GARCH(p, q) on a return series. Returns (fitted_model, next_period_vol).

    Args:
        returns: daily returns (decimal, NOT percent)
        p: ARCH order
        q: GARCH order
        dist: error distribution; "normal" or "t" (heavier tails)

    Returns:
        (fitted_model, next_period_predicted_volatility) — vol in same units as returns
    """
    r = returns.dropna()
    if len(r) < 100:
        return None, float(r.std())  # fallback: realized vol
    # arch package expects returns in percent for numerical stability
    r_pct = r * 100
    try:
        model = arch_model(r_pct, vol="GARCH", p=p, q=q, dist=dist, rescale=False)
        res = model.fit(disp="off", show_warning=False)
        # Forecast next period's variance
        forecast = res.forecast(horizon=1, reindex=False)
        next_var = float(forecast.variance.iloc[-1, 0])
        next_vol = np.sqrt(next_var) / 100  # back to decimal
        return res, next_vol
    except Exception:
        return None, float(r.std())


def vol_target_scaling(forecast_vol: float, target_vol_annual: float = 0.15) -> float:
    """Compute exposure multiplier for vol-targeting.

    Args:
        forecast_vol: GARCH forecast of next-period DAILY vol (decimal)
        target_vol_annual: target portfolio ANNUALIZED vol (default 15% =
                           market-like). Higher → more aggressive sizing.

    Returns:
        Multiplier in [0, 2] applied to gross exposure. Capped at 2 to
        prevent excessive leverage when realized vol is extremely low.
    """
    if forecast_vol is None or forecast_vol <= 0:
        return 1.0
    forecast_vol_annual = forecast_vol * np.sqrt(252)
    if forecast_vol_annual <= 0:
        return 1.0
    multiplier = target_vol_annual / forecast_vol_annual
    return float(min(max(multiplier, 0.0), 2.0))


def garch_vol_at(returns_series: pd.Series, target_vol_annual: float = 0.15) -> float:
    """One-shot helper: returns the next-period scaling multiplier given a
    return series. Uses 252 most-recent days for fitting."""
    r = returns_series.dropna().iloc[-504:]  # 2 years of history
    if len(r) < 100:
        # Fall back to simple realized vol
        realized_annual = float(r.std() * np.sqrt(252))
        if realized_annual <= 0:
            return 1.0
        return min(max(target_vol_annual / realized_annual, 0.0), 2.0)
    _, forecast_vol = fit_garch(r)
    return vol_target_scaling(forecast_vol, target_vol_annual)
