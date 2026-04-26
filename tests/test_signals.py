"""Tests for signal correctness. Each signal has a deterministic expected output
on synthetic data — if any drifts, the test fails before live trading does."""
import numpy as np
import pandas as pd
import pytest

from trader.signals import (
    momentum_score, rsi, bollinger_z, trend_intact, volume_spike,
    atr, bottom_catch_score,
)


def _series(values):
    idx = pd.date_range("2020-01-01", periods=len(values), freq="B")
    return pd.Series(values, index=idx)


def test_momentum_positive_uptrend():
    # 252 days of steady 0.1% daily gain
    prices = _series([100 * (1.001 ** i) for i in range(252)])
    score = momentum_score(prices, lookback_months=6, skip_months=1)
    assert score > 0.10, f"Steady uptrend should score >10% momentum, got {score}"


def test_momentum_negative_downtrend():
    prices = _series([100 * (0.999 ** i) for i in range(252)])
    score = momentum_score(prices, lookback_months=6, skip_months=1)
    assert score < -0.05


def test_rsi_oversold():
    # 30 days of monotone decline
    prices = _series([100 - i for i in range(30)])
    val = rsi(prices)
    assert val < 35, f"Monotone decline should be oversold, got RSI={val}"


def test_rsi_overbought():
    prices = _series([100 + i for i in range(30)])
    val = rsi(prices)
    assert val > 65


def test_bollinger_z_below_band():
    # 19 days at 100, then a crash to 80
    prices = _series([100] * 19 + [80])
    z = bollinger_z(prices, window=20)
    assert z < -2.0, f"Crash should be >2 sigma below mean, got z={z}"


def test_trend_intact_uptrend():
    prices = _series([100 + i * 0.1 for i in range(250)])
    assert trend_intact(prices) is True


def test_trend_intact_downtrend():
    prices = _series([100 - i * 0.1 for i in range(250)])
    assert trend_intact(prices) is False


def test_volume_spike_true():
    vol = _series([1_000_000] * 19 + [3_000_000])
    assert volume_spike(vol) is True


def test_volume_spike_false():
    vol = _series([1_000_000] * 20)
    assert volume_spike(vol) is False


def test_atr_basic():
    n = 30
    rng = np.random.default_rng(42)
    closes = 100 + rng.normal(0, 1, n).cumsum()
    high = closes + rng.uniform(0.5, 1.5, n)
    low = closes - rng.uniform(0.5, 1.5, n)
    df = pd.DataFrame({"High": high, "Low": low, "Close": closes},
                      index=pd.date_range("2020-01-01", periods=n, freq="B"))
    val = atr(df)
    assert val > 0


def test_bottom_catch_high_score_on_oversold():
    # 220 days uptrend, 28 days flat, then a 4-day crash on heavy volume
    closes = list(np.linspace(50, 100, 220)) + [100] * 28 + [98, 95, 90, 85]
    n = len(closes)
    high = [c * 1.005 for c in closes]
    low = [c * 0.995 for c in closes]
    vol = [1_000_000] * (n - 1) + [3_500_000]
    df = pd.DataFrame(
        {"High": high, "Low": low, "Close": closes, "Volume": vol},
        index=pd.date_range("2020-01-01", periods=n, freq="B"),
    )
    score, comp = bottom_catch_score(df)
    assert score >= 0.45, f"Crash-after-uptrend should fire bottom catch, got {score}, comp={comp}"
