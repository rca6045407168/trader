"""Broker abstraction layer.

The trader had been tightly coupled to Alpaca's TradingClient
throughout main.py + execute.py. v6.0.x ships the migration plumbing
to Public.com — but real money has to cross from Alpaca paper to
Public.com without rewriting every caller.

The pattern: a single `BrokerAdapter` protocol with the minimum
surface needed by main.py + execute.py + reconcile.py. Two
concrete implementations:

  - `AlpacaAdapter` — wraps alpaca.trading.client.TradingClient.
    Default. Preserves all existing paper-account behavior.
  - `PublicAdapter` — wraps public_api_sdk.PublicApiClient.
    Activated by setting BROKER=public_live in the env.

Switch via `BROKER` env var. Default `alpaca_paper`. Reads at
import-time of this module (`get_broker_client()` is the entry
point).

Migration plan: docs/MIGRATION_ALPACA_TO_PUBLIC.md
Go-live readiness: scripts/go_live_gate.py
"""
from __future__ import annotations

import os
from typing import Optional

from .base import BrokerAdapter, Account, Clock, Position, OrderRecord, OpenOrder


_BROKER_CLIENT: Optional[BrokerAdapter] = None


def get_broker_client() -> BrokerAdapter:
    """Singleton accessor. Picks an adapter based on BROKER env var.

    BROKER values:
      - "alpaca_paper" (default): Alpaca paper account
      - "alpaca_live": Alpaca real-money account (don't use; we
        migrated to Public.com for real money)
      - "public_live": Public.com real-money account via public_api_sdk
      - "public_paper": not currently supported (Public.com doesn't
        offer paper API access). Use alpaca_paper for paper trading.
    """
    global _BROKER_CLIENT
    if _BROKER_CLIENT is not None:
        return _BROKER_CLIENT
    broker = os.environ.get("BROKER", "alpaca_paper").lower()
    if broker in ("alpaca_paper", "alpaca_live"):
        from .alpaca_adapter import AlpacaAdapter
        _BROKER_CLIENT = AlpacaAdapter(
            paper=(broker == "alpaca_paper"),
        )
    elif broker == "public_live":
        from .public_adapter import PublicAdapter
        _BROKER_CLIENT = PublicAdapter()
    else:
        raise ValueError(
            f"Unknown BROKER={broker}. Must be one of: alpaca_paper, "
            "alpaca_live, public_live."
        )
    return _BROKER_CLIENT


def reset_broker_client_for_testing() -> None:
    """Tests can reset the singleton between cases."""
    global _BROKER_CLIENT
    _BROKER_CLIENT = None


__all__ = [
    "BrokerAdapter", "Account", "Clock", "Position", "OrderRecord", "OpenOrder",
    "get_broker_client", "reset_broker_client_for_testing",
]
