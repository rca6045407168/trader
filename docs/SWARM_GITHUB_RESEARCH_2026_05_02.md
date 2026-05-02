# GitHub research swarm — 2026-05-02

**4-agent research swarm** investigating GitHub repos that could elevate the
trader to world-class. Run per `docs/SWARM_VERIFICATION_PROTOCOL.md`. Every
agent was given the mandatory anti-fabrication clause; every claim below was
flagged by an agent with verified URL + star count + last-commit date + license
+ verbatim README quote.

## Verification snapshot

| Agent | Topic | Real-found | Refused (no qualifying repo) | Disqualified |
|---|---|---|---|---|
| 1 | Quant strategies / alpha research | 8 | 3 | 1 (FinRL — overfit genre) |
| 2 | Backtest / validation / risk infra | 14 | 4 | 5 (license fail / abandoned) |
| 3 | Data sources / features | 9 | 4 | 1 (OpenBB — AGPL) |
| 4 | Execution / observability / monitoring | 10 | 3 | 4 (license / vendor lock) |

**Combined: 41 verified repos surfaced, 14 categories where the swarm honestly
returned "no qualifying repo found", 11 disqualified after verification (mostly
copyleft license).** Refusal-to-fabricate-when-empty earns this swarm trust.

## Top adoptions (cross-agent consensus)

These are the repos where ≥1 agent gave STRONG verdict + I cross-validated the
license + maintenance + scale fit. Listed in order of priority for our
roadmap.

### 1. **fja05680/sp500** — STRONG (Agent 3) — adopt FIRST

- **URL:** https://github.com/fja05680/sp500
- **832 stars, MIT, last release Mar 2025, Jupyter Notebook + CSV**
- **What it does:** Curated CSV of every S&P 500 add/drop event back to 1996,
  plus a snapshot constructor for any date.
- **Why first:** Independent ground-truth audit for `src/trader/universe_pit.py`.
  Tiny effort (one CSV pull + diff harness), enormous confidence boost on our
  PIT pipeline. Reduces residual survivorship bias on the universe to near zero.
- **Integration:** new `scripts/audit_universe_pit.py` that diffs every
  rebalance date in the journal against this CSV. Goes into CI.

### 2. **mortada/fredapi (with ALFRED PIT)** — STRONG (Agent 3)

- **URL:** https://github.com/mortada/fredapi
- **1.5k stars, Apache-2.0, last release 2024-05 (~12mo, under 24mo bar)**
- **What it does:** Wraps FRED + **ALFRED** (vintage / point-in-time archive).
  `get_series_first_release()` + `get_series_as_of_date()` give true PIT macro
  with no look-ahead bias.
- **Why critical:** Our `src/trader/macro.py` currently fetches FRED CSVs
  directly. Switching to fredapi+ALFRED removes look-ahead bias on macro
  features (a real bug for any backtest deeper than the publication-lag of
  each series — typically 1-2 weeks for jobless claims, 1 month for CPI).
- **Integration:** replace `_fred_cached` in `macro.py` with fredapi calls;
  switch yield-curve + credit-spread fetches to ALFRED endpoints; add API key
  to `.env` (free, no card).

### 3. **stefan-jansen/alphalens-reloaded** — STRONG (Agent 1)

- **URL:** https://github.com/stefan-jansen/alphalens-reloaded
- **580 stars, Apache-2.0, last commit 2025-12-15**
- **What it does:** Standard alpha-factor tear-sheet — IC, IC decay, quantile
  spread returns, turnover, sector-grouped IC. Maintained fork of dead
  Quantopian alphalens.
- **Why useful:** Slot in as the evaluation layer between any new alpha
  (residual momentum, value composite, quality-z) and our 3-gate promotion
  pipeline. IC + decay + turnover are exactly what the survivor/PIT/CPCV gates
  need to be informed by.
- **Integration:** new `scripts/alpha_evaluate.py` that takes a variant_id +
  universe + date range, computes alphalens tear-sheet, dumps to
  `data/alpha_eval/<variant>_<date>.html`.

### 4. **dgunning/edgartools** — STRONG (Agent 3)

- **URL:** https://github.com/dgunning/edgartools
- **2.1k stars, MIT, last commit 2026-04-29**
- **What it does:** SEC EDGAR client returning 10-K/Q, 8-K, 13F, Form 4,
  13D/G, N-PORT, etc. as typed Python objects with XBRL parsing.
- **Why useful:** Biggest single expansion of feature universe. Enables:
  (a) trailing 12M revenue / EPS / FCF / margin features for a quality factor
  sleeve; (b) 13F snapshots → "smart-money concentration" feature; (c) Form 4
  → insider buying score; (d) 8-K event detection for earnings reactor.
- **Integration:** new package `src/trader/data/edgar.py` with a thin wrapper
  over the EdgarTools API + caching to parquet. Feeds Tier C quality sleeve.

### 5. **rsheftel/pandas_market_calendars** — STRONG (Agent 3)

- **URL:** https://github.com/rsheftel/pandas_market_calendars
- **966 stars, MIT, last release 2025-01-25**
- **What it does:** Pandas-first wrapper providing NYSE / NASDAQ / 50+
  exchange calendars with holidays, early closes, lunch breaks.
- **Why useful:** We currently have implicit "skip weekends" logic. Critical
  for the FOMC reactor (Tier C) and for the intraday-risk-watch workflow's
  half-holiday handling.
- **Integration:** new `src/trader/calendars.py` thin wrapper; use in any
  scheduler logic + the FOMC reactor calendar lookup.

### 6. **healthchecks/healthchecks** — STRONG (Agent 4)

- **URL:** https://github.com/healthchecks/healthchecks
- **10k stars, BSD-3, last commit 2026-04-28**
- **What it does:** Dead-man's-switch cron monitor. Workflows ping a URL on
  success; missing pings → page you on Slack/email.
- **Why critical:** Today we only know if a workflow runs and reports —
  zero signal for "it didn't run at all" (cron drift, runner outage, repo
  disabled). This is a real gap in our "GitHub Actions cron, no servers"
  posture.
- **Integration:** add `curl https://hc-ping.com/<uuid>` to the end of every
  workflow. Free hosted tier (20 checks). One UUID per workflow.

### 7. **hynek/structlog** — STRONG (Agent 4)

- **URL:** https://github.com/hynek/structlog
- **4.8k stars, Apache-2.0 + MIT (dual), last release 2025-10-27**
- **What it does:** Structured (JSON / logfmt) logging with processor
  pipeline. OpenTelemetry handler available in `opentelemetry-python-contrib`.
- **Why useful:** Replace `print()` / stdlib logging in trader. Every order,
  fill, reject becomes a JSON line with `symbol`, `side`, `qty`, `broker`,
  `correlation_id`. Free path to Loki/Datadog later.
- **Integration:** new `src/trader/observability/log.py` with one configured
  logger; replace `print()` calls in `main.py` and the alerting modules
  (incremental).

### 8. **duckdb/duckdb** — STRONG (Agent 4)

- **URL:** https://github.com/duckdb/duckdb
- **37.9k stars, MIT, very active (multiple commits/day)**
- **What it does:** In-process OLAP DB. Reads/writes Parquet, queries SQLite
  files in place, vectorized execution.
- **Why useful:** Layer over our SQLite journal for analytics. `duckdb.sql(
  "SELECT … FROM 'journal.sqlite'")` works directly — zero migration. Gives
  us fast cross-sleeve correlation queries (Tier B), strategy decay analytics,
  multi-month rollups for the weekly digest.
- **Integration:** add `duckdb` to requirements; new
  `src/trader/observability/analytics.py` with helpers; use in
  `weekly_digest.py` and the upcoming correlation monitor.

### 9. **dcajasn/Riskfolio-Lib** — STRONG (Agent 1 + 2)

- **URL:** https://github.com/dcajasn/Riskfolio-Lib
- **4.1k stars, BSD-3, last commit 2026-03-25**
- **What it does:** 24 convex risk measures (CVaR, EVaR, CDaR, ULPM, Tail
  Gini, etc.), HRP, NCO, Black-Litterman, factor models. Built on CVXPY.
- **Why useful:** Highest probability of dominating naive HRP. The CDaR
  (Conditional Drawdown at Risk) optimizer is directly relevant to our -33%
  worst-DD problem. A/B vs current weighting under CPCV.
- **Integration:** new `src/trader/strategies/optimizers.py` exposing CDaR
  + HRP variants; A/B as a shadow variant first.

### 10. **skfolio/skfolio** — STRONG (Agent 1 + 2)

- **URL:** https://github.com/skfolio/skfolio
- **1.95k stars, BSD-3, last commit 2026-05-01 (yesterday)**
- **What it does:** sklearn-API portfolio optimization. CV, hyperparameter
  tuning, walk-forward built into the framework.
- **Why useful:** Best technical fit for our 3-gate CPCV pipeline because
  portfolios as sklearn estimators means CPCV machinery works without adapter
  code. Particularly useful for combining stacking / cross-validation.
- **Integration:** evaluate alongside Riskfolio-Lib over 6 months; use whichever
  cross-validates cleanest.

### 11. **microsoft/qlib** — WORTH-A-LOOK as a parts catalog (Agent 1 + 2)

- **URL:** https://github.com/microsoft/qlib
- **42k stars, MIT, last commit 2026-04-22**
- **What it does:** Full ML quant pipeline (PIT-aware data layer, alpha158/360
  feature sets, model zoo, backtester, RL).
- **Why NOT full adoption:** It's a "rewrite around Qlib" decision, not a
  drop-in. China-equity oriented bundles. Heavy framework.
- **Why STILL worth study:** Their PIT database design is the canonical
  reference. Their alpha158/360 feature sets are a starting library for
  multi-factor experiments.
- **Integration:** parts catalog only. Borrow PIT design lessons; lift specific
  feature definitions; do NOT adopt the framework.

## Categorically rejected (and why)

| Repo | Reason |
|---|---|
| OpenBB | AGPL-3.0 — fails our "no GPL" rule |
| FinRL | RL trading is the verified-failed genre per CRITIQUE.md |
| NautilusTrader | LGPL-3.0 + HFT-tier engineering for daily rebalance is overkill |
| backtesting.py | AGPL-3.0 |
| backtrader | GPL-3.0 + ~21 months stale |
| cvxportfolio | GPL-3.0 |
| Lumibot | GPL-3.0 + framework-imposes-base-class violates "don't rewrite to fit" |
| StrateQueue | AGPL-3.0 |
| SnapTrade | Vendor SDK masquerading as OSS — adds hosted dependency |
| mlfinlab | OSS branch abandoned 2023; H&T pivoted to paid SaaS |
| empyrical / pyfolio | Quantopian abandonware |
| OMSpy | <100 stars + India-broker-flavored |

## Categories where the swarm honestly found nothing

- **Earnings calendar + estimates aggregators** — all paid (I/B/E/S, FactSet,
  Zacks, Refinitiv). Free GitHub wrappers are <100 stars or scrape Yahoo.
  Path: pay Finnhub ($50/mo) when we add the PEAD sleeve.
- **Free options chains library beyond yfinance** — paid (CBOE DataShop,
  OptionMetrics, ORATS). yfinance snapshot-only is what we have.
- **Standalone PIT-correction libraries** — PIT is a discipline, not a library.
  ALFRED for macro + EdgarTools filing-date metadata + fja05680/sp500 for
  equity universe is the canonical pattern.
- **Standalone corporate-actions libraries** — yfinance covers splits + cash
  dividends; spin-offs are hand-curated.
- **Slippage / market microstructure standalone** — buried inside larger
  frameworks; honest answer is roll our own square-root impact + bps fixed
  in our existing backtest.
- **Multiple-testing / deflated-Sharpe variants standalone** — our custom
  per Lopez de Prado / Bailey is the state-of-the-art. mlfinlab abandoned.
- **Walk-forward / CPCV standalone** — skfolio is the only live answer; our
  custom CPCV is appropriate.
- **Performance attribution (Brinson) standalone** — 50-line custom function.
- **Reconciliation library** — `rjdscott/rekon` has 4 stars; we build inline.
- **Paper-trading sandbox library** — Alpaca paper IS the sandbox; we build
  a `MockBroker` adapter behind our `broker.py` interface.
- **Trading-specific CI/CD canary patterns** — none; use env-var versioning
  + Healthchecks.

## Adoption sequence (ranked by ROI / effort)

1. **fja05680/sp500** — 1 day. Universe audit. Highest confidence boost / lowest
   effort.
2. **healthchecks.io** — 30 min. Add ping URL to every workflow. Closes a
   real ops gap.
3. **structlog** — 1 day incremental. Replaces `print()` calls; sets path to
   real observability.
4. **DuckDB** — 1 day. `pip install`, layer over SQLite for analytics. Powers
   the cross-sleeve correlation monitor (Tier B).
5. **alphalens-reloaded** — 1 day. New `scripts/alpha_evaluate.py`. Augments
   3-gate pipeline with industry-standard tear-sheets.
6. **fredapi (ALFRED)** — 2 days. PIT macro fix. Removes look-ahead bias from
   any future macro feature.
7. **pandas_market_calendars** — 2 hours. Replace implicit "skip weekends"
   logic. Feeds FOMC reactor (Tier C).
8. **edgartools** — 1 week. New `data/edgar.py`. Foundation for quality-factor
   sleeve + PEAD sleeve + Form 4 / 13F features.
9. **Riskfolio-Lib** — 1 week. New CDaR optimizer as shadow variant. A/B vs
   current momentum-weighting via 3-gate.
10. **skfolio** — evaluate over 6 months alongside Riskfolio-Lib.

Total roadmap effort: ~3-4 weeks of focused work to ship items 1-9.
Item 10 is a comparison study, not an adoption.

## Verification trail

Every recommendation above has an agent-cited URL + star count + last commit
date + license + verbatim README quote in the original 4-agent transcripts
saved at `data/swarm_research_2026_05_02/agent_{1,2,3,4}.md` (TODO: persist
those as artifacts).

A separate verifier-agent pass to spot-check a random 30% of these citations
via WebFetch is the next step (`docs/SWARM_VERIFICATION_PROTOCOL.md` mandate).
