# Verification audit — 2026-05-03 (v3.60.1)

*Triggered by user question: "are your claims verified and backtested?"*

**TL;DR: Most of my P&L claims are NOT verified on our data. Several are
empirically REFUTED. The "+140bp/yr net lift" headline from v3.60.0 is
wrong. True verifiable edge is much smaller.**

---

## What was claimed vs what backtests show

| Claim | Source | Backtest verdict |
|---|---|---|
| Walk-forward "mean Sharpe +1.16" | `run_walk_forward.py` summary | ⚠️ MEASUREMENT ARTIFACT. Mean of per-window Sharpes ≠ Sharpe of aggregate. **Real annualized Sharpe = +0.55** (95% CI [+0.12, +0.98]) per `verify_walkforward_significance.py`. |
| Walk-forward "62% positive" | same | ✅ Real (24 windows, 62% positive). But mean per-window return t=1.34 — **NOT significant at 95%** (CI overlaps zero). |
| LowVol "28/33 regime DD wins" | `stress_test_v5.py` | ✅ Real defensive characteristic. But… |
| LowVol blend lifts Sharpe | (V5 proposal) | ❌ REFUTED. `multi_sleeve_backtest.py`: blend Sharpe 0.80 vs 100%-momentum 0.82. -6pp return for ≈ same Sharpe. |
| MomentumCrashDetector +80bp/yr | Daniel-Moskowitz 2016 | ❌ REFUTED on SPY proxy. `backtest_crash_detector.py`: -64bp/yr CAGR. Catches 2008 (+3.3pp) but burns recoveries (-2.9 / -3.4pp). Same V-recovery problem as v3.x HMM. |
| Residual momentum +70bp/yr | Blitz-Hanauer literature | ❌ REFUTED on liquid_50. `backtest_residual_momentum.py`: residual CAGR -4.90% vs vanilla +0.74% over 2022-2026. -564bp/yr WORSE. |
| SectorNeutralizer adds value | (v3.58 SHADOW) | 🟡 NEUTRAL-NEGATIVE on backtest. `backtest_overlays.py`: -0.05 Sharpe, -0.92pp CAGR, 0pp DD change. Doesn't help. |
| TrailingStop -15% adds value | (v3.58 SHADOW) | ❌ REFUTED. `backtest_overlays.py`: -0.28 Sharpe, -4.67pp CAGR, **MaxDD WORSE** (-25.9% vs -23.9%). Whipsaws in volatile recoveries. |
| EarningsRule LIVE saves 15bp | (v3.58 SHADOW + LIVE wired) | ❌ INERT. `backtest_overlays.py`: 0 trims applied because yfinance.earnings_dates returns empty for most tickers (silent failure). The LIVE wiring in v3.58.1 has been a no-op. |
| MOC orders +35bp/yr | spread-cost calc | ❌ UNVERIFIED. No real fill data — `slippage_log` table doesn't even exist (created on first order; we haven't placed any since shipping). |
| DrawdownCircuitBreaker LIVE | wired in v3.58.1 | ⚠️ UNTRIGGERED. Wired correctly but never fired in production data; only tail-event verification. |
| Parameter surface FLAT (16.5%) | `parameter_sensitivity.py` | ✅ Real. Strategy is not overfit to canonical params. |
| FOMC drift 0/3 gates fail | `backtest_fomc_drift.py` | ✅ Real (88 events 2015-2025 close-to-close). Sleeve dead on free data. |

## The pattern

Of **12 specific P&L-impact claims** I made over the last few releases:

- ✅ **3 verified** (parameter flatness, FOMC drift dead, walk-forward Sharpe ~0.55 modest-but-real)
- ❌ **6 refuted on backtest** (LowVol blend, crash detector, residual momentum, sector neutralizer, trailing stop, earnings rule wiring)
- ⚠️ **3 unverified** (MOC orders, slippage tracker, drawdown breaker — no data to test)

**Strike rate of LITERATURE-CALIBRATED claims when actually backtested on our system: 0/4.**

## Why this happened

1. **I conflated "well-published" with "works here."** Daniel-Moskowitz 2016, Blitz-Hanauer 2024, Frazzini-Pedersen 2014 are real findings on real data — but on different universes, different periods, different implementations. Re-testing on our universe (liquid_50) and period (2022-2026) gives different answers. Liquid_50 is specifically narrow + Mag-7-dominated, which violates the diversification assumptions in many of these signals.

2. **I shipped SHADOW infrastructure WITHOUT shadow data.** The whole point of SHADOW status is that the module computes alongside LIVE so you can A/B before promoting. But none of the v3.58 modules ever ran in shadow against real LIVE order flow because (a) we don't run LIVE daily, (b) the slippage_log table never got populated, (c) the EarningsRule LIVE wire-in has been silently no-op.

3. **I didn't backtest before recommending.** The cost_impact_report.py headline of "+140bp/yr" was constructed from literature numbers and bp arithmetic, not from running each module on historical data. That's the textbook overfitting failure mode dressed up as discipline.

4. **Some tests are still suspect.** The trailing stop backtest doesn't keep stopped-out portion in cash (re-distributes to survivors, which biases against the stop). The earnings rule "test" was an inadvertent no-op due to yfinance silent failure. Even my refutations need their own scrutiny.

## What's actually defensible right now

**Strategy:**
- Vanilla momentum top-15 monthly rebalance, ~Sharpe 0.55 OOS (24 quarter windows 2022-2026)
- 95% CI on Sharpe: [+0.12, +0.98] — significantly positive but lower bound is small
- Per-window return t=1.34, not statistically significant at 95%
- Parameter surface FLAT — not overfit

**Risk infrastructure (genuine, not measured-bp):**
- DrawdownCircuitBreaker: cheap insurance even if untriggered
- Existing kill_switch.py + risk_manager freezes
- v3.58 stress test framework

**What I should NOT recommend flipping LIVE:**
- ❌ MOMENTUM_CRASH_STATUS=LIVE (refuted on SPY proxy)
- ❌ residual momentum LIVE swap (refuted on our universe)
- ❌ LowVolSleeve blend LIVE (refuted on multi-sleeve test)
- ❌ TrailingStop LIVE (refuted)
- ❌ SectorNeutralizer LIVE (refuted)
- ⚠️ USE_MOC_ORDERS=true (unverified)
- ⚠️ EarningsRule LIVE — already on, but doing NOTHING (yfinance bug)

## What to actually do

1. **Fix the EarningsRule yfinance bug** — switch to a working earnings calendar source (Polygon free tier? Finnhub free tier? Manual scrape?) before claiming the rule does anything.

2. **Run the slippage_log loop in paper-trading** for 30 days to actually measure fill quality. Then re-evaluate MOC orders with REAL data.

3. **Re-test trailing stop with cash-preserving simulation** (don't redistribute to survivors). May still fail; honest test required.

4. **Test crash detector on actual momentum portfolio path**, not SPY. Daniel-Moskowitz claim was specifically about momentum, not the index. SPY-proxy result may not refute the claim properly.

5. **Test residual momentum on SP500 universe** (not liquid_50) and on a longer window (5+ years). Liquid_50 + Mag-7 dominance may be the wrong test bed.

6. **Stop building more sleeve modules until existing ones are properly validated.** v3.58 shipped 15 modules; 0 of them have passed real promotion gates.

7. **Honest comms going forward:** every P&L claim must carry a `verification_status` flag (VERIFIED / REFUTED / CALIBRATED / UNTESTED). The cost_impact_report should default to REFUTED until proven otherwise.

## Net expected lift after this audit

**Original v3.60.0 headline: +140 bp/yr.**

**Audited true number: ~0 bp/yr verified, with -50 to +30 bp uncertainty.**

The strategy generates real (modest) Sharpe via vanilla momentum. None of the SHADOW or LIVE-wired overlays adds measurable lift on backtest. Several reduce P&L. The dashboard, tests, infrastructure, observability work is solid. The strategy improvements are not.

This is the honest answer to "make money for me."

---

*Audit triggered by Richard 2026-05-03. Documented in v3.60.1.
Future P&L claims must reference this doc and pass backtest before
recommendation.*
