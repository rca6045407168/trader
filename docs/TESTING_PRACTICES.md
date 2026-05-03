# Testing Practices — A Robust-Trading-System Reference

*Companion to V5_ALPHA_DISCOVERY_PROPOSAL.md, SCENARIO_LIBRARY.md, and BLINDSPOTS.md. Catalogues the full testing surface a trading system should cover. Backtesting is one rung of a 12-rung ladder. This doc maps Richard's current rungs (substantial — he's already at ~7/12) against the missing ones, and specifies the minimum-viable version of each so the v5 build can pick its level.*

---

## Why testing in trading is different from general software

A normal web service is correct if it returns the right response to a request. A trading system is correct if it makes a profitable decision under conditions it has never seen, with adversarial inputs, irreversible side-effects, and a reviewer (the market) that takes weeks to grade you. The tests have to cover three failure-mode classes that don't exist for ordinary software:

The first is **statistical falsification of edges that look real but are noise**. A strategy with a +1.5 Sharpe in backtest and 95% confidence is still 50%+ likely to be a noise artifact if 1,000 variants were tested to find it (Bailey & López de Prado on the Probability of Backtest Overfitting). General software doesn't have this problem.

The second is **time-asymmetry of information**. Code can use information from the future without an IDE warning. A unit test that passes does not catch the look-ahead bias where this morning's volatility feature was computed using tomorrow's prices. Specialist tests (PIT validation, vintage-aware feature stores) are the only defense.

The third is **adversarial inputs by the market**. The market actively discovers and arbitrages the alpha you find. A test that confirms a strategy was profitable in 2020-2024 is not a test that the alpha will persist in 2025-2030. McLean-Pontiff: post-publication anomaly returns drop 58%. Ongoing crowd-decay is not testable; only live-tracking can detect it.

The 12 categories below are the minimum surface to cover all three of these concerns.

---

## The 12-category testing taxonomy

| # | Category | What it tests | Richard has | Richard missing |
|---|---|---|---|---|
| 1 | Backtesting | strategy returns on historical data | yes (`backtest.py`, `iterate_v*`) | walk-forward variants |
| 2 | Cross-validation | OOS robustness | CPCV (`cpcv_backtest.py`) | purged K-fold variants |
| 3 | Statistical significance | Sharpe is real, not noise | Deflated Sharpe + PBO | bootstrap CIs, White's Reality Check, SPA |
| 4 | Stress / scenario | extreme regimes | 5-regime stress test | tier 1+2+3 + scripted (see SCENARIO_LIBRARY) |
| 5 | Sensitivity / robustness | params and inputs perturbed | slippage_sensitivity.py | parameter grid, universe, lookback |
| 6 | Data quality | inputs are not lying | basic `validation.py` | schema-strict, drift detection |
| 7 | Code correctness | implementation matches spec | 39 unit tests + e2e | property-based, mutation testing |
| 8 | Chaos / failure injection | system survives operational shocks | `chaos_test.py` | Alpaca outage, library drift, time-zone bugs |
| 9 | Determinism / reproducibility | same inputs produce same outputs | partial (snapshot) | one-day-shift test, replay test |
| 10 | Live execution / fill calibration | backtest model matches real fills | none documented | TCA, fill-distribution audit |
| 11 | Live monitoring / drift | LIVE matches expectation | reconcile + degradation check | strategy-level performance drift, IC decay |
| 12 | Process / human | reviews catch what code can't | adversarial review + pre-mortem | external human review, runbook drill, DR drill |

The remainder of this doc walks each category in order. Each has the same structure: definition → why it matters → Richard's current → minimum viable → advanced.

---

## 1. Backtesting — beyond the single backtest

**What it is.** Run the strategy on historical data, measure CAGR / Sharpe / drawdown.

**Why it matters but is not enough.** A single backtest is the absolute floor — it answers "would this have made money in the past." It does not answer "is the past indicative of the future" or "did I find this edge by trying 50 variants." Treat a single backtest result as a hypothesis, not a conclusion.

**Richard's current state.** Strong. `backtest.py`, `iterate_v3.py` through `iterate_v14.py`, `regime_stress_test.py`, `run_backtest.py`. Two backtest functions in `backtest.py` — one assumes month-end close fills (the older one, known to overstate Sharpe by ~7% CAGR per `CRITIQUE.md` B4) and one with realistic next-day-open fills.

**Minimum viable.** A single backtest that uses (a) realistic fills (open-after-decision, not close-on-decision), (b) realistic costs (slippage 10-20bps for retail at single-share lot sizes, not 5bps), (c) PIT universe (no survivorship bias in security selection), (d) PIT features (no look-ahead in macro / fundamentals). Richard has all four.

**Advanced — anchored walk-forward.** Train on [start, T], test on [T, T+1yr]. Roll T forward by 6 months. Aggregate test windows. This is the standard quant-fund presentation of OOS performance — every fund pitch shows the walk-forward chart, not the single backtest. Implementation: ~6 hours wrapping existing backtest.

**Advanced — rolling walk-forward.** Train on [T-3yr, T], test on [T, T+1yr]. Tests parameter stability under shifting training distributions. Implementation: ~4 hours after anchored is built.

**Anti-pattern.** Iteration 50 of "let's test variant X" without acknowledging that 50 variants × 5% false-discovery-rate = expected 2.5 false discoveries. Richard's 3-gate methodology (PBO + Deflated Sharpe) is exactly the correction for this. Many shops ship without it; that's the ~70% of published strategies that don't replicate (López de Prado).

---

## 2. Cross-validation — purged + embargoed

**What it is.** Split data into K folds, train on K-1, test on the held-out fold. Repeat. Aggregate.

**Why standard K-fold is broken for trading.** Time-series data has serial autocorrelation. Standard K-fold leaks information from training fold into test fold (yesterday's feature value is correlated with today's, and today's training fold is leaking forward via the autocorrelation). The naive K-fold Sharpe is overstated by 30-100%.

**The fix.** Purged K-fold cross-validation (López de Prado, *Advances in Financial Machine Learning*, ch. 7). Remove training samples whose label-window overlaps the test window. Add an embargo period of E observations after each test fold, where E ≥ the maximum label-window length, to prevent leakage from sample-overlap.

**Richard's current state.** Strong. `cpcv_backtest.py` implements Combinatorial Purged Cross-Validation — superset of purged K-fold that runs all (N choose K) train/test combinations, used to compute Probability of Backtest Overfitting (PBO).

**Minimum viable.** Purged K-fold with K=5 and embargo = max(label_window) on every backtest. Standard practice at any quant fund.

**Advanced — CPCV with PBO.** What Richard has. PBO < 0.5 is the gate; lower is better. Worth noting: Bailey-López de Prado's threshold is "PBO close to 0," but in practice 0.3-0.5 is the realistic acceptable range for retail-size strategies.

**Anti-pattern.** Running CPCV but reporting only the IS Sharpe. Always report (a) CPCV-mean OOS Sharpe, (b) PBO, (c) variance of OOS Sharpe across folds. Richard's `cpcv_backtest.py` should be audited for which of these it surfaces.

---

## 3. Statistical significance — bootstrap CIs and multiple-testing correction

**What it is.** Quantify the uncertainty around a Sharpe estimate; correct for the fact that picking the best of N strategies inflates the apparent Sharpe of the chosen one.

**Why it matters.** A single backtest produces a point estimate. A point Sharpe of 1.5 with confidence interval [-0.2, +3.2] is meaningless. A point Sharpe of 1.5 with CI [+0.9, +2.1] is real. Without the CI, you can't distinguish.

**Multiple-testing.** When you've tested 50 variants and report the best, the best's Sharpe is biased upward. Deflated Sharpe Ratio (Bailey-López de Prado 2014) corrects for this — the "deflation" subtracts the expected maximum order-statistic of N null Sharpes from the reported Sharpe. White's Reality Check (1999), Hansen's Superior Predictive Ability test (2005), and the Romano-Wolf stepwise method (2005) are the canonical multiple-testing-correction frameworks; all three are stronger than DSR for some scenarios.

**Richard's current state.** Has Deflated Sharpe Ratio (`deflated_sharpe.py`) and PBO. Likely missing bootstrap CIs on Sharpe, White's Reality Check, and SPA test. The v3.44 OTM call barbell research (per `CLAUDE.md`) does compute a bootstrap CI on annualized return — that pattern should be standard on every variant.

**Minimum viable.** Block bootstrap CIs on Sharpe with B=1000 and block-length = 21 (one trading month) for monthly-resampled returns. Report 95% CI on every backtest.

**Advanced — White's Reality Check + SPA.** When testing >5 variants of the same family, run SPA test on the cohort to get a single multiple-testing-corrected p-value for "is the best variant better than the benchmark." If SPA p > 0.05, the cohort has not produced a significant edge. Implementation: ~12 hours; reference implementations exist in `arch` Python package.

**Anti-pattern.** "Sharpe = 1.5, p < 0.05, ship it" without correcting for the variant zoo behind the discovery. A 1.5 Sharpe out of 100 variants tested is one-sided p ≈ 0.40 after correction — totally insignificant.

---

## 4. Stress / scenario testing

**What it is.** Evaluate the strategy on extreme historical and scripted-hypothetical regimes.

**Why it matters.** Average-case backtest tells you the strategy is profitable. Stress test tells you whether the strategy survives. The two are different questions.

**Richard's current state.** 5-regime list (2018-Q4, 2020-Q1, 2022, 2023, recent 3 months) in `regime_stress_test.py`. v5 plan expands to Tier 1 (9 regimes) + Tier 2 (23 regimes) + Tier 3 (14 deep-history regimes) + 11 scripted forward scenarios + Monte Carlo overlay. See `SCENARIO_LIBRARY.md`.

**Minimum viable.** Tier 1 (9 regimes) with portfolio Sharpe ≥ 0.80 in each, max-DD ≤ 25% per regime. This is the v5 gate-1A.

**Advanced.** Tier 1 + Tier 2 + Tier 3 + scripted scenarios + 1,000-path Monte Carlo with tail injection. Gate-1A + 1B + 1C from SCENARIO_LIBRARY.

**Anti-pattern.** Average over all stress windows and report a single Sharpe. The point is to find the *worst* regime, not the average. Always report per-regime separately.

---

## 5. Sensitivity / robustness analysis

**What it is.** Vary each parameter and input within a defensible range; observe whether results are stable or fragile.

**Why it matters.** A strategy that earns +1.5 Sharpe with top-N=15 but +0.4 Sharpe with top-N=13 is overfit to the parameter. Edge that exists at exactly one parameter value is sample-fit, not real signal.

**Richard's current state.** Has `slippage_sensitivity.py` and `account_size_test.py`. Likely doesn't have a parameter grid on top-N, lookback months, skip months, or universe choice.

**Minimum viable.** Parameter sensitivity grid: vary each numeric parameter ±20% (e.g., top-N ∈ {12, 13, 14, 15, 16, 17, 18}, lookback ∈ {10, 11, 12, 13, 14}). Plot Sharpe surface. Strategy passes if the Sharpe surface is roughly flat (±10%) over the central plateau. If results spike at exactly one parameter combination, kill.

**Advanced — universe sensitivity.** Re-run on Russell 1000, Russell 3000, MSCI USA, S&P 500. Strategy should produce qualitatively similar Sharpe across reasonable universe choices. Edge that lives in only one universe is universe-overfit.

**Advanced — cost sensitivity.** Slippage sweep 0bps → 50bps. Strategy passes if break-even (Sharpe drops to 0.5) is reached only at unrealistic costs (>30bps for retail).

**Advanced — White noise injection.** Add zero-mean noise of varying magnitude to returns before running strategy. Strategy that's robust to small noise survives; strategy that flips to negative Sharpe with σ=20bps noise injection is fragile.

---

## 6. Data quality testing

**What it is.** Schema validation + statistical drift detection on every input dataset.

**Why it matters.** Bad inputs are silent; they produce subtly-wrong decisions that don't crash anything. The yfinance bug where `Adj Close` quietly disappeared in 2024 broke real systems for weeks before anyone noticed. The 2024-08 yfinance schema flip on `Ticker.history()` similarly. PIT correctness on macro requires vintage-aware data. Data quality is a real risk surface.

**Richard's current state.** `validation.py` with critical (raise) + warning (log) levels — empty / all-NaN / insufficient history / single-day jump / missing-data percentage. Good baseline.

**Minimum viable.** What he has, plus add: max-staleness check (last row no older than N business days), per-ticker missing-day audit, split-detection (sudden 50%+ price change without a corresponding adjustment), corporate-action sanity check (volume spikes that aren't explained).

**Advanced — schema-strict validation.** Use [Pandera](https://pandera.readthedocs.io/) for runtime DataFrame schema validation: column types, ranges, uniqueness, multi-column constraints, all in code. On every dataset boundary (yfinance call result, Alpaca position pull, FRED fetch), run a schema check. Forces explicit contracts and fails loudly when an upstream change breaks them.

**Advanced — distribution drift detection.** Kolmogorov-Smirnov test on each feature comparing this week's distribution against rolling 12-week baseline. Alert if KS statistic exceeds threshold. Catches the case where a feature silently changes meaning (e.g., yfinance switched from raw close to adjusted close on a subset of tickers). Implementation: weekly cron, ~6 hours.

**Advanced — vintage-aware features.** For macro features, use ALFRED via fredapi (free) so every feature is computed using only data that was actually available on the as-of date. This is in the v5 proposal but worth restating: it's a *test* not just an integration. The test is "does this feature change if I run it today vs run it as-of last year." If yes, you have look-ahead.

---

## 7. Code correctness testing

**What it is.** Unit tests, integration tests, end-to-end tests, plus advanced techniques that go beyond.

**Why it matters.** A correct strategy implemented incorrectly produces wrong results. `CRITIQUE.md` documents B1-B5 — five real bugs in production code that produced wrong trading behavior. Three were caught by reading the code, not by tests.

**Richard's current state.** 39 test files. Strong coverage on most modules. Has e2e pipeline test, journal tests, kill-switch tests, risk-manager tests, deflated-Sharpe tests, PBO tests. Coverage is in the right zip code for retail.

**Minimum viable.** Unit test every public function with at least: nominal case, empty input, single-element input, large input. Integration test every cross-module path. E2E test the full daily-run pipeline on a known fixture.

**Advanced — property-based testing with [Hypothesis](https://hypothesis.readthedocs.io/).** Instead of writing concrete test cases, define properties ("for any valid price DataFrame, weights should sum to ≤ 1.0", "for any rebalance, total turnover should be ≤ 200%") and let Hypothesis generate thousands of random inputs that try to break the property. This is genuinely stronger than concrete test cases — it finds edge cases your imagination missed. Implementation: ~10 hours to instrument the 5-10 most important invariants in the strategy.

**Advanced — mutation testing with [mutmut](https://mutmut.readthedocs.io/).** Mutmut deliberately introduces small bugs (flip `>` to `<`, change `+` to `-`) and checks whether your test suite catches them. The score is "% of mutations the tests catch." Anything below 80% means your tests aren't actually testing what you think. This is the canonical way to measure test-suite quality. Implementation: ~4 hours setup, then ongoing.

**Advanced — snapshot/regression testing.** Capture the output of the daily run on a known-good day. Re-run with the same inputs after every code change. If outputs change, either you intentionally changed strategy (commit it as a new snapshot) or you have a regression. Most quant funds have this; retail systems often don't.

---

## 8. Chaos / failure injection

**What it is.** Deliberately break the system to test that it fails gracefully.

**Why it matters.** Real trading systems fail at the most inopportune moments (broker API outage during a vol spike, library version regression on the day of a Fed meeting, disk full while persisting an order journal). Tests that only run on the happy path don't catch these. Netflix invented Chaos Monkey for exactly this reason.

**Richard's current state.** Has `chaos_test.py` — exists, doesn't know contents but the name suggests baseline coverage. Has `kill_switch.py` and `risk_manager.py` for portfolio-level halts.

**Minimum viable.** Test these injected failures:

- **Alpaca API down.** Mock the Alpaca client to raise on every call. Does the orchestrator notice? Does it fail safely or place orders into a half-broken state?
- **yfinance returns stale data.** Mock yfinance to return data 3 days old. Does `validation.py` catch it? (It should, per the staleness check.)
- **yfinance returns malformed data.** Mock returning a DataFrame with renamed columns. Does the system fail loudly or silently make wrong decisions?
- **Database lock / disk full.** Mock SQLite to raise `OperationalError`. Does the run abort cleanly with state preserved?
- **Cron runs twice.** Run `main.py` twice in succession with `DRY_RUN=False`. Does the idempotency guard prevent duplicate orders? (`CRITIQUE.md` B5 documents this race.)
- **Time-zone bug.** Run on a DST-transition day. Run on a half-day exchange schedule (Black Friday, Christmas Eve). Run on a market holiday. Does the orchestrator behave correctly?

**Advanced — latency injection.** Wrap network calls in a wrapper that injects 5-10s delays randomly. Does the system handle slow external services without cascading?

**Advanced — adversarial scenario replay.** Take the v5 SCENARIO_LIBRARY scripted scenarios (Iran 2026, Taiwan invasion, Volcker 2.0) and replay them through the live orchestrator (in paper mode). Do the kill-switch + risk-manager fire correctly?

**Anti-pattern.** Chaos testing only in CI, never on the production system. The point is the production system has the bug; CI doesn't. Periodic "game day" exercises where you deliberately break a production component on purpose are how Google SRE catches the real bugs.

---

## 9. Determinism / reproducibility testing

**What it is.** Same inputs produce same outputs. If they don't, you have hidden state.

**Why it matters.** Hidden state in a trading system is a footgun. If the strategy makes one decision on Tuesday morning and a different decision on Tuesday morning when re-run with identical inputs, you have non-determinism and can't trust either decision. Common causes: non-deterministic ML models without seeded RNG, dict iteration order in old Python, file-system listing order, timestamp-dependent behavior.

**Richard's current state.** Likely partial. SQLite snapshots probably preserve enough state to re-derive decisions, but I'm not sure there's an explicit determinism check.

**Minimum viable — the one-day-shift test.** Every day, take yesterday's prices + yesterday's account state. Run the strategy. The decisions should match what was actually decided yesterday. If they don't, find the source of non-determinism. Run weekly in CI.

**Minimum viable — replay test.** Save a "fixture" of inputs from a known-good day. Periodically re-run the orchestrator on the fixture and check that outputs match. Catches the case where a code change accidentally changed strategy.

**Advanced — full replay infrastructure.** Record every input into a system (prices, fundamentals, account state, time) and every output (decisions, orders). Be able to replay any historical day from the recordings, in production code. This is what Citadel / Two Sigma / Renaissance have. At retail you don't need full coverage; the cron-and-fixture approach above gets ~80% of the benefit.

**Anti-pattern.** Strategies that use `np.random` or `lightgbm` without a seeded RNG. Every random call must seed from a deterministic value (e.g., `as_of_date.toordinal()`) so the same date always produces the same randomness.

---

## 10. Live execution / fill calibration

**What it is.** Audit whether your backtest's fill model matches reality.

**Why it matters.** A 5bps backtest slippage assumption that turns out to be 25bps in reality wipes out the entire alpha. You can't tell from the backtest alone — you need to compare backtest-expected fills against actual Alpaca fills.

**Richard's current state.** Has `slippage_sensitivity.py` (parameter test). Likely missing live fill audit (compare backtest-expected fills to actual Alpaca fills, ticker by ticker).

**Minimum viable — Transaction Cost Analysis (TCA).** After every trading day, log: expected price (per backtest model), actual fill price, slippage in bps. Aggregate weekly. Alert if rolling-30-day average slippage exceeds 2x the assumed slippage in backtest. Implementation: ~6 hours.

**Advanced — fill-distribution audit.** Don't just track mean slippage; track the distribution. Ideal: backtest distribution matches live distribution. If real fills have fat right tail (occasional big losses on adverse fills), backtest is understating risk. Use Kolmogorov-Smirnov test to compare distributions monthly.

**Advanced — execution improvements.** Once TCA reveals the cost stack, work backwards to reduce it. Limit orders instead of market-on-open; TWAP/VWAP across the day instead of single-point fills; staging across multiple days for large rebalances. Each of these is a separate test (does my limit-order strategy fill at the desired price 80% of the time, or does the queue position kill me?).

**Anti-pattern.** Running paper-trading and assuming live will be similar. Paper fills are always at NBBO; live fills are often worse. Real TCA only starts when real money is at risk.

---

## 11. Live monitoring / drift detection

**What it is.** Continuous testing of the LIVE strategy against backtest expectations.

**Why it matters.** Even a perfectly-validated strategy degrades over time as market conditions change and crowding eats the edge. If you can't detect decay within ~3 months, you can lose half a year of expected alpha before noticing.

**Richard's current state.** Has `weekly_degradation_check` scheduled task and `reconcile.py`. Don't know exact metrics tracked.

**Minimum viable — three drift metrics, weekly.** Track: (1) signal IC — correlation of factor scores with forward returns (declining IC = signal decay); (2) realized Sharpe over rolling 60 trading days vs backtest expectation; (3) feature distribution KS-distance vs training-period distribution. Alert if any drifts beyond threshold for 2 consecutive weeks.

**Minimum viable — strategy-level performance reconciliation, daily.** Each evening, compute: expected daily P&L from backtest model (using today's actual prices) vs realized daily P&L. Track the residual. Persistent positive residual = something is leaking edge; persistent negative residual = better than expected. Either is a flag.

**Advanced — A/B parallel tracking.** Run shadow versions of v5 candidates in parallel with LIVE. Compare 30-day rolling Sharpe across all candidates and LIVE. The implicit live-shadow A/B is the true signal of whether v5 sleeves work.

**Advanced — alpha decay curve.** For each sleeve, track the average forward return of new positions opened in calendar quarter Q1 vs Q2 vs Q3. If newer positions earn lower forward returns than older positions did, the alpha is decaying.

**Anti-pattern.** Monitoring only portfolio P&L. Total P&L masks sleeve-level drift. A momentum sleeve declining might be hidden by a VRP sleeve doing well, until both stop working at once.

---

## 12. Process / human review testing

**What it is.** Decisions, methodology, and reviews are themselves audited.

**Why it matters.** Code can't catch methodology errors that the same coder introduced. Your kill-switch can't kill itself when broken. Process layers exist to catch what code can't.

**Richard's current state.** Has `adversarial_review.py`, `postmortem.py`, `BEHAVIORAL_PRECOMMIT.md`, `PRE_MORTEM_TEMPLATE.md`, `PRE_REGISTRATION_OOS.md`, mistake-db. Genuinely strong relative to retail.

**Minimum viable — pre-registration of every promotion.** Before running the 3-gate on a new sleeve, write down the expected Sharpe, expected drawdown, and the falsifying conditions, in a dated file. After running the gate, compare actual results to pre-registered. If pre-registration is consistently wrong (always optimistic), you're suffering from optimism bias and should adjust. Richard already has the template; check that it's actually populated for every promotion.

**Minimum viable — runbook drill, quarterly.** Take the deployment, kill switch, and recovery runbooks. Hand them to one trusted human (spouse, friend, co-founder) and have them attempt to "operate the system from cold." Every gap they hit is a runbook bug. Fix on the spot.

**Minimum viable — disaster recovery drill, annually.** Deliberately corrupt the journal database. Rebuild from backups. Time it. If RTO exceeds your tolerance, fix backup strategy.

**Advanced — external human review.** A quant friend, former colleague, or paid consultant reviews methodology quarterly. Ideally not someone who has seen the code before; new eyes catch errors the original author can't. Cost: small. Catch: large.

**Advanced — public pre-registration.** Per `BLINDSPOTS.md` section 7: write the v5 thesis publicly (gist or blog), with expected results and falsifying conditions, dated. Publish actual results 12 months later regardless of outcome. The accountability force-function is real and Bailey-López de Prado replication work shows ~50% lower discovered-vs-live Sharpe gap on pre-registered strategies.

**Anti-pattern.** Adversarial review by an LLM in the same Claude ecosystem that produced the code. The reviewer shares the priors of the reviewed; the failure modes are correlated. External (human) review is the only way to break this.

---

## Sequencing — what to ship first for v5

Not all 12 categories are equally urgent for v5 launch. The priority order, given Richard's current state and the v5 plan:

**Must ship before any v5 sleeve goes LIVE:**
1. Walk-forward variants on existing backtest framework (Category 1) — 6 hours
2. Block-bootstrap CIs on Sharpe for every variant report (Category 3) — 4 hours
3. Tier 1 stress test (Category 4) — already specified in SCENARIO_LIBRARY — 6 hours
4. Schema-strict validation with Pandera on all data inputs (Category 6) — 8 hours
5. The one-day-shift determinism test in CI (Category 9) — 4 hours
6. TCA / live fill audit on the existing momentum sleeve (Category 10) — 6 hours
7. Three drift metrics tracked weekly (Category 11) — 6 hours
8. Pre-registration audit (Category 12) — 2 hours

Total: ~42 hours of test infrastructure work before v5 sleeves begin. Done in parallel with v5 Phase 1-3 (audit + PIT swap + virtual shadow), this overlaps cleanly.

**Should ship as part of v5 sleeve work:**
9. Property-based tests for VRP sleeve invariants (Category 7) — 8 hours
10. Mutation testing baseline (Category 7) — 4 hours setup
11. Distribution drift detection on features (Category 6) — 6 hours
12. Tier 2 + Tier 3 + scripted scenarios (Category 4) — per SCENARIO_LIBRARY — 12 hours

**Should ship before scaling capital beyond Roth IRA:**
13. White's Reality Check / SPA test on the variant cohort (Category 3) — 12 hours
14. Full chaos / failure-injection suite (Category 8) — 16 hours
15. External human review of methodology (Category 12) — 4 hours engagement
16. Public pre-registration of v5 results (Category 12) — 2 hours

Total v5 launch readiness: ~42 hours infra + 30 hours sleeve-specific = 72 hours. This is on top of the v5 build itself (~97 hours per the proposal). Combined: ~170 hours, or 4-6 weeks at part-time pace. Realistic.

---

## Anti-patterns to avoid (drawn from `CRITIQUE.md` and common retail mistakes)

The mistakes that destroy retail systematic systems are predictable. The pattern-match list:

**"More variants" instead of "better validation."** If your discovery rate is ~1 real edge per 10 tested, the answer to slow alpha discovery is not "test 100 variants in parallel" — it's "improve validation rigor so the 1 real edge is found cleanly." Richard's 3-gate is the discipline.

**Testing only the strategy, not the operational pipeline.** Strategy is correct in isolation; pipeline corrupts it. Categories 6-12 above are the answer.

**Trusting paper-trading as if it were live.** Paper fills are always at NBBO; real fills are not. TCA only starts when real money is at risk.

**One person reviews their own work.** Adversarial review is a real pattern but it has limits when the reviewer shares the reviewed's mental model. External eyes catch a different class of errors.

**Backtest ratchet.** Running backtest, tweaking parameters until Sharpe looks good, declaring done. CPCV + DSR are the antibodies; use them.

**Snapshot-then-forget.** Building elaborate validation infra, running it once, never running it again. Validation is continuous, not a launch-gate.

**Conflating absence of evidence with evidence of absence.** "I didn't see this bug in 3 months of paper trading" is not evidence the bug doesn't exist. Tail-event bugs only fire in tail events. The SCENARIO_LIBRARY scripted scenarios force the tails to exercise the code paths.

---

## Citations and tools

**Methodology references:**
- López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley. [chapter 7 = purged K-fold and CPCV]
- Bailey, D. H., Borwein, J. M., López de Prado, M., & Zhu, Q. J. (2014). "The Probability of Backtest Overfitting." *Journal of Computational Finance*.
- Bailey, D. H., & López de Prado, M. (2014). "The Deflated Sharpe Ratio." *Journal of Portfolio Management*.
- White, H. (2000). "A Reality Check for Data Snooping." *Econometrica*.
- Hansen, P. R. (2005). "A Test for Superior Predictive Ability." *Journal of Business and Economic Statistics*.
- Romano, J. P., & Wolf, M. (2005). "Stepwise Multiple Testing as Formalized Data Snooping." *Econometrica*.
- McLean, R. D., & Pontiff, J. (2016). "Does Academic Research Destroy Stock Return Predictability?" *Journal of Finance*.

**Tools:**
- [Pandera](https://pandera.readthedocs.io/) — DataFrame schema validation
- [Hypothesis](https://hypothesis.readthedocs.io/) — property-based testing
- [mutmut](https://mutmut.readthedocs.io/) — mutation testing
- [Great Expectations](https://greatexpectations.io/) — data quality (heavier than Pandera; only worth it at scale)
- [arch](https://arch.readthedocs.io/) — Python package with White's Reality Check, SPA, and bootstrap utilities
- [empyrical](https://github.com/quantopian/empyrical) — financial performance metrics (Sortino, Calmar, Omega, CVaR)

---

*Last updated 2026-05-03. Companion to V5_ALPHA_DISCOVERY_PROPOSAL.md, SCENARIO_LIBRARY.md, BLINDSPOTS.md. Status: reference doc; no implementation required directly. The sequencing section above maps to v5 Phase ordering.*
