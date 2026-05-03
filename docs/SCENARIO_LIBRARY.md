# Scenario Library — Stress Testing v5

*Companion document to `V5_ALPHA_DISCOVERY_PROPOSAL.md` and `scripts/regime_stress_test.py`. Defines the full set of historical and hypothetical scenarios every strategy variant must survive before promotion. Replaces the 5-regime list currently in `regime_stress_test.py:130`.*

---

## Why expand from 5 regimes to ~25

The current REGIMES tuple covers 2018-Q4, 2020-Q1 COVID, 2022 bear, 2023 AI rally, and recent 3 months. That's enough to sanity-check momentum but it has three holes that bite v5 specifically:

The first hole is **vol-regime shocks**. Sleeve A (VRP) is short volatility. Its critical failure modes are not slow bears — they are the days the VIX prints 50+ from a complacent base. February 2018 (Volmageddon, XIV blowup) and August 5, 2024 (yen carry unwind, intraday VIX 65) are the canonical retail-killer events. Neither is in the current REGIMES list.

The second hole is **monetary-regime shocks**. Sleeve B (Pre-FOMC drift) is calendar-driven. It must demonstrate it works through both dovish-surprise and hawkish-surprise eras. The 2013 Taper Tantrum (May–June 2013) is when pre-FOMC drift inverted; the 2022 fastest-hiking-cycle is when every FOMC meeting was a downside risk. Neither is in REGIMES.

The third hole is **microstructure / liquidity shocks**. Any strategy that submits market-on-open orders has tail risk from days like the August 24, 2015 ETF flash crash (where SPY printed 30+% below intrinsic value at the open) or the August 2007 quant quake (when systematic factors decorrelated for 72 hours). These are 1-week events, not 6-month windows. Calendar-window backtests miss them entirely.

Plus the user has explicitly named scenarios — Iran 2026, drought/famine, oil contango, tariffs — that are not yet in any history-window. Those need synthetic / scripted replay, not just historical replay.

The library below covers all three holes, plus the historical baseline.

---

## Taxonomy: shocks organized by what they stress

A useful library is not a list of dates. It is a list of **failure mechanisms**, each represented by 1–3 historical episodes. The strategy is robust if it survives the mechanism class, not if it happens to survive one episode of it.

**1. Trending-bull regimes** — strategy must capture upside without being whipped out by mid-trend pullbacks. Test cases: 1995–2000 internet bull, 2003–2007 housing bull, 2009–2020 post-GFC bull, 2023–2025 AI bull. Failure mode: defensive overlays that cut exposure on every dip and miss the trend (this is exactly why HMM regime overlay was killed in v3.x).

**2. Slow-grind bears** — strategy must avoid ratcheting losses; momentum often prints negative for 18+ months. Test cases: 2000–2002 dot-com unwind, 2007–2009 GFC, 2022 rate-shock bear. Failure mode: momentum keeps "buying the dip" of last quarter's winners that are now this quarter's losers (momentum-crash mechanism, see Daniel-Moskowitz 2016).

**3. Sudden crash + V-recovery** — strategy must avoid selling at the bottom and being long-flat at the recovery. Test cases: 1987 Black Monday + recovery, 2010 Flash Crash, 2020-Q1 COVID, 2018-Q4 Powell-pivot recovery, 2024 yen-unwind recovery. Failure mode: defensive gates fire at panic lows, buy back at higher prices (the v3.x regime-overlay failure mode that cost -34pp in 2020-Q1).

**4. Vol-regime shocks** (CRITICAL FOR VRP) — strategy must survive the rare but brutal days when VIX expands 30+ vol points in a session. Test cases: Feb 5–9, 2018 Volmageddon (XIV terminated), March 9–18, 2020 COVID vol expansion, October 2008 Lehman/AIG/TARP week, August 5, 2024 yen-unwind day. Failure mode: short-vol positions that don't have defined-risk hedges; spread-width sized to "normal" market that gets gapped through.

**5. Liquidity / microstructure shocks** — strategy must survive when bid-ask widens 10x and circuit breakers fire. Test cases: August 2007 quant-quake (statistical arb factors blew up over 72h), May 6, 2010 Flash Crash (S&P -9% intraday), August 24, 2015 ETF Flash Crash (SPY -7% at open), August 2019 repo spike, March 2020 Treasury market dysfunction, March 2023 SVB/regional bank panic. Failure mode: market orders submitted at open-on-rebalance day fill at irrational prices.

**6. Geopolitical risk-off** — strategy must survive sudden cross-asset risk-off days. Test cases: September 11, 2001 (markets closed 4 trading days, reopened down 14%), October 2002 Iraq war buildup, August 2008 Russia-Georgia, March 2014 Crimea, February 24, 2022 Russia-Ukraine, October 7, 2023 Hamas-Israel, April 13, 2024 Iran-Israel direct strikes. Failure mode: positions sized to normal vol get dislocated by single-day 5+ sigma moves.

**7. Monetary-policy shocks** — strategy must survive Fed surprises in both directions. Test cases: February 1994 Fed surprise hike, May–August 2013 Taper Tantrum, December 2018 hawkish hike + Powell market-doesn't-care comments, March 2020 emergency cut, March 2022 first hike of fastest cycle, September 2024 first cut of cutting cycle. Failure mode: pre-FOMC-drift strategies that worked in dovish era (Lucca-Moench sample) fail when meetings are net-hawkish.

**8. Currency / sovereign / cross-asset contagion** — strategy must survive non-equity-origin shocks that propagate to equities. Test cases: March 1994 Mexican Peso crisis, July 1997 Asian Financial Crisis, August 1998 Russian default + LTCM, May 2010 European sovereign debt (EFSF), September–October 2022 GBP gilt / LDI crisis, August 2024 yen-carry unwind. Failure mode: equity-only models miss the leading indicator from cross-asset; positions sized to equity vol get killed by FX-crisis-driven equity moves.

**9. Commodity / supply-chain shocks** — strategy must survive commodity-driven equity dislocations. Test cases: August 1990 Kuwait invasion oil spike, July 2008 oil to $147 / July 2014 oil collapse start, April 20, 2020 negative WTI prices, February 2022 wheat/grain spike (Russia is world's #2 wheat exporter), 2023–2024 Panama Canal drought (water-driven shipping delays). Failure mode: sector concentration in energy/materials/industrials gets gap-moved by commodity events.

**10. Trade policy / tariff shocks** — strategy must survive policy-driven sector rotation. Test cases: March–December 2018 US-China trade war Phase 1, June 2016 Brexit referendum, January–February 2025 Trump tariff regime initiation, April 2025 reciprocal-tariff escalation. Failure mode: factor models keyed to pre-tariff sector correlations break.

**11. Sector-specific bubble / bust** — strategy must survive concentration unwinds. Test cases: March 2000 dot-com top, July 2007 housing/financials top, August 2014 oil-and-gas top, January 2021 GameStop / meme-stock cycle, November 2021 ARKK / SPAC top, May 2022 Luna/Terra crypto, March 2023 SVB-Signature regional bank panic. Failure mode: top-N-momentum strategies are most concentrated at the worst possible moment (long every name in the bubble going into the bust).

**12. Tail / structural events** (forward-looking, scripted) — synthetic scenarios for shocks that haven't happened yet but are plausible 1–5 year tails. Need Monte Carlo or hand-scripted replay since no historical data exists. Discussed in section 4.

---

## Section 1: The historical regimes table

This is the proposed replacement for `REGIMES` in `regime_stress_test.py`. Each entry is `(name, start, end, taxonomy_class, expected_market_context)`. Strategies must report Sharpe + max-DD per regime; gate-1 promotion requires a defined pass-rate across the full set.

The table is split into TIER 1 (must-pass; failure on any single one is a kill) and TIER 2 (should-pass; failure permitted but documented).

### Tier 1 — must-pass historical regimes

| Name | Window | Class | Stress mechanism |
|---|---|---|---|
| 2008 GFC | 2008-09-01 to 2009-06-30 | slow-bear + liquidity | -56% S&P, VIX 89, deepest historical drawdown; tests every gate simultaneously |
| 2018 Volmageddon | 2018-02-01 to 2018-02-28 | vol shock | VIX 9 → 50 in 4 days; XIV terminated; canonical short-vol disaster |
| 2018-Q4 selloff | 2018-09-01 to 2019-03-31 | crash + V-recovery | -20% S&P then full recovery; tests defensive-overlay whipsaw |
| 2020 COVID | 2020-01-15 to 2020-06-30 | crash + V-recovery + vol shock | -34% S&P in 22 days, VIX 82, fastest-ever recovery |
| 2022 bear | 2022-01-01 to 2022-12-31 | slow-bear + monetary shock | -25% S&P, fastest hiking cycle in history, momentum reversal |
| 2024 yen unwind | 2024-08-01 to 2024-08-31 | vol shock + cross-asset | VIX 16 → 65 intraday Aug 5; carry-trade cascade |
| 2025 tariff regime | 2025-01-15 to 2025-06-30 | trade-policy + sector rotation | Trump reciprocal tariffs; tests factor model under policy regime change |
| 2023 AI rally | 2023-04-01 to 2023-10-31 | trending bull + narrow breadth | Mag-7 leadership; tests whether momentum captures concentration alpha |
| Recent 3 months | (rolling) | current regime | rolling sanity check |

### Tier 2 — should-pass historical regimes (broader robustness)

| Name | Window | Class | Stress mechanism |
|---|---|---|---|
| 1987 Black Monday | 1987-10-01 to 1987-12-31 | crash + recovery | -22% single-day; tests circuit-breaker era assumptions |
| 1990 Kuwait oil shock | 1990-07-01 to 1990-12-31 | commodity + geopolitical | Oil $20 → $40 in 2 months; sector dislocation |
| 1994 Fed surprise | 1994-02-01 to 1994-12-31 | monetary shock | Greenspan unexpected 25bp hike; bond market crash |
| 1997 Asian crisis | 1997-07-01 to 1998-01-31 | currency contagion | Thai baht devaluation cascades; -10% S&P October |
| 1998 LTCM | 1998-08-01 to 1998-10-31 | sovereign + liquidity | Russian default + LTCM near-collapse; -19% peak-to-trough |
| 2000 dot-com top | 2000-03-01 to 2002-09-30 | sector bust + slow bear | Nasdaq -78%; multi-year drawdown; tests momentum-crash |
| 2001 9/11 | 2001-09-01 to 2001-12-31 | geopolitical + closure | Markets closed 4 days; -14% reopen; tests liquidity assumptions |
| 2007 quant quake | 2007-08-01 to 2007-08-31 | microstructure | Statistical-arb factors decorrelated 72h; tests ML/factor sleeves |
| 2010 Flash Crash | 2010-05-01 to 2010-05-31 | microstructure | -9% intraday May 6; tests open/close fill assumptions |
| 2011 US debt downgrade | 2011-07-01 to 2011-09-30 | sovereign | S&P -19%, VIX 48; first US downgrade |
| 2013 Taper Tantrum | 2013-05-01 to 2013-08-31 | monetary | Bernanke "taper" comment; tests pre-FOMC drift inversion |
| 2014 oil collapse | 2014-07-01 to 2015-02-28 | commodity bust | Oil $107 → $44; energy sector -50% |
| 2015 ETF Flash Crash | 2015-08-20 to 2015-08-31 | microstructure | SPY -7% at Aug 24 open; tests open-fill execution |
| 2016 Brexit | 2016-06-15 to 2016-07-15 | trade-policy | -5% in 2 days; recovery in 4 weeks |
| 2018 trade war | 2018-03-01 to 2018-12-31 | trade-policy | US-China tariff escalation; sector rotation |
| 2019 repo spike | 2019-09-15 to 2019-10-15 | liquidity | Overnight repo to 10%; Fed standing facility response |
| 2020 negative WTI | 2020-04-15 to 2020-04-30 | commodity microstructure | WTI to -$37; energy ETF dislocation |
| 2021 meme stocks | 2021-01-15 to 2021-02-15 | sector bubble | GME, AMC, BBBY squeeze; momentum-crash signal |
| 2021 ARKK top | 2021-11-01 to 2022-06-30 | sector bust | High-growth tech -75%; momentum-crash candidate |
| 2022 GBP gilt | 2022-09-15 to 2022-10-15 | sovereign | UK LDI crisis; cross-asset propagation |
| 2023 SVB crisis | 2023-03-01 to 2023-04-30 | sector + liquidity | Regional bank failures; FRC, SBNY |
| 2023 Hamas-Israel | 2023-10-07 to 2023-11-07 | geopolitical | Oct 7 attack; oil + risk-off |
| 2024 Iran-Israel | 2024-04-01 to 2024-04-30 | geopolitical | First direct missile exchange; brief risk-off |

### Tier 3 — deep historical archetypes (pre-1985)

Direct-replay testing on these is constrained by data quality — yfinance is reliable for individual S&P names only from ~1985–1990 forward, and even index-level data has known revisions and survivorship issues going back further. These regimes are nevertheless critical because they define **archetype magnitudes** larger than anything in the modern record. The 1973–74 oil embargo produced a -48% S&P drawdown over 21 months, deeper than 2008. The 1962 Cuban Missile Crisis produced a -27% peak-to-trough in months, higher VIX-equivalent than any post-1990 single-event window except COVID. Modern strategies that survive only the post-1985 era are potentially under-stressed against these magnitudes.

Use Tier 3 in two ways: (a) replay at the index level (S&P only, no single-name detail) where data exists, to test broad portfolio responsiveness to extreme regime moves; (b) treat each as the **archetype mapping** for one or more forward / scripted scenarios where data does not exist — see Section 4.

| Name | Window | Class | Archetype it teaches |
|---|---|---|---|
| 1962 Cuban Missile Crisis | 1962-10-15 to 1962-11-30 | geopolitical extreme | imminent-nuclear standoff; precursor for Taiwan and Iran-direct-attack scenarios. S&P -7% in 6 days then sharp recovery on resolution |
| 1968 Tet / social unrest | 1968-01-15 to 1968-12-31 | political + monetary | year of compounding risk-off (Tet, MLK assassination, RFK assassination, USD crisis); tests strategies under continuous tape-bombs |
| 1973 OPEC oil embargo | 1973-10-15 to 1974-12-31 | commodity + stagflation | the canonical oil-shock + stagflation regime; archetype for Iran 2026 / Taiwan oil-disruption forward scenarios. S&P -48% peak-to-trough; CPI 11%; core archetype for Stagflation Shock script |
| 1979 Iran Revolution | 1979-01-01 to 1979-12-31 | geopolitical + commodity | second oil shock + revolution-of-major-supplier; analog for Iran 2026 forward scenario |
| 1979–80 Volcker shock | 1979-10-06 to 1982-08-31 | monetary extreme | Fed funds 9% → 20% → 9%; equities flat-to-down for 3 years; archetype for "extreme monetary regime change" — what would happen if 2026 inflation forces Fed back to 8%+ |
| 1980 Iran–Iraq war begins | 1980-09-22 to 1980-12-31 | regional war + commodity | precedent for two-major-supplier regional war disrupting oil; analog input for Iran 2026 |
| 1980 Hunt brothers silver | 1980-03-01 to 1980-06-30 | commodity microstructure | single-commodity squeeze that propagated to broader markets; archetype for any cornered-commodity scenario |
| 1985 Plaza Accord | 1985-09-22 to 1986-12-31 | currency devaluation by agreement | deliberate USD devaluation -50% over 2 years; archetype for orchestrated reserve-currency rebalance |
| 1986–87 Iran-Contra | 1986-11-01 to 1987-09-30 | political / regulatory | constitutional crisis affecting executive credibility; archetype for "presidential authority shock" |
| 1989 Berlin Wall falls | 1989-11-09 to 1990-06-30 | regime change (positive shock) | cold-war-end-of-era; tests strategies' response to positive geopolitical shocks (does momentum capture them, or does the strategy lag?) |
| 1989–91 S&L crisis | 1989-01-01 to 1991-12-31 | sector + sovereign | ~1000 thrift institutions failed; cost ~$160B (3% of GDP); archetype for slow-motion banking-sector unwind |
| 1991 Soviet collapse | 1991-08-19 to 1991-12-31 | regime change (adversary state) | collapse of major adversarial state; archetype for hypothetical regime instability in China / Russia / Iran |
| 1994 Mexican Peso (added Tier 3 detail) | 1994-12-20 to 1995-03-31 | EM currency contagion | Tequila Crisis; archetype for any major EM devaluation cascading into US equities |
| 1995–2000 dot-com boom | 1995-01-01 to 2000-03-10 | trending bull (extreme) | the canonical sustained narrow-leadership trending bull (5+ years); archetype for "is current AI rally just dot-com 2.0"; tests whether momentum strategies size correctly during extended manias |

A note on price data: for index-level replay (most strategies that route through SPY-equivalent), `yfinance` provides ^GSPC back to 1927 with reasonable quality from 1950 forward. For single-name backtests, treat anything before 1985 as illustrative only — survivorship bias becomes severe and corporate action data is unreliable. The dot-com bust window (2000–2002) is in Tier 2; the dot-com **boom** window (1995–2000) is in Tier 3 because it tests whether momentum sleeves size correctly during multi-year manias rather than testing the bust.

### Tier 1 + Tier 2 + Tier 3 totals

24 + 14 = 38 distinct regimes covering ~12 years of cumulative stress windows plus the rolling current regime. Compared to the current 5-regime list, this is ~7.5x broader coverage and adds 7 of the 12 taxonomy classes that are currently absent.

---

## Section 2: Sleeve-specific gating

Not every sleeve needs to pass every regime. Some scenarios are diagnostic for some sleeves and irrelevant to others. The gating matrix:

**Momentum sleeve M (existing top-15) must pass:** all Tier 1, plus Tier 2 momentum-crash regimes (2000 dot-com, 2008 GFC continuation, 2009 momentum crash, 2021 ARKK top, 2022 momentum reversal). Specifically must demonstrate that drawdown in 2009-Q1 / 2022-Q1 (momentum-crash regimes) is bounded — momentum strategies historically lose 30-50% in these windows; the question is whether v5's sleeve sizing keeps portfolio impact below 15%.

**VRP sleeve A (new) must pass:** Feb 2018 Volmageddon, March 2020 COVID, August 5 2024 yen unwind, October 2008 Lehman week. Worst-case 5-day drawdown on the sleeve must be inside the engineered max-loss (defined-risk spread width × position count). If any of these four events produces a sleeve drawdown beyond -25% that is not explained by the spread-width math, the sleeve is broken — kill it.

**Pre-FOMC sleeve B (new) must pass:** 2013 Taper Tantrum (drift inverted), 2018-Q4 hawkish-Powell, 2022 fastest-hiking-cycle period. Must demonstrate that the average pre-FOMC drift signal does not flip negative for more than 2 consecutive meetings in any of these windows. Sample size is small (8 meetings/year × 11 years = 88), so use bootstrap CI on the per-meeting return.

**ML-PEAD sleeve C (new) must pass:** Tier 1 across the board, plus 2023 mega-cap "earnings beats but stocks dropped" episodes (META Feb 2023, GOOG Feb 2024) where naive PEAD failed. Specifically: the ML version must not produce worse-than-naive results in these windows — that is the test of whether the ML feature set is real edge or just sample-fit.

**Total portfolio must pass:** all Tier 1 with portfolio Sharpe > 0.80 in each, no individual regime drawdown deeper than -25%, no consecutive 3-regime drawdown stretch deeper than -35%. This is stricter than v3.x which allowed any single-regime 3/5 wins; v5's tighter portfolio gate compensates for the reduced (50%) momentum allocation.

---

## Section 3: Hypothetical / forward scenarios (Monte Carlo + scripted replay)

Historical replay only tests against shocks that have happened. The user explicitly listed forward scenarios — Iran 2026, drought/famine, hypothetical tariff escalation. For these, no historical data exists, so they need scripted replay: take a known historical analog and inject scenario-specific shocks, then replay. **Each scripted scenario is informed by a Tier 3 deep-history archetype** — that is the value of including 1962–1985 events even when their direct price replay is data-quality-limited.

### Archetype mapping table (deep history → forward scripts)

| Tier 3 archetype | Stress mechanism | Forward scenario it informs | Key magnitude lesson |
|---|---|---|---|
| 1962 Cuban Missile Crisis | imminent-nuclear standoff | Taiwan invasion; Iran-direct | -7% in 6 days then sharp recovery on resolution; defense-sector spike; bond rally |
| 1973 OPEC embargo | oil shock + stagflation | Stagflation scenario; Iran 2026 | -48% S&P over 21 months; CPI 11%; energy +400% |
| 1979 Iran Revolution | major-supplier regime change | Iran 2026 | oil $14 → $39 in 18 months; inflation re-accelerated |
| 1979–82 Volcker shock | monetary extreme | Stagflation 2.0 | Fed funds 9 → 20 → 9; equities flat 3 yrs; momentum strategies useless |
| 1980 Iran-Iraq war | two-supplier regional war | Iran 2026 (escalated form) | oil spike but short-lived; defense names sustained; longer-term sector rotation |
| 1980 Hunt silver squeeze | cornered commodity | any single-commodity squeeze | silver $5 → $50 → $11; spillover to gold, banks |
| 1985 Plaza Accord | orchestrated USD devaluation | hypothetical 2026 G7 dollar accord | USD -50% over 2 years; multinationals dramatically outperform domestics |
| 1989 Berlin Wall | positive geopolitical shock | any positive surprise (peace deal, ceasefire) | momentum strategies LAG; tests whether v5 captures upside-shock alpha |
| 1991 Soviet collapse | adversary-state regime change | China political instability; Iran regime change | EM dislocation; sector winners change overnight; not a uniform risk-off |
| 1994 Mexican Peso | EM currency contagion | hypothetical EM cascade | -10% S&P briefly; tests EM contagion path that's been quiet 2010–2024 |
| 1995–2000 dot-com boom | extreme trending bull (years) | AI bull continuation | tests whether momentum sleeve sizing handles 5+ year sustained narrow-leadership rallies |

The lesson from this mapping: a strategy that survives only the post-2008 era is implicitly betting that future shocks resemble 2008–2024 in **magnitude and duration**. Deep history says that is optimistic. Volcker's 3-year flat market is worse for momentum than 2022's one-year decline. The 1973–74 21-month grind down is worse than 2008's 18-month version because there was no clean recovery — just stagflation. Forward scripts must price these magnitudes in, not just the modern equivalents.

### Implementation pattern for scripted replay

The implementation pattern: the `scripts/regime_stress_test.py` framework supports replaying arbitrary daily-price arrays as input. For scripted scenarios, generate a synthetic price path by taking a base regime and overlaying shock events. Each scenario below specifies its base regime, shock events, and what the strategy is supposed to do. The shock-event magnitudes are calibrated to the deep-history archetype where one exists (e.g., the Iran 2026 oil-shock parameters use the 1973 + 1979 magnitudes scaled to current oil price).

### Scripted scenario 1: 2026 Iran direct attack

Base regime: 2024-Q4 (calm, low VIX, AI bull persisting). Shock event: T+0 Iran direct strikes US Gulf assets, oil +20% in one day, S&P -3.5% intraday gap, VIX 16 → 38. T+5 retaliation cycle, oil +15% additional, defense sector +12%, semiconductors -8% on supply-chain panic. Recovery path: T+30 oil-driven inflation expectations spike, Fed pivots hawkish, S&P -8% additional over 6 weeks. Total scenario length: 90 days. **What the strategy must do:** VRP sleeve survives the gap (defined-risk math), momentum sleeve does not double-down on tech in the rotation, FOMC drift sleeve correctly handles the surprise hawkish pivot. Expected total portfolio drawdown: -12 to -18%.

### Scripted scenario 2: Taiwan invasion

Base regime: 2025 mid (Mag-7 concentrated, post-tariff stabilization). Shock event: T+0 Chinese amphibious operation begins, semiconductors -25% in 3 days (TSMC supply-chain implication), defense +20%, S&P -12% in 1 week, USD/JPY -8%, VIX 18 → 70. Recovery path: T+30 sanction regime imposed, semis bounce -15% net over 60 days, broader market begins recovery on Fed liquidity response. **What the strategy must do:** This is the worst-case for top-N-momentum because the top-N is heavily semi-loaded in 2025–2026. Expected portfolio drawdown -25 to -35%; gating threshold is whether VRP+FOMC sleeves provide enough offset to stay above -25% at the portfolio level. If not, momentum allocation must be cut further.

### Scripted scenario 3: AI capex bubble pop

Base regime: 2026 mid (AI capex still expanding, NVDA / AVGO / Mag-7 concentration extreme). Shock event: T+0 hyperscaler announces AI capex cut from $80B → $30B for following year, citing weakening enterprise adoption. Semis -18% in 1 week, broader Mag-7 -12%, S&P -8%. Recovery path: T+90 broadening rotation into value/cyclicals, S&P recovers but momentum stays underwater. Total scenario: 180 days. **What the strategy must do:** This is a momentum-crash scenario (top-N concentration is the bug, not the feature). Expected sleeve M drawdown -35%, portfolio drawdown -15 to -22%. Gates whether reducing M from 80% to 50% in v5 is sufficient.

### Scripted scenario 4: Major US drought / Panama Canal closure (commodity / supply chain)

Base regime: 2025-Q3 calm. Shock event: T+0 Panama Canal water levels force 80% throughput reduction (this happened partially in 2023–24 — precedent exists), shipping costs +60% over 30 days, retailers -8%, transports +15%, consumer staples -10% on margin compression. Recovery path: T+120 alternate routing established, costs normalize. Total scenario: 180 days. **What the strategy must do:** No specific sleeve handles this directly; the test is whether the cross-sleeve correlation matrix stays under 0.7 during commodity-driven sector rotation. Expected portfolio drawdown -8 to -12%.

### Scripted scenario 5: Cyber attack on major US exchange

Base regime: 2026 calm. Shock event: T+0 NASDAQ trading halted 6 hours due to cyber incident, broad market frozen, when reopens S&P -5% on liquidity gap. Recovery path: T+5 normalcy restored, full recovery T+15. **What the strategy must do:** This is purely a microstructure / execution test. Strategy must not place market-on-open orders during halt-recovery windows. Tests whether `kill_switch.py` correctly suspends rebalancing on exchange-disruption days.

### Scripted scenario 6: 2026 Treasury auction failure

Base regime: 2026 with debt-ceiling brinkmanship. Shock event: T+0 a 30-year Treasury auction tails 8bp (auction failure), 30-year yield +35bp in a day, equities -4%, dollar -2%. Recovery path: T+10 Fed steps in with backstop language; partial recovery. Total scenario: 60 days. **What the strategy must do:** Tests bond-market-origin contagion to equity. Most strategies are agnostic to this, but the FOMC drift sleeve must correctly handle inter-meeting Fed actions.

### Scripted scenario 7: Pandemic 2.0 (slower onset than COVID)

Base regime: 2027 calm. Shock event: T+0 novel pathogen emerges in major Asian metro, T+30 first US cases, T+60 mobility restrictions begin in select states. S&P drift -15% over 90 days then -25% additional in panic week. Different from 2020 in that the onset is slower, so the V-recovery is also slower (4–6 months not 4–6 weeks). **What the strategy must do:** Tests whether v5 has actually fixed the regime-overlay-V-recovery problem from v3.x. If sleeves cut exposure during the slow drift and re-enter only post-recovery, that's the same -34pp failure as 2020. The test passes only if exposure cuts are gradual and re-entry is signal-driven, not calendar-driven.

### Scripted scenario 8: Stagflation shock

Base regime: 2026 high-rate. Shock event: T+0 CPI prints 6.0% (vs 3.0% expected), 10-year yield +50bp, equities -5%, gold +6%. T+90 Fed forced to hike again, S&P -12% from initial level. Recovery path: not within scenario window (180 days). **What the strategy must do:** Tests whether strategies optimized in 2010–2024 disinflationary regime transfer to high-inflation regime. Expected portfolio drawdown -12 to -18%; gating threshold is whether portfolio Sharpe over the full 180 days stays above 0.4.

### Monte Carlo overlay: tail event injection

In addition to the eight scripted scenarios above, run 1,000 Monte Carlo paths through the 2018–2025 base period with random injection of one tail event per path drawn from `{vol shock, geopolitical shock, sector bust, microstructure dislocation}`. Each path generates a portfolio drawdown distribution. Required gate: 95th-percentile drawdown < -30%. This is the catch-all for shock combinations the scripted scenarios don't cover.

---

## Section 4: Implementation — drop-in REGIMES_V2 for `regime_stress_test.py`

Tier 1 only (the must-pass list). Tier 2 should be added after Tier 1 passes cleanly, to avoid one giant test run per iteration.

```python
REGIMES_V2_TIER1 = [
    # name,                       start,                          end,                            class
    ("2008 GFC",                  pd.Timestamp("2008-09-01"),     pd.Timestamp("2009-06-30"),     "slow_bear+liquidity"),
    ("2018 Volmageddon",          pd.Timestamp("2018-02-01"),     pd.Timestamp("2018-02-28"),     "vol_shock"),
    ("2018-Q4 selloff",           pd.Timestamp("2018-09-01"),     pd.Timestamp("2019-03-31"),     "crash+V_recovery"),
    ("2020 COVID",                pd.Timestamp("2020-01-15"),     pd.Timestamp("2020-06-30"),     "crash+V+vol_shock"),
    ("2022 bear",                 pd.Timestamp("2022-01-01"),     pd.Timestamp("2022-12-31"),     "slow_bear+monetary"),
    ("2024 yen unwind",           pd.Timestamp("2024-08-01"),     pd.Timestamp("2024-08-31"),     "vol_shock+cross_asset"),
    ("2025 tariff regime",        pd.Timestamp("2025-01-15"),     pd.Timestamp("2025-06-30"),     "trade_policy"),
    ("2023 AI rally",             pd.Timestamp("2023-04-01"),     pd.Timestamp("2023-10-31"),     "trending_bull+narrow"),
    ("recent 3 months",           pd.Timestamp.today() - pd.Timedelta(days=95),  pd.Timestamp.today(), "current"),
]

REGIMES_V2_TIER3_DEEP_HISTORY = [
    # Deep history — index-level replay only; single-name data unreliable pre-1985.
    ("1962 Cuban Missile Crisis", pd.Timestamp("1962-10-15"),     pd.Timestamp("1962-11-30"),     "geopolitical_extreme"),
    ("1968 Tet / social unrest",  pd.Timestamp("1968-01-15"),     pd.Timestamp("1968-12-31"),     "political+monetary"),
    ("1973 OPEC oil embargo",     pd.Timestamp("1973-10-15"),     pd.Timestamp("1974-12-31"),     "commodity+stagflation"),
    ("1979 Iran Revolution",      pd.Timestamp("1979-01-01"),     pd.Timestamp("1979-12-31"),     "geopolitical+commodity"),
    ("1979-82 Volcker shock",     pd.Timestamp("1979-10-06"),     pd.Timestamp("1982-08-31"),     "monetary_extreme"),
    ("1980 Iran-Iraq war begins", pd.Timestamp("1980-09-22"),     pd.Timestamp("1980-12-31"),     "regional_war+commodity"),
    ("1980 Hunt silver squeeze",  pd.Timestamp("1980-03-01"),     pd.Timestamp("1980-06-30"),     "commodity_microstructure"),
    ("1985 Plaza Accord",         pd.Timestamp("1985-09-22"),     pd.Timestamp("1986-12-31"),     "currency_devaluation"),
    ("1986-87 Iran-Contra",       pd.Timestamp("1986-11-01"),     pd.Timestamp("1987-09-30"),     "political_regulatory"),
    ("1989 Berlin Wall falls",    pd.Timestamp("1989-11-09"),     pd.Timestamp("1990-06-30"),     "regime_change_positive"),
    ("1989-91 S&L crisis",        pd.Timestamp("1989-01-01"),     pd.Timestamp("1991-12-31"),     "sector+sovereign"),
    ("1991 Soviet collapse",      pd.Timestamp("1991-08-19"),     pd.Timestamp("1991-12-31"),     "regime_change_adversary"),
    ("1994 Mexican Peso",         pd.Timestamp("1994-12-20"),     pd.Timestamp("1995-03-31"),     "em_currency_contagion"),
    ("1995-2000 dot-com boom",    pd.Timestamp("1995-01-01"),     pd.Timestamp("2000-03-10"),     "trending_bull_extreme"),
]

REGIMES_V2_TIER2 = [
    # 1980s-1990s
    ("1987 Black Monday",         pd.Timestamp("1987-10-01"),     pd.Timestamp("1987-12-31"),     "crash+recovery"),
    ("1990 Kuwait oil",           pd.Timestamp("1990-07-01"),     pd.Timestamp("1990-12-31"),     "commodity+geopolitical"),
    ("1994 Fed surprise",         pd.Timestamp("1994-02-01"),     pd.Timestamp("1994-12-31"),     "monetary"),
    ("1997 Asian crisis",         pd.Timestamp("1997-07-01"),     pd.Timestamp("1998-01-31"),     "currency_contagion"),
    ("1998 LTCM",                 pd.Timestamp("1998-08-01"),     pd.Timestamp("1998-10-31"),     "sovereign+liquidity"),
    # 2000s
    ("2000-2002 dot-com bust",    pd.Timestamp("2000-03-01"),     pd.Timestamp("2002-09-30"),     "sector_bust+slow_bear"),
    ("2001 9/11",                 pd.Timestamp("2001-09-01"),     pd.Timestamp("2001-12-31"),     "geopolitical+closure"),
    ("2007 quant quake",          pd.Timestamp("2007-08-01"),     pd.Timestamp("2007-08-31"),     "microstructure"),
    # 2010s
    ("2010 Flash Crash",          pd.Timestamp("2010-05-01"),     pd.Timestamp("2010-05-31"),     "microstructure"),
    ("2011 US downgrade",         pd.Timestamp("2011-07-01"),     pd.Timestamp("2011-09-30"),     "sovereign"),
    ("2013 Taper Tantrum",        pd.Timestamp("2013-05-01"),     pd.Timestamp("2013-08-31"),     "monetary"),
    ("2014 oil collapse",         pd.Timestamp("2014-07-01"),     pd.Timestamp("2015-02-28"),     "commodity_bust"),
    ("2015 ETF Flash Crash",      pd.Timestamp("2015-08-20"),     pd.Timestamp("2015-08-31"),     "microstructure"),
    ("2016 Brexit",               pd.Timestamp("2016-06-15"),     pd.Timestamp("2016-07-15"),     "trade_policy"),
    ("2018 trade war",            pd.Timestamp("2018-03-01"),     pd.Timestamp("2018-12-31"),     "trade_policy"),
    ("2019 repo spike",           pd.Timestamp("2019-09-15"),     pd.Timestamp("2019-10-15"),     "liquidity"),
    # 2020s
    ("2020 negative WTI",         pd.Timestamp("2020-04-15"),     pd.Timestamp("2020-04-30"),     "commodity_microstructure"),
    ("2021 meme stocks",          pd.Timestamp("2021-01-15"),     pd.Timestamp("2021-02-15"),     "sector_bubble"),
    ("2021 ARKK top",             pd.Timestamp("2021-11-01"),     pd.Timestamp("2022-06-30"),     "sector_bust"),
    ("2022 GBP gilt",             pd.Timestamp("2022-09-15"),     pd.Timestamp("2022-10-15"),     "sovereign"),
    ("2023 SVB crisis",           pd.Timestamp("2023-03-01"),     pd.Timestamp("2023-04-30"),     "sector+liquidity"),
    ("2023 Hamas-Israel",         pd.Timestamp("2023-10-07"),     pd.Timestamp("2023-11-07"),     "geopolitical"),
    ("2024 Iran-Israel",          pd.Timestamp("2024-04-01"),     pd.Timestamp("2024-04-30"),     "geopolitical"),
]


SCRIPTED_SCENARIOS = [
    # Each scripted scenario is (name, base_regime_start, base_regime_end, shock_specs, expected_portfolio_dd_band)
    ("2026 Iran attack",      pd.Timestamp("2024-10-01"), pd.Timestamp("2024-12-31"),
        [{"day": 0,  "spy_ret": -0.035, "vix_mult": 2.4, "oil_ret": +0.20, "sector_dispersion": "defense+,semis-"},
         {"day": 5,  "spy_ret": -0.025, "vix_mult": 1.6, "oil_ret": +0.15, "sector_dispersion": "defense+,semis-"},
         {"day": 30, "spy_ret_cumulative": -0.08, "rate_path": "+50bp"},  ],
        (-0.18, -0.12)),

    ("Taiwan invasion",       pd.Timestamp("2025-03-01"), pd.Timestamp("2025-06-30"),
        [{"day": 0,  "spy_ret": -0.12, "vix_mult": 3.9, "semis_ret": -0.25, "defense_ret": +0.20, "usdjpy_ret": -0.08},
         {"day": 30, "semis_recovery": -0.15, "fed_response": "liquidity_facility"},  ],
        (-0.35, -0.25)),

    ("AI capex bubble pop",   pd.Timestamp("2026-03-01"), pd.Timestamp("2026-09-30"),
        [{"day": 0,   "hyperscaler_capex_cut": -0.625, "semis_ret": -0.18, "mag7_ret": -0.12, "spy_ret": -0.08},
         {"day": 90,  "rotation_into": ["value", "cyclicals"], "momentum_sleeve_dd": -0.35},  ],
        (-0.22, -0.15)),

    ("Panama Canal closure",  pd.Timestamp("2025-07-01"), pd.Timestamp("2026-01-01"),
        [{"day": 0,   "shipping_costs": +0.60, "retail_ret": -0.08, "transports_ret": +0.15, "staples_ret": -0.10},
         {"day": 120, "rerouting_complete": True},  ],
        (-0.12, -0.08)),

    ("Exchange cyber attack", pd.Timestamp("2026-06-01"), pd.Timestamp("2026-06-30"),
        [{"day": 0, "exchange_halt_hours": 6, "reopen_spy_ret": -0.05},
         {"day": 5, "normalcy_restored": True},  ],
        (-0.06, -0.03)),

    ("Treasury auction fail", pd.Timestamp("2026-08-01"), pd.Timestamp("2026-09-30"),
        [{"day": 0,  "auction_tail_bp": 8, "yield_30y_change_bp": +35, "spy_ret": -0.04, "dxy_ret": -0.02},
         {"day": 10, "fed_backstop_language": True},  ],
        (-0.10, -0.05)),

    ("Pandemic 2.0 slow",     pd.Timestamp("2027-01-01"), pd.Timestamp("2027-06-30"),
        [{"day": 30,  "spy_ret_cumulative": -0.05},
         {"day": 60,  "spy_ret_cumulative": -0.15},
         {"day": 90,  "spy_ret_cumulative": -0.40},  ],
        (-0.30, -0.20)),

    ("Stagflation shock",     pd.Timestamp("2026-01-01"), pd.Timestamp("2026-06-30"),
        [{"day": 0,  "cpi_surprise_bp": +300, "yield_10y_change_bp": +50, "spy_ret": -0.05, "gold_ret": +0.06},
         {"day": 90, "fed_hike_bp": +50, "spy_ret_cumulative": -0.12},  ],
        (-0.18, -0.12)),

    # NEW — calibrated to 1973+1979 oil-shock magnitudes (not just 2022 modern analog)
    ("Iran 2026 deep",        pd.Timestamp("2024-10-01"), pd.Timestamp("2026-03-31"),
        [{"day": 0,    "spy_ret": -0.045, "vix_mult": 2.6, "oil_ret": +0.25, "defense_ret": +0.10},
         {"day": 30,   "oil_ret_cumulative": +0.50, "cpi_surprise_bp": +180},
         {"day": 180,  "oil_ret_cumulative": +0.90, "spy_ret_cumulative": -0.22, "fed_pivot": "hawkish"},
         {"day": 540,  "oil_ret_cumulative": +1.50, "spy_ret_cumulative": -0.35, "regime": "stagflationary"}, ],
        (-0.40, -0.25)),

    # NEW — Volcker-extreme: Fed forced back to 8%+ to break re-acceleration
    ("Volcker 2.0 shock",     pd.Timestamp("2026-01-01"), pd.Timestamp("2028-12-31"),
        [{"day": 0,    "fed_funds_target": 0.08, "yield_10y_change_bp": +200},
         {"day": 90,   "fed_funds_target": 0.12, "spy_ret_cumulative": -0.15},
         {"day": 365,  "fed_funds_target": 0.14, "spy_ret_cumulative": -0.22, "momentum_sleeve_status": "no_signal"},
         {"day": 1095, "fed_funds_target": 0.07, "spy_ret_cumulative": -0.05, "recovery": "slow"}, ],
        (-0.30, -0.20)),

    # NEW — positive geopolitical surprise (1989 Berlin Wall analog)
    ("Peace deal surprise",   pd.Timestamp("2026-06-01"), pd.Timestamp("2026-09-30"),
        [{"day": 0,   "spy_ret": +0.04, "vix_mult": 0.5, "defense_ret": -0.15, "europe_ret": +0.08},
         {"day": 30,  "rotation": "into_cyclicals_em", "momentum_sleeve_lag": True}, ],
        (-0.05, +0.05)),  # negative band means "must not LOSE more than 5% on a positive shock"
]
```

The scripted-scenario replay engine needs to be built. Pseudo-code: for each scenario, take the base-regime window's actual prices, then on each shock-day apply the listed return overrides to the affected tickers/sectors and propagate through to the next day. Run the strategy as if those were the actual prices; report the portfolio path. Implementation effort: ~12 hours. Recommend implementing in a new file `scripts/scripted_scenarios.py` rather than extending `regime_stress_test.py`, to keep historical replay separate from synthetic replay.

---

## Section 5: Promotion gates updated for v5

The 3-gate methodology in `CLAUDE.md` says any variant must pass all three: survivor 5-regime → PIT validation → CPCV. v5 expands gate 1.

**Gate 1A (REPLACES current 5-regime):** must pass Tier 1 (9 regimes) with portfolio Sharpe ≥ 0.80 in each, max-DD per-regime ≤ 25%, no consecutive 3-regime drawdown stretch deeper than -35%.

**Gate 1B (NEW, scripted):** must pass at least 6 of 8 scripted forward scenarios with portfolio drawdown inside the expected band (or better). Scenarios that bust their band must have a written explanation of why (acceptable: "this scenario stresses a sleeve we deliberately accept tail risk on"; not acceptable: "we didn't expect that").

**Gate 1C (Monte Carlo):** 1000-path tail-injection MC over 2018–2025 base regime; 95th-percentile portfolio DD < -30%.

**Gates 2 (PIT) and 3 (CPCV)** unchanged.

The combined Gate 1A+1B+1C is meaningfully harder than today's gate 1. That's the point — v5 sleeves are non-equity-factor and need broader stress coverage to earn LIVE.

---

## Section 6: What this scenario library does NOT test (be honest)

It does not test execution / fill alpha (use `iterate_v12_realistic.py` for that). It does not test capacity at $1M+ AUM (irrelevant at $10k). It does not test broker counterparty failure (Alpaca going down) — that is operational risk, handled in `kill_switch.py` and `BEHAVIORAL_PRECOMMIT.md`. It does not test against truly unprecedented shocks (true black swans, by definition). The Monte Carlo overlay in 1C is the only catchall for novelty; it does not generate genuinely new shock types, only recombines historical ones.

The known unknowns: what happens to vol surfaces under a sustained nuclear-incident regime, what happens to the dollar under a true reserve-currency rotation, what happens to systematic factors under an AGI-driven productivity shock. These are not in the library. They are documented here so future post-mortems do not say "we should have known."

---

*Last updated 2026-05-03. Companion to V5_ALPHA_DISCOVERY_PROPOSAL.md. Implementing session should land Gate 1A first (just the 9 Tier-1 regimes) before Gate 1B + 1C, to avoid combinatorial test-time explosion during early v5 development.*
