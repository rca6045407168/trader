# trader

Personal automated trading system for Richard. Lives in `~/FlexHaul/trader/`.

## What it does

1. **Ranks the S&P 500 by 12-month momentum** (skipping the most recent month) → buys top 5, equal-weighted, monthly rebalance. ~80% of capital. Defaults set from a walk-forward parameter sweep — see `CAVEATS.md` for empirical findings.
2. **Scans for oversold bottoms** every day: RSI<30 + price >2σ below 20-day MA + volume spike + long-term uptrend intact. Bottom-catch candidates go to a multi-agent debate (Bull / Bear / Risk Manager via Claude API). Approved ones get up to 20% of capital.
3. **Logs every decision** (decisions, orders, P&L snapshots) to SQLite.
4. **Self-reviews each night**: a Post-Mortem agent reads yesterday's decisions + today's price reaction, proposes ONE specific tweak per day. Logged, not auto-applied.

## Brokerage

[Alpaca](https://alpaca.markets) paper trading. Free. Real market data. Paper account is unlimited and lets the system run indefinitely without real money. Switch to live trading by changing one env var (`ALPACA_PAPER=false`) once paper Sharpe > 1.0 over 3 months.

**Why not Fidelity?** Fidelity has no public retail trading API. The only "Fidelity API" on PyPI is an unofficial Playwright scraper that violates ToS and can lock your account. Keep Fidelity for long-term holds; run the algo on Alpaca.

## Setup (Richard, do this once)

```bash
cd ~/FlexHaul/trader
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Then edit .env with your Alpaca paper keys + Anthropic key
```

**Get Alpaca paper keys** (5 min):
1. Sign up at https://alpaca.markets (free, no SSN for paper, just email)
2. Switch to Paper Trading in the dashboard
3. Generate API key + secret → paste into `.env`

## Run

```bash
# Backtest momentum on liquid 50, 2015-now
python scripts/run_backtest.py

# Dry-run today's trade decisions (no orders placed)
DRY_RUN=true python scripts/run_daily.py

# Place actual paper orders
python scripts/run_daily.py

# Nightly self-review (run after market close, before next open)
python scripts/run_postmortem.py
```

## Architecture

```
src/trader/
├── config.py        # env loading
├── universe.py      # S&P 500 / liquid-50 ticker lists
├── data.py          # yfinance fetch + parquet cache
├── signals.py       # momentum, RSI, Bollinger z-score, ATR, bottom-catch composite
├── strategy.py      # ranks momentum + finds bottoms → trade candidates
├── backtest.py      # pandas-based backtest with SPY benchmark
├── critic.py        # Bull/Bear/Risk-Manager swarm debate (Claude API)
├── postmortem.py    # Nightly self-review agent (Claude API)
├── journal.py       # SQLite — decisions, orders, daily snapshots, postmortems
├── execute.py       # Alpaca order placement (notional orders)
├── notify.py        # Slack webhook + console
└── main.py          # daily orchestrator
```

## Reality check

90% of retail algo traders underperform buy-and-hold SPY in year 1. 80% of backtested strategies fail live. Realistic returns for survivors: 8-15% annual. **Run paper for at least 3 months before risking real money.** Even then, start with $1-5k you can lose entirely.
