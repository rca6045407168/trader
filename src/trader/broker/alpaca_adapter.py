"""Alpaca adapter — wraps alpaca.trading.client.TradingClient.

Preserves the existing trader behavior. Default-instantiated by
get_broker_client() when BROKER=alpaca_paper or BROKER=alpaca_live.

The trader's pre-v6 code called Alpaca's client directly; this
adapter is the indirection layer that lets the same callers point
at Public.com without rewriting the call sites.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..config import ALPACA_KEY, ALPACA_SECRET
from .base import Account, BrokerAdapter, Clock, OrderRecord, Position


class AlpacaAdapter(BrokerAdapter):
    broker_name = "alpaca"

    def __init__(self, paper: bool = True):
        if not ALPACA_KEY or not ALPACA_SECRET:
            raise RuntimeError(
                "Alpaca keys missing. Set ALPACA_API_KEY and "
                "ALPACA_API_SECRET in .env."
            )
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical import StockHistoricalDataClient
        self.paper = paper
        self.broker_name = "alpaca_paper" if paper else "alpaca_live"
        self._trading = TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=paper)
        self._data = StockHistoricalDataClient(ALPACA_KEY, ALPACA_SECRET)

    def get_account(self) -> Account:
        a = self._trading.get_account()
        return Account(
            account_id=str(getattr(a, "account_number", "alpaca")),
            equity=float(a.equity),
            cash=float(a.cash),
            buying_power=float(a.buying_power),
            currency=str(getattr(a, "currency", "USD")),
        )

    def get_clock(self) -> Clock:
        c = self._trading.get_clock()
        return Clock(
            is_open=bool(c.is_open),
            next_open=c.next_open if hasattr(c, "next_open") else None,
            next_close=c.next_close if hasattr(c, "next_close") else None,
        )

    def get_all_positions(self) -> list[Position]:
        out = []
        for p in self._trading.get_all_positions():
            out.append(Position(
                symbol=p.symbol,
                qty=float(p.qty),
                avg_entry_price=float(getattr(p, "avg_entry_price", 0) or 0),
                market_value=float(p.market_value or 0),
                current_price=float(getattr(p, "current_price", 0) or 0),
                unrealized_pl=float(getattr(p, "unrealized_pl", 0) or 0),
                unrealized_plpc=float(getattr(p, "unrealized_plpc", 0) or 0),
                side=str(getattr(p, "side", "long")),
                day_pl_dollar=float(getattr(p, "day_pl_dollar", 0) or 0),
                day_pl_pct=float(getattr(p, "day_pl_pct", 0) or 0),
            ))
        return out

    def get_last_price(self, symbol: str) -> float:
        from alpaca.data.requests import StockLatestTradeRequest
        resp = self._data.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=symbol),
        )
        return float(resp[symbol].price)

    def submit_market_order(
        self, symbol: str, qty: Optional[float] = None,
        notional: Optional[float] = None, side: str = "buy",
    ) -> OrderRecord:
        if qty is None and notional is None:
            raise ValueError("must specify either qty or notional")
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        req_kwargs = dict(
            symbol=symbol,
            side=OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        if qty is not None:
            req_kwargs["qty"] = qty
        else:
            req_kwargs["notional"] = notional
        order = self._trading.submit_order(MarketOrderRequest(**req_kwargs))
        return OrderRecord(
            order_id=str(order.id),
            symbol=symbol,
            side=side.lower(),
            qty=float(qty) if qty is not None else 0.0,
            notional=float(notional) if notional is not None else None,
            order_type="market",
            status=str(getattr(order, "status", "submitted")),
            submitted_at=datetime.utcnow(),
        )

    def close_position(self, symbol: str) -> OrderRecord:
        order = self._trading.close_position(symbol)
        return OrderRecord(
            order_id=str(order.id) if order else "closed",
            symbol=symbol,
            side="sell",
            qty=0.0,
            notional=None,
            order_type="market",
            status="submitted",
            submitted_at=datetime.utcnow(),
        )
