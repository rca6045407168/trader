# Automation — what runs without me touching it

*Last updated 2026-05-04 (v3.68.1).*

This system has three layers of automation. Knowing which fires when
saves debugging time when something doesn't update.

---

## Layer 1 — prewarm.py (every Streamlit container start)

Fires when `streamlit run scripts/dashboard.py` starts. Runs **before**
the dashboard accepts traffic so the first user-load is fast (warm
disk caches).

| Section | Idempotent gate | Cost |
|---|---|---|
| HMM regime overlay | 5 min cache TTL | $0 (yfinance + FRED) |
| Live portfolio fetch | 30s cache | $0 (Alpaca paper) |
| Morning briefing | 1h disk cache | $0 (recomputes if stale) |
| Journal backup | Per-day marker file | $0 |
| Self-eval postmortem | Per-day journal row check | ~$0.01 (Claude) |
| News poller (US-only) | 10 min cache | $0 (RSS) |
| Earnings archive (8-K, no LLM) | Per-day marker file | $0 (SEC EDGAR) |
| Low-vol shadow | Per-day marker file | $0 |

Idempotency means: restarting Streamlit 5 times doesn't fire each
section 5 times. Per-day marker files (`data/.last_<job>_run`) gate
sections that should only fire once per day. Cache TTLs gate sections
that recompute periodically.

**The earnings archive section here is `--skip-claude`** — it just
fetches new 8-Ks from EDGAR and stores them on disk. The actual
Claude-powered analysis is gated by Layer 2.

---

## Layer 2 — launchd (scheduled background jobs)

macOS launchd jobs that fire on a schedule even when Streamlit isn't
running. All idempotent so missed fires (laptop asleep) get caught up
on wake without double-effects.

### `com.trader.earnings-reactor` (v3.68.3 — daemon mode)

**What it does:** long-running daemon that polls every 5 minutes,
fetching new SEC 8-Ks for every LIVE position, archiving them,
running Claude analysis, persisting signals to
`journal.earnings_signals`, and emailing on M≥3.

**Architecture (v3.68.3 change from v3.68.1):**
- v3.68.1 was launchd-respawn-every-4h: ~6 fires/day, 4h latency,
  ~1500 Python startups/year
- v3.68.3 is one persistent process polling every 5 min: 5-min
  latency, ~1 Python startup per crash (typical: zero/year)

**Why the change:** the user asked "how are you constantly looking?"
and I had to admit it wasn't constant — 4h cadence. v3.68.3 makes
it actually constant within the bound that EDGAR allows (we don't
hammer; we poll a per-ticker submissions endpoint at 5-min cadence).

**Tunability:** set `REACTOR_WATCH_INTERVAL` env to override the 300s
default (60s minimum). Tighter = more responsive but more SEC hits
(still well under the 10 req/sec rate limit even at 60s × 15 positions).

**Failure recovery:**
- KeepAlive=true → launchd respawns the daemon if it ever crashes
- ThrottleInterval=60s prevents tight crash loops
- Per-iteration try/except catches transient EDGAR / Claude errors
  without tearing down the daemon
- SIGTERM/SIGINT handled cleanly — finishes current iter, then exits
  with code 0

**Idempotency:** UNIQUE constraint on (symbol, accession) at archive
+ signals + notifications layers. Re-polling 8-Ks we've already
analyzed costs zero Claude tokens. Email alerts gated by `notified_at`
column so the user gets one email per material event, not 288.

**Token cost:** ~$0.018 per material 8-K analyzed. ~5-10 8-Ks/year
per position × 15 positions = ~$1-2/month steady state.

**Install:**
```
bash scripts/install_launchd_earnings.sh
```

**Logs:**
```
tail -f ~/Library/Logs/trader-earnings-reactor.out.log
tail -f ~/Library/Logs/trader-earnings-reactor.err.log
```

**Status:**
```
launchctl list | grep com.trader.earnings-reactor
```

**Disable:**
```
bash scripts/install_launchd_earnings.sh --uninstall
```

**Plist source:** `infra/launchd/com.trader.earnings-reactor.plist`
(version-controlled in the repo). The install script copies it to
`~/Library/LaunchAgents/` and `launchctl load`s it.

---

## Layer 3 — daily orchestrator (Mon-Fri 13:10 UTC)

`com.trader.daily-run` (a separate launchd job, lives in
`~/openclaw-workspace/trader-jobs/`, not in this repo). Fires the
monthly rebalance check, computes new target weights, submits orders
to Alpaca paper. **This is the only automation that places orders.**

The earnings reactor (Layer 2) feeds INTO Layer 3 indirectly — its
flags become inputs to manual decisions, but the orchestrator itself
only consumes momentum signals today. Wiring earnings signals into
the rebalance gate is a future v3.69+ extension.

---

## How to verify it's actually working

After installing the launchd job, you should see within ~10 seconds:

```
$ launchctl list | grep earnings
-       0       com.trader.earnings-reactor
```

The first column is PID (— means not running), second is exit code
(0 means last run succeeded). Then check the log:

```
$ tail ~/Library/Logs/trader-earnings-reactor.out.log
=== earnings reactor (15 symbols, since=14d, model=claude-sonnet-4-6) ===
  [M3] NVDA   2026-05-02 BULLISH    items=2.02,9.01  $0.0184  Q1 beat consensus...
  ...
```

Then check the dashboard's 📞 Earnings reactor view — flagged signals
will appear within seconds.

---

## Slack alerts (prismtrading workspace)

v3.69.1+: reactor alerts now push to **prismtrading** Slack alongside
email. The webhook is workspace-agnostic — the URL determines which
workspace + channel receives the message.

### One-time setup

1. Go to https://api.slack.com/apps → **Create New App** → **From
   scratch**.
2. Pick the **prismtrading** workspace (you must be an admin there to
   add apps).
3. Sidebar → **Incoming Webhooks** → toggle **Activate**.
4. **Add New Webhook to Workspace** → pick the channel for alerts
   (e.g. `#alerts` or `#trader-alerts`).
5. Copy the webhook URL (starts `https://hooks.slack.com/services/T.../B.../xxx`).
6. Add to your `.env`:
   ```
   SLACK_WEBHOOK=https://hooks.slack.com/services/T.../B.../xxx
   ```
7. Either restart the reactor daemon (`bash scripts/install_launchd_earnings.sh`)
   OR wait — the reactor reads SLACK_WEBHOOK at call time, so the next
   alert will use it once the env is in place.

### What it pushes

Same threshold as email: every M≥3 reactor signal triggers a Slack
message via Block Kit. Subject becomes a header block; body
(structured fields + summary + verbatim bull/bear quotes) lands in a
section as a code block. Both email AND Slack go out per signal —
either delivering counts as success for idempotency, so unconfigured
channels never cause retry loops.

### Disabling

Just remove `SLACK_WEBHOOK` from `.env`. Email continues to deliver.
Or leave both unset for console-only.

---

## What's NOT automated (yet)

- **News sentiment scoring** — `scripts/news_poller.py --score` exists
  but isn't wired to a launchd schedule. Hourly cron is reasonable;
  defer until the news view actually drives a trade decision.
- **Earnings call transcripts** — only 8-K press releases today
  (free via EDGAR). Full transcripts need a paid API
  (Polygon/Finnhub/AlphaVantage paid tier). The archive layer accepts
  them when added.
- **Cross-quarter diff analysis** — "how did NVDA's guidance language
  shift Q1→Q4?" The data is in the archive; the reader isn't built.
