# trader

Personal automated equity trading system. Lives in `~/trader/`. Goal: **understand whether a disciplined long-only momentum book has durable edge against SP500**, run end-to-end against an Alpaca paper account with the operational stack of an institutional shop. The trader stands alone — see `CLAUDE.md` for the no-external-project-names rule.

> **Standing directives:**
> 1. Iterate autonomously. Don't ask permission for reversible work.
> 2. Use a swarm-of-agents approach for research, but **verify every agent's output** — they fabricate.
> 3. **Goal = beat SP500 on alpha-IR**, not just absolute return. The cum-active number can be leveraged-beta in disguise; the α-decomposition is the central scoreboard.
> 4. **Try to kill the strategy.** Run it against harsher benchmarks, hostile regimes, time-correct universe. Trust comes from surviving adversarial validation, not from features.

---

## Current state (snapshot — updated 2026-05-06)

| Field | Value |
|---|---|
| Version | **v3.73.19** |
| LIVE variant | `momentum_top15_mom_weighted_v1` (top-15 by 12-1 momentum, min-shifted weights, 80% gross target × VIX-gate × deployment-anchor) |
| Brokerage (paper) | Alpaca paper — actively running |
| Paper account equity | **$109,789** (+9.79% since funding; +27pp vs SPY over the most recent 5y backfill window post-fix) |
| 25y backtest (302 obs) | **+546pp cum-α / α-IR 0.70 / β 0.90** (long-window, see §3.5 in writeup) |
| 5y backtest (47 obs) | +25.6pp cum-α / α-IR 0.46 / β 1.15 |
| Tests | **149 v3.73.* + 800+ legacy = 950+ total**, all green in CI |
| Strategies tracked | **25** in eval harness (12 active + 6 sizing-aware + 7 passive baselines) |
| Live armed? | **No.** Paper only. The Tier-0 gates have not cleared (0/30 clean daily runs, 0/30 post-fix benchmark days, drawdown protocol still ADVISORY). |
| Capital recommendation | **Paper + plumbing-test live ($500-$2,000) only.** Not for return generation. |
| Full writeup | **`docs/TRADER_SYSTEM_WRITEUP_2026_05_05.pdf`** (31 pages) |

---

## What it actually does (v3.73.19 LIVE behavior)

1. **Monthly rebalance** to the top-15 momentum names from a 50-name curated US large-cap universe, weighted via min-shift formula `weight ∝ (score - min(score) + 0.01)`, scaled to 80% gross.
2. **Multiple vol-scaling layers stack** to produce the actual target gross:
   `base 80% × deployment-anchor × VIX-gate × regime-overlay × drawdown-protocol = effective gross`. Currently effective ≈ 68% (80% × 0.85 VIX gate at VIX=17.4). All layers visible in the dashboard's effective-exposure-decomposition panel (v3.73.19).
3. **Concentration caps** post-weighting: 8% single-name, 25% sector, with cap-aware redistribution (v3.73.5).
4. **Earnings reactor** monitors SEC 8-K filings for live-book names; Claude tags severity (M1-M3) + direction. Currently SHADOW: signals are journaled and forward-return-validated, but the trim rule does NOT execute. INTC's $6.5B debt-raise BEARISH M3 signal is the canonical case — facts verified against source, market interpretation diverged.
5. **Continuous strategy evaluator** journals 25 candidate strategies' picks every rebalance + settles forward returns. Surfaces a β-adjusted leaderboard (v3.73.15-18) with cum-α / α-IR / max-relative-DD per strategy.
6. **Hourly reconciliation** (journal vs broker); HALT on drift. Lot-resync tool (`scripts/resync_lots_from_broker.py`) for recovery.
7. **Heartbeat** detects silent daemon failures (Mon-Fri 14:30 UTC + 30min safety net + RunAtLoad backfill). Email + Slack alert.
8. **Journal replication** to iCloud Drive nightly (sqlite3 .backup, transactionally consistent).
9. **Build-info badge** + drift detector on the dashboard (v3.73.1) catches container-vs-host code drift.

---

## Honest performance expectations (v3.73.19 — corrected)

These come from cross-validated backtests after caught-and-fixed bugs (warmup-drag, sqrt(252) IR overstatement); not from pre-fix overstated numbers.

| Window | n obs | Cum-α | α-IR | β | Cum-active |
|---|---:|---:|---:|---:|---:|
| **25y full (2001-2026)** | 302 | **+546pp** | **0.70** | 0.90 | (universe varies) |
| Dot-com 2001-2003 | 24 | +31pp | **1.16** | **0.59** | (defensive!) |
| GFC 2007-2010 | 24 | **-19pp** | **-0.93** | 0.90 | (real weakness) |
| Long-bull 2010-2019 | 120 | +142pp | 0.86 | 0.90 | |
| COVID 2020 | 12 | -3pp | -0.29 | 0.80 | |
| Post-COVID 2021-2026 | 50 | +27pp | 0.46 | 1.07 | +77pp |

**What this says, honestly:** The LIVE strategy survives 25 years with statistically meaningful α-IR (SE ≈ 0.06 at 302 obs; the 0.70 result is many sigmas above zero). It was *defensive* through dot-com (lower β AND higher α than the naive baseline). It *underperformed* through the GFC — a real, documented weakness. Over the full 25y, LIVE has 3x naive's cumulative alpha at essentially identical α-IR (0.70 vs 0.72).

**The naive baseline check**: `naive_top15_12mo_return` (raw 12-month return, no Jegadeesh skip, no min-shift, no caps, equal-weight) is included as an adversarial active candidate. Over 25y, LIVE wins on cum-α 3x but ties on α-IR. Over the recent 5y, naive has slightly higher α-IR (0.60 vs 0.46) — regime-specific.

**Survivorship caveat**: the 41-name 25y universe is the subset of our SECTORS that survived to 2026. Names that delisted 2000-2026 aren't there. True time-versioned universe construction (using historical SP500 constituent data) is open work.

**The first 12 months of live trading have a real chance of underperforming SPY.** 90%+ of retail algo traders do. The thesis is multi-year, not multi-quarter.

---

## Tier-0 gates (must clear before meaningful capital)

These six gates were prescribed by an internal due-diligence review (v3.73.4) and tightened by an adversarial critique (v3.73.17). Today **none have cleared**.

| Gate | Status | What "cleared" looks like |
|---|---|---|
| 30+ completed daily runs | 0 / 30 | Journal shows 30 consecutive weekday rows with status=completed, no missed-fire alerts. (Counter reset 2026-05-06 after manual lot-resync.) |
| 30+ days post-fix benchmark tracking | 7 / 30 | daily_snapshot table has 30+ rows with non-zero SPY closes, all post-v3.73.13 (clean of the IR/warmup bugs) |
| Caps verified live | PARTIAL | Today's broker positions all ≤ 8% (max GOOGL 6.82%); Tech 21.4% (under 25%). Cap math verified live; not yet verified through 30 consecutive rebalances. |
| 80% target vs actual gross gap explained | DONE | Resolved 2026-05-06: VIX × 0.85 risk gate. Decomposition panel surfaces it permanently. |
| Drawdown protocol enforced | OPEN | Currently DRAWDOWN_PROTOCOL_MODE=ADVISORY (warns only). Flip to ENFORCING is a deliberate operator decision, not yet made. |
| GFC weakness postmortem | OPEN | LIVE -19pp vs naive -8.7pp during 2007-2010. Cause likely the min-shift weighting concentrating into financial-leverage names. Not yet investigated. |

---

## 3‑gate promotion methodology (no shortcuts)

Any candidate variant must pass all three gates before promoting from `shadow` → `live`:

1. **Gate 1 — Survivor 5‑regime backtest.** Bull, bear, sideways, vol‑spike, slow‑grind. Must beat SPY‑equiv risk‑adjusted in ≥4 of 5.
2. **Gate 2 — PIT validation.** Re‑run on `universe_pit.py` (ticker membership as of date, not today) + `data.py` cache without future leakage. Sharpe must drop <30% from in‑sample.
3. **Gate 3 — CPCV (Combinatorial Purged Cross‑Validation, Lopez de Prado).** `cpcv_backtest.py`. PBO (Probability of Backtest Overfitting) <0.5; deflated Sharpe (Bailey & Lopez de Prado) >0.

If any gate fails: candidate is logged in the kill‑list (`docs/CRITIQUE.md`) with a reason, and **not re‑proposed**.

---

## 4‑layer defense architecture (v3.46)

Every layer must independently fail for real money to be at risk.

### Layer 1 — Code enforcement (this repo)
- `risk_manager.py` — 9 ladders: position cap (16% safety / 10% target), gross cap, daily‑loss freeze (6% → 48h), deploy‑DD freeze (25% → 30‑day no‑new), liquidation gate (33% → requires written post‑mortem to clear), sector cap (35%), vol scaling, exposure check, kill‑switch passthrough.
- `kill_switch.py` — 6 triggers: manual flag, missing keys, week/month/peak DD, reconcile mismatch.
- `deployment_anchor.py` — locks equity at first daily‑run; all DD math anchored here. `reset_anchor()` requires `reason ≥ 50 chars` + `post_mortem_path`.
- `override_delay.py` — SHA‑256 over LIVE variant + risk constants; any change triggers 24‑hour cooling‑off before the next daily‑run executes. Bypass requires sentinel file (which we don't create).
- `peek_counter.py` — counts `workflow_dispatch` events (manual triggers); alerts at >3 / 30‑day rolling window.
- `agent_verifier.py` (v3.47) — TRUST/VERIFY/ABSTAIN gate for LLM outputs feeding decisions. Catches fabricated arxiv citations, anonymous authors, Sharpe>10 claims.
- `validation.py` — empty/short/bad price data raises; warns on splits, stale data, concentration.
- `reconcile.py` — journal vs broker positions; HALT on drift.

### Layer 2 — Custodian (broker)
- Alpaca paper today; Public.com Roth IRA planned. Brokers enforce regulatory limits (PDT exemption in IRAs, settlement, NBBO).

### Layer 3 — Human checkpoint
- `docs/BEHAVIORAL_PRECOMMIT.md` — must be signed before live arming. Pre‑commits to: (a) no manual override after −15% DD; (b) no doubling down; (c) liquidation gate triggers REQUIRE 7‑day cool‑off + post‑mortem before any new deployment.
- Spousal pre‑brief required before LIVE flip.

### Layer 4 — Document trail
- `docs/CRITIQUE.md` — kill‑list of every retired candidate + reason.
- `docs/PRE_REGISTRATION_OOS.md` — pre‑registers exact strategy parameters before any new shadow runs (so we can't post‑hoc tune).
- `docs/RESEARCH.md`, `PAPER.md`, `ARCHITECTURE.md` — design rationale, audit trail.

---

## Roth IRA path (corrected v3.48)

**WRONG (earlier doc):** Open Roth IRA at Alpaca direct. Per Alpaca support: *"As of September 2024, IRA accounts are only available for Broker API clients"* — they only sell IRAs to fintech partners (Robinhood, SoFi).

**RIGHT:** Open Roth IRA at **Public.com**. Direct retail, fractional shares, official Python SDK (`publicdotcom-py`), $0 API access.

Setup checklist: `docs/ROTH_IRA_SETUP.md`. Migration plan: `docs/MIGRATION_ALPACA_TO_PUBLIC.md`. Read‑only API verification: `scripts/test_public_connection.py`.

**Migration architectural choice:** broker abstraction layer (NOT direct swap). New `src/trader/broker.py` interface; `broker_alpaca.py` + `broker_public.py` adapters. GitHub variable `BROKER=alpaca_paper|public_live` flips between them. Lets us keep Alpaca paper running in parallel after live flip for ongoing validation.

**Estimated effort:** 1‑2 focused days. **Do NOT start before** Roth IRA is open + funded, 60+ paper days complete, `go_live_gate.py` showing 7+/9.

---

## LLM agent verification (v3.47)

Discovered the hard way: agents fabricate convincing citations. After a behavioral‑research swarm cited unverified Gollwitzer/Karlan/Loewenstein effect sizes, then a follow‑up swarm cited an "Anonymous"‑authored arxiv paper, we built a mandatory verification gate.

**Three actions** (RSCB‑MC framing — `docs/SWARM_VERIFICATION_PROTOCOL.md`):
- **TRUST** — output stands.
- **VERIFY** — sample 1‑2 claimed citations, WebFetch them.
- **ABSTAIN** — discard the entire output.

**Auto‑abstain triggers** (`agent_verifier.py`):
- Anonymous authors on arxiv
- Sharpe > 10
- Sub‑agent claims "verified via arxiv API" (sub‑agents typically lack web access)
- Citations with no quoted text (uncheckable)

**Mandatory swarm prompt elements:**
1. Verifiable output structure (arxiv ID + verbatim quote + claimed authors)
2. Refusal‑is‑acceptable clause ("If you cannot find a real paper, say 'no qualifying paper found' — DO NOT FABRICATE")
3. Verification warning ("I WILL verify N random citations. Fake = entire output discarded.")
4. Anti‑pattern list (e.g., "reject Sharpe > 5.0 claims")

**Empirical proof it works:** the 4‑agent swarm on 2026‑05‑02 caught Agent 2 fabricating an "Anonymous"‑authored Sharpe 2.43 paper. Without the gate, that would have shipped into a live trading decision.

---

## Killed candidates — DO NOT RE‑PROPOSE

Documented in full in `docs/CRITIQUE.md`. High‑level kill list:

| Candidate | Why killed |
|---|---|
| `momentum_top3_aggressive_v1` | 27% concentration risk, single‑name blowup → ~30% account drawdown. **Retired v3.42.** |
| `momentum_top5_equal_v1` | Outperformed by top‑15 mom‑weighted on Sortino + max‑DD jointly. |
| Naive PEAD | Look‑ahead in earnings timestamp; PIT version had no edge. |
| LLM‑driven full trading agent (TradingGPT, FinAgent style) | 95%+ of LLM‑trading papers have look‑ahead via training cutoff. Verified via FINSABER (arxiv 2505.07078). |
| GPT stock‑recommender portfolio | Same look‑ahead problem; cost > alpha at retail scale. |
| Multi‑agent LLM debate over picks | API cost ($50‑500/day) eats alpha. |
| Daily LLM rebalance | Latency disadvantage vs systematic players. |
| Bottom‑catch with bracket orders | Brackets gave back 36% of edge (v0.7 4‑mode exit comparison). |
| 6m / top‑10 momentum | Walk‑forward dominated by 12m / top‑5 → top‑15. |
| Activist 13D follow‑on (naive) | Pump already priced; PIT edge negative. Kept as scanner only. |
| Cointegration pairs (naive) | OOS broke down post‑2017; no edge after costs. |
| Merger‑arb (naive) | Spread compression eaten by deal‑break tail risk. |
| Inverse‑vol allocator | Beat by HRP on identical universe. |
| Direct Alpaca Roth IRA | **Alpaca doesn't sell IRAs to retail.** Public.com is the right path. |

If you propose any of these, check the kill date and reason first.

---

## Module catalog

### `src/trader/` (core)

| Module | Purpose |
|---|---|
| `config.py` | env loading, broker selection (`BROKER=alpaca_paper|public_live`) |
| `universe.py` / `universe_pit.py` | S&P 500 / liquid‑50 ticker lists; PIT version uses membership as of date |
| `data.py` | yfinance fetch + parquet cache |
| `signals.py` | momentum, RSI, Bollinger z‑score, ATR, bottom‑catch composite |
| `vol_signals.py` | realized vol, IV proxies |
| `strategy.py` | ranks momentum + finds bottoms → trade candidates |
| `variants.py` | **LIVE variant = `momentum_top15_mom_weighted_v1`**; ~10 shadow variants |
| `backtest.py` | pandas‑based backtest with SPY benchmark |
| `cpcv_backtest.py` (script) | CPCV gate (Lopez de Prado) |
| `pbo.py` | Probability of Backtest Overfitting |
| `deflated_sharpe.py` | Bailey‑Lopez de Prado deflated Sharpe |
| `perf_metrics.py` | Sharpe / Sortino / Calmar / Information Ratio |
| `regime.py` / `hmm_regime.py` | regime detection (rule‑based + 3‑state HMM) |
| `garch_vol.py` | GARCH(1,1) vol forecast for sizing |
| `risk_manager.py` | 9 risk ladders + freeze state machine |
| `risk_parity.py` / `hrp.py` | Hierarchical Risk Parity allocator |
| `residual_momentum.py` | momentum after market/sector beta strip |
| `sectors.py` | GICS sector caps |
| `macro.py` | macro regime overlay (slope of yield curve, HY OAS) |
| `merger_arb.py` | merger‑arb spread scanner |
| `cointegration.py` | pairs scanner |
| `activist_signals.py` | 13D filings parser |
| `anomalies.py` | PEAD, drift, gap‑fill scanners |
| `ml_ranker.py` | gradient‑boosted ranker over feature stack |
| `ab.py` | A/B test framework for variants |
| `meta_optimizer.py` | meta‑allocator across variants |
| `options_barbell.py` (v3.43) | OTM call sleeve research; **NOT wired into LIVE** |
| `critic.py` | Bull/Bear/Risk‑Manager swarm debate (Claude API) |
| `postmortem.py` | nightly self‑review agent (Claude) |
| `narrative.py` | daily report narrative (Claude with web_search) |
| `agent_verifier.py` (v3.47) | TRUST/VERIFY/ABSTAIN gate for any LLM output feeding decisions |
| `journal.py` | SQLite — decisions, orders, snapshots, postmortems, position_lots |
| `execute.py` | Alpaca order placement (will become broker‑abstracted in migration) |
| `reconcile.py` | journal vs broker positions; HALT on drift |
| `kill_switch.py` | 6 triggers |
| `deployment_anchor.py` (v3.46) | locks equity at first run; DD math anchored here |
| `override_delay.py` (v3.46) | SHA + 24h cooling‑off on LIVE config change |
| `peek_counter.py` (v3.46) | manual workflow_dispatch counter |
| `validation.py` | data sanity checks |
| `replay.py` | deterministic replay of any past day for debugging |
| `report.py` | daily report renderer |
| `notify.py` / `alerts.py` | Slack / email outputs |
| `order_planner.py` | translates target weights → orders, respects fractional support |
| `main.py` | daily orchestrator (override_delay → peek_counter → deployment_anchor → kill_switch → variants → execute → reconcile → narrative) |

### `scripts/` (entry points + research)

**Daily / operational:**
- `run_daily.py` — main entry; placed by GitHub Action
- `run_reconcile.py` — hourly reconciliation
- `run_postmortem.py` — nightly self‑review
- `run_anomaly_scan.py` — scanner sweep
- `weekly_digest.py` — SPY‑relative + DD + peek + override‑delay status
- `halt.py` / `halt.sh` — manual kill switch
- `notify_cli.py` — manual alert
- `resume.sh` — clear halt
- `drawdown_alert.py` — out‑of‑band DD watcher

**Backtests / research:**
- `run_backtest.py` — single‑variant backtest
- `run_optimizer.py` — walk‑forward parameter sweep
- `cpcv_backtest.py` — CPCV gate
- `run_pbo_audit.py` — PBO over candidate set
- `run_dsr_audit.py` — deflated Sharpe over candidate set
- `bootstrap_sharpe_ci.py` — bootstrap Sharpe CIs
- `regime_stress_test.py` — 5‑regime stress
- `chaos_test.py` — 10 chaos scenarios (data outage, broker outage, partial fills, etc.)
- `compare_variants.py` — head‑to‑head variant comparison
- `strategy_decay_check.py` — flags shadows outperforming LIVE
- `slippage_sensitivity.py` / `realized_slippage_tracker.py` — slippage realism
- `run_tax_aware_sim.py` — taxable vs Roth simulation
- `cash_yield_audit.py` — cash sweep yield check
- `iterate_v3.py` ... `iterate_v14_more_anomalies.py` — historical iteration logs (immutable record)
- `walk_forward_prefomc.py` — pre‑FOMC drift backtest
- `pead_proxy_test.py` / `pead_smallcap_backtest.py` — PEAD studies
- `activist_13d_backtest.py`, `cointegration_backtest.py`, `run_merger_arb_scan.py` — anomaly backtests
- `options_barbell_backtest.py` (v3.43) — barbell sleeve research
- `account_size_test.py` — minimum viable account size
- `bsc_scaling_analysis.py` — Black‑Scholes call sizing
- `exp_inverse_vol.py` — inverse‑vol allocator (killed)
- `regression_check.py` — daily regression vs golden runs
- `spy_relative_dashboard.py` — outperformance vs SPY
- `three_numbers.py` — single‑output: excess CAGR, vol, max‑DD vs SPY
- `readiness_monitor.py` — go‑live readiness dashboard
- `go_live_gate.py` — **9 automated gates; must show 9/9 before live arming**
- `backfill_3month.py` / `backfill_lots.py` / `backfill_journal_from_alpaca.py` (v3.46.1) — journal restoration from broker truth
- `test_public_connection.py` (v3.48.1) — read‑only Public.com API verification
- `test_email.py` — alert plumbing test
- `run_task_health.py` — workflow self‑check

### `.github/workflows/`

| Workflow | Trigger | Purpose |
|---|---|---|
| `daily-run.yml` | cron 21:10 UTC | full daily orchestrator |
| `hourly-reconcile.yml` | cron hourly | journal vs broker reconciliation |
| `backfill-journal.yml` | manual | restores journal artifact + backfills lots from broker |
| `readiness-and-dd-alerts.yml` | cron + push | readiness dashboard + DD alerts |
| `weekly-digest.yml` | cron weekly | weekly summary email |
| `ci.yml` | push | unit tests + chaos + go‑live gate sanity |

**Cross‑workflow journal artifact lookup:** all daily/hourly workflows now query `repos/$GITHUB_REPOSITORY/actions/artifacts?name=trader-journal` for the LATEST artifact across ALL workflows (so backfill output is picked up). Old code only looked at the same workflow's history → broke after backfill.

### `docs/`

| Doc | Purpose |
|---|---|
| `ARCHITECTURE.md` | end‑to‑end system design |
| `PAPER.md` | research paper / evaluation framework / v2/v3 roadmap |
| `RESEARCH.md` | references + paper notes |
| `CRITIQUE.md` | **kill list — every retired candidate + reason** |
| `BEHAVIORAL_PRECOMMIT.md` | signed pre‑commit (the binding behavioral contract) |
| `BEHAVIORAL_PRECOMMIT_DRAFT.md` | unsigned draft to edit before sign |
| `PRE_MORTEM_TEMPLATE.md` | template for liquidation‑gate post‑mortem |
| `PRE_REGISTRATION_OOS.md` | pre‑register parameters before shadow runs |
| `GO_LIVE_CHECKLIST.md` | 9 automated gates + manual sign‑off list |
| `RICHARD_ACTION_ITEMS.md` | open items requiring human action |
| `ROTH_IRA_SETUP.md` | **Public.com path (corrected v3.48)** |
| `MIGRATION_ALPACA_TO_PUBLIC.md` | broker abstraction migration plan |
| `LLM_APPLICATIONS.md` | honest assessment of where LLMs help vs don't |
| `SWARM_VERIFICATION_PROTOCOL.md` | mandatory verification protocol for any agent output feeding decisions |
| `CLOUD.md` | GitHub Actions deploy notes |

---

## Architecture diagrams

See **[`docs/ARCHITECTURE_DIAGRAM.md`](docs/ARCHITECTURE_DIAGRAM.md)** for rendered Mermaid diagrams covering:
- System overview (every component, data flow, storage layer)
- Daily run sequence (21:10 UTC step-by-step)
- 3-gate promotion pipeline (survivor → PIT → CPCV)
- Defense-in-depth (4 layers from code → real money)
- Broker abstraction (Alpaca paper ↔ Public.com swap)

GitHub renders Mermaid natively — view that file on github.com.

## Strategy direction (Architect + Trader)

See **[`docs/ARCHITECT_TRADER_DEBATE.md`](docs/ARCHITECT_TRADER_DEBATE.md)** for the two‑persona adversarial review of the system + the synthesized 18‑item action plan to make it world‑class. The debate informs every Tier B / C decision.

## GitHub research

See **[`docs/SWARM_GITHUB_RESEARCH_2026_05_02.md`](docs/SWARM_GITHUB_RESEARCH_2026_05_02.md)** — 4‑agent swarm investigating GitHub repos that could elevate the trader to world‑class. 41 verified‑real repos surfaced; 14 categories where the swarm honestly returned "no qualifying repo found." Top 11 adoptions ranked by ROI/effort.

## GCP deployment plan

See **[`docs/GCP_DEPLOYMENT.md`](docs/GCP_DEPLOYMENT.md)** — full migration plan from GitHub Actions cron to Cloud Run + Scheduler + Secret Manager + Artifact Registry. Cuts over the same week as `BROKER=public_live`. ~$3‑6/month, fixes the "didn't run at all" alarm gap, gives us Cloud Logging + Monitoring for free.

---

## Local live dashboard (v3.50)

Streamlit UI showing real-time decisions, positions, regime overlay state, freeze state, shadow variants, and intraday risk log. Auto-refreshes every 30 seconds. Reads `data/journal.db` (the same SQLite that GitHub Actions writes via the trader-journal artifact).

```bash
cd ~/trader
docker compose up -d dashboard       # builds + starts in background
open http://localhost:8501           # auto-restarts on crash
docker compose logs -f dashboard     # tail logs
docker compose down                  # stop
```

**Tabs:**
- 🏠 Overview — pre-flight gate state (deployment anchor, override-delay SHA, peek counter, freeze state) + last 5 runs
- 🎯 Decisions — last 50 decisions + last 50 orders
- 📦 Positions — open lots by sleeve + closed lots history
- 🌡️ Regime overlay — **live recomputation** of HMM + macro + GARCH every refresh
- 👥 Shadow variants — last 7 days of shadow decisions, side-by-side
- ⚡ Intraday risk — log from `intraday-risk-watch.yml`
- 📈 Performance — equity curve + drawdown chart
- 📜 Postmortems — nightly self-review summaries
- 🔧 Manual — workflow-dispatch buttons (gated by "type 'I-MEANT-TO'" + counted by `peek_counter`)

**Sidebar:**
- "⬇️ Pull latest journal artifact" — runs `gh run download` to sync the latest `trader-journal` artifact from GitHub Actions into local `data/journal.db`
- Auto-refresh slider (5–300 sec)
- Data freshness indicator

The dashboard is **read-only by default**. Manual workflow triggers exist in the "🔧 Manual" tab but require typing `I-MEANT-TO` to enable, and every dispatch counts toward the 3-per-30-day `peek_counter` limit.

## "Running constantly" — what to put on your laptop

| Component | Run constantly on laptop? | Why |
|---|---|---|
| **Dashboard** (`docker compose up -d dashboard`) | ✅ yes | Read-only viewer; auto-refreshes; perfect for monitoring |
| **GitHub Actions cron** (5 workflows) | already on, no action needed | The trading trigger; lives in GitHub's infra |
| **Local cron emulator** (commented in `docker-compose.yml`) | ❌ no, by default | Strategy doesn't trade more often by running locally; would create split-brain reconciliation problem with GitHub Actions |
| **Production trader image** (`Dockerfile`) | ❌ no | One-shot. Use `docker run` for QA / smoke tests / debugging |

Trading itself is monthly-rebalance + daily-checkpoint by design (per `docs/CRITIQUE.md` — overtrading is the #1 retail-blow-up mode). The cron schedule is correct; the dashboard is what you actually want running constantly.

## Docker images

Three Dockerfiles:
- **`Dockerfile`** — production image. Slim base, no tests. Will become the prod image once we cut over to GCP Cloud Run (`docs/GCP_DEPLOYMENT.md`).
- **`Dockerfile.test`** — adds `pytest`, `hmmlearn`, `arch`, and the test suite. Default ENTRYPOINT runs all 141 tests. Override `--entrypoint python` to run any script. **This is what we use for QA + local production smoke testing today.**
- **`Dockerfile.dashboard`** — adds `streamlit`. Default ENTRYPOINT serves the dashboard on port 8501. Used by `docker compose up -d dashboard`.

**Verified working as of v3.49.2:**
- `docker build -f Dockerfile.test -t trader-test .` → builds clean
- `docker run --rm trader-test` → 141/141 tests pass in 30 seconds
- `docker run --rm -e DRY_RUN=true -e USE_DEBATE=false ... --entrypoint python trader-test scripts/run_daily.py --force` → full pipeline executes including the new regime overlay (computed for observability, not applied because `REGIME_OVERLAY_ENABLED=false` default)

**To run the full QA + smoke test:**

```bash
cd ~/trader
# 1. Build test image
docker build -f Dockerfile.test -t trader-test .

# 2. Run the 141-test suite
docker run --rm trader-test
# Expected: 141 passed in ~30s

# 3. Run a production smoke test (DRY_RUN, no real orders)
docker run --rm \
  -e DRY_RUN=true \
  -e USE_DEBATE=false \
  -e ALPACA_API_KEY=dummy_smoke \
  -e ALPACA_API_SECRET=dummy_smoke \
  -e ALPACA_PAPER=true \
  -e ANTHROPIC_API_KEY=dummy_smoke \
  --entrypoint python \
  trader-test scripts/run_daily.py --force
```

**To run real orders (production image, not test):**

```bash
docker build -t trader .
docker run --rm \
  --env-file .env \
  -v $(pwd)/data:/app/data \
  trader
```

Default `CMD` of the production image is `python scripts/run_daily.py --force`. Override with any script:

```bash
docker run --rm --env-file .env -v $(pwd)/data:/app/data \
  trader scripts/run_reconcile.py

docker run --rm --env-file .env -v $(pwd)/data:/app/data \
  trader scripts/test_public_connection.py
```

**Recommendation:** for local dev/debugging just use the venv path below — it's faster and the parquet cache is shared. Reach for Docker for QA + final pre-push verification.

---

## Setup (venv path — preferred for local dev)

```bash
git clone <this repo> ~/trader
cd ~/trader
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env:
#   ALPACA_API_KEY / ALPACA_API_SECRET / ALPACA_PAPER=true
#   ANTHROPIC_API_KEY
#   PUBLIC_API_SECRET / PUBLIC_ACCOUNT_NUMBER  (only after Public.com Roth IRA approved)
#   BROKER=alpaca_paper                        (do NOT flip to public_live without 9/9 gates)
```

**Get Alpaca paper keys:** sign up at https://alpaca.markets (free, no SSN for paper) → Paper Trading → API key + secret.

**Get Public.com keys:** open Roth IRA at https://public.com → wait 1‑3 days for approval → fund → Account Settings → Security → API → "Create personal access token". **Add directly to `.env`. NEVER paste secrets into chat.**

---

## Run

```bash
# Verify Public.com API is reachable (read‑only, no orders)
python scripts/test_public_connection.py

# Backtest LIVE variant
python scripts/run_backtest.py

# CPCV gate (must pass before any promote)
python scripts/cpcv_backtest.py

# Walk‑forward parameter sweep
python scripts/run_optimizer.py

# Dry‑run today's decisions (no orders)
DRY_RUN=true python scripts/run_daily.py

# Place actual paper orders
python scripts/run_daily.py

# Force re‑run if you already ran today (override idempotency)
python scripts/run_daily.py --force

# Nightly self‑review
python scripts/run_postmortem.py

# Reconcile journal vs broker
python scripts/run_reconcile.py

# Manual kill switch
python scripts/halt.py on "flash crash"
python scripts/halt.py off
python scripts/halt.py status

# Readiness check (9 automated gates)
python scripts/go_live_gate.py

# SPY‑relative performance
python scripts/three_numbers.py
python scripts/spy_relative_dashboard.py

# Decay check — does any shadow beat LIVE?
python scripts/strategy_decay_check.py

# Backfill journal from broker (after artifact loss)
python scripts/backfill_journal_from_alpaca.py
```

---

## Live‑arm checklist (do NOT flip until ALL true)

1. ✅ `docs/BEHAVIORAL_PRECOMMIT.md` is signed (saved from `_DRAFT.md`)
2. ✅ Spousal pre‑brief completed
3. ✅ Public.com Roth IRA: open, funded ($25k or contribution‑limit max), settled
4. ✅ `python scripts/test_public_connection.py` shows green
5. ✅ Broker abstraction layer (`broker.py` + adapters) merged + tested
6. ✅ `python scripts/go_live_gate.py` shows **9/9** automated gates
7. ✅ ≥60 paper trading days accumulated in `data/trader.db`
8. ✅ `python scripts/three_numbers.py` shows excess CAGR over SPY > 0
9. ✅ `python scripts/strategy_decay_check.py` shows no shadow significantly outperforms LIVE
10. ✅ Independent strategy review completed (different model OR human reviewer)
11. ✅ GitHub variable `BROKER=public_live` flipped (one‑click)
12. ✅ Override‑delay catches the variable change → next daily‑run skips
13. ✅ Day +1: first live daily‑run executes at **25% sizing cap** (v3.45)

If any item is ❌: do not arm.

---

## Version history (what each release shipped)

| Ver | Highlight |
|---|---|
| v0.1‑v0.9 | Initial momentum + bottom‑catch; walk‑forward; survivorship correction; v0.9 hardening (kill switch, validation, reconcile, idempotency, 38 tests) |
| v1.x | Multi‑variant framework; A/B; meta‑allocator |
| v2.x | Anomaly scanners (PEAD, activist, merger‑arb, cointegration); HRP allocator; HMM regime |
| v3.0‑v3.20 | ML ranker; deflated Sharpe; PBO; CPCV gate; PIT universe; tax‑aware sim |
| v3.27 | Independent reviewer caught kill‑switch bug |
| v3.29 | top‑15 mom‑weighted promoted to shadow |
| v3.42 | **LIVE flipped from top‑3 → top‑15 mom‑weighted** (concentration risk) |
| v3.43‑v3.44 | OTM call barbell sleeve research + stress test (NOT wired) |
| v3.45 | 25% initial deployment cap |
| v3.46 | **4‑layer enforcement:** deployment_anchor + override_delay + peek_counter + tightened risk ladders |
| v3.46.1 | journal artifact persistence fix; backfill workflow; cross‑workflow artifact lookup |
| v3.47 | **agent_verifier:** TRUST/VERIFY/ABSTAIN gate for any LLM output feeding decisions; SWARM_VERIFICATION_PROTOCOL |
| v3.48 | **ROTH_IRA_SETUP corrected:** Public.com (NOT Alpaca direct); MIGRATION_ALPACA_TO_PUBLIC plan |
| v3.48.1 | Read‑only Public.com API verification script (`test_public_connection.py`); confirmed against account 5OH27398 |
| v3.48.2 | README comprehensive rewrite |
| v3.48.3 | Mermaid architecture diagrams (`docs/ARCHITECTURE_DIAGRAM.md`) + Docker explainer in README |
| v3.49.0 | **Tier A world‑class build:** wired the 3 dormant signal modules (HMM regime, macro stress, GARCH vol) into a unified `regime_overlay.py` applied to gross exposure (env‑flag default off); built `meta_allocator.py` for sleeve‑level capital allocation across multiple LIVE sleeves (`single_live` default mode preserves today's behavior); built `intraday_risk.py` + workflow that catches flash crashes in 30 min instead of 24 h via the existing freeze‑state machine. 15 new tests. |
| v3.49.1 | **GitHub research swarm + architect/trader debate.** 4 agents researched verified‑real GitHub repos (41 found, 14 honestly‑empty refusals, 11 disqualified after license verification). Top 11 adoptions ranked. Two‑persona adversarial review converged on 18‑item action plan. |
| v3.49.2 | **QA pass green: 141/141 tests pass in Docker container.** Production smoke test of `scripts/run_daily.py --force` in `Dockerfile.test` confirmed full pipeline executes including the new regime overlay (computed for observability, not applied — exactly as designed with `REGIME_OVERLAY_ENABLED=false` default). `requirements.txt` promoted `hmmlearn` + `arch` from lazy‑loaded variant deps to first‑class. **GCP deployment plan** (`docs/GCP_DEPLOYMENT.md`) documents Cloud Run + Scheduler + Secret Manager + Artifact Registry migration path (~$3‑6/mo). |

---

## Open work / timeline

| When | What |
|---|---|
| Now → Day 60 | Continue Alpaca paper; accumulate journal data; `weekly_digest.py` weekly review |
| Day 30 | User: open Public.com Roth IRA (1‑3 day approval) |
| Day 31‑35 | User: fund Roth IRA; settle |
| Day 35 | User: regenerate Public.com API keys scoped to IRA; add to `.env` + GitHub secrets |
| Day 60‑75 | Build broker abstraction (`broker.py` + adapters) per `MIGRATION_ALPACA_TO_PUBLIC.md` |
| Day 75 | Run dual: Alpaca paper + Public paper (if Public exposes paper) for 1 week |
| Day 80 | `go_live_gate.py` review — chase any red gates |
| Day 85 | Sign `BEHAVIORAL_PRECOMMIT.md`; spousal brief |
| Day 90 | Flip `BROKER=public_live` → override‑delay catches → Day +1 first live run at 25% sizing |
| Day 90+30 | Review live perf vs paper expectation; scale to 50% if within band |
| Day 90+90 | Scale to 100% if within band |

---

## Reality check (read this before arming)

- 90% of retail algo traders underperform buy‑and‑hold SPY in year 1.
- 80% of backtested strategies fail live.
- Realistic survivor returns: **8‑15% annual after costs** (our PIT honest is +19%; the gap is uncertainty).
- A −33% drawdown WILL happen at some point. It's already in the deployment_anchor + liquidation_gate machinery — your job is to not panic‑override it.
- The single biggest mistake is **flipping live before the gates pass**. The second biggest is **manual override after a drawdown**. Both are pre‑committed against in `BEHAVIORAL_PRECOMMIT.md`.
- This is RETIREMENT money in a Roth IRA. You can't withdraw gains until 59½ without a 10% penalty. Don't deploy capital you'll need before then.

The patient version of this is the version that doesn't blow up.
