# Research notes — where retail edge actually lives

*A working document for cataloguing market inefficiencies our system might exploit, and being honest about which ones we cannot.*

---

## Where retail CANNOT win

Documenting these so we don't waste cycles trying:

| Inefficiency | Owned by | Why we can't compete |
|---|---|---|
| Microstructure latency arb | Citadel, Jane Street, Jump, Virtu | <1µs infrastructure, co-located servers |
| Cross-exchange arb | HFT bots | Fixed in <100µs globally |
| Statistical arb on US equities | Renaissance, Two Sigma, DE Shaw | Custom datasets, $100M+ compute |
| Index/ETF authorized-participant arb | Designated APs | Privileged exchange relationship |
| Crypto market-making | Wintermute, GSR, Jump Crypto | Capital + colocation |
| News-reading speed (NLP at first millisecond) | Bloomberg-terminal funds | $24k/yr terminals + co-location |
| Treasury basis trade | Hedge funds with prime broker leverage | Need 10x+ leverage to be economical |

If a strategy claims to exploit one of the above on retail infrastructure, **it is wrong**. Either the inefficiency is gone, or you'll be the slowest player and lose.

---

## Where retail CAN win (cataloguing)

These persist because they're behaviorally driven, capacity-constrained, or unattractive to large players. Each adds 1-4% per year of uncorrelated alpha *if* you systematize them. **No single one is the answer.** The retail edge comes from compounding 4-5 of these.

### Calendar effects (with v1.7 empirical re-test)

| Anomaly | Published claim | **Our 2015-2025 measurement** | Status |
|---|---|---|---|
| Pre-FOMC drift | +49bps (Lucca-Moench 2015) | **+22bps, Sharpe 2.35 single-day** | High conf, half-strength but real |
| Turn-of-month | +70bps -1 to +3 (Etf 2008) | **+18bps vs +15.5bps random = +2.5bps edge** | **DEAD** — don't deploy |
| OPEX week | +20bps Mon-Wed (Stoll-Whaley 1987) | **+10.5bps Mon-Thu, 56.5% win** | Low conf, half-strength |
| Sell-in-May | -200bps May-Oct (Bouman-Jacobsen 2002) | not yet retested | Pending |
| Year-end reversal | +200bps Jan small-cap (Reinganum 1983) | not yet retested | Pending |
| Holiday effect | +12bps pre-holiday (Ariel 1990) | not yet retested | Pending |

### Event-driven

| Anomaly | Effect | Citation | Implementable? |
|---|---|---|---|
| Post-earnings drift (PEAD) | +400bps over 60d after earnings beats | Bernard & Thomas (1989); persists | Need earnings calendar API |
| 52-week high breakout | +50bps/mo for stocks within 5% of 52w high | George & Hwang (2004) | Already in `signals.py:breakout_52w_score` |
| S&P 500 add/drop | +5-8% pop on add, -3-5% on drop | Chen et al. (2004) | Tradable but small universe |
| M&A merger arb | 4-8%/yr return, low correlation | Mitchell & Pulvino (2001) | Possible with options |
| Spinoff effect | +12% YoY for spinoffs in year 1 | Cusatis et al. (1993) | Manual screening |

### Risk premia

| Anomaly | Effect | Citation | Implementable? |
|---|---|---|---|
| Volatility risk premium (VRP) | +6-10%/yr by selling short-term SPX puts | Bondarenko (2014) | Yes via options |
| Term structure of VIX (carry) | +3-5%/yr by short VXX in contango | Various | Yes via VIX futures |
| Trend-following on managed futures | Sharpe 0.6-1.0 over 30y | Hurst, Ooi, Pedersen (2017) | Easy via DBMF ETF |
| Quality factor | +2-3%/yr long high-ROE / short low-ROE | Asness et al. (2014) | Need fundamentals |
| Low-vol anomaly | +1-2%/yr long low-vol vs market | Frazzini & Pedersen (2014) | Easy add |

### Behavioral

| Anomaly | Effect | Citation | Implementable? |
|---|---|---|---|
| Earnings call sentiment | +200bps after positive transcripts | Loughran & McDonald (2011) | NLP pipeline (Claude API) |
| Analyst forecast dispersion | +200bps long low-dispersion / short high | Diether et al. (2002) | Need analyst data feed |
| Short interest decay | -300bps for high-SI stocks | Boehmer et al. (2008) | Need FINRA short data |
| 52-week low avoidance | -200bps for stocks within 5% of 52w low | Multiple | Easy filter |

---

## Honest assessment of what's deployed today (v1.4)

**Currently exploited:**
- 12-month cross-sectional momentum (Jegadeesh-Titman) — 1-2% expected alpha vs SPY
- Bottom-catch / mean reversion on RSI<30 + Bollinger — unmeasured live but +2.3%/20d in backtest

**Aware of, not yet implemented:**
- 52-week breakout (signal exists in `signals.py`, not deployed)
- Pre-FOMC drift (scanner shipped, not wired to executor)
- Turn-of-month (scanner shipped, not wired)
- All others above

**Out of scope for retail-without-options:**
- VRP (would need to write SPX puts — needs portfolio margin)
- Merger arb (needs options + capital)
- All factor sleeves needing fundamentals data ($99/mo Sharadar to add)

---

## How to evaluate a paper before trying to trade it

1. **Is it post-2010?** Pre-2010 anomalies have been published and arb'd to death.
2. **Sample period:** must include at least one bear market. 2010-2020 momentum looks great because everything trended.
3. **Sharpe ≥ 0.7 net of costs:** below this, transaction costs eat the edge.
4. **Out-of-sample test:** at least 5 years held out. If they only show in-sample, ignore.
5. **Capacity:** does the paper explicitly test it on $1B? If yes, it's already crowded.
6. **Does it require something we don't have?** Tick data, fundamentals, alt data, satellite imagery, prime broker leverage — if yes, retire.

## Approaching arxiv responsibly

arxiv quant papers, by frequency:

- 60% of new "alpha" papers are LSTM/Transformer applied to OHLCV. **Almost all overfit.** Skip.
- 20% are RL-for-trading. **Generally overfit on a single backtest period.** Skip unless walk-forward + CPCV.
- 10% are sentiment/NLP on news/social. **Sometimes real.** Check capacity.
- 5% are factor research extending Fama-French. **Often replicable.** Worth reading.
- 5% are market microstructure / liquidity / volatility. **Useful but not retail-tradable.**

**The honest filter:** read the abstract, look for words like "out-of-sample", "walk-forward", "deflated Sharpe", "after costs". If those phrases aren't there, skip.

---

## Next ideas to test (priority order for v2.0)

1. **Pre-FOMC drift sleeve** — small allocation, fires 8x/year. Backtest first, then deploy ~5% capital.
2. **Earnings drift sleeve** — needs earnings calendar API. Most academically validated anomaly. Allocate 10-15% if backtest holds.
3. **VIX term-structure carry** (short VXX or long VIX-inverse) — small position, tail-hedged. Validate with covid backtest.
4. **52-week breakout** — already coded as signal, never deployed. Add as 5-10% sleeve.
5. **Sell-in-May / Halloween effect** — simplest to add. Reduce equity exposure May-Oct.

---

*Last updated 2026-04-27. PRs welcome.*
