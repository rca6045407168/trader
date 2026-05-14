# Ops runbook — incident response & diagnostics

> What to do when the trader stops behaving. This is the operational counterpart to `RUNBOOK_MAX_RETURN.md` (which covers happy-path setup). If something is genuinely broken — heartbeat alerted, reconcile halted, a strategy did something weird — start here.

## Triage at the top

Before opening any file, run:

```bash
python scripts/platform_state.py
```

That tool prints the platform's current state in ~2 pages — env flags, recent strategy picks, daemon health, journal counts, recent TLH events. **80% of incidents are diagnosed from this output alone.**

If you also want a fresh reconciliation against the broker:
```bash
python scripts/run_reconcile.py
```

---

## Cash-park overlay (v6.1.0, 2026-05-14)

**What:** route residual cash (after all overlays) into a benchmark ETF
(default SPY) to remove cash drag on up days. Active only in GREEN
drawdown tier — when DD escalates, cash IS the protection and the
overlay shuts off.

**Why:** the trader finishes most sessions at ~60-70% deployed (alpha
sleeve sizing + vol-target). The leftover ~30-40% sits in cash earning
~0% (Alpaca) / ~5% (Public.com). On a +0.80% SPY day, that's
30-40 bps of relative underperformance baked in. The cash-park
overlay converts that bucket from beta-0 to beta-1.

**Backtest (2021-2026 sim, 62% deployed alpha, residual → SPY):**
- ΔCAGR: +7.78 pp (17.30% → 25.08%)
- ΔSharpe: +0.09 (1.10 → 1.19)
- ΔmaxDD: -6.6 pp (worse — mitigated by drawdown-tier gate in prod)
- Δalpha-vs-SPY: +7.78 pp (2.36 → 10.14)

**Enable / disable:**
```bash
# enable for the next daemon fire:
echo 'CASH_PARK_TICKER=SPY' >> .env
# disable:
sed -i '' '/CASH_PARK_TICKER/d' .env
```

**Suppression rules (built in, no operator action):**
- Skipped if drawdown tier != GREEN (YELLOW @ -5%, RED @ -8%,
  ESCALATION @ -12%, CATASTROPHIC @ -20%).
- Skipped if residual < 1% (don't bother trading dust).
- Always keeps a 5% minimum cash buffer regardless.

**What to expect in the daemon log when active:**
```
  cash-park: park 33.5% in SPY (residual 38.5%, buffer 5.0%)
```

**Validation warning you'll see (expected, not a halt):**
```
  validation warn: SPY weight 33.5% > 20% — concentration risk
```

This is the overlay working — SPY is the concentrated cash-park
position by design. The warning is informational.

**To remove SPY from the book after disabling:** next rebalance will
naturally sell it to zero. To force immediate exit, run
`/Users/richardchen/trader/.venv/bin/python -m trader.main --force`
after removing the env var.

---

## Incident playbook

### Daemon harness silently rejected commands (no orders placed)

**Symptom:** scheduled `trader-daily-run` agent fires on time, but logs at
`~/openclaw-workspace/trader-jobs/logs/trader-daily-run-*.log` say something like
"The Bash tool is prompting for approval", "the harness keeps rejecting the
command", or "source / . shell builtins are blocked by the sandbox". The
orchestrator never ran, no orders placed, no snapshot written.

**Root cause (2026-05-13):** the daemon's Claude Code harness only auto-approves
Bash commands matching the patterns in `.claude/settings.local.json`, and
shell builtins `source` / `.` are blocked outright by the sandbox. SKILL.md
files that say `cd /Users/richardchen/trader && source .venv/bin/activate;
python scripts/run_reconcile.py` fail twice: `source` is blocked, and the
relative `python` path doesn't match the absolute-path allowlist entries.

**The contract:**
- Always invoke the venv Python by absolute path:
  `/Users/richardchen/trader/.venv/bin/python /Users/richardchen/trader/scripts/<name>.py`
- Never use `source` / `.` inside a scheduled-task SKILL.md.
- New scheduled tasks must use only commands matching an entry in
  `.claude/settings.local.json::permissions.allow`. Add new entries before
  deploying the task.

**Fix if it recurs:** open the daemon log to see what command was rejected,
then either (a) add the pattern to `.claude/settings.local.json`, or
(b) rewrite the SKILL.md step to use a pre-approved absolute-path form.

**Manual recovery for a missed run:**
```bash
/Users/richardchen/trader/.venv/bin/python /Users/richardchen/trader/scripts/run_reconcile.py
# if HALT: /Users/richardchen/trader/.venv/bin/python /Users/richardchen/trader/scripts/resync_lots_from_broker.py --apply
/Users/richardchen/trader/.venv/bin/python -m trader.main
```

### Daily heartbeat alert fires

The heartbeat daemon (`com.trader.daily-heartbeat`) alerts when the daily-run hasn't completed for >24 hours.

**Most common cause:** Mac was asleep through the cron firing.
**Diagnosis:**
```bash
tail -50 ~/Library/Logs/trader-daily-run.{out,err}.log
```

**If laptop was asleep:** the daemon's `StartInterval` pairing should have caught it within an hour of wake. If it didn't, the `RunAtLoad` flag in the plist is off — kickstart manually:
```bash
launchctl kickstart -k gui/$(id -u)/com.trader.daily-run
```

**If the orchestrator HALTed:** see the reconciliation-drift section below or check the kill-switch reason in the log tail.

---

### Reconciliation HALT (`matched=X missing=Y unexpected=Z size_mismatch=W`)

The orchestrator refuses to trade when the journal disagrees with the broker.

**Diagnosis:**
```bash
python scripts/run_reconcile.py
```

The output identifies which symbols drifted. **Three common causes:**

1. **Pending fills from a weekend submission.** Friday after-hours orders queue at the broker for Monday open. While they're queued, the journal has a lot but the broker shows no position. The `awaiting_fill` bucket should catch this — if `halt_recommended` is True AND `awaiting_fill` is non-empty, just wait for the fills to clear.

2. **Real broker drift** (stop fired, manual trade, broker bug). Use `scripts/resync_lots_from_broker.py --check` to see the per-symbol drift, then `--apply` to make the journal match the broker.

3. **Schema mismatch on Public.com.** Public.com's `Portfolio.positions` shape may have changed. Check the adapter's mapping in `src/trader/broker/public_adapter.py::get_all_positions()`.

**Recovery:** after fixing the underlying cause, the next daily-run will re-attempt. To force a re-run after a fix:
```bash
python -m trader.main --force
```

---

### Market closed but daily-run fired (Sunday / holiday)

This was a real bug we fixed in commit `[v6-weekend-safety]`. As of that commit, the market-open gate halts before submitting orders unless `ALLOW_WEEKEND_ORDERS=1`.

**Symptom:** orchestrator log says `HALT: market closed (next open ...)`.
**This is correct behavior.** The next daily-run on a market-open day will execute normally.

**If you actually want to submit pre-market or weekend orders** (rare — orders queue at the broker until next open):
```bash
ALLOW_WEEKEND_ORDERS=1 python -m trader.main --force
```

---

### Drawdown breaker tripped (-10% from all-time peak)

The v3.58 circuit breaker halts trading when equity drops 10% from the all-time peak in the journal.

**Confirm the breaker is real:**
```bash
python -c "
import sys; sys.path.insert(0, 'src')
from trader.journal import recent_snapshots
snaps = recent_snapshots(days=10_000)
peak = max(s['equity'] for s in snaps if s.get('equity'))
print(f'Peak: \${peak:,.2f}')
print(f'Current: \${snaps[0][\"equity\"]:,.2f}')
print(f'DD: {(snaps[0][\"equity\"] - peak) / peak * 100:.2f}%')
"
```

**If the DD is real:** stop and think. The breaker is mechanical — review the cause (regime change? data error? real loss?) before clearing. Once you've decided to continue:
```bash
launchctl setenv DRAWDOWN_BREAKER_STATUS SHADOW
launchctl kickstart -k gui/$(id -u)/com.trader.daily-run
```

**If the DD is spurious** (e.g. a stale snapshot inflated the peak): edit `data/journal.db` directly via `sqlite3` or run a manual snapshot insert with the corrected equity.

---

### Data-quality halt (`HALT: data-quality halt-severity issues detected`)

The v6 data-quality monitor (commit `[v6-data-quality]`) refuses to trade on suspect yfinance output.

**Diagnosis:** the halt message names the issue. Most common:
- `freshness`: yfinance returned a stale data window
- `dead_nan`: a symbol has all-NaN in last 5 rows (delisted? feed broken?)
- `extreme_jump`: a >20% day-over-day move that doesn't track SPY

**If real** (e.g. INTC delisted after a buyout): remove the symbol from the universe in `src/trader/universe.py` or `src/trader/sectors.py`, re-run.

**If spurious** (e.g. yfinance hiccup, ticker symbol changed temporarily):
```bash
DATA_QUALITY_HALT_ENABLED=0 python -m trader.main --force
```

---

### TLH harvest expected but didn't fire

You see drawdowns in the book but `position_lots` shows no realized losses.

**Diagnosis sequence:**

1. **Is `TLH_ENABLED=true`?** Check env:
   ```bash
   launchctl getenv TLH_ENABLED
   ```
   If not set, that's it. TLH defaults OFF.

2. **Is the position actually below cost basis by ≥5%?** Check:
   ```bash
   python -c "
   import sys; sys.path.insert(0, 'src')
   from trader.direct_index_tlh import get_current_unrealized_pnl
   print(get_current_unrealized_pnl())
   "
   ```
   The harvest threshold is 5% (configurable via `TLH_MIN_LOSS_PCT`). A 4% drawdown won't fire.

3. **Is the replacement wash-sale-blocked?** Every sector-matched replacement is checked against the last 31 days of `closed_at` entries. If you sold the replacement recently, the planner correctly skips.

4. **Is the position in the `direct_index_core` sleeve?** TLH only harvests the core sleeve, not auto-router-alpha positions. Check `position_lots.sleeve`:
   ```bash
   sqlite3 data/journal.db "SELECT symbol, sleeve FROM position_lots WHERE closed_at IS NULL"
   ```

---

### A strategy returned weird picks

Auto-router suddenly picked INTC at +250% momentum and you can't tell why.

**Diagnosis:** open the 🌳 Decisions tab in the dashboard, click into the per-decision paragraph reasoning (shipped in commit `[v6-decisions-reasoning]`). Each row explains the signal, ranking, and variant selection in plain English.

If the reasoning looks correct but the OUTPUT looks wrong, the issue is data:
```bash
python -c "
import sys; sys.path.insert(0, 'src')
from trader.data import fetch_history
import pandas as pd
end = pd.Timestamp.today()
start = (end - pd.DateOffset(months=13)).strftime('%Y-%m-%d')
p = fetch_history(['INTC'], start=start)
print(p['INTC'].tail(20))
print(f'12-1 month return: {p[\"INTC\"].iloc[-22] / p[\"INTC\"].iloc[-264] - 1:.2%}')
"
```

If the price history looks corrupted (e.g. splits not adjusted), purge the yfinance cache:
```bash
rm -rf data/cache/*.parquet
```

---

### Public.com API errors

After the BROKER=public_live flip, you see `APIError` or `AuthenticationError` in logs.

**Diagnosis:**
```bash
python scripts/test_public_connection.py
```

That script's 4-step verification will narrow down whether the issue is creds, network, or schema.

**If creds:** regenerate the API key in Public.com → Settings → API Access. Update `.env`. Restart daemons via `launchctl kickstart`.

**If schema:** Public.com's SDK may have updated. Pin the version:
```bash
pip install 'public_api_sdk==<version-from-test_public_connection>'
```

**If network:** transient. The orchestrator's clock-fetch failure path proceeds conservatively (logs but doesn't halt), so single-call failures shouldn't break a daily-run.

---

### Want to roll back from Public.com to Alpaca

```bash
launchctl setenv BROKER alpaca_paper
launchctl kickstart -k gui/$(id -u)/com.trader.daily-run
```

The next daily-run will use the Alpaca paper account. **Public.com positions stay where they are** — manual cleanup if you want them closed:
```python
import os; os.environ["BROKER"] = "public_live"
from trader.broker.public_adapter import PublicAdapter
adapter = PublicAdapter()
for pos in adapter.get_all_positions():
    print(adapter.close_position(pos.symbol))
```

---

### Before the flip — pre-flight rehearsal

Run this BEFORE flipping `BROKER=public_live` for the first time:

```bash
python scripts/first_live_dry_run.py
```

It does an in-process BROKER=public_live override (your launchctl env stays unchanged), reads your real Public.com account state, computes what the next daily-run would do, and prints the order plan WITHOUT submitting anything.

**What to verify in the output:**
- ✅ Public.com connectivity (adapter authenticates)
- ✅ Account equity matches what you expect
- ✅ Market clock reads correctly
- ✅ Strategy targets are non-zero (if all targets are zero, see "All targets zero" below)
- ✅ Order plan looks sane (notional sizes match equity × target weights)

### All targets zero / CATASTROPHIC tier (cross-broker drawdown false positive)

**Current state (2026-05-12): RESOLVED via the durable fix.** The journal, deployment_anchor, and risk_freeze_state are now all broker-scoped — each broker has its own peak/anchor/freeze-state. `.env` is back to `DRAWDOWN_PROTOCOL_MODE=ENFORCING`. Cross-broker false-positive CATASTROPHIC is no longer possible.

**The original bug (preserved for context)**: when you flipped `BROKER=public_live`, the drawdown protocol read the same journal as before. The journal's all-time peak ($111k from Alpaca paper) got compared to Public.com's much smaller live equity, producing a false -99% drawdown → CATASTROPHIC tier → all targets zeroed → daily-run would liquidate everything.

**The durable fix that landed** (commit `[v6-broker-scoped-journal]`):
1. `daily_snapshot` table now has composite PK `(date, broker)`. Existing rows tagged as `alpaca_paper`. `recent_snapshots()` filters by current BROKER env by default; pass `broker="all"` for cross-broker views.
2. `deployment_anchor.json` is now a dict keyed by broker. Legacy single-tenant JSON gets migrated on first load. Each broker's anchor auto-sets on first daily-run for that broker.
3. `risk_freeze_state.json` similarly broker-scoped. Liquidation-gate trips, daily-loss freezes, and deploy-DD freezes are all per-broker now.
4. `main.py`'s direct SQL read for drawdown protocol filters by current broker.

**What this means operationally:**
- When the operator flips `BROKER=public_live`, the system has 0 historical snapshots for that broker → no peak comparison → no CATASTROPHIC.
- A fresh deployment_anchor auto-sets to the broker's current equity on first daily-run.
- The freeze state on alpaca_paper is preserved untouched (won't bleed into public_live behavior).
- After enough public_live snapshots accumulate (~30 days), the protocol resumes its normal "peak vs current" semantics for that broker independently.

### First week post-flip checklist

After flipping `BROKER=public_live`, watch these for 7 days:

| Day | Verify |
|---|---|
| Day 0 (flip) | `go_live_gate.py` shows 9/9. `platform_state.py` shows expected env. |
| Day 1 | Daily-run completed without halt. Orders fired on Public.com. Check Public.com UI for fills. |
| Day 2 | Reconciliation matched on Day-1 fills. `position_lots` has new MOMENTUM entries. |
| Day 3-5 | Daily-snapshot writing equity values that track Public.com's number ±1%. |
| Day 7 | First weekly digest fires. Compare to actual Public.com weekly statement. |

**If any of these fail:** halt the daemon (`launchctl unload ~/Library/LaunchAgents/com.trader.daily-run.plist`), diagnose using this runbook, re-load when fixed.

---

### Where logs live

| File | What |
|---|---|
| `~/Library/Logs/trader-daily-run.{out,err}.log` | orchestrator stdout/stderr |
| `~/Library/Logs/trader-shadow-eval.{out,err}.log` | shadow eval daemon |
| `~/Library/Logs/trader-daily-heartbeat.{out,err}.log` | heartbeat checker |
| `~/Library/Logs/trader-earnings-reactor.{out,err}.log` | earnings poller |
| `data/journal.db` | source of truth (decisions, orders, lots, snapshots) |
| `data/reports/run_<run_id>.md` | per-run decision reports (one per daily run) |

`tail -200` on the right log usually surfaces the cause within a minute.

---

### What this runbook deliberately doesn't cover

- **Strategy modifications.** Adding/removing edges is a code change, not an incident. See ARCHITECTURE.md §3 for the version-history protocol.
- **Tax decisions.** Year-end tax reconciliation is in `scripts/tlh_year_end.py` and your accountant's hands.
- **Performance evaluation.** "Is the system making money?" is a 6-month question, not a daily-run-failure question. Use `view_performance` in the dashboard.

If something happens that isn't in this runbook and isn't obviously one of the categories above: open the conversation, paste the symptom + the relevant log, ask for diagnosis. Don't just kickstart blindly.
