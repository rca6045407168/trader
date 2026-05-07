# Drawdown-Based Recovery Rule — GFC Stress Test

**Date:** 2026-05-07  
**Goal:** test whether a drawdown-based recovery detector fixes the GFC weakness where the VIX-based rule (xs_top15_recovery_aware) failed to fire.

## Detector definitions

**VIX-based** (existing v3.73.22 rule):
```
recovery_active = (current_vix < 25) AND (max_vix_30d > 35)
```

**Drawdown-based** (this work, v3.73.24):
```
recovery_active = (SPY_180d_DD < -25%) AND (SPY_1m_return > +5%)
```

Both rules switch from 12-1 to 6-1 momentum when active. Both keep min-shifted weighting + 80% gross.

## GFC results (2008-09 → 2010-12, 28 months)

| Strategy | Cum return | Max DD | Recovery fires |
|---|---:|---:|---:|
| production (12-1) | +2.45% | -25.37% | n/a |
| VIX-based recovery | +2.45% | -25.37% | 0 |
| DD-based recovery | +1.21% | -26.44% | 4 |
| SPY (passive) | +15.97% | -35.74% | n/a |

## Delta

- DD-recovery vs production: **-1.24pp**
- DD-recovery vs VIX-recovery: **-1.24pp**

## Verdict: ⚠️  DD-rule fires but P&L delta is mixed

The drawdown detector successfully fires more often than the VIX detector during the GFC, but the resulting 6-1 lookback did not improve P&L vs production. The detector works; the response (6-1 momentum) may not be the right action during a deep-crash recovery. Worth exploring alternative responses (e.g., shift to defensive sectors).
