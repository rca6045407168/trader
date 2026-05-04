# Research reading list — trader system

*Curated 2026-05-03 from a Claude conversation thread (US equities,
intraday/HFT-ish, eventual fund/track record). Mapped to which trader/
modules each entry would inform.*

---

## ⚠️ Read this first — STRATEGY-vs-INFRASTRUCTURE mismatch

You told the other Claude: **"US equities + intraday/HFT-ish + eventual
fund."** This trader/ system as currently built is:

- **Monthly rebalance** (not intraday)
- **Top-15 momentum on liquid_50** (not microstructure / market-making)
- **Equal-weight or score-weighted** (not optimal-execution)
- **Alpaca paper trading** (not co-located, no L1/L2 feeds)

Most of the reading list below — Almgren-Chriss, Cartea-Jaimungal,
Bouchaud market impact, queue position — is **infrastructure for a
horizon we don't trade**. If you actually want intraday, this codebase
is not the path; you'd start a different repo with:

- L1/L2 order-book feeds (Polygon / Databento / direct exchange)
- Co-located or low-latency execution
- Tick-level backtest infrastructure
- Microstructure simulation environment

**The honest answer to your "eventual fund" question** (per the other
Claude's last paragraph, which is correct): solo-to-fund at intraday
is rare and usually requires prior pedigree. The realistic solo-to-fund
path is daily-to-multi-day stat-arb where infrastructure matters less.

This trader/ system as built **fits the realistic path**, not the
ambitious one. The reading list below is split accordingly.

---

## Tier 1 — what to read FOR THIS TRADER SYSTEM (monthly momentum)

These map directly to modules already in `src/trader/`:

| Book / Paper | Maps to | Why now |
|---|---|---|
| **López de Prado, *Advances in Financial Machine Learning*** ch 3, 7, 11–14 | `cpcv_backtest.py`, `deflated_sharpe.py`, `pbo.py`, `bootstrap_ci.py` | Already wired. Re-read ch 7 (CPCV) and 11–14 (backtest overfitting) to validate that what we shipped matches the methodology. |
| **Daniel & Moskowitz (2016) "Momentum crashes"** *JFE* | `momentum_crash.py` | Backtest in v3.60.1 REFUTED our SPY-proxy implementation. Re-read the paper to find what we got wrong (it's specifically about momentum portfolios, not SPY). |
| **Asness-Frazzini-Israel-Moskowitz (2018) "Fact, Fiction, and Momentum Investing"** *JPM* | `strategy.py::rank_momentum`, `residual_momentum.py` | Defends 12-1 momentum's persistence. Read for the "is this still real?" question after our walk-forward Sharpe came back at +0.55. |
| **Blitz-Hanauer "Residual Momentum Revisited"** (2024 Robeco/SSRN) | `residual_momentum.py` (REFUTED on liquid_50) | Re-read to figure out why our test failed. Hypothesis: liquid_50 too narrow + Mag-7 dominance violates FF5 regression assumptions. Re-test on SP500. |
| **Frazzini-Pedersen (2014) "Betting Against Beta"** *JFE* | `v358_world_class.LowVolSleeve` | Theoretical basis for the LowVol sleeve. Backtest showed defensive but no Sharpe lift on blend. |
| **Bailey & López de Prado, *PBO* (2014)** *JCF* + *Deflated Sharpe* (2014) *JPM* | `deflated_sharpe.py`, `pbo.py` | Already wired. Re-read to validate our PBO < 0.5 / DSR > 0 thresholds match the paper. |
| **Robert Carver, *Systematic Trading*** | `risk_manager.py`, position-sizing logic | Best treatment of position sizing + risk targeting. We don't currently risk-target our momentum sleeve; this is an upgrade to consider. |

**Action items per Tier 1:**

1. Re-read AFML ch 7 + 11–14, audit our CPCV / DSR implementations
2. Read Daniel-Moskowitz Table 4 — specifically the "momentum portfolio"
   simulation, not the SPY analog. May redeem our crash detector test.
3. Read Blitz-Hanauer 2024 carefully — understand why residual works in
   their universe (broad EU/Global) and may not in our liquid_50.
4. Read Carver's chapters on risk-targeting; consider implementing
   vol-target sizing for the momentum sleeve.

---

## Tier 2 — read IF you fork to a real intraday system

These don't map to current trader/ modules. They're for the system
you'd build if you actually pursue the intraday/HFT path.

### The non-negotiables (intraday)
- **Almgren & Chriss (2000) "Optimal Execution of Portfolio Transactions"** — execution math foundation
- **Larry Harris, *Trading and Exchanges: Market Microstructure for Practitioners*** — cover-to-cover required reading
- **Maureen O'Hara, *Market Microstructure Theory*** — academic complement (Kyle, Glosten-Milgrom)
- **Cartea, Jaimungal, Penalva, *Algorithmic and High-Frequency Trading*** — the textbook for stochastic-control execution + market making

### Bouchaud / CFM research (microstructure)
- **Bouchaud, Bonart, Donier, Gould, *Trades, Quotes and Prices*** — modern microstructure bible
- CFM website: capitalfundmanagement.com/insights — free papers on:
  - Square-root market impact law
  - Order-book dynamics + queue position
  - Cross-impact across correlated names

### Specific intraday papers
- **Hasbrouck, *Empirical Market Microstructure*** — measuring things from TAQ data
- **Almgren follow-ups on impact** (search SSRN; ~6 you'd want)
- **Lehalle & Laruelle, *Market Microstructure in Practice***
- **Gatheral (2010) "No-dynamic-arbitrage and market impact"**
- **Cont, Stoikov, Talreja (2010) "A stochastic model for order book dynamics"**

### Journals to follow (intraday-relevant ranking)
1. *Journal of Financial Markets* — most directly microstructure
2. *Journal of Financial Econometrics* — high-frequency vol, jumps
3. *Quantitative Finance* (Taylor & Francis) — execution, impact
4. *Journal of Financial Data Science* — newer, ML-tolerant
5. *Review of Financial Studies* — slow but canonical

**Skip** *Journal of Finance* and *JFE* unless a specific paper is cited.
They're cross-sectional and monthly — wrong horizon.

### arXiv / SSRN feeds
- arXiv `q-fin.TR` (Trading and Microstructure) — weekly skim
- arXiv `q-fin.ST` (Statistical Finance)
- SSRN: Microstructure: Trading & Market Structure eJournal
- SSRN: Capital Markets: Market Microstructure eJournal
- Author alerts: Bouchaud, Lehalle, Cartea, Jaimungal, Cont, Kirilenko,
  Easley, O'Hara, Hasbrouck

---

## Tier 3 — practitioner reading (uneven, daily-bar focus)

Useful for color but not where the alpha lives:

- **Ernie Chan blog + 3 books** (Quantitative Trading / Algorithmic
  Trading / Machine Trading) — closest thing to a working retail-quant
  playbook. Daily-bar focus.
- **QuantStart** (quantstart.com) — Mike Halls-Moore. Backtest design,
  ARMA/GARCH, execution. Free.
- **Robot Wealth** (robotwealth.com) — practitioner-grade R/Python.
- **QuantInsti blog** — uneven; walk-forward / cointegration / regime
  posts decent.
- **Quantocracy** (quantocracy.com) — aggregator. Best way to skim 50+
  quant blogs daily.
- **Robert Carver's blog** (qoppac.blogspot.com) + *Systematic Trading*
  + *Leveraged Trading* + `pysystemtrade` repo

## Tier 4 — institutional research (free)

- **AQR Insights / Cliff Asness / Antti Ilmanen** — best institutional
  research that's freely published
- **Ilmanen, *Expected Returns*** — reference book
- **Two Sigma research blog**
- **Man AHL Academic Advisory Board papers**

## Tier 5 — books for taxonomy / overview

- **Narang, *Inside the Black Box*** — what real systematic shops actually do
- **Aldridge, *High-Frequency Trading*** — broader survey, less rigorous than Cartea
- **Grinold & Kahn, *Active Portfolio Management*** — info ratio, breadth, transfer coefficient (later when running multi-PM)

---

## Resources you'll keep coming back to

- **wilsonfreitas/awesome-quant** on GitHub — canonical list of libraries, data, books
- **Hudson & Thames** (hudsonthames.org) — `mlfinlab` package implements López de Prado methods
- **CFM papers** — best public market-impact research

---

## Reading status (update as you go)

- [ ] AFML ch 7 (purged k-fold)
- [ ] AFML ch 11–14 (backtest overfitting)
- [ ] Daniel-Moskowitz 2016 momentum crashes
- [ ] Asness 2018 Fact Fiction Momentum
- [ ] Blitz-Hanauer 2024 residual momentum revisited
- [ ] Carver Systematic Trading (positioning + sizing chapters)
- [ ] *(intraday tier — only if you fork the intraday repo)*

---

## What I'd actually do with this list given our trader system

1. **Don't add intraday infrastructure to this repo.** Different stack.
2. **Re-read the 4 Tier-1 papers** that map to modules we already shipped.
   Specifically, Daniel-Moskowitz to figure out our crash-detector
   refutation, and Blitz-Hanauer to understand why residual momentum
   failed on liquid_50.
3. **Apply Carver's risk-targeting** to the momentum sleeve — replace
   equal-weight with vol-targeted weights. Backtest first.
4. **If pursuing intraday-fund path: start a separate repo.** Not a
   feature inside this one.

---

*Last updated 2026-05-03 (v3.61.0). Sources: synthesized from
academic + practitioner curation in the Claude conversation thread,
plus modules already implemented in `src/trader/`.*
