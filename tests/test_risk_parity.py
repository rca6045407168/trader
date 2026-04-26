"""Unit tests for risk_parity. Validates the v1.2 sleeve weighting logic."""
import pandas as pd
import numpy as np
import pytest

from trader.risk_parity import (
    compute_weights, SleeveWeights,
    PRIOR_MOMENTUM_VOL_MONTHLY, PRIOR_BOTTOM_VOL_MONTHLY,
    MIN_WEIGHT, MAX_WEIGHT,
)


def test_priors_only_when_no_history():
    sw = compute_weights(None, None)
    assert sw.method == "prior_only"
    assert MIN_WEIGHT <= sw.momentum <= MAX_WEIGHT
    assert MIN_WEIGHT <= sw.bottom <= MAX_WEIGHT
    assert abs(sw.momentum + sw.bottom - 1.0) < 0.001


def test_priors_used_when_short_history():
    short = pd.Series([0.01, 0.02, -0.01])  # only 3 obs, less than min_obs=6
    sw = compute_weights(short, short)
    assert sw.method == "prior_only"


def test_sample_used_when_enough_history():
    rng = np.random.default_rng(0)
    mom = pd.Series(rng.normal(0.01, 0.05, 24))  # 24 monthly returns, vol 5%
    bot = pd.Series(rng.normal(0.01, 0.03, 24))  # vol 3%
    sw = compute_weights(mom, bot)
    assert sw.method == "sample"
    # Higher-vol momentum should get LOWER weight (inverse-vol logic)
    assert sw.momentum < sw.bottom, f"got mom={sw.momentum} bot={sw.bottom}"


def test_weights_clipped_at_min():
    # Construct momentum returns with VERY high vol so its weight wants to crash to ~0
    rng = np.random.default_rng(42)
    mom = pd.Series(rng.normal(0, 0.30, 24))  # 30% monthly vol — absurd
    bot = pd.Series(rng.normal(0, 0.01, 24))  # 1% monthly vol — very calm
    sw = compute_weights(mom, bot)
    assert sw.momentum >= MIN_WEIGHT, f"mom weight {sw.momentum} below MIN {MIN_WEIGHT}"
    assert sw.bottom <= MAX_WEIGHT


def test_zero_vol_falls_back_to_60_40():
    mom = pd.Series([0.0] * 12)  # zero vol
    bot = pd.Series([0.0] * 12)
    sw = compute_weights(mom, bot)
    assert sw.momentum == 0.6 and sw.bottom == 0.4
    assert sw.method == "fallback_60_40"


def test_priors_make_sense():
    # Momentum is more volatile than bottom-catch in our backtest data
    assert PRIOR_MOMENTUM_VOL_MONTHLY > PRIOR_BOTTOM_VOL_MONTHLY
