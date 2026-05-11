"""Tests for the v6 broker-abstraction port (round 1 — read paths).

This commit ported main.py + execute.py READ paths to use the broker
abstraction. Order submission still goes through the raw Alpaca
client (separate port pass).

Verifies:
  1. execute.py exposes get_broker() helper alongside get_client()
  2. get_last_price() routes through abstraction when BROKER != alpaca_paper
  3. main.py imports get_broker
  4. main.py kill-switch / market-open / snapshot paths use the abstraction
     (source-text verification — the actual orchestrator end-to-end
     would need full Alpaca mocking which we already do in other tests)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# execute.py exposes the new helper
# ============================================================
def test_execute_exposes_get_broker():
    from trader.execute import get_broker
    assert callable(get_broker)


def test_execute_exposes_get_client_legacy():
    """The legacy get_client() stays — bracket-order paths use raw client."""
    from trader.execute import get_client
    assert callable(get_client)


def test_execute_get_broker_returns_broker_adapter(monkeypatch):
    """get_broker() returns a BrokerAdapter, not the raw Alpaca client."""
    from trader.broker import reset_broker_client_for_testing
    reset_broker_client_for_testing()
    monkeypatch.setenv("BROKER", "alpaca_paper")
    with patch("trader.broker.alpaca_adapter.AlpacaAdapter") as m:
        mock_adapter = MagicMock()
        mock_adapter.broker_name = "alpaca_paper"
        m.return_value = mock_adapter
        from trader.execute import get_broker
        broker = get_broker()
        # broker should be the mock adapter, not the raw TradingClient
        assert broker is mock_adapter


# ============================================================
# get_last_price routing
# ============================================================
def test_get_last_price_default_uses_alpaca_data_api(monkeypatch):
    """BROKER=alpaca_paper (default) → fast path through Alpaca data API."""
    monkeypatch.setenv("BROKER", "alpaca_paper")
    # When BROKER=alpaca_paper, get_last_price doesn't call broker layer;
    # it goes through _get_data_client(). Mock that path.
    with patch("trader.execute._get_data_client") as m:
        mock_client = MagicMock()
        resp = MagicMock()
        resp.__getitem__.return_value = MagicMock(price=150.50)
        mock_client.get_stock_latest_trade.return_value = resp
        m.return_value = mock_client
        from trader.execute import get_last_price
        price = get_last_price("AAPL")
        assert price == 150.50
        # Should have called the Alpaca data path, not the broker abstraction
        mock_client.get_stock_latest_trade.assert_called_once()


def test_get_last_price_non_alpaca_uses_broker_abstraction(monkeypatch):
    """BROKER=public_live → routes through broker.get_last_price."""
    from trader.broker import reset_broker_client_for_testing
    reset_broker_client_for_testing()
    monkeypatch.setenv("BROKER", "public_live")
    with patch("trader.execute.get_broker") as m:
        mock_broker = MagicMock()
        mock_broker.get_last_price.return_value = 200.00
        m.return_value = mock_broker
        from trader.execute import get_last_price
        price = get_last_price("AAPL")
        assert price == 200.00
        mock_broker.get_last_price.assert_called_once_with("AAPL")


# ============================================================
# main.py source-text wiring
# ============================================================
def test_main_imports_get_broker():
    src = Path(__file__).resolve().parent.parent / "src" / "trader" / "main.py"
    txt = src.read_text()
    assert "get_broker" in txt
    # Original 4 sites should now reference get_broker
    assert "get_broker().get_account()" in txt
    assert "get_broker().get_clock()" in txt
    assert "broker = get_broker()" in txt


def test_main_kill_switch_equity_uses_broker():
    """The kill-switch pre-flight reads equity via broker abstraction."""
    src = Path(__file__).resolve().parent.parent / "src" / "trader" / "main.py"
    txt = src.read_text()
    # The kill-switch section should have get_broker().get_account().equity
    kill_idx = txt.find("kill-switch pre-flight")
    next_section_idx = txt.find("v6.0.x: market-open gate")
    assert kill_idx > 0 and next_section_idx > kill_idx
    section_txt = txt[kill_idx:next_section_idx]
    assert "get_broker().get_account()" in section_txt


def test_main_market_open_uses_broker_clock():
    src = Path(__file__).resolve().parent.parent / "src" / "trader" / "main.py"
    txt = src.read_text()
    gate_idx = txt.find("market-open gate")
    next_idx = txt.find("data-quality pre-flight", gate_idx)
    section_txt = txt[gate_idx:next_idx]
    assert "get_broker().get_clock()" in section_txt


def test_main_snapshot_uses_broker():
    src = Path(__file__).resolve().parent.parent / "src" / "trader" / "main.py"
    txt = src.read_text()
    snapshot_idx = txt.find("Snapshot account")
    # The snapshot block uses get_broker().get_account() + get_all_positions()
    snapshot_section = txt[snapshot_idx:snapshot_idx + 1000]
    assert "get_broker()" in snapshot_section
    assert "get_all_positions()" in snapshot_section


# ============================================================
# Behavior preservation: with BROKER=alpaca_paper, broker.get_account()
# returns an Account dataclass whose .equity matches what Alpaca returns
# ============================================================
def test_alpaca_path_account_normalizes_correctly(monkeypatch):
    """End-to-end: BROKER=alpaca_paper → get_broker().get_account() returns
    Account(equity=...) matching what Alpaca's account.equity would have."""
    monkeypatch.setattr("trader.broker.alpaca_adapter.ALPACA_KEY", "test")
    monkeypatch.setattr("trader.broker.alpaca_adapter.ALPACA_SECRET", "test")
    from trader.broker import reset_broker_client_for_testing
    reset_broker_client_for_testing()
    monkeypatch.setenv("BROKER", "alpaca_paper")
    with patch("alpaca.trading.client.TradingClient") as mtrading, \
         patch("alpaca.data.historical.StockHistoricalDataClient"):
        mock_acct = MagicMock(
            account_number="TEST",
            equity="50000.00",
            cash="10000.00",
            buying_power="20000.00",
        )
        mtrading.return_value.get_account.return_value = mock_acct
        from trader.execute import get_broker
        broker = get_broker()
        account = broker.get_account()
        assert account.equity == 50000.00
        assert account.cash == 10000.00
        # AlpacaAdapter.broker_name should be set
        assert broker.broker_name == "alpaca_paper"
