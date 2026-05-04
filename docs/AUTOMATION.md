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

### `com.trader.earnings-reactor` (v3.68.1)

**What it does:** runs `scripts/earnings_reactor.py` for every LIVE
position. Fetches new SEC 8-Ks, archives them, runs Claude with a
structured-output schema, persists signals to
`journal.earnings_signals`. Surfaces in the 📞 Earnings reactor view.

**Schedule:**
- Weekdays at 17:05 ET (post-close)
- Every 4 hours via `StartInterval` (sleep-resilient)
- On launchd load (= when you install it, or after every laptop wake)

**Idempotency:** the reactor's UNIQUE constraint on (symbol, accession)
makes over-firing free. If no new 8-Ks have been filed since the last
run, no Claude tokens are spent.

**Token cost:** ~$0.018 per material 8-K analyzed. With 15 LIVE
positions at ~5-10 8-Ks/year each, expected spend is ~$1-2/month.

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

`ai.flexhaul.trader-daily-run` (a separate launchd job, lives in
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
- **Slack/email reactor alerts** — when the reactor flags a M5 (thesis-
  altering) event, no notification is pushed. Add this when material
  events become more common in the portfolio.
