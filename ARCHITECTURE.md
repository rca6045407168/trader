# trader — Architecture, Strategy, Process, Mechanism

**Status:** Frozen at v4.0.0 (2026-05-07). Personal momentum sandbox on Alpaca paper. No edge claimed. Engineering preserved for personal reference.

This document is the canonical reference for the trader project. It is comprehensive by design — written as a single source of truth so that the project can be understood without reading code, and so that future-me (or anyone reading after the disposition) finds the answer here instead of relitigating it.

**This is the last document this repo will accept.** Per the v4.0.0 stop-rule, no new docs except this one. The README is two sentences and points here.

**SUNSET (v4.1.0, 2026-05-08).** All 11 launchd daemons unloaded + plists removed from `~/Library/LaunchAgents/`. Dashboard Docker container stopped. Path C executed cleanly per the v4.0.0 disposition spec. The repo is no longer operational — it is a static reference. Existing paper positions on Alpaca remain where they are; nothing is liquidated. Future commits to this repo are restricted to security patches on pinned dependencies and deletions only. See §11.5.

---

## Table of contents

1. [What this is, what it isn't](#1-what-this-is-what-it-isnt)
2. [Honest caveats — read this before anything else](#2-honest-caveats--read-this-before-anything-else)
3. [Version history & the v4.0.0 disposition](#3-version-history--the-v400-disposition)
4. [System architecture](#4-system-architecture)
5. [Strategy](#5-strategy)
6. [Process — what fires when](#6-process--what-fires-when)
7. [Mechanism — how each layer works](#7-mechanism--how-each-layer-works)
8. [Observability — journal schema & dashboard](#8-observability--journal-schema--dashboard)
9. [Failure modes & how the system halts](#9-failure-modes--how-the-system-halts)
10. [Day-to-day operation](#10-day-to-day-operation) — *historical; daemons are stopped*
11. [The disposition — what survives, what was deleted, sunset record](#11-the-disposition--what-survives-what-was-deleted)
12. [Glossary](#12-glossary)

---

## 1. What this is, what it isn't

**What it is.** A long-only top-15 momentum strategy operated on an Alpaca paper account. Universe is a hand-picked 43-name set of US large-caps (`src/trader/sectors.py`). Signal is 12-1 cross-sectional momentum (12-month return excluding the most recent month). Allocation is min-shift-weighted at 80% gross with an 8% single-name cap and 25% sector cap. Daily orchestrator runs once per trading day, journals every decision, reconciles against the broker, and writes a snapshot. The dashboard is a Streamlit viewer of the paper account.

**What it isn't.**
- It is not a fund. It is not seeded with live capital.
- It is not a system with demonstrated alpha — see §2.
- It is not iterating. It is frozen. The version is v4.0.0 + two stop-rule bypasses, and no further feature work is planned.
- It is not the right place to learn how to build a trading system from scratch — there is too much vestigial scaffolding from the v3.x build that did the iterating, and the strategy doesn't beat its own naive baseline.

**What survives the freeze, intentionally.** The engineering wins, separable from the strategy: cross-validation harness, journal replication discipline, source-spot-check pattern against LLM hallucination, launchd sleep-resilience pattern, the dashboard as a viewer. Those generalize; the strategy doesn't.

---

## 2. Honest caveats — read this before anything else

Every one of these is documented in `git log` and was surfaced by the user's own critique of the project. They are stated here at the top because they are the ones that determine whether anything else in this document matters.

**The strategy does not beat its own naive baseline on a risk-adjusted basis.** `naive_top15_12mo_return` (top-15 by raw 12-month return, no skip, equal-weighted at 80% gross, no caps) has an α-IR of 0.60. The LIVE variant (`xs_top15_min_shifted`, with skip, min-shift weighting, single-name cap, sector cap, deployment-anchor gate, VIX gate, regime overlay, drawdown protocol) has an α-IR of 0.46. The complexity stack costs ~14 IR points. The LIVE variant has more cumulative alpha (+25.6 vs +19.1pp) but that lead is consistent with β-amplification on a friendly window, not skill.

**The 25-year backtest is on a survivorship-biased universe.** The 43-name universe is, by construction, the set of names that survived to 2026. Running a winner-picking factor (12-1 momentum) on a panel of ultimate winners double-counts the selection. The often-cited "$1 → $54.73 vs SPY's $10.53" number is computed against this contaminated universe. A v0 fix added four GFC casualties (AIG, FNMA, FMCC, C) to the panel and showed the strategy correctly rotated out before their 2008 collapse — useful but gestural. The structural fix requires CRSP/Compustat point-in-time constituents, which the project does not have.

**The live book runs at β ≈ 1.7. The backtest assumed β ≈ 0.90.** The realized factor exposure differs materially from the modeled one. Concentration into AI-cycle tech (28-30% sector pre-cap, four of six top positions) is doing it. The headline "+77pp vs SPY" decomposes to roughly +52pp leveraged-tech-beta and +25pp residual α — still positive, but the cum-active number on its own is the wrong one.

**The strategy lost two of the five regime windows it was tested in.** GFC -44.9pp active. COVID -6.7pp active. Both losses were caused by recovery-whipsaw: 12-1 momentum points at yesterday's leaders (defensives that held up in 2008) precisely when tomorrow's leaders (cyclicals that bounce hard in 2009) need to be picked. A drawdown-based recovery detector (`xs_top15_dd_recovery_reduced_gross`) shipped as a SHADOW candidate in v3.73.28 — fires correctly during the GFC, response was "cut gross to 40%" rather than chase a different signal — but it is single-window evidence (4 fires in 25 years, all GFC) and explicitly fitted to the regime it was designed for.

**The drawdown protocol has never fired on real money in any direction.** It was in ADVISORY mode for most of the system's life, flipped to ENFORCING in paper at v3.73.25, flipped back to ADVISORY at v4.0.0 because there was no real DD to enforce against and the synthetic canary was theatre of discipline. The math has been tested under controlled conditions (synthetic -13% snapshots → ESCALATION → 30% gross). Live behavior under stale prices, partial fills, T+1 settlement, an asleep laptop, and an actual market panic is a different claim than the synthetic canary tests.

**The reactor (8-K signal layer) is one signal that was wrong.** 14 signals fired across its operational life, 1 was severity M3, the M3 was BEARISH on INTC, the market priced it BULLISH (+13.5% on the day, +40pp 5d alpha vs SPY). It stays in SHADOW and the in-process daily-eval hook was removed at v4.0.0. The cost-benefit framing the writeups gave ("$0.17 lifetime LLM cost vs $160 of hypothetical equity protection") was a category error — the protection is hypothetical and contingent on the signal predicting, which is exactly what's unverified.

**Tests passing is not strategy validation.** 810 tests green at v4.0.0 verify code does what the code claims. They cannot verify that what the code says matches what the market does. The cross-validation harness has caught real bugs (sqrt(252) IR overstatement, MultiIndex dashboard crash, warmup-drag in backtest accounting) — that is real engineering value. It is not edge.

---

## 3. Version history & the v4.0.0 disposition

### v3.x — the build years

v3.x went from v3.0.0 to v3.73.28 across roughly six months on a paper account. Key shipping points:

- **v3.6** — variant registry as source of truth. Caught a silent drift where production was running top-5 while metadata claimed top-3.
- **v3.46** — daily-loss freeze + deployment-anchor DD gates.
- **v3.58** — DrawdownCircuitBreaker (separate -10% all-time-peak halt independent of the v3.46 deployment-anchor gates).
- **v3.73.0** — heartbeat alert (caught silent cron failures; one missed Monday in the journal).
- **v3.73.2** — four-threshold drawdown protocol (GREEN/YELLOW/RED/ESCALATION/CATASTROPHIC).
- **v3.73.7** — strategy_eval harness; 28 candidate strategies tracked in shadow.
- **v3.73.13** — cross-validation harness; caught warmup-drag + sqrt(252) IR bugs.
- **v3.73.15** — β-adjusted alpha leaderboard.
- **v3.73.17** — sizing primitives (vol target overlay, max loss check, inverse-vol weighting).
- **v3.73.20** — SP500-beat CI assertion (the test that fails CI if LIVE stops beating SPY on the long window). *Removed at v4.0.0 — the long-window number is contaminated.*
- **v3.73.21** — drawdown protocol wired into orchestrator (still ADVISORY by default).
- **v3.73.25** — ENFORCING flipped in paper; 30-run clock counter.
- **v3.73.26** — weekly synthetic-DD canary.
- **v3.73.27** — tier-by-tier canary coverage; stale-data halt finally implemented.
- **v3.73.28** — drawdown-recovery + reduced-gross response shipped as SHADOW candidate.

### v4.0.0 — the disposition

In the v4.0.0 disposition, after a calibrated harsh review by the user, the project recognized that:

- The framing was incoherent (oscillating between "craftsman's bench" and "capital-deployment system with Tier-0 gates")
- The strategy was barely defensible on its own data (naive baseline beat it on IR)
- The 25-year backtest was uninterpretable (survivor-biased universe)
- The 28 versioned point-releases were the incoherence in numerical form
- The honesty in the writeups was doing the work of the critic — graceful concession in lieu of action

The disposition collapsed the project to a **personal momentum sandbox** framing. Specifically:

- **Deleted**: SP500-beat CI assertion, 30-run ENFORCING-mode clock, weekly synthetic-DD canary, the 38-page-PDF generator, 60+ docs, 17 dashboard apparatus views (~2,682 lines), 60+ research scripts, daily eval-harness hook, ENFORCING flag in paper.
- **Renamed nothing.** No version-bump theatre.
- **Surfaced the engineering wins**: cross-validation harness, journal replication, source-spot-check pattern, launchd sleep-resilience, the dashboard as a viewer.
- **Tagged v4.0.0** with a stop-rule: future commits restricted to security patches on pinned deps, deletion of code, or bug fixes required to keep journal/replication daemons honest. Any violation = path C (sunset).

### v4.0.1 — viewer-honesty fix (defensible bypass)

v4.0.1 fixed a real bug: the dashboard headline (`TOTAL RETURN +9.81% vs SPY +6.23%`) was reading end-of-prior-day journal snapshots and serving them through a 10-minute Streamlit cache, so it lied mid-session about the actual broker state. Real fix: `compute_performance()` accepts `live_equity` and the dashboard injects today's broker value on every click; cache TTLs cut from 600s to 30s; `"As of <timestamp>"` caption added below the headline. Argued under the spec as a viewer-honesty bugfix. Spec-compliant.

### 0bc70ca — token-cost readout (explicit stop-rule bypass)

A conversation token-cost caption was added to `view_chat`. ~28 lines. Zero new dependencies. No tag. Honestly a new feature, not a bugfix. Shipped under explicit user override ("keep going" → "take care of all the blockers with your best judgement"). Recorded as the **last** stop-rule bypass without explicit sunset acknowledgment. Future "implement X for the trader" requests get re-flagged to path C.

---

## 4. System architecture

### 4.1 Data flow

```
     yfinance (price history)            Alpaca paper (broker, live equity)
          │                                       │
          ▼                                       ▼
   ┌────────────┐                          ┌──────────────┐
   │ data.py    │ parquet cache            │ execute.py   │ TradingClient
   │ fetch_     │  (CACHE_DIR/*.parquet)   │ get_client() │
   │ history()  │                          └──────┬───────┘
   └─────┬──────┘                                 │
         │                                        │
         ▼                                        ▼
   ┌──────────────────────────────────────────────────────┐
   │ main.py — daily orchestrator                         │
   │   1. start_run(run_id)                               │
   │   2. kill-switch pre-flight (manual halt, missing    │
   │      keys, equity loss, stale data)                  │
   │   3. reconcile(client) → halt on drift               │
   │   4. close aged bottom-catch positions               │
   │   5. build_targets(universe) → momentum picks +      │
   │      bottom-catches (legacy, USE_DEBATE=false)       │
   │   6. portfolio_caps.apply (8% name / 25% sector)     │
   │   7. sizing.apply_vol_target (overlay)               │
   │   8. risk_manager.apply_drawdown_protocol            │
   │      (currently ADVISORY)                            │
   │   9. risk_manager.check_account_risk (kill, freeze,  │
   │      v3.58 breaker, deployment-anchor gates)         │
   │  10. validate_targets (sanity)                       │
   │  11. submit orders / log_decision / open_lot         │
   │  12. write daily_snapshot (equity, cash, positions)  │
   │  13. finish_run(run_id)                              │
   └──────────────────┬───────────────────────────────────┘
                      │
                      ▼
              ┌───────────────┐         ┌──────────────────┐
              │ journal.db    │ ──────▶ │ replicate_       │ ─→ iCloud Drive
              │ (SQLite)      │  nightly│ journal.sh       │    backup
              │               │         └──────────────────┘
              │  daily_       │
              │  snapshot     │         ┌──────────────────┐
              │  runs         │ ──────▶ │ dashboard.py     │ Streamlit
              │  decisions    │  read   │ (viewer only)    │ :8501
              │  position_    │ -only   └──────────────────┘
              │  lots         │
              │  strategy_    │         ┌──────────────────┐
              │  eval (frozen)│ ──────▶ │ check_daily_     │ heartbeat
              │               │  read   │ heartbeat.py     │ alert
              └───────────────┘  -only  └──────────────────┘
```

### 4.2 Module map (post-v4.0.0)

| Layer | Module | Role |
|---|---|---|
| **Config** | `config.py` | env loading via dotenv, single source of truth for `DRY_RUN`, `TOP_N`, `LOOKBACK_MONTHS`, model strings, etc. |
| **Data** | `data.py` | yfinance wrapper with parquet disk cache. `fetch_history()` is the canonical price source. |
| **Universe** | `sectors.py` / `universe.py` | 43-name `SECTORS` dict (sector tags), `DEFAULT_LIQUID_50` list. |
| **Signal** | `signals.py` | `momentum_score(series, lookback, skip)` — N-month return excluding skip-month. |
| **Strategy** | `strategy.py` | `rank_momentum(universe, top_n)` — produces ranked candidate list. |
| **Variants** | `variants.py` / `ab.py` | A/B variant registry. `get_live()` returns the LIVE variant function. |
| **Eval (frozen)** | `eval_strategies.py` / `eval_runner.py` | 28 candidate strategies. Modules retained for ad-hoc research. **Daily orchestrator hook removed at v4.0.0** — these no longer run on a schedule. |
| **Risk** | `risk_manager.py` | Drawdown protocol tiers, `apply_drawdown_protocol`, `check_account_risk`. |
| **Risk circuit** | `v358_world_class.py` | DrawdownCircuitBreaker — separate all-time-peak -10% halt independent of the deployment-anchor gates. |
| **Risk gate** | `deployment_anchor.py` | -25% / -33% from-deployment freeze + liquidation gates. |
| **Risk overlay** | `sizing.py` | Realized vol → target vol scaling, max-loss-per-name pre-trade gate. |
| **Caps** | `portfolio_caps.py` | 8% single-name + 25% sector with cap-aware redistribution. |
| **Reactor (SHADOW)** | `reactor_rule.py` | 8-K severity/direction signal → bounded trim. Runs but doesn't execute. |
| **Reconciliation** | `reconcile.py` | broker positions vs journal positions. Halts orchestrator on drift. |
| **Kill switch** | `kill_switch.py` | manual halt flag, missing keys, equity loss thresholds, **stale-data halt** (v3.73.27). |
| **Equity state** | `equity_state.py` | `get_equity_state()` — live broker → journal → briefing fallback chain. |
| **Journal** | `journal.py` | SQLite. `start_run`/`finish_run`/`open_lot`/`close_lots_fifo`/`recent_snapshots`/`log_decision`/`log_order`. |
| **Execution** | `execute.py` | Alpaca client + order submission. |
| **Orchestrator** | `main.py` | The daily run. ~800 lines, this is where everything composes. |
| **Analytics** | `analytics.py` | `compute_performance()` — Sharpe / Sortino / max DD / IR / β. **`live_equity` parameter (v4.0.1) injects today's broker value into the series.** |
| **Chat (peripheral)** | `copilot.py` | View_chat backend — Anthropic SDK passthrough with 10 read-only tools (recent positions, journal queries, etc.). |

### 4.3 What's running on the laptop right now

Four launchd plists in `infra/launchd/` (mirrored to `~/Library/LaunchAgents/`):

| Job | Schedule | What it does |
|---|---|---|
| `com.trader.daily-run` | weekdays 13:10 UTC | Orchestrator main loop. Picks targets, submits orders, journals. |
| `com.trader.daily-heartbeat` | weekdays 14:30 UTC + 30min interval | Reads journal, alerts if today's run row is missing. |
| `com.trader.earnings-reactor` | hourly | SEC EDGAR poll for 8-K filings on live-book names. SHADOW. |
| `com.trader.journal-replicate` | nightly | sqlite3 .backup → iCloud Drive. |

Deleted at v4.0.0: `com.trader.weekly-enforcing-canary` (theater of discipline).

All plists pair `StartCalendarInterval` with `StartInterval` for sleep-resilience — a documented launchd lesson from May 2026 (silent cron failure during sleep is the top operational-risk blindspot).

The Streamlit dashboard runs as `trader-dashboard` Docker container on port 8501. Rebuild with `bash scripts/build_dashboard.sh`.

---

## 5. Strategy

### 5.1 The LIVE variant

`xs_top15_min_shifted` in `src/trader/eval_strategies.py`. Registered in `variants.py` as the production source of truth — the orchestrator gets its picks from `get_live().fn(...)`, not from a separately-tuned set of constants.

```python
def xs_top15_min_shifted(asof, prices):
    p = _stock_panel(prices)              # 43 names, ETFs filtered out
    p = p[p.index <= asof]
    if len(p) < 252: return {}            # need 1y history
    scored = []
    for sym in p.columns:
        s = p[sym].dropna()
        m = momentum_score(s, 12, 1)      # 12-month return, skip 1 month
        if not pd.isna(m):
            scored.append((sym, float(m)))
    scored.sort(key=lambda x: -x[1])
    top15 = scored[:15]
    if not top15: return {}
    min_s = min(s for _, s in top15)
    shifted = [(t, s - min_s + 0.01) for t, s in top15]
    total = sum(s for _, s in shifted)
    return {t: 0.80 * (s / total) for t, s in shifted}
```

Three decisions baked in:

1. **Top-15 by 12-1 momentum.** Cross-sectional ranking. 12-month lookback, skip the most recent month (Jegadeesh-Titman convention; the most recent month is noisy and partly mean-reverts).
2. **Min-shift weighting.** Subtract the worst score in the top-15, add a floor (0.01), normalize to 80% gross. Result: the top name gets meaningfully more weight than the bottom name, but the bottom-of-the-top-15 doesn't get crushed to ~0%. Compromise between equal-weight (loses information) and pure-score-weight (over-concentrates on the leader).
3. **80% gross.** Reserves 20% cash for the cap-aware redistribution + drawdown protocol response margins.

### 5.2 The complexity stack on top

Layered on top of the LIVE variant's targets, in this order:

```
base 80% × deployment-anchor × VIX-gate × regime-overlay × drawdown-protocol = effective gross
```

At time of writing (frozen state), effective ≈ 80% × 0.85 (VIX gate at VIX=17.4) ≈ 68%. The other multipliers are 1.0 in the current regime.

- **Deployment-anchor gate.** Reads `data/deployment_anchor.json`. If equity is -25% from the deploy-day equity, freeze new positions for 30 days. -33% triggers a written-postmortem-required liquidation gate.
- **VIX gate.** A gentle vol-scaler. Multiplies gross by a function of VIX. Does *not* go to zero — agent-2 institutional review explicitly flagged that aggressive VIX cuts ("VIX>40 → 50% gross") cut at panic lows; the gentle version is the current behavior.
- **Regime overlay.** Optional HMM-based regime classifier. Default OFF (`USE_REGIME_FILTER=false`). When ON, reduces gross during "distress" regime. Disabled because the user's review found it added noise without alpha.
- **Drawdown protocol.** Five tiers, mechanical responses. Fires when the 180-day-peak DD exceeds a threshold. See §7.4. Currently ADVISORY (warns but doesn't mutate targets); flipped briefly to ENFORCING in v3.73.25 then back at v4.0.0.

### 5.3 The complexity tax — the honest finding

The project shipped 28 candidate strategies in shadow over six months. The eval harness writes a row to `strategy_eval` for every candidate on every rebalance and settles forward returns over the next month. The β-adjusted leaderboard (sorted by `cum_alpha_pct`) showed the LIVE variant in the middle of the pack, not the top.

**Top of the leaderboard (most recent shadow run before v4.0.0 froze the daily hook):**

| Strategy | α-IR | cum-α (5y) | β | Notes |
|---|---:|---:|---:|---|
| `naive_top15_12mo_return` | 0.60 | +19.1pp | 1.05 | Top-15 by raw 12-month return. No skip. Equal-weight. No caps. |
| `xs_top15_capped` | 0.55 | +22.8pp | 0.97 | LIVE picks but uniformly capped at 8% (no min-shift) |
| `xs_top15_min_shifted` (LIVE) | 0.46 | +25.6pp | 0.90 | The production variant. |
| ... | | | | |
| `buy_and_hold_qqq` | 0.39 | +8.4pp | 1.10 | passive baseline |
| `buy_and_hold_spy` | 0.00 | 0.0 | 1.00 | reference |

The naive variant exists as a candidate (not as a deletion target) precisely so this finding stays visible in the data. **The honest reading is that LIVE harvests the same factor as the naive variant in a more complicated way, and the complications cost ~14 IR points.** The cum-α lead the LIVE variant has is consistent with β-amplification on a friendly window.

The v4.0.0 disposition removed the SP500-beat CI test that asserted LIVE beats SPY on long-window data, because the long-window number runs on the survivor-biased universe and was load-bearing under a contaminated assumption.

### 5.4 What was tried and didn't ship

All surfaced via the eval harness. None promoted from SHADOW:

- `vertical_winner` — concentrated top-3. Better cum-α but with double the max DD; not worth it.
- `xs_top15_vol_targeted` — vol-scale to 18% target. Lowered IR by adding lag.
- `score_weighted_vol_parity` — inverse-vol within score-weighting. Marginal change vs LIVE.
- `long_short_momentum` — long top-15 / short bottom-5 / 40% net. Net positive but adds rule-of-thumb leverage costs the project doesn't model.
- `xs_top15_recovery_aware` (VIX-based) — 12-1 → 6-1 lookback when recovery detected. Detector failed to fire during GFC (VIX never crossed back below 25 during the actual recovery turn).
- `xs_top15_dd_recovery_aware` — drawdown-based detector. Fires correctly during GFC (4 times). Response (6-1 momentum) made GFC P&L *worse* by -1.24pp vs LIVE.
- `xs_top15_dd_recovery_reduced_gross` — same detector, different response (cut gross 80%→40% when fires). Improves GFC by +1.15pp. Promoted to SHADOW in v3.73.28. **Still not LIVE — it's single-window evidence; the threshold was fitted to the regime it was designed for.**

---

## 6. Process — what fires when

### 6.1 The daily run

Fires weekdays at 13:10 UTC (08:10 ET). About 30 min before US market open. ~2-5 minutes typical runtime.

1. **start_run** — write a `'started'` row to `runs` table with today's `run_id`. Idempotency guard: a second daily run for the same date returns False unless the run_id ends with `-FORCE` (`python -m trader.main --force` after a halt).
2. **kill-switch pre-flight** (`kill_switch.check_kill_triggers`):
    - manual halt flag at `/tmp/trader_halt`
    - missing `ALPACA_API_KEY` (or missing `ANTHROPIC_API_KEY` while `USE_DEBATE=true`)
    - 7-day equity loss > 10% / 30-day equity loss > 20% / 30-day-peak DD > 15%
    - **stale data: SPY's most recent close is more than 3 business days old** (v3.73.27)
    - bypass via `SKIP_DATA_FRESHNESS_CHECK=true` for offline tests
3. **reconciliation** (`reconcile.reconcile`):
    - compare broker positions to journal positions
    - halt if matched < expected, missing > 0, unexpected > 0, or size_mismatch > 0
    - `alert_halt()` fires Slack + email with structured detail
4. **close aged bottom-catches** (legacy V4 sleeve) — close any bottom-catch position aged >20 trading days. Currently inert because `USE_DEBATE=false`.
5. **build_targets** — call the LIVE variant function. Get top-15 + min-shift weights. Journal each pick via `log_decision`.
6. **caps** — `portfolio_caps.apply_caps` redistributes anything above 8% single-name or 25% sector to the next-ranked names that have headroom.
7. **vol-target overlay** — if `VOL_TARGET_ENABLED=true` and realized portfolio vol > target, scale all weights by `target / realized` (capped at 1.0; never levers up).
8. **drawdown protocol** — `apply_drawdown_protocol(equity, targets, snapshots, momentum_ranks)` evaluates the 180-day-peak DD, picks the tier, and (if `DRAWDOWN_PROTOCOL_MODE=ENFORCING`) mutates targets per the tier's action. Currently `ADVISORY` — warns and journals the tier but leaves targets unchanged.
9. **check_account_risk** — composite gate: daily-loss freeze, v3.58 circuit breaker, deployment-anchor gates, sector cap re-check.
10. **target validation** — sanity (no negative weights unless long-short variant, no NaN, total ≤ 95% gross).
11. **submit orders** — diff target weights against current positions, generate buys/sells, route through Alpaca. Each open writes a `position_lots` row; each close runs FIFO.
12. **log_order** — write each order's status/error to `decisions` + `orders`.
13. **daily_snapshot** — write current equity, cash, positions JSON, SPY close.
14. **finish_run** — flip the `runs` row to `'completed'` (or `'halted'` / `'failed'`).
15. **(removed at v4.0.0)** strategy_eval daily hook. The 28 candidates no longer run on the schedule. Modules remain importable for ad-hoc research.

### 6.2 Hourly / minutely cadences

- **Earnings reactor poll** — hourly. SEC EDGAR for 8-K filings on live-book names. Claude-tagged severity (M1-M3) + direction. SHADOW: signals journaled to `earnings_signals`, the trim rule does not execute.
- **Daily heartbeat** — weekdays 14:30 UTC + 30min interval (sleep-resilience). Reads `runs` table, fires alert if today's run row is missing.
- **Intraday risk watch** (optional) — `run_intraday_risk_watch.py`. Daemon-mode: polls live equity, alerts if intraday DD breach.
- **Slippage tracker** — `realized_slippage_tracker.py`. Per-fill slippage measurement.

### 6.3 Weekly / nightly

- **Journal replicate** — nightly `sqlite3 .backup` to iCloud Drive. Transactionally consistent.
- **Cross-validation harness** (`cross_validate_harness.py`) — manual / ad-hoc. Replays the LIVE variant against the journal's recorded picks, asserts the production code path and the backtest code path agree on the same universe + same lookback. Caught the v3.6 silent drift.

### 6.4 Manual operator paths

- `python scripts/halt.py arm "reason"` — touch `/tmp/trader_halt`. Next daily run halts.
- `python scripts/halt.py disarm` — remove the flag.
- `python scripts/resync_lots_from_broker.py` — rebuild `position_lots` from current broker positions. Use after a journal-broker drift that's been investigated and accepted.
- `python scripts/run_reconcile.py` — one-shot reconcile, no orchestrator side-effects.
- `python -m trader.main --force` — bypass the daily idempotency guard. Use after a halt has been resolved and you want to run the same date again.
- `bash scripts/build_dashboard.sh` — rebuild the dashboard Docker container. Required after any `dashboard.py` or `analytics.py` edit, because the running container holds its own copy.

---

## 7. Mechanism — how each layer works

### 7.1 Universe selection

`SECTORS` in `src/trader/sectors.py` is a hand-curated dict of 43 US large-caps with GICS-ish sector tags. The LIVE variant uses `DEFAULT_LIQUID_50` (a list of those tickers) as its universe.

The universe is biased by survivorship — by construction, every name was alive in 2026. A `time_versioned_universe_v0` script existed (deleted at v4.0.0) that augmented this with four fetchable GFC casualties (AIG, FNMA, FMCC, C). The full graveyard (LEH, BSC, WAMU, WB, NCC, WCOM, ENE) requires CRSP-grade data the project doesn't have.

### 7.2 Momentum scoring

`signals.momentum_score(series, lookback_months=12, skip_months=1)`:

```
score = (price[t - skip] / price[t - lookback]) - 1
```

Returns `NaN` if the series is too short. The LIVE variant uses `(12, 1)` — Jegadeesh-Titman convention.

### 7.3 Min-shift weighting

Given top-15 names with scores `s_1 > s_2 > ... > s_15`:

```
m = min(s_i) for i in top-15
shifted_i = (s_i - m) + 0.01     # 0.01 = floor so the worst doesn't go to 0
weight_i = 0.80 * shifted_i / sum(shifted_j)
```

Design goal: the top name gets meaningfully more weight than the bottom, but the bottom doesn't collapse. Tested concentration: typical max single-name weight pre-cap is 14-18%; the 8% cap binds and redistributes via `portfolio_caps`.

### 7.4 Drawdown protocol — the five tiers

Thresholds against the 180-day-peak DD (decimal, negative):

| Tier | DD threshold | Action enum | What it does in ENFORCING mode |
|---|---|---|---|
| **GREEN** | DD > -5% | NONE | Targets unchanged. |
| **YELLOW** | -8% < DD ≤ -5% | PAUSE_GROWTH | Warning + suggestion to skip rebalance. Targets currently unchanged (TODO: thread current_weights through call signature). |
| **RED** | -12% < DD ≤ -8% | HALT_ALL | The existing -8% kill in `check_account_risk` halts the run before this tier's enforcement runs. Informational here. |
| **ESCALATION** | -15% < DD ≤ -12% | TRIM_TO_TOP5 | Keep top-5 names by momentum, drop ranks 6-15, rescale to **30% gross** (cash 70%). |
| **CATASTROPHIC** | DD ≤ -15% | LIQUIDATE_ALL | Set all targets to 0.0. Manual re-arm only after 30-day cool-off + external review. |

Mode flag: `DRAWDOWN_PROTOCOL_MODE` env. `ADVISORY` (default) journals the tier in warnings + dashboard, doesn't mutate. `ENFORCING` mutates. Currently ADVISORY.

### 7.5 Cap-aware redistribution

`portfolio_caps.apply_caps(targets, max_per_name=0.08, max_per_sector=0.25)`:

1. Start with min-shift weights.
2. For each name above 8%: clip to 8%, redistribute the excess to the next-ranked names in the same sector that have headroom.
3. For each sector above 25%: clip to 25%, redistribute the excess to the next-ranked names in *other* sectors that have headroom.
4. Re-normalize to the original gross.

The redistribution is rank-aware — it doesn't equal-weight; it pushes capacity to the next-best name by score.

### 7.6 Reconciliation

`reconcile.reconcile(client)` returns:

```python
{
  "matched": int,              # journal qty == broker qty
  "missing": list[dict],       # in journal, not at broker
  "unexpected": list[dict],    # at broker, not in journal
  "size_mismatch": list[dict], # in both but different qty
  "awaiting_fill": list[dict], # journal-pending, treated as matched
  "halt_recommended": bool,
  "summary": str,
}
```

The orchestrator halts the run if `halt_recommended` is True, alerts via `alert_halt()`, and refuses to submit orders until the drift is resolved (typically via `resync_lots_from_broker.py`).

A known gotcha: pending-buy orders look like "missing" until they fill. v3.52 added `awaiting_fill` to handle this — orders submitted in the last 5 minutes that haven't filled yet are treated as matched, not missing.

### 7.7 Stale-data halt (v3.73.27)

`kill_switch._check_data_freshness()` was a documented-but-never-implemented gap until v3.73.27. The `kill_switch` module's header docstring claimed since day one that it halts on yfinance stale data; the check did not exist.

v3.73.27 closed that gap with a positive-confirmation check: fetch SPY's latest close, count business days between latest data and today, halt if more than 3. Bypass: `SKIP_DATA_FRESHNESS_CHECK=true` for offline tests / weekend backfills.

### 7.8 Reactor (8-K signal layer, SHADOW)

`reactor_rule.ReactorSignalRule` reads from `journal.earnings_signals`. The reactor proper is the daemon `scripts/earnings_reactor.py` which:

1. Polls SEC EDGAR for 8-K filings on live-book names
2. Fetches the filing text
3. Runs Claude (sonnet-4-6) on the filing with a structured-output schema asking for `severity` (M1-M3) and `direction` (BULLISH/BEARISH/NEUTRAL)
4. Logs to `earnings_signals`

The rule (`ReactorSignalRule.compute_trims`) reads recent BEARISH M3 signals and proposes trims to 50% of current weight. The orchestrator calls the rule but **doesn't apply the trims** — they're journaled and forward-return-validated against the next week's price action.

**Empirical record**: 14 signals across the operational life. 1 was M3. The M3 was BEARISH on INTC's $6.5B debt-raise filing. The market priced it BULLISH (+13.5% on the day, +40pp 5d alpha vs SPY). The rule stayed SHADOW after this and the v4.0.0 disposition removed the daily-eval hook. The reactor module is preserved because the source-spot-check pattern (verifying the LLM's claim against the filing text) is itself an engineering win independent of whether the signal predicts.

### 7.9 Source-spot-check (LLM hallucination defense)

`scripts/spotcheck_reactor.py`. For each reactor signal, re-fetches the filing and verifies that the structured fields Claude returned are actually grounded in the filing text:

- The cited dollar amounts appear verbatim in the filing
- The cited dates exist
- The summary contains keywords from the filing's first 500 chars

Fails any signal that doesn't pass. Pattern is generalizable beyond filings — anywhere LLM structured output makes claims that should be groundable.

### 7.10 Cross-validation harness (v3.73.13)

`scripts/cross_validate_harness.py`. Walks the journal's recorded picks for the last N rebalances and asserts:

1. The production code path (`rank_momentum` + the LIVE variant function) and the backtest code path agree on the picks for each rebalance (caught the v3.6 silent drift).
2. The forward returns the eval harness recorded match a fresh re-computation from the price panel (caught a warmup-drag bug in backtest accounting).
3. The annualized IR uses `sqrt(12)` for monthly returns, not `sqrt(252)` (caught the v3.73.13 IR overstatement).

This harness is the engineering crown jewel — it catches measurement drift the way the canary was supposed to catch behavioral drift.

---

## 8. Observability — journal schema & dashboard

### 8.1 SQLite schema (post-v4.0.0)

`data/journal.db`. Survives across runs. Replicated nightly to iCloud Drive.

| Table | Purpose | Key fields |
|---|---|---|
| `daily_snapshot` | one row per trading day; the equity ledger | `date`, `equity`, `cash`, `positions_json`, `benchmark_spy_close` |
| `runs` | one row per orchestrator invocation | `run_id`, `started_at`, `completed_at`, `status` ∈ {started, completed, halted, failed} |
| `decisions` | every BUY/SELL/HOLD/SKIP decision the orchestrator made | `ticker`, `action`, `style`, `score`, `rationale`, `final` |
| `orders` | one row per Alpaca order submitted | `order_id`, `symbol`, `side`, `qty`, `status`, `error` |
| `position_lots` | FIFO lots for sleeve-level P&L attribution | `symbol`, `sleeve`, `opened_at`, `qty`, `open_price`, `closed_at`, `close_price`, `realized_pnl` |
| `earnings_signals` | reactor signals (SHADOW) | `symbol`, `accession`, `severity`, `direction`, `journaled_at`, `forward_5d_alpha` |
| `strategy_eval` | candidate picks + settled returns | `asof`, `strategy`, `picks_json`, `period_return`, `spy_return`, `active_return`, `cum_alpha_pct`, `beta`, ... |
| `risk_freeze_state` | persistent state for daily-loss freeze | `freeze_started_at`, `reason` |

### 8.2 Dashboard

Streamlit app at `:8501`. Single Docker container `trader-dashboard`. Build via `bash scripts/build_dashboard.sh`. Rebuild required after any `scripts/dashboard.py` or `src/trader/analytics.py` edit — the container holds its own copy.

Post-v4.0.0 navigation (apparatus views deleted):

- **Top tier**: Overview, Performance, Alerts
- **Portfolio**: Live positions, Decisions, Position lots, Attribution
- **Discovery**: News, Events, Watchlist
- **Diagnostics**: Intraday risk, Slippage, Sleeve health
- **System**: Manual triggers, Manual override, Settings

Deleted: 17 apparatus views (Strategy Lab, P&L Readiness, V5 Sleeves, Validation, Stress Test, Regime, Shadow Signals, Postmortems, Reports, World-Class Gaps, Risk Roadmap, Strategy Leaderboard, Earnings Reactor view, Filings Archive, Screener, Grid).

**Headline freshness (v4.0.1):** the Performance view's TOTAL RETURN headline reads from `compute_performance(window, live_equity=...)` — the `_get_equity_state()` call pulls today's broker equity on every click and injects it into the series. Cache TTL on the headline is 30s (was 600s); a freshness caption beneath the title shows `"As of HH:MM:SS · headline includes live broker equity $X (source: live_broker)"`.

**Chat (peripheral):** `view_chat` is the HANK copilot — Anthropic SDK passthrough with 10 read-only tools (recent positions, journal queries, performance metrics). 0bc70ca added a conversation-token-cost caption: `"🟢 Conversation: ~N tokens / 200,000 context (P%)"` above the chat box.

---

## 9. Failure modes & how the system halts

### 9.1 The halt ladder

In order of precedence (each step fires first):

1. **Manual flag** (`/tmp/trader_halt` exists) — instant halt, no further checks.
2. **Missing required keys** — `ALPACA_API_KEY` absent, or `ANTHROPIC_API_KEY` absent while `USE_DEBATE=true`.
3. **Equity loss thresholds** — 7d loss > 10%, 30d loss > 20%, 30d-peak DD > 15%.
4. **Stale data** — yfinance SPY > 3 business days behind today.
5. **Reconciliation drift** — broker vs journal mismatch.
6. **Daily-loss freeze** (`MAX_DAILY_LOSS_PCT=6%`) — if today's open vs yesterday's close < -6%, freeze for 48h.
7. **180-day-peak DD (-8%)** — existing kill in `check_account_risk`.
8. **v3.58 DrawdownCircuitBreaker** — separate -10% from all-time-peak halt (`DRAWDOWN_BREAKER_STATUS=LIVE`).
9. **Deployment-anchor gates** — -25% from deployment freezes, -33% triggers liquidation gate.
10. **Drawdown protocol** — only mutates targets in ENFORCING mode (currently ADVISORY).
11. **Target validation** — final sanity (negative weights, NaN, > 95% gross).

Any of (1)-(8) returns `{halted: True, reason}` from `main()` and writes `runs.status='halted'`. The heartbeat will not alert (because the run row exists), but Slack/email gets the halt detail via `alert_halt()`.

### 9.2 Operator response by halt type

| Halt type | Right move |
|---|---|
| Manual flag | Inspect `/tmp/trader_halt` content, decide, `halt.py disarm` if resolved. |
| Missing keys | Edit `.env`. |
| Equity loss | Don't run. Wait. Review what happened. Re-arm only after written reason. |
| Stale data | Check yfinance status. If recovered, re-run with `--force`. If persistent, `SKIP_DATA_FRESHNESS_CHECK=true` is the bypass *only if you've confirmed fresh data through another source*. |
| Reconciliation drift | Run `scripts/run_reconcile.py` to inspect. If accepted, `resync_lots_from_broker.py` to rebuild journal. |
| Daily-loss freeze | 48h auto-clear. No action needed unless investigating. |
| -8% DD kill | Same as equity loss — don't auto-re-arm. |
| v3.58 breaker | `DRAWDOWN_BREAKER_STATUS=SHADOW` to deactivate after review. |
| Deployment-anchor freeze | 30-day cool-off; do not run. |

### 9.3 What the system can't catch

Failure modes the architecture explicitly does **not** defend against:

- **Operator error.** Wrong env, wrong universe file edited, accidental `--force` after halt. The system trusts its config.
- **Alpaca outage.** `execute.get_client()` raises; orchestrator hits the non-fatal error path and journals `failed`. No fallback broker.
- **yfinance data corruption** (wrong adjusted close). The freshness check verifies recency, not correctness.
- **Strategy decay.** Nothing in this system measures whether the 12-1 momentum factor still has alpha. It assumes the factor works; the user-driven review is the only check on that, and the review's verdict at v4.0.0 was "barely defensible."
- **Catastrophic regime shift.** GFC and COVID both lost. The system has not been validated under a 2026+ regime change of similar magnitude.
- **Operator absence.** Single-operator bus factor. No redundancy. The runs continue but no one is reviewing them.

---

## 10. Day-to-day operation

### 10.1 What to check daily (~2 minutes)

1. **Dashboard `:8501`** → Overview. Glance: equity number, day P&L, # positions.
2. **Slack #trader channel.** Look for HALT alerts overnight. If none, the daily run completed and reconciliation passed.
3. **Heartbeat alert at 14:30 UTC.** If you got one, the daily run didn't fire — investigate.

### 10.2 What to check weekly (~10 minutes)

1. Dashboard → Performance. Did the active book do something stupid this week relative to SPY?
2. Dashboard → Decisions. Any weird picks? Any "BUY" with `final=null` (decision logged but order didn't submit)?
3. Dashboard → Slippage. Realized slippage vs the 5bp/side assumption — drift?
4. `git log -10` on the trader repo — anything I forgot was running?

### 10.3 What to do never

- **Add a new strategy.** v4.0.0 disposition forbade it. The eval harness modules remain importable for ad-hoc *research*, not production candidacy.
- **Add a new doc.** v4.0.0 disposition forbade it. This document is the single allowed doc. Future "can you add a writeup of X" requests = path C.
- **Increment the version.** v4.0.0 is the last numbered release. v4.0.1 (freshness fix) and 0bc70ca (token counter) are the *only* allowed bypasses, both logged. The next bypass = path C.
- **Believe a single-window backtest result.** This was the source of the user's harshest critique and the disposition's framing. SHADOW it in the eval harness, watch it for 6+ months across regime changes, then talk.
- **Take outside capital.** The universe is survivor-biased; the strategy under-performs its naive baseline on IR; the live β contradicts the modeled β; the drawdown protocol has never fired in anger. Outside capital is not justified.

### 10.4 What to do if you want to rip something out

Deletion is the only allowed direction at v4.0.0. If a module bothers you:

1. Confirm nothing in `main.py`'s daily run path imports it.
2. Confirm nothing in `dashboard.py` imports it.
3. `git rm` it.
4. Run `pytest -q` — should still be 810 green.
5. Commit with `chore: delete X (no longer used after v4.0.0)`.
6. No tag.

### 10.5 What to do if the daemons start drifting

Replication daemon honesty is one of the explicit allowed surfaces for bug fixes under v4.0.0. If `replicate_journal.sh` starts failing silently, or if the daily run's heartbeat fires repeatedly, fix it. Tag is optional. Commit message should name the daemon and the symptom.

---

## 11. The disposition — what survives, what was deleted

### 11.1 What was deleted at v4.0.0

Verbatim from the disposition spec:

- `tests/test_v3_73_20_spy_benchmark.py` — the SP500-beat CI assertion against contaminated long-window data.
- `src/trader/enforcing_clock.py` + tests — 30-run gate counter.
- `scripts/weekly_enforcing_canary.py` + tests + plist + log — synthetic-DD canary.
- `infra/launchd/com.trader.weekly-enforcing-canary.plist` — unloaded from launchctl.
- `scripts/generate_system_writeup.py` + 6 versioned `TRADER_SYSTEM_WRITEUP_*.pdf` — the 38-page-PDF apparatus.
- 60+ `docs/*.md` (DUE_DILIGENCE, MEASUREMENT_AUDIT, GFC_POSTMORTEM, ROUND_2_SYNTHESIS, RICHARD_ACTION_ITEMS, GO_LIVE_CHECKLIST, RISK_FRAMEWORK, V5_ALPHA_DISCOVERY_PROPOSAL, the entire docs tree).
- 17 dashboard apparatus views (~2,682 lines).
- 60+ research scripts.
- Daily eval-harness hook in `main.py` — modules retained for ad-hoc.
- 96 apparatus tests across 22 files.
- `DRAWDOWN_PROTOCOL_MODE` flipped `ENFORCING` → `ADVISORY`.
- README replaced with 5 sentences pointing here.

### 11.2 What survives

- **Cross-validation harness** (`scripts/cross_validate_harness.py`) — engineering crown jewel.
- **Journal replication** (`scripts/backup_journal.py` + `scripts/replicate_journal.sh`) — the data is the ledger; protect it.
- **Source-spot-check pattern** (`scripts/spotcheck_reactor.py`) — generalizable LLM-hallucination defense.
- **Launchd lessons** (in module docstrings) — `StartCalendarInterval` + `StartInterval` + `RunAtLoad` for sleep-resilience.
- **Eval harness modules** (`eval_strategies.py` + `eval_runner.py`) — ad-hoc only, no schedule.
- **The dashboard** (`scripts/dashboard.py`) — viewer only, apparatus views removed.
- **The orchestrator** (`src/trader/main.py`) — the daily run path.
- **Risk modules** — `risk_manager.py`, `kill_switch.py`, `v358_world_class.py`, `deployment_anchor.py`, `sizing.py`, `portfolio_caps.py`. Drawdown protocol stays in code; just not enforcing.
- **All four production daemons** — daily-run, daily-heartbeat, earnings-reactor, journal-replicate.
- **810 tests** — verify code does what code claims.

### 11.3 Exit criterion (replaces the v4.0.0 stop-rule)

The v4.0.0 stop-rule was: "any commit beyond security patches / deletion / daemon-bugfixes inside 90 days means A failed and you owe yourself C — and you do C without negotiating."

The stop-rule didn't hold. Three bypasses landed inside four weeks (`11f2480` freshness fix, `0bc70ca` token counter, `62d6f39` this document). Each was rationalized as a defensible one-off. That is the same pattern v3.x's 28 versioned point-releases were. A freeze that gets renegotiated three times in four weeks is not a freeze.

**The replacement is an exit criterion, not another stop-rule:**

- **Exit fired on 2026-05-08.** All daemons unloaded, dashboard stopped, repo no longer operational. See §11.5.
- **Reactivation requires v5.0.0** — explicit new disposition document, not a stop-rule bypass.
- **The IR finding stands as the falsification.** `naive_top15_12mo_return` outperformed the LIVE variant on annualized monthly IR (0.60 vs 0.46) over the recorded eval-harness window. The disposition's job was to act on that. The valid responses were: flip LIVE to naive, ablate the complexity component-by-component, or shut down. The disposition picked the third — belatedly, on 2026-05-08, when the daemons stopped. (Caveat: the IR comparison runs on the same survivor-biased panel that killed the long-window CI test in §11.1. The complexity-tax finding is *directional* — the simpler variant beats the complex one — but the *magnitude* lives on the same contaminated data and should not be quoted as a precise number.)
- **No further commits to this repo unless they are: security patches on pinned deps, or deletions.** Documentation additions count as bypasses by the same logic that made `0bc70ca` and `62d6f39` bypasses. The next addition triggers nothing automatic, because there is nothing left running to trigger — but it would be the third costume the project's accumulated to avoid the underlying decision, after "strategy iteration" (v3.x) and "viewer-honesty" (v4.0.x).

The document does not warrant a future v6 freeze framing. The exit happened.

### 11.4 The bypasses on record

| Commit | Tag | What it shipped | Defense |
|---|---|---|---|
| `c0a8100` | `v4.0.0` | The disposition itself | n/a — this is the freeze |
| `f304eea` | (untagged) | Drop `COPY docs/` from `Dockerfile.dashboard` | deletion of code; required to actually rebuild after the docs/ delete |
| `11f2480` | `v4.0.1` | Dashboard freshness fix | viewer-honesty bugfix; the headline was lying mid-session |
| `0bc70ca` | (untagged) | View_chat token-cost caption | **Honestly a new feature.** Shipped under explicit user override. Logged as the *last* bypass without explicit sunset acknowledgment. |
| (this commit) | (untagged) | This document | The single allowed doc. Replaces what would otherwise be many docs. |

The next non-deletion / non-daemon-bugfix commit means path C without negotiating.

### 11.5 Path C executed (v4.1.0, 2026-05-08)

Three bypasses in the post-v4.0.0 window triggered the path-C clause from the disposition spec:

1. `f304eea` — Dockerfile docs/ removal. Defensible deletion. (No-op for path-C count.)
2. `11f2480` (v4.0.1) — Dashboard freshness fix. Argued as viewer-honesty bugfix. (Borderline.)
3. `0bc70ca` — View_chat token-cost caption. Honestly a new feature. (Logged as final bypass.)
4. `<this commit>` — ARCHITECTURE.md. Honestly a new doc. (Tipping point.)

Per the spec: "a v4.0.1 inside 90 days outside this list means A failed and you owe yourself C — and you do C without negotiating." Executed on the user's terse instruction ("do it").

**What actually happened on 2026-05-08:**

- 11 launchd plists unloaded via `launchctl unload` and removed from `~/Library/LaunchAgents/`:
  - `com.trader.daily-run` — the orchestrator
  - `com.trader.daily-heartbeat` — silent-cron-failure detector
  - `com.trader.earnings-reactor` — SHADOW reactor signal poller
  - `com.trader.journal-replicate` — nightly iCloud backup
  - `com.trader.anomaly-scan` — research/apparatus
  - `com.trader.daily-perf-digest` — research/apparatus
  - `com.trader.monday-fill-check` — research/apparatus
  - `com.trader.monthly-dsr-audit` — research/apparatus
  - `com.trader.monthly-walkforward` — research/apparatus
  - `com.trader.research-paper-scanner` — research/apparatus
  - `com.trader.weekly-degradation-check` — research/apparatus
  - (Note: 7 of these were apparatus that should have been removed at v4.0.0 but weren't.)
- `trader-dashboard` Docker container stopped (`docker stop`, exit code 0).
- README rewritten to a two-sentence sunset tombstone.
- Repo tagged `v4.1.0` with the suffix `sunset`.

**What didn't happen, and why:**

- **Paper positions not liquidated.** The user has $108K equity in Alpaca paper across 15 positions. Path C is about stopping the *project*, not unwinding the *book*. Closing positions is a manual step the user can do in Alpaca's UI when they want; the trader codebase has no business doing it as part of a disposition.
- **Repo not GitHub-archived.** That's a public-facing signal that requires explicit operator approval. The git tag + README rewrite carry the same disposition intent without making it a one-click public statement.
- **Engineering not migrated.** The disposition spec was explicit: "the patterns are already in your head. The code isn't portable. Don't let 'migration' be the costume in which the project keeps living." The cross-validation harness, journal replication, source-spot-check, and launchd patterns survive in code form here as static reference; they don't need to be moved.

**Reactivation path (for the record).** If you ever want to bring it back:

1. Decide *why*. Read §2 first. Re-litigate the survivor-bias / complexity-tax / β issues honestly before relaunching daemons.
2. Reinstall plists: `cp infra/launchd/com.trader.*.plist ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/com.trader.*.plist`
3. Rebuild dashboard: `bash scripts/build_dashboard.sh`
4. Run a manual reconciliation first: `python scripts/run_reconcile.py` — the broker book has been drifting since 2026-05-08; the journal hasn't.
5. Tag v5.0.0 and write a new disposition document that explains why this time is different.

Do not do (5) lightly.

---

## 12. Glossary

| Term | Meaning |
|---|---|
| **12-1 momentum** | 12-month return, skip the most recent 1 month. The factor the LIVE variant ranks on. Jegadeesh-Titman convention; the skip is to avoid short-term reversal noise. |
| **α-IR** | Information ratio computed against β-stripped excess returns. Annualized monthly via `sqrt(12)`. |
| **β-amplification** | A book that runs at β > 1 will mechanically have higher cumulative active return than its α-only contribution suggests, when the market trends up. The trader's live book runs β ≈ 1.7. |
| **Cap-aware redistribution** | The rule that, when the 8% / 25% caps clip a name or sector, the excess weight moves to the *next-ranked* name with headroom (not equal-weighted). |
| **Cum-α** | Cumulative β-stripped alpha. The pp number after subtracting `β × SPY_return` from the strategy's return. |
| **Cum-active** | Cumulative `strategy_return - SPY_return`. Includes β contribution; not skill-only. |
| **DD** | Drawdown. Always negative or zero. Computed as `(equity / equity.cummax()) - 1`. |
| **Deployment-anchor** | The equity-at-deploy fixed reference. Persisted in `data/deployment_anchor.json`. -25% / -33% gates are computed against this, not against rolling peak. |
| **ENFORCING / ADVISORY** | Modes for the drawdown protocol. ENFORCING mutates targets; ADVISORY only warns. Currently ADVISORY. |
| **HANK** | The dashboard's chat copilot. Honest Analytical Numerical Kopilot. Anthropic SDK passthrough with read-only journal-query tools. |
| **LIVE variant** | The single registered variant the orchestrator gets its picks from. Currently `xs_top15_min_shifted`. |
| **Min-shift** | The weighting formula `(score - min(score) + 0.01)` normalized to target gross. |
| **Reactor** | The 8-K signal layer. Currently SHADOW (signals journaled, trim rule does not execute). |
| **Sleeve** | A slice of capital with its own selection logic. Currently one sleeve (momentum) at 80% gross. The bottom-catch sleeve is legacy / disabled (`USE_DEBATE=false`). |
| **TIER** | One of GREEN/YELLOW/RED/ESCALATION/CATASTROPHIC. The drawdown protocol's classification of the current 180-day-peak DD. |
| **TRIM_TO_TOP5** | The ESCALATION-tier action: keep top-5 names by score, drop ranks 6-15, rescale to 30% gross. |
| **Universe** | The set of tickers eligible for the strategy. Currently 43 hand-picked US large-caps. Survivor-biased. |
| **V4** | The legacy bottom-catch sleeve from v3.x. Disabled at v3.59 (`USE_DEBATE=false` default) and effectively dead at v4.0.0. |
| **VIX gate** | A gentle vol-scaler on gross. Multiplies by a function of VIX. Does *not* go to zero. |
| **Walk-forward** | The cross-validation pattern: train on data up to t, evaluate forward returns over (t, t+1m), step t forward, repeat. Used by the eval harness. |

---

*This document is frozen alongside the project at v4.1.0. Path C executed 2026-05-08. The trader is no longer operational.*
