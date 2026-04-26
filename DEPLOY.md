# DEPLOY.md — daily autonomous operation

## Current state (as of v1.2 / commit 30d08e8)

- Alpaca paper account `PA3PBSJ525RN` connected, $100k starting equity
- 5 paper orders queued for Monday open (CAT/INTC/AMD/GOOGL/AVGO @ $6,793 each)
- Strategy: 12-month momentum top-5 + bottom-catch with risk-parity weighting
- 44 unit tests passing, kill switch armed, reconciliation script ready

## Make it run autonomously

The system needs to fire 4 times per trading week:

| Time | Script | Purpose |
|---|---|---|
| 4:05 PM ET (after close) | `run_reconcile.py` | Compare journal vs Alpaca; halt on divergence |
| 4:10 PM ET | `run_daily.py` | Place tomorrow's orders based on today's close |
| 6:00 AM ET (before open) | `run_postmortem.py` | Self-review yesterday's decisions vs P&L |
| Sunday 8:00 AM | `run_optimizer.py` | Walk-forward parameter check; alert if drift |

## macOS launchd setup (recommended)

Launchd is more reliable than cron on macOS — it survives reboots and handles missed runs.

```bash
# 1. Create the plist files
mkdir -p ~/Library/LaunchAgents

cat > ~/Library/LaunchAgents/com.flexhaul.trader.daily.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.flexhaul.trader.daily</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string><string>-c</string>
    <string>cd /Users/richardchen/FlexHaul/trader && source .venv/bin/activate && python scripts/run_reconcile.py && python scripts/run_daily.py >> logs/daily.log 2>&amp;1</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>10</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>10</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>10</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>10</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>10</integer></dict>
  </array>
  <key>StandardOutPath</key><string>/Users/richardchen/FlexHaul/trader/logs/launchd.out</string>
  <key>StandardErrorPath</key><string>/Users/richardchen/FlexHaul/trader/logs/launchd.err</string>
</dict>
</plist>
EOF

mkdir -p logs
launchctl load ~/Library/LaunchAgents/com.flexhaul.trader.daily.plist
```

## Stop autonomous trading

```bash
# Soft halt (next run won't place orders)
python scripts/halt.py on "vacation"

# Hard stop (uninstall the cron)
launchctl unload ~/Library/LaunchAgents/com.flexhaul.trader.daily.plist
```

## How to know if something broke

1. **Check the journal** — `sqlite3 data/journal.db "select * from postmortems order by date desc limit 3"`
2. **Check launchd logs** — `tail -50 logs/launchd.err`
3. **Run reconciliation manually** — `python scripts/run_reconcile.py` (exit code 2 = halt recommended)
4. **Check Alpaca dashboard** — open https://app.alpaca.markets and verify positions match `data/journal.db daily_snapshot`

## When to switch to live trading

**Wait at least 3 months of paper trading.** Specifically:
- Paper Sharpe > 1.0 over rolling 3-month window
- Paper max drawdown stayed within -20% of starting equity
- No reconciliation errors
- No kill-switch trips
- You've reviewed at least 3 post-mortems and they make sense

Then, in `.env`: change `ALPACA_PAPER=true` to `ALPACA_PAPER=false`. Use a SEPARATE Alpaca account funded with $1-5k that you can lose entirely. Don't move your Fidelity holdings.

## When to upgrade the strategy (v2.0 ideas)

Documented in [CAVEATS.md](CAVEATS.md). Top candidates not yet implemented:
- 2008 crisis backtest (need data older than 2015)
- Quality-momentum filter (ROE-based filter on momentum picks)
- Post-earnings-announcement drift signal (need earnings calendar API)
- Crypto sleeve via Alpaca crypto API (uncorrelated returns)
- Multi-account: separate Roth IRA for tax efficiency

But the v1.0+v1.1 results showed adding more features = lower OOS Sharpe. Be skeptical of any further additions — walk-forward EVERYTHING.

## Real expected returns (memorize these)

| Metric | Realistic | Backtest (biased) |
|---|---|---|
| CAGR | 17-25% | 30%+ |
| Sharpe | 1.0-1.4 | 1.4-1.8 |
| Max DD | -15 to -25% | -14 to -20% |
| Worst observed crash DD | **-27%** (2018-Q4) | — |

If you ever see live performance dramatically diverge from these (better OR worse), investigate before reacting. Drawdowns deeper than -25% should trigger a manual review of whether the strategy edge is still intact.
