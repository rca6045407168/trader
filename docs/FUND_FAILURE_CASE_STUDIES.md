# Fund Failure Case Studies

*Forensic memo. Synthesized from a Round-2 advisory swarm (financial historian lens, May 2026). Companion to RISK_FRAMEWORK.md, BLINDSPOTS.md, V5_ALPHA_DISCOVERY_PROPOSAL.md. Status: reference doc — pattern-match against catastrophic failures from 1998 to 2024 and identify which failure mechanisms apply at retail scale.*

---

## Why this exists

Every failure mode you can imagine has happened to a fund somewhere. The same patterns recur across decades because the underlying mechanisms — leverage, concentration, deployment failures, correlation collapse, governance breakdown — are properties of complex systems under stress, not properties of any specific era. Reading the case studies is the cheapest education available. A weekend with Lowenstein and Lewis is more useful than a quarter of additional backtesting.

This doc covers 12 famous fund failures, the mechanism that killed each one, and the specific lesson for Richard's v5. Several mechanisms apply directly. Several — leverage in particular — do not apply at retail scale and would be paranoia to spend cycles on. The discrimination matters.

---

## 1. The case studies

**Long-Term Capital Management (1998).** Two Nobel laureates (Merton, Scholes), former Salomon arbitrage desk, $5B equity, 25× leverage. Strategy: relative-value arbitrage on bond markets — buy off-the-run, short on-the-run, capture liquidity premium. Edge was real but capacity-limited. They scaled past capacity, lost 50% in one month when Russia defaulted in August 1998, requiring a Fed-coordinated bailout to prevent systemic damage. Mechanism: leverage + concentration + correlation collapse. The "uncorrelated" trades all became correlated when liquidity disappeared. Read: Roger Lowenstein, [*When Genius Failed*](https://www.penguinrandomhouse.com/books/55878/when-genius-failed-by-roger-lowenstein/) (2000). Lesson for v5: the defining property of a stress regime is that uncorrelated bets become correlated. Your scenario-conditional sizing in RISK_FRAMEWORK.md exists for exactly this.

**Amaranth Advisors (2006).** $9B multi-strategy hedge fund, lost $6B in one week on natural-gas spread bets (one trader, Brian Hunter). Self-described "multi-strategy" but in practice 80%+ of risk concentrated in a single trader's gas positions. Risk management warned; governance failed. Mechanism: single-trader concentration disguised as diversification + governance breakdown. Lesson for v5: a "multi-sleeve" portfolio is only diversified if the sleeves are *actually* uncorrelated *and* their risk budgets are independently enforced. RISK_FRAMEWORK.md's per-sleeve gross caps + factor budget caps are the structural safeguard against the Amaranth pattern. Without them, "multi-strategy" is a label, not a property.

**Bear Stearns High-Grade Structured Credit Fund (June 2007).** Two hedge funds inside Bear, leveraged subprime CDO holdings 17×. Mark-to-model on illiquid assets. June 2007 redemption requests forced fire-sale; mark-to-model collapsed; investors got pennies. Mechanism: leverage + illiquid mark-to-model + redemption mismatch. Lesson for v5: doesn't directly apply — your account doesn't take redemptions and doesn't use leverage — but the broader pattern (mark-to-model on positions that don't actually trade at the model price) is worth noting for VRP. Defined-risk options have observable mid-prices but at retail size, the mid is often a fiction; real fills are 5–15% wider. Calibrate `slippage_sensitivity.py` accordingly.

**Madoff (uncovered December 2008).** $65B Ponzi over decades. Smooth returns 10%/year with negligible drawdowns; "edge" was fabricated; assets didn't exist. Multiple SEC tips ignored. Mechanism: operational fraud + governance failure + suspension of disbelief in face of impossibly-smooth returns. Lesson for v5: if your own LIVE returns ever look "too smooth" — Sharpe > 3 sustained over 12+ months with no meaningful drawdown — the alarm should fire. That's not skill; it's a hidden bug, mark-to-fantasy, or you've stumbled into a regime that's about to mean-revert hard. The smoothness itself is the warning. Read: Diana Henriques, [*The Wizard of Lies*](https://www.henryholt.com/9780805091342/the-wizard-of-lies/) (2011).

**Galleon Group (2009).** $7B, prosecuted for systematic insider trading via a network of corporate sources. Strategy "looked like" technical/fundamental analysis but the edge was illegal information flow. Mechanism: edge that's actually information asymmetry obtained via crime. Lesson for v5: this matters specifically because BLINDSPOTS.md flagged your operator day-job intelligence as a paradigm-shift alpha source. The *legality* of that signal depends on it being aggregated, contextual industry intelligence — not specific material non-public information about specific public companies. The 72-hour lag rule and ledger discipline in BLINDSPOTS.md section 1 exists to keep that signal on the right side of the line. Read: Anita Raghavan, *The Billionaire's Apprentice* (2013).

**Knight Capital Group (1 August 2012).** Market-making firm, $440M loss in 45 minutes due to a deployment bug — old code re-activated by a flag flip on the new release sent ~4 million erroneous orders to the market. Bankruptcy in days. Mechanism: deployment bug under live conditions + no automated kill-switch fast enough to catch a 45-minute event + insufficient pre-deployment validation. Lesson for v5: this is the failure mode most directly analogous to your setup. Your GitHub Actions auto-deploys to production on push to `main`. A bad merge at 4:09pm on a rebalance day could be your Knight Capital moment. Mitigation: pre-deploy simulation gate (run the new code against the prior day's input fixture; require diff to be within tolerance before allowing deploy) + manual approval step on any change to `main.py` or `execute.py`.

**Optiver and the 2010 Flash Crash (6 May 2010).** Multiple market-makers and HFT firms experienced cascading liquidity withdrawal during the 14:32–14:45 ET event. Several firms momentarily quoted "stub" prices ($0.01 or $99,999) which executed against retail market orders. The structural lesson: every algorithmic strategy has a behavior in the absence of liquidity; if that behavior wasn't designed deliberately, it will surprise you. Lesson for v5: market orders submitted at-open during a flash-crash-like event can fill at irrational prices. The defense is limit orders with reasonable bands, plus a circuit-breaker that pauses rebalance if the previous day's close-to-open gap exceeds N standard deviations.

**Melvin Capital (January 2021).** $13B fund, lost ~50% in one week from short positions in GameStop and other meme stocks. The crowd-detected the fund's positioning via 13F filings and Reddit's r/wallstreetbets coordinated a squeeze. Mechanism: visible-positioning + crowd coordination against the fund. Lesson for v5: if your strategy ever has a position large enough to be visible (which won't happen at $10k Roth scale), assume it can be coordinated against. The deeper lesson: even sophisticated funds underestimate how visible their positions are to a determined adversary. Public 13F filings, prime-broker disclosures, and inferred-from-options-chain positioning all leak.

**Archegos Capital (March 2021).** Bill Hwang's family office, leveraged 5–8× via Total Return Swaps with multiple prime brokers (each broker thought they were the only counterparty). Concentrated long positions in Viacom, Discovery, Chinese ADRs. When ViacomCBS dropped 30%, margin calls hit; broker liquidations cascaded; $20B+ losses across Credit Suisse, Nomura, Morgan Stanley. Mechanism: hidden leverage via derivatives + counterparty-side blindness + concentration. Read: Robert Kelly, *The Fall of the House of Hwang* (2023). Lesson for v5: doesn't directly apply (no leverage, no swaps), but worth knowing for when v6 considers options leverage. The pattern — derivative leverage that doesn't show up in your own gross/net calculations — is a recurring trap.

**Three Arrows Capital (June 2022).** Crypto fund, ~$10B AUM, leveraged DeFi positions, blew up alongside Terra/LUNA. Mechanism: same as Archegos — hidden leverage, concentration, counterparty cascade. The crypto-specific lesson: liquidity in crypto markets is fundamentally different from equities; "deep" markets in Tier-1 tokens can become illiquid in hours. Lesson for v5: not directly applicable (you're equity-only) but the broader lesson — that any "diversifier" must be tested under the regime where it stops being a diversifier — applies to VRP and FOMC drift sleeves under stress regimes.

**FTX / Alameda Research (November 2022).** $32B exchange + sister hedge fund. Customer funds commingled with prop trading; mark-to-fantasy on FTT token holdings; no real risk function. Sam Bankman-Fried convicted of fraud. Mechanism: operational separation failure + mark-to-fantasy + governance vacuum + outright fraud. Lesson for v5: the structural separations matter even at retail scale. Your Roth IRA assets are custodian-held, which is the single most important property of the structure. The custodian is the operational separation that makes you safer than FTX's customers.

**Tiger Global / Coatue / ARKK class (2021–2022).** Growth-tech hedge funds and Cathie Wood's ARKK ETF, peak AUM $90B+ collectively. Concentrated bets on high-multiple tech (Zoom, Peloton, Roku, etc.) compounded by 2021 SPAC mania. 60–80% drawdowns over 18 months as rates rose and growth multiples compressed. Mechanism: concentrated bet on a single thesis (low-rate-driven multiple expansion) that reversed when rates moved. The "smart money" was just as crowded as retail. Lesson for v5: your top-15 momentum sleeve has implicit factor-loading on Mag-7 / AI-bull regime. If that regime ends abruptly, momentum sleeve drawdown is unbounded by anything except your behavioral pre-commit. The economist's voice in V5_ALPHA_DISCOVERY_PROPOSAL.md flagged exactly this. Tier 3 deep-history scenarios in SCENARIO_LIBRARY.md (Volcker, dot-com bust) test the analog. Read: Sebastian Mallaby, [*More Money Than God*](https://www.penguinrandomhouse.com/books/304541/more-money-than-god-by-sebastian-mallaby/) (2010, on the broader hedge-fund era patterns).

---

## 2. The taxonomy of fund failure mechanisms

Eight mechanisms cover essentially every catastrophic failure. For each, the cases that exhibit it and the v5-applicability call:

**Leverage + concentration.** LTCM, Bear Stearns High-Grade, Archegos, 3AC. Not applicable to v5 (no leverage, no concentration above 30% per name). The single biggest reason retail systematic systems can survive shocks that kill funds: no leverage means no margin call cascade.

**Hidden derivatives leverage.** Archegos, 3AC. Not applicable until v5 wires options. When it does, the OTM call barbell (v3.44) and short-VRP put-spreads must compute and report effective delta-equivalent leverage in the daily risk report.

**Single-trader concentration disguised as diversification.** Amaranth, Madoff. Applicable in mechanism: the v5 "four sleeves" structure is only diversified if the sleeves are actually uncorrelated and independently risk-budgeted. RISK_FRAMEWORK.md's per-sleeve and per-factor caps are the structural defense.

**Operational / deployment.** Knight Capital, 2010 Flash Crash. **Highly applicable.** Your GitHub Actions auto-deploy + cron-driven daily run is the closest analog to Knight's setup in any retail trading system. Pre-deploy fixture-replay gate is the mitigation.

**Crowding / regime shift.** Tiger / Coatue / ARKK, 2007 quant quake. Applicable. McLean-Pontiff post-publication decay (cited extensively in TESTING_PRACTICES.md and INFORMATION_THEORY_ALPHA.md) is the slow version; sudden regime shifts (rates 2022, dot-com 2000) are the fast version.

**Fraud / governance.** Madoff, FTX/Alameda. Not applicable to your operational setup (custodian-held, no investors), but Madoff's lesson about smooth returns being the warning sign applies universally.

**Liquidity mismatch.** Bear Stearns High-Grade, 1998 LTCM. Not directly applicable (no redemption obligation), but the related lesson — that observed mid-prices on illiquid instruments lie about realizable value — applies to VRP option pricing.

**Counterparty.** Archegos cascaded across multiple prime brokers. Not applicable at retail (single broker, custodian-held, account-segregated). Document for when scaling.

**Slow alpha decay.** GLG, various man-quant equity, the Tiger class as it played out. Highly applicable. Your weekly degradation check is the early-warning system. McLean-Pontiff says expect 58% decay post-publication on factor strategies; design v5 sleeves with explicit kill-or-keep decay-thresholds.

**Information-edge that's actually illegal.** Galleon. Applicable specifically to the operator thesis-ledger sleeve. The ledger discipline in BLINDSPOTS.md exists to keep that sleeve on the right side.

---

## 3. The five lessons most directly applicable to v5

Ranked by likelihood × impact:

**Lesson 1 — Knight Capital deployment gate.** GitHub Actions auto-deploying changes to `main.py` or `execute.py` creates a real Knight Capital risk. Implement a pre-deploy fixture replay: any PR that touches the live trading path must pass a "given yesterday's input, produce decisions within X% diff of yesterday's actual decisions" gate before it can merge. Alternative: require manual approval (not just CI green) on those specific files. Mitigation cost: 4 hours.

**Lesson 2 — multi-sleeve correlation governance.** Amaranth-style "diversification in name, concentration in fact" is the v5 risk if sleeves correlate to 1.0 in stress. The scenario-conditional sizing in RISK_FRAMEWORK.md section 4 is the structural defense. Run the correlation matrix audit weekly; if observed cross-sleeve correlation > 0.5 in normal regime, halve the smaller sleeve.

**Lesson 3 — smooth-returns alarm.** Madoff's specific lesson: if LIVE Sharpe ever sustains > 3.0 with no meaningful drawdown over 12+ months, suspect a hidden bug or mark-to-fantasy before believing it's edge. Add an explicit "too good to be true" gate to weekly degradation check: alert if rolling-90-day Sharpe > 2.5 *and* max-DD < 1%.

**Lesson 4 — slow alpha decay tracking.** The Tiger / ARKK / GLG pattern — strategies that worked for years and then didn't — is the highest-probability long-horizon failure for v5. Implement explicit decay tracking on each sleeve: 6-month rolling Sharpe, 12-month rolling Sharpe, half-life of the mutual-information signal (per INFORMATION_THEORY_ALPHA.md). Pre-commit a kill rule: any sleeve whose 12-month rolling Sharpe drops below 0.5 of its backtested expectation for two consecutive quarters gets demoted to shadow status.

**Lesson 5 — visible-positioning awareness.** Melvin's lesson scaled down: even at $10k, *if* the operator thesis-ledger sleeve goes LIVE, the universe of positions becomes inferable from your LinkedIn / primary-work / Apollo activity. Do not publicly comment on specific public-company tickers in operator-context channels in the days surrounding any trade. This is a behavioral pre-commit, not a code change.

---

## 4. Lessons that DON'T apply at retail $10k scale

Worth being explicit about, so you don't waste cycles on the wrong concerns:

- **Leverage cascades** (LTCM, Archegos, 3AC). You have no leverage. No margin call can compound.
- **Counterparty failure** (Archegos via prime brokers). Single broker, account-segregated, SIPC-insured up to $500k. Not your problem.
- **Redemption mismatch** (Bear Stearns High-Grade, 1998 LTCM). No outside investors.
- **Concentrated single-trader bet disguised as diversification** (Amaranth) — only applies if your "diversification" claim is false. The structural defense in RISK_FRAMEWORK.md addresses this.
- **Mark-to-model fraud** (FTX, Madoff) — not applicable to your operational setup.

The honest framing: out of 12 famous fund failures, roughly 2–3 mechanism-classes apply meaningfully to your retail Roth at v5. The rest are interesting but not load-bearing. Don't over-rotate.

---

## 5. The single book to re-read before LIVE arming

If you read one thing before flipping the LIVE switch, read **Roger Lowenstein, *When Genius Failed: The Rise and Fall of Long-Term Capital Management*** (2000). Three reasons:

The first is structural: LTCM had the most sophisticated risk apparatus of any fund of its era. Two Nobels, the best statistical models, more PhDs than any rival. They still blew up. The lesson is not that the models were wrong — the lesson is that *all* models have a tail outside the domain where they were fit, and *every* fund eventually meets that tail. Your CPCV + Deflated Sharpe + EVT framework is meaningful; it doesn't make you immune. Knowing this in advance changes how you respond when the kill-switch eventually fires.

The second is behavioral: LTCM's principals had a chance to take chips off the table at the peak. They didn't. They took *more* leverage instead. The behavioral failure mode wasn't ignorance; it was an inability to scale risk down when their own success had pushed them beyond the regime their models were fit for. The behavioral pre-commit in your `BEHAVIORAL_PRECOMMIT.md` is doing the same work LTCM's risk team failed to do.

The third is humility-calibrating: LTCM was bailed out by a Fed-coordinated consortium because their failure threatened the global financial system. Your failure won't threaten anything except your Roth IRA. That's a feature, not a bug. The smaller stakes mean you can afford to actually execute the discipline LTCM couldn't, *because the cost of being wrong is bounded*. Reading the book reinforces what's actually different about your situation: not the strategy, the absence of leverage and external accountability that gives you the margin to be honest.

Adjacent secondary reads, in priority order: Sebastian Mallaby, [*More Money Than God*](https://www.penguinrandomhouse.com/books/304541/more-money-than-god-by-sebastian-mallaby/) (2010) for the broader hedge-fund era; Scott Patterson, [*The Quants*](https://www.penguinrandomhouse.com/books/202090/the-quants-by-scott-patterson/) (2010) for systematic-strategy failures; Gregory Zuckerman, [*The Man Who Solved the Market*](https://www.penguinrandomhouse.com/books/591538/the-man-who-solved-the-market-by-gregory-zuckerman/) (2019) for what works and how rare it is.

---

## Sources

- Lowenstein, R. (2000). *When Genius Failed*. Random House.
- Henriques, D. (2011). *The Wizard of Lies*. Holt.
- Raghavan, A. (2013). *The Billionaire's Apprentice*. Hachette.
- Mallaby, S. (2010). *More Money Than God*. Penguin Press.
- Patterson, S. (2010). *The Quants*. Crown Business.
- Zuckerman, G. (2019). *The Man Who Solved the Market*. Penguin.
- Kelly, R. (2023). *The Fall of the House of Hwang*. (On Archegos.)
- Bloomberg, FT, Institutional Investor reporting on Archegos (March–April 2021), 3AC (June 2022), FTX (November 2022).
- Knight Capital SEC investigation report ([Release No. 70694, October 2013](https://www.sec.gov/litigation/admin/2013/34-70694.pdf)).
- 2010 Flash Crash CFTC-SEC joint report ([September 2010](https://www.sec.gov/news/studies/2010/marketevents-report.pdf)).

---

*Last updated 2026-05-04. Status: REFERENCE doc. Five lessons in section 3 are actionable inputs to the v5 build sequence.*
