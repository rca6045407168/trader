# trader

Personal multi-strategy quant platform. Stacks tax-loss harvesting, factor overlays, and a momentum auto-router over an extensible broker abstraction.

```
   ┌─────────────────────────────┐
   │  main.py orchestrator       │   daily-run daemon
   └──────────────┬──────────────┘
                  │
       ┌──────────┼──────────┐
       ▼          ▼          ▼
   ┌────────┐ ┌────────┐ ┌────────┐
   │ Book A │ │ Book B │ │Overlays│
   │  TLH   │ │ Alpha  │ │ × 4    │
   └────┬───┘ └────┬───┘ └────┬───┘
        └─────────┼──────────┘
                  ▼
        ┌──────────────────┐
        │ BrokerAdapter    │   src/trader/broker/
        └────────┬─────────┘
                 │
         ┌───────┴────────┐
         ▼                ▼
   ┌──────────┐    ┌──────────┐
   │ Alpaca   │    │Public.com│
   └──────────┘    └──────────┘
```

## What it does

- **Book A — TLH direct-index core**: cap-weighted basket, harvests realized losses on 5 %+ drawdowns, sector-matched wash-sale-safe replacements. Structural edge.
- **Book B — auto-router alpha sleeve**: scans a pool of momentum/quality/insider/PEAD strategies, routes capital to the rolling-IR winner under an eligibility filter.
- **Overlays**: vol-targeting, HIFO close-lot accounting, drawdown-aware sizing, calendar-effect overlay.
- **Broker layer**: swap brokers via the `BROKER` env var. Alpaca and Public.com adapters ship; the protocol is in `src/trader/broker/base.py`.

## Where things live

| Path | What |
|---|---|
| `src/trader/` | Core logic (orchestrator, broker adapters, sizing, risk, strategies) |
| `src/trader/broker/` | Broker abstraction layer + adapters |
| `src/trader/eval_strategies.py` | Strategy registry (the 32 candidates) |
| `scripts/` | Operator-facing tools (digests, audits, gates, reports) |
| `tests/` | 1000+ tests covering every module |
| `data/journal.db` | SQLite source of truth (decisions, orders, lots, snapshots) |
| `infra/launchd/` | Production daemon plists |

## Canonical references

- **`ARCHITECTURE.md`** — system design, every layer's mechanism, version history (v3 → v6), the disposition record
- **`RUNBOOK_MAX_RETURN.md`** — operator playbook: env knobs, weekly workflow, tax-time checklist
- **`docs/MIGRATION_ALPACA_TO_PUBLIC.md`** — broker-migration plan and 9-gate go-live process
- **`V5_DISPOSITION.md`** — multi-strategy frame + capital ladder (still the operating spec)

## Operator workflow

For real-money setup, daily/weekly operations, env-var activation order, and the broker-migration go-live process: see `RUNBOOK_MAX_RETURN.md`.

## Tests

```bash
python -m pytest tests/ -q
```

## Status

Active development. See `ARCHITECTURE.md` §3 for current version and the full version-history record.
