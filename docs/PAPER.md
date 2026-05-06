# Building an Automated Trading System That Judges and Improves Itself

*A working paper from the trader project.*

---

## Abstract

We describe the architecture, evaluation framework, and improvement loop for a personal automated trading system currently in paper-trading on Alpaca. Two questions drive this paper: **(1) how does an algorithmic trader judge its own performance honestly**, and **(2) how do you run multiple strategies in parallel and let the system learn which to deploy more capital to**? We synthesize current academic work (Lopez de Prado, Bailey on Deflated Sharpe and Combinatorial Purged Cross-Validation), 2025 industry practice (AQR's factor blending, Two Sigma's signal aggregation), and our own walk-forward results across twelve hypotheses. The paper concludes with a concrete v3.0 roadmap: from two strategies under fixed risk-parity weights, to four uncorrelated strategies under a meta-allocator that adapts to regime.

---

## 1. The judgment problem

> "In trading, the easiest thing in the world is to fool yourself with a good-looking backtest." — paraphrasing Marcos Lopez de Prado

### 1.1 Why naive metrics deceive

A single Sharpe ratio number is meaningless without context about how many other Sharpe ratios were considered before settling on it. If we test 100 strategy variants and pick the best, the highest Sharpe will look extraordinary by pure chance. This is the **selection bias** problem.

Our own iteration history demonstrates this empirically:

| Iteration | What we tested | In-sample Sharpe | Out-of-sample Sharpe | Decay |
|---|---|---|---|---|
| v0.1 (initial guess) | 6m / top-10 momentum | 1.32 | 0.69 | -48% |
| v0.2 (walk-forward winner) | 12m / top-5 momentum | 1.16 | 0.83 | -28% |
| v0.5 (best in-sample 3-sleeve) | momentum + bottom + breakout | 2.01 | 1.31 | -35% |
| v0.5 (3-sleeve risk-parity) | inverse-vol over 3 sleeves | 1.81 | 0.99 | -45% |
| **v1.2 (deployed)** | momentum + bottom-catch, risk-parity | **2.15** | **1.38** | **-36%** |

Note that several configurations had higher *in-sample* Sharpe than what we deployed. We chose the deployed version based on out-of-sample performance, not in-sample, because the in-sample winner with the highest decay (-45%) was likely curve-fit.

### 1.2 The metrics we actually track

We judge the system on four orthogonal axes:

**1. Risk-adjusted return (Sharpe / Sortino / Calmar)**

| Metric | Formula | What it captures |
|---|---|---|
| Sharpe | (Rₐ − rₒ) / σ | Symmetric vol-adjusted return |
| Sortino | (Rₐ − rₒ) / σₙ | Downside-vol-adjusted return |
| Calmar | CAGR / |MaxDD| | Return per unit of worst-case loss |
| MAR | CAGR / |MaxDD| (rolling) | Calmar with rolling 3-year window |

Why three: Sharpe is the standard but penalizes upside volatility (which is good!). Sortino fixes that. Calmar is the metric you actually feel when a strategy goes through a -30% drawdown.

**2. Selection-bias-corrected metrics (the rigorous ones)**

- **Deflated Sharpe Ratio (DSR)** (Bailey & Lopez de Prado 2014). Adjusts the Sharpe for non-normality (skew, kurtosis) and the number of independent trials. *If you tested 100 strategies and the best has Sharpe 2.0, the DSR might be 0.3, indicating 30% probability the result wasn't luck.* For our system, we tested ~12 hypotheses; the DSR adjustment shrinks our 1.38 OOS Sharpe to roughly 1.15-1.25.
- **Probability of Backtest Overfitting (PBO)** (Bailey, Borwein, Lopez de Prado, Zhu 2014). Quantifies the chance that the selected strategy will underperform the median of the trial set out-of-sample. We aim for PBO < 0.20.

**3. Live attribution (where did the return come from?)**

- Sleeve P&L decomposition: how much came from momentum vs bottom-catch?
- Per-trade attribution: which entries are positive expectancy, which are negative?
- Factor exposures: how much of our return is just SPY beta? (β = 1.0 means we're a SPY proxy with extra cost.)
- Alpha = our return − (β × SPY return)

**4. Degradation signals (is the strategy still working?)**

- Live Sharpe (rolling 60-day) vs backtested Sharpe — if live > backtest by 50%, suspicious; if live < backtest by 50%, the edge has decayed.
- Slippage drift: are real fills converging to our 5bps assumption, or worse?
- Win rate drift: bottom-catch should win 60-65% of trades. If it drops below 55% for 30 trades, the signal has decayed.
- Correlation drift: bottom-catch should have +0.21 correlation with SPY. If it climbs above +0.5, it's collapsing into a beta clone.

### 1.3 Walk-forward vs CPCV: why we use both

**Walk-forward** (what our v0.5 optimizer does):
- Train on years 1–6, test on years 7–10.
- Pro: chronologically clean, no information leakage from future to past.
- Con: only ONE test period. If 2021–2025 was an anomaly, we'd never know.

**Combinatorial Purged Cross-Validation (CPCV)** (Lopez de Prado 2018):
- Generate many train/test partitions that respect chronology AND purge any overlapping information.
- Compute Sharpe on each test fold; the distribution gives a confidence interval.
- Pro: lower probability of overfitting, more robust estimates.
- Con: computationally expensive, harder to implement.

**Our practice:** walk-forward is in production (`scripts/run_optimizer.py`); CPCV is on the v3.0 roadmap.

### 1.4 The behavioral metric we don't measure but should

The single biggest risk in any retail algo system is the *operator giving up during a drawdown*. When live results dip 15-20% below the backtest expectation, the operator either turns off the system (locking in the loss) or starts tinkering with parameters (curve-fitting to recent failures). Both destroy returns.

We haven't found a quantitative metric for this, but the operational practice that helps: **pre-commit a written rule for what level of drawdown will pause trading, and what evidence will resume it.** Tape it to the monitor.

---

## 2. Multi-strategy deployment

### 2.1 Why one strategy is fragile

A single strategy has one persistence assumption. Momentum needs leadership to persist; mean reversion needs ranges to mean-revert; breakout needs trends to follow-through. When the regime breaks the assumption, returns evaporate.

Our own data shows this:
- 12-month momentum lost 4.5% during the 2022 bear market while SPY lost only 0.2% (regime change — leaders rotated).
- Bottom-catch lost 1.78% in 2020 (every other year was profitable) because COVID was a falling-knife environment, not a mean-reverting one.

The two strategies failed in DIFFERENT environments. A portfolio holding both had max drawdown -14.6% (risk-parity) vs -32.8% (momentum-only) over the same 2021-2025 OOS window.

### 2.2 What the big quant firms actually do

Three models worth studying:

**AQR (multi-factor blending).** Combines value + momentum + carry + quality + low-vol across equities, bonds, currencies, commodities. Each factor is a strategy; capital is allocated based on long-run risk-adjusted contribution. The whole portfolio has Sharpe 1.0-1.5 because the factors are uncorrelated even when individual factors fail. ([AQR Multi-Factor approach](https://funds.aqr.com/Insights/Strategies/Multi-Factor))

**Two Sigma (signal aggregation).** Hundreds of weak signals aggregated via machine-learning meta-models. No single signal is meaningful; the ensemble is. They crowdsource signal discovery via competitions. ([Two Sigma overview](https://medium.com/@navnoorbawa/how-renaissance-technologies-aqr-and-pdt-built-100-billion-factor-models-statistical-arbitrage-ac0c9cd8a518))

**Renaissance Medallion (massive ensemble).** Thousands of signals across global equities, commodities, currencies, futures — including unconventional ones (weather patterns). Holdings are short-term, leverage is significant, turnover is high. The fund's ~40% net annual returns come from edge that's invisible in any single signal. ([Renaissance overview](https://medium.com/@navnoorbawa/how-renaissance-technologies-aqr-and-pdt-built-100-billion-factor-models-statistical-arbitrage-ac0c9cd8a518))

Common thread: **diversification across uncorrelated edges is the only known way to push Sharpe above 1.5 sustainably.** Single-strategy Sharpes above 1.5 are usually overfit.

### 2.3 Our v1.2 system (deployed today)

Two strategies with risk-parity weighting:

```
                 Momentum (12m, top-5)
                 │ — monthly rebalance
                 │ — sleeve P&L tracked separately
                 │
  Risk-parity ◄─┤ weights = inverse-vol normalized, clipped [30%, 85%]
  Allocator      │   bootstrap with 2015-2020 priors
                 │
                 Bottom-catch (RSI<30 + Bollinger + volume + trend)
                 — bracket order: cat-stop only, time exit 20d
                 — sleeve P&L tracked separately
```

This works, but it's the simplest possible multi-strategy system. The next step is meaningful: more sleeves and a smarter allocator.

### 2.4 The v3.0 multi-strategy architecture

Our target architecture, validated by walk-forward where possible, would have:

**Sleeves (4-6 uncorrelated strategies):**

| Sleeve | Type | Horizon | OOS Sharpe (est) | Status |
|---|---|---|---|---|
| Cross-sectional momentum | Trend | 1-12 months | 0.83 | DEPLOYED |
| Bottom-catch (oversold bounce) | Mean-reversion | 5-20 days | 0.74 | DEPLOYED |
| Sector momentum (XLK, XLF, ...) | Trend (sector ETFs) | 3-6 months | 0.6-0.9 | v2.0 |
| Low-vol anomaly | Risk premium | 6-12 months | 0.4-0.7 | v2.5 |
| Post-earnings drift | Event-driven | 2-30 days post-earnings | 0.6-0.8 | v3.0 (needs earnings calendar API) |
| Bond/equity rotation | Macro | 1-3 months | 0.4-0.6 | v3.0 (TLT/IEF) |

**Meta-allocator (the brain that picks weights):**

Three options, increasing in sophistication:

1. **Fixed weights** (what AQR mostly does for retail products). Simplest. Set monthly. No learning.
2. **Risk-parity** (what we deploy now). Inverse-vol weights between sleeves. Adapts to vol regime.
3. **Bandit-based dynamic allocator** (what advanced quants do). Each sleeve is an "arm" of a multi-armed bandit. We track each sleeve's recent P&L and gradually shift capital toward winners using an Upper Confidence Bound (UCB) algorithm. ([TradeBot, Sun et al 2021](https://www.sciencedirect.com/science/article/abs/pii/S003132032100666X) deployed this in production with sub-200ms execution.)

The trade-off: **bandit allocators learn from live data, which means they can chase regime changes — sometimes correctly, sometimes too late.** A pure risk-parity allocator is more stable but slower to adapt. Our recommendation: deploy bandit but with strong priors (the UCB exploration parameter set conservatively) so it takes 6+ months of contrary evidence to abandon a sleeve.

**Regime overlay (the master switch):**

Different strategies work in different regimes. Suggested regime detector:
- Bull-trending (SPY > 200d MA, VIX < 20): full momentum allocation
- Bull-volatile (SPY > 200d MA, VIX 20-30): reduce momentum, increase bottom-catch
- Bear-trending (SPY < 200d MA, VIX > 20): reduce equity exposure 50%, add bond sleeve
- Crisis (VIX > 35): kill switch — cash + tail hedge

The naive regime overlay (200d MA on SPY) hurt performance in our v0.8 test. A smarter regime detector probably needs:
- Hidden Markov Model (HMM) fitting to a few macro variables
- OR a Random Forest classifier trained on labeled regime data ([QuantInsti regime detection](https://blog.quantinsti.com/epat-project-machine-learning-market-regime-detection-random-forest-python/))

Both are v3.0+ work.

---

## 3. Online learning: how the system improves itself

### 3.1 Three things to learn, three different speeds

**Slow loop: monthly walk-forward (what we have).**
- Re-run the parameter sweep on the latest 5-year window
- If the recommended params change AND OOS Sharpe is materially better, alert the operator (don't auto-deploy)
- Cadence: monthly

**Medium loop: bandit-based sleeve weighting (v3.0 plan).**
- Each sleeve gets a UCB confidence interval based on its recent P&L
- Reallocate capital toward higher-mean sleeves while preserving exploration
- Cadence: weekly or bi-weekly

**Fast loop: signal-level adjustments (we don't do this).**
- Adjust individual signal thresholds (e.g. RSI<30 → RSI<28) based on rolling 30-day signal hit-rate
- Risk: noise will dominate; the system will chase whatever just worked
- Recommendation: avoid. Let signals run for 2+ years before considering a parameter tweak.

### 3.2 The post-mortem agent (already deployed)

We have a Claude-API agent that runs nightly and reviews yesterday's decisions vs the resulting P&L. It outputs ONE specific tweak per day, logged but NOT auto-applied. This is the human-in-the-loop layer over the autonomous system. It catches things a numeric metric wouldn't (e.g. "the bottom-catch on XYZ was actually a fraud disclosure, the strategy can't see that").

### 3.3 What CAN'T be learned by the system itself

- **Regime breaks** (e.g., 2020 COVID): no statistical method detects this until weeks after the fact. Only human judgment + macro reading can suspect it early.
- **Crowding decay**: when too many traders deploy the same strategy, the edge disappears. No internal metric detects this; you need external data (filings, fund flows).
- **Operational disasters**: API outages, exchange halts, wrong-account trading. These need external monitoring.

We build the system to learn what it can, and we build alerts so a human catches what it can't.

---

## 4. Architecture for a multi-strategy learning system

### 4.1 Component diagram (v3.0 target)

```
   [data feeds]              [signal generators]             [strategy adapters]
   yfinance / Polygon  -->   Momentum / RSI / ATR     -->    sleeve_momentum.py
   FRED macro          -->   Earnings / Bollinger     -->    sleeve_bottom_catch.py
   VIX / TLT           -->   Sector relative-strength -->    sleeve_sector.py
                                                              sleeve_postearn.py
                                                              sleeve_lowvol.py
                                                                       │
                                                                       ▼
                                                              [meta-allocator]
                                                              UCB bandit + risk-parity prior
                                                              + regime overlay
                                                                       │
                                                                       ▼
                                                              [risk manager]
                                                              9 layers (cap, drawdown, vol scale, …)
                                                                       │
                                                                       ▼
                                                              [order planner]
                                                              limit / bracket / OTO
                                                                       │
                                                                       ▼
                                                              [executor]
                                                              Alpaca (paper / live)
                                                                       │
                                                                       ▼
                                                              [journal + reconciler]
                                                              SQLite, daily snapshot
                                                                       │
                          ┌──────────────────────────────────────────┘
                          ▼                       ▼                  ▼
                  [post-mortem agent]      [walk-forward]      [degradation alerts]
                  daily Claude review       monthly             rolling Sharpe vs backtest,
                  proposes 1 tweak          reruns optimizer    slippage drift, win-rate drift
```

What's deployed today: everything except the bandit allocator, additional sleeves, regime overlay, and degradation alerts. Those are the v3.0 roadmap.

### 4.2 Software discipline

Seven principles from the production hedge-fund world ([Datos Insights 2025 hedge fund survey](https://www.thetradenews.com/wp-content/uploads/2025/06/Algo-Survey-HF-2025.pdf)):

1. **Reconciliation is non-negotiable.** Every position in the trading system must match every position at the broker daily. Drift = bug. Halt and investigate.
2. **Idempotency over retries.** A daily run that fires twice should produce the same orders, not double them. Our system enforces this via journal-based date checks.
3. **Kill switch with multiple triggers.** Manual flag, daily loss, weekly loss, drawdown from peak, missing API key. We have 6.
4. **Dry-run mode for everything.** Every code path that places orders should have a `dry_run=True` test path. We do.
5. **Log structurally.** Decisions, orders, P&L — all in SQLite, all queryable. Plain text logs are unsearchable when you need them.
6. **Test the safety layers.** We have 44 unit tests covering risk_manager, kill_switch, validation, journal, order_planner.
7. **Deploy and verify, not deploy and hope.** Reconcile after every order placement. If actual fills don't match planned orders within 5%, halt.

### 4.3 What NOT to over-engineer

Three things we built or considered, and concluded were overkill for personal-account scale:

- **Real-time WebSocket order management.** Useful for HFT, useless for monthly-rebalance momentum. We poll daily.
- **GPU-accelerated backtesting.** A Python backtest of 10 years on 50 stocks runs in 30 seconds. We don't need to optimize this.
- **Microservice architecture.** A single Python process is fine. Our entire system is 4,086 lines.

Keeping it simple is itself a form of robustness.

---

## 5. What we learned from 12 hypotheses

A condensed table of every hypothesis we tested, the result, and the rule that emerged:

| H | Hypothesis | Result | Rule emergent |
|---|---|---|---|
| H1 | Bottom-catch signal decays over time | Holds (+2.57% recent vs +2.10% early) | Don't assume signal decay; measure it |
| H2 | 52-week breakout has independent edge | Yes, +1.06%/20d, 58% win | Real but smaller than bottom-catch |
| H3 | Bottom-catch is uncorrelated with SPY | +0.21 correlation | Real diversification benefit |
| H4 | Combined ensemble beats single strategy | Sharpe 0.83 → 1.15-1.38 | Ensembles work; deploy multi-strategy |
| H5 | Skip bottom-catch in deep crashes | Wrong — deep-crash trades had +14.10% mean | Counter-intuition: extreme oversold IS the alpha |
| H6 | Adding 3rd sleeve (breakout) improves Sharpe | In-sample yes, OOS overfit | Test marginal additions in walk-forward |
| H7 | Risk-parity beats fixed weights | Yes — Sharpe 1.41 → 1.76 OOS | Inverse-vol allocation is the right default |
| H8 | Brackets preserve mean-reversion edge | NO — lost 36% of edge | Brackets are anti-pattern for mean-reversion |
| H9 | Signal-strength weighted momentum | Higher CAGR, worse Sharpe + DD, OOS hurt | Equal-weight beats clever weighting |
| H10 | Bottom-catch returns are front-loaded | No — only 45% in first 5d | Time-based exit (20d) preserves edge |
| F1 | Momentum acceleration filter (3m AND 12m >0) | Marginally worse | Don't add filters that kill good entries |
| F2 | Risk-parity with backtest priors | Best OOS Sharpe ever (1.76) | DEPLOYED |

Three meta-rules emerged:

1. **Walk-forward decay is REAL.** Every config we tested lost 30-50% of in-sample Sharpe out-of-sample. Plan accordingly.
2. **Diversification adds Sharpe more than any clever filter.** Adding a 2nd uncorrelated sleeve added 0.5+ Sharpe; adding filters / regimes / overlays mostly hurt.
3. **The simple system has converged.** 8 of our 12 hypotheses were rejected. We're at the part of the curve where more features = lower OOS performance.

---

## 6. The roadmap from here

### v2.0 (next 1-3 months of paper trading)

- Add **sector momentum sleeve** using sector ETFs (XLK, XLY, XLF, XLE, XLV, XLI, XLB, XLU, XLP, XLC, XLRE). Target: 3 ETFs with strongest 6-month momentum, monthly rebalance. Should be uncorrelated with single-name momentum.
- Add **cash-yield sleeve**: idle cash in Alpaca pays ~3-4% APY. Track it as a separate sleeve so the meta-allocator considers staying in cash when other sleeves underperform.
- **CPCV evaluation harness**: replace walk-forward with combinatorial purged cross-validation for parameter sweeps.
- **Deflated Sharpe** in the optimizer output — tell the operator how much of the apparent Sharpe is selection bias.

### v2.5 (months 4-6, after first live results)

- **Bandit allocator** (UCB). Replace fixed risk-parity weights with adaptive sleeve allocation that learns from live P&L.
- **Low-vol anomaly sleeve**: long the lowest-vol decile of S&P 500. Slow, defensive, uncorrelated with momentum.
- **Degradation alerts**: rolling-30-day Sharpe vs backtest Sharpe; if delta exceeds 1σ, page the operator.
- **Quality momentum filter**: drop momentum picks with declining EPS. Needs fundamentals data API (e.g. SimplyWall, Polygon).

### v3.0 (months 6-12)

- **Regime detector**: HMM or RF classifier over (SPY 50/200 cross, VIX, term structure, breadth). Master switch over sleeve weights.
- **Post-earnings-drift sleeve**: long stocks with positive earnings surprises for 30 days post-announcement. Well-documented anomaly. Needs earnings calendar API.
- **Bond/equity rotation sleeve**: long TLT when SPY breaks 200d MA + VIX > 25. Tail hedge that's only on when needed.
- **Crypto sleeve via Alpaca crypto API**: 5-10% allocation to a momentum strategy on BTC/ETH. Uncorrelated.

### v4.0 (year 2+)

- Multi-account: separate Roth IRA for tax efficiency. Aggressive long-only there; defensive in taxable.
- Options overlay: covered calls on momentum positions for yield enhancement; protective puts during high VIX.
- ML-driven signal discovery (this is where Two Sigma's edge comes from). Caveat: extremely high overfitting risk for retail-scale data.

---

## 7. The honest limits

We are explicit about what this system can and cannot do, because most algo-trading content overstates both:

**Can do:**
- Reliably extract well-documented anomalies (momentum, mean-reversion) at low cost.
- Beat SPY by 3-7% annual alpha after costs and tax (realistic, not the headline 15%).
- Operate autonomously with low human-time cost.
- Improve continuously through walk-forward and bandit learning.

**Cannot do:**
- Beat HFT firms in microsecond execution. We're long-horizon, low-turnover.
- Predict regime breaks. We can react, not predict.
- Avoid -25-30% drawdowns. They will happen ~once per 5-year window.
- Substitute for the operator's judgment about whether to trust the system during a drawdown.
- Eliminate survivorship bias. Our backtest CAGR is 30%; reality is ~17%.

**Will likely happen:**
- A 20-30% drawdown within the first 18 months. The walk-forward says it.
- One bug in production that costs 1-3% of equity. Plan for it; the kill switch contains the damage.
- A six-month period of underperformance vs SPY. Momentum has these. Don't react.

---

## 8. Conclusion

We deploy a 2-strategy ensemble (momentum + bottom-catch) with risk-parity weighting on Alpaca paper, judged by walk-forward Sharpe (1.38 OOS), Deflated Sharpe (~1.20 after correcting for our 12-hypothesis selection bias), and live degradation detection. Multi-strategy deployment is not just possible but necessary for risk-adjusted performance: a single-strategy Sharpe above 1.5 is almost always overfit, while uncorrelated ensembles routinely deliver 1.5+ at the sleeve count of 4-6 used by AQR and Two Sigma.

The path from here is more sleeves (sector, low-vol, post-earnings, bond rotation), a smarter allocator (UCB bandit over risk-parity), and a regime detector (HMM or RF). Each addition must justify itself by walk-forward AND survive the Deflated Sharpe correction. Most won't.

The operator's job is not to add more features; it is to **resist adding features**, monitor degradation, and intervene only when objective alerts fire.

Written 2026-04-26, before any live data exists. To be revised after 90 days of paper-trading results.

---

## References

Core academic:
- Bailey, D.H. & Lopez de Prado, M. (2014). [The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551). SSRN.
- Bailey, D.H., Borwein, J., Lopez de Prado, M., Zhu, Q.J. (2014). [The Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253). SSRN.
- Lopez de Prado, M. (2018). [Advances in Financial Machine Learning](https://en.wikipedia.org/wiki/Purged_cross-validation). Wiley. (CPCV chapter.)
- Jegadeesh, N. & Titman, S. (1993). "Returns to Buying Winners and Selling Losers." *Journal of Finance.* (Cross-sectional momentum.)
- George, T.J. & Hwang, C-Y. (2004). "The 52-Week High and Momentum Investing." *Journal of Finance.*

Industry / 2025:
- AQR Capital Management. [Multi-Factor Approaches](https://funds.aqr.com/Insights/Strategies/Multi-Factor).
- The TRADE News (2025). [Algorithmic Trading Survey: Hedge Funds 2025](https://www.thetradenews.com/wp-content/uploads/2025/06/Algo-Survey-HF-2025.pdf).
- Sun et al. (2021). [TradeBot: Bandit learning for hyper-parameters optimization of high-frequency trading strategy](https://www.sciencedirect.com/science/article/abs/pii/S003132032100666X).
- Bawa, N. (2025). [How Renaissance, AQR, and PDT Built $100 Billion: Factor Models, Statistical Arbitrage, and Quantitative Trading Explained](https://medium.com/@navnoorbawa/how-renaissance-technologies-aqr-and-pdt-built-100-billion-factor-models-statistical-arbitrage-ac0c9cd8a518).
- arXiv preprint 2509.16707 (2025). [Increase Alpha: Performance and Risk of an AI-Driven Trading Framework](https://arxiv.org/html/2509.16707v1). (Read with skepticism — see CAVEATS.md.)
- arXiv preprint 2511.12120 (2025). [Deep Reinforcement Learning for Automated Stock Trading: An Ensemble Strategy](https://arxiv.org/abs/2511.12120).
- QuantInsti. [Machine Learning for Market Regime Detection Using Random Forest](https://blog.quantinsti.com/epat-project-machine-learning-market-regime-detection-random-forest-python/).

Open-source reference implementations:
- [Stefan Jansen — Machine Learning for Algorithmic Trading (book + code)](https://github.com/stefan-jansen/machine-learning-for-trading)
- [QuantConnect](https://www.quantconnect.com/) — platform with built-in walk-forward, CPCV, ensemble strategies.
- [Nautilus Trader](https://github.com/nautechsystems/nautilus_trader) — production-grade event-driven engine in Rust.

This project's empirical results live in [CAVEATS.md](../CAVEATS.md). All hypotheses tested are reproducible from `scripts/iterate_v*.py`.
