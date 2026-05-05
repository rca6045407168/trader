# Information Theory of Alpha

*Methodology reframe. Synthesized from a Round-2 advisory swarm (Bayesian / information-theoretic lens, May 2026). Companion to TESTING_PRACTICES.md, V5_ALPHA_DISCOVERY_PROPOSAL.md. Status: research note — informs how v5 sleeves are pre-screened and how alpha-decay is monitored.*

---

## The reframe

Sharpe is a downstream metric. The upstream question — what governs whether *any* implementation of a signal can produce alpha — is informational: how much does the signal tell you about future returns?

Formally, the mutual information between signal $S$ and forward return $R$ is

$$I(S; R) = \sum_{s, r} p(s, r) \log \frac{p(s, r)}{p(s) p(r)}$$

measured in bits or nats. A signal with $I(S; R) = 0$ contains zero predictive content — no Sharpe, no information ratio, no edge can be extracted regardless of cleverness in implementation. A signal with $I(S; R) = 0.01$ nats may yield a Sharpe of +1.2 or +0.3 depending on transaction costs, position sizing, regime, and execution discipline. Mutual information sets the upper bound; Sharpe is what you actually capture.

This matters for v5 because your current methodology is a frequentist apparatus designed to detect overfitting (CPCV, PBO, Deflated Sharpe). It works — your v3.25 audit crushed eight shadow variants whose +1.6 survivor Sharpes collapsed to negative on PIT. But the apparatus answers "does this edge persist," not "should this edge exist in the first place." Information theory inverts the question. Before you backtest 50 momentum variants, ask which ones plausibly carry independent mutual information.

---

## 1. Estimating mutual information from finite samples

Three practical estimators, in increasing order of sophistication and risk:

**Fixed-bin (histogram) discretization.** Divide signal and return into bins, compute empirical $I$ from joint counts. Bias is severe — discretization loses information; expect $I$ underestimates of 10–30%. Variance is low. Implementation is one-liner in NumPy/Pandas. Use for quick sanity checks: "is binary $I(\text{sign of momentum}; \text{sign of return})$ above noise floor (~0.001 nats)?" If no, the signal is dead and no sophisticated estimator will resurrect it.

**Kraskov-Stögbauer-Grassberger (KSG, 2004).** Uses k-nearest-neighbor distances in joint $(S, R)$ space; non-parametric, no binning. Bias underestimates $I$ in high dimensions but is well-behaved at $d \leq 5$. Variance scales reasonably with sample size. Implementation: 50 lines via [scikit-learn `mutual_info_regression`](https://scikit-learn.org/stable/modules/generated/sklearn.feature_selection.mutual_info_regression.html) or standalone. Use for the canonical applications: $I(\text{12-month momentum}; \text{next-month return})$, $I(\text{earnings surprise}; \text{60-day forward}\,)$.

**Mutual Information Neural Estimator (MINE, Belghazi et al. 2018).** Trains a neural network to maximize a lower bound on $I$ via the InfoNCE loss. Asymptotically unbiased; tightest of the three estimators. Variance is high in small samples and the network itself can overfit. Use only for high-dimensional signal blends (15+ features) where KSG's bias becomes prohibitive — and only after understanding that you've now introduced a second model whose own generalization gap could mislead you.

**Practical guidance for v5.** Audit each new sleeve with a fixed-bin pass first. Anything below 0.001 nats binary-MI to the canonical forward-return horizon is dead on arrival; kill before backtest. Confirm survivors with KSG on continuous signal-return pairs. Reserve MINE for the ML-PEAD sleeve specifically, where the signal is naturally multi-dimensional. The discipline: estimate MI *before* committing CPCV cycles, not after.

Citation: Cover & Thomas, *Elements of Information Theory* (1991), chapters 2 and 8 for the foundations. Kraskov, Stögbauer, & Grassberger, "Estimating Mutual Information," *Physical Review E* 69 (2004). Belghazi et al., "Mutual Information Neural Estimation," *ICML* (2018).

---

## 2. Bayesian promotion with the McLean-Pontiff prior

Frequentist gates ask "does this beat the null." Bayesian promotion asks "given the evidence and what we know about the population of trading anomalies, what's the posterior probability of genuine alpha."

The relevant prior comes from McLean & Pontiff (2016). Across 97 published anomalies, post-publication returns decay to roughly 58% of the published in-sample magnitude. This is the factor-zoo prior — the distribution from which your candidate signals are drawn.

Apply it to v3.20 (the killed quality-momentum blend):

**Step 1 — Backtest evidence.** Survivor Sharpe +0.81 across 5 regimes; CPCV sub-window standard error roughly 0.30.

**Step 2 — Likelihood.** Backtest Sharpe is normally distributed around the true Sharpe with standard error from sub-window variance. $p(\text{observed} = 0.81 \mid \theta)$ is Gaussian with $\sigma = 0.30$.

**Step 3 — Prior.** Quality-momentum is a published-anomaly flavor (Asness QMJ + Frazzini-Pedersen BAB are the relevant literature). Apply McLean-Pontiff: prior mean $= 0.81 \times 0.58 = 0.47$, with prior standard deviation reflecting uncertainty in the decay parameter (~0.20 across the McLean-Pontiff sample).

**Step 4 — Posterior.** Standard Bayesian normal-normal update yields posterior mean ≈ +0.43 with standard deviation ≈ 0.17. Your LIVE baseline is +0.95. Even the upper-95% posterior bound (+0.77) is below LIVE. Kill justified, with explicit numerical posterior — much stronger than the frequentist "PIT collapsed it."

For unpublished signals (VRP structural premium, FOMC drift behavioral edge), the prior is different. Use a flatter prior centered near zero with higher variance. The backtest evidence carries more weight because you're not fighting publication-decay. VRP at survivor +0.6 Sharpe with unpublished-edge prior centered at 0 (σ = 0.30) yields posterior ≈ +0.40 — materially higher than the published-anomaly framework would allow.

This is the methodologically correct answer to "should I trust this backtest." It explicitly encodes the prior knowledge that academic anomalies decay, while leaving room for genuinely novel structural premia. Citation: Harvey, Liu, & Zhu, "...and the Cross-Section of Expected Returns" (*RFS* 2016) — the comprehensive treatment of the multiple-testing problem in factor research.

---

## 3. Information-theoretic kill criteria

Three complementary gates to layer on top of CPCV + DSR + PBO:

**Gate A — MI-to-Sharpe efficiency ratio.** Measure $I(S; R)$ on the backtest sample. Compute the realized Sharpe. The ratio $\text{Sharpe} / I(S; R)$ is implementation efficiency: how much theoretical signal does your strategy actually capture through position sizing and execution. Empirical typical ratios:

- Cross-sectional momentum, well-implemented: 2–5
- Naive sentiment / NLP signals: 1–3
- Highly tuned ML strategies: 5–10

Ratios above 10 are red flags. Either the backtest is overfit (Sharpe inflated artificially), or the MI estimator is biased downward (KSG bias in high dimensions). Either way, treat as a signal that something in the methodology is broken.

Apply retroactively: your v3.16 shadow ("best ever +1.61 Sharpe") almost certainly had $I < 0.10$ nats, implying ratio > 16. Immediate red flag if you'd had this gate. PIT validation eventually killed it; the MI gate would have killed it cheaper.

**Gate B — Minimum Description Length.** A strategy requiring 47 parameters (dynamic allocation rules, regime flags, rolling lookback windows, leverage curves) encodes more bits than a strategy requiring 3 (universe, momentum lag, rebalance frequency). The Minimum Description Length principle (Rissanen 1978) says

$$\text{MDL} = L(\text{model}) + L(\text{data} \mid \text{model})$$

where the first term is the bits needed to describe the strategy and the second is the residual error compressed. Models with lower MDL win.

For v5: top-3 aggressive (v3.1–v3.41) had ~8 parameters; top-15 mom-weighted (v3.42) has ~6. Both achieve similar PIT Sharpe (+0.95 to +0.98). MDL prefers the simpler. You arrived at this via "reduce idiosyncratic noise" intuition; MDL formalizes it. Apply MDL when comparing v5 candidate sleeves: prefer the smaller-parameter version when Sharpe estimates are statistically indistinguishable.

**Gate C — Half-life of edge decay.** Track $I(S; R)$ in rolling 90-day windows. Does it decay exponentially (typical for published behavioral anomalies, half-life ~12–18 months) or remain flat (structural premium)?

- Published momentum: half-life ~12–18 months post-publication.
- VRP structural premium: half-life > 36 months because the underlying institutional hedging-demand is structurally persistent.
- Behavioral signals (FOMC drift, PEAD): half-life ~6–12 months once arbitrageurs find them.

Kill rule: if rolling-90-day MI half-life drops below 3 months, the edge is being arbitraged in real-time at speed retail can't match. Kill the sleeve regardless of current Sharpe — you're racing a clock you'll lose.

This is the missing gate in your current methodology: CPCV + PBO + Deflated Sharpe all measure cross-sectional-and-temporal robustness *at a point in time*; none of them detect that an edge is decaying. The Tiger Global / GLG / ARKK case studies in FUND_FAILURE_CASE_STUDIES.md are precisely "strategy that passed every static test, then decayed." MI half-life tracking catches that pattern early.

---

## 4. The 50-variant problem, reframed

You've tested ~50 momentum variants across `iterate_v3` through `iterate_v14`. White's Reality Check (2000) and Hansen's Superior Predictive Ability test (2005) say the family-wise error correction at $\alpha = 0.05$ across 50 independent tests is Bonferroni $\alpha_{\text{adj}} = 0.001$ — only variants beating null by 3+ standard deviations survive.

Information theory clarifies why this isn't quite right. Your 50 variants are *not* independent. They share roughly 90% of the mutual information with the same forward-return series — they're 50 noisy views of the same underlying ~0.15-nat momentum signal, not 50 independent hypothesis tests. The effective number of independent tests is closer to **3–5**, the number of structurally-distinct primitives across the variant family.

Under the corrected effective-test-count, the Bonferroni cutoff loosens to $\alpha_{\text{eff}} = 0.05 / 4 \approx 0.0125$ — much more permissive than the naive 0.001. This is what the SPA test computes formally; you can approximate it cheaply by computing pairwise MI across the variant cohort and reducing the effective count by the average correlation.

**Practical lesson for v5.** Adding three new sleeves (VRP, FOMC, ML-PEAD) increases the effective test count *only if* the new sleeves carry independent mutual information from the momentum core. Pre-flight check: compute $I(\text{VRP signal}; \text{momentum signal})$, $I(\text{FOMC}; \text{momentum})$, $I(\text{PEAD}; \text{momentum})$. If pairwise MI < 0.3, the sleeves are usefully independent and stacking is information-theoretically justified. If two sleeves share more than 0.5, they're effectively the same test and stacking compounds the multiple-testing curse rather than reducing it.

This is the formal version of "are these sleeves actually uncorrelated." The correlation-of-returns metric in RISK_FRAMEWORK.md is a noisy proxy for this; mutual information of the underlying signals is the cleaner version. Compute both.

---

## 5. The retail-specific insight — effective MI vs. raw MI

Institutional quants obsess over $I(S; R)$ magnitude. Retail-scale operators should obsess over $I(S; R)$ *minus friction MI*.

The math: a $50M institutional momentum fund taking a $5M position in NVDA captures roughly 80% of the theoretical signal after slippage. A $10k retail account taking a $1.4k position (top-3 momentum at 80% gross) incurs:

- Bid-ask spread: 0.5–1 bp per round trip = 0.01–0.02% per rebalance
- Market impact: negligible for limit orders; ~0.05% for market-on-open
- Cumulative monthly slippage at monthly rebalance cadence: ~0.10–0.20%

Annualized, that's 1.2–2.4% drag. If the signal's edge is +1.5%/month, friction eats 8–16% of the theoretical edge. Realized retail Sharpe is 60–90% of backtested Sharpe.

Institutions don't see this proportionally — $5M slippage on $500M AUM is 0.1%. **Retail effective MI must be modeled as $I_{\text{eff}}(S; R) = I(S; R) - I(\text{friction}; R)$.** A signal with $I = 0.20$ nats and $I_{\text{friction}} = 0.05$ nats has effective MI = 0.15 nats for retail (but 0.20 for institutions running the same strategy at scale).

The actionable implication for v3.42 → v5: top-15 mom-weighted has lower single-name concentration (~10–14% top, ~1–2% bottom) than top-3 aggressive (~27% per name). Single-name slippage is bounded by position size. Smaller positions hit less friction per dollar. Effective MI is materially higher for top-15 even though *raw* MI of the signal is the same. This is the information-theoretic justification for the v3.42 promotion.

The same logic extends to v5: VRP options have wider bid-ask in absolute terms but smaller friction *as a percentage of edge* if the underlying premium is large enough. Calibrate `slippage_sensitivity.py` accordingly — it's not a separate test from MI analysis; it's the same analysis from a different direction.

---

## 6. What MI tells you that CPCV doesn't

Your existing methodology covers a lot of ground. Where does information theory add coverage:

**CPCV** measures generalization gap — does in-sample edge persist out-of-sample. In MI language, CPCV detects when $I(S_{\text{train}}; R_{\text{train}}) \gg I(S_{\text{train}}; R_{\text{OOS}})$. Catches overfitting.

**Deflated Sharpe / PBO** correct for multiple-testing inflation in the maximum order statistic. In MI language, they're penalizing inflated effective dimensionality from variant-cohort testing.

**PIT validation** detects feature-selection bias by comparing performance on survivor-universe vs honest-universe. In MI language, $I(S_{\text{survivor}}; R_{\text{survivor}}) \gg I(S_{\text{survivor}}; R_{\text{all}})$ flags forward-looking bias in universe construction.

**The blindspot all three miss: structural decay in MI over time.** Your `weekly_degradation_check` monitors live drift in *realized* returns; it doesn't measure MI decay in the underlying signal-return relationship. If momentum's MI is halving every 6 months post-publication, your v3.42 PIT-Sharpe of +0.95 will mechanically decay to +0.5–0.6 by 2027–2028. The frequentist gates won't see this until the realized Sharpe drops, which lags the underlying signal decay by months.

**Add an MI-tracking gate to weekly degradation check.** Compute $I(S_t; R_t)$ on rolling 90-day windows for each LIVE sleeve. If median MI decay rate exceeds 50% per year over a 24-month horizon, escalate to deprecation review. This isn't a hard kill — some structural decay is priced into the McLean-Pontiff prior — but it's an early-warning that complements the realized-performance drift detection.

---

## 7. What this means for v5 specifically

Three concrete changes informed by the information-theoretic frame:

**Pre-screen v5 sleeves with an MI gate before CPCV.** For each candidate signal, compute fixed-bin MI to forward returns at the strategy's natural horizon. Gate: $I_{\text{binary}} > 0.001$ nats. Anything below is dead; don't waste CPCV cycles. Cost to implement: ~4 hours (one-pass NumPy script applied to each sleeve's signal-and-return pair).

**Compute pairwise MI across the four sleeves.** Confirm $I(\text{momentum}; \text{VRP}) < 0.3$, $I(\text{momentum}; \text{FOMC}) < 0.3$, $I(\text{momentum}; \text{PEAD}) < 0.3$, and the cross-sleeve pairs similarly. If any pair exceeds 0.5, the sleeves are effectively the same test and the multi-sleeve diversification claim is illusory. Cost: ~6 hours.

**Track MI half-life on each LIVE sleeve weekly.** Add to `weekly_degradation_check`: compute rolling 90-day $I(S; R)$ for each sleeve, fit exponential decay, alert if half-life drops below 3 months for any sleeve. Cost: ~6 hours.

Total: ~16 hours of incremental work on top of v5's existing methodology budget. Modest cost; meaningful incremental coverage on the alpha-decay blindspot.

---

## 8. The honest verdict

Information theory does not replace your existing CPCV + DSR + PBO framework. It **clarifies what those gates protect against and supplies vocabulary for discussing signal quality upstream of Sharpe optimization**. For your system at the v3.42 → v5 transition, the most actionable conclusion is that the existing methodology is information-theoretically sound — survivor stress checks MI persistence across regimes, PIT validation checks MI robustness across universes, CPCV checks MI generalization. You arrived here via pragmatism; the math says you're right.

The incremental contribution is decay tracking. Watch the bits, not just the Sharpe. If momentum's MI is halving annually post-2026, you want to know that in 2027 — not when realized Sharpe collapses in 2028.

---

## Sources

- Cover, T. M., & Thomas, J. A. (1991). *Elements of Information Theory*. Wiley.
- Shannon, C. E. (1948). "A Mathematical Theory of Communication." *Bell System Technical Journal*.
- Kelly, J. L. (1956). "A New Interpretation of Information Rate." *Bell System Technical Journal*, 35(4).
- Kraskov, A., Stögbauer, H., & Grassberger, P. (2004). "Estimating Mutual Information." *Physical Review E*, 69.
- Belghazi, M. I., et al. (2018). "Mutual Information Neural Estimation." *ICML*.
- Rissanen, J. (1978). "Modeling by Shortest Data Description." *Automatica*, 14.
- McLean, R. D., & Pontiff, J. (2016). "Does Academic Research Destroy Stock Return Predictability?" *Journal of Finance*, 71(1).
- Harvey, C. R., Liu, Y., & Zhu, H. (2016). "...and the Cross-Section of Expected Returns." *Review of Financial Studies*, 29(1).
- Hansen, P. R. (2005). "A Test for Superior Predictive Ability." *Journal of Business & Economic Statistics*, 23(4).
- White, H. (2000). "A Reality Check for Data Snooping." *Econometrica*, 68(5).
- López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley.
- [scikit-learn `mutual_info_regression`](https://scikit-learn.org/stable/modules/generated/sklearn.feature_selection.mutual_info_regression.html)

---

*Last updated 2026-05-04. Status: research note. Three concrete v5 changes in section 7 are actionable inputs to the build sequence.*
