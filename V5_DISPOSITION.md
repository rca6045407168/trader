# v5.0.0 disposition — multi-strategy auto-routed, capital-deployable

**Date:** 2026-05-08
**Replaces:** v4.0.0 freeze + v4.1.0 sunset
**Status:** Active

The v4.0.0 freeze and the v4.1.0 sunset were the right exercises but the
wrong destinations. Walking the project through path C let two questions
get asked that the original framing didn't permit:

1. **Why does the LIVE slot have to be one frozen strategy?** The
   apparatus is multi-tenant by design — variant registry, eval harness
   tracking 28 candidates, β-adjusted leaderboard, SHADOW→LIVE promotion
   path. The disposition's failure was not that the apparatus was
   useless; it was that *the operator never executed the swap the data
   already justified.* Solution: make the apparatus self-acting.

2. **Why is paper the only acceptable surface?** Tiny live capital was
   defensible weeks before the sunset; the only blocker was a brokerage
   account that didn't exist yet. Public.com plumbing test is now
   imminent.

This disposition reactivates the project under a different shape.

---

## Core changes from v4.0.0/v4.1.0

| | v4.0.0 (frozen) | v5.0.0 (this disposition) |
|---|---|---|
| Capital | paper only | tiny live → graduated |
| Strategy slot | one frozen LIVE | auto-routed, rolling-IR winner |
| Development | stop-rule | active under hard guardrails |
| Discipline mechanism | "no commits" | exit criteria + β cap + ENFORCING DD + capital cap |
| Failure trigger | bypasses → C | metric breach → C |
| Doc framing | freeze monument | terse decision logs |

---

## §1 The auto-router

`src/trader/auto_router.py` (new). On every rebalance, it reads
`strategy_eval` and chooses which registered candidate occupies the LIVE
slot for that rebalance. Replaces the hardcoded `register_variant(...
status="live")` pattern from variants.py.

### Selection rule

For each candidate strategy `s`:

1. **Eligibility filter:**
   - `s` is in the eligible-candidate set (see §1.1)
   - `s` has at least `MIN_EVIDENCE_MONTHS = 6` months of settled
     forward returns in `strategy_eval`
   - `s`'s realized β over the eligibility window ≤ `MAX_BETA = 1.20`
   - `s`'s max-drawdown over the eligibility window ≥ `MIN_DD = -25%`

2. **Score:** rolling 6-month annualized α-IR computed from
   `strategy_eval.cum_alpha_pct` and `strategy_eval.alpha_ir_pct`. Use
   the existing `eval_runner.compute_alpha_metrics()`.

3. **Pick:** the eligible strategy with the highest rolling α-IR.

4. **Hysteresis:** if the previous LIVE strategy is still eligible and
   within `HYSTERESIS_MARGIN = 0.10` IR points of the current winner,
   keep it. Avoids monthly thrashing on noise.

5. **Fallback:** if no candidate is eligible (e.g., insufficient
   evidence after a long halt), the orchestrator HALTs with a
   `"no LIVE candidate eligible"` reason. Operator must investigate.

### §1.1 Eligible candidate set

Of the 28 registered strategies in `eval_strategies.py`, exclude:

- **`long_short_momentum`** — has no short-cost modeling; numbers are
  systematically optimistic. Disqualify until short-cost is wired.
- **All `buy_and_hold_*` and passive baselines** (spy/qqq/mtum/schg/vug/
  xlk/equal_weight_sp500/boglehead_three_fund/simple_60_40) — these are
  reference benchmarks, not candidates for the LIVE active-trading slot.
  An operator who wants to buy SPY can just buy SPY.

That leaves 17 active candidates eligible:

```
xs_top15, xs_top15_capped, xs_top15_min_shifted, xs_top8, xs_top25,
score_weighted_xs, inv_vol_xs, dual_momentum, sector_rotation_top3,
equal_weight_universe, vertical_winner, naive_top15_12mo_return,
xs_top15_vol_targeted, score_weighted_vol_parity,
xs_top15_reactor_trimmed, xs_top15_recovery_aware,
xs_top15_dd_recovery_aware, xs_top15_dd_recovery_reduced_gross
```

Including the over-fit recovery-aware variants is intentional: the
auto-router's evidence threshold + hysteresis will reject them
naturally if they don't perform forward, regardless of whether they
were fitted on the historical panel. Better to let the data eject
than to pre-eject and risk being wrong.

### §1.2 Parameter rationale (defended individually)

- **`MIN_EVIDENCE_MONTHS = 6`** — the eval harness has been settling
  forward returns since v3.73.7. 6 months of monthly settled returns
  gives ~6 data points for IR estimation; statistically thin but not
  zero. Below 6, IR estimates are noise.
- **`MAX_BETA = 1.20`** — directly addresses the β=1.7 finding from §2
  of ARCHITECTURE.md. Caps inherited-tech tilt at 1.2× SPY exposure.
  Strategies that systematically pick high-β names are excluded from
  LIVE candidacy until the β profile changes.
- **`MIN_DD = -25%`** — anything that's ridden through a 25%+ drawdown
  in its own forward-eval window has shown a failure mode worth
  observing before promoting to live capital.
- **`HYSTERESIS_MARGIN = 0.10` IR points** — empirical pick from prior
  observed leaderboard noise. Less and we'd thrash; more and the data
  has to scream before the LIVE slot moves.

These four parameters are the things requiring discipline. They are
named here so future-me can challenge them with evidence rather than
re-derive them under pressure.

---

## §2 Capital ladder

| Tier | Capital | Trigger to advance |
|---|---|---|
| 0 | $0 (paper) | Auto-router runs cleanly for 30 days; at least 2 LIVE-strategy swaps observed without halt; reconciliation drift never fires. |
| 1 | $1-2k (Public.com plumbing) | Public.com account funded. 30 days at Tier 0 clean. Tier 1 measures: real fills, real slippage, real T+1 settlement. |
| 2 | $5-10k (learning) | Tier 1 ran 60 days clean. Realized slippage within ±20% of paper assumption. No reconciliation halts. |
| 3 | $25-50k (meaningful) | Tier 2 ran 90 days clean. ENFORCING drawdown protocol exercised by at least one real -5% event without intervention. Auto-router has demonstrated at least one mid-flight LIVE swap on real money without operational pain. |
| 4 | $100k+ (material) | Tier 3 ran 180 days clean, including at least one regime change observed. Realized α (β-stripped, post-cost) positive over the Tier-3 period. |

Each tier's "clean" definition is in §3 (exit criteria). Tier 0 is the
default state right now under v5.0.0.

The ladder is not aspirational. **No tier is reached without the prior
tier's evidence requirement being met.** Skipping tiers = automatic
demotion to Tier 0 + sunset review.

---

## §3 Hard exit criteria

These are pre-committed. Triggers are mechanical. No override.

1. **Auto-router can't pick a LIVE.** No candidate clears the eligibility
   filter for 3 consecutive rebalances → daemons halt, operator review
   required before resumption.

2. **Realized DD breach.** Live-equity DD from the highest tier's
   deploy-date watermark exceeds 15% → all capital pulls. Rebuild from
   Tier 0 or sunset.

3. **β-budget breach.** Realized live-book β over a trailing 30-day
   window exceeds 1.5 (vs the 1.2 cap) for two consecutive measurements
   → daemons halt for review.

4. **Cross-validation harness flag.** If `cross_validate_harness.py`
   detects a measurement bug (production code path disagrees with
   backtest code path on the same inputs) → daemons halt until the bug
   is reproduced + fixed. Same discipline as v3.73.13.

5. **Operator absence.** No human review of the daemons' output for 14
   consecutive days → daemons halt automatically. (Implementation:
   heartbeat-style — operator must `touch ~/trader/.alive` weekly. Stale
   marker → halt.) Addresses the §9.3 bus-factor critique that v4.0.0
   named but never solved.

6. **Naive variant beats every other LIVE candidate for 6 consecutive
   months.** This is the v3.x complexity-tax finding playing out
   forward. If the auto-router keeps picking `naive_top15_12mo_return`
   for 6 months running and no other strategy comes close, the answer
   is: stop running active strategies, just run the naive (or buy SPY).

Each of these triggers is in code, not in prose. The auto-router
module owns checks 1, 2, 3. The cross-validation harness owns 4. The
heartbeat owns 5. The auto-router emits a special signal for 6.

---

## §4 What's preserved from v4.0.0

The §2 honesty list in ARCHITECTURE.md is still right, and v5.0.0 does
not pretend to have solved any of those critiques:

- The historical IR comparison (naive 0.60 vs LIVE 0.46) is on a
  survivor-biased panel. v5.0.0's auto-router lets forward returns
  arbitrate on a non-survivor-biased basis (the actual paper book +
  eventual live book are not survivor-curated).
- The β=1.7-vs-0.90 finding remains real. v5.0.0 explicitly addresses
  it via MAX_BETA cap and the realized-β monitor.
- The GFC/COVID losses remain real. The dd_recovery_reduced_gross
  variant is in the candidate set but the auto-router's MIN_EVIDENCE
  threshold will keep it out of LIVE until it earns its place forward.
- The reactor's n=1, contradicted record remains. The reactor stays
  SHADOW. Any reactor variant in the LIVE-candidate set must clear
  MIN_EVIDENCE on forward returns before it sees real capital.

The cross-validation harness, journal replication, source-spot-check
pattern, and stale-data halt all stay. They're the engineering wins
that earn their keep across any disposition framing.

---

## §5 What's killed in v5.0.0

- **Single-LIVE pattern.** `register_variant(..., status="live")` is
  no longer how the LIVE slot is decided. variants.py loses the
  `status="live"` line; the auto-router decides per-rebalance.
- **ADVISORY drawdown protocol.** Flipped to ENFORCING in `.env`.
  Capital is real now (or about to be); the protocol must mutate
  targets, not just warn.
- **The "frozen forever" framing.** Active development resumes under
  the §3 exit criteria. The stop-rule was the wrong guardrail because
  it forbade exactly the development that would have addressed the
  IR finding (the auto-router itself).
- **Doc-as-monument tendency.** This document is intentionally short
  (~250 lines, one page of markdown). Future state changes get terse
  decision logs in `git log`, not new prose architecture documents.
  ARCHITECTURE.md's §11 disposition section gets a one-paragraph
  v5.0.0 entry; the doc otherwise stays.

---

## §6 Reactivation steps

1. Reinstall 4 production daemons:
   - `com.trader.daily-run.plist`
   - `com.trader.daily-heartbeat.plist`
   - `com.trader.earnings-reactor.plist`
   - `com.trader.journal-replicate.plist`

   The 7 apparatus daemons that were unloaded in the sunset stay gone.

2. Flip `DRAWDOWN_PROTOCOL_MODE=ENFORCING` in `.env`.

3. Run `python scripts/run_reconcile.py` to verify the journal still
   matches the broker. The book has been frozen since 2026-05-08 sunset;
   the reconciliation should be clean.

4. Auto-router goes live on the next daily run. First rebalance under
   v5.0.0: most likely picks `naive_top15_12mo_return` based on the
   historical leaderboard, but the eligibility filter will require
   forward evidence and MIN_EVIDENCE_MONTHS may keep it out for the
   first 6 months. During warmup, operator must consciously confirm
   what's running each day.

5. Tag v5.0.0. Push.

---

## §7 What this disposition does not promise

- It does not promise the strategy works. Forward returns will tell us.
- It does not promise no further sunsets. §3's exit criteria are real.
- It does not promise capital is safe. Tier-0 paper is safe; every
  step up the ladder accepts more risk in exchange for more evidence.
- It does not promise the apparatus is enough. If the auto-router picks
  the wrong strategies for 6 months, exit criterion 6 fires and we
  shut down. Again.

The disposition is a frame for honest active development with
mechanical safeguards. Not a guarantee.

---

*Drafted 2026-05-08 the same day v4.1.0 sunset was executed. The
sunset's value was the kill-and-think cycle that produced this frame.
Reversal is intentional and recorded.*
