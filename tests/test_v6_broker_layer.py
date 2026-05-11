"""Tests for v6.0.x broker abstraction layer.

Coverage:
  - Module imports + factory dispatch (alpaca_paper / public_live / unknown)
  - AlpacaAdapter contract on mocked client
  - PublicAdapter contract on mocked SDK
  - NYSE clock helper (weekday + weekend behavior)
  - go_live_gate gate functions on synthetic journals

Real SDKs aren't exercised in CI — we mock them at the seam.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

# public_api_sdk isn't a public pip package — it's installed via the
# institutional access path. CI doesn't have it. Skip those tests
# there.
public_sdk_available = True
try:
    import public_api_sdk  # noqa: F401
except ImportError:
    public_sdk_available = False
requires_public_sdk = pytest.mark.skipif(
    not public_sdk_available,
    reason="public_api_sdk not installed (institutional access only)",
)


# ============================================================
# Module structure
# ============================================================
def test_broker_module_exposes_factory():
    from trader.broker import (
        get_broker_client, BrokerAdapter, Account, Clock,
        Position, OrderRecord, reset_broker_client_for_testing,
    )
    assert callable(get_broker_client)


def test_factory_dispatch_alpaca(monkeypatch):
    from trader.broker import (
        get_broker_client, reset_broker_client_for_testing,
    )
    reset_broker_client_for_testing()
    monkeypatch.setenv("BROKER", "alpaca_paper")
    # Mock the AlpacaAdapter to avoid hitting Alpaca
    with patch("trader.broker.alpaca_adapter.AlpacaAdapter") as m:
        m.return_value.broker_name = "alpaca_paper"
        client = get_broker_client()
        m.assert_called_once_with(paper=True)


def test_factory_dispatch_unknown_raises(monkeypatch):
    from trader.broker import (
        get_broker_client, reset_broker_client_for_testing,
    )
    reset_broker_client_for_testing()
    monkeypatch.setenv("BROKER", "wells_fargo")
    with pytest.raises(ValueError, match="Unknown BROKER"):
        get_broker_client()


def test_factory_singleton_caches(monkeypatch):
    from trader.broker import (
        get_broker_client, reset_broker_client_for_testing,
    )
    reset_broker_client_for_testing()
    monkeypatch.setenv("BROKER", "alpaca_paper")
    with patch("trader.broker.alpaca_adapter.AlpacaAdapter") as m:
        m.return_value.broker_name = "alpaca_paper"
        c1 = get_broker_client()
        c2 = get_broker_client()
        assert c1 is c2
        assert m.call_count == 1


# ============================================================
# AlpacaAdapter
# ============================================================
def test_alpaca_adapter_get_account(monkeypatch):
    monkeypatch.setattr("trader.broker.alpaca_adapter.ALPACA_KEY", "test-key")
    monkeypatch.setattr("trader.broker.alpaca_adapter.ALPACA_SECRET", "test-sec")
    from trader.broker.alpaca_adapter import AlpacaAdapter
    with patch("alpaca.trading.client.TradingClient") as mtrading, \
         patch("alpaca.data.historical.StockHistoricalDataClient"):
        instance = mtrading.return_value
        instance.get_account.return_value = MagicMock(
            account_number="ABC123",
            equity="105000.50",
            cash="20000",
            buying_power="40000",
        )
        adapter = AlpacaAdapter(paper=True)
        a = adapter.get_account()
        assert a.equity == 105000.50
        assert a.cash == 20000
        assert a.account_id == "ABC123"


def test_alpaca_adapter_get_clock(monkeypatch):
    monkeypatch.setattr("trader.broker.alpaca_adapter.ALPACA_KEY", "test-key")
    monkeypatch.setattr("trader.broker.alpaca_adapter.ALPACA_SECRET", "test-sec")
    from trader.broker.alpaca_adapter import AlpacaAdapter
    with patch("alpaca.trading.client.TradingClient") as mtrading, \
         patch("alpaca.data.historical.StockHistoricalDataClient"):
        instance = mtrading.return_value
        instance.get_clock.return_value = MagicMock(
            is_open=True,
            next_open=datetime(2026, 5, 11, 13, 30),
            next_close=datetime(2026, 5, 10, 20, 0),
        )
        adapter = AlpacaAdapter(paper=True)
        c = adapter.get_clock()
        assert c.is_open is True


# ============================================================
# PublicAdapter — NYSE clock helper (no SDK needed)
# ============================================================
def test_public_nyse_open_during_weekday_hours():
    from trader.broker.public_adapter import _is_nyse_open
    # Mon 2026-05-11 18:00 UTC = 2 PM EDT — market open
    is_open, _, _ = _is_nyse_open(datetime(2026, 5, 11, 18, 0))
    assert is_open is True


def test_public_nyse_closed_on_sunday():
    from trader.broker.public_adapter import _is_nyse_open
    is_open, next_open, _ = _is_nyse_open(datetime(2026, 5, 10, 18, 0))
    assert is_open is False
    # Next open should be Monday
    assert next_open.weekday() == 0  # Monday


def test_public_nyse_closed_after_4pm():
    from trader.broker.public_adapter import _is_nyse_open
    # Mon 21:00 UTC = 5 PM EDT — market closed
    is_open, next_open, _ = _is_nyse_open(datetime(2026, 5, 11, 21, 0))
    assert is_open is False


def test_public_nyse_closed_before_930():
    from trader.broker.public_adapter import _is_nyse_open
    # Mon 12:00 UTC = 8 AM EDT — market not yet open
    is_open, _, _ = _is_nyse_open(datetime(2026, 5, 11, 12, 0))
    assert is_open is False


# ============================================================
# PublicAdapter — mocked SDK
# ============================================================
@requires_public_sdk
def test_public_adapter_get_account(monkeypatch):
    monkeypatch.setenv("PUBLIC_API_SECRET", "test-key")
    monkeypatch.setenv("PUBLIC_ACCOUNT_NUMBER", "TEST_ACCT")
    with patch("public_api_sdk.PublicApiClient") as mclient:
        from trader.broker.public_adapter import PublicAdapter
        # Build a mocked portfolio response
        portfolio = MagicMock()
        portfolio.equity = [
            MagicMock(value=50000),
            MagicMock(value=30000),
        ]
        portfolio.buyingPower = MagicMock(
            cashOnlyBuyingPower=10000,
            buyingPower=20000,
        )
        portfolio.positions = []
        mclient.return_value.get_portfolio.return_value = portfolio

        adapter = PublicAdapter()
        a = adapter.get_account()
        assert a.equity == 80000
        assert a.cash == 10000
        assert a.buying_power == 20000


@requires_public_sdk
def test_public_adapter_missing_creds_raises(monkeypatch):
    monkeypatch.delenv("PUBLIC_API_SECRET", raising=False)
    monkeypatch.delenv("PUBLIC_ACCOUNT_NUMBER", raising=False)
    with patch("public_api_sdk.PublicApiClient"):
        from trader.broker.public_adapter import PublicAdapter
        with pytest.raises(RuntimeError, match="PUBLIC_ACCOUNT_NUMBER missing"):
            PublicAdapter()


@requires_public_sdk
def test_public_adapter_get_all_positions(monkeypatch):
    monkeypatch.setenv("PUBLIC_API_SECRET", "test-key")
    monkeypatch.setenv("PUBLIC_ACCOUNT_NUMBER", "TEST_ACCT")
    with patch("public_api_sdk.PublicApiClient") as mclient:
        from trader.broker.public_adapter import PublicAdapter
        portfolio = MagicMock()
        portfolio.equity = []
        portfolio.buyingPower = MagicMock(
            cashOnlyBuyingPower=0, buyingPower=0,
        )
        pos = MagicMock()
        pos.instrument = MagicMock(symbol="AAPL")
        pos.quantity = 100
        pos.currentValue = 21000
        pos.lastPrice = MagicMock(value=210)
        pos.costBasis = MagicMock(value=18000)
        pos.instrumentGain = MagicMock(absoluteGain=3000, percentageGain=0.1667)
        pos.positionDailyGain = MagicMock(absoluteGain=200, percentageGain=0.0095)
        portfolio.positions = [pos]
        mclient.return_value.get_portfolio.return_value = portfolio

        adapter = PublicAdapter()
        positions = adapter.get_all_positions()
        assert len(positions) == 1
        assert positions[0].symbol == "AAPL"
        assert positions[0].qty == 100
        assert positions[0].market_value == 21000
        assert abs(positions[0].avg_entry_price - 180) < 1e-6


# ============================================================
# go_live_gate functions
# ============================================================
def test_gate_credentials_pass(monkeypatch):
    monkeypatch.setenv("PUBLIC_API_SECRET", "abcdef1234567890")
    monkeypatch.setenv("PUBLIC_ACCOUNT_NUMBER", "ACCT1234")
    from go_live_gate import gate_1_credentials
    passed, msg = gate_1_credentials()
    assert passed is True
    assert "1234" in msg


def test_gate_credentials_missing(monkeypatch):
    monkeypatch.delenv("PUBLIC_API_SECRET", raising=False)
    monkeypatch.delenv("PUBLIC_ACCOUNT_NUMBER", raising=False)
    from go_live_gate import gate_1_credentials
    passed, msg = gate_1_credentials()
    assert passed is False


def test_gate_cost_basis_method(monkeypatch):
    from go_live_gate import gate_4_cost_basis_method
    monkeypatch.setenv("PUBLIC_COST_BASIS_METHOD", "SPECIFIC_ID")
    assert gate_4_cost_basis_method()[0] is True
    monkeypatch.setenv("PUBLIC_COST_BASIS_METHOD", "FIFO")
    assert gate_4_cost_basis_method()[0] is False
    monkeypatch.delenv("PUBLIC_COST_BASIS_METHOD")
    assert gate_4_cost_basis_method()[0] is False


def test_gate_no_drift_passes_on_clean_journal(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT, "
        "completed_at TEXT, status TEXT, notes TEXT)"
    )
    con.execute(
        "INSERT INTO runs VALUES ('r1', ?, ?, 'completed', 'clean')",
        (datetime.utcnow().isoformat(),
         datetime.utcnow().isoformat()),
    )
    con.commit()
    con.close()
    monkeypatch.setattr("trader.config.DB_PATH", db)
    # Force re-import so the gate sees the patched DB_PATH
    import importlib, go_live_gate
    importlib.reload(go_live_gate)
    passed, msg = go_live_gate.gate_8_no_recent_drift()
    assert passed is True


def test_gate_tlh_proof_fails_without_realized_losses(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE position_lots (id INTEGER PRIMARY KEY, symbol TEXT, "
        "sleeve TEXT, opened_at TEXT, qty REAL, open_price REAL, "
        "open_order_id TEXT, closed_at TEXT, close_price REAL, "
        "close_order_id TEXT, realized_pnl REAL)"
    )
    con.commit()
    con.close()
    monkeypatch.setattr("trader.config.DB_PATH", db)
    import importlib, go_live_gate
    importlib.reload(go_live_gate)
    passed, msg = go_live_gate.gate_7_tlh_proof()
    assert passed is False
    assert "no realized-loss" in msg


def test_gate_tlh_proof_passes_with_realized_losses(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE position_lots (id INTEGER PRIMARY KEY, symbol TEXT, "
        "sleeve TEXT, opened_at TEXT, qty REAL, open_price REAL, "
        "open_order_id TEXT, closed_at TEXT, close_price REAL, "
        "close_order_id TEXT, realized_pnl REAL)"
    )
    con.execute(
        "INSERT INTO position_lots (symbol, sleeve, opened_at, qty, "
        "open_price, closed_at, close_price, realized_pnl) VALUES "
        "('AAPL', 'M', '2026-01-01', 10, 200, '2026-02-01', 180, -200)",
    )
    con.commit()
    con.close()
    monkeypatch.setattr("trader.config.DB_PATH", db)
    import importlib, go_live_gate
    importlib.reload(go_live_gate)
    passed, msg = go_live_gate.gate_7_tlh_proof()
    assert passed is True
