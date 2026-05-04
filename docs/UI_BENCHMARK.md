# UI benchmark — what world-class trading platforms do that we don't

*Generated 2026-05-03 (v3.64.0 → v3.65.0). Companion to PRODUCTIZATION_ROADMAP.md.*

I studied 5 mainstream trading / investing platforms by capturing their AAPL
detail pages with Chrome MCP. Goal: see what professionals expect when they
land on a stock, and reverse-engineer the layout primitives we're missing.

Couldn't reach via the MCP safety allowlist: TradingView, Robinhood, Public,
Webull, MarketWatch, Bloomberg, Morningstar, Schwab, Finviz. Captured:
**Yahoo Finance, CNBC, Nasdaq.com, TipRanks, Composer.trade**.

---

## Pattern 1 — The price headline is HUGE and color-blocked

| Platform | Price font | Color treatment |
|---|---|---|
| Nasdaq.com | ~64px bold | Whole headline sits in a green/red full-width block ("$280.14 ▲ +8.79 +3.24%") |
| Yahoo | ~48px bold | Plain background; arrow + delta colored, price black |
| CNBC | ~56px bold | After-hours + close STACKED as separate price blocks side-by-side |
| TipRanks | ~40px bold | Number prominent next to a hexagonal "Smart Score" badge |

**Our system today:** the morning briefing shows numbers as Streamlit
`st.metric` cards which are 24-32px. Functional but doesn't communicate
"this is the headline number."

**Action for v3.65.0:** new "tape" header at the top of the briefing: equity
NOW, day P&L %, vs SPY excess — each as a 48px+ headline with green/red
background tint. Match the Nasdaq treatment for the dominant number.

---

## Pattern 2 — Sticky market ribbon at the very top

Yahoo Finance ships a thin ticker rail above the main content showing live
SPX / NDX / RUT / DJIA / VIX / 10Y with sparklines. **Always visible while
scrolling.** It's the "you're inside a market" anchor.

CNBC has a similar treatment with the LIVESTREAM chip + ticker.

**Our system today:** SPY/QQQ/VIX/regime live in the briefing card at the top
of one tab. They scroll out of view as soon as you go to Performance or
Strategy Lab. **Result: when reading P&L Readiness, you've forgotten what
the market is doing.**

**Action for v3.65.0:** sticky `st.container` ribbon at the top of every
view showing SPY %, QQQ %, VIX, regime label. Maybe `st.sidebar` is wrong
home for these — they belong in a horizontal strip that stays put.

---

## Pattern 3 — Per-symbol left rail on stock pages

TipRanks, Yahoo Finance, and Nasdaq all use a vertical left rail when the
user lands on a single stock. The rail shows ALL the lenses available for
that one ticker:

- Overview / Summary
- Analyst Forecasts
- AI Stock Analysis (TipRanks calls it this — explicit "AI" branding)
- Dividends
- Earnings
- Ownership
- Financials
- Statistics
- Technical Analysis
- Historical Prices
- News & Insights
- Chart

**Our system today:** drill-down opens a modal with 4 inline tabs (Overview /
Risk / Trade / Notes). That's fewer lenses than TipRanks ships out of the
box.

**Action for v3.65.0:** add 3 more tabs to the symbol modal:
- **AI Analysis** (HANK summary already exists — promote from a section
  inside Overview to its own tab)
- **News** (from `news_sources` filtered by ticker)
- **Earnings** (next earnings date + last 4 surprises from
  `earnings_calendar`)

Then we have 7 lenses, comparable to TipRanks 12 but covering the most-asked.

---

## Pattern 4 — Timeframe chips are universal

Every platform has the same 6-9 chips above any chart:
**1D / 5D / 1M / 3M / 6M / YTD / 1Y / 5Y / All** (Nasdaq has all 9; Yahoo
omits some; CNBC has all 9; TipRanks has 3m/5d/3m/6m/YTD/1y/3y/5y).

**Our system today:** Performance view defaults to 90D and offers "Last 30 /
60 / 90 / 180 / 365 days" via a selectbox. Functional but unfamiliar.

**Action for v3.65.0:** replace the selectbox with horizontal buttons
(`st.columns` + `st.button`) labeled `1D 5D 1M 3M 6M YTD 1Y 5Y ALL`. Match
industry convention.

---

## Pattern 5 — AI is FRONT and CENTER

| Platform | AI surface |
|---|---|
| Composer.trade | "Build trading algorithms with AI" — entire homepage hero. AI = the product. |
| TipRanks | "Ask Samuel AI" floating button bottom-right of every page. Plus dedicated "AI Stock Analysis" tab. |
| Yahoo Finance | No AI surface (still catching up) |
| CNBC | No AI surface |
| Nasdaq.com | No AI surface |

The AI-native platforms (Composer + TipRanks) treat AI as a first-class
citizen, not a feature buried in a tab. The legacy platforms have nothing.

**Our system today:** HANK chat lives in its own tab. The per-symbol HANK
summary lives inside a drill-down modal. No floating "ask HANK" button on
every view.

**Action for v3.65.0:** floating "🧠 Ask HANK" button bottom-right via
`st.fab` pattern (or fixed-position container). Click → opens chat in a
sidepanel pre-loaded with context for whatever view you're on (e.g., on
Strategy Lab, the chat starts with "I'm on the Strategy Lab page; ask me
about a strategy"). This is the TipRanks Samuel pattern.

---

## Pattern 6 — Big block whitespace, not table density

CNBC, Yahoo, Nasdaq all use **lots of whitespace** between sections. They
treat each metric as a card with breathing room. Counterintuitively, this
makes the pages feel more authoritative — not less informative.

The DENSE platforms (Bloomberg Terminal, Refinitiv) are for professionals
who tolerate it for speed. The CONSUMER platforms have evolved away from
density because density correlates with anxiety, not insight.

**Our system today:** Strategy Lab shows a 31-row dataframe with 8 columns.
That's bloomberg-terminal dense but we're a consumer-facing dashboard.

**Action for v3.65.0:** Strategy Lab gets a default "card view" — top 3
LIVE strategies as full-width cards with status, plain-English description,
and a CTA. Below the fold: the existing dense table for power users.
Toggle = `st.radio("View", ["Cards", "Table"], horizontal=True)`.

---

## Pattern 7 — Color discipline (only for direction)

Across all 5 platforms:
- **Green = up / good**
- **Red = down / bad**
- **All other UI: black, white, gray, occasional brand blue**

NO platform uses purple/orange/yellow for non-directional info in the
headline. Color is a signal, not decoration.

**Our system today:** we use yellow/orange chips for SHADOW status, blue for
LIVE, magenta accents in some places. **This dilutes the green/red signal.**

**Action for v3.65.0:** audit the dashboard's color palette. Reserve
green/red for P&L direction ONLY. Everything else: shades of gray + one
brand color (call it HANK-blue #2563eb). Use chip BACKGROUND tints, not
chip FILL colors, for status (LIVE/SHADOW/REFUTED).

---

## Pattern 8 — News feed is below-the-fold, ticker-filtered

CNBC and Yahoo both place "Latest on Apple Inc" UNDER the price/chart.
Headlines are hyperlinks with a date stamp. Click → opens article in a
new tab. Source attribution is minimal (small gray text after the
headline).

**Our system today:** News tab is a separate top-level item. The per-symbol
modal doesn't show news for that symbol.

**Action for v3.65.0:** News tab in the per-symbol modal (covered in
Pattern 3 above) plus: News view itself gets a "Filter by symbol"
multi-select at the top, defaulting to current LIVE positions.

---

## Pattern 9 — A "what changed since last visit" cue

Yahoo Finance shows "Edited: 2 hours ago" timestamps on its news. CNBC
shows MAY 2, 2026 datelines. Nasdaq shows "Apr 30, 2026" next to the
price. **All platforms communicate freshness explicitly.**

**Our system today:** the briefing card shows timestamp at the bottom in
small gray text. Easy to miss. P&L Readiness has no "as of" badge.

**Action for v3.65.0:** every numerical card gets a small "as of {time}"
suffix in 10px gray. If the data is more than 15 min stale, the suffix
turns yellow. If more than 60 min, red.

---

## Pattern 10 — Discoverable but not noisy: search is a visible pill

| Platform | Search treatment |
|---|---|
| Yahoo | Top-center search bar, persistent, autocomplete |
| Nasdaq | Top-right search pill with magnifying glass |
| TipRanks | Top-left search bar with placeholder "Search" |
| CNBC | Top-right magnifying glass icon (collapsed) |

**Our system today:** the cmd_bar lives in the sidebar. It is not visible
unless the user's eye flicks left.

**Action for v3.65.0:** cmd_bar moves to a fixed position at the top of the
main content area. Looks like a search bar. Placeholder: "Search symbols,
strategies, or ask HANK…"

---

## Synthesis — concrete v3.65.0 punch list

Ranked by leverage (effort × impact). Each is 1-30 LOC.

| # | Change | LOC | Impact |
|---|---|---|---|
| 1 | Sticky market-ribbon container above every view | ~25 | High — anchor user in market context everywhere |
| 2 | Bigger price headline in briefing (HTML span styling) | ~10 | High — matches Nasdaq/CNBC/Yahoo pattern |
| 3 | Floating "Ask HANK" button bottom-right (fixed position) | ~30 | High — matches TipRanks Samuel pattern |
| 4 | Symbol modal: add News + Earnings + AI Analysis tabs | ~60 | High — closes lens gap vs TipRanks |
| 5 | Timeframe chips on Performance (`1D 5D 1M 3M 6M YTD 1Y 5Y ALL`) | ~20 | Medium — familiarity for traders |
| 6 | Strategy Lab card view + Table view toggle | ~40 | Medium — discoverability for casual users |
| 7 | "as of {time}" stale-data badge | ~15 | Medium — trust + transparency |
| 8 | cmd_bar moves to top of main content as a search-bar-styled pill | ~20 | Medium — discoverability |
| 9 | Color audit: reserve green/red for direction only | ~50 | Low — polish, not behavior change |
| 10 | News view: per-symbol filter defaulting to live positions | ~15 | Low — incremental usefulness |

**Total: ~285 LOC for a v3.65.0 push that closes the most-visible UX gaps.**

---

## What we won't copy

- **Ad slots** (Yahoo / CNBC / Nasdaq) — we're a personal tool, no monetization pressure
- **Sponsored content** ("How Savvy Investors Pay…") — pollutes signal
- **Premium upsell banners** (TipRanks "Unlock with Plus") — we're not a SaaS yet
- **Forced cookie modals** that block content — bad pattern
- **Bloomberg-terminal density** — we serve a different user (Roth IRA owner, not trading desk)

---

## Open question — do we hire a designer?

The above list ships in a weekend. But the BIGGER question is whether we
should hire a contract designer to do a real design system before
productizing (per `PRODUCTIZATION_ROADMAP.md`). My take: the v3.65.0 punch
list is enough to validate with 5-10 friends-and-family users (per the
roadmap's recommendation). If they LIKE it, then invest in a designer for
the multi-tenant launch. Don't over-design before product-market fit.

---

*Last updated 2026-05-03.*
