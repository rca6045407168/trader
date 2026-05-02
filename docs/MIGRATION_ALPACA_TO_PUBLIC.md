# Migration: Alpaca paper → Public.com live

**Created v3.48 (2026-05-02).** Concrete code migration plan to swap from
the current `alpaca-py` SDK to `publicdotcom-py` for live Roth IRA trading.

## TL;DR

- **Files to change:** 5 (execute.py, reconcile.py, main.py, report.py, config.py)
- **New module to create:** `src/trader/broker.py` (abstraction layer)
- **GitHub workflows to update:** 3 (daily-run.yml, hourly-reconcile.yml, backfill-journal.yml)
- **New tests to add:** ~10 (broker abstraction + Public-specific)
- **Estimated effort:** 1-2 focused days
- **Risk level:** medium (broker swap is structural; mitigated by keeping Alpaca paper running in parallel)

## Architectural choice: abstraction vs direct swap

**Two options:**

| Option | Pro | Con |
|---|---|---|
| **A. Direct swap** (replace alpaca-py with publicdotcom-py everywhere) | Less code | Loses Alpaca paper as fallback; can't A/B brokers |
| **B. Broker abstraction** (new `broker.py` interface; both adapters) | Keeps Alpaca paper for ongoing testing; allows future broker swaps | More code upfront |

**Choice: B (broker abstraction).** Rationale:
- Alpaca paper is our best ongoing-test environment (we have 4+ days of journal data, the verifier, etc.)
- Want to run paper-Alpaca + live-Public in parallel for at least 30 days post-flip to validate
- Future-proofs against IBKR/Schwab swap if Public.com has problems

## File-by-file changes

### NEW: `src/trader/broker.py`

```python
"""Broker abstraction layer. Interface defining what the trader needs from
any broker (Alpaca paper, Public live, IBKR fallback, etc.). Lets us swap
brokers without touching execute.py / reconcile.py / main.py."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class Position:
    symbol: str
    qty: float
    market_value: float
    avg_entry_price: float
    unrealized_pl: float

@dataclass
class Account:
    equity: float
    cash: float
    buying_power: float

@dataclass
class Order:
    order_id: str
    symbol: str
    side: str  # "buy" | "sell"
    qty: float | None  # for share-quantity orders
    notional: float | None  # for dollar orders
    order_type: str
    time_in_force: str
    status: str
    filled_qty: float
    filled_avg_price: float | None
    submitted_at: str
    filled_at: str | None

class Broker(ABC):
    @abstractmethod
    def get_account(self) -> Account: ...
    @abstractmethod
    def get_positions(self) -> list[Position]: ...
    @abstractmethod
    def submit_market_order(self, symbol: str, side: str,
                             notional: float | None = None,
                             qty: float | None = None,
                             time_in_force: str = "day") -> Order: ...
    @abstractmethod
    def submit_bracket_order(self, symbol: str, qty: float, side: str,
                              limit_price: float, take_profit: float,
                              stop_loss: float, trail_percent: float | None) -> Order: ...
    @abstractmethod
    def close_position(self, symbol: str) -> None: ...
    @abstractmethod
    def get_orders(self, status: str = "all", limit: int = 100,
                    after_iso: str | None = None) -> list[Order]: ...
    @abstractmethod
    def get_last_price(self, symbol: str) -> float: ...
```

### NEW: `src/trader/broker_alpaca.py`

Wraps existing Alpaca code. Trivial — just moves what's in `execute.py`
into a class implementing `Broker`. Estimated: 100 LOC, 1 hour.

### NEW: `src/trader/broker_public.py`

Wraps `publicdotcom-py` to match the same interface. Specific implementation
notes per the SDK we just verified:

```python
from public_api_sdk import (
    PublicApiClient, ApiKeyAuthConfig, PublicApiClientConfiguration,
    OrderRequest, OrderInstrument, InstrumentType, OrderSide, OrderType,
    OrderExpirationRequest, TimeInForce,
)
from decimal import Decimal
import uuid

class PublicBroker(Broker):
    def __init__(self, api_secret: str, account_number: str):
        self._client = PublicApiClient(
            ApiKeyAuthConfig(api_secret_key=api_secret),
            config=PublicApiClientConfiguration(default_account_number=account_number),
        )
        self._account_number = account_number

    def submit_market_order(self, symbol, side, notional=None, qty=None,
                             time_in_force="day"):
        order = self._client.place_order(OrderRequest(
            order_id=str(uuid.uuid4()),
            instrument=OrderInstrument(symbol=symbol, type=InstrumentType.EQUITY),
            order_side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            order_type=OrderType.MARKET,
            expiration=OrderExpirationRequest(
                time_in_force=TimeInForce.DAY if time_in_force == "day" else TimeInForce.GTC
            ),
            quantity=Decimal(str(qty)) if qty else None,
            # Public's notional vs qty handling needs verification — TBD
        ))
        return self._to_order_dataclass(order)
    # ... etc
```

Estimated: 200 LOC, 4-6 hours. Most of the time is in:
- Notional vs qty handling (Alpaca accepts notional; Public may want qty in
  Decimal — need to convert via current price or use Decimal qty everywhere)
- Order ID handling (Public requires UUID client-side; Alpaca generates server-side)
- Error mapping (different exception types per SDK)

### MODIFY: `src/trader/config.py`

Add:
```python
BROKER = os.getenv("BROKER", "alpaca_paper").lower()  # "alpaca_paper" | "public_live"
PUBLIC_API_SECRET = os.getenv("PUBLIC_API_SECRET", "")
PUBLIC_ACCOUNT_NUMBER = os.getenv("PUBLIC_ACCOUNT_NUMBER", "")
```

### MODIFY: `src/trader/execute.py`

Replace the `get_client()` function:
```python
def get_broker():
    from .config import BROKER, ALPACA_KEY, ALPACA_SECRET, ALPACA_PAPER, PUBLIC_API_SECRET, PUBLIC_ACCOUNT_NUMBER
    if BROKER == "public_live":
        from .broker_public import PublicBroker
        return PublicBroker(PUBLIC_API_SECRET, PUBLIC_ACCOUNT_NUMBER)
    from .broker_alpaca import AlpacaBroker
    return AlpacaBroker(ALPACA_KEY, ALPACA_SECRET, paper=ALPACA_PAPER)
```

All `get_client()` callsites become `get_broker()`. The interface is the same.

### MODIFY: `src/trader/reconcile.py`, `main.py`, `report.py`

Update `client.get_all_positions()` → `broker.get_positions()`, etc.
Mechanical find-and-replace.

### MODIFY: `.github/workflows/daily-run.yml`

Add new env block:
```yaml
env:
  BROKER: ${{ vars.BROKER || 'alpaca_paper' }}  # Workflow variable, not secret
  ALPACA_API_KEY: ${{ secrets.ALPACA_API_KEY }}
  ALPACA_API_SECRET: ${{ secrets.ALPACA_API_SECRET }}
  ALPACA_PAPER: "true"
  PUBLIC_API_SECRET: ${{ secrets.PUBLIC_API_SECRET }}
  PUBLIC_ACCOUNT_NUMBER: ${{ secrets.PUBLIC_ACCOUNT_NUMBER }}
```

To flip live: change repo variable `BROKER` from `alpaca_paper` to `public_live`.
This is a one-click change in the GitHub UI. The override-delay system (v3.46)
will catch the config change and enforce 24h cooling-off before the next run
executes under the new broker.

## Testing strategy

1. **Unit tests for `BrokerInterface`** — mock both adapters, verify interface
   contract holds. ~5 tests.
2. **Integration test** — fire a real order to Alpaca paper through the new
   abstraction; verify behavior unchanged. ~3 tests.
3. **Public.com smoke test** — once Roth IRA is open + funded, fire ONE
   `submit_market_order(notional=$10)` for a single name; verify it fills and
   appears in `get_positions()`. ~1 manual run.
4. **CPCV-style regression** — run 7 days of Alpaca paper through the new
   abstraction layer in parallel with the OLD direct calls; assert outputs
   identical. ~1 week of dual-running.

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| Public.com order semantics differ from Alpaca subtly (e.g., notional handling) | Smoke test with $10 order before any meaningful capital |
| Public.com rate limits unknown | Test with full rebalance batch (15 orders) BEFORE live; backoff if needed |
| Public.com SDK errors are different exceptions | Wrap in BrokerError abstraction; smooth out per-broker quirks |
| Public.com account suspension during onboarding | Keep Alpaca paper running in parallel; revert with one variable flip |
| Migration introduces a bug that loses real money | Override-delay (v3.46) catches the LIVE-flip; first live run is auto-delayed 24h. Plus 25%-initial-deployment cap (v3.45) limits damage if anything goes wrong. |

## When to do this work

NOT before:
- Roth IRA at Public.com is OPEN and FUNDED
- 60+ paper days completed at Alpaca
- `go_live_gate.py` shows 7+ of 9 automated gates passing
- Behavioral pre-commit signed

So: probably **week of 2026-06-30 or later** (60 days from today).

## Single-step cutover plan

When all gates pass and migration code is merged:

1. **Day -7:** all migration code merged + tests green; `BROKER=alpaca_paper` still
2. **Day -3:** spousal pre-brief + behavioral pre-commit signed
3. **Day -1:** verify Public.com Roth IRA: $25k funded, settled, ready
4. **Day 0 morning:** flip GitHub variable `BROKER=public_live`
5. **Day 0 morning:** override-delay catches the change → next daily-run skips
6. **Day +1 21:10 UTC:** first daily-run under live trades real $25k at 25% sizing

The single highest-leverage change is the broker abstraction. Once `Broker`
exists as an interface, swapping is a 1-line config change forever.
