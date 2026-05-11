"""Public.com adapter — wraps public_api_sdk.PublicApiClient.

Active when BROKER=public_live. Reads PUBLIC_API_SECRET and
PUBLIC_ACCOUNT_NUMBER from env (loaded from .env at config import).

Mapping notes (Alpaca → Public.com):
  - get_account()     → client.get_portfolio() (equity is a sum of
                         PortfolioEquity rows; buyingPower is a
                         dedicated object)
  - get_clock()       → not directly available; computed from NYSE
                         hours using a simple calendar (Public.com's
                         API doesn't expose a market-status endpoint
                         on the surface used here)
  - get_all_positions() → client.get_portfolio().positions
  - submit_market_order() → client.place_order(OrderRequest(...))
  - close_position()  → submit a SELL for the full position qty
  - get_last_price()  → client.get_quotes([OrderInstrument(symbol)])

Decimal handling: Public.com's SDK uses decimal.Decimal throughout.
We coerce to float on the adapter boundary so the rest of the trader
keeps using its existing float-based interfaces.

Order-id contract: Public.com requires the CALLER to supply a unique
order_id (idempotency key). We generate a UUID4 per order. This is
different from Alpaca where the broker assigns the id.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Optional

from .base import Account, BrokerAdapter, Clock, OrderRecord, Position


# Default account number lookup — picks up the env var that
# test_public_connection.py uses
def _get_account_id() -> str:
    acct = os.environ.get("PUBLIC_ACCOUNT_NUMBER", "")
    if not acct:
        raise RuntimeError(
            "PUBLIC_ACCOUNT_NUMBER missing. Set in .env or via "
            "launchctl setenv before instantiating PublicAdapter."
        )
    return acct


def _get_api_secret() -> str:
    secret = os.environ.get("PUBLIC_API_SECRET", "")
    if not secret:
        raise RuntimeError(
            "PUBLIC_API_SECRET missing. Set in .env or via "
            "launchctl setenv before instantiating PublicAdapter."
        )
    return secret


def _is_nyse_open(now: Optional[datetime] = None) -> tuple[bool, datetime, datetime]:
    """Return (is_open, next_open, next_close) using simple NYSE
    hours. Doesn't handle holidays — operator should verify via
    Public.com's UI on partial-session days (Black Friday early
    close, etc.). Replaces Alpaca's clock.is_open.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    # NYSE hours: 9:30-16:00 ET (= 14:30-21:00 UTC during EDT)
    # We use a constant 4h ET offset (EDT). DST handling is best-
    # effort; on DST-transition days the gate may be off by an hour.
    et_offset = timedelta(hours=-4)
    et_now = now + et_offset
    is_weekday = et_now.weekday() < 5
    market_open_today = et_now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close_today = et_now.replace(hour=16, minute=0, second=0, microsecond=0)

    is_open = (
        is_weekday
        and market_open_today <= et_now < market_close_today
    )

    # Compute next open
    if is_open:
        next_open = market_open_today
    else:
        # If we're past today's close or it's the weekend, next open
        # is the next business day at 09:30 ET
        next_d = et_now
        if next_d > market_close_today:
            next_d = next_d + timedelta(days=1)
        while next_d.weekday() >= 5:
            next_d = next_d + timedelta(days=1)
        next_open = next_d.replace(hour=9, minute=30, second=0, microsecond=0)

    # Next close: today's close if we haven't passed it, otherwise
    # the next open + 6.5h
    if et_now < market_close_today and is_weekday:
        next_close = market_close_today
    else:
        next_close = next_open + timedelta(hours=6, minutes=30)

    # Strip the ET offset to return UTC datetimes
    return is_open, next_open - et_offset, next_close - et_offset


class PublicAdapter(BrokerAdapter):
    broker_name = "public_live"

    def __init__(self):
        from public_api_sdk import (
            PublicApiClient, ApiKeyAuthConfig, PublicApiClientConfiguration,
        )
        self._account_id = _get_account_id()
        self._client = PublicApiClient(
            ApiKeyAuthConfig(api_secret_key=_get_api_secret()),
            config=PublicApiClientConfiguration(
                default_account_number=self._account_id,
            ),
        )

    def _portfolio(self):
        return self._client.get_portfolio(account_id=self._account_id)

    def get_account(self) -> Account:
        p = self._portfolio()
        # Public.com's Portfolio.equity is a list of PortfolioEquity
        # rows (one per asset type). Sum them for total equity.
        equity_total = sum(
            float(getattr(row, "value", 0) or 0)
            for row in getattr(p, "equity", [])
        )
        bp_obj = getattr(p, "buyingPower", None) or getattr(p, "buying_power", None)
        cash = float(getattr(bp_obj, "cashOnlyBuyingPower", 0) or 0) if bp_obj else 0.0
        buying_power = float(getattr(bp_obj, "buyingPower", 0) or 0) if bp_obj else 0.0
        return Account(
            account_id=self._account_id,
            equity=equity_total,
            cash=cash,
            buying_power=buying_power,
            currency="USD",
        )

    def get_clock(self) -> Clock:
        is_open, next_open, next_close = _is_nyse_open()
        return Clock(is_open=is_open, next_open=next_open, next_close=next_close)

    def get_all_positions(self) -> list[Position]:
        p = self._portfolio()
        out = []
        for pos in getattr(p, "positions", []) or []:
            instr = getattr(pos, "instrument", None)
            symbol = getattr(instr, "symbol", "") if instr else ""
            qty = float(getattr(pos, "quantity", 0) or 0)
            cost_basis_obj = getattr(pos, "costBasis", None)
            avg_entry = (
                float(getattr(cost_basis_obj, "value", 0) or 0) / qty
                if cost_basis_obj and qty > 0 else 0.0
            )
            last_price_obj = getattr(pos, "lastPrice", None)
            last_price = float(getattr(last_price_obj, "value", 0) or 0) \
                if last_price_obj else 0.0
            current_value = float(getattr(pos, "currentValue", 0) or 0)
            gain_obj = getattr(pos, "instrumentGain", None)
            day_gain_obj = getattr(pos, "positionDailyGain", None)
            unrealized_pl = float(getattr(gain_obj, "absoluteGain", 0) or 0) \
                if gain_obj else 0.0
            unrealized_plpc = float(getattr(gain_obj, "percentageGain", 0) or 0) \
                if gain_obj else 0.0
            day_pl = float(getattr(day_gain_obj, "absoluteGain", 0) or 0) \
                if day_gain_obj else 0.0
            day_pl_pct = float(getattr(day_gain_obj, "percentageGain", 0) or 0) \
                if day_gain_obj else 0.0
            out.append(Position(
                symbol=symbol,
                qty=qty,
                avg_entry_price=avg_entry,
                market_value=current_value,
                current_price=last_price,
                unrealized_pl=unrealized_pl,
                unrealized_plpc=unrealized_plpc,
                day_pl_dollar=day_pl,
                day_pl_pct=day_pl_pct,
            ))
        return out

    def get_last_price(self, symbol: str) -> float:
        from public_api_sdk import OrderInstrument, InstrumentType
        quotes = self._client.get_quotes(
            instruments=[OrderInstrument(symbol=symbol, type=InstrumentType.EQUITY)],
            account_id=self._account_id,
        )
        if not quotes:
            return 0.0
        q = quotes[0]
        # Quote has bid/ask/last fields; prefer last, fall back to mid
        last = getattr(q, "last", None) or getattr(q, "lastPrice", None)
        if last is not None:
            return float(getattr(last, "price", last))
        bid = getattr(q, "bid", None)
        ask = getattr(q, "ask", None)
        if bid is not None and ask is not None:
            return (float(getattr(bid, "price", bid)) +
                     float(getattr(ask, "price", ask))) / 2
        return 0.0

    def submit_market_order(
        self, symbol: str, qty: Optional[float] = None,
        notional: Optional[float] = None, side: str = "buy",
    ) -> OrderRecord:
        if qty is None and notional is None:
            raise ValueError("must specify either qty or notional")
        from public_api_sdk import (
            OrderRequest, OrderInstrument, OrderSide, OrderType,
            InstrumentType,
        )
        from public_api_sdk.models.order import (
            OrderExpirationRequest, TimeInForce,
        )
        order_id = str(uuid.uuid4())
        side_enum = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        kwargs = dict(
            order_id=order_id,
            instrument=OrderInstrument(symbol=symbol, type=InstrumentType.EQUITY),
            order_side=side_enum,
            order_type=OrderType.MARKET,
            expiration=OrderExpirationRequest(time_in_force=TimeInForce.DAY),
        )
        if qty is not None:
            kwargs["quantity"] = Decimal(str(qty))
        else:
            kwargs["amount"] = Decimal(str(notional))
        req = OrderRequest(**kwargs)
        ack = self._client.place_order(req, account_id=self._account_id)
        return OrderRecord(
            order_id=str(getattr(ack, "order_id", order_id)),
            symbol=symbol,
            side=side.lower(),
            qty=float(qty) if qty is not None else 0.0,
            notional=float(notional) if notional is not None else None,
            order_type="market",
            status=str(getattr(ack, "status", "submitted")),
            submitted_at=datetime.utcnow(),
        )

    def close_position(self, symbol: str) -> OrderRecord:
        # Find current position qty, submit SELL for full amount
        positions = self.get_all_positions()
        pos = next((p for p in positions if p.symbol == symbol), None)
        if pos is None or pos.qty <= 0:
            return OrderRecord(
                order_id="noop", symbol=symbol, side="sell",
                qty=0.0, notional=None, order_type="market",
                status="no_position", submitted_at=datetime.utcnow(),
            )
        return self.submit_market_order(symbol, qty=pos.qty, side="sell")
