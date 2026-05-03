# Archived research scripts

These are old iteration scripts moved out of the working set per V5
Alpha Discovery Proposal Phase 1 (audit & delete). They are kept for
historical reference (they document the kill-list) but should NOT be
run from cron or imported by LIVE code.

| Script | Purpose | Status |
|---|---|---|
| iterate_v3.py..iterate_v14_more_anomalies.py | Sequential research iterations on momentum + factor + anomaly variants | superseded by `regime_stress_test.py` + `cpcv_backtest.py` |

If you need to resurrect any of these, copy back to `scripts/` rather
than importing from `archive/` — the archive directory is intentionally
not on the import path.
