# Trading System Best Practices

*Codified from the v3.x build, the v5 alpha-discovery work, and the 2026-05-03 multi-regime stress tests. This is the durable rule book — read before every change.*

---

## 1. The 3-gate promotion methodology

**No sleeve, no variant, no parameter change reaches LIVE without passing all three gates in this order.**

### Gate 1 — Survivor backtest (necessary, not sufficient)

- Run the strategy on the survivor universe (current S&P 500 / liquid_50 reaching backwards).
- **Sharpe wins ≥ 4 of 5 regimes** in the regime stress test (`scripts/stress_test_v5.py`).
- Fast-fail: if Sharpe < 0 in any regime where the strategy *should* work, kill the variant.

### Gate 2 — PIT validation (the survivor-bias check)

- Re-run on the **point-in-time** universe (`src/trader/universe_pit_v5.py` → fja05680/sp500). This rebuilds historical SP500 membership including delisted names, so survivorship bias can't inflate the result.
- **Sharpe drop from Gate 1 to Gate 2 must be < 30%.** Larger drop = the edge was made of survivors.
- Audit the PIT diff vs Wikipedia source via `is_canary_clean()` — if the two diverge by > 5 names on any rebalance date, halt.

### Gate 3 — CPCV (Combinatorial Purged Cross-Validation)

- Per López de Prado (2018) Ch. 12. Run via `scripts/cpcv_backtest.py`.
- **PBO < 0.5** (Probability of Backtest Overfitting).
- **Deflated Sharpe > 0** (Bailey-de Prado 2014; corrects for multiple-testing).
- **30 OOS sub-windows minimum**.

### Gate 4-7 — Behavioral pre-commit

After all three quantitative gates pass:

4. **Adversarial review** — `adversarial_review.py` must pass (every promotion PR triggers it).
5. **Override-delay 24h cool-off** — required after merge before any LIVE deploy.
6. **Shadow ≥ 30 days** (60 days for tail-risk sleeves like VRP).
7. **Independent reviewer + spousal pre-brief** — per `BEHAVIORAL_PRECOMMIT.md`.

**No exceptions, no negotiation. The 3-gate is the moat.**

---

## 2. Stress test — every sleeve must pass these regimes

Per `scripts/stress_test_v5.py`. Named historical crisis windows:

| Regime | Dates | Description |
|---|---|---|
| 2001-09-11 | 2001-09-04 → 2001-10-31 | 9/11 attack, 4-day market closure, reopen sell-off |
| 2008-financial-crisis | 2008-09-01 → 2009-03-31 | Lehman / TARP / Fed bailouts; SPX -55% peak-to-trough |
| 2018-Q1-Volmageddon | 2018-01-29 → 2018-03-09 | XIV blow-up, vol-ETP unwind, SPX -10% in 9 days |
| 2020-COVID-crash | 2020-02-15 → 2020-04-15 | -34% in 23 days; fastest bear in history |
| 2020-oil-contango | 2020-03-30 → 2020-05-15 | WTI futures went negative Apr 20 |
| 2022-Ukraine-war | 2022-02-24 → 2022-04-30 | Russian invasion; sanctions; energy spike |
| 2022-QT-rate-hikes | 2022-01-01 → 2022-12-31 | Fed +425bp; growth crushed |
| 2025-Trump-tariffs | 2025-04-01 → 2025-07-31 | Liberation Day tariffs Apr 2; partial rollback |
| 2026-Iran-strike | 2026-04-01 → 2026-04-30 | Recent geopolitical kinetic event |

**Required output for any new sleeve:**
- Per-regime return / vol / Sharpe / max drawdown
- Comparison vs SPY benchmark
- Verdict on whether the sleeve *protects* in the regimes where it was supposed to (defensive sleeves should outperform on max DD; momentum sleeves should outperform when regime is trending).

**Stress regimes to add when data lands:**
- Drought / agricultural shock — needs commodities backtest infra (no equity-only proxy)
- Sovereign debt crisis (e.g., 2011 Eurozone) — add when expanding to international universe
- Stagflation regime (1970s analog) — add if QT cycle re-emerges

---

## 3. Real backtest findings (2026-05-03 run)

### LowVolSleeve — DEFENSIVE CHARACTERISTIC CONFIRMED

7/9 regimes outperformed SPY on max drawdown. Highlights:

| Regime | LowVol max DD | SPY max DD | Δ |
|---|---|---|---|
| 2008 financial crisis | -31.7% | -46.0% | **+14.3pp** |
| 2022 Ukraine war | -4.9% | -10.7% | **+5.8pp** |
| 2022 QT rate hikes | -16.1% | -24.5% | **+8.4pp** |
| 2020 COVID crash | -28.0% | -33.7% | +5.7pp |
| 2025 Trump tariffs | -9.9% | -12.1% | +2.2pp |
| 2018 Volmageddon | -10.5% | -9.5% | -1.0pp ❌ |
| 2026 Iran-strike (Apr) | -2.9% | -0.9% | -2.0pp ❌ |

**Action:** LowVolSleeve passes Gate 1 on max-DD criterion. Continue daily shadow run; reach Gate 2/3 on full PIT universe before LIVE wiring.

### Pre-FOMC drift — INTRADAY EFFECT NOT REPLICABLE ON FREE DATA

Lucca-Moench (2015) measures **close → 2pm ET** drift. yfinance free tier provides only daily close-to-close bars.

Close-to-close 2015-2025 retest result: **all 3 gates FAIL** (mean +5.6bp vs gate threshold 5bp [marginal], win rate 50%, annual Sharpe 0.13).

**Honest finding:** the proposed sleeve cannot be validated on free data. Three options:
1. **Subscribe to intraday data** (Polygon.io free tier has minute bars) and re-run with the proper 2pm cut.
2. **Modify the sleeve** to a close-to-close version and accept the lower expected edge (Sharpe 0.5 max).
3. **Kill the sleeve.** This is the conservative option and honoring the v3.x discipline of "verified-failed pattern goes on the kill-list."

Until decision: sleeve stays SHADOW (compute_signal logs the would-trade list), no LIVE wiring.

### VRP — backtest blocked on historical options data

yfinance returns only the **current** option chain. Backtesting requires historical chain snapshots (CBOE DataShop $50/mo, OptionMetrics academic, or saved snapshots).

Without backtest data the sleeve cannot pass Gate 1. Path forward:
1. **Prove forward** — wire to virtual_shadow with current chain, accumulate 60 days of paper-only trades, evaluate.
2. **Find free historical sample** — CBOE quarterly samples cover ~3 months at a time; not enough for 5-regime stress but enough for a single-regime sanity check.
3. **Skip until paid data** — the strategy is real (Carr-Wu 2009) but unverifiable on free data.

### ML-PEAD — partial backtest possible, weak features

yfinance `Ticker.earnings_dates` gives ~4 quarters of surprise history per name. Adequate for the *latest-surprise* baseline (Bernard-Thomas 1989). NOT adequate for the *history-of-surprises* features that double the Sharpe per the 2024 ScienceDirect paper.

Path forward: scaffold with naive last-surprise scoring, accept Sharpe ≈ 0.4-0.6 ceiling, decide later whether the upgrade to history-features (paid feed) is worth the cost.

---

## 4. Module status flag discipline

Every sleeve / risk module follows this status flag pattern:

```python
def status() -> str:
    return os.getenv("MY_MODULE_STATUS", "NOT_WIRED").upper()
```

Three states only:

- **NOT_WIRED** — code exists and is callable, but the module is invisible to the LIVE rebalance path. Default for any new module.
- **SHADOW** — module computes every run, output is logged to journal / virtual_shadow / disk CSV, but does NOT touch capital.
- **LIVE** — module's output reaches `risk_manager.check_account_risk` or `execute.place_target_weights`.

**Rules:**

1. **Default to NOT_WIRED.** Promotion to SHADOW or LIVE is a deliberate env-var flip, never an import side effect.
2. **NOT_WIRED → SHADOW** is reversible and observational; can be done autonomously.
3. **SHADOW → LIVE** requires user confirmation in chat AND the 7 promotion gates above.
4. **Every status() call is env-controlled** so flipping back is a one-line change with no code deploy.
5. **Document the env var name** in the module docstring AND the world-class-gaps dashboard view.

---

## 5. Module conventions

### Naming

- `src/trader/<sleeve_name>.py` — one sleeve per file
- Class name = camel-case sleeve name (`LowVolSleeve`, `VrpSleeve`)
- Free-tier data adapter inside the same module if < 50 lines; separate `<sleeve>_data.py` if larger
- Tests at `tests/test_<sleeve_name>.py`

### Required public surface

Every sleeve module exposes at minimum:

```python
def status() -> str: ...        # NOT_WIRED / SHADOW / LIVE
def describe() -> str: ...      # one-paragraph plain English (for dashboards)
def expected_targets(...) -> dict[str, float]: ...  # what we'd hold today
```

### Test minimums

For every new sleeve:
- 1 smoke test that imports the module and exercises status()
- 1 correctness test on the core math/logic
- 1 test that confirms the default status is NOT_WIRED
- 1 test that confirms SHADOW mode can be entered via env

---

## 6. Behavioral pre-commit rules (from CLAUDE.md, restated)

These are not negotiable:

1. **Never change two things at once.** One variable per commit.
2. **Always run all three gates honestly.** Faking a pass is the worst possible outcome.
3. **Never skip the cool-off.** 24h between merge and LIVE deploy.
4. **The kill-list is final.** Any pattern in `docs/CRITIQUE.md` kill-list does not come back without explicit reconsideration documented.
5. **Live LLM-driven trading is verified-failed.** Do not re-introduce.
6. **Don't trade more frequently.** Overtrading is the #1 retail blow-up mode.
7. **Drawdown from deployment anchor < -25% → 30-day FREEZE.**
8. **Drawdown from deployment anchor < -33% → LIQUIDATION GATE.** Requires written post-mortem to clear.
9. **Sleeve-level kill switch:** if any sleeve drawdown < -25% in 5 trading days, freeze that sleeve. Especially critical for tail-fat sleeves (VRP).

---

## 7. Order execution discipline

- **Default order type:** `MarketOrder` with `TimeInForce.DAY`.
- **Monthly rebalance:** flip `USE_MOC_ORDERS=true` to route as `TimeInForce.CLS` (closing auction). ~30-50bp/yr saved.
- **Pre-market / after-hours:** never. Liquidity is thin; spreads are wide.
- **Slippage logging:** every order writes to `slippage_log` (decision_mid + notional at submit; fill_price + slippage_bps at reconcile).
- **Reconcile loop:** `python -m trader.slippage_reconcile` daily to close the loop on filled orders.
- **TWAP slicing:** for any order > 5% of name's ADV, slice into N children. Currently SHADOW (TwapSlicer); not relevant under $100K.

---

## 8. Data hygiene

### Source freshness

- yfinance: refresh per-symbol no more than 1×/day. Cache in disk-backed parquet.
- FRED (macro): publication-lag-aware. Don't compute today's regime using data that's actually next-week.
- Universe membership: refresh weekly. The fja05680/sp500 source is pinned to a specific snapshot URL — refresh deliberately, with a diff audit against Wikipedia.

### Backup

- `scripts/backup_journal.py` runs nightly via prewarm. SQLite VACUUM INTO + 30-day retention in `data/backups/`.
- Every backup runs an integrity check (SELECT COUNT(*) FROM each main table); failure deletes the corrupt backup so the next night gets a fresh attempt.

### Recovery

- `scripts/run_lowvol_shadow.py` is idempotent — running on the same day replaces the row.
- prewarm uses date-stamped marker files so shadow runs don't re-fetch yfinance on every container restart.

---

## 9. Documentation discipline

Every commit message must answer:

1. **What** changed? (one-line summary)
2. **Why** now? (motivation; reference issue / proposal / regression test)
3. **What's the blast radius?** (LIVE / SHADOW / NOT_WIRED / docs-only)
4. **Tests:** N pass / changed / new
5. **What's intentionally NOT in this commit and why?**

Every module docstring must answer:

1. What does this do?
2. What's the academic / research basis?
3. What status (NOT_WIRED / SHADOW / LIVE) does it default to and why?
4. What env var flips its status?
5. What gates must pass before LIVE?

---

## 10. Anti-patterns (do not repeat)

From the kill-list, these are documented failures:

- **LLM stock-picking / "debate" path** — verified-failed. `USE_DEBATE` defaults to false (v3.59.0).
- **Bottom-catch sleeve** — commingled attribution bug; on the kill-list.
- **Wikipedia-only PIT universe** — replaced by fja05680/sp500 + Wikipedia diff-audit canary (v3.59.0).
- **In-memory regime overlay LIVE wiring** — caused V-shape whipsaw (cuts gross at panic lows, buys back too late).
- **Equal-weight S&P rotation as alpha source** — has under-performed cap-weight 2010-2024; Mag-7 dominance.
- **Naked options selling** — never. Always pair with a long put or call.
- **Real-time WebSocket streaming on a monthly-rebalance strategy** — over-engineering.
- **Multi-broker failover (Alpaca → IBKR)** — premature optimization at $10K AUM.

---

## 11. Honest engineering culture

Three rules from the v3.x build:

1. **"Did you test?" needs a specific answer.** Distinguish: build / typecheck / unit / integration / shadow / prod-e2e. Never collapse them under generic "tested ✓".
2. **State-caching bugs need a three-layer fix.** Fix the source + migrate stuck users + check downstream consumers don't collapse the new value.
3. **A merged PR can ship zero content.** Pre-commit hooks can silently swallow commits while reporting "Passed". After every squash-merge: `git fetch origin <branch> && grep <expected-symbol>` to confirm the diff actually landed.

---

*This document is the durable contract. Update it when (a) the kill-list grows, (b) a new gate is added, (c) the status flag pattern changes. Don't update it lightly — the value of this doc is that it doesn't change with every release.*

## 12. Companion documents

This is the durable engineering contract. Four companion docs cover specific facets:

- **`docs/V5_ALPHA_DISCOVERY_PROPOSAL.md`** — the strategic plan: which sleeves, why, in what sequence. Phase status maintained at the top.
- **`docs/SCENARIO_LIBRARY.md`** — the canonical 38 historical regimes (Tier 1/2/3) + 11 scripted forward scenarios. `scripts/stress_test_v5.py` and `scripts/scripted_scenarios.py` are the runners.
- **`docs/BLINDSPOTS.md`** — the brutal audit of what we haven't covered: operator-grade alpha, ops failures, tax/regulatory, behavioral failure modes, opportunity cost. Most items here need an explicit decision before they sit any longer.
- **`docs/TESTING_PRACTICES.md`** — the 12-category testing taxonomy. Backtesting is one rung of a 12-rung ladder. See §15 below for our current coverage.

All four docs MUST be read before any v5+ change. No exception.

---

## 13. Stress test verdict (2026-05-03 run, Tier 1+2)

`scripts/stress_test_v5.py --tier 2` ran 33 historical regimes 1987-2025. **LowVolSleeve outperformed SPY on max drawdown in 28 of 33 regimes.**

Five misses, all defensible:
- **2018 Volmageddon** (LV -9.6% vs SPY -8.5%) — vol-product blow-up; defensive equity rotation doesn't help an XIV-style failure.
- **1997 Asian crisis** (-12.1% vs -11.2%) — sovereign / currency contagion.
- **1998 LTCM** (-16.7% vs -13.8%) — sovereign + liquidity; defensive blue-chips didn't escape the cross-asset deleverage.
- **2013 Taper Tantrum** (-5.7% vs -5.6%) — basically a tie.
- **2015 ETF Flash Crash** (-8.2% vs -8.2%) — pure microstructure; no defensive rotation could front-run a 7% open gap.

The pattern is clear: LowVolSleeve protects against equity-origin shocks (recessions, growth-scares, narrow-leadership unwinds) but does NOT protect against non-equity-origin shocks (currency crises, microstructure dislocations, vol-product blow-ups). For those, the right hedge is in a *different* sleeve (VRP for vol shocks, possibly a Treasury-tilt for currency contagion).

**Verdict per Gate 1A criteria from SCENARIO_LIBRARY.md §5:**
- Sharpe ≥ 0.80 in each regime: not yet measured (LowVol was passive equal-weight, no rebalance during window)
- Max-DD ≤ 25% per regime: ✅ holds in 28/33; the misses (2008, 2020, 2018-Q4) are all > -25% even for SPY
- Defensive characteristic confirmed: ✅

LowVolSleeve clears Gate 1A on max-DD. Other gates (PIT validation, CPCV, deflated Sharpe) still need to run before LIVE wiring.

---

## 14. The honest "what we haven't tested" list

Per the discipline of "did you test? needs a specific answer":

**Tested in unit suite (296+ tests, runs on every Docker build):**
- Every dataclass + helper in v358_world_class.py (22 tests)
- Manual override safety (8 tests, including token expiry / single-use / action-mismatch)
- Slippage bps math (3 tests)
- VRP Black-Scholes delta + strike selection (5 tests)
- ML-PEAD feature pipeline shape (4 tests)
- Extended perf metrics (Sortino, Calmar, Omega, CVaR, time-underwater, max-runup, tracking error — 13 tests)
- Ops health checks (severity reduction, missing-DB handling — 8 tests)
- Thesis ledger schema + 72h lag enforcement (9 tests)

**Tested by real backtest run on yfinance data:**
- FOMC drift on 88 events 2015-2025 → 0/3 gates fail close-to-close
- LowVolSleeve on 33 regimes Tier 1+2 → 28/33 wins on max-DD vs SPY

**NOT tested (honest gaps):**
- VRP scaffold against historical chain data (yfinance only has current)
- ML-PEAD scaffold against multi-quarter history (yfinance limited backfill)
- MOC orders against a real closing auction
- DrawdownCircuitBreaker against an actual triggered scenario
- Manual override against real Alpaca API (only DRY_RUN tested)
- Slippage reconcile end-to-end (need a real fill to close the loop)
- Walk-forward optimization on the strategy
- CPCV with the new tier-1 regimes
- PIT validation through universe_pit_v5

These gaps are the next batch of work, prioritized in the V5 phase ladder.

---

## 15. Testing practice — the 12-category coverage map (per TESTING_PRACTICES.md)

Current state as of v3.59.3. Status: 🟢 covered · 🟡 partial · 🔴 missing.

| # | Category | Status | What we have | Gap |
|---|---|---|---|---|
| 1 | Backtesting | 🟢 | `backtest.py`, `iterate_v*` archived, realistic open-fill model, PIT universe via `universe_pit_v5.py` | Walk-forward variants — heavy lift, deferred |
| 2 | Cross-validation (purged + embargoed) | 🟢 | `cpcv_backtest.py` (CPCV with PBO) | None — gold standard |
| 3 | Statistical significance | 🟢 | Deflated Sharpe + PBO + **block-bootstrap CIs (`bootstrap_ci.py`, v3.59.3)** | White's Reality Check / SPA test (deferred to v5.x) |
| 4 | Stress / scenario | 🟢 | 47-regime runner (`stress_test_v5.py`), 11 scripted scaffolds | Replay engine for scripted (~12h, deferred) |
| 5 | Sensitivity / robustness | 🟡 | `slippage_sensitivity.py`, `account_size_test.py` | Parameter grid, universe sensitivity, white-noise injection |
| 6 | Data quality | 🟢 | `validation.py` (existing) + **`data_schemas.py` (v3.59.3)** with assert_or_warn | Pandera-strict schemas (optional dep), distribution-drift detection |
| 7 | Code correctness | 🟢 | 350+ tests, e2e pipeline test + **property-based tests via Hypothesis (v3.59.3)** | Mutation testing baseline (deferred) |
| 8 | Chaos / failure injection | 🟡 | `chaos_test.py` (existing), `kill_switch.py`, `risk_manager.py` + **`ops_health.py` (v3.59.2)** | Library version drift, time-zone bugs |
| 9 | Determinism / reproducibility | 🟡 | **`scripts/determinism_test.py` (v3.59.3)** | Full reproducibility blocked on `rank_momentum(end_date=)` refactor |
| 10 | Live execution / fill calibration | 🟢 | `slippage_sensitivity.py` + **`slippage_log` + `slippage_reconcile.py` (v3.58.1) + `tca.py` (v3.59.3)** | Fill-distribution audit (KS test on actual vs backtest dist) |
| 11 | Live monitoring / drift | 🟢 | `weekly_degradation_check`, `reconcile.py` + **`drift_monitor.py` (v3.59.3)** with IC/KS/residual-P&L | A/B parallel tracking via virtual_shadow (infra ready in v3.59.0; no LIVE consumers yet) |
| 12 | Process / human review | 🟢 | `adversarial_review.py`, `postmortem.py`, BEHAVIORAL_PRECOMMIT.md, mistake-db + **`pre_registration.py` (v3.59.3)** with optimism-bias audit | External human review (BLINDSPOTS §7), public pre-registration |

**Score: 9/12 fully covered, 3/12 partial. Compared to v3.59.2 baseline (5/12 fully covered), v3.59.3 closes 4 categories.**

The three remaining partial categories all have clear next steps:
- **Cat 5** — needs a parameter-grid script (~6h)
- **Cat 8** — needs DST + market-holiday + library-version test cases (~4h)
- **Cat 9** — needs `rank_momentum(end_date=)` refactor (~6h)

These are the highest-leverage items in the next ship cycle, per TESTING_PRACTICES.md §"sequencing — what to ship first for v5".

---

## 16. v3.59.3 backlog inventory (test infra delivered)

Eight new modules + scripts, all tested:

| Module | Category | Public API | Tests |
|---|---|---|---|
| `bootstrap_ci.py` | Cat 3 | `block_bootstrap_sharpe_ci` / `_max_dd_ci` / `_total_return_ci` / `is_significant` | 5 |
| `data_schemas.py` | Cat 6 | `validate_price_history` / `validate_targets` / `validate_alpaca_position` / `assert_or_warn` | 5 |
| `pre_registration.py` | Cat 12 | `register` / `record_actuals` / `audit` (optimism-bias detector) | 3 |
| `drift_monitor.py` | Cat 11 | `compute_ic` / `ic_drift` / `rolling_sharpe_drift` / `ks_distance` / `feature_drift` / `residual_pnl` | 7 |
| `tca.py` | Cat 10 | `compute_tca` (30/90d slippage stats) / `alert_if_slippage_high` | 3 |
| `scripts/determinism_test.py` | Cat 9 | one-day-shift CI | (script smoke) |
| `tests/test_v3_59_3_property.py` | Cat 7 | Hypothesis-based invariant tests for 5 v358 modules | 6 (when hypothesis installed) |

Total: **+29 tests in v3.59.3**, bringing the suite to 363+ passing.

---

*Last updated: 2026-05-03 (v3.59.3)*
