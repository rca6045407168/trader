# Migration: Alpaca paper → Public.com live

> **Status (2026-05-10):** API plumbing verified. Public.com adapter authenticates and fetches account state. Funding in progress (current equity $20 — placeholder). 4/9 go-live gates passing; remaining 5 need paper-account history to accumulate. **Do not flip `BROKER=public_live` until `scripts/go_live_gate.py` shows 9/9.**

## Why this migration exists

The trader was built against Alpaca's `TradingClient` (paper account). Real money lives on Public.com because:
- Public.com offers stock lending program with better terms
- Cash interest is higher (~4.5 % vs Alpaca's ~4.0 %)
- Specific-lot-ID closing is exposed in the UI (required for HIFO TLH to actually save tax dollars)
- Options, fractional shares, treasuries on one platform

Public.com's API is institutional-tier (not generally available to retail), but the operator has access via Matthew Foster's team. The Python SDK is `public_api_sdk` (installed in `.venv`).

## Architecture: broker abstraction layer

```
                ┌──────────────────────────┐
                │   main.py / execute.py   │   (callers — unchanged)
                │   reconcile.py           │
                └────────────┬─────────────┘
                             │
                             ▼
                ┌──────────────────────────┐
                │  src/trader/broker/      │
                │  get_broker_client()     │   (factory; reads BROKER env)
                │  Protocol: BrokerAdapter │
                └────────────┬─────────────┘
                             │
            ┌────────────────┼────────────────┐
            ▼                                  ▼
   ┌─────────────────┐                ┌─────────────────┐
   │ AlpacaAdapter   │                │ PublicAdapter   │
   │ (paper / live)  │                │ (public_live)   │
   └─────────────────┘                └─────────────────┘
```

**Minimum surface** (`base.py::BrokerAdapter`):
- `get_account()` → `Account(equity, cash, buying_power, ...)`
- `get_clock()` → `Clock(is_open, next_open, next_close)`
- `get_all_positions()` → `list[Position]`
- `get_last_price(symbol)` → `float`
- `submit_market_order(symbol, qty|notional, side)` → `OrderRecord`
- `close_position(symbol)` → `OrderRecord`

Anything beyond these is broker-specific and lives in the adapter.

## Switching brokers

Single env knob:
```bash
launchctl setenv BROKER alpaca_paper   # default
launchctl setenv BROKER public_live    # only after 9/9 gates
launchctl kickstart -k gui/$(id -u)/com.trader.daily-run
```

`AlpacaAdapter` works in `alpaca_paper` and `alpaca_live` modes; `PublicAdapter` is `public_live`-only (Public.com doesn't expose paper API access).

## The 9-gate go-live process

Run `python scripts/go_live_gate.py`. All must pass:

| # | Gate | What "pass" looks like |
|---|---|---|
| 1 | Public.com credentials in env | `PUBLIC_API_SECRET` + `PUBLIC_ACCOUNT_NUMBER` set |
| 2 | Adapter auth + account fetch | Real equity + buying-power readback |
| 3 | Positions fetch | `get_all_positions()` doesn't raise |
| 4 | Cost-basis method | `PUBLIC_COST_BASIS_METHOD=SPECIFIC_ID` (operator self-attestation; not API-queryable) |
| 5 | Alpaca paper stable ≥30 days | ≥20 runs, ≤5 halts in last 30d |
| 6 | Eval-harness coverage | ≥60 distinct asof dates in last 120d |
| 7 | TLH proof | ≥1 realized-loss close in journal (proves the harvest path works) |
| 8 | No reconciliation drift | No reconcile halts in last 7 days |
| 9 | Quarterly review | `quarterly_reviews` table has an entry in last 90 days |

Gates 5-7 require **time and market events**. The earliest realistic go-live date is ~30 days after first funding + first market drawdown (whichever later).

## Operator setup checklist (before go-live)

### One-time, Public.com side

- [ ] Public.com → Settings → **Cost Basis Method** = Specific Lot ID
- [ ] Public.com → Settings → **Stock Lending Program** = Enable
- [ ] Public.com → Account → Cash Management → **Earn Interest** = ON
- [ ] Generate API key scoped to Brokerage (and IRA if applicable)
- [ ] Add to `.env`:
  ```
  PUBLIC_API_SECRET=<key>
  PUBLIC_ACCOUNT_NUMBER=<account-ending>
  PUBLIC_COST_BASIS_METHOD=SPECIFIC_ID
  ```
- [ ] Run `python scripts/test_public_connection.py` → must show ✅ on all 4 steps

### One-time, trader side

- [ ] Run `python scripts/quarterly_review.py` (or `--acknowledge-all` if you've already thought through the assumptions)
- [ ] Verify `data/journal.db` has ≥60 days of `strategy_eval` rows
- [ ] Run `python scripts/go_live_gate.py` until 9/9 passes

### Per-rebalance, after go-live

The flow is the same as the Alpaca paper flow — the orchestrator handles everything:
- Daily-run daemon submits orders via the `PublicAdapter`
- Reconciliation against Public.com positions runs each morning
- TLH planner uses Public.com's lot-ID surface via the adapter
- Year-end report (`tlh_year_end.py`) pulls realized losses from journal; reconcile against Public.com's 1099-B

## What changes when `BROKER=public_live`

| Concern | Alpaca paper | Public.com live |
|---|---|---|
| Order venue | Alpaca routes to its execution layer | Public.com routes to Apex Clearing |
| Slippage | ~0 (paper fills at midpoint) | ~2-5 bps/side typical |
| Fees | $0 | $0 (zero commission) |
| Market-on-close | Supported | Supported |
| Fractional shares | Supported | Supported |
| Margin | Supported (10% rate) | Supported (similar) |
| Pattern-Day-Trader rule | Bypassed (paper) | Enforced (real account) |
| Position reporting | Real-time API | Real-time API |
| Tax events | None (paper) | Real — 1099-B at year-end |

## Rollback plan

If something goes wrong post-flip:

```bash
launchctl setenv BROKER alpaca_paper
launchctl kickstart -k gui/$(id -u)/com.trader.daily-run
```

The next daily-run will use Alpaca again. The Public.com positions stay where they are — manual cleanup is your call. The journal will reconcile against Alpaca on the next run, so Public.com positions appear as "journal-only" until you also flip the reconciliation target back.

For a clean rollback that also closes Public.com positions:
```python
from trader.broker.public_adapter import PublicAdapter
adapter = PublicAdapter()
for pos in adapter.get_all_positions():
    adapter.close_position(pos.symbol)
```

## What's NOT yet wired (to-do list when ready to flip)

**Read paths — DONE (commit `[v6-broker-port-1]`)**
- [x] `main.py` kill-switch equity read uses `get_broker().get_account()`
- [x] `main.py` market-open gate uses `get_broker().get_clock()` (works on both Alpaca and Public.com via the NYSE-clock helper)
- [x] `main.py` snapshot uses `get_broker().get_all_positions()`
- [x] `execute.py::get_last_price()` routes through broker abstraction when `BROKER != alpaca_paper`
- [x] `execute.py::get_broker()` helper added for new code paths

**Outstanding before `BROKER=public_live` is safe to flip:**
- [ ] `reconcile.py` still accepts a raw Alpaca client. Two paths use it: `get_actual_positions_qty()` (works on either via duck-typing) and `get_pending_orders_qty()` (Alpaca-specific `GetOrdersRequest`). The latter needs a `get_open_orders()` method on `BrokerAdapter` + Public.com implementation.
- [ ] `execute.py::place_target_weights()` uses Alpaca `MarketOrderRequest` + `OrderSide`/`TimeInForce` enums directly. Port to `broker.submit_market_order()`.
- [ ] `execute.py::place_bracket_order()` uses Alpaca-specific `OrderClass.BRACKET`. **Hard port** — Public.com doesn't have direct bracket-order support. Options: (a) compose 3 separate orders + manage state, (b) use Public.com's strategies feature, (c) keep BOTTOM_CATCH sleeve Alpaca-only with an env gate that disables it on `public_live`.
- [ ] `execute.py::close_aged_bottom_catches()` submits Alpaca SELL orders directly. Port to `broker.submit_market_order(side="sell")`.

The read-path port lands first because it's safe + lets the operator verify Public.com connectivity end-to-end on each daily run without ANY orders being affected. Order-submission paths come next, in this order:

1. `place_target_weights` (rebalance loop, simple market orders)
2. `close_aged_bottom_catches` (simple sells)
3. `reconcile` (positions + pending orders)
4. `place_bracket_order` (the hard one — design Public.com mapping)

## Key references

- `src/trader/broker/__init__.py` — factory + env-driven dispatch
- `src/trader/broker/base.py` — minimum interface protocol
- `src/trader/broker/alpaca_adapter.py` — preserves existing behavior
- `src/trader/broker/public_adapter.py` — wraps `public_api_sdk`
- `scripts/go_live_gate.py` — 9-gate readiness check
- `scripts/test_public_connection.py` — sanity-test the Public.com plumbing
- `RUNBOOK_MAX_RETURN.md` §2 — manual workflow (for use BEFORE API is ready)

## Decision log

- **2026-05-04**: Matthew Foster (Public.com Director of API Trading) sends API access info.
- **2026-05-07**: API plumbing verified by `test_public_connection.py`. Funds moving over.
- **2026-05-10**: Broker abstraction layer shipped; 9-gate gate framework in place; 4/9 currently passing. Earliest realistic flip date: 2026-06-10 (after 30 days of paper accumulation + Public.com cost-basis-method confirmation).
