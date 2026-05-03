# What we haven't covered — blind-spot audit

*Companion to V5_ALPHA_DISCOVERY_PROPOSAL.md and SCENARIO_LIBRARY.md. Surfaces the gaps Richard implicitly asked about. Status: research note. Most of these need a sharp answer before v5 ships, even if the answer is "deliberate accept."*

---

## 1. The biggest one — operator-grade alpha from FlexHaul

You are Head of Growth at FlexHaul.ai, a multi-modal logistics platform covering ocean, air, FTL, LTL, drayage, parcel. You see real-time signal from operating in this market that no public quant can buy:

- LTL / FTL spot-rate moves before they show up on TruckStop or DAT
- Carrier-specific customer-satisfaction data on ODFL, SAIA, KNX, XPO, JBHT, CHRW, EXPD, ARCB, ODFL, FDX, UPS — names that are publicly traded
- 3PL margin pressure as it accumulates in conversations, weeks before it shows in 10-Q
- Cross-border China-US flow shifts via Apollo prospect activity, XHS content, WeChat carrier outreach
- Parcel-network dynamics from the Partnerships/Parcel/ folder

This is structural information asymmetry. It is not insider information (you are not an insider at any of these public companies); it is the same kind of signal a Bloomberg-terminal industry analyst tries to construct from scratch out of channel checks, except you produce it as a byproduct of your day job. The edge half-life before it diffuses to public data is probably 2-6 weeks.

A logistics-sector tilt or pair-trade sleeve built on this signal is in a different category than the v5 sleeves (VRP / FOMC / ML-PEAD), which are all public-information factor strategies. This sleeve would be **proprietary alpha**. The right way to wire it is a "thesis ledger" — every meaningful FlexHaul observation about a public-carrier or shipper public-co gets logged with date, ticker, direction (positive/negative for the company's fundamentals), and confidence. Then a backtest on the ledger answers whether the signals translated to forward returns. If yes, that becomes a 10-15% sleeve allocation, monthly-rebalanced or event-rebalanced.

The right operational guardrail: ledger is mandatory for every observation regardless of trade outcome (so survivorship bias doesn't creep in over time), and there is a 72-hour minimum lag between observation and trade (so you cannot accidentally enter material non-public information territory if a private-co customer says something about a public-co partner).

This is the paradigm shift larger than VRP. Effort to build the ledger: ~10 hours. Effort to backtest: depends on how much retrospective signal you can reconstruct. Worth a dedicated v5.1 sleeve once core v5 is stable.

---

## 2. Operational risks not in `kill_switch.py`

`kill_switch.py` covers portfolio-level drawdown gates and `risk_manager.py` covers position-cap math. Neither covers the failures listed in `CAVEATS.md` exhaustively. The remaining surface:

**Single point of failure on the cron host.** The system runs daily after market close. If the host (whatever it is — local machine, GCP, ec2) is down at 4:10pm PT on a rebalance day, the orchestrator silently doesn't fire. Detection: the existing `weekly_degradation_check` won't catch a daily missed-run quickly enough. Fix: a "did the daily run actually fire?" alert that pings if `daily_snapshot` table has no row for today by 7pm PT.

**Alpaca API outage on a rebalance day.** Alpaca went down for hours during the August 2024 yen unwind. If an outage straddles your scheduled rebalance, what does the system do? Currently: probably retries until success, possibly placing orders into an irrational market when the API recovers. Fix: a "broker-availability check" before every rebalance; if Alpaca was down for >2h in the prior 24h, defer rebalance by 24h and email.

**Library version drift.** `yfinance` and Alpaca's Python client have both made silent breaking changes in the past 18 months (yfinance Adj Close removal in 2024 and the schema flip on `Ticker.history()` are both documented bugs that broke real systems). You have `pyproject.toml` but no version pins observable in the audit. Fix: pin every dependency, dependabot or weekly automated diff against a known-good lockfile.

**API key rotation.** Your `.env` has `ALPACA_KEY` and `ALPACA_SECRET`. Have they ever been rotated? Were they ever committed to git history before you `.gitignore`d them? `git log -p .env` would tell you. If yes, rotate immediately.

**LLM-in-path failure.** Your `copilot.py` and `adversarial_review.py` call Claude. If Anthropic API is down or rate-limited, what's the fallback? Currently the daily report generator probably fails silently. Fix: every LLM call wrapped in a try/except with a `notify("LLM call failed")` and a non-LLM fallback path.

**Backup of the SQLite journal.** `journal.py` is your single source of truth for fills, decisions, lots, and snapshots. Is the .db file backed up off-host on a schedule? What's your recovery RTO if the disk dies tonight? This is the "what if your laptop falls in a pool" test.

**Estate / continuity.** If something happens to you, can your spouse access the Roth IRA, the cron host, the Alpaca account, the Github repo? This is in `BEHAVIORAL_PRECOMMIT.md`'s spirit but not its letter. A documented runbook handed to one trusted person is the minimum.

---

## 3. Tax / regulatory / account-structure gaps

**Roth IRA contribution cap.** $7,000/yr. Even a 30%/yr Roth IRA at $10k caps lifetime build-up by orders of magnitude. The strategy success scenario is bottlenecked by your ability to scale into a non-IRA account. Have you thought through that transition? At what AUM does the system move from "Roth IRA only" to "Roth IRA + taxable mirror"?

**Pattern Day Trader (PDT) rule.** Sub-$25k accounts are limited to 3 day-trades per 5 trading days. If v5's VRP sleeve ever needs to roll mid-day or close a spread early, that's a day-trade. If FOMC drift rolls intraday, that's a day-trade. Have you confirmed PDT non-trigger paths for every sleeve? If not, document it now.

**Options approval level on Alpaca.** Defined-risk spreads require Level 2 or 3 (varies by broker). Naked option selling requires Level 4 + portfolio margin. You may have approval for the v3.44 long-call barbell but not for short-side spreads. Verify.

**Wash-sale rules.** Don't apply in Roth IRA. Will apply when you mirror to taxable. Momentum strategies are wash-sale magnets — selling a name and buying back within 31 days is the *core mechanic* of monthly rebalanced top-N. Plan ahead.

**1099-B reporting.** Alpaca generates these. Have you sanity-checked one? Missing or mis-categorized lots cause 6-month tax-software pain.

**Beneficiary on the Roth IRA.** Set? Spouse? Ideally a real human verified to have access.

---

## 4. Behavioral failure modes not in pre-commit

`BEHAVIORAL_PRECOMMIT.md` covers drawdown discipline (no manual override after -15%), spousal pre-brief, override-delay 24h cool-off. Things it does not cover:

**Founder's regret at LIVE arming.** v5 ships, paper-trades for 90 days, hits the LIVE arming gate. You hesitate. You say "let me run another 30 days of shadow." This is the most common pattern in research-to-production transitions. Pre-commit answer: a written "if I am hesitating to arm LIVE on the day the gate clears, here is my rule" — either a fixed deadline (arm on day 91 regardless) or a fixed condition (arm if in-window Sharpe > 1.0, else kill the project).

**Life-event auto-pause.** If FlexHaul has a fundraise, acquisition, customer crisis, or personnel emergency that needs your full attention, the trading system should auto-pause for N days. Currently it has no concept of "Richard is too busy to monitor this safely." Fix: a manual `PAUSE_REASON` env var and a reminder to set it during the obvious life events. Or a simpler version: every Sunday night, you must explicitly type "ARMED" into a one-shot prompt, otherwise the system stays paused all week.

**Health change.** Same pattern. Document the rule.

**Cognitive overload.** You manage 70+ files in trader, ~50+ skills + scheduled tasks for FlexHaul, an Apollo pipeline, LinkedIn engagement, customer operations. The trader codebase has more methodology than the actual capital justifies. **Honest self-test: is the trader system generating attention residue that costs you 5+ hrs/week of focus you should be spending on FlexHaul GTM?** If yes, that's a real cost not currently in the EV calc. (See section 8.)

**The "1.5 Sharpe" success failure mode.** If v5 hits 1.5 Sharpe consistently for 6 months, will you scale capital correctly or torpedo it? The Munger answer is "back the truck up." The correct retail-investor answer is much more conservative because retail attribution-to-luck is high. Pre-commit a scaling rule *now* before the temptation is real. Suggested rule: only scale Roth IRA capital up to the legal contribution cap. Do not move taxable money in until 3 years of LIVE Sharpe > 1.0 with deflated-Sharpe-significant edge.

---

## 5. Portfolio context outside the trading system

The right question is not "what Sharpe does my Roth IRA earn?" — it's "given the rest of my financial life, where should this $10k bucket sit?" Things missing from the analysis:

- Emergency fund (3-6 months expenses) — does it exist, where is it, what's the yield?
- Liquid taxable brokerage — current allocation? SPY-equivalent index? Concentration?
- Any 401(k) — current balance and allocation? Probably target-date fund?
- FlexHaul founder equity — concentration risk (you're already long freight-tech via your job; do you really want a logistics-sector trading sleeve on top of that?)
- Real estate, crypto, other
- Spousal income and assets

A v5 strategy that earns +1.5 Sharpe on $10k and adds 0.05 to total household Sharpe is great. The same strategy that consumes 5 hrs/week of your attention is bad if those 5 hrs would otherwise be spent on FlexHaul (which is your highest-EV asset by orders of magnitude).

This is not a numerical claim, it's a framing one. The trading system is one bucket in a larger portfolio. Optimize the bucket only after you're sure the larger portfolio is structured correctly.

---

## 6. Performance metrics you don't track

Your current stats: Sharpe, max-DD, monthly returns, win rate, alpha vs benchmark. Gaps:

- **Sortino ratio** — same as Sharpe but only penalizes downside vol. For asymmetric strategies (VRP, momentum), Sortino is more honest than Sharpe.
- **Calmar ratio** — CAGR / max-DD. Captures the trade-off Sharpe ignores. For retail-with-behavioral-risk, Calmar is often the right metric.
- **Omega ratio** — full distribution-aware. Best for strategies with skew.
- **CVaR / Expected Shortfall at 95%, 99%, 99.5%** — what's the average loss in the worst-5% / worst-1% of months? This is the number that matters for tail-risk-bound strategies like VRP.
- **Time underwater** — average and max days from a peak before recovering. Behavioral relevance: how long can you watch your account be down before you tap out?
- **Maximum runup before drawdown** — the symmetric counterpart to max-DD. If you're up 60% in 4 months, that's a behavioral risk too (overconfidence, sizing errors).
- **Tracking error vs SPY** — at what point does deviating from the benchmark cost you sleep?

The existing `perf_metrics.py` has hooks for some of these. Extend to compute all 7 on every backtest report and on the daily LIVE snapshot.

---

## 7. External / human review

Every gate in your system (3-gate, adversarial review, behavioral pre-commit, mistake-db) is generated and verified inside the same Claude+codebase ecosystem. That is a known failure mode — the reviewer shares the priors of the reviewed. An external human review is missing from the loop.

Concrete options: the [Quantitative Finance Stack Exchange](https://quant.stackexchange.com/) for narrow methodology questions; r/algotrading for general code review; a quant friend / former colleague paid in lunch; an academic mentor if you have one. Cost: a few hours per quarter. Catches: methodology bugs and survivorship-bias errors that the same-ecosystem reviewer cannot see.

The strongest version of this is **public pre-registration of v5 results** before LIVE. Write the v5 thesis, expected Sharpe range, and the falsifying conditions in a public Github gist or blog post, dated. After 12 months of LIVE, publish results regardless of outcome. This costs you nothing and the accountability force-function is enormous. Replication-crisis literature (Bailey-López de Prado) shows that pre-registered strategies have ~50% lower discovered-vs-live Sharpe gap. Free alpha to the discipline.

---

## 8. The opportunity cost of v5 itself

The honest top-down EV calculation, brutal version:

- v5 build: ~100 focused hours. Plus 5-10 hrs/week of monitoring overhead during the 90-day shadow → live transition. Call it 200 hours total over 6 months.
- Expected v5 lift over v3.42: ~+0.4 Sharpe at $10k AUM. In dollar terms on a $10k account, that's roughly +$400-800/year of additional expected return.
- Same 200 hours spent on FlexHaul GTM at pre-seed stage: realistically the marginal ARR contribution is probably $50k-500k+ in deal value at your conversion rates. EV is *3-4 orders of magnitude higher.*

The trader system has non-financial value: it's a learning platform, it builds discipline, it's a hedge against career risk if FlexHaul fails, it's interesting. None of that is zero. But the EV math says **the trader system is a lifestyle / hobby asset, not a wealth-creation asset, until it has 3+ years of LIVE evidence and you can scale beyond the Roth IRA cap.**

The actionable implication: time-box v5 hard. If Phase 1-3 (audit, PIT swap, virtual shadow) doesn't ship in 30 days of focused work, that signals a deeper engineering problem and the project should be paused, not extended. A research project that overruns its budget is usually telling you something is wrong with the underlying premise.

---

## 9. Competitive / structural risks to v5 specifically

**VRP capacity at scale.** SPX 30-delta put-spread liquidity supports ~$10M before book impact. At $10k you're invisible. Document the threshold.

**FOMC drift decay.** Your own retest measured +22bps vs Lucca-Moench's published +49bps — half-strength after publication, classic McLean-Pontiff decay. Probable additional decay to +10-15bps over 2025-2030. Sleeve B might earn a small contribution for a year then trend to zero. Plan the kill-or-keep gate.

**ML-PEAD model overfit.** LightGBM on 5-year rolling earnings-surprise features is small-sample by ML standards. Walk-forward retraining is essential, and the test for overfit (out-of-sample IC < in-sample IC by >50%) must be run quarterly. Build in the kill switch.

**The 2026-2030 monetary regime is unknown.** Every backtest you have lives in either ZIRP-era or rate-shock-era market structure. The next 5 years may be a different regime entirely (sustained 3-5% real rates, multipolar reserve currency, deglobalized supply chain). Strategies optimized for 2010-2024 may not transfer. Tier 3 deep-history scenarios (Volcker shock, 1970s stagflation, 1985 Plaza Accord) partially address this. Forward-scenario Volcker 2.0 is the explicit test.

---

## 10. Things genuinely outside the system that could end it

True black swans, by definition, can't be enumerated. But documented unknowns:

- AGI productivity shock — what happens to systematic factors when AI productivity adds ~5% real GDP growth in 18 months? No analog.
- Cyber attack on US financial infrastructure — partial precedent in 2010 Flash Crash and 2008 short-sale ban; no full precedent.
- Reserve currency rotation — what happens to USD-denominated equity returns in a multi-reserve world? 1985 Plaza is a partial precedent only.
- Systemic AI vendor failure — if Anthropic / OpenAI / Google have a multi-day outage and your dependent systems silently degrade at the same time as 1000 other systems do.

Document them; do not pretend you can backtest them. They exist as known unknowns and the system's job is to fail gracefully (kill switch + manual override + human review), not to predict them.

---

*Last updated 2026-05-03. Status: research note. Most items here need a yes/no/defer decision from Richard before v5 implementation begins.*
