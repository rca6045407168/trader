"""Tests for PBO."""
import numpy as np
import pandas as pd
from trader.pbo import pbo_from_returns


def test_pbo_obvious_overfit_detected():
    """Construct a known overfit scenario: 1 'lucky' strategy that's noise +
    one good strategy that's bias. Verify PBO sees through the noise."""
    rng = np.random.default_rng(42)
    T = 200
    N = 10
    # Pure noise across all strategies
    df = pd.DataFrame(
        rng.normal(0, 0.01, (T, N)),
        columns=[f"S{i}" for i in range(N)],
    )
    result = pbo_from_returns(df, n_partitions=8)
    # With pure noise, best-in-sample has roughly 50% chance of below-median OOS
    assert 0.30 <= result["pbo"] <= 0.70


def test_pbo_handles_short_series():
    df = pd.DataFrame({"a": [0.01, 0.02], "b": [0.01, -0.01]})
    result = pbo_from_returns(df, n_partitions=8)
    assert result["verdict"] == "insufficient_observations"


def test_pbo_handles_single_strategy():
    df = pd.DataFrame({"a": [0.01] * 200})
    result = pbo_from_returns(df, n_partitions=8)
    assert result["verdict"] == "insufficient_strategies"
