# v5 — Alpha Discovery Proposal

*Author: research synthesis from a 5-agent advisory swarm (trader, quant researcher, principal SWE, technical architect, economist), debated adversarially against the codebase as of 2026-05-03. Status: PROPOSAL — pending Richard's approval before Claude Code implements.*

---

## TL;DR

Stop stacking equity factors. They are arbitraged.

The v4 multi-sleeve plan (residual momentum + quality+low-vol + PEAD + merger-arb) is a 56-hour build that, per the same 3-gate methodology that killed those exact signals before, is statistically likely to produce a portfolio Sharpe of **1.10–1.25** rather than the claimed 1.4–1.6. Three of the four sleeves have already failed PIT validation in v3.x; assuming they pass when stacked is wishful.

The path to higher Sharpe is to add an alpha source that is **not an equity cross-sectional factor**. Specifically:

1. **Variance Risk Premium sleeve** (short SPX put-spread or short VIX-ETN ladder) — structural premium, ~0.5–1.0 Sharpe globally and ~0 correlation to momentum. Richard flagged this in `RESEARCH.md` as a top-tier retail premium then ruled it out because he thought it needed portfolio margin. It does not — defined-risk put-spreads work in a Reg-T account, and Alpaca options are now wired (v3.44 approved).
2. **Pre-FOMC drift sleeve** — Richard *measured* this himself in v1.7: +22bps single-day, Sharpe 2.35, fires 8x/yr. Built but never wired to executor. Approximately **zero crowding risk** (calendar event, not a screened universe).
3. **ML-augmented PEAD sleeve** ("Beyond the Last Surprise" formulation, ScienceDirect 2024) — not the textbook Bernard-Thomas (1989) PEAD, which is mined out at mega-cap scale. The 2024 ML version conditions on **history of prior surprises**, which roughly doubles the Sharpe per recent replication.

These three are the v5 paradigm shift. The total expected portfolio improvement is **+0.40 to +0.55 Sharpe** at lower marginal engineering cost than v4 multi-sleeve, *and* with structural (not behavioral) persistence reasons.

The proposal also kills three pieces of dead code that are leaking edge or attention: the LLM "debate" path in `main.py`, the bottom-catch sleeve, and the Wikipedia-scraped PIT universe (replace with `fja05680/sp500`).

---

## How the advisory swarm fought

Five agents wrote independent memos. Here is where they agreed and where they violently disagreed.

**Cross-agent consensus (high confidence):**

The trader, quant, and economist all independently concluded that v4's equity-factor stack is unlikely to achieve its claimed Sharpe. Trader cited Asness et al. 2018 → Arnott et al. 2023 showing residual momentum decayed from +1.2% to roughly zero net of costs. Quant cited Bailey & López de Prado on factor-zoo overfitting. Economist cited McLean-Pontiff 2016 (post-publication anomaly returns drop 58%) and Chen 2024 institutional-crowding measurements showing momentum-factor returns drop ~8% annualized per one-sigma of crowding.

The architect's parallel observation: Richard tests roughly one signal per week, has killed 8+, and his discovery rate is the limiting factor — not his validation rigor. The 3-gate is a moat; it is not the constraint.

**Cross-agent disagreement (productive):**

The trader proposed pivoting to microstructure / IPO-SPO / order flow alpha — high-frequency edge sources. Adversarial test failed: Alpaca's options API has minute-level granularity, not millisecond. The microstructure pitch overstates retail accessibility. The trader's *secondary* claim — secondary-offering (SPO) underpricing in the 2–5 day window post-pricing — survives scrutiny but the win-rate claim of 85% is overstated; published evidence (Levin & Olson 2020 cited; not directly verified post-2022) supports a smaller, noisier edge that is worth a small allocation but not a cornerstone.

The quant proposed Bayesian conditional momentum via HMM regime entropy. Adversarial test partially failed: Richard already killed the HMM regime overlay in v3.x for V-shape whipsaw (cuts gross at panic lows, buys back too late). Re-skinning the same primitive as "entropy-routed" likely re-fails for the same structural reason — regime detection at retail sample size is signal-noise constrained regardless of the wrapper.

The economist proposed FCF-weighted momentum and equal-weight S&P rotation. The FCF tilt survives adversarial test as a low-cost *modification to existing momentum*, not a new sleeve — sensible to A/B test, but probably +0.05 to +0.10 Sharpe at best, not paradigm-shifting. The equal-weight rotation pitch fails adversarial test: equal-weight has under-performed cap-weight from 2010–2024 because Mag-7 dominance is the entire market structure; timing the reversion is the hard part the pitch glosses over.

The architect proposed a feature store + research/prod split + virtual shadows for 4–6x discovery velocity. Adversarial test partial: at $10k AUM the feature store is over-engineering (the architect's own bear case is the right one), but **virtual shadow portfolios** (run candidate sleeves against LIVE-executed prices in-process, no duplicate trades) is genuinely cheap and unlocks parallel A/B testing. That piece survives; the rest does not at this account size.

**The synthesis nobody quite said:** Richard already wrote down the answer in `RESEARCH.md` 6 days ago. He listed VRP, pre-FOMC drift, and PEAD as the three top retail-accessible premiums, then mistakenly disqualified VRP for an obsolete reason. The v5 proposal is mostly *connecting his own dots*, not introducing new information.

---

## The paradigm shift

v3.x: top-15 momentum, equity-only, monthly rebalance. PIT Sharpe 0.95.

v4 (his current plan): five equity factors stacked, monthly rebalance. Claimed 1.4–1.6, realistic 1.10–1.25.

**v5 (this proposal):** equity momentum core + three uncorrelated, structurally-persistent, *non-equity-factor* sleeves. Realistic Sharpe target: 1.30–1.50. Lower correlation to AI-mega-cap regime risk. Lower drawdown.

The reason v5 reaches the same target as v4 with less complexity is that each v5 sleeve has a *structural* reason to persist:

- VRP persists because pension and insurance mandates require buying tail protection regardless of price. Retail being short vol is *taking the structurally-supplied side of an institutional flow*. McLean-Pontiff publication decay does not apply — this isn't a screened anomaly, it's a premium for absorbing risk that institutions cannot hold.
- Pre-FOMC drift persists because it is a behavioral leak by traders rebalancing into Fed announcements. Calendar-driven, no universe selection, near-zero crowding. Lucca-Moench (2015) measured +49bps; Richard's own v1.7 retest measured +22bps with Sharpe 2.35. Half-strength but real.
- ML-augmented PEAD persists because the inefficiency is in how the market processes *the sequence* of prior surprises, not the latest one. Cognitive recency bias. Per ScienceDirect 2024 "Beyond the last surprise," the ML formulation roughly doubles Sharpe vs textbook PEAD. Mega-cap PEAD is mostly mined out in its naive form; the ML form retains alpha because it requires a feature pipeline that retail competitors don't build.

These are different *kinds* of premia than the equity factors v4 plans to stack. Diversification gain across kinds is much larger than across factors of the same kind.

---

## Sleeve specifications

### Sleeve A — Variance Risk Premium (CORE NEW SLEEVE)

**What it is:** systematically sell defined-risk SPX or SPY put-spreads (e.g., 30-delta short put / 10-delta long put, ~30 days to expiry, rolled monthly), targeting capture of the gap between implied volatility and realized volatility.

**Allocation:** 15% of capital, *capital reserved* (not at-risk premium). Max loss per cycle is the spread width minus credit, sized so per-trade max loss is ~2% of total portfolio.

**Why it persists:** structural, not behavioral. Carr (2009), Bondarenko (2014), Asness et al. AQR work on global vol premium document Sharpe ratios of 0.5–1.0 in equities with >85% of months profitable. The premium exists because end-buyers of insurance (pensions, endowments, insurance balance-sheet hedgers) are mandated buyers. They pay over fair value for tail protection. Anyone willing to absorb the tail risk gets paid. Richard at $10k absorbs trivially small tails; the put-spread structure caps disasters.

**Defined risk vs naked:** never sell naked puts. Always pair with a long put 20 deltas further out-of-the-money. This caps maximum loss per trade at the spread width minus credit. A naked-put strategy at retail size in a Reg-T account is Russian roulette; a put-spread is engineered with a pre-known worst-case.

**Tail risk caveat (mandatory):** every short-vol strategy has fat-tailed loss distribution. Even with spreads, a Volmageddon-style event (Feb 2018, March 2020) can produce a -50% loss on the sleeve in a single week. Sleeve-level kill switch required: if sleeve drawdown > -25% in any 5-day window, freeze sleeve and require manual unfreeze.

**Implementation surface:**
- New module `src/trader/vrp_sleeve.py`: option chain fetch via Alpaca options API (already wired in v3.44 design), strike selection by delta, spread construction, position sizing, roll logic.
- Backtest: requires historical SPX/SPY option chain data. CBOE DataShop provides 1-min EOD data ~$50/month, or free quarterly samples for backtest validation. Alternative: use the iVolatility free historical IV surface for SPX as a proxy and discount results by 10–15% to be conservative.
- 3-gate validation per existing methodology — survivor 5-regime including 2018-Q1 (Volmageddon) and 2020-Q1 (COVID), PIT validation on out-of-sample 2023–2025, CPCV with PBO < 0.5 and deflated Sharpe > 0.

**Expected contribution:** standalone Sharpe 0.5–0.8 net of costs, correlation ~0 to momentum sleeve, expected portfolio Sharpe lift +0.20 to +0.30.

---

### Sleeve B — Pre-FOMC Drift (CALENDAR ALPHA)

**What it is:** systematically lever long S&P futures (or just SPY) from market close on FOMC eve through 2pm ET on FOMC day. Fires 8x per year (each scheduled meeting). Lucca & Moench (2015) found +49bps single-event drift on SPX in 1994–2011 sample. Richard's own 2015–2025 retest in `RESEARCH.md` measured +22bps with Sharpe 2.35 single-day — half-strength but still highly statistically significant.

**Allocation:** 10% of capital deployed only on FOMC days, idle otherwise. Sleeve-level daily exposure is 10% × 8 days = 0.7% of capital-days/year (very small footprint).

**Why it persists:** behavioral. Pre-announcement drift is hypothesized to come from leveraged-investor pre-positioning ahead of expected dovish surprise. The published edge has not been arbitraged because (a) it requires holding overnight risk that algos minimize, (b) the half-life of Fed surprise has shifted but the pre-meeting drift is driven by flow, not surprise direction.

**Implementation surface:**
- Module `src/trader/fomc_drift.py` already partially exists per `RESEARCH.md` (signal scanner shipped). Wire to executor.
- FOMC calendar: hard-coded 8 dates per year, refreshed annually from Federal Reserve calendar page. No API dependency.
- Position is binary: full sleeve allocation on FOMC day, zero otherwise. No optimization needed.
- 3-gate is straightforward — 11 years of measured data, fires 88 times in sample. Sample size is adequate.

**Expected contribution:** standalone Sharpe 1.5–2.0 on the days it fires (very high concentrated event Sharpe). Annualized portfolio contribution: 22bps × 8 / portfolio_capital × leverage factor = ~1.5–2.0% annual return on a 10% sleeve. Sharpe lift to total portfolio: +0.05 to +0.10. Small contribution, but very low cost to wire and zero crowding risk.

---

### Sleeve C — ML-Augmented PEAD (REPLACES v4's NAIVE PEAD)

**What it is:** post-earnings drift, but ranked using a model that conditions on the *history* of prior earnings surprises for the same name, not just the latest one. Per ScienceDirect 2024 "Beyond the last surprise: Reviving PEAD with machine learning and historical earnings," this formulation roughly doubles Sharpe versus single-surprise sorting.

**Allocation:** 10% of capital, ~5–8 names held at any time, 60-day average hold.

**Why this passes when v4's textbook PEAD might not:** the naive "buy after positive surprise, hold 60 days" strategy is what 25 years of academic followers have replicated; institutional flow has eaten most of it in the mega-cap window where retail can execute cleanly. The ML version requires constructing a feature vector from a name's prior 8–12 surprises (sign, magnitude, decay path, sector context). Most retail systematic systems don't bother — that gap is the persistence reason.

**Implementation surface:**
- Earnings dates and consensus: Finnhub free tier covers limited backfill. Paid tier ($50/month) covers 5+ years. Required for backtest training. Documented as line-item cost.
- Feature construction: per-name standardized unexpected earnings (SUE) sequence over prior 8 quarters; surprise sign run-length; decay slope; sector relative position.
- Model: gradient boosted trees (lightgbm) trained on rolling 5-year window, retrained quarterly. Output: cross-sectional rank-score for each name with a fresh earnings release.
- Position rule: long top-quintile rank-score, hold 60 days, exit on signal decay or +20% gain.
- 3-gate validation: same methodology, with explicit feature-leakage audit (no use of post-release data in features).

**Expected contribution:** standalone Sharpe 0.6–0.9 per ScienceDirect 2024. Correlation with momentum ~0.3 (they share trending behavior). Portfolio Sharpe lift +0.10 to +0.15.

---

### Sleeve M — Existing Momentum Core (UNCHANGED, BUT TRIMMED)

Stays at top-15 cross-sectional 12-1 momentum, weighted by score. **Allocation drops from 80% to 50%** to make room for sleeves A/B/C. Exposure is still ~50% of total capital, which preserves most of the standalone alpha while freeing capital for less-correlated sleeves.

The economist's FCF-weight tweak (multiply momentum score by `sqrt(fcf_yield_pctl)` before ranking) is approved as an A/B test variant but **not** as a default until 3-gate passes. Implementation cost is low (FCF data from yfinance; no new module needed).

---

## What to DELETE (audit of dead code leaking attention)

The system has 70+ Python files. Several are hot, several are cold but useful, and at least three are actively harmful:

**1. The LLM "debate" path in `main.py`.** `USE_DEBATE` is still wired into the live orchestrator; `find_bottoms` produces oversold candidates, `debate(candidate)` runs Bull/Bear/Risk LLM agents, and approved bottoms get up to 20% of portfolio capital. This is the pattern Richard's own CLAUDE.md explicitly lists as "verified-failed pattern" ("No LLM stock-picking"). The bottom-catch sleeve P&L attribution is documented in `CRITIQUE.md` as bug B2 (commingles momentum returns into bottom-catch attribution). **Action: rip out.** Sleeve-tag any open bottom-catch lots and let them age out, then delete `find_bottoms` from the live path. Reduces orchestrator complexity ~30%, eliminates a known attribution bug, and frees the 20% capital for the new sleeves.

**2. `iterate_v3.py` through `iterate_v14.py` family.** Eleven research scripts with overlapping purposes. The current iteration is recorded in `regime_stress_test.py` and `cpcv_backtest.py` — the iterate_v* scripts are research debt. Move to `scripts/archive/` directory. Don't delete — they document the kill-list — but get them out of the working set.

**3. Wikipedia-scraped PIT universe.** `universe_pit.py` reconstructs S&P 500 membership from Wikipedia change-history. Per `SWARM_GITHUB_RESEARCH_2026_05_02.md`, the canonical replacement is `fja05680/sp500` (832-star MIT-licensed CSV with full add/drop history back to 1996). **Action: replace primary source, keep Wikipedia as a diff-audit canary.** Mentioned in the swarm research doc as adoption #1 already; should ship before any further sleeve work because every backtest below depends on it.

**4. `ml_ranker.py` is loaded only by `regime_stress_test.py`.** It is not in any LIVE path. Either wire it into Sleeve C above or move it to `scripts/archive/`. Cold infrastructure attracts confusion.

---

## Infrastructure changes (minimum viable)

The architect's full feature-store proposal is over-engineering at $10k. The infrastructure pieces that ARE worth shipping:

**Virtual shadow portfolios.** New module `src/trader/virtual_shadow.py`. When LIVE places an order, callbacks fire on N candidate sleeves with the executed fill price. Each shadow maintains its own notional book in SQLite. No duplicate trades, no duplicate API calls. Lets Richard run 5 candidates in shadow simultaneously and accumulate the 30-day-of-shadow data required for promotion in 1/5 the wall-clock time. Estimated effort: 15 hours.

**Sleeve P&L attribution by lot.** Already partially fixed in v1.3 (per the `close_aged_bottom_catches` v1.3 docstring) via the `position_lots` table. Audit and confirm every order tags its sleeve, and that P&L attribution reads from the lot table not the order-history regex. Required for honest sleeve performance measurement under v5.

**Sleeve-level kill switch.** Existing `kill_switch.py` is portfolio-level. Add per-sleeve drawdown gate: if any sleeve drawdown < -25% in 5 trading days, freeze that sleeve and email. Especially critical for VRP (tail-fat sleeve).

That's it. No feature store, no research/prod repo split, no event-driven sleeve framework. Those are $100k-AUM problems.

---

## Sequence (no parallel work; never change two things at once)

The behavioral pre-commit rule from `CLAUDE.md` ("never change two things at once") holds. This sequence is sequential, not parallel.

| Phase | Work | Effort | Promotion gate |
|---|---|---|---|
| 1 | Audit & delete dead paths (LLM debate, iterate_v* archive, ml_ranker decision) | 6h | Tests still green; LIVE strategy unchanged behaviorally |
| 2 | Replace Wikipedia PIT universe with `fja05680/sp500` source | 4h | PIT diff between old and new sources < 1% per rebalance date over 2015–2025 |
| 3 | Build virtual shadow portfolio infra | 15h | Can run a synthetic shadow of LIVE for 5 trading days with reconciliation drift < 5bps |
| 4 | Wire sleeve B (Pre-FOMC drift) — easiest, lowest risk, fastest 3-gate | 8h | 3-gate pass; 30-day shadow |
| 5 | Wire sleeve A (VRP) — biggest expected lift, biggest tail risk | 30h | 3-gate pass with explicit Volmageddon + COVID stress; 60-day shadow (longer due to tail concern) |
| 6 | Wire sleeve C (ML-PEAD) — depends on Finnhub data subscription | 24h | 3-gate pass with leakage audit; 30-day shadow |
| 7 | Reduce momentum from 80% to 50%, integrate sleeves A/B/C | 4h | Portfolio backtest shows expected Sharpe ≥ 1.30 on PIT |
| 8 | Tests + docs + V5 release notes | 6h | Adversarial review + behavioral pre-commit re-sign |

Total: ~97 hours. Realistic timeline at part-time pace: 4–6 weeks. Faster than v4's 56 hours of *naive* effort because v4 didn't budget for the 3-gate failure rate on each of its 4 sleeves.

---

## Promotion gates (unchanged from v3.x)

Every sleeve must pass:

1. **Survivor 5-regime backtest** — Sharpe wins ≥ 4/5 regimes. For VRP, regimes must include 2018-Q1 (Volmageddon) and 2020-Q1 (COVID); failure modes are not optional.
2. **PIT validation** — Sharpe drop < 30% from survivor to PIT universe.
3. **CPCV** — PBO < 0.5, deflated Sharpe > 0, 30 OOS sub-windows.
4. **Adversarial review** — `adversarial_review.py` must pass.
5. **Override-delay 24h cool-off** — required after merge.
6. **Shadow ≥ 30 days** (60 days for VRP).
7. **Independent reviewer + spousal pre-brief** — per `BEHAVIORAL_PRECOMMIT.md`.

No exceptions, no negotiation. The 3-gate is the moat.

---

## Risks and pre-mortems

**Risk 1: VRP sleeve gets caught in a Volmageddon-style event during the live-arm period.** Mitigation: defined-risk spreads cap loss per cycle. Sleeve-level kill switch freezes after -25% drawdown. Maximum sleeve-level loss is engineered to be -8 to -12% portfolio-level even under February 2018 conditions, because the spread width × position count × hedge ratio is designed for that case.

**Risk 2: Pre-FOMC drift has decayed since Richard's v1.7 retest.** The half-strength signal (+22bps vs published +49bps) suggests it's already partially eaten. Probable additional decay over 2025–2026. Mitigation: the sleeve allocation is small (10%, 8 days/year); if 3-gate fails on fresh data, kill the sleeve permanently.

**Risk 3: ML-PEAD requires a Finnhub subscription that has unreliable historical fundamentals coverage.** Mitigation: prototype on free-tier first to verify data quality, only pay for the upgrade if Phase 4 prototype shows promise. If Finnhub data fails leakage audit, fall back to IBES via WRDS academic access (some universities provide free) or skip sleeve C entirely.

**Risk 4: All three new sleeves fail 3-gate.** This is the scenario where the v5 paradigm shift turns out to be wrong. In that case, Richard ships zero new sleeves, keeps the audit & infrastructure work (which is independently valuable), and has spent ~30 hours instead of 90+. Failure mode is bounded and informative.

**Risk 5: Behavioral risk — Richard launches v5 sleeves before adequate shadow time because he's excited about the new alpha source.** Mitigation: written pre-commit signed before Phase 4 begins; spousal pre-brief; 24h cool-off before each promotion. These are the same gates that worked in v3.x.

---

## Open questions for Richard (must answer before Claude Code starts)

1. **Approve v5 direction over v4?** v4 multi-sleeve and v5 alpha-discovery are mutually exclusive — both spend ~3 months of focused capacity. Picking one means deferring the other indefinitely.
2. **Approve the LLM-debate kill?** This removes a chunk of historically-shipped code. The bottom-catch sleeve has been LIVE through v3.x, so removing it is a real behavioral change. Pre-commit cool-off applies.
3. **Approve the $50/month Finnhub subscription** for sleeve C, conditional on Phase 4 prototype passing? Decision can defer to Phase 4 gate.
4. **Live capital target unchanged?** Roth IRA $10k, paper-trading until 90-day clock + all-3-gates pass on whichever sleeves ship. v5 doesn't change capital target or paper-vs-live discipline.
5. **Sequence preference?** Default sequence above puts the audit+infrastructure first (low-risk), then sleeve B (lowest-risk new sleeve), then sleeve A (biggest expected lift), then sleeve C. Alternative: ship sleeve A first because the expected Sharpe lift dominates. Default sequence is recommended because failing on sleeve A first would burn the most engineering hours; failing on sleeve B first burns 8 hours and is informative.

---

## Implementation handoff for Claude Code

When Richard approves, the implementing session should:

1. Read this proposal in full plus `CLAUDE.md`, `CRITIQUE.md`, `V4_PARADIGM_SHIFT.md`, `RESEARCH.md`, and `BEHAVIORAL_PRECOMMIT.md`.
2. Create a v5 milestone in the issue tracker with one ticket per phase.
3. Start with Phase 1 (audit & delete) — this is reversible and tests must remain green.
4. Each phase ends with a commit that bumps a version marker (v3.45 → v3.46 → ... → v5.0 on Phase 7) and updates `CLAUDE.md` killed-list with any sleeves that fail 3-gate during the build.
5. Never skip the 3-gate. Never ship two changes simultaneously. Use the existing adversarial-review CI gate (shipped v3.51) on every promotion PR.
6. Defer the OTM call barbell (v3.44, currently approved-deferred) until v5.0 is stable. v3.44 wiring on top of v5 is a v5.x conversation, not v5.0.

The single most important thing the implementing session must do is **run 3-gate honestly**. If sleeve A fails CPCV, kill it and document. If sleeve B fails, kill it. The hardest discipline is shipping zero sleeves rather than shipping a fake winner — that discipline is what makes the system valuable.

---

## Sources cited in the swarm

- Asness, C. S., Frazzini, A., Israel, R., & Moskowitz, T. J. (2018). "Fact, Fiction, and Momentum Investing." *Journal of Portfolio Management*.
- Bailey, D. H., & López de Prado, M. (2016). "The Probability of Backtest Overfitting." *Journal of Computational Finance*, 20(4).
- Bernard, V. L., & Thomas, J. K. (1989). "Post-earnings-announcement drift." *Journal of Accounting Research*, 27.
- Bondarenko, O. (2014). "Why Are Put Options So Expensive?" *Quarterly Journal of Finance*, 4(3).
- BOWLES, P. et al. (2024). "Anomaly Time." *Journal of Finance.* https://onlinelibrary.wiley.com/doi/10.1111/jofi.13372
- Carr, P., & Wu, L. (2009). "Variance Risk Premia." *Review of Financial Studies*.
- Chen, A. J. (2024). "Wisdom of the Institutional Crowd: Implications for Anomaly Returns." AEA Conference 2024.
- Hurst, B., Ooi, Y. H., & Pedersen, L. H. (2017, updated 2024). "A Century of Evidence on Trend-Following Investing." AQR.
- Lucca, D. O., & Moench, E. (2015). "The Pre-FOMC Announcement Drift." *Journal of Finance*, 70(1).
- McLean, R. D., & Pontiff, J. (2016). "Does Academic Research Destroy Stock Return Predictability?" *Journal of Finance*, 71(1).
- ScienceDirect (2025). "Beyond the last surprise: Reviving PEAD with machine learning and historical earnings." https://www.sciencedirect.com/science/article/abs/pii/S1544612325020057
- Hedge Fund Journal. "Harvesting the Volatility Risk Premium Globally." https://thehedgefundjournal.com/harvesting-the-volatility-risk-premium-globally/
- Alpha Architect. "Crowding and Factor Premiums." https://alphaarchitect.com/crowding-and-factor-premiums/

---

*Status of this document: PROPOSAL. Awaiting Richard's answer to the five open questions before Claude Code begins Phase 1.*
