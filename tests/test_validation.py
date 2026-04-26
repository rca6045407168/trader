"""Unit tests for the data validation layer."""
import pandas as pd
import numpy as np
import pytest

from trader.validation import validate_prices, validate_targets, DataQualityError


def _good_prices(n=300):
    # End today so the staleness check passes regardless of when the test runs
    end = pd.Timestamp.today().normalize()
    idx = pd.bdate_range(end=end, periods=n)
    rng = np.random.default_rng(0)
    actual_n = len(idx)
    df = pd.DataFrame({
        "AAPL": 180 + rng.normal(0, 0.5, actual_n).cumsum(),
        "MSFT": 400 + rng.normal(0, 1.0, actual_n).cumsum(),
    }, index=idx)
    return df


def test_validate_prices_happy_path():
    df = _good_prices(300)
    rep = validate_prices(df)
    assert rep["warnings"] == []
    assert rep["n_tickers"] == 2


def test_validate_prices_empty_raises():
    with pytest.raises(DataQualityError):
        validate_prices(pd.DataFrame())


def test_validate_prices_short_history_raises():
    df = _good_prices(50)
    with pytest.raises(DataQualityError):
        validate_prices(df, min_history_days=252)


def test_validate_prices_split_warning():
    df = _good_prices(300)
    df.iloc[150, 0] = df.iloc[149, 0] * 0.5  # fake 50% split-style drop
    rep = validate_prices(df)
    assert any("AAPL" in w and "single-day" in w for w in rep["warnings"])


def test_validate_targets_happy():
    rep = validate_targets({"AAPL": 0.10, "MSFT": 0.10})
    assert rep["ok"]
    assert rep["total"] == 0.20


def test_validate_targets_overleveraged_raises():
    with pytest.raises(DataQualityError):
        validate_targets({"AAPL": 0.6, "MSFT": 0.6})


def test_validate_targets_negative_raises():
    with pytest.raises(DataQualityError):
        validate_targets({"AAPL": -0.1})


def test_validate_targets_concentration_warning():
    rep = validate_targets({"AAPL": 0.25})
    assert any("concentration" in w.lower() for w in rep["warnings"])
