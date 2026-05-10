"""Tests for trader.insider_signal — Cohen-Malloy-Pomorski.

Network is mocked. The yfinance dependency is patched via a tiny
fake module that returns canned per-ticker DataFrames in the same
shape that real yfinance does."""
from __future__ import annotations

import os
import sys
import time
import types
from pathlib import Path

import pandas as pd
import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


def _fake_yf(insider_data: dict[str, dict | None]) -> types.ModuleType:
    """Build a fake `yfinance`-like module.

    insider_data: {ticker: {"net_shares": float, "pct": float} | None}
    None means the Ticker's insider_purchases is empty.
    """
    class _FakeTicker:
        def __init__(self, sym):
            self.sym = sym
        @property
        def insider_purchases(self):
            d = insider_data.get(self.sym)
            if d is None:
                return pd.DataFrame()
            return pd.DataFrame({
                "Insider Purchases Last 6m": [
                    "Purchases",
                    "Sales",
                    "Net Shares Purchased (Sold)",
                    "Total Insider Shares Held",
                    "% Net Shares Purchased (Sold)",
                    "% Buy Shares",
                    "% Sell Shares",
                ],
                "Shares": [
                    abs(d["net_shares"]) + 100,
                    100,
                    d["net_shares"],
                    1_000_000,
                    d["pct"],
                    0.5,
                    0.5,
                ],
                "Trans": [10, 4, 14, pd.NA, pd.NA, pd.NA, pd.NA],
            })

    mod = types.ModuleType("yfinance_fake")
    mod.Ticker = _FakeTicker
    return mod


# ============================================================
# _fetch_one
# ============================================================
def test_fetch_one_success():
    from trader.insider_signal import _fetch_one
    yf = _fake_yf({"AAPL": {"net_shares": 246332, "pct": 0.001}})
    out = _fetch_one("AAPL", yf_module=yf)
    assert out is not None
    assert out["net_shares"] == 246332
    assert out["score"] == 0.001


def test_fetch_one_empty_returns_none():
    from trader.insider_signal import _fetch_one
    yf = _fake_yf({"DEAD": None})
    assert _fetch_one("DEAD", yf_module=yf) is None


def test_fetch_one_handles_exception_silently():
    from trader.insider_signal import _fetch_one
    yf = types.ModuleType("broken")

    class _BrokenTicker:
        def __init__(self, sym): pass
        @property
        def insider_purchases(self):
            raise RuntimeError("API timeout")

    yf.Ticker = _BrokenTicker
    # Should NOT raise — defensive design
    assert _fetch_one("AAPL", yf_module=yf) is None


# ============================================================
# insider_scores
# ============================================================
def test_insider_scores_populates_dict(tmp_path):
    from trader.insider_signal import insider_scores
    cache = tmp_path / "cache.parquet"
    yf = _fake_yf({
        "AAPL": {"net_shares": 100, "pct": 0.001},
        "XOM":  {"net_shares": 1_000_000, "pct": 0.409},
        "MSFT": {"net_shares": -14000, "pct": -0.002},
    })
    out = insider_scores(["AAPL", "XOM", "MSFT"], yf_module=yf,
                          cache_path=cache)
    assert out["AAPL"] == pytest.approx(0.001)
    assert out["XOM"] == pytest.approx(0.409)
    assert out["MSFT"] == pytest.approx(-0.002)


def test_insider_scores_skips_failed_tickers(tmp_path):
    from trader.insider_signal import insider_scores
    cache = tmp_path / "cache.parquet"
    yf = _fake_yf({
        "AAPL": {"net_shares": 100, "pct": 0.001},
        "DEAD": None,  # empty df
    })
    out = insider_scores(["AAPL", "DEAD"], yf_module=yf, cache_path=cache)
    assert "AAPL" in out
    assert "DEAD" not in out


def test_insider_scores_uses_cache_within_ttl(tmp_path, monkeypatch):
    from trader.insider_signal import insider_scores
    cache = tmp_path / "cache.parquet"
    call_count = {"n": 0}

    class _CountingTicker:
        def __init__(self, sym):
            self.sym = sym
        @property
        def insider_purchases(self):
            call_count["n"] += 1
            return pd.DataFrame({
                "Insider Purchases Last 6m": [
                    "Net Shares Purchased (Sold)",
                    "% Net Shares Purchased (Sold)",
                ],
                "Shares": [100.0, 0.05],
            })

    yf = types.ModuleType("counter")
    yf.Ticker = _CountingTicker

    # First call — should hit yfinance
    insider_scores(["AAPL"], yf_module=yf, cache_path=cache,
                     cache_ttl_hours=1.0)
    first = call_count["n"]
    # Second call within TTL — should NOT hit yfinance
    insider_scores(["AAPL"], yf_module=yf, cache_path=cache,
                     cache_ttl_hours=1.0)
    assert call_count["n"] == first, "cache should have served the 2nd call"


def test_insider_scores_refreshes_when_ttl_expired(tmp_path):
    from trader.insider_signal import insider_scores
    cache = tmp_path / "cache.parquet"
    call_count = {"n": 0}

    class _CountingTicker:
        def __init__(self, sym): pass
        @property
        def insider_purchases(self):
            call_count["n"] += 1
            return pd.DataFrame({
                "Insider Purchases Last 6m": [
                    "Net Shares Purchased (Sold)",
                    "% Net Shares Purchased (Sold)",
                ],
                "Shares": [100.0, 0.05],
            })

    yf = types.ModuleType("counter2")
    yf.Ticker = _CountingTicker

    # Cache TTL = 0 → every call should re-fetch
    insider_scores(["AAPL"], yf_module=yf, cache_path=cache,
                     cache_ttl_hours=0)
    n1 = call_count["n"]
    insider_scores(["AAPL"], yf_module=yf, cache_path=cache,
                     cache_ttl_hours=0)
    assert call_count["n"] > n1


# ============================================================
# top_n_by_insider
# ============================================================
def test_top_n_filters_by_min_score(tmp_path):
    from trader.insider_signal import top_n_by_insider
    cache = tmp_path / "cache.parquet"
    yf = _fake_yf({
        "BUY1": {"net_shares": 1000, "pct": 0.10},
        "BUY2": {"net_shares": 500, "pct": 0.05},
        "FLAT": {"net_shares": 0, "pct": 0.0},
        "SELL": {"net_shares": -1000, "pct": -0.10},
    })
    # min_score=0.0 → exclude SELL, include FLAT
    picks = top_n_by_insider(
        ["BUY1", "BUY2", "FLAT", "SELL"],
        n=10, min_score=0.0, yf_module=yf, cache_path=cache,
    )
    tickers = [t for t, _ in picks]
    assert "BUY1" in tickers
    assert "BUY2" in tickers
    assert "SELL" not in tickers


def test_top_n_sorted_descending_by_score(tmp_path):
    from trader.insider_signal import top_n_by_insider
    cache = tmp_path / "cache.parquet"
    yf = _fake_yf({
        "A": {"net_shares": 100, "pct": 0.01},
        "B": {"net_shares": 500, "pct": 0.05},
        "C": {"net_shares": 100, "pct": 0.10},
    })
    picks = top_n_by_insider(["A", "B", "C"], n=10,
                                yf_module=yf, cache_path=cache)
    scores = [s for _, s in picks]
    assert scores == sorted(scores, reverse=True)
    assert picks[0][0] == "C"  # 0.10 highest


def test_top_n_respects_n(tmp_path):
    from trader.insider_signal import top_n_by_insider
    cache = tmp_path / "cache.parquet"
    yf = _fake_yf({
        f"T{i}": {"net_shares": 100, "pct": float(i) / 100}
        for i in range(20)
    })
    picks = top_n_by_insider([f"T{i}" for i in range(20)], n=5,
                                yf_module=yf, cache_path=cache)
    assert len(picks) == 5


# ============================================================
# Strategy registration
# ============================================================
def test_insider_strategy_in_registry():
    from trader import eval_strategies
    names = {s.name for s in eval_strategies.all_strategies()}
    assert "xs_top10_insider_buy" in names


def test_insider_strategy_returns_empty_on_historical_asof():
    """Walk-forward guard: historical asof returns {} (no leak-forward)."""
    from trader.eval_strategies import xs_top10_insider_buy
    import numpy as np
    dates = pd.bdate_range("2020-01-01", periods=400)
    prices = pd.DataFrame(
        100 * np.cumprod(1 + np.random.RandomState(0).randn(400, 5) * 0.01, axis=0),
        index=dates, columns=["AAPL", "MSFT", "JPM", "XOM", "GOOGL"],
    )
    # asof in 2020 (years ago) → strategy must NOT call yfinance
    result = xs_top10_insider_buy(dates[-1] - pd.Timedelta(days=365 * 5), prices)
    assert result == {}


def test_insider_strategy_handles_empty_universe():
    from trader.eval_strategies import xs_top10_insider_buy
    prices = pd.DataFrame()
    result = xs_top10_insider_buy(pd.Timestamp.today(), prices)
    assert result == {}
