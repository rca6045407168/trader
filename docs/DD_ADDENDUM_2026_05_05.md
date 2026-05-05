# DD Addendum — Corrected leaderboard finding

**Date:** 2026-05-05 (same day, later in session)
**Subject:** Correction to `DUE_DILIGENCE_2026_05_05.md` strategy comparison
**Author:** Same analyst, with better data

---

## What changed

Earlier today's DD compared 10 candidate strategies and concluded that the
production `xs_top15` was mid-pack, with `score_weighted_xs` and `xs_top8`
leading by +44pp / +42pp over 5 years. The recommendation was a phased
production switch.

**That comparison used the wrong baseline.** The harness's `xs_top15` is
*equal-weighted* top-15. The actual production variant
`momentum_top15_mom_weighted_v1` (promoted to LIVE on 2026-04-29 per v3.42)
is *score-weighted with min-shift* — a different scheme.

When the production scheme is added to the harness as `xs_top15_min_shifted`
and the same 5y backfill is re-run, the standings change materially:

| Strategy | Cum Active vs SPY | IR | Note |
|---|---:|---:|---|
| **`xs_top15_min_shifted` (LIVE)** | **+88.35pp** | **2.51** | *production* |
| `score_weighted_xs` | +60.44pp | 2.11 | next-best alt |
| `xs_top8` | +58.73pp | 1.88 | concentrated |
| `xs_top15` (equal-weight) | +16.60pp | 0.67 | wrong-baseline |
| `xs_top15_capped` | +14.13pp | 0.54 | |
| `dual_momentum` | +13.36pp | 0.52 | |
| `xs_top25` | +8.16pp | 0.20 | |
| `vertical_winner` | +1.10pp | -0.17 | failed cross-regime |
| `inv_vol_xs` | -1.93pp | -0.33 | |
| `sector_rotation_top3` | -8.08pp | -0.48 | |
| `equal_weight_universe` | -11.86pp | -2.16 | |

**The production strategy is the leader**, by 28pp over the next-best alternative.

## Why the production scheme wins

The two score-weighting schemes differ in negative-momentum handling:

- `score_weighted_xs` (my proposal): `weight ∝ max(score, 0)`. Drops names
  whose 12-1 momentum is negative; redistributes weight to positive-score
  names.
- `xs_top15_min_shifted` (production): `weight ∝ (score − min(score) + 0.01)`.
  Keeps all 15 names with a small floor weight even when scores are negative.

In bear regimes (2022 reversal, COVID drawdown), most names have negative
trailing 12-1 returns. My scheme concentrates into a smaller pool of
positive-score names — typically defensive sectors at that phase. The
production scheme keeps the diversification of all 15 picks. Empirically
the production approach wins because:

1. The "positive-score names in a bear" set is small and crowded; concentration
   there pays a crowding tax.
2. The "all 15 names in a bear" set retains exposure to names that recover
   first when the regime flips. The slight edge in capturing the recovery
   compounds across multiple regime cycles.
3. The min-shift's `+0.01` floor ensures no name is fully exited, which means
   no transaction cost is paid to re-enter when momentum flips back.

## Updated recommendation

**Do not switch the production strategy.** The leader of the 11-way comparison
is what's already deployed. Switching would cost realized capital while moving
to a worse strategy.

The recommendation that follows from the corrected data is different:

1. **Keep `momentum_top15_mom_weighted_v1` LIVE.**
2. **Build new candidate strategies *worth comparing against this leader***. The
   bar is now +88pp / IR 2.51 over 5 years — that is high. Most "obvious
   improvements" will fail to clear it.
3. **Universe expansion** is the highest-leverage next test: does the LIVE
   variant maintain its edge on a wider universe (S&P 500 vs the curated 50)?
   If yes, it's robust. If not, the edge may be partially universe-curation
   alpha rather than weighting alpha.
4. **Pair-trade / short ballast** is the structural alpha the long-only book
   can't produce. Round-2 punted it; the corrected data says it's still the
   most credible path to *additional* IR above the LIVE variant's already-
   strong baseline.

## What this changes in the v3.73.4 DD

The DD recommended (Tier 1, item 6) "8% single-name cap at score-to-weight
conversion." The cap was shipped in v3.73.5 and is non-binding on the LIVE
variant at top-15 / 80% gross. The cap doesn't help the LIVE strategy;
it only helps the (worse) equal-weight variants in the eval harness.

The DD's other recommendations — measurement infrastructure, operational
fixes — all remain correct and have shipped.

## Why I missed this in the first DD

I conflated "the production strategy" with "what the docstring of `rank_momentum`
implies": equal-weight top-N. The production strategy actually goes through
the variant registry (`ab.py` + `variants.py`), and the LIVE variant happens
to be score-weighted. I should have read the variant registry before writing
the DD's strategy section.

Lesson: when reviewing the strategy stack, **always check the variant registry
for the LIVE variant's actual implementation**, not the canonical
`rank_momentum` function. The variant fn is what runs.

---

*This addendum supersedes the `DUE_DILIGENCE_2026_05_05.md` strategy
comparison section. Operational + measurement findings stand.*
