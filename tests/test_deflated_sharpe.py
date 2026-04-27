"""Tests for deflated Sharpe."""
import pytest
from trader.deflated_sharpe import deflated_sharpe_ratio


def test_dsr_probability_drops_with_more_trials():
    """DSR returns a probability — more trials of selection bias → lower probability of real edge."""
    obs = 1.50
    dsr_few, _ = deflated_sharpe_ratio(obs, n_observations=120, skew=0, kurt_excess=0, n_trials=1)
    dsr_many, _ = deflated_sharpe_ratio(obs, n_observations=120, skew=0, kurt_excess=0, n_trials=100)
    assert dsr_few > dsr_many, f"few={dsr_few} should be > many={dsr_many}"


def test_dsr_probability_in_unit_interval():
    obs = 1.50
    dsr, p = deflated_sharpe_ratio(obs, n_observations=120, skew=0, kurt_excess=0, n_trials=10)
    assert 0.0 <= dsr <= 1.0
    assert 0.0 <= p <= 1.0
    assert abs(dsr + p - 1.0) < 1e-9, f"DSR + p should sum to 1, got {dsr + p}"


def test_dsr_handles_short_series():
    d, p = deflated_sharpe_ratio(1.0, n_observations=10, skew=0, kurt_excess=0, n_trials=5)
    import math
    assert math.isnan(d) and math.isnan(p)
