"""Abstract broker interface — minimum surface used by the trader.

The trader's three callers of broker functionality:
  1. main.py — gets live equity, gets clock for market-open check
  2. execute.py — submits orders, fetches positions for reconciliation
  3. reconcile.py — fetches positions to compare to journal

Anything beyond these is broker-specific and lives in the adapter
files. Adding a new broker means implementing this protocol; nothing
else needs to change.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass
class Account:
    """Normalized account state."""
    account_id: str
    equity: float
    cash: float
    buying_power: float
    currency: str = "USD"


@dataclass
class Clock:
    """Market clock — used by the v6 weekend-order gate."""
    is_open: bool
    next_open: datetime | None
    next_close: datetime | None


@dataclass
class Position:
    """Normalized broker position."""
    symbol: str
    qty: float
    avg_entry_price: float
    market_value: float
    current_price: float
    unrealized_pl: float
    unrealized_plpc: float
    side: str = "long"  # 'long' or 'short'
    day_pl_dollar: float = 0.0
    day_pl_pct: float = 0.0


@dataclass
class OrderRecord:
    """Normalized broker order ack."""
    order_id: str
    symbol: str
    side: str       # 'buy' | 'sell'
    qty: float
    notional: float | None
    order_type: str  # 'market' | 'limit'
    status: str
    submitted_at: datetime


@dataclass
class OpenOrder:
    """Normalized representation of an unfilled (open / pending) order
    in the broker's queue. Used by reconcile.py to distinguish
    awaiting-fill positions from genuinely missing ones."""
    order_id: str
    symbol: str
    side: str       # 'buy' | 'sell'
    qty: float      # absolute quantity (not signed)
    submitted_at: datetime | None = None


class BrokerAdapter(Protocol):
    """Minimal interface the trader needs from a broker."""

    broker_name: str  # e.g. "alpaca_paper", "public_live"

    # --- read-only ---
    def get_account(self) -> Account: ...
    def get_clock(self) -> Clock: ...
    def get_all_positions(self) -> list[Position]: ...
    def get_last_price(self, symbol: str) -> float: ...
    def get_open_orders(self) -> list[OpenOrder]:
        """Unfilled orders in the broker's queue. Used by reconcile.py
        to detect awaiting-fill positions vs missing positions."""
        ...

    # --- write (orders) ---
    def submit_market_order(
        self, symbol: str, qty: float | None = None,
        notional: float | None = None, side: str = "buy",
        market_session: str = "day",
    ) -> OrderRecord:
        """Submit a market order. EITHER qty OR notional must be set.

        `side`: "buy" or "sell" (lowercase, matches Alpaca convention).
        `market_session`: "day" (regular session) or "closing"
          (MarketOnClose / TimeInForce.CLS). MOC saves ~3-8 bps per
          trade on liquid names by participating in the closing
          auction print. Brokers that don't support MOC fall back to
          "day" silently. AlpacaAdapter supports both; PublicAdapter
          falls back to "day" for now (closing auction routing on
          public_api_sdk uses EquityMarketSession which is a separate
          port).

        Returns OrderRecord with broker-assigned order_id.
        """
        ...

    def close_position(self, symbol: str) -> OrderRecord:
        """Close the entire position in a symbol. Used by the bottom-
        catch time-exit path."""
        ...
