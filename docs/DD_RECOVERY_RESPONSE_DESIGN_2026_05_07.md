# Recovery Response Design — GFC Test

**Date:** 2026-05-07  
**Goal:** the v3.73.24 dd-recovery DETECTOR fires correctly during GFC but the 6-1 momentum RESPONSE degrades P&L. This work tests three alternative responses to the SAME detector signal.

## Detector (unchanged from v3.73.24)

```
recovery_active = (SPY_180d_DD < -25%) AND (SPY_1m_return > +5%)
```

## Three response candidates

| Code | Response | Description |
|---|---|---|
| A | Defensive tilt | Restrict to ConsumerStap + Healthcare; top-15 by 12-1 momentum among defensives, 80% gross |
| B | Reduced gross | Keep 12-1 picks, cut gross 80% → 40% |
| C | Equal-weight | Drop min-shift, equal-weight top-15 at 80% |

## GFC results (2008-09 → 2010-12, 28 months)

| Strategy | Cum return | Max DD | Recovery fires |
|---|---:|---:|---:|
| production (12-1, control) | +2.45% | -25.37% | n/a |
| A: defensive tilt | +1.87% | -24.74% | 4 |
| B: reduced gross | +3.61% | -22.59% | 4 |
| C: equal-weight | +3.33% | -25.87% | 4 |

## Delta vs production

- Response A (defensive): **-0.58pp**
- Response B (reduced gross): **+1.15pp**
- Response C (equal-weight): **+0.88pp**

## Verdict: ✅ B: reduced gross improves GFC P&L

Best response: **B: reduced gross** with delta +1.15pp vs production over the GFC window. Max DD -22.59% (production -25.37%). Worth promoting to a shadow-mode strategy candidate in the eval harness.

**Caveat:** this is single-window evidence over 28 months. Before any production swap, would also need to verify the response doesn't break normal-regime returns (the detector fires only 4 times in 25y, so the response barely runs outside crisis windows — but worth confirming with a full-window backtest).

## Full-window 25y confirmation

The detector fires only 4 times across 25 years (all in GFC). Outside those months, response B is identical to production. So the full-window result should be very close to production (a tiny boost from the GFC delta).

| Metric | production | response B | delta |
|---|---:|---:|---:|
| 25y cum return (×) | 57.2460 | 57.8911 | +0.6451 |
| 25y max DD | -38.50% | -36.21% | +2.29pp |
| Detector fires across 25y | n/a | 4 | n/a |

**Normal-regime returns preserved**: response B does not degrade 25-year cum return or max DD beyond noise. Safe to promote to a SHADOW-mode candidate in the eval harness.
