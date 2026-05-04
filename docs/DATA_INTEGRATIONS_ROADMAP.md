# Data integrations roadmap — what to wire to augment trading decisions

*Created 2026-05-03 (v3.61.0). Answers two user questions:
"What integrations should you write to augment your data for trading decisions?"
+ "Add Asian + American news sources" + customer ask about Asia.*

---

## ⚠️ Honest framing first

Most trading edges don't come from "more data." They come from BETTER
processing of data we already have, or from non-public data we
genuinely can't get. Adding 20 free RSS feeds is easy; turning them
into trading edge is hard. The list below is ordered by **effort vs.
expected lift**, not by what's exciting.

Per the v3.60.1 verification audit: of 4 LITERATURE-CALIBRATED claims I
made and tested, **0 of 4 survived backtest on our data**. Adding more
sources without rigorous backtest discipline will likely produce more
calibrated-but-not-real claims.

---

## 1. What's shipped in this release (v3.61.0)

`src/trader/news_sources.py` — RSS / API adapters:

**🌏 Asian (per customer ask):**
- ✅ **Caixin** (财新) — high-credibility Chinese financial news, Eng + 中文 RSS
- ✅ **Yicai** (第一财经) — retail-oriented Chinese finance
- ✅ **Sina Finance** — retail-skewed Chinese roll feed (used as Eastmoney proxy)
- ✅ **Nikkei Asia** — English; Japan + broader Asia
- ✅ **Yonhap** — Korea, English version
- ⚠️ **Xueqiu** (雪球) — STUB. Public API requires session cookie.

**🇺🇸 US (per follow-up ask):**
- ✅ **Reuters Business** (via Google News RSS — Reuters direct RSS deprecated)
- ✅ **WSJ Markets** (headlines free; full text paywalled)
- ✅ **MarketWatch** real-time headlines
- ✅ **Seeking Alpha Market Currents**
- ✅ **SEC EDGAR Form 4** — insider transactions (free)
- ✅ **Yahoo Finance** per-ticker news (via yfinance.Ticker.news)

`src/trader/news_sentiment.py` — Claude-backed scorer:
- Translates non-English (zh/ja/ko → en)
- Scores -1 (bearish) to +1 (bullish)
- Extracts tickers
- URL-hash caches → `data/news_sentiment_cache.json`

---

## 2. What I'd build NEXT, ranked by ROI

### Tier 1 — high value, low effort

**Insider transactions (SEC Form 4)** — beyond the scaffold above
- Cluster detection: 3+ insiders buying same name in 30 days = strong signal
- Brav-Jiang-Partnoy (2008): insider clusters predict +5-8% in 6 months
- ✅ Free via SEC EDGAR full-text search
- Effort: ~6h to wire properly

**Short interest (FINRA RegSHO)** — already stubbed in AltDataAdapter
- Bi-monthly short interest reports (FINRA threshold list)
- Failure-to-deliver alerts (RegSHO threshold securities)
- Crowding signal: high SI + accelerating = squeeze candidate
- ✅ Free via finra.org / sec.gov
- Effort: ~6h

**Earnings calendar (RELIABLE)** — fix the v3.58.1 EarningsRule INERT bug
- Current: yfinance.earnings_dates fails silently for major tickers
- Replacement: Polygon free tier (500 calls/day) OR Finnhub free tier OR
  scrape company IR pages (last resort)
- Effort: ~4h. **HIGHEST PRIORITY** — fixes a known LIVE-but-broken feature.

**FRED macro** — already partially done in `trader.macro`
- Vintage-aware via ALFRED for backtest correctness
- Indicators: 10Y-2Y curve, HYG/LQD credit, ICE BofA HY OAS, US recession indicators
- Free via fredapi (no key required for basic; key for higher rate limits)
- Effort: ~3h to upgrade existing module

### Tier 2 — high value, moderate effort

**Polygon.io free tier** — 5 API calls/min
- Reliable per-ticker news, financials, earnings dates
- Real-time-ish quotes (NBBO)
- Replaces yfinance for the cases yfinance fails silently
- Free for backtesting use cases
- Effort: ~8h to integrate properly

**Alpha Vantage News Sentiment** — free 500 req/day
- Pre-scored sentiment per article
- Saves Claude API tokens for the scoring step
- Effort: ~4h

**Reddit r/wallstreetbets / r/investing** — Pushshift / praw
- Mention frequency + sentiment per ticker
- Specifically flag "unusual" mention spikes (z-score > 3)
- Useful for catching meme dynamics before they break in mainstream
- Effort: ~6h

**StockTwits** — free message stream
- Pre-tagged by ticker via `$AAPL` cashtags
- Bullish/bearish flag built into the message schema
- Effort: ~4h

**Treasury / FOMC calendar** — federalreserve.gov
- Already partially done in `trader.events_calendar`
- Add: Treasury auction schedule, Fed speakers, CPI/NFP/GDP release dates
- All free, all calendar-driven
- Effort: ~3h

### Tier 3 — moderate value, harder

**Options flow** (heavy)
- Free: CBOE delayed end-of-day chains
- Cheap: SpotGamma free tier (gamma exposure)
- Useful for the VRP sleeve once it goes LIVE
- Harder: real-time unusual options activity (paid: FlowAlgo, Cheddar Flow)
- Effort: ~16h for backtest infra

**Earnings call transcripts** (heavy)
- Free: company IR pages (uneven, requires scraping)
- Paid: Seeking Alpha, AlphaSense, Refinitiv
- High alpha potential: management tone analysis (Loughran-McDonald sentiment)
- Effort: ~20h to scrape + sentiment-score reliably

**13F institutional positions** (slow)
- Free via SEC EDGAR; quarterly with 45-day lag
- Useful for "smart money" tracking but the lag kills most edge
- Effort: ~12h for parser + diff detection

**FINRA / SEC enforcement actions** — SCAFFOLD
- Filings of regulatory action against CEOs / companies
- Negative signal for the named co
- Free via sec.gov / finra.org
- Effort: ~8h

### Tier 4 — Asian-market-specific (per customer ask)

**Critical caveat first:** Adding Asian news is easy. Trading Asian
markets requires:

1. **Different broker** — Alpaca is US-only. Asian markets need:
   - **Interactive Brokers** (HK, JP, KR, TW; SOME China A-shares via Stock Connect)
   - **Tiger Brokers** (good for HK/CN retail)
   - **Futu/moomoo** (HK + retail-friendly)
   - **Mirae Asset** (Korea)
   - China A-shares specifically need QFII/RQFII or HK Stock Connect
2. **Different price data** — yfinance has SOME HK/JP/KR coverage (use
   suffix like `.HK`, `.T`, `.KS`) but **NOT mainland China A-shares**.
   Those need 雪球 / Wind (paid) / Tushare (Chinese open data).
3. **Currency math** — every position needs a USD-equiv valuation
4. **Different trading hours** — KOSPI 09:00-15:30 KST; HSI 09:30-12:00 +
   13:00-16:00 HKT; SSE 09:30-11:30 + 13:00-15:00 CST
5. **Different fee structures** + stamp duty (HK 0.1%, China 0.05%)
6. **Different tax treatment** for the operator

**Asian-market-specific data integrations to wire when above is in place:**

| Source | Region | Free? | Effort |
|---|---|---|---|
| **AKShare** (Chinese open-data lib) | CN | ✅ free | 8h — wraps Sina/Tushare/Eastmoney |
| **Tushare** (Chinese open-data) | CN | free tier; paid for full | 8h |
| **HKEX** stock-connect data | HK | ✅ free | 6h |
| **KRX** (Korea Exchange) market data | KR | ✅ free RSS | 4h |
| **JPX** (Japan Exchange) | JP | ✅ free | 4h |
| **Xueqiu** (sentiment) | CN | needs login | 12h — scraper risk |
| **Weibo** financial accounts | CN | API + scraper | 16h — scraper risk |
| **WeChat** finance subscriptions | CN | scraping risky / TOS issues | not recommended |
| **Naver** finance forum | KR | free scrape | 6h |
| **Yahoo Japan** finance | JP | free RSS | 4h |

**Honest take on the customer pitch (the WhatsApp screenshot):**

> "Asian markets are retail-dominated → AI tools work better there"

This is **partially true but oversold**:

- ✅ True: Chinese A-share retail share is 80%+ vs US 25%; Korean is similar
- ✅ True: Retail behavior creates more momentum/mean-reversion patterns
- ❌ Concern: Capital controls (CN), home-bias (KR), language barrier — these are operational headaches that EAT into edge
- ❌ Concern: US hedge funds DO compete on Chinese markets via Stock Connect at scale; "no competition" claim is overstated
- ❌ Concern: "AI win rate is improving" claim has no backing data — could be selection bias from people who ONLY share their wins
- ❌ Concern: Selling AI-trading tools to retail in China has compliance risk (CSRC views algorithmic-retail-pitches as potential violations of investor-suitability rules)

**My honest recommendation if the customer is real:**

1. Start with **HK-listed Chinese ADRs accessible via Alpaca** (BABA, JD, BIDU, PDD, NIO, etc) — gets ~80% of the China-market exposure WITHOUT the capital-controls / broker-change overhead
2. Add `news_sources.py` Caixin/Yicai/Nikkei/Yonhap to enrich US-listed-ADR signals
3. **Don't** pivot the whole architecture to A-shares until there's a paying customer who's solved their own broker access
4. **Don't** make the "AI beats retail" pitch the core value prop — it's true on average but doesn't survive realistic trading-cost assumptions for a $10K account

---

## 3. Beyond news — alt-data integrations to consider

These augment the SIGNAL side, not just the news layer:

| Integration | What it gives you | Free? | Wired? |
|---|---|---|---|
| **SEC EDGAR Form 4** | Insider buy/sell transactions | ✅ | scaffold only |
| **FINRA RegSHO** | Short interest threshold list | ✅ | scaffold only |
| **CBOE put-call ratio** | Options sentiment | ✅ delayed | no |
| **VIX term structure** | Vol regime | ✅ via yfinance ^VIX | yes (regime_overlay) |
| **TIPS breakeven inflation** | Inflation expectations | ✅ FRED | no |
| **HYG/LQD credit spread** | Credit stress | ✅ FRED | yes (regime_overlay) |
| **Yield curve 10Y-2Y** | Recession signal | ✅ FRED | yes (regime_overlay) |
| **Earnings call transcripts** | Tone / surprise detection | scrape | no |
| **Google Trends** | Retail attention | ✅ pytrends | no |
| **Reddit WSB sentiment** | Retail meme signal | ✅ pushshift | no |
| **Twitter/X cashtag** | Real-time sentiment | paid only post-2023 | no |
| **StockTwits** | Tagged sentiment | ✅ | no |
| **AlphaVantage News Sentiment** | Pre-scored | free 500/day | no |
| **Polygon Reference** | Reliable fundamentals | free tier | no |
| **Tradier** | Real-time quotes | free w/ account | no |
| **CFTC Commitment of Traders** | Futures positioning | ✅ | no |

---

## 4. Concrete next steps (if I were prioritizing for P&L)

1. **Fix the EarningsRule INERT bug** (Tier 1) — the LIVE feature that
   does nothing. ~4h. Makes existing claim real.
2. **Wire SEC Form 4 properly** (Tier 1) — clusters of insider buys are
   the most-replicated free alt-data edge. ~6h.
3. **Polygon.io integration** (Tier 2) — replaces yfinance silent
   failures. ~8h. Unblocks several other items.
4. **Add the Asian news adapters to the dashboard** — already shipped
   in this commit. Surface as a new "📰 News" view.
5. **DEFER** Asian-market broker / price-data work until there's a
   paying customer who's solved their own access first. The architectural
   change is too big to speculate on.

The integrations themselves are the easy part. The discipline is to
backtest each one BEFORE claiming it adds P&L. Per the v3.60.1 audit,
0 of 4 literature-calibrated claims survived. Don't add news/sentiment
to that list without honest verification.

---

*Last updated 2026-05-03 (v3.61.0). Linked from BEST_PRACTICES.md §12.*
