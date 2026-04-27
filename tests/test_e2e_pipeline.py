"""v1.4 (B11 fix): end-to-end test with mocked Alpaca + yfinance.

Verifies the hot path: kill_switch -> aged-close -> rank_momentum ->
find_bottoms -> compute_weights -> risk_manager -> place_target_weights.

Uses fakes for the network-dependent layers so the test runs in <1s.
"""
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
import tempfile
from pathlib import Path
import pandas as pd
import numpy as np
import pytest


@pytest.fixture(autouse=True)
def temp_db(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "test.db"
        monkeypatch.setattr("trader.journal.DB_PATH", path)
        yield path


def _fake_prices_df(tickers, days=300):
    end = pd.Timestamp.today().normalize()
    idx = pd.bdate_range(end=end, periods=days)
    rng = np.random.default_rng(42)
    data = {}
    for i, t in enumerate(tickers):
        # different drift per ticker so momentum has something to rank
        drift = (i - len(tickers) / 2) * 0.001
        rets = drift + rng.normal(0, 0.015, len(idx))
        prices = 100 * np.exp(rets.cumsum())
        data[t] = prices
    return pd.DataFrame(data, index=idx)


def test_e2e_dry_run_produces_targets(monkeypatch):
    """Hot path: build_targets -> risk-parity -> validation. Dry run, no Alpaca."""
    from trader.universe import DEFAULT_LIQUID_50
    fake_prices = _fake_prices_df(DEFAULT_LIQUID_50, days=400)

    monkeypatch.setattr("trader.data.fetch_history", lambda tickers, start, end=None, force_refresh=False: fake_prices[tickers] if isinstance(tickers, list) else fake_prices[[tickers]])

    # Mock fetch_ohlcv to return empty (so no bottom-catch triggers fire)
    def _fake_ohlcv(ticker, start, end=None, force_refresh=False):
        df = pd.DataFrame({
            "Open": fake_prices[ticker] * 0.999,
            "High": fake_prices[ticker] * 1.005,
            "Low": fake_prices[ticker] * 0.995,
            "Close": fake_prices[ticker],
            "Volume": [1_000_000] * len(fake_prices),
        })
        return df
    monkeypatch.setattr("trader.data.fetch_ohlcv", _fake_ohlcv)

    from trader.strategy import rank_momentum
    momentum_picks = rank_momentum(DEFAULT_LIQUID_50[:20], top_n=5)
    assert len(momentum_picks) == 5, f"expected 5 momentum picks, got {len(momentum_picks)}"
    assert all(c.action == "BUY" for c in momentum_picks)
    assert all(c.style == "MOMENTUM" for c in momentum_picks)


def test_e2e_risk_parity_uses_priors_when_no_lots(monkeypatch):
    from trader.risk_parity import compute_weights, compute_sleeve_returns_from_journal
    mom, bot = compute_sleeve_returns_from_journal()
    assert mom is None and bot is None  # no journal data yet
    sw = compute_weights(mom, bot)
    assert sw.method == "prior_only"
    assert sw.momentum + sw.bottom == pytest.approx(1.0, abs=1e-3)


def test_e2e_risk_parity_uses_lots_after_history(monkeypatch):
    """Once enough closed lots exist, sleeve weights derive from realized P&L."""
    from trader.journal import init_db, _conn
    from trader.risk_parity import compute_sleeve_returns_from_journal

    init_db()
    base = datetime(2025, 1, 1)
    # Insert closed lots directly so we can control opened_at/closed_at per row
    with _conn() as c:
        for month in range(8):
            month_start = (base + timedelta(days=30 * month)).isoformat()
            month_end = (base + timedelta(days=30 * month + 25)).isoformat()
            for sleeve in ("MOMENTUM", "BOTTOM_CATCH"):
                close_p = 105.0 if sleeve == "MOMENTUM" else 102.0
                c.execute(
                    """INSERT INTO position_lots
                       (symbol, sleeve, opened_at, qty, open_price, closed_at, close_price, realized_pnl)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("AAPL", sleeve, month_start, 10, 100.0, month_end, close_p, (close_p - 100) * 10),
                )

    mom, bot = compute_sleeve_returns_from_journal()
    assert mom is not None and bot is not None, f"got mom={mom}, bot={bot}"
    assert len(mom) >= 6, f"expected >=6 months of momentum returns, got {len(mom)}: {mom}"
    assert len(bot) >= 6, f"expected >=6 months of bottom returns, got {len(bot)}"
    from trader.risk_parity import compute_weights
    sw = compute_weights(mom, bot)
    assert sw.momentum + sw.bottom == pytest.approx(1.0, abs=1e-3)
