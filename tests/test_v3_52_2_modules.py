"""Tests for v3.52.2: reconcile pending-order awareness fix."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _mock_alpaca_client(positions: dict[str, float], pending_buys: dict[str, float] = None):
    """Create a mock Alpaca trading client with given positions + pending orders."""
    pending_buys = pending_buys or {}
    client = MagicMock()
    # Mock positions: list of objects with .symbol, .qty
    pos_objs = []
    for sym, qty in positions.items():
        p = MagicMock()
        p.symbol = sym
        p.qty = qty
        p.market_value = qty * 100  # dummy
        pos_objs.append(p)
    client.get_all_positions.return_value = pos_objs

    # Mock get_orders: list of order objects with .symbol, .qty, .side
    order_objs = []
    for sym, qty in pending_buys.items():
        o = MagicMock()
        o.symbol = sym
        o.qty = qty
        side = MagicMock()
        side.value = "buy"
        o.side = side
        order_objs.append(o)
    client.get_orders.return_value = order_objs
    return client


def test_reconcile_treats_pending_buy_as_awaiting_fill(monkeypatch):
    """Journal has lot for AAPL, Alpaca has no position but has pending BUY —
    should land in 'awaiting_fill', NOT 'missing'."""
    from trader import reconcile as recon
    monkeypatch.setattr(recon, "get_expected_positions_qty",
                        lambda: {"AAPL": 10.0})
    client = _mock_alpaca_client(positions={}, pending_buys={"AAPL": 10.0})
    rep = recon.reconcile(client)
    assert len(rep["awaiting_fill"]) == 1
    assert len(rep["missing"]) == 0
    assert rep["awaiting_fill"][0]["symbol"] == "AAPL"
    assert not rep["halt_recommended"]


def test_reconcile_still_halts_on_real_orphan_with_no_pending(monkeypatch):
    """Journal has 2 lots, Alpaca has neither, NO pending orders — real bug."""
    from trader import reconcile as recon
    monkeypatch.setattr(recon, "get_expected_positions_qty",
                        lambda: {"AAPL": 10.0, "NVDA": 5.0})
    client = _mock_alpaca_client(positions={}, pending_buys={})
    rep = recon.reconcile(client)
    assert len(rep["missing"]) == 2
    assert len(rep["awaiting_fill"]) == 0
    assert rep["halt_recommended"] is True


def test_reconcile_partial_fill_recognized():
    """Journal expects 10, Alpaca has 6, pending BUY is 4 — should be
    'awaiting_fill', NOT size_mismatch."""
    from trader import reconcile as recon
    with patch.object(recon, "get_expected_positions_qty",
                       return_value={"AAPL": 10.0}):
        client = _mock_alpaca_client(positions={"AAPL": 6.0},
                                       pending_buys={"AAPL": 4.0})
        rep = recon.reconcile(client)
        assert len(rep["awaiting_fill"]) == 1
        assert len(rep["size_mismatch"]) == 0


def test_reconcile_clean_when_all_match():
    """Journal lot matches Alpaca position exactly — no halts."""
    from trader import reconcile as recon
    with patch.object(recon, "get_expected_positions_qty",
                       return_value={"AAPL": 10.0}):
        client = _mock_alpaca_client(positions={"AAPL": 10.0})
        rep = recon.reconcile(client)
        assert len(rep["matched"]) == 1
        assert not rep["halt_recommended"]


def test_reconcile_summary_includes_awaiting_fill():
    """The summary string should include the new awaiting_fill count."""
    from trader import reconcile as recon
    with patch.object(recon, "get_expected_positions_qty",
                       return_value={"AAPL": 10.0}):
        client = _mock_alpaca_client(positions={}, pending_buys={"AAPL": 10.0})
        rep = recon.reconcile(client)
        assert "awaiting_fill=" in rep["summary"]
