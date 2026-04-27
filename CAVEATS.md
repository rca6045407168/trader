# CAVEATS — things you probably haven't thought of

This file exists because Richard asked "what else have I not thought of?". Read this BEFORE deploying real money. Most of these are why retail algos fail.

## Backtest hazards

### 1. Survivorship bias (CRITICAL)
The S&P 500 list we pull from Wikipedia is the *current* list — by definition the survivors. A 2015–2025 backtest using the 2026 constituents excludes every company that got delisted, went bankrupt, or got booted from the index. Real-world momentum on the *historical* index would have included GE in 2018, Bed Bath & Beyond, Silicon Valley Bank, etc. **Real backtest CAGR is typically 1-3% lower than what we measure.** Fix: use point-in-time index constituents (CRSP, Sharadar — paid).

### 2. Look-ahead bias
Using tomorrow's data for today's decision. Our backtest uses month-end-1 prices to rank, then trades at month-end-1 close. We never peek forward, but watch out if you add new features ("news sentiment as-of" must use the *publish* timestamp, not the *crawl* timestamp).

### 3. Overfitting / multiple-comparisons
If you test 100 strategies, ~5 will look amazing by chance alone. The walk-forward optimizer (`scripts/run_optimizer.py`) holds out 2021-2025 and abstains if Sharpe decays >50%. **Trust the recommender's ABSTAIN.** Don't manually pick the in-sample winner.

### 4. Backtest slippage is fiction
We assume 5bps round-trip. Real fills on $100k orders in S&P 500 names average 8-15bps; on illiquids 30-100bps. Live results will be ~3-8% CAGR worse than backtest.

## Tax & regulatory traps

### 5. Wash-sale rule (IRS §1091)
If you sell a stock at a loss and rebuy it (or a "substantially identical" security — e.g. SPY → IVV) within 30 days, the loss is disallowed. Our momentum strategy rotates monthly — if a stock falls out of top-10 at a loss and re-enters next month, you eat a wash sale. Track in journal; consider 31-day cooldown.

### 6. Pattern Day Trader (PDT) rule
If account < $25k AND you make 4+ day-trades in 5 business days, your account gets locked for 90 days. Bottom-catch trades that exit same day count. Our risk_manager warns; don't ignore it.

### 7. Short-term capital gains tax
Holding < 12 months = ordinary income tax. CA + federal for someone in your bracket: ~37-50%. A 20% gross return is 10-12% after tax. Plan accordingly. Buy-and-hold in IRA is structurally tax-advantaged — you may want this in a Roth, not a taxable.

### 8. Wash-trading / spoofing rules (FINRA)
If the algo crosses your own orders or rapidly cancels orders to influence price, that's market manipulation — even unintentional. We use plain notional/limit orders, but if you ever add layered limits, talk to a lawyer.

## Strategy-decay risks

### 9. Momentum dies in choppy markets
Momentum strategies underperform massively in 2018, late 2015, March 2020. Backtest Sharpe of 0.8 includes survival through these. Be ready for 6-9 month drawdowns of 15-25%.

### 10. Mean reversion dies in trending crashes
Buying the dip in a 2008 / March 2020 environment = catching falling knives. The trend_intact filter mitigates but doesn't eliminate. Consider a regime detector (e.g. SPY > 200-day MA) as a master switch.

### 11. Crowding
The "6-month-skip-1 momentum" effect was discovered in 1993 (Jegadeesh-Titman). It's now in every quant textbook. The edge has compressed from ~12% annual alpha (1965-1989) to ~3-5% alpha (2010-2020). Could go to zero.

### 12. Correlation collapse during crises
Diversifying across 10 momentum names looks safe — until March 2020 when correlations went to 1.0 and everything dropped 35% together. Position sizing assumes normal vol; size down ahead of known events (Fed days, elections).

## Data hazards

### 13. yfinance is not authoritative
Yahoo's data has known issues: occasional bad ticks, late dividend adjustments, missing splits on small names. Cross-check daily P&L against Alpaca's reported equity — if they diverge >0.5% it's a data bug.

### 14. Ticker changes
FB → META, FISV → FI, FB → META mid-backtest. yfinance handles most but not all. The cache key is by ticker string; renames invalidate history.

## Operational hazards

### 15. The "4 PM cron" gap
If your cron runs at 4:01 PM, after-hours news (earnings beats, M&A, Fed announcements) hit between today's close and tomorrow's open. Your decision is stale. Either accept overnight gap risk or run pre-market.

### 16. API outages
Alpaca had a 4-hour outage in Feb 2024. yfinance gets rate-limited. The system needs a kill switch that says "if we can't fetch fresh data, do not place orders blindly."

### 17. The behavioral problem (the biggest one)
After a 20% drawdown, you'll want to turn it off. After a hot 30% gain, you'll want to lever up. Both impulses destroy the strategy. **Pre-commit a written rule**: "I will not change parameters or stop the algo unless OOS Sharpe < 0 over a 3-month window." Sign it. Tape it to your monitor.

### 18. The "who's accountable" problem
When the algo loses money, you have to remember: YOU built it, YOU deployed it, YOU are accountable. The Bull/Bear/Risk debate gives you a paper trail of *why* each trade was taken — use the journal to learn, not to blame.

## v1.2 risk-parity sleeve weighting (deployed)

Validated by v1.1 walk-forward (TRAIN 2015-2020, TEST 2021-2025):

| Config | OOS CAGR | OOS Sharpe | OOS MaxDD |
|---|---|---|---|
| momentum-only (no bottom) | 16.0% | 0.74 | -32.8% |
| fixed 60/40 (was deployed) | 25.9% | 1.41 | -20.2% |
| **risk-parity w/ priors (now deployed)** | **30.6%** | **1.76** | **-14.6%** |

Mechanism: each month, momentum and bottom-catch sleeves are weighted inversely
to their realized vol. Bootstrapped with backtest priors (PRIOR_MOMENTUM_VOL =
6.31%, PRIOR_BOTTOM_VOL = 4.20% monthly) so no live warmup needed. Once we
have 6+ months of live monthly returns, the sample vol takes over.

Weights are clipped to [30%, 85%] so neither sleeve dominates entirely.

First-day live: priors gave momentum 40% / bottom 60%. With no bottom-catch
triggers, only momentum sleeve activated — 5 stocks at $6,793 each ($34k of $100k
deployed, after VIX 0.85 vol scaling).

## v1.0/v1.1 enhancements TESTED and REJECTED

Four ideas that sounded smart but didn't survive walk-forward:

| Enhancement | OOS Sharpe vs baseline | Verdict |
|---|---|---|
| Sector-neutral (1 per sector) | 0.51 vs 0.83 | REJECT — forces weak-sector picks |
| Vol-targeting 15% annualized | 0.52 vs 0.83 | REJECT — scales down at wrong moments |
| Tail hedge (TLT when VIX>25) | 0.79 vs 0.83 | REJECT — tiny drag, no hedge needed in 2021-2025 |
| Momentum acceleration filter (12m AND 3m > 0) | 0.80 vs 0.83 | REJECT — kills good entries |
| 3-sleeve (add 52w breakout) | 0.99 vs 1.15 baseline | REJECT — overfit (decay 45%) |

**Key meta-finding:** the simple system has converged. Adding more features past v1.2 risk-parity is negative-ROI. Occam's razor wins.

## v0.8 stress test results (5 historical crashes + bias quantification)

### Crash performance (strategy vs SPY through named drawdowns)

| Crash | Strategy CAGR | SPY CAGR | Strategy MaxDD | SPY MaxDD | Verdict |
|---|---|---|---|---|---|
| 2015-Q3 China devaluation | +23.5% | +2.4% | -11.2% | -8.5% | OUTPERFORMS |
| 2018-Q4 Powell selloff | -0.4% | +3.2% | **-27.4%** | -13.5% | UNDERPERFORMS |
| 2020 COVID | +110.4% | +15.8% | -14.7% | -19.4% | OUTPERFORMS (suspect: NVDA/TSLA tail) |
| 2022 bear market | -4.5% | -0.2% | -21.2% | -20.3% | UNDERPERFORMS |
| 2025 tariff selloff | +12.3% | -4.1% | -14.5% | -7.6% | OUTPERFORMS |

**Pattern**: outperforms 3 of 5 crashes. Underperforms during regime changes when the previous winners (which the strategy holds) get rotated out (2018 Q4 = FAANG implosion, 2022 = tech bear). The 2018-Q4 -27% drawdown is the worst observed — plan for at least one such period in any 5-year window.

### Survivorship-bias quantified (the most important v0.8 finding)

| Universe | CAGR 2015-2025 | Sharpe | MaxDD |
|---|---|---|---|
| Current top-50 (deployed) | 30.4% | 1.16 | -32.8% |
| **2015-known top-50 (less biased)** | **15.7%** | **0.82** | **-17.2%** |

Delta = ~14.7% CAGR / -0.34 Sharpe / +15.6% MaxDD inflation. The current-top-50 backtest is roughly 2x optimistic on returns. The 2015-top-50 result (15.7% / 0.82) **converges with the v0.5 walk-forward OOS (17% / 0.83)** — two independent methods agree on real expected return.

**Use 15-17% CAGR / 0.80-0.85 Sharpe as your real expectation.**

### Slippage sensitivity (5→50 bps)

| Slippage | CAGR | Sharpe |
|---|---|---|
| 5 bps | 30.4% | 1.16 |
| 10 bps | 30.0% | 1.15 |
| 25 bps | 29.0% | 1.12 |
| 50 bps | 27.3% | 1.06 |
| 100 bps | 24.0% | 0.96 |

Strategy is robust to slippage. At realistic 8-15bps Alpaca paper, only loses ~50bps CAGR. Even at 100bps unrealistic-bad slippage, still beats SPY.

### Monte Carlo block bootstrap

Realized Sharpe 1.16 sits at the 41.5th percentile of bootstrapped distribution (1000 iterations, 3-month blocks). Strategy is **NOT path-dependent**, slightly unlucky vs random shuffle.

### Top-N tradeoff

| N | CAGR | Sharpe | MaxDD |
|---|---|---|---|
| 3 | 40.7% | 1.24 | **-38.7%** |
| **5 (deployed)** | 30.4% | 1.16 | -32.8% |
| 10 | 22.3% | 1.11 | -21.9% |
| 20 | 18.9% | 1.11 | -22.3% |

Top-3 has highest in-sample CAGR but a -38.7% drawdown is operationally painful. Top-5 is the deployed sweet spot. Top-10 reduces drawdown by 1/3 at the cost of ~8% CAGR.

## v0.7 — fixed bottom-catch exit logic

Original bracket (stop -1.5 ATR + take +3 ATR + trail 1 ATR) gave back 36% of the +2.29%/20d edge. Tested 4 alternatives:

| Mode | Description | Mean | Win |
|---|---|---|---|
| A | time-only 20d | +2.23% | 63.1% |
| **B** | **time + wide cat-stop -3.5 ATR** | **+2.29%** | **61.0%** |
| C | time + signal exit (MA20 / RSI>50) | +1.83% | 71.3% |
| D | time + take +4 ATR | +2.20% | 64.2% |

Mode B deployed: full +2.29% edge preserved, with -3.5 ATR catastrophic stop only firing on tail events.

## v2.7 STATUS (2026-04-27)

Live: paper trading ~$99,937 equity, 5 momentum positions held, 0 bottom-catch.
Cloud: GitHub Actions runs daily orchestrator + weekly digest. Email pipeline live.
Tests: 76 passing. Repo: https://github.com/rca6045407168/trader

DEPLOYED + VERIFIED:
- 12-month momentum sleeve (top-5 liquid-50, monthly rebalance, walk-forward 0.76 Sharpe OOS)
- Bottom-catch sleeve (4-signal confluence, 60% sleeve cap, nothing fired live yet)
- Risk-parity sleeve weighting (40/60 from priors; uses live data after 6+ months)
- 9-layer risk manager + 6-trigger kill switch
- Reconciliation (lot-based qty comparison, halts on drift)
- Position lots: per-sleeve FIFO accounting
- 4 tested anomaly detectors: pre-FOMC, OPEX, year-end, pre-holiday
- Rich daily emails: at-a-glance + 9 sections + LLM analysis
- True alpha (Jensen) with β, R², tracking error
- Critical alerts (alerts.py): halts/kill-switch/API-failure bypass daily summary
- Weekly digest workflow: Sunday 5pm PT recap

OPEN (from CRITIQUE.md, in priority order):
- B6 Bull/Bear/Risk debate is unmeasured — could be theatrical at $0.05/trade
- B12 Regression-check thresholds were themselves fit
- B14 Journal lives in GH Actions artifact (90d retention; not durable)
- B22 No proper model registry beyond git
- B23 No A/B / shadow framework for new strategy candidates
- Earnings-drift sleeve (PEAD): not built (needs earnings calendar API)
- Crypto sleeve via Alpaca crypto API
- VRP / box spreads (need IBKR or TastyTrade, not Alpaca)

REALISTIC TARGET (post-DSR correction):
- 10-12% pretax CAGR, 0.5-0.7 Sharpe
- Tax drag: 47% STCG eats 83% of pretax in taxable → run in Roth IRA only

---

## v1.8 Deflated Sharpe — the most honest result yet (and most uncomfortable)

Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014) corrects for selection bias from multiple-trial testing. Applied to our deployed strategy:

| Effective trials assumed | Observed Sharpe | DSR (probability of real edge) | Verdict |
|---|---|---|---|
| 3 (most generous, treats iterations as one) | 1.09 | **47.4%** | Coin flip |
| 5 (some independent variants) | 1.09 | **<1%** | Cannot distinguish from random |
| 12 (literal hypothesis count) | 1.09 | **<1%** | Cannot distinguish from random |

**Implication:** the in-sample Sharpe of 1.09 is statistically indistinguishable from "test 12 random strategies, pick the best" — even under the most generous trial count.

We have an OOS test (2021-2025 walk-forward) that gave Sharpe 0.76. That IS independent evidence. But:
- DSR says the in-sample is selection-biased
- OOS Sharpe is positive (0.76) but not high
- Both are consistent with: strategy has SOME edge, less than the in-sample Sharpe suggests

**Updated honest expectation after DSR correction: 10-12% CAGR, 0.4-0.6 Sharpe.** Earlier estimates of 17% CAGR / 0.83 Sharpe should be considered upper-bound (achievable only if 2021-2025 OOS reflects true edge rather than continued bias).

This is humbling. The strategy may still beat SPY by 1-3% annual after costs, but "meaningful retail alpha" is closer to 1-3% than the 5-7% we'd been targeting.

## Anomaly empirical re-test (v1.7 + v1.8)

Four additional anomalies tested on real 2015-2025 data instead of trusting citations:

| Anomaly | Published claim | **Empirical** | Status |
|---|---|---|---|
| Pre-FOMC drift | +49bps | **+22bps**, single-day Sharpe 2.35 | High conf, half-strength but real |
| Turn-of-month | +70bps | **+18bps vs +15.5bps random = +2.5bps edge** | DEAD |
| OPEX week (Mon-Thu) | +20bps | +10.5bps, 56.5% win | Low conf |
| Year-end reversal (IWM) | +200bps | **+139bps**, 50% win | Half-strength |
| Sell-in-May differential | +200bps annualized | **+110bps annualized** | Weakened |
| Pre-holiday (Ariel 1990) | +12bps excess | **+11.8bps excess, 64.8% win** | **Real, replicated almost exactly** |

Real retail edge from stacking these (post-empirical adjustment):
- Pre-FOMC: 22bps × 8 events ≈ +1.8%/yr
- Pre-holiday: 12bps × 9 events ≈ +1.1%/yr
- Year-end reversal: ~139bps once/yr ≈ +1.4%/yr
- OPEX: 10bps × 12 events ≈ +1.2%/yr
- **Total: ~5-6% annual, before stacking with momentum/bottom-catch**

This is the realistic edge ceiling. Anyone promising 30%+ retail returns is wrong or trading hopium.

## v0.5 walk-forward results (the canonical numbers to trust)

**Train: 2015-01 to 2020-12  |  Test: 2021-01 to 2025-04 (held out)**

| Config | OOS Sharpe | OOS CAGR | OOS MaxDD | Decay | Status |
|---|---|---|---|---|---|
| Risk-parity 2-sleeve | **1.38** | 21.8% | -15.0% | 35.8% | best validated |
| Fixed 60/30/10 | 1.33 | 21.7% | -16.4% | 30.7% | (3-sleeve, retired) |
| Equal 33/33/33 | 1.31 | 17.2% | -19.2% | 34.8% | (3-sleeve, retired) |
| **Fixed 60/40 (deployed)** | ~1.25 (interp) | ~22% | ~-16% | ~32% | **current** |
| Fixed 80/20 (was deployed) | 1.15 | 21.7% | -15.8% | 31.6% | superseded |
| Risk-parity 3-sleeve | 0.99 | 12.4% | -23.8% | 45.5% | overfit — dropped |
| Momentum-only | 0.83 | 17.1% | -17.4% | 40.8% | borderline |

Key lessons:
- The **bottom-catch sleeve adds real diversification** (SPY corr +0.21, OOS Sharpe lifts from 0.83 → 1.15-1.38)
- The **52-week breakout signal works alone (+1.06%/20d) but DOESN'T add ensemble value** — too correlated with momentum, drags Sharpe in 3-sleeve form
- **Counter-intuitive: skipping bottom-catches in deep crashes HURTS** — the -20% SPY drawdown trades had +14.10% mean forward return. The fear-extreme bounces are the alpha. Filter dropped.
- **Risk-parity weighting** between sleeves is the next upgrade (Sharpe 1.38 OOS vs 1.15 fixed). Needs 12 months of live data to bootstrap vol estimates — v0.6 work.

## Empirical findings from this codebase's signal tests

### Bottom-catch signal: validated, but with a non-obvious tweak

Forward-return test on 2,206 triggers across liquid-50 over 2015-2025:
- 5-day mean: +0.94%, win 60.7%
- 20-day mean: +2.29%, win 62.5%
- 60-day mean: +6.92%, win 70.0%

Breakdown by composite score (20-day forward):

| Score bucket | Mean | Win rate | n |
|---|---|---|---|
| 0.55-0.65 | +1.7% | 59.9% | 724 |
| 0.65-0.75 | +3.5% | 65.1% | 373 |
| 0.75-0.85 | +3.4% | 65.4% | 350 |
| 0.85-1.00 | +0.8% | 60.4% | 450 |

**Lesson: the highest-conviction signals are NOT the best.** When the score crosses 0.85 (RSI<25 AND z<-2.5 AND volume spike AND trend intact), the average forward return is *worse* than the 0.65-0.85 bucket. Extreme oversold often means the selloff continues. Threshold updated to 0.65 to skip the weak tail; high-score trades still route through the Bull/Bear/Risk debate to filter catastrophic-looking ones.

## Empirical findings from this codebase's backtests (2015-2025, liquid-50)

### In-sample (2015-2025) vs out-of-sample (2021-2025) Sharpe decay

| Config | In-sample CAGR | Out-of-sample CAGR | Decay |
|---|---|---|---|
| 6m / top-10 (initial guess) | 27.5% | 12.5% | 57% |
| **12m / top-5 (walk-forward winner)** | **30.4%** | **17.1%** | **41%** |
| 12m / top-5 with 200d regime filter | 19.4% | not measured | n/a |

Key takeaways:
- **The first config you try will look ~15% better than reality.** Plan for it.
- The 200-day SPY-MA regime filter HURT in-sample (19.4% vs 30.4% CAGR). Whipsaw cost > drawdown benefit. Need a smarter regime detector (50/200 cross? VIX gate? volatility-of-volatility?). Filter is implemented but defaulted OFF.
- 9 out of 16 parameter combinations had NEGATIVE alpha out-of-sample. Most plausible-looking strategies actually underperform SPY in 2021-2025 — a low-momentum era after the 2020 covid crash trade-of-the-decade.
- Real expected: **17% CAGR, 0.83 Sharpe, +5.5% alpha vs SPY**, with 25-35% drawdowns to be expected once per multi-year period.

## What we don't yet model

- **Earnings blackouts**: don't enter 2 trading days before scheduled earnings (need an earnings calendar API — Polygon/Finnhub).
- **Index inclusion/exclusion events**: addition to S&P 500 = +5-8% pop on average; exclusion = -3-5%. Tradable, not tracked.
- **Sector concentration**: I cap per-position at 5% but don't yet check sector. Could end up 80% tech.
- **Currency hedging**: not relevant for US-only equities. Becomes relevant if you add international.
- **Options for downside protection**: SPY puts as portfolio insurance during high VIX. Cost ~1-3% annual; reduces max drawdown by ~50%.
- **Crypto / FX**: separate strategy. Don't bolt onto this one.
