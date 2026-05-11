"""Tests for v6 broker port round 2 — order submission paths.

Round 1 ported the read paths (account, clock, positions, last-price).
Round 2 ports the write paths (place_target_weights,
close_aged_bottom_catches, reconcile-pending-orders) and gates the
bracket-order path Alpaca-only.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# OpenOrder dataclass + protocol extension
# ============================================================
def test_broker_module_exports_open_order():
    from trader.broker import OpenOrder
    o = OpenOrder(order_id="abc", symbol="AAPL", side="buy", qty=10)
    assert o.symbol == "AAPL"
    assert o.qty == 10


def test_alpaca_adapter_has_get_open_orders(monkeypatch):
    monkeypatch.setattr("trader.broker.alpaca_adapter.ALPACA_KEY", "t")
    monkeypatch.setattr("trader.broker.alpaca_adapter.ALPACA_SECRET", "t")
    from trader.broker.alpaca_adapter import AlpacaAdapter
    with patch("alpaca.trading.client.TradingClient") as mtrading, \
         patch("alpaca.data.historical.StockHistoricalDataClient"):
        mock_order = MagicMock(
            id="ord-1", symbol="AAPL", qty=10,
            submitted_at=datetime(2026, 5, 11),
        )
        side = MagicMock(); side.value = "buy"
        mock_order.side = side
        mtrading.return_value.get_orders.return_value = [mock_order]
        adapter = AlpacaAdapter(paper=True)
        orders = adapter.get_open_orders()
        assert len(orders) == 1
        assert orders[0].symbol == "AAPL"
        assert orders[0].qty == 10
        assert orders[0].side == "buy"


def test_alpaca_adapter_get_open_orders_empty_on_error(monkeypatch):
    monkeypatch.setattr("trader.broker.alpaca_adapter.ALPACA_KEY", "t")
    monkeypatch.setattr("trader.broker.alpaca_adapter.ALPACA_SECRET", "t")
    from trader.broker.alpaca_adapter import AlpacaAdapter
    with patch("alpaca.trading.client.TradingClient") as mtrading, \
         patch("alpaca.data.historical.StockHistoricalDataClient"):
        mtrading.return_value.get_orders.side_effect = RuntimeError("api down")
        adapter = AlpacaAdapter(paper=True)
        assert adapter.get_open_orders() == []


# ============================================================
# reconcile.py dual-path
# ============================================================
def test_reconcile_pending_uses_abstraction_when_broker_name_present():
    """When client has broker_name (string) + get_open_orders,
    use the abstraction path."""
    from trader.reconcile import get_pending_orders_qty
    from trader.broker import OpenOrder

    class FakeBroker:
        broker_name = "test_broker"

        def get_open_orders(self):
            return [
                OpenOrder("ord-1", "AAPL", "buy", 10),
                OpenOrder("ord-2", "MSFT", "sell", 5),
            ]

    result = get_pending_orders_qty(FakeBroker())
    assert result == {"AAPL": 10.0, "MSFT": -5.0}


def test_reconcile_pending_falls_back_to_legacy_alpaca_for_magicmock():
    """MagicMock has every attribute but broker_name isn't a real str
    — must fall through to the Alpaca-specific GetOrdersRequest path
    (which the legacy test mocks)."""
    from trader.reconcile import get_pending_orders_qty
    client = MagicMock()
    mock_order = MagicMock(symbol="AAPL", qty=10)
    side = MagicMock(); side.value = "buy"
    mock_order.side = side
    client.get_orders.return_value = [mock_order]
    # MagicMock auto-attributes broker_name as another MagicMock,
    # NOT a str — so the abstraction branch should NOT fire
    result = get_pending_orders_qty(client)
    assert result == {"AAPL": 10.0}


# ============================================================
# execute.py order submission ports
# ============================================================
def test_place_target_weights_uses_abstraction(monkeypatch):
    """The rebalance path now goes through broker.submit_market_order."""
    from trader.broker import reset_broker_client_for_testing
    reset_broker_client_for_testing()
    monkeypatch.setenv("BROKER", "alpaca_paper")
    monkeypatch.setattr("trader.broker.alpaca_adapter.ALPACA_KEY", "t")
    monkeypatch.setattr("trader.broker.alpaca_adapter.ALPACA_SECRET", "t")
    with patch("alpaca.trading.client.TradingClient") as mtrading, \
         patch("alpaca.data.historical.StockHistoricalDataClient"):
        # Mock account, positions, and the order ack
        mtrading.return_value.get_account.return_value = MagicMock(
            equity="100000", cash="20000", buying_power="40000",
            account_number="TEST",
        )
        mtrading.return_value.get_all_positions.return_value = []
        order_ack = MagicMock(id="ord-xyz", status="submitted")
        mtrading.return_value.submit_order.return_value = order_ack

        from trader.execute import place_target_weights
        # Single target: AAPL at 5% of equity = $5000
        results = place_target_weights({"AAPL": 0.05}, min_order_usd=50.0)
        # Should have one submitted order
        submitted = [r for r in results if r["status"] == "submitted"]
        assert len(submitted) == 1
        assert submitted[0]["symbol"] == "AAPL"
        assert submitted[0]["notional"] == 5000.0
        assert submitted[0]["side"] == "buy"


def test_place_target_weights_respects_min_order_usd(monkeypatch):
    """Orders below min_order_usd are skipped."""
    from trader.broker import reset_broker_client_for_testing
    reset_broker_client_for_testing()
    monkeypatch.setenv("BROKER", "alpaca_paper")
    monkeypatch.setattr("trader.broker.alpaca_adapter.ALPACA_KEY", "t")
    monkeypatch.setattr("trader.broker.alpaca_adapter.ALPACA_SECRET", "t")
    with patch("alpaca.trading.client.TradingClient") as mtrading, \
         patch("alpaca.data.historical.StockHistoricalDataClient"):
        mtrading.return_value.get_account.return_value = MagicMock(
            equity="100000", cash="20000", buying_power="40000",
            account_number="TEST",
        )
        mtrading.return_value.get_all_positions.return_value = []
        from trader.execute import place_target_weights
        # Target 0.0003 * 100k = $30 < $50 min
        results = place_target_weights({"AAPL": 0.0003}, min_order_usd=50.0)
        below_min = [r for r in results if r["status"] == "below_min"]
        assert len(below_min) == 1


# ============================================================
# bracket-order gate
# ============================================================
def test_bracket_order_raises_on_non_alpaca_broker(monkeypatch):
    """When BROKER=public_live, bracket orders refuse (no
    bracket-OCO equivalent in public_api_sdk)."""
    monkeypatch.setenv("BROKER", "public_live")
    from trader.order_planner import OrderPlan
    from trader.execute import place_bracket_order
    plan = OrderPlan(
        symbol="AAPL", side="BUY", order_type="LIMIT",
        notional=1000, qty=None,
        limit_price=200, stop_loss_price=190, take_profit_price=220,
        trail_pct=None, time_in_force="DAY",
    )
    with pytest.raises(NotImplementedError, match="BRACKET"):
        place_bracket_order(plan, dry_run=False)


def test_bracket_order_proceeds_on_alpaca(monkeypatch):
    """When BROKER=alpaca_paper (or unset), bracket orders work as before."""
    monkeypatch.setenv("BROKER", "alpaca_paper")
    from trader.order_planner import OrderPlan
    from trader.execute import place_bracket_order
    plan = OrderPlan(
        symbol="AAPL", side="BUY", order_type="LIMIT",
        notional=1000, qty=None,
        limit_price=200, stop_loss_price=190, take_profit_price=220,
        trail_pct=None, time_in_force="DAY",
    )
    # dry_run=True short-circuits BEFORE Alpaca calls — just verify
    # no exception
    result = place_bracket_order(plan, dry_run=True)
    assert result["status"] == "dry_run"


# ============================================================
# MOC market_session
# ============================================================
def test_alpaca_submit_market_order_supports_closing_session(monkeypatch):
    """market_session="closing" → TimeInForce.CLS."""
    monkeypatch.setattr("trader.broker.alpaca_adapter.ALPACA_KEY", "t")
    monkeypatch.setattr("trader.broker.alpaca_adapter.ALPACA_SECRET", "t")
    from trader.broker.alpaca_adapter import AlpacaAdapter
    with patch("alpaca.trading.client.TradingClient") as mtrading, \
         patch("alpaca.data.historical.StockHistoricalDataClient"):
        mtrading.return_value.submit_order.return_value = MagicMock(
            id="ord-1", status="submitted",
        )
        adapter = AlpacaAdapter(paper=True)
        adapter.submit_market_order(
            "AAPL", notional=1000, side="buy", market_session="closing",
        )
        call = mtrading.return_value.submit_order.call_args
        req = call.args[0] if call.args else call.kwargs.get("order_data")
        # The request's time_in_force should be CLS
        from alpaca.trading.enums import TimeInForce
        assert req.time_in_force == TimeInForce.CLS


def test_alpaca_submit_market_order_default_session_is_day(monkeypatch):
    monkeypatch.setattr("trader.broker.alpaca_adapter.ALPACA_KEY", "t")
    monkeypatch.setattr("trader.broker.alpaca_adapter.ALPACA_SECRET", "t")
    from trader.broker.alpaca_adapter import AlpacaAdapter
    with patch("alpaca.trading.client.TradingClient") as mtrading, \
         patch("alpaca.data.historical.StockHistoricalDataClient"):
        mtrading.return_value.submit_order.return_value = MagicMock(
            id="ord-1", status="submitted",
        )
        adapter = AlpacaAdapter(paper=True)
        adapter.submit_market_order("AAPL", notional=1000, side="buy")
        call = mtrading.return_value.submit_order.call_args
        req = call.args[0] if call.args else call.kwargs.get("order_data")
        from alpaca.trading.enums import TimeInForce
        assert req.time_in_force == TimeInForce.DAY
