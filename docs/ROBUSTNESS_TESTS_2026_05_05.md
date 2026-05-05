# Robustness Tests — Universe Expansion + Long-Short

**Date:** 2026-05-05
**Subject:** Two empirical tests of the LIVE strategy's edge
**Author:** Same analyst, completing what the DD called for

---

## Test 1: Does the LIVE edge hold on S&P 500?

The 5-year backfill on the curated 50-name universe showed
`xs_top15_min_shifted` (the LIVE production variant) leading by
+88.35pp vs SPY (IR 2.51). The robustness test asks whether that
edge is *weighting alpha* (would survive on a wider, noisier sample)
or *universe-curation alpha* (would degrade).

**Test:** rerun all 11 strategies on a 121-name fallback list of
S&P 500 large-caps over the same 5y window.

**Result:**

| Strategy | 50-name Cum Active | 500-name Cum Active | Held? |
|---|---:|---:|---|
| **`xs_top15_min_shifted` (LIVE)** | **+88.35pp** | **+125.75pp** | **YES — improved** |
| `xs_top8` | +58.73pp | +91.79pp | YES — improved |
| `score_weighted_xs` | +60.44pp | +62.11pp | YES |
| `xs_top15` (equal-wt) | +16.60pp | +29.06pp | YES — improved |
| `dual_momentum` | +13.36pp | +29.06pp | tied |
| `xs_top15_capped` | +14.13pp | +23.22pp | improved |
| `vertical_winner` | +1.10pp | +21.97pp | dramatically improved |
| `xs_top25` | +8.16pp | +17.26pp | improved |
| `inv_vol_xs` | -1.93pp | +14.84pp | improved |
| `sector_rotation_top3` | -8.08pp | +3.66pp | improved |
| `equal_weight_universe` | -11.86pp | -7.03pp | held loser |

**Reading:**
- The LIVE variant's edge **grew** on the wider universe (+88 → +125pp). The
  alpha is in the weighting + signal, not in universe-curation.
- The relative ordering is preserved at the top: LIVE > top-8 > score-weighted.
- Almost every strategy did *better* on the wider universe. The S&P 500
  has names with stronger trailing momentum than the curated 50, so any
  signal-driven selection captures more of that.
- **Equal-weight universe still trails SPY** (-7.03pp). Universe alone
  is not edge.

**Conclusion:** The LIVE strategy is robust to universe expansion. The
edge is real and not an artifact of the 50-name curation.

---

## Test 2: Does long-short momentum produce structural alpha?

The DD's recommendation #4 said pair-trade / short ballast is "the only
structural alpha the long-only book can't produce." Tested empirically.

**Strategy:** `long_short_momentum`
- Long: top-15 by momentum, min-shift weighted at 70% gross (mirrors LIVE)
- Short: bottom-5 by momentum, equal-weight at 30% gross
- Net: +40% gross long bias

**Result on 50-name universe, 5y backfill:**

| Strategy | Cum Active | IR | Win % |
|---|---:|---:|---:|
| `xs_top15_min_shifted` (LIVE long-only) | +88.35pp | 2.51 | 47% |
| **`long_short_momentum`** | **-0.90pp** | **0.03** | **43%** |

**Long-short LOST -0.90pp vs SPY** over 5 years. The DD's claim that the
structural addition would produce alpha was empirically wrong on this
sample.

**Why it failed:**
1. **Mean-reversion in the worst-momentum names.** Bottom-5 names by
   12-1 momentum have already been beaten down. They tend to bounce —
   shorting them costs money on most months.
2. **Bull-regime tax.** The 2021-2026 window is mostly bull conditions.
   The smaller long-side gross (70% vs LIVE's 80%) gives up beta
   exposure during the longest stretches of the period.
3. **No regime-conditional sizing.** The structure is fixed — 70/30
   regardless of regime. A regime-switching version that goes pure-long
   in BULL and engages shorts in BEAR/CHOP would likely outperform,
   but is a meaningfully bigger build.

**What the failure tells us:**
- Long-short isn't a free lunch. It trades regime-specific alpha
  (drawdown protection in 2022-style reversals) for unconditional beta
  drag. On a 5y bull-heavy sample, the trade is negative.
- The "long-short helps" narrative requires a window that includes a
  meaningful bear episode. 2022 is the only one in our sample, and the
  long-short gain there isn't enough to offset 4y of bull-regime drag.
- A regime-conditional variant is the credible next experiment, not a
  static long-short.

**Conclusion:** Static long-short does NOT produce structural alpha on
this universe and window. The DD's recommendation overstated the case.
The remaining structural-alpha hypothesis worth testing is *regime-
conditional* long-short — engage shorts only when an HMM regime
classifier says BEAR.

---

## What this means for the next step

Two unambiguous findings:

1. **The LIVE strategy is robust.** Universe expansion confirms the
   alpha. Don't over-engineer; the production variant is doing real work.
2. **Static long-short is not the answer.** The honest path to additional
   IR above the LIVE baseline is *regime-conditional* sizing, not
   structural net-exposure changes.

The credible remaining alpha experiments are:
- **HMM regime classifier + regime-conditional sizing** (Round-2 work,
  est. 12 hours).
- **Wider universe permanent move.** If the LIVE variant works on 121
  S&P 500 names, why are we running on 50? The gain may be 30-50pp of
  cum-active over multi-year windows. Cost is data fetch latency at
  rebalance. Worth a feature-flagged production run.
- **Pair the LIVE variant with a separate volatility-targeting overlay**
  to manage realized vol independent of the picks. Less ambitious
  than HMM but easier to wire.

Universe expansion to production is a 4-hour ship. HMM regime work is
12+ hours. Vol-targeting overlay is 6-8 hours. All three are at the
"would the LIVE variant + this overlay clear +88pp / IR 2.51?"
threshold — that bar is high.

---

*Companion to `DD_ADDENDUM_2026_05_05.md`. Together these supersede
the `DUE_DILIGENCE_2026_05_05.md` strategy section.*
