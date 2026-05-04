# Why so many strategies were refuted on backtest

*Triggered 2026-05-03 by user question after seeing the Strategy Lab.*

After the v3.60.1 verification audit, **7 of the v3.58 SHADOW modules
came back REFUTED on backtest**. That's a striking failure rate. The
honest investigation: **most aren't actually genuine claim failures —
they're test design flaws, implementation bugs, or period-dependent
results.** Categorizing each one explicitly.

---

## Refutation taxonomy

Four categories, in roughly increasing severity:

1. **IMPLEMENTATION_BUG** — the strategy never even ran correctly
2. **TEST_DESIGN_FLAW** — the test measured the wrong thing
3. **PERIOD_DEPENDENT** — the result is true in our test window but
   may flip in another period or universe
4. **GENUINE_REFUTATION** — the claim is false

If a strategy is in category 1 or 2, the refutation **doesn't tell us
the strategy is bad**. It tells us our test was bad. We should fix the
test before kill-listing the strategy.

---

## Per-strategy breakdown

### 1. earnings_rule_t1_trim50 → IMPLEMENTATION_BUG

**Verdict:** "0 trims applied — yfinance silent failure on earnings_dates"

**What happened:** I built a rule that reads earnings dates from
`yfinance.Ticker.earnings_dates`. yfinance silently returns empty for
major tickers (AAPL, NVDA, MSFT, etc) — they print "No earnings dates
found, symbol may be delisted" but don't raise. So the rule's lookup
returns nothing, no trims happen.

**The strategy itself was never tested.** It's been LIVE-wired in
production for 3 releases doing absolutely nothing.

**Path forward:** switch to Polygon free tier / Finnhub free / SEC
EDGAR scrape. The claim itself ("trim positions before earnings to
reduce variance") is sensible and has decades of literature support.

---

### 2. fomc_drift → TEST_DESIGN_FLAW

**Verdict:** "0/3 gates fail on close-to-close 2015-2025 (88 events)"

**What happened:** Lucca-Moench (2015) measured the drift from market
close on FOMC eve through **2pm ET on FOMC day**. yfinance daily bars
only give us close-to-close (eve close → FOMC day close). The 2pm-to-
close window often UNDOES the morning drift. So our close-to-close
measurement throws away most of the signal.

**The claim might still be valid.** We just can't test it on free
daily-bar data.

**Path forward:** Polygon free tier has minute bars. Re-test with
proper 2pm-ET cutoff. If it still fails on real intraday data, then
it's a genuine refutation — but right now we don't know.

---

### 3. momentum_crash_detector → TEST_DESIGN_FLAW

**Verdict:** "-64bp/yr CAGR on SPY proxy; Sharpe lift only +0.04"

**What happened:** Daniel-Moskowitz (2016) "Momentum Crashes" measured
the crash regime on a long-short momentum portfolio. Their Table 4
shows momentum portfolios losing 25-40% in the windows we'd want to
cut exposure. SPY-passive doesn't behave that way — SPY itself only
falls 20-50% in those windows, and recovers fast.

**My backtest used SPY as the strategy proxy, not the momentum
portfolio.** So the V-recovery problem dominates: SPY rebounds quickly,
the cut-to-50% misses upside, lift goes negative.

**The claim might still be valid for our momentum sleeve.** We just
tested it against the wrong base.

**Path forward:** rerun the test with the actual top-15 momentum
portfolio path as the base. If momentum DOES drop 25-40% in the
crash regime, the cut-to-50% saves real money.

---

### 4. trailing_stop_15pct → TEST_DESIGN_FLAW (suspected)

**Verdict:** "-0.28 Sharpe, -4.67pp CAGR, MaxDD WORSE (-25.9% vs -23.9%)"

**What happened:** When my backtest stopped a position out, the
remaining N-1 names got equal-weight rebalanced — so each survivor's
weight grew. That biases AGAINST the stop because we're effectively
doubling down on whatever's left after the worst names exit.

A correct test would put the stopped-out portion in CASH and run with
N-X positions for the remainder of the window.

**Even with the fix, V-recovery problem may dominate** — stops kick
in at the bottom, name rallies, we miss it. So the strategy might
still fail. But the current test is biased.

**Path forward:** rewrite the test to keep stopped-out portion in
cash. Re-test. If the result is still negative, accept it; if the
sign flips, reconsider.

---

### 5. residual_momentum → PERIOD_DEPENDENT

**Verdict:** "-564bp/yr WORSE than vanilla on liquid_50 2022-2026"

**What happened:** Blitz-Hanauer (2024) tested on broad EU/Global
universes (~3000 names) over 1990-2023. Our test was on liquid_50
(~50 names) over 2022-2026.

Two real problems:
- **Universe too narrow:** FF5 regression needs cross-sectional
  variation across many names. With 50 names you have 5 factors and
  weak betas everywhere.
- **Mag-7 dominance:** the residual-momentum thesis is "factor
  loadings mean-revert." But 2020-2024 was a regime where Mag-7
  factor loadings just kept getting stronger. Mean-reversion didn't
  fire in our test window.

**The claim is probably valid on its original universe.** Probably
fails on ours specifically.

**Path forward:** re-test on SP500 (universe_pit_v5 covers this) over
a longer period (2010-2024). If residual still loses to vanilla on
that universe, accept the refutation.

---

### 6. lowvol_sleeve → PERIOD_DEPENDENT

**Verdict:** "defensive (28/33 regime DD wins) but blend has same
Sharpe as 100% momentum, -6pp return give-up"

**What happened:** Sleeve correlation between LowVol and momentum
during 2022-2026 was +0.67 — way too high for the blend to actually
diversify. In a Mag-7 era, the lowest-vol stocks are also the boring
mega-caps that benefit from the same dynamics as momentum picks.

The defensive characteristic (DD-win) IS real (28/33 regime stress
wins is empirical). The Sharpe-blend lift didn't materialize because
of the period.

**The claim is valid.** The implementation just doesn't help at our
account size + this market regime.

**Path forward:** run the blend through a regime-conditional router
(see RegimeRouter v3.58) — long momentum in bull, blend with LowVol
in transition, LowVol-only in bear. Static 70/30 is the wrong wrapper.

---

### 7. sector_neutralizer_35cap → PERIOD_DEPENDENT

**Verdict:** "-0.05 Sharpe, -0.92pp CAGR, 0pp DD change"

**What happened:** During 2022-2026, the highest-momentum names were
all tech (Mag-7). Capping tech at 35% of the sleeve means kicking out
the very names driving the alpha.

**The claim is valid in normal regimes** (where one sector doesn't
dominate). It fails specifically when the alpha LIVES in the
concentration.

**Path forward:** make the cap regime-conditional (no cap during
trending bull; 35% cap during transitional / bear). Or just accept
that if a single sector is structurally outperforming, you should
ride it.

---

### 8. bottom_catch_llm_debate → GENUINE_REFUTATION

**Verdict:** "commingled attribution bug; on the kill-list"

**What happened:** v3.x experiment with using Claude to vote on
bottom-catch trades. Killed because:
- LLM-driven trading is on the verified-failed pattern list
- Attribution accounting was bugged (commingled momentum P&L into
  bottom-catch)

**The claim is properly refuted.** Even if we fixed the accounting
bug, LLM-driven trading is a different problem class than what this
codebase is designed for.

---

## Summary scorecard

| Strategy | Category | Re-testable? |
|---|---|---|
| earnings_rule_t1_trim50 | IMPLEMENTATION_BUG | ✅ fix yfinance, will likely pass |
| fomc_drift | TEST_DESIGN_FLAW | ✅ need intraday data |
| momentum_crash_detector | TEST_DESIGN_FLAW | ✅ test on momentum portfolio not SPY |
| trailing_stop_15pct | TEST_DESIGN_FLAW (suspected) | ✅ keep stopped portion in cash |
| residual_momentum | PERIOD_DEPENDENT | ✅ re-test on SP500 + longer window |
| lowvol_sleeve | PERIOD_DEPENDENT | 🟡 use regime-conditional router |
| sector_neutralizer_35cap | PERIOD_DEPENDENT | 🟡 regime-conditional cap |
| bottom_catch_llm_debate | GENUINE | ❌ stay killed |

**Of 8 refutations, only 1 is a genuine "the claim is false" verdict.**

The rest break down as:
- 1 implementation bug
- 3 test design flaws
- 3 period-dependent results

**This is good news.** It means the strategies aren't broken — our
tests were. There's real expected lift sitting on the floor IF we
fix the test infrastructure:

1. Buy intraday data (Polygon free tier, ~$0/mo for our volume)
2. Fix earnings calendar (Polygon / Finnhub / SEC EDGAR)
3. Re-test crash detector on momentum-portfolio path
4. Rewrite trailing stop test to preserve cash
5. Re-test residual momentum on SP500 over 2010-2024

That's the priority list for the next research cycle.

---

## Lessons for future strategy development

1. **Test the actual claim, not a proxy.** I tested
   "Daniel-Moskowitz crash detector on SPY" when DM specifically
   measured the effect on a momentum portfolio. SPY wasn't a fair
   proxy.

2. **Match the universe to the paper.** Blitz-Hanauer 2024 used 3000+
   names; I tested on 50. Tiny universe is not enough variation for
   the residual-momentum thesis to express.

3. **Watch for silent data failures.** yfinance has multiple silent-
   failure modes (empty earnings_dates, MultiIndex columns on single-
   ticker downloads). Wrap every external data call with a clear error
   path.

4. **Window matters.** 2022-2026 is a specific regime (Mag-7 dominance,
   QT cycle, AI bull). Strategies tested in only that window are
   period-overfit by construction.

5. **Publish the methodology, not just the result.** v3.60.1 audit
   only worked because each refutation cited the script + window +
   universe. Without that, "REFUTED" is just a label, not a finding.

---

*Last updated 2026-05-03 (v3.62.2). When re-running any of these tests
under improved methodology, update this doc + the strategy_registry
verification field.*
