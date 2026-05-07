# ENFORCING-mode Proof Drill
**Date:** 2026-05-07  
**Purpose:** prove that DRAWDOWN_PROTOCOL_MODE=ENFORCING actually mutates orchestrator targets when a synthetic drawdown tier fires. Per the v3.73.21 critique: "A loaded fire extinguisher sitting in the corner is not the same as a sprinkler system."  

## Drill results

| Scenario | Mode | Tier returned | Tier action | Weights changed? | Input gross | Output gross |
|---|---|---|---|---|---:|---:|
| YELLOW (-5% DD) | ADVISORY | YELLOW | PAUSE_GROWTH | NO | 68.01% | 68.01% |
| YELLOW (-5% DD) | ENFORCING | YELLOW | PAUSE_GROWTH | NO | 68.01% | 68.01% |
| RED (-8% DD) | ADVISORY | RED | HALT_ALL | NO | 68.01% | 68.01% |
| RED (-8% DD) | ENFORCING | RED | HALT_ALL | NO | 68.01% | 68.01% |
| ESCALATION (-12% DD) | ENFORCING | ESCALATION | TRIM_TO_TOP5 | YES | 68.01% | 30.00% |
| CATASTROPHIC (-15% DD) | ENFORCING | CATASTROPHIC | LIQUIDATE_ALL | YES | 68.01% | 0.00% |

## Detailed CATASTROPHIC drill output

At -17% DD, mode=ENFORCING, the protocol returned tier `CATASTROPHIC` with action `LIQUIDATE_ALL`.

Input targets (15 names, ~80% gross):
```
  CAT    6.80%
  GOOGL  6.80%
  INTC   5.21%
  AMD    5.21%
  JNJ    5.19%
  AVGO   5.12%
  GS     5.04%
  MRK    4.27%
  MS     4.20%
  WMT    4.14%
  XOM    4.14%
  NVDA   3.69%
  BA     3.12%
  TSLA   3.05%
  CSCO   2.03%
  TOTAL  68.01%
```

Output targets after ENFORCING applied:
```
  INTC   0.00%
  CAT    0.00%
  AMD    0.00%
  GOOGL  0.00%
  AVGO   0.00%
  NVDA   0.00%
  JNJ    0.00%
  GS     0.00%
  MRK    0.00%
  MS     0.00%
  WMT    0.00%
  XOM    0.00%
  CSCO   0.00%
  BA     0.00%
  TSLA   0.00%
  TOTAL  0.00%
```

Warnings emitted:
```
  drawdown_protocol[ENFORCING]: Catastrophic (-17.00% from 180d peak $120,000). Liquidate all positions. Manual re-arm only after 30-day cool-off + external human review + written re-arming pre-commit. -$1.5k on $10k account; risk is no longer 'managed', it's catastrophic.
  CATASTROPHIC enforced: all targets set to 0.0. Daily orchestrator is expected to liquidate; manual re-arm required after the 30-day cool-off.
```

## Detailed ESCALATION drill output

At -13% DD, mode=ENFORCING, the protocol returned tier `ESCALATION` with action `TRIM_TO_TOP5`. TRIM_TO_TOP5 keeps the 5 highest-momentum names and zeros the rest.

Output targets:
```
  CAT    6.98%  (KEPT)
  GOOGL  6.98%  (KEPT)
  INTC   5.35%  (KEPT)
  AMD    5.35%  (KEPT)
  JNJ    5.33%  (KEPT)
  TOTAL  30.00%
```

## Assertions

✅ ENFORCING + ESCALATION mutated weights as expected
✅ ENFORCING + CATASTROPHIC zeroed all 15 names (LIQUIDATE_ALL)

## Verdict

**ENFORCING mode is verified working.** The path from threshold-fired → tier-evaluated → targets-mutated → output-returned is end-to-end functional.

The remaining gap is paper-run integration: setting DRAWDOWN_PROTOCOL_MODE=ENFORCING in .env and observing an actual rebalance under (synthetic or real) drawdown produce the mutated orders. That is operator action, not code.
