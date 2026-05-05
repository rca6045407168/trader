# Risk Management Framework — v5 Multi-Sleeve

*CRO-grade risk governance memo. Synthesized from a Round-2 advisory swarm (CRO lens, May 2026). Companion to V5_ALPHA_DISCOVERY_PROPOSAL.md, SCENARIO_LIBRARY.md, BLINDSPOTS.md, TESTING_PRACTICES.md. Status: proposal — pending Richard's approval before Claude Code implements `risk_manager_v5.py`.*

---

## Executive verdict

Your current risk surface — `kill_switch.py` (portfolio-level DD halt at -8% over 180d) plus `risk_manager.py` (per-name 30%, gross 95%, daily-loss halt -3%, VIX scaling) — is **sufficient for v3.42's single-sleeve momentum core. It is dangerously insufficient for v5.**

Adding three orthogonal sleeves (VRP, FOMC drift, ML-PEAD) alongside momentum multiplies the risk surface by roughly 3x: four correlation matrices to monitor instead of one, four factor exposures to budget, four sets of behavioral biases to hedge against. The four-sleeve system also creates a new failure mode that does not exist with one sleeve — **execution blindness during a kill-switch event**: when portfolio DD triggers, you cannot tell which sleeve caused it without per-sleeve P&L attribution. You halt everything and kill healthy sleeves alongside the broken one.

This document specifies the minimum-viable CRO mandate: explicit per-sleeve risk budgets, correlation governance with scenario-conditional triggers, and tail-event response protocols pre-committed before deployment. The framework is sized to a $10k Roth IRA but scales without rewrite to $1M+.

---

## 1. The risk taxonomy

Eight risk classes exist. Each is measured separately; each has limits.

**Market risk** (directional beta to broad equity). Measured as portfolio beta to SPY, delta to SPY, and max-loss under -30% SPY shock using your historical regimes (2008 GFC, 2018-Q4, 2020 COVID, 2022 bear).

**Factor risk** (exposures to systematic premia). Measured via Fama-French 5-factor regression of daily portfolio returns on Mkt-RF, SMB, HML, RMW, CMA, plus a custom WML momentum factor. Critical because all four sleeves can load momentum simultaneously and produce hidden net leverage (Asness, Frazzini, Pedersen, 2019).

**Concentration risk** (idiosyncratic exposure to single names). Existing 30% per-name cap is preserved. New: per-sleeve concentration cap to prevent the case where a name appears in both momentum and ML-PEAD universes and the combined exposure exceeds intended risk.

**Correlation risk** (joint movement of sleeves). Momentum × VRP runs +0.15 in calm regimes and +0.60 in vol spikes (March 2020, August 2024). FOMC × momentum is near-zero in rates stability and +0.50 in rate shocks. Without governance, the diversification benefit you're paying for vanishes exactly when you need it.

**Liquidity risk** (inability to exit without slippage). Bounded by trading at-open, no intraday forced selling, and per-sleeve volume caps. At $10k it's small but real for VRP options legs.

**Counterparty risk** (Alpaca outage, broker failure). Negligible in paper; material at LIVE arming.

**Model risk** (backtester assumptions don't match reality). Specifically VRP options-IV realism in stress regimes; addressed in TAIL_RISK_PLAYBOOK.md.

**Behavioral risk** (your decisions under stress). Pre-commitment governance; the kill-switch protocol below has explicit response actions to remove discretion at every threshold.

Citations: Jorion (2007) *Value at Risk*; Acerbi & Tasche (2002) on Expected Shortfall; Markowitz (1952) on concentration; Acharya & Pedersen (2005) on liquidity-adjusted CAPM; Barberis & Thaler (2003) on behavioral.

---

## 2. Position-level limits

**Per-name (existing, unchanged).** 30% gross. Binding constraint for momentum core.

**Per-sleeve (new) — gross caps:**

- Momentum core: 50% (down from current 80% per v5 proposal). Proven, +0.95 PIT Sharpe.
- VRP sleeve: 15%. Defined-risk put-spreads only; never naked.
- FOMC drift sleeve: 5%. Macro directional, isolated to avoid factor double-up.
- ML-PEAD sleeve: 10%. Experimental until 90+ days of LIVE evidence.
- Cash buffer: 0–10% (varies by allocation rule below).
- **Total portfolio gross: 80% standard, 95% absolute ceiling.** No leverage.

**Per-factor exposure caps (new) — measured weekly via Fama-French regression:**

- Momentum (WML): ≤ +0.80 beta. All four sleeves can load momentum; cap their net.
- Low-volatility (BAB / QMJ proxies): ≤ +0.40 beta. VRP is structurally low-vol; momentum core can be too in late-cycle quality regimes.
- Size (SMB): ≤ +0.30 beta.
- Value (HML): no cap (orthogonal to current sleeves).
- Quality (RMW), Profitability (CMA): ≤ +0.20 beta.

**Per-sleeve correlation caps (new) — scenario-conditional:**

In **normal regime** (VIX < 18, term spread positive, OAS < 150bp): no hard correlation cap, but monitor. Expect momentum × VRP ∈ [+0.05, +0.25]. If observed correlation > +0.40 over 5 trading days, halve VRP allocation.

In **elevated regime** (VIX 18–25 or rates moving > 25bp/day): expect correlations to rise. FOMC × momentum may hit +0.50. Cut FOMC drift sleeve to 2.5% (half) if observed correlation > +0.45 over 5 days.

In **stress regime** (VIX > 25 or daily SPY < -2%): all sleeves correlate toward 1.0. Pre-committed action: halt new positions across all sleeves, reduce all sleeves proportionally to 50% of notional, switch from weekly to daily risk review.

---

## 3. Portfolio-level risk metrics

**Value at Risk (VaR) — measure daily, report weekly.**

*Parametric VaR at 95%* (Jorion 2007): assume rolling-252-day return distribution is normal. VaR(95%) = portfolio_mean − 1.645 × σ. For a $10k account at +0.95 expected Sharpe and ~1.2% rolling daily σ, parametric VaR(95%) ≈ -$90 to -$120 per day. Breach threshold: -$150.

*Historical VaR at 95% / 99%*: rank the worst 1,260 daily returns from the 5-year backtest. Extract 63rd-worst (95th percentile) and 13th-worst (99th percentile). Always worse than parametric for momentum strategies because of fat left tails (Bender, 2013). **The gap between historical and parametric VaR is itself a signal — when it widens, the distribution is becoming more fat-tailed.**

*Monte Carlo VaR (post-deployment)*: simulate 10,000 forward 20-day return paths using a regime-conditional covariance matrix recomputed weekly. Report 95% / 99% percentiles. This is the formal version of what your existing scenario stress tests do informally.

**Expected Shortfall (ES / CVaR) — the metric institutions actually obsess over.**

ES(95%) = mean of all returns *worse than* the 95th percentile, not just the 95th-percentile cutoff (Acerbi & Tasche 2002). For momentum strategies ES is typically 30–50% worse than VaR at the same level. Example: VaR(95%) = -$100, ES(95%) ≈ -$150. **This is the right number for "how bad can a bad day actually be."** Compute weekly. Breach threshold: ES(99%) > -$200 on a $10k account — that is "down 2% on a 1-in-100 day," and it should not happen at v5 sizing.

**Maximum Drawdown — protocol with explicit response actions.**

Existing 180-day rolling max-DD halt at -8%. Refined to four thresholds with pre-committed responses:

- **-5% (yellow alert).** Pause new position sizing across all sleeves. Continue holding existing. Increase review cadence weekly → twice-weekly. No halt.
- **-8% (red alert, existing kill).** All sleeves freeze. Daily review. Liquidate VRP sleeve in full (highest-Sharpe, highest tail-risk experimental bet — reduce complexity in stress).
- **-12% (escalation).** Liquidate FOMC drift and ML-PEAD sleeves. Trim momentum core from 50% to 30% gross (keep top 5 by score, drop ranks 6–15). Raise cash to 50%.
- **-15% (catastrophic).** Liquidate all. -$1.5k on $10k account. Risk is no longer "managed"; it's catastrophic. Manual re-arming required after 30-day cool-off and external review.

**Time-Under-Water (new — see BLINDSPOTS.md section 6).** Track consecutive days below prior peak. If > 90 days, increase scenario-stress refresh cadence from monthly to bi-weekly. Document recovery path in weekly notes. This is the metric that catches behavioral drift before performance drift becomes visible.

**Brinson-Fachler attribution — weekly.** For each sleeve, decompose daily return into allocation effect, selection effect, interaction. Goal: identify whether underperformance comes from "bad regime, you allocated wrong" (allocation bet) or "your picks were bad" (selection bet). If allocation, retune the scenario-conditional weights below. If selection, audit the signal itself.

---

## 4. Scenario-conditional sizing

The static 50/15/5/10/cash split is the default; the actual allocation flexes by regime. Replace the proposed static allocation with explicit IF-THEN rules.

**Regime detection (daily, computed from free data):**

- **Momentum regime** = (12-month cross-sectional momentum dispersion across S&P 500) > 20th percentile. Good for momentum sleeve.
- **Vol regime** = VIX > 18. Reduce VRP allocation; IV crush risk rises when realized vol is already elevated.
- **Rates regime** = 10Y yield > prior 20-day MA. FOMC drift edge is regime-conditional on rate trajectory.
- **Credit regime** = HYG-LQD OAS spread > 150bp. Stress signal; reduce all sleeves.

**Allocation rules (CPCV-validate before deploying):**

```
IF momentum_regime AND credit_spread < 150bp:
    M=50, VRP=15, FOMC=5, ML-PEAD=10, Cash=20
ELIF vol_regime:
    M=45, VRP=10, FOMC=5, ML-PEAD=5, Cash=35
ELIF rates_regime:
    M=45, VRP=12, FOMC=5, ML-PEAD=5, Cash=33
ELIF credit_spread > 150bp:
    M=30, VRP=8, FOMC=2.5, ML-PEAD=5, Cash=54.5
ELSE:
    M=45, VRP=12, FOMC=4, ML-PEAD=6, Cash=33
```

The rule set is hand-engineered for legibility but must pass CPCV with PBO < 0.5 on 30 OOS sub-windows before deployment. If CPCV reveals the rule is overfit, simplify.

**Why the rule-based approach beats discretion:** you don't wake up and decide "feels like a good day to add VRP." The rule decides. Pre-commitment matters because in stress regimes, every behavioral study shows discretion drifts toward the wrong direction (Kahneman/Thaler; Barberis-Thaler 2003). Citation: this is the AQR / Bridgewater discipline, scaled to retail.

---

## 5. Risk governance protocol

**Daily (automated, no human):** `risk_manager.py` computes per-name gross %, portfolio gross %, daily P&L, 180d max-DD, parametric VaR(95%), Fama-French factor loadings, sleeve correlation matrix. Logs to CSV with timestamp. If any breach (gross > 95%, daily loss < -3%, DD > -8%), email to richard.chen.1989@gmail.com with metric name and threshold. Subject line `[RISK] BREACH: <metric>` for grep-ability.

**Weekly (30 min, human reviews automated output):** read the risk CSV. Chart the week's metrics. Sleeve correlation 4×4 heatmap. Brinson attribution by sleeve. Document one-line findings — "VRP correlation spiked to +0.55 Wednesday, allocation rule cut VRP to 7.5%" — as audit trail for future post-mortems.

**Monthly (1 hour strategic):** full stress test on the trailing 21 days, compare predicted to realized. Scenario library refresh. Monthly Fama-French regression on each sleeve; flag if loadings exceed budget. If any drawdown ≥ -5% occurred, write a 3-5 sentence drawdown-journal entry — what triggered it, how rules responded, what was learned.

**Quarterly (formal governance, 3 hours):** model validation against fresh Fama-French data from Kenneth French library. Correlation matrix audit (rolling 60-day across all sleeves). Backtest refresh — replay last 3 months of LIVE through the backtester; flag if Sharpe estimates drift > 0.3. External review of the quarter's results (per BLINDSPOTS.md section 7).

**Annual (audit + reset):** kill-switch threshold recalibration, behavioral compliance review (did you break any pre-committed rules), capital scaling decision (Roth IRA only or expand to taxable mirror).

---

## 6. Tail-event response protocol

Every event has a pre-committed response. No discretion under stress.

**Daily loss > -3%.** Email alert (immediate). Halt trading for remainder of day. At 16:00 ET, post-mortem: which sleeve caused it? Was it expected (in scenario library) or unexpected (new shock)? If unexpected, switch to intraday daily-loss monitoring (every 15 minutes) for next 5 trading days.

**180d max-DD ≥ -5%.** Pause new position sizing in all sleeves. Continue holding. Increase review cadence to twice-weekly.

**180d max-DD ≥ -8% (existing kill switch).** Halt all rebalancing. Liquidate VRP sleeve in full. Freeze FOMC drift and ML-PEAD entries; let existing positions expire. Momentum core: do not add new names; let existing positions run. Daily risk review until DD recovers to -6%.

**180d max-DD ≥ -12%.** Liquidate FOMC drift and ML-PEAD. Trim momentum core from 50% gross to 30% (keep top 5 names by score, drop 6–15). Raise cash to 50%. Daily email with recovery plan.

**180d max-DD ≥ -15%.** Liquidate all. Manual re-arm only, and only after a 30-day cool-off, an external human review, and a written re-arming pre-commit.

**Correlation breach (momentum × VRP > +0.50 sustained 5 days in normal regime).** Reduce VRP by 50%. Audit cause: common factor loading? Common shock? Document in weekly report.

**Portfolio gross > 95%.** Next rebalance (monthly for momentum, quarterly for VRP), trim smallest positions to bring gross back to 80–90%.

**Factor exposure breach (momentum beta > +0.80 over 5-day average).** Audit which sleeve drove it. Trim accordingly. If multiple sleeves caused it (likely), rebalance toward momentum-neutral sleeves (FOMC drift up to 7.5%).

---

## 7. The single most important metric you're not tracking

**Time-Under-Water (TUW) and Recovery Asymmetry.**

You track maximum drawdown. You don't track how *long* you spend underwater. This is critical because behavioral risk compounds with time: a -10% drawdown recovered in one week is a contained tail event with low behavioral drift; a -10% drawdown taking six months to recover is a psychological drain that breaks even disciplined operators. Momentum strategies have asymmetric recovery — 2022's drawdown bottomed at -25% in June, recovery to peak took until March 2023. Nine months underwater. The temptation to abandon the system at month 6 is nontrivial.

Implementation: weekly report includes "Current TUW: 45 days. Historical median for ≥-5% DD: 12 days. 95th percentile: 90 days." This calibrates whether you're in normal recovery or outlier territory.

Pre-commitment, signed before LIVE arming: "If DD > -5% and TUW > 60 days, I will not abandon the system. I will not trade around the kill-switch even at 180 days TUW. I accept that 5% of paths will exceed 120 days underwater." This is the equivalent of Odysseus tying himself to the mast.

---

## 8. Implementation roadmap

**Pre-deployment (during v5 build, before any sleeve goes LIVE):**

1. Build `risk_manager_v5.py`: factor regression, correlation matrix, per-sleeve tracking, scenario detection, tail-event response state machine. Target: ~600 lines, ~16 hours.
2. Document allocation rules in standalone `ALLOCATION_RULES.md` (separate from code for audit clarity). 2 hours.
3. CPCV-validate allocation rules on 30 OOS windows across the v5 SCENARIO_LIBRARY Tier-1 regimes. 6 hours.
4. Wire scenario-conditional sizing into the rebalance path. 4 hours.
5. Update `kill_switch.py` to fire the four-threshold protocol (-5 / -8 / -12 / -15). 4 hours.

Total: ~32 hours of risk-framework work, in addition to v5 sleeve build.

**Post-deployment (v5.0+ LIVE):**

Weeks 1–4: daily risk review, no exceptions. Weeks 5–12: weekly review with daily-automated logging. Month 3+: monthly stress test. Month 4+: quarterly governance review.

---

## 9. Why this framework survives

Pre-committed. You don't debate when a rule fires; you execute. Scenario-aware. You're preparing for -8%, -12%, -15%, not hoping for "no drawdown." Quantified. Every decision has a metric, threshold, consequence. Scalable. Adding a 5th sleeve in v6 means one more row in the allocation rule and one more column in the correlation matrix; no rewrite.

The alternative — relying on the existing two-gate `risk_manager.py` + `kill_switch.py` for a four-sleeve portfolio — works until it doesn't. A -15% drawdown on a $10k Roth is -$1,500. That's the moment you override the kill-switch, double down on the barbell, or abandon ship. Pre-commitment saves you from yourself.

---

## Sources

- Acerbi, C., & Tasche, D. (2002). "On the Coherence of Expected Shortfall." *Journal of Banking & Finance*, 26(7).
- Acharya, V. V., & Pedersen, L. H. (2005). "Asset Pricing with Liquidity Risk." *Journal of Financial Economics*, 77(2).
- Asness, C. S., Frazzini, A., & Pedersen, L. H. (2019). "Quality for the Price of Value." *JPM*, 45(1).
- Barberis, N., & Thaler, R. (2003). "A Survey of Behavioral Finance." *Handbook of the Economics of Finance*.
- Bender, J. (2013). "The Promises and Pitfalls of Factor Timing." Research Affiliates.
- Jorion, P. (2007). *Value at Risk: The New Benchmark for Managing Financial Risk* (3rd ed.). McGraw-Hill.
- Kenneth French Data Library (continuously updated factor data; free).
- Litterman, R. (2003). "Modern Investment Management: An Equilibrium Approach." *JPM*, 29(5).
- Markowitz, H. (1952). "Portfolio Selection." *JF*, 7(1).
- Treynor, J., & Black, F. (1973). "How to Use Security Analysis to Improve Portfolio Selection." *Journal of Business*, 46(1).

---

*Last updated 2026-05-04. Companion to V5_ALPHA_DISCOVERY_PROPOSAL.md, SCENARIO_LIBRARY.md, BLINDSPOTS.md, TESTING_PRACTICES.md. Status: PROPOSAL.*
