# CLOUD.md — running the trader off your laptop

## TL;DR

The trader now runs on **GitHub Actions cron**, not your laptop. Two workflows fire automatically Mon-Fri:

- `daily-run.yml` at 21:10 UTC (1:10pm PT) — places the day's trades, reconciles, snapshots, emails you
- `perf-digest.yml` at 21:38 UTC (1:38pm PT) — emails the day's P&L digest

Both use GitHub Secrets for credentials (Alpaca, Anthropic, SMTP) and persist the journal SQLite via GitHub Actions artifacts. **Your laptop can be off and the system still trades + emails you.**

## Why GitHub Actions, not Lightsail / EC2

| Approach | Cost | Setup | Reliability for our cadence |
|---|---|---|---|
| **GitHub Actions cron** (chosen) | $0 | None — just commit `.github/workflows/*.yml` | ~5-15 min cron drift, fine for daily strategy |
| AWS Lightsail VM | $5/mo | Provision + ssh + crontab + secrets mgmt | Bulletproof but more ops |
| Fly.io machines | $0-5/mo | Dockerfile + fly.toml + secrets | Cleaner than VMs but still more ops |

For a daily-rebalance strategy with no sub-minute timing requirement, GitHub Actions is the ideal fit. If we later add intraday strategies or need <1 min cron precision, switch to Fly.io machines using the included `Dockerfile`.

## Setup (one-time, ~5 minutes)

### 1. Add secrets to the GitHub repo

Visit https://github.com/rca6045407168/trader/settings/secrets/actions and add:

| Secret name | Value |
|---|---|
| `ALPACA_API_KEY` | from your `.env` (paper trading) |
| `ALPACA_API_SECRET` | from your `.env` |
| `ANTHROPIC_API_KEY` | from your `.env` |
| `SMTP_USER` | richard.chen.1989@gmail.com |
| `SMTP_PASS` | your Gmail app password (16 chars, no spaces) |

### 2. Verify the first run

After committing the workflows, GitHub will start running them on the cron schedule. To verify before the first scheduled fire:

1. Visit https://github.com/rca6045407168/trader/actions
2. Click "trader-daily-run" → "Run workflow" → click the green "Run workflow" button
3. Watch the run; check your email inbox for the resulting notification

### 3. Disable the laptop-side scheduled tasks

Once the GitHub Actions runs are confirmed working, you can disable the local Claude scheduled tasks to avoid double-firing:

```bash
# Disable each one (won't auto-run anymore):
launchctl unload ~/Library/LaunchAgents/com.trader.daily-run.plist  # if you ever installed it
# OR via the Claude Code Scheduled Tasks UI: toggle each "trader-*" task off
```

## What still runs locally

Some Claude scheduled tasks NEED LLM reasoning and can't easily move to GitHub Actions:

- `trader-research-paper-scanner` — needs WebSearch + paper-quality ranking
- The post-mortem agent (currently console only)

Those stay on your laptop / Claude Code for now. They're low-priority and weekly, so the laptop being asleep occasionally is fine.

## Journal persistence

GitHub Actions runs are ephemeral. The journal (`data/journal.db`) persists between runs via GitHub Actions artifacts:

- Each workflow first downloads the previous artifact (last successful run's journal)
- Runs the trader (writes new entries)
- Uploads the updated journal as the next artifact
- Retention: 90 days (configurable in workflow YAML)

If you ever need a fresh start, delete the artifact via GitHub UI: Actions → Artifacts → trader-journal → Delete.

## Troubleshooting

### Run failed
- Check the logs in GitHub Actions UI
- Common: missing secret (verify all 5 are set)
- Common: yfinance rate limit (retry the workflow)
- Common: Alpaca outage (system halts, not a bug)

### Run succeeded but no email
- Check spam folder
- Verify `SMTP_USER` and `SMTP_PASS` secrets are correct
- Check that 2FA is on for the Gmail account (app passwords require it)

### Journal divergence
- If the laptop and GitHub Actions both run at same time, they could diverge
- Keep one source of truth: disable laptop tasks once GH Actions is live
- Reconciliation runs at start of every daily-run, so divergence is caught and halts the run

## Rollback

To stop GitHub Actions from firing:

1. GitHub UI → Actions tab → click each "trader-*" workflow → "..." menu → "Disable workflow"
2. Or delete the `.github/workflows/*.yml` files and push

Local laptop tasks resume being the source of truth (if you didn't already disable them).
