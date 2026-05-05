# Tail Risk Playbook — EVT for v5

*Catastrophe-modeling memo. Synthesized from a Round-2 advisory swarm (reinsurance / actuarial lens, May 2026). Companion to RISK_FRAMEWORK.md, V5_ALPHA_DISCOVERY_PROPOSAL.md, SCENARIO_LIBRARY.md. Status: proposal — informs `risk_manager_v5.py` tail-event gates and VRP sleeve sizing.*

---

## Why this exists

Your CPCV + Deflated Sharpe + PBO framework correctly answers "is this strategy's mean Sharpe estimate honest." It does not answer "what's the realistic 1-in-100-year loss." Those are different questions. The first is a frequentist mean estimate; the second is a tail-quantile extrapolation requiring a different mathematical apparatus — Extreme Value Theory.

This matters specifically for v5 because the proposed Variance Risk Premium (VRP) sleeve is the canonical fat-tailed retail strategy: collect 2–4% annual premium nine months out of ten, lose 30–50% of sleeve notional in a single month when realized vol explodes. Sharpe-based analysis systematically understates this asymmetry. A reinsurer would not write a hurricane cat bond using only mean-loss data; you should not size VRP using only Sharpe.

---

## 1. The math of fat tails

Equity returns are not normally distributed. This is one of the most-replicated empirical findings in finance and has been documented since Mandelbrot (1963). Daily S&P 500 return distributions exhibit kurtosis 5–10× that of a Gaussian distribution [Cont, 2001 — "Empirical properties of asset returns: stylized facts and statistical issues," *Quantitative Finance* 1, 223–236]. The left tail decays as a power law, not exponentially:

$$P(\text{loss} > x) \sim x^{-\alpha}$$

Empirical estimates of α for SPX daily returns since 2000 cluster around α ≈ 3.0–3.5 via the Hill estimator (Cont 2001; recent post-2020 work by Danielsson). This means a 5σ loss is roughly **30× more frequent** than the Gaussian model predicts and a 10σ loss is roughly **1000× more frequent**. The "sigma" framework, which works adequately for portfolio risk in calm regimes, breaks down precisely when you need it most — in the tail.

For short-vol strategies the situation is worse. The realized P&L of a short-volatility position has *negative* convexity to the underlying tail, which makes the *implied* tail of the strategy's P&L distribution heavier than the underlying's. A short put-spread's effective tail exponent α may drop to 1.5–2.5, putting it firmly in "infinite variance" territory under naive parametric assumptions. Mandelbrot's original critique of Gaussian finance ([*The Misbehavior of Markets*](https://www.basicbooks.com/titles/benoit-mandelbrot/the-misbehavior-of-markets/9780465043576/), 2004) is most acute for short-vol; Taleb's [*Statistical Consequences of Fat Tails*](https://arxiv.org/abs/2001.10488) (2020) extends the warning operationally.

---

## 2. Return-period thinking

Reinsurers don't ask "what's my 95% VaR." They ask "what's the expected loss in a 1-in-100-year event, and how often does it actually happen."

For your portfolio at the v5 proposed allocation (50% momentum, 15% VRP, 5% FOMC drift, 10% ML-PEAD, 20% cash), the tail picture stratified by return period:

**1-in-10-year tail event** — analog: February 2018 Volmageddon, August 2024 yen unwind. VIX spikes 10–15 vol points in 1–4 days; equities -3 to -7%; realized vol elevated 1–3 weeks. Expected portfolio loss: **-3% to -7%**. Recovery: ~4–8 weeks. Manageable; within the existing -8% kill-switch threshold.

**1-in-100-year tail event** — analog: March 2020 COVID, October 2008 Lehman week, 1987 Black Monday. VIX spikes 60–150% in 1–2 days; equities -15 to -35% over 2–4 weeks; realized vol elevated 2–8 weeks. Expected portfolio loss: **-12% to -22%**. Recovery: 2–12 months. Trips the -12% escalation threshold; VRP and FOMC drift sleeves liquidate; momentum core trims to top-5.

**1-in-1000-year tail event** — analog: 1929 crash, 2008 GFC peak-to-trough, hypothetical Volcker 2.0. VIX > 200 sustained; equities -40 to -60% over 6–18 months; structural regime change. Expected portfolio loss: **-30 to -45%**. Recovery: years. -15% catastrophic threshold trips early; full liquidation; manual re-arm only.

The loss magnitudes do *not* scale linearly with return period. A 1-in-100 event is roughly 3–4× worse than a 1-in-10. A 1-in-1000 is roughly 3–5× worse than a 1-in-100. **The tail accelerates non-linearly with return period — that's the defining property of fat tails.**

---

## 3. Fitting the tail — GPD and POT

The standard EVT machinery for financial tails is the Peaks Over Threshold (POT) method, fitting a Generalized Pareto Distribution (GPD) to exceedances above a chosen threshold [Embrechts, Klüppelberg & Mikosch, *Modelling Extremal Events for Insurance and Finance*, 1997].

The procedure for your portfolio:

1. Pick threshold $u$ corresponding to the 95th percentile of historical losses (typically around -2% daily for SPX). About 5% of observations sit beyond it.
2. Collect the exceedances $\{Y_i = X_i - u : X_i > u\}$.
3. Fit a GPD to the exceedances, estimating shape parameter $\xi$ and scale parameter $\sigma$. For SPX since 2000, $\xi$ typically estimates around 0.3–0.5 (positive — heavy tail), $\sigma$ depends on the volatility regime.
4. Project return-period quantiles via:

$$\text{VaR}_p = u + \frac{\sigma}{\xi}\left[\left(\frac{n}{N_u}(1-p)\right)^{-\xi} - 1\right]$$

$$\text{ES}_p = \frac{\text{VaR}_p + \sigma - \xi u}{1 - \xi}$$

where $n$ is total observations, $N_u$ is the count of exceedances, and $p$ is the desired confidence level.

This gives you principled VaR and Expected Shortfall at 99.5% / 99.9% / 99.99% — return periods of 200, 1000, and 10000 days respectively — even when your historical sample contains zero observations at those quantiles. This is what insurance companies do for hurricane modeling. It's the right tool for VRP sizing.

Implementation: the `scipy.stats.genpareto` distribution and the [`pyextremes`](https://github.com/georgebv/pyextremes) library both support GPD fitting in 50 lines of Python. Apply to (a) raw daily SPX returns to validate the methodology against published estimates, then (b) backtested portfolio returns under the v5 allocation to derive a portfolio-level tail estimate.

---

## 4. VRP-specific tail math

The canonical short-vol payoff is asymmetric: collect $X$ premium with probability $p \approx 0.85$, lose $kX$ with probability $1-p$ where $k$ is leverage on the loss leg. For a defined-risk SPX put-spread (short 30-delta, long 10-delta, 30-day expiry), $k$ caps at the spread width / credit ratio — typically 4× to 8×.

Three historical VRP blowups calibrate the realistic tail:

**XIV termination (5–9 February 2018).** XIV (a daily-reset short-vol ETN) lost 93% in a single day. VIX spiked from 11 to 50 in five trading days. A static put-spread at v5 sizing (15% allocation, defined-risk 4× spread width) would have taken a sleeve-level loss of approximately -50 to -70%, translating to **-7 to -10% portfolio loss**. The defined-risk structure caps the disaster at the spread width and prevents the unbounded loss XIV experienced.

**March 2020 COVID.** VIX hit 82, S&P -34% in 22 days. Realized vol stayed elevated for 8 weeks. A 15% VRP sleeve would have lost 30–60% over the month: **-4.5 to -9% portfolio**. Recovery within 4 months given the V-shape — but if you'd been forced out at the bottom by margin calls or kill-switch action, you would have realized the entire loss.

**August 2024 yen carry unwind.** VIX 16 → 65 intraday on August 5. A 15% VRP sleeve lost roughly -20 to -35% over the week: **-3 to -5% portfolio**, mostly recovered within 30 days.

The pattern: defined-risk caps the disaster but doesn't eliminate it. Sizing VRP at 15% means accepting a realistic 1-in-100 sleeve drawdown of -50 to -70%, translating to -7.5 to -10.5% portfolio. That is on top of any concurrent momentum drawdown. **Sleeves correlate to 1.0 in stress** — see Section 6 below.

---

## 5. Sizing for the tail, not the average

Kelly criterion (Thorp 1962; MacLean, Thorp, Ziemba 2011) gives the growth-optimal bet size: $f^* = (pb - q)/b$ where $p$ is win rate, $b$ is win/loss ratio, $q = 1-p$. For a typical short-vol distribution ($p \approx 0.85$, $b \approx 1$), full Kelly recommends ~70% of capital. This is insane for a fat-tailed strategy because Kelly assumes a known, finite distribution — exactly the assumption that breaks for power-law tails.

Three standard fixes, in increasing rigor:

**Fractional Kelly.** Scale full Kelly by 0.10–0.50 to buffer against estimation error and unknown-tail risk. At 25% Kelly with $f^* = 0.70$, sleeve allocation drops to 17.5% — close to the v5 proposal's 15%.

**Kelly with explicit drawdown constraint.** Solve for the $f$ that maximizes long-term growth subject to a maximum drawdown constraint (e.g., 1-in-100-year sleeve loss ≤ 50%). This typically yields 10–20% of Kelly — even more conservative.

**CVaR-constrained sizing.** Define the loss ceiling at portfolio level (e.g., -10% per quarter). Back-solve sleeve allocation so that the 1-in-100 sleeve event corresponds to that ceiling. If the 1-in-100 VRP loss is -50% of sleeve, and the portfolio ceiling is -10%, then maximum VRP allocation is 20%.

The v5 proposed 15% sits at fractional Kelly (~25% of full) and CVaR-constrained at 75% of the -10% ceiling. Reasonable. Tighten to 10% if the GPD tail estimate (Section 3) shows $\xi > 0.5$ on the historical sample, indicating heavier tails than mean-Kelly assumes.

---

## 6. The ergodicity trap — why ensemble averages mislead

[Peters & Gell-Mann (2016, *PNAS*)](https://www.pnas.org/doi/10.1073/pnas.1607053113) and Taleb's ergodicity arguments distinguish two averages: the *ensemble average* (mean across many parallel universes running the same strategy) and the *time average* (the long-run growth rate of a single universe — your actual path).

For most strategies the two are close. For short-vol strategies they diverge sharply. A VRP sleeve has positive ensemble expectation (+3 to +4% annual), but a single time-path can spend years recovering from a single 1-in-50 loss event. The time-average growth rate (TAGR), which is what you actually experience, satisfies:

$$\text{TAGR} = \mu - \frac{\sigma^2}{2} - \text{tail correction}$$

When the tail is fat (high $\xi$), the tail correction term grows nonlinearly. For a strategy with $\mu = 0.04$, $\sigma = 0.15$, and $\xi = 0.4$, TAGR can drop to +0.5 to +1.5% — much lower than the ensemble Sharpe-implied return of +3 to +4%.

The implication for sizing: do not size on $\mu$. Size on TAGR. A 15% allocation that yields +1% time-averaged return on a $10k account is +$15/year — small. The point is that *survival* during the tail event is what unlocks compounding. Lose -10% in year 3 to a tail event and you compound the next 30 years off a 10% smaller base. The arithmetic is brutal.

This is the second reason — alongside fractional Kelly and CVaR — to size VRP at 10–15%, not 25%+. The growth-optimal allocation under known distributions (Kelly) is a different number from the survival-optimal allocation under fat tails (fractional Kelly or CVaR). For VRP, the gap is large.

---

## 7. The most likely black swan

Across a 30-year LIVE horizon, the highest-expected-impact tail event is *not* a single asset-class catastrophe (1929-style crash, 1973 stagflation, 2008 GFC). It is the **correlation regime break**: the day all "diversifying" sleeves correlate to 1.0 simultaneously and the carefully-engineered uncorrelated portfolio behaves like a leveraged single-name bet.

The mechanism: stress regime triggers cross-asset risk-off. All equity strategies (momentum, ML-PEAD, FOMC drift) lose simultaneously. Vol spikes (VRP loses concurrently). The cash buffer is the only sleeve that isn't decreasing. Correlation across sleeves jumps from +0.15 in calm to +0.85+ in stress.

Empirical incidence: roughly every 5–10 years (1998 LTCM, 2008 GFC, 2011 European sovereign, 2018-Q4, March 2020, March 2022, August 2024). Call it a 1-in-7-year event with conditional portfolio impact -10 to -25%.

This dominates the unconditional tail estimate because it occurs more frequently than the 1-in-100 single-event tails the GPD analysis emphasizes. **Over 30 years of LIVE, expected number of correlation-regime events: 4–6. Expected number of 1-in-100 single-event tails: 0.3.** The frequent moderate-severity event matters far more than the rare catastrophic one for cumulative compounding.

This is exactly why the RISK_FRAMEWORK.md scenario-conditional sizing (cut all sleeves 20% on credit spread > 150bp, halt new positions in stress regime) matters more than tail-VaR formalism. The tail-VaR formalism gives you the right *order of magnitude* for sleeve sizing; the regime-conditional governance gives you the right *response time* when the correlation break actually happens.

---

## 8. Concrete recommendations

**Add tail-aware gates to `risk_manager_v5.py`:**

1. **GPD-derived tail VaR.** Compute rolling 99.5% Expected Shortfall using POT-fitted GPD on trailing 252 daily returns. Alert if ES exceeds -2% daily; halt new VRP entries.
2. **Per-sleeve max-loss-per-day.** No sleeve can lose more than 2% of total account in a single day. For a $10k account, that's -$200 per sleeve per day. Liquidate the sleeve if breached.
3. **Per-sleeve max-loss-per-quarter.** No sleeve can lose more than 5% of account per quarter. Hard stop with manual re-arm required.
4. **Gap-risk monitor for VRP.** Track (short-strike − spot) / spot. If gap narrows below 5% (deep in-the-money risk), close the spread regardless of P&L.
5. **Realized vol regime gate.** If trailing 20-day realized vol is above the 60th percentile of its 5-year distribution, halt new VRP entries. The IV-mean-reversion edge that makes VRP work assumes elevated IV; in elevated *realized* vol regimes, IV is *more* likely to expand than contract.
6. **Drawdown-contingent allocation cut.** If 63-day max-DD exceeds -4%, freeze VRP at current notional, no new entries until DD recovers to -2%.

**Tighten VRP sleeve sizing:**

The v5 proposal calls for 15% allocation. Under fat-tailed tail-VaR (Section 4) and ergodicity (Section 6), the right answer is **10–12% baseline, scaling to 5–7% when realized vol is in the upper half of its 5-year distribution**. Add a regime tilt to the allocation rule:

```
if realized_vol_20d_percentile > 60:
    vrp_allocation = 7%
elif realized_vol_20d_percentile > 40:
    vrp_allocation = 10%
else:
    vrp_allocation = 12%
```

**Pre-commit drawdown response.** Before LIVE arming, sign the protocol from RISK_FRAMEWORK.md section 6 (yellow at -5%, red at -8%, escalation at -12%, catastrophic at -15%) and the explicit recovery sequence. The four thresholds are pre-committed; no discretion under stress.

**Stress-test under explicit GPD parameters.** Run the v5 scripted scenarios from SCENARIO_LIBRARY.md but with VRP P&L generated from a GPD-sampled tail rather than scenario-script overrides. This is more honest than the script-override approach because it draws from the empirical fat-tailed distribution rather than analyst-imagined magnitudes.

---

## 9. Is this overengineering at $10k?

A reasonable adversarial question. The answer is: **EVT is overkill for the momentum core; it is the right tool for VRP**.

The momentum sleeve has a tail that is fat but not catastrophic — historical worst-case ~-33% over 12 months, well within the kill-switch and behavioral-precommit envelope. Adding GPD analysis doesn't change sizing decisions there.

The VRP sleeve is fundamentally different. The tail risk *defines* the strategy. Sizing it without tail-aware mathematics is sizing it on the strategy's lie about itself — that the 0.5–1.0 Sharpe in calm regimes is the relevant number. It isn't. The relevant number is "what fraction of your account can you afford to lose in a 1-in-50-year event," and that requires GPD or its equivalent.

The implementation cost is small (~10 hours including pyextremes wiring and a tests round). The downside protection is large. Ship it before VRP goes LIVE.

---

## Sources

- Mandelbrot, B., & Hudson, R. L. (2004). [*The Misbehavior of Markets: A Black Swan Approach to Risk, Profit, and Opportunity*](https://www.basicbooks.com/titles/benoit-mandelbrot/the-misbehavior-of-markets/9780465043576/). Basic Books.
- Taleb, N. N. (2007). *The Black Swan*. Random House.
- Taleb, N. N. (2020). [*Statistical Consequences of Fat Tails*](https://arxiv.org/abs/2001.10488). SSRN.
- Cont, R. (2001). "Empirical properties of asset returns: stylized facts and statistical issues." *Quantitative Finance* 1.
- Embrechts, P., Klüppelberg, C., & Mikosch, T. (1997). *Modelling Extremal Events for Insurance and Finance*. Springer.
- Thorp, E. (1962). *Beat the Dealer*. Vintage.
- MacLean, L. C., Thorp, E. O., & Ziemba, W. T. (2011). *The Kelly Capital Growth Investment Criterion*. World Scientific.
- Peters, O., & Gell-Mann, M. (2016). "Evaluating gambles using dynamics." *PNAS*. https://www.pnas.org/doi/10.1073/pnas.1607053113
- [`pyextremes`](https://github.com/georgebv/pyextremes) Python library for EVT.
- [`scipy.stats.genpareto`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.genpareto.html)

---

*Last updated 2026-05-04. Status: PROPOSAL. Tail-aware gates ship in `risk_manager_v5.py` before VRP sleeve goes LIVE.*
