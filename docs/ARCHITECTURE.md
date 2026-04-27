# ARCHITECTURE.md — v3.0 production target

*A reference architecture for a single-operator quantitative trading system at $50k–$1M scale. The goal is **safe autonomy**: the system runs unattended for weeks at a time, fails closed under any error, and surfaces meaningful signals to the operator without crying wolf.*

Departure from today's prototype: instead of running on a laptop with a SQLite database and scheduled Claude tasks, v3.0 runs on cloud infrastructure with PostgreSQL, redundant data feeds, structured logging, and Prometheus monitoring. **Same strategy logic, fundamentally different operational substrate.**

---

## 1. Layered architecture

```
   ┌───────────────────────────────────────────────────────────────────────────┐
   │  L7  Operator UI / dashboards / Slack alerts                                  │
   ├───────────────────────────────────────────────────────────────────────────┤
   │  L6  Observability — Prometheus, Grafana, Loki                                │
   ├───────────────────────────────────────────────────────────────────────────┤
   │  L5  Strategy / meta-allocator / regime overlay                               │
   │      — strategy registry (versioned, signed)                                  │
   │      — sleeves: momentum / bottom-catch / sector / postearn / lowvol / hedge   │
   │      — meta-allocator: bandit (UCB) over historical sleeve P&L                 │
   │      — regime: HMM over (SPY/MA, VIX, term structure, breadth)                 │
   ├───────────────────────────────────────────────────────────────────────────┤
   │  L4  Risk — pre-trade + intraday + portfolio-level (real-time + nightly)      │
   │      — 9 caps: per-position, gross, daily-loss, drawdown, vol-scale, sector,   │
   │        sleeve, beta exposure, correlation                                     │
   │      — 6 kill triggers: manual flag, missing keys, equity drops               │
   │      — stress checker: nightly VaR + scenario tests (2008/2020/2022 replay)    │
   ├───────────────────────────────────────────────────────────────────────────┤
   │  L3  OMS — order construction, validation, lifecycle, audit                   │
   │      — every order tagged: strategy_id, sleeve_id, decision_id                 │
   │      — idempotent submission via run-id sentinel                              │
   │      — lot-level position tracking (NOT fungible across sleeves)               │
   ├───────────────────────────────────────────────────────────────────────────┤
   │  L2  EMS — execution algorithm, smart routing, slippage tracking              │
   │      — algos: MARKET / LIMIT / VWAP / TWAP / Iceberg                          │
   │      — Alpaca live + paper as separate broker adapters                        │
   │      — fill subscriber (websocket) writes to journal real-time                 │
   ├───────────────────────────────────────────────────────────────────────────┤
   │  L1  Data — redundant feeds, point-in-time, normalized cache                  │
   │      — primary: Polygon (real-time + EOD)                                     │
   │      — fallback: Alpaca data, then yfinance                                   │
   │      — fundamentals: Sharadar (point-in-time, survivorship-free)                │
   │      — cache: PostgreSQL with TimescaleDB extension                            │
   ├───────────────────────────────────────────────────────────────────────────┤
   │  L0  Persistence — PostgreSQL (warm) + S3 backups (cold) + WAL archive        │
   └───────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Deployment topology

### 2.1 Compute

| Component | Where | Why |
|---|---|---|
| Trader (daily orchestrator) | Cloud Run / Lightsail / Fly.io | Always-on, no laptop dependency. ~$5-10/mo. |
| WebSocket subscriber (real-time fills) | Same node, separate process | Persistent connection to Alpaca |
| Backtest worker | On-demand cloud (Modal, GitHub Actions, Cloud Build) | Burst CPU, spend nothing when idle |
| Walk-forward optimizer | Same as backtest | Monthly cron via cloud scheduler |
| Operator UI | Streamlit on $5/mo droplet | View dashboards, manual halt |

### 2.2 Data

| Layer | Tech | Purpose |
|---|---|---|
| Hot (real-time prices) | Redis | TTL 60s, fall-through to Postgres |
| Warm (historical OHLCV) | PostgreSQL + TimescaleDB | Daily bars, multi-year, fast aggregations |
| Cold (raw tick / archives) | S3 + Parquet | Cheap, replay-able, point-in-time |
| Fundamentals (point-in-time) | Sharadar via Quandl/Nasdaq | $99-199/mo. Survivorship-bias-free. |

### 2.3 Networking

All outbound to Alpaca / Anthropic / Polygon over HTTPS with signed JWT or API key. Secrets in cloud KMS, never in env files. Allowlist outbound to known broker domains; block everything else as defense in depth against credential exfiltration.

---

## 3. Strategy lifecycle

A strategy progresses through five states with explicit gates:

```
Research → Backtest → Shadow → Paper → Live
   ↑         ↑          ↑        ↑       ↑
   |    walk-fwd OK  shadow=live  paper Sharpe  manual approval
   |    & PBO<0.20    for 30d     >1.0 for 90d  + capital allocation
   |
   └─ monthly review → retire decaying strategies
```

**Research:** Free experimentation. Lives in `notebooks/`. No discipline required.

**Backtest:** Walk-forward + CPCV evaluation. Must report Deflated Sharpe and PBO. Code lives in `strategies/<name>/v1.py`.

**Shadow:** Strategy emits decisions to a `shadow_decisions` table. NO orders placed. Compared daily to live decisions of the production strategy. Promote to Paper if shadow Sharpe (computed from forward returns of shadow decisions) >= live Sharpe over 30 days.

**Paper:** Full pipeline with paper Alpaca account. Allocated $0 capital. After 90 days, compare paper Sharpe to backtest expectation. If decay > 50%, retire. If aligned, promote.

**Live:** Allocated capital, included in meta-allocator's bandit. Subject to monthly walk-forward re-validation.

**Retired:** Strategy is removed from rotation but its historical decisions are archived for postmortem analysis.

Key property: **strategy versions are immutable**. A code change creates v2; v1 continues to run in shadow until v2 has 30 days of better OOS performance.

---

## 4. The strategy registry

A PostgreSQL table that tracks every strategy variant ever deployed:

```sql
CREATE TABLE strategies (
    id              UUID PRIMARY KEY,
    name            TEXT NOT NULL,           -- 'momentum', 'bottom_catch', etc.
    version         TEXT NOT NULL,           -- 'v1.2', 'v2.0'
    state           TEXT NOT NULL,           -- 'research'|'backtest'|'shadow'|'paper'|'live'|'retired'
    params_json     JSONB NOT NULL,          -- {"lookback_months": 12, "top_n": 5, ...}
    code_sha        TEXT NOT NULL,           -- git SHA of source
    backtest_stats  JSONB,                   -- {cagr, sharpe, maxdd, deflated_sharpe, pbo, ...}
    promoted_from   UUID REFERENCES strategies(id),  -- previous version
    created_at      TIMESTAMPTZ NOT NULL,
    promoted_at     TIMESTAMPTZ,
    retired_at      TIMESTAMPTZ
);

CREATE TABLE orders (
    id              UUID PRIMARY KEY,
    strategy_id     UUID REFERENCES strategies(id),  -- WHICH strategy emitted this order
    sleeve_id       UUID REFERENCES strategies(id),  -- WHICH sleeve (often == strategy_id)
    decision_id     UUID REFERENCES decisions(id),
    -- ... rest of order fields
);
```

**Why:** every order is forever attributable to a specific strategy version. When you change params, you can SEE the before/after on the realized P&L curve. Today's system can't do this.

---

## 5. Order management (OMS) + execution (EMS)

### 5.1 OMS responsibilities

- Validate every order against pre-trade risk checks (B1–B9 in current `risk_manager.py`).
- Tag with strategy_id, sleeve_id, decision_id.
- Submit idempotently — the OMS keeps a `pending_orders` table; if the same logical order is requested twice, the second submission is a no-op.
- Track order lifecycle (NEW → SUBMITTED → PARTIAL → FILLED / REJECTED / EXPIRED) via webhook subscriber. The current daily-snapshot model misses intraday state.

### 5.2 EMS responsibilities

- Choose execution algorithm based on order size relative to ADV (average daily volume):
  - < 0.1% ADV: market order, fills instantly
  - 0.1–1% ADV: limit order at midpoint
  - 1–5% ADV: TWAP over 30 min
  - \> 5% ADV: VWAP over 2-4 hours, or split across days
- For our $100k account on liquid S&P 500 names, we're nearly always in the first bucket. But the framework matters when scaling.
- Track realized slippage per order; feed back to backtest model so the next backtest accounts for our actual slippage distribution.

### 5.3 Position lots, not fungible positions

Current system: Alpaca shows 100 NVDA shares. Are they momentum or bottom-catch? Unknowable.

v3.0: a `position_lots` table with FIFO accounting:

```sql
CREATE TABLE position_lots (
    id            UUID PRIMARY KEY,
    symbol        TEXT,
    sleeve_id     UUID REFERENCES strategies(id),
    opened_at     TIMESTAMPTZ,
    qty           NUMERIC,
    open_price    NUMERIC,
    closed_at     TIMESTAMPTZ,           -- nullable
    close_price   NUMERIC,                -- nullable
    realized_pnl  NUMERIC                 -- nullable
);
```

When a partial close happens, FIFO closes the oldest matching-sleeve lot. This makes per-sleeve P&L calculation exact instead of heuristic.

---

## 6. The meta-allocator (bandit-based)

Replaces today's risk-parity with an Upper Confidence Bound (UCB1) bandit:

```
For each sleeve s with n_s observations and mean return r̄_s:
    UCB(s) = r̄_s + c · √(2 ln N / n_s)

Allocate capital proportional to softmax(UCB(s) / τ)
```

Where `c` is the exploration parameter (~0.1–0.3 for trading) and `τ` is the temperature (controls how aggressively we tilt toward winners).

**Properties:**
- Always allocates *some* capital to every sleeve (exploration), so a sleeve that's been under-performing recently still gets a chance to recover.
- Tilts toward sleeves with proven recent returns (exploitation).
- Has known regret bounds (UCB1 is O(log n) optimal in the bandit literature).

**Risk:** chases recent performance. Mitigated by setting `c` conservatively so it takes 6+ months of contrary evidence to abandon a sleeve.

---

## 7. Regime overlay

Above the meta-allocator, a regime detector that scales TOTAL exposure based on market state. Hidden Markov Model over four observed states:
- Trend strength (50-day return / 50-day vol)
- Implied vol (VIX)
- Yield curve slope (10y - 2y)
- Breadth (% of S&P 500 above 50-day MA)

Outputs probabilities of being in each of 3 regimes:
- **Risk-on** (calm + trending): full exposure, momentum-tilted
- **Choppy** (high vol, no trend): reduce exposure 30%, mean-reversion-tilted
- **Crisis** (extreme vol + breakdown): 50% cash, hedge with TLT

Regime is computed nightly. A regime CHANGE requires confirmation across 3 consecutive days to avoid whipsaws.

---

## 8. Observability

### 8.1 Metrics (Prometheus)

Every component emits time-series metrics:

- `trader_orders_total{strategy, sleeve, status}` — counter of orders by outcome
- `trader_position_value_dollars{symbol, sleeve}` — gauge of current positions
- `trader_realized_pnl_dollars{strategy, sleeve}` — cumulative realized P&L
- `trader_decision_latency_seconds{step}` — histogram of how long each pipeline step took
- `trader_data_staleness_seconds{feed}` — how delayed each data feed is
- `trader_kill_switch_active` — gauge (0/1)
- `trader_reconciliation_drift_dollars` — absolute $ difference between journal and broker

### 8.2 Dashboards (Grafana)

- **P&L dashboard:** equity curve, sleeve attribution, vs SPY benchmark, rolling Sharpe
- **Operations dashboard:** order success rate, decision latency, data staleness, reconciliation drift
- **Risk dashboard:** per-position size, gross exposure, sector concentration, VaR
- **Strategy lifecycle:** which strategies are live/shadow/paper/retired, when last promoted

### 8.3 Alerting

PagerDuty or OpsGenie. Alert tiers:
- **P0** (call immediately): kill switch tripped, reconciliation drift > $1000, broker down for >5 min
- **P1** (Slack with @here): rolling Sharpe < -1, daily loss > 2%, scheduled task failed
- **P2** (Slack): shadow strategy outperforms live by 0.3 Sharpe for 30+ days

### 8.4 Audit log

Every decision, every order, every state transition is logged to an append-only `audit_log` table with structured JSON. SQL views derive operational dashboards from this. Critical for postmortems and regulatory inquiry (yes, even for personal accounts you may need this someday).

---

## 9. CI / CD

### 9.1 Pre-commit

- Lint (ruff)
- Type check (mypy strict on `src/`)
- Unit tests must pass
- No print statements in `src/` (use logger)

### 9.2 PR checks

- All pre-commit checks
- Backtest regression: re-run baseline backtest, fail if Sharpe drops >0.05 OR CAGR drops >2%
- Shadow simulation: run new strategy against last 90 days of live decisions, compare

### 9.3 Deployment

- Merge to `main` triggers Docker build
- Image pushed to registry (ECR / GHCR)
- Deploy to staging (paper account) automatically
- Smoke test: 10-minute synthetic order test on staging
- Manual promotion to production (live account) via tag

### 9.4 Rollback

- Last 10 production images kept
- Single-command rollback
- Auto-rollback if production sees >3 errors in 60 seconds

---

## 10. Disaster recovery

### 10.1 Database

- PostgreSQL with daily logical backups to S3 (point-in-time recovery via WAL archive)
- 30-day retention
- Quarterly restore drill: stand up a fresh DB from backups, verify checksums match

### 10.2 Compute

- Stateless containers: any node can be replaced in 60 seconds
- Health check endpoint at `/healthz`
- Auto-restart on crash via process manager

### 10.3 Broker outage

- If Alpaca API is down at scheduled run time: skip the run, alert P1, do NOT retry. Tomorrow's run will rebalance.
- If we have positions during a Alpaca outage: nothing to do; positions are insured by SIPC up to $500k.

### 10.4 Data feed outage

- If primary (Polygon) is down: fall through to Alpaca data, then yfinance
- If ALL feeds are down: arm kill switch (do not trade with stale data)

### 10.5 Model corruption

- Strategy code and parameters are version-controlled in git AND the strategy registry
- A bad parameter change can be reverted by retiring the new strategy version and re-promoting the previous one

---

## 11. Security & compliance

### 11.1 Secrets

- API keys in cloud KMS (AWS Secrets Manager / Doppler / Infisical)
- Never in `.env` files in production
- Rotated quarterly
- Per-environment (paper key cannot trade live and vice versa)

### 11.2 Access

- Multi-factor on all broker accounts
- IAM with least-privilege for cloud resources
- Audit log of every admin action

### 11.3 Compliance (personal account scale)

- Track wash-sale violations in journal (32-day window)
- Generate annual 1099 reconciliation
- Maintain audit log for 7 years (IRS retention)
- Tax-loss harvesting opportunity detection (separate worker)

For institutional scale this expands to a much larger set of regulatory obligations — out of scope for this document.

---

## 12. The migration plan: prototype → v3.0

**This codebase is NOT what gets deployed to production.** It's a prototype that informed the architecture above. The migration is roughly 6 months of work:

| Month | Milestone | Outcome |
|---|---|---|
| 1 | Cloud infrastructure (Postgres + Lightsail) | Trader runs off-laptop |
| 1 | WebSocket fill subscriber | Real-time P&L tracking |
| 2 | Strategy registry + position lots | Per-sleeve P&L attribution |
| 2 | OMS / EMS extraction | Order lifecycle properly tracked |
| 3 | Polygon + Sharadar data integration | Survivorship-bias-free backtests |
| 3 | Bandit meta-allocator | Adaptive sleeve weighting |
| 4 | Regime detector (HMM) | Master switch above sleeves |
| 4 | Shadow / paper / live lifecycle | Safe strategy iteration |
| 5 | Prometheus + Grafana + alerting | Real observability |
| 5 | CI/CD pipeline + auto-rollback | Safe deployments |
| 6 | Disaster recovery drills | Verified resilience |

Until Month 4, the prototype keeps running on paper-trading at $100k notional with manual review. Live capital migrates only after the v3.0 system is feature-complete and has 90+ days of clean paper-trading evidence.

---

## 13. Cost estimate

For a single-operator system at $50k–$1M scale:

| Item | Monthly | Annual |
|---|---|---|
| Cloud compute (Lightsail droplet + serverless backtest) | $20 | $240 |
| PostgreSQL (Neon free tier or $19/mo) | $20 | $240 |
| S3 backups | $2 | $24 |
| Polygon market data (Stocks Starter) | $79 | $948 |
| Sharadar fundamentals (via Quandl) | $99 | $1,188 |
| Alpaca trading | $0 | $0 |
| Anthropic API (debate + post-mortem agents) | $20 | $240 |
| Domain + DNS | $1 | $12 |
| Monitoring (Grafana Cloud free tier) | $0 | $0 |
| **Total** | **~$240** | **~$2,900** |

At $100k AUM, that's 2.9% annual operational drag. At $500k AUM, 0.6%. Becomes economic at $200k+. Below that, paper-trade or accept the prototype's limits.

---

## 14. What stays from today's prototype

Not everything needs replacing:

- **Strategy logic** (signals.py, strategy.py) is correct and reusable
- **Risk manager** (risk_manager.py) is well-designed; needs to be wired to a real-time loop
- **Walk-forward optimizer** is the right pattern; needs CPCV upgrade
- **Backtest framework** (backtest.py) is a fine starting point; needs realistic-fill model
- **Tests** (44 unit tests) carry over
- **CAVEATS.md / PAPER.md / DEPLOY.md** are the institutional knowledge of why the design is what it is

The prototype is **how we learned what to build**. The v3.0 architecture is **what we build with that knowledge**.

---

*Last updated 2026-04-26. To be revised after Month 1 of paper-trading evidence.*
