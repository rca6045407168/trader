# Recovery Rule Test — Does It Fix the GFC Whipsaw?

**Date:** 2026-05-07
**Subject:** Empirical test of the v3.73.22 recovery-aware momentum candidate
**Verdict:** Helps in moderate-vol regimes, does NOT fix the GFC specifically.
The strategy still has a documented GFC weakness; the recovery rule
addresses a related but distinct failure mode.

## The hypothesis

Per the v3.73.21 GFC postmortem, the failure mode was: 12-1 momentum lagged
the 2009 Q1 recovery rally because the signal still pointed at defensives
(WMT, NFLX, MCD) that had won by losing-less, while high-beta names (AMD,
AMZN, BAC) were leading the bounce.

The hypothesis: a VIX-compression-after-panic signal could detect the
recovery moment. When `(current_vix < 25) AND (max_vix_30d > 35)` is true,
switch from 12-1 momentum to 6-1 momentum for that rebalance — forcing
faster rotation into the names that just bounced.

## The implementation

`xs_top15_recovery_aware` candidate in `src/trader/eval_strategies.py`.
Same picks + min-shifted weights as production, but with conditional
6-month vs 12-month lookback based on the recovery signal.

## The test — per-regime active return vs SPY

| Regime | LIVE active | Recovery-aware active | Δ |
|---|---:|---:|---:|
| Dot-com 2001-2003 | (similar) | (similar) | ~0 |
| **GFC 2007-2010** | **+13.1%** | **+13.1%** | **0.0pp** |
| **Recovery 2009 Q1-Q2** | **-27.5%** | **-27.5%** | **0.0pp** |
| **COVID 2020** | **+27.4%** | **+24.7%** | **-2.7pp** |
| **Post-COVID 2021-2026** | **-2.4%** | **+16.3%** | **+18.7pp** ← real |

(Numbers reflect the full 2001-2026 long-window backtest with VIX added
to the price panel; some figures differ slightly from the prior
postmortem because the ETF presence shifts the cross-validation slightly.)

## Why the recovery rule doesn't fix the GFC

**VIX during 2009 Q1 was 35-45 throughout** — the recovery threshold
(VIX < 25) was never met until July 2009, by which time the worst of the
whipsaw was already past.

VIX history during the 2009 recovery:
- Mar 2009: 41-49 (during the bottom)
- Apr 2009: 35-45
- May 2009: 26-37 (briefly touched the threshold neighborhood)
- Jun 2009: 25-32
- Jul 2009: 23-30 (first sustained VIX < 25)

The 2009 recovery happened **with elevated VIX**. The rule's "compression
after panic" assumption (VIX collapses below 25) was too strict for the
specific GFC episode. The S&P rallied 30%+ off the March bottom while
VIX was still in the 30-40 range.

## Why the recovery rule helps post-COVID

The post-COVID period (2021-2026) had several distinct vol-spike-then-
compression episodes (2022 reversal in particular) where VIX briefly
crossed 35 then dropped below 25 within 30 days. The 6-1 momentum
during those compression windows captured names rotating into the
recovery while 12-1 still pointed at the prior leaders.

**+18.7pp of cum-active over 5 years is substantial.** This is the
clearest evidence we have that a recovery-aware rule has empirical
value, even though it doesn't fix the GFC specifically.

## What this means

Three honest readings:

1. **The recovery rule is a real candidate with empirical support** in
   the post-COVID regime. It can stay in the eval harness and ship as
   a SHADOW variant for in-prod comparison.

2. **The GFC weakness remains structurally unresolved.** The recovery
   rule addresses a different (more common, less severe) failure mode.
   GFC-style multi-month elevated-VIX recoveries are not caught by
   VIX-compression-based rules.

3. **A more aggressive rule** — e.g., "switch to 6-1 momentum any time
   trailing 3-month equity drawdown exceeds 15%" — would catch the GFC
   whipsaw. But it would also fire on every minor correction, costing
   alpha in normal regimes. The tradeoff requires careful empirical
   tuning that has not been done.

## Recommendation

Keep `xs_top15_recovery_aware` as a SHADOW candidate. Do NOT promote it
to LIVE on the strength of the GFC postmortem alone — the postmortem's
intended fix doesn't address the postmortem's diagnosis. The +18.7pp
post-COVID gain is the actual case for the rule, and that needs more
out-of-sample validation.

The GFC weakness should remain documented as **accepted** for now. The
strategy's 25-year cumulative beat (+5,372% vs +953%) absorbs the
2009 Q1-Q2 cost. Operators who can't psychologically tolerate -8pp/month
underperformance during a recovery should not deploy meaningful capital
until a tested fix exists.

## Status

- Recovery rule SHIPPED as candidate `xs_top15_recovery_aware`
- Result: helps post-COVID (+18.7pp), does NOT fix GFC (0.0pp)
- GFC weakness remains an explicit documented limitation
- Further rule iteration (different threshold, different signal) is
  open work — not a 1-hour task
