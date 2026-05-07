# ENFORCING Canary Log

Weekly verification that the drawdown protocol still mutates targets correctly under synthetic -13% DD. Run via `scripts/weekly_enforcing_canary.py`.

| Timestamp | Mode | Tier | OK | Gross before → after |
|---|---|---|---|---|
| 2026-05-06T20:43:51 | ENFORCING | ESCALATION | ✅ | 80.00% → 30.00% |
| 2026-05-06T20:45:26 | ENFORCING | ESCALATION | ✅ | 80.00% → 30.00% |
| 2026-05-06T21:06:17 | ENFORCING | 5/5 | ✅ | GREEN, YELLOW, RED, ESCALATION, CATASTROPHIC |
