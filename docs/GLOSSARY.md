# Glossary — what each thing in the dashboard actually means

*Generated 2026-05-03 (v3.67.1). Last updated when the team called out
that "shadow signals", "shadow variants", "V5 sleeves", "sleeve health",
"validation", and "stress test" all sound like the same thing.*

This doc disambiguates the overloaded vocabulary in the dashboard.
Every Lab / Research / Diagnostics page links here.

---

## Sleeve

A **sleeve** is one self-contained alpha strategy you can run in isolation.

- Owns its own universe (e.g. "S&P 500"), holding period (monthly /
  intraday / event-driven), and rebalance schedule
- Outputs target weights that get aggregated with sibling sleeves at
  the portfolio level
- Examples: `vanilla_momentum_top15`, `low_vol_sleeve`, `pre_fomc_drift`

**Why it matters:** sleeves are the unit-of-decomposition. When you
ask "is this sleeve working?", everything downstream (validation,
stress test, attribution, refutation) operates on one sleeve at a time.

---

## Strategy

Synonym for sleeve in 80% of usage. The dashboard tab is called
**🧪 Strategy Lab** and lists all 31 sleeves we've ever built. Each has
a `status` (LIVE / SHADOW / NOT_WIRED / DEPRECATED / REFUTED) and a
`verification` flag (VERIFIED / REFUTED / UNTESTED / CALMAR_TRADE).

---

## V5 sleeves

The 3 NEWEST alpha-discovery sleeves, currently under research:
**pre-FOMC drift**, **VRP (variance risk premium)**, and **ML-PEAD
(post-earnings drift, ML-routed)**. They live in a separate dashboard
page (**🔬 V5 alpha sleeves**) because they're not yet eligible for the
LIVE rotation — we're still backtesting them. Once they pass validation
+ stress + shadow shadow, they graduate into the main Strategy Lab.

---

## Shadow

Two distinct things are confusingly both called "shadow":

### 1. Shadow signal
**A LIVE module emitting real-time decisions that we DON'T act on.**
Page: **👁️ Shadow signals (live)**.

The classic example: a new sleeve under evaluation runs every day and
publishes "I would BUY 8% AAPL today" decisions to the journal —
side-by-side with what the LIVE sleeves recommend. We DON'T trade on
the shadow signal; we just record what it would have done so we can
score it after-the-fact. After 30+ trading days of agreement /
divergence we promote (LIVE) or reject (REFUTED).

### 2. Shadow variant (A/B sleeve)
**An alternative parameterization of the same sleeve, run in parallel.**
Page: **🧪 A/B sleeve variants**.

Example: `vanilla_momentum_top15` is LIVE. We're considering whether
top-12 or top-20 would be better. We run all three — top-12, top-15,
top-20 — concurrently and the dashboard tracks which had the best
realized risk-adjusted return. After enough data we promote the winner.

**Difference:** shadow signals are NEW sleeves on probation; shadow
variants are PARAMETER tweaks of an already-LIVE sleeve.

---

## Validation

**Methods that test whether a sleeve's edge is real before shipping
it.** Page: **🧪 Validation (walk-forward)**.

Three sub-tools live on this page:
- **Walk-forward** — split the backtest period into sequential train /
  test windows, refit on each train, score on each test. Catches
  curve-fitting where a single backtest looks great but the model
  doesn't generalize through time.
- **Sensitivity grid** — sweep the strategy's parameters (top_n,
  lookback_days, etc.) and chart the Sharpe surface. Catches single
  point fits where one combination works but the neighbors all fail.
- **Chaos check** — perturb the input data slightly (jitter prices,
  drop random observations) and re-score. Catches data-leakage and
  "specific dates required" sleeves.

If a sleeve passes all three, it's a candidate for SHADOW. If it
passes shadow with positive ex-post Sharpe, it's a candidate for LIVE.

---

## Stress test

**A sleeve's behavior across historical crisis regimes.** Page:
**💥 Stress test (crisis regimes)**.

Different from Validation: stress test asks "if 2008 / 2020 / 2022
happened again, what would this sleeve do?" rather than "is the edge
real on average?". A sleeve can have great walk-forward Sharpe but
collapse in a single crisis (LowVol-Selloff is a common example).

The page enumerates 38 historical regimes (1970s stagflation, dot-com,
GFC, COVID, 2022 rate shock, etc.) and runs the sleeve through each.

---

## Sleeve health

**Operational health of LIVE sleeves, NOT a backtest.** Page:
**🩺 Sleeve health (correlation)**.

Three signals:
- **Cross-sleeve correlation** — when momentum sleeve and VRP sleeve
  start moving together (corr > 0.7), we lose diversification. Auto-
  demote rule fires.
- **Per-sleeve rolling Sharpe** — 60-day Sharpe for each LIVE sleeve.
  When it drops below threshold for N consecutive weeks, recommend
  pause.
- **Auto-demote suggestions** — surfaces sleeves whose live performance
  has decayed past tolerance and should be dropped from the LIVE
  rotation.

---

## Refutation

**A previously-claimed alpha that was later shown to NOT work on this
universe / this period / this construction.** Three sub-categories:

| Category | Example | Action |
|---|---|---|
| `IMPLEMENTATION_BUG` | Lookback off-by-one made signal trade on PIT-leaked data | Fix code, re-test |
| `TEST_DESIGN_FLAW` | Crash detector tested on SPY proxy not momentum portfolio | Re-test on right universe |
| `PERIOD_DEPENDENT` | Sector-neutral momentum worked 2010-2018, dead 2019+ | Document; don't ship |
| `GENUINE` | Trailing stop 15% strictly underperforms unstopped on US large caps | Permanently REFUTED |

Strategy Lab marks each REFUTED entry with the category. See
`docs/WHY_REFUTED.md` for the full per-strategy refutation analysis.

---

## CALMAR_TRADE

**A strategy that does not improve Sharpe but DOES improve max-drawdown
at the cost of CAGR.** Useful for risk-constrained capital that cares
more about drawdown control than absolute return.

Example: the **momentum_crash_detector** (Daniel-Moskowitz) — when
applied to our momentum portfolio it cuts max drawdown from -34.8% to
-24.2% in exchange for -1.1pp annualized return. Sharpe-positive
investors should skip; "I can't lose more than 25%" investors should
deploy.

The verification flag is `CALMAR_TRADE` instead of `VERIFIED` so
nobody accidentally treats it as a Sharpe-positive sleeve.

---

## Regime overlay

**A multiplier applied to ALL sleeve weights based on current market
regime.** Page: **🌡️ Regime overlay**.

Three components multiply together:
1. **HMM regime** (BULL=1.0× / TRANSITION=0.7× / BEAR=0.3×)
2. **Macro tilt** (yield curve + credit spreads)
3. **GARCH vol forecast**

Final multiplier is bounded [0.3×, 1.2×]. When markets look bad, the
overlay shrinks position sizing across the entire book.

The overlay can be in DISABLED mode — it computes the multiplier but
doesn't enforce it (used during ramp-up to verify the signal is
sensible before letting it move real capital).

---

## Slippage

**The gap between the price you wanted to fill at and the price you
got.** Page: **⚡ Slippage**.

The journal records every fill with target price, fill price, and the
spread/impact decomposition. The slippage view shows:
- Per-symbol average fill quality
- Worst-fill outliers (LIM orders that crossed the spread)
- Cost basis impact (how much slippage ate into your edge per quarter)

---

## Postmortem

**A self-evaluating Claude-authored review of the previous trading
day's decisions.** Page: **📜 Postmortems**.

Runs nightly via prewarm. Reads the day's journal entries (decisions,
fills, intraday alerts) and writes a markdown summary covering:
- Which sleeve produced which P&L
- Decisions that conflicted (sleeve A wanted BUY, B wanted SELL)
- Risk gates that fired
- Surprises vs the morning briefing's expectation

---

## Sources of truth

| Concept | Lives in |
|---|---|
| Strategy/sleeve registry | `src/trader/strategy_registry.py` |
| Refutation categories | `docs/WHY_REFUTED.md` |
| Validation methods | `src/trader/validation/` |
| Stress test scenarios | `docs/SCENARIO_LIBRARY.md` |
| Regime overlay logic | `src/trader/regime_overlay.py` |
| LLM cost audit | `src/trader/llm_audit.py` |
| Equity state | `src/trader/equity_state.py` (v3.66.0+) |
| Market session | `src/trader/market_session.py` (v3.65.1+) |
| Productization plan | `docs/PRODUCTIZATION_ROADMAP.md` |
| UI design rationale | `docs/UI_BENCHMARK.md` |

---

*Last updated 2026-05-03 (v3.67.1).*
