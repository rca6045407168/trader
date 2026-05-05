# Measurement & Evaluation Audit

**Date:** 2026-05-05
**Subject:** What we measure, how we measure it, and how the experiments are doing
**Audience:** Owner-operator deciding whether to act on the leaderboard

---

## TL;DR

We now have **four layers** of measurement, three of them shipped this session:

1. **Unit/integration tests** — 114 v3.73.* tests, locally invoked. Coverage is good for behaviors added since v3.65; thinner for older code. *No CI; tests run on demand.*
2. **Production journal** — every decision, order, run, daily snapshot, reactor signal, and LLM call is persisted to `data/journal.db`. *Single source of truth for live behavior; not yet replicated.*
3. **Benchmark-relative tracking (v3.73.6)** — NAV vs SPY with active return / IR / beta / alpha on the Overview headline. *7 days of data; honest sample-size disclosure.*
4. **Constant strategy evaluator (v3.73.7)** — 10 candidate strategies run on every rebalance, journaled to `strategy_eval`, ranked by cumulative active return on the Strategy Leaderboard view. *19 monthly observations from backfill; daemon-extended going forward.*

Live answers to the user's two questions:

- **"Do we have a mechanism to measure tests?"** Yes — unit tests + journal + benchmark tracker + constant-eval harness. The constant-eval harness is the new piece; before this session it didn't exist.
- **"How are our experiments doing?"** Concentrated strategies are leading: `xs_top8` is +10.04pp vs SPY over 19 months; the current production `xs_top15` is mid-pack at +5.28pp. Score-weighting beats equal-weighting (`score_weighted_xs` at +8.94pp with the highest IR of 1.74). Sector rotation and equal-weight-universe both lose to SPY. **The current production strategy is competitive but not the best on this comparison.** Caveat: 19 obs is below the threshold for statistical claims — read the trend, don't act on the numbers.

---

## 1. Test infrastructure

### What's tested today

| Test file | LOC | Behaviors covered |
|---|---|---|
| `test_v3_73_1_build_info.py` | 220 | Pickle-safe disk overlay, BUILD_INFO baking, drift detector, build helper |
| `test_v3_73_2_drawdown_tiers.py` | 288 | Four threshold values, tier evaluator, ADVISORY/ENFORCING modes, dashboard panel wiring |
| `test_v3_73_3_risk_roadmap.py` | 200 | Roadmap view, status resolver, doc inline rendering, Dockerfile copies docs |
| `test_v3_73_5_portfolio_caps.py` | 287 | Cap math (no-op, single-name clip with/without headroom, sector cap with redistribution, both-bound interaction), wiring |
| `test_v3_73_6_benchmark_track.py` | 218 | Metrics math (perfect-corr, beta-2, zero-vol, negative-alpha 60d), persistence, dashboard wiring |
| `test_v3_73_7_eval_harness.py` | 240 | 10 strategies registered, idempotent eval, settle-skip-when-unpriceable, leaderboard ranking, dashboard wiring |
| Older v3.65–v3.72 tests | ~3000 | Reactor archive, parallel reactor, HOT/WARM cadence, ReactorSignalRule, backtest harness, structured "why" panel, healthcheck, etc. |

Total: ~4,500 lines of test code against ~21,500 lines of source. Test-to-source ratio ~21% — typical for a mature single-author codebase.

### What's NOT tested

- **No CI.** Tests run only when manually invoked. A regression introduced today is detected only when the operator next runs `pytest`. *Highest-leverage operational gap.*
- **No frozen-snapshot regression test for the strategy itself.** A refactor of `rank_momentum` could silently change the picks; no test catches that. (Recommended in the v3.73.4 DD; not yet shipped.)
- **No live-integration test of v3.73.2 ENFORCING mode.** The unit tests cover the math; nothing tests that the dashboard panel renders correctly under stress, or that the orchestrator threads the `current_weights` correctly when ENFORCING is on.
- **Most dashboard tests assert source-text presence**, not rendered output. They verify the view exists and references the right symbols; they don't exercise the Streamlit rendering path. This is OK for a single-operator system but would fail a multi-user-fund bar.
- **Reactor signal accuracy is un-tested against forward returns.** We have 13 signals, 1 M3 — but no test or measurement that verifies the M3 was *correct*. This is the work the v3.73.4 DD recommended (replay against forward returns); the harness now exists but the analysis hasn't run.

---

## 2. Production measurement (the journal)

`data/journal.db` is SQLite, schema:

| Table | Rows today | Purpose |
|---|---|---|
| `runs` | 5 | Daily orchestrator start/finish |
| `decisions` | 15 | Per-name BUY/HOLD/SELL with rationale + final action |
| `orders` | 15 | Submitted Alpaca orders with status |
| `daily_snapshot` | 7 (post v3.73.6 backfill) | Date × equity × cash × SPY close |
| `position_lots` | varies | Per-fill lots for tax-aware tracking |
| `postmortems` | varies | After-action review docs |
| `earnings_signals` | 13 | Reactor-emitted signals from 8-K filings |
| `llm_audit_log` | 69 | Every Claude call: model, tokens, cost, influenced_trade flag |
| `strategy_eval` | 190 (v3.73.7 backfill) | Per-strategy picks + forward returns |

**What we do well**: every load-bearing decision is journaled. We can reconstruct *what* the system did and *why* on any day in the last ~6 months.

**What we don't do well**: the journal is on the same laptop as the orchestrator. If the laptop dies, the journal dies with it. The v3.73.4 DD's Tier-1 recommendation #4 was journal replication; not yet shipped.

**Honest discrepancy**: 5 `runs` rows total but only 1 `completed`. The other 4 are `started` with no `completed_at` — meaning the daemon launched but didn't finish. That's the silent-failure mode the v3.73.0 heartbeat was built to catch. **As of 2026-05-05 17:00 UTC, we don't have on-disk evidence the heartbeat fired today, so we don't yet know if it worked.** This was flagged in the v3.73.4 DD; closing it is the dominant Tier-1 ops item.

---

## 3. Benchmark-relative tracking (v3.73.6)

Shipped this session. The Overview headline now answers the question: *are we beating SPY?*

Metrics computed from `daily_snapshot.(equity, benchmark_spy_close)`:

- **Active return** = port_return − benchmark_return
- **Tracking error (annualized)** = σ(daily active return) × √252
- **Information ratio** = mean(active) / σ(active) × √252
- **Beta** = cov(port, bench) / var(bench)
- **Alpha (Jensen's, annualized)** = mean(port) − β × mean(bench), × 252
- **Max relative drawdown** on the cumulative-port-NAV / cumulative-bench-NAV curve
- **Daily win-rate** = fraction of days port_ret > bench_ret

Live values, 2026-04-23 → 2026-05-01 (7 days):

| Metric | Value |
|---|---|
| Portfolio return | +6.49% |
| SPY return | +1.72% |
| Active return | **+4.76pp** |
| Information ratio (annualized) | +8.80 |
| Beta to SPY | +1.72 |
| Alpha (annualized) | +143.94% |
| Win-rate | 50% |

**These numbers are statistically meaningless on 7 days.** The dashboard panel says so explicitly. The structure is in place; the data accumulates daily as the journal extends.

---

## 4. The constant-eval harness (v3.73.7) — the new measurement layer

Before v3.73.7, the system had *one* strategy (`rank_momentum`) and no automated way to evaluate alternatives. We could backtest manually but couldn't keep them running in parallel. v3.73.7 fixes that.

### What it does

- Defines **10 candidate strategies** as pure functions of (asof, prices)
- The orchestrator **journals each strategy's picks every rebalance** (idempotent on `(asof, strategy)`)
- A **settle pass** computes forward returns + SPY returns + active returns for every unsettled row
- The **dashboard leaderboard** (`/strategy_leaderboard`) shows rankings, win-rate, IR, sample-size warning

### Current standings (19 monthly obs, 2024-09 → 2026-04)

| Rank | Strategy | Cum Active vs SPY | Cum Port | Cum SPY | Win % | IR |
|------|----------|------------------:|---------:|--------:|------:|-----:|
| 1 | `xs_top8` | **+10.04pp** | +38.57% | +28.53% | 37% | 1.68 |
| 2 | `score_weighted_xs` | +8.94pp | +37.48% | +28.53% | 37% | **1.74** |
| 3 | `xs_top15_capped` | +5.49pp | +34.02% | +28.53% | 42% | 1.53 |
| 4 | `dual_momentum` | +5.28pp | +33.81% | +28.53% | 42% | 1.46 |
| 5 | **`xs_top15`** *(current production)* | +5.28pp | +33.81% | +28.53% | 42% | 1.46 |
| 6 | `vertical_winner` | +4.67pp | +33.20% | +28.53% | **58%** | 1.04 |
| 7 | `inv_vol_xs` | -2.63pp | +25.90% | +28.53% | 47% | -0.79 |
| 8 | `xs_top25` | -4.09pp | +24.44% | +28.53% | 37% | -1.38 |
| 9 | `equal_weight_universe` | -5.82pp | +22.71% | +28.53% | 42% | -3.94 |
| 10 | `sector_rotation_top3` | -10.23pp | +18.30% | +28.53% | 37% | -2.17 |

### What I read in this

**Confirmed:**
- The factor signal works. 6 of 10 strategies beat SPY over 19 months. The losers (`inv_vol_xs`, `xs_top25`, `equal_weight_universe`, `sector_rotation_top3`) are all *signal-diluting* variants — either too diversified, weighted toward stable names that don't capture momentum, or selected via a noisier proxy (avg-sector-score vs name-score).
- **Equal-weighting the universe (no signal) lags SPY by ~6pp.** Universe selection alone is not edge.

**Surprises:**
- **Concentration wins.** `xs_top8` is the leader by a wide margin. The current production `xs_top15` is mid-pack. This contradicts the diversification heuristic but is consistent with: when the signal is right on N names, picking the top-K of those at lower K captures more of the signal-to-noise per unit of position weight.
- **Score-weighting beats equal-weighting.** `score_weighted_xs` produces +8.94pp on the *same picks* as `xs_top15`'s +5.28pp. The marginal alpha from leaning into the highest-conviction names is +3.66pp over 19 months. Highest IR in the field.
- **Vertical-winner has the highest WIN RATE (58%) but middle cumulative.** It wins more individual months but with smaller magnitude. Translation: lower vol, smaller drawdowns, but doesn't capture the upside tails as well.

**Honest caveats:**
- 19 monthly observations. SE on Sharpe ≈ 0.4-0.5; the gap between #1 (1.68) and #5 (1.46) is *less than* one standard error. Statistically these are tied.
- Single regime (post-2023 bull, semis-led). 2022-style reversal would order these differently.
- No transaction costs modeled. `xs_top8` trades fewer names than `xs_top25`, so any cost-modeling will favor the concentrated strategy more than the table shows.
- Universe is 50 hand-curated names. Wider universe (S&P 500) would test these strategies in noisier conditions.

### What I would NOT do based on this data alone

**Switch the production LIVE variant to `xs_top8` today.** The evidence is suggestive, not conclusive. The right move is:
1. Let the harness accumulate another 30-60 obs (12-24 months of monthly rebalance data).
2. After 60 obs, the SE is tight enough to make IR comparisons real.
3. Check cross-regime stability (does the ordering hold in a 2022-style episode?). The harness doesn't have that data; we'd need to extend backfill further.
4. Add transaction-cost modeling and re-rank.
5. Then decide.

The harness is *the mechanism for that decision*. It's now in place.

---

## 5. The honest measurement gaps

Even with the new harness, these remain:

| Gap | Severity | Tier 1 fix |
|---|---|---|
| No CI; tests don't auto-run on push | High | GitHub Action on `pytest tests/` (~30 min) |
| Daily orchestrator silent failures (5 starts, 1 finish) | **Critical** | v3.73.0 heartbeat — verify it actually fires today |
| Journal not replicated | High | Daily rsync to iCloud/S3 (~2 hrs) |
| Reactor signals un-validated against forward returns | Medium | 30-day backtest of M3 signals (~1 hr; harness exists) |
| No frozen-snapshot regression test for `rank_momentum` | Medium | One pytest that asserts known-input → known-output (~30 min) |
| No transaction-cost model | Medium | 5-10bps slippage in backtest harness (~4 hrs) |
| No cross-regime backfill (2018, 2020, 2022) | Medium | Extend `fetch_history` to 5+ years (~4 hrs) |
| Multiple `runs` rows show `started` without `completed` | High | Investigate (likely App Nap on calendar fire — apply `StartInterval` per FlexHaul lesson) |
| Dashboard test coverage is text-presence, not rendered | Low | Streamlit testing harness (~12 hrs) |

The first three are the dominant blockers for sized capital. Tier-1 ops is gating; alpha extensions are below.

---

## 6. The recommendation

Treat the leaderboard like a science experiment, not a competition:

1. **Don't switch production today.** 19 obs is not enough. xs_top15 is competitive (+5.28pp vs SPY) and any switch introduces operational risk.
2. **Let the harness accumulate.** Each rebalance adds 10 rows. By Q4 2026 we'll have ~30 obs total — close to the threshold for IR-with-confidence.
3. **Watch the rankings stabilize.** If `xs_top8` and `score_weighted_xs` continue to lead through a regime change (any non-trivial drawdown month), that's evidence of robustness, not regime-fitting.
4. **Add the missing instrumentation.** Transaction-cost model, cross-regime backfill, reactor-signal validation. Each is a few-hour ship; together they tighten the leaderboard claims considerably.
5. **Close the operational gap first.** Without a heartbeat verified working, the strategy comparison runs on code that may not be running. Step 1 is always the daemon.

The strategy stack is at v3.73.7. The operational stack is sometimes at v3.5. Closing the gap remains the highest-leverage work for the next 30 days.

---

*Reviewed against: live `data/journal.db` (5 runs, 13 reactor signals, 69 LLM audit rows, 7 NAV/SPY snapshots, 190 strategy_eval rows), source review of `eval_runner.py`, `eval_strategies.py`, `benchmark_track.py`, `portfolio_caps.py`, and the dashboard wiring at `view_strategy_leaderboard` / `_render_benchmark_panel` / `_render_portfolio_caps_panel`.*
