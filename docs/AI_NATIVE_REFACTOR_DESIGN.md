# AI-native trading dashboard — refactor design (v3.54+)

**Status:** research spike (2026-05-03). v3.54.0 ships Phase 0 (Copilot promoted to primary surface + auto-briefing). Phases 1–3 are roadmap.

User feedback that triggered this: *"this doesn't really look like an AI-powered GUI for trading."* Correct. We had 14 tabs of tables + a 15th chat tab. That's a 2018 SaaS admin panel pattern. This doc synthesizes a 3-agent research swarm (per `docs/SWARM_VERIFICATION_PROTOCOL.md`) into a real refactor plan.

## Research swarm summary (verified)

3 agents, 30 minutes, 30+ verified-source citations. Full findings persisted; key takeaways below.

### Agent 1 — AI-native trading platforms (Composer, Trade Ideas, Tickeron, Kavout, TipRanks, Bloomberg GenAI, Robinhood Cortex, TradingView)

Common patterns across 8 platforms:
1. **Score-first card on home (5/8)** — single AI-generated headline number, not a table. TipRanks Smart Score (1–10), Kavout Kai Score (1–9), Robinhood Cortex Digest.
2. **NL as screener language, not chat (3/8)** — Composer, Kavout, Robinhood Cortex scanner. User types "Large-cap stocks with P/E < 20 and Kai Score > 7" and gets a deterministic list, not a chat reply.
3. **Inline AI summaries on detail pages (3/8)** — Bloomberg explicitly chose this *over* chat. 3 bullets at top of every news/earnings page.
4. **Pre-market "morning brief" of AI-curated ideas** — Trade Ideas Holly, Robinhood Cortex Digests.
5. **Score decomposition for explainability** — NEVER a black-box number. TipRanks shows 8 component factors; Robinhood Digest shows "Market backdrop / Return drivers / Top movers."
6. **Visual graph / DSL output instead of opaque LLM trade** — Composer Symphony, Robinhood Trade Builder. LLM is a translator into a deterministic DSL, not an executor.
7. **Pure chat-replacement is industry-leading-edge but NOT shipped** — even Robinhood Cortex full chat is Q1 2026 announced, not GA. Hybrid (chat + dense data) is the verified-shipped middle.
8. **Branded AI persona** — Trade Ideas Holly, TipRanks Spark, Robinhood Cortex, Tickeron AI Robots.

### Agent 2 — Pro trader workstations (Bloomberg, IBKR TWS, Eikon, TradeStation, NinjaTrader, TradingView Pro, GS Marquee, Lightspeed)

What makes a tool feel PROFESSIONAL not RETAIL (9 patterns):
1. **Mnemonic / command-bar primary nav** — Bloomberg `mnemonic <GO>`, TradingView `Cmd+K`, Eikon autosuggest. Type-to-jump beats click-to-drill.
2. **User-composable named workspaces** — IBKR Mosaic tabs, NinjaTrader workspaces, Lightspeed page bar. The user, not the designer, decides what's on screen.
3. **Symbol/instrument link-coloring across panels** — IBKR "grouping blocks," NinjaTrader "instrument link." Change symbol in one panel, every same-color panel updates. **Single biggest pattern to copy.**
4. **Color discipline** — black/dark bg, narrow semantic palette (green up / red down / amber warning). No decorative gradients.
5. **Information density first** — 1,000+ data points visible. Bloomberg, Lightspeed, NinjaTrader DOM. "Whitespace for breathing room" is the retail tell.
6. **Hotkey vocabulary** — every action a trader does >10×/day has a 1–2-keystroke binding.
7. **Streaming by default, snapshot on demand** — retail tell is a Refresh button or 30s poll.
8. **Time-stamps on everything** — "as of HH:MM:SS" on every value.
9. **Domain-specific visualization replaces generic tables** — NinjaTrader vertical price-ladder DOM, TradeStation RadarScreen scriptable columns, Marquee visual derivative structurer.
10. **Scriptable user logic inside the surface** — EasyLanguage in RadarScreen columns, Pine Script in indicators. Dashboard becomes an extensible IDE.

### Agent 3 — AI agent UX patterns (ChatGPT, Claude.ai, Cursor, Copilot, Perplexity, Linear AI, Notion AI, Slack AI, Harvey, Hebbia, Glean, Sierra, Decagon, Replit)

12 AI-native patterns ranked for our personal trading dashboard:
1. **Conversation starters on empty state** — ChatGPT, Claude. NN/g calls this "highest discoverability ROI for genAI." ✅ Already shipped v3.53.0
2. **Inline citation pills with click-to-source** — Perplexity, Slack AI. Every claim from a tool call gets `[1]` linking to row/file/API response.
3. **Tool-call cards with collapsible reasoning** — Cursor, Copilot, Linear. Linear's "thinking state + timer at bottom" is cleanest. ✅ Already shipped v3.53.0
4. **Plan Mode before execution for risky ops** — Replit Plan Mode, Cursor Plan Mode. Any tool that places trades goes through plan-and-approve gate.
5. **Side-panel artifacts for charts/tables** — ChatGPT Canvas, Claude Artifacts, Cursor canvases. Don't render giant tables in chat — push to right panel.
6. **Follow-up question chips after each answer** — Perplexity "related searches." Halves typing burden.
7. **Cross-session memory + project context split** — Claude Memory (auto preferences) + Projects (per-strategy custom instructions).
8. **Spreadsheet/grid view for multi-asset analysis** — Hebbia Matrix. "Run this question across my entire watchlist" = grid not chat.
9. **Sandbox / approval gate distinction** — Cursor sandboxing. read-only / sim / live tool tiers.
10. **Hover-for-reasoning on every AI suggestion** — Linear Triage. Default view clean; power users get the trace.
11. **Workflow Builder / saved playbooks** — Harvey AOPs, Glean agent builder. Save "Morning Briefing" as named workflow.
12. **Observability log per session** — Decagon Watchtower, Copilot session log. Reviewable transcript for any AI suggestion that costs money.

## Where we are now (v3.54.0)

Already shipped from these patterns:
- ✅ **Conversation starters** (suggested prompts above chat input)
- ✅ **Tool-call cards** (expandable per turn)
- ✅ **Branded AI** ("Copilot," though could be more domain-specific)
- ✅ **Streaming with thinking visible** (text deltas + tool log)
- ✅ **System prompt encodes our strategy + 3-gate + critique constraints** (TRUST/VERIFY/ABSTAIN)
- ✅ **Auto-briefing on page load** — v3.54.0 Phase 0
- ✅ **Copilot promoted to primary surface above tabs** — v3.54.0 Phase 0
- ✅ **Multi-tool dispatch** (10 read-only tools, all via Anthropic tool use)

## Refactor roadmap

### Phase 0 — v3.54.0 (this commit) — promote Copilot to primary surface

- Auto-briefing computes on every page load (60s cache): equity / day P&L vs SPY / regime / freeze state / upcoming events / yesterday's post-mortem.
- Copilot chat moves above the tabs row, side-by-side with briefing.
- 4 suggested-prompt buttons one-click into the chat.
- 14 reference tabs persist below as secondary navigation for power users.

### Phase 1 — v3.55.0 — inline citations + side-panel artifacts (Patterns 2, 5)

- Tool results that produce tables (decisions, lots, events) render inline in chat as compact summaries with `[1][2][3]` citation pills.
- Pills click through to a right-side panel showing the full table.
- Charts (equity curve, attribution waterfall) render as Plotly artifacts in the side panel.
- Effort: ~6h. Streamlit limitation: no native side-panel; use `st.dialog` or right-column.

### Phase 2 — v3.56.0 — cross-session memory + workflow builder (Patterns 7, 11)

- Persist Copilot conversation to `data/copilot_sessions/<session_id>.json` so refresh doesn't reset.
- Add a "Memory" file (`data/copilot_memory.md`) the user edits manually with strategy preferences ("prefer daily candles," "investor type: long-only," etc.) — loaded into every system prompt.
- Workflow builder: user saves a named multi-tool query ("Morning Briefing," "Pre-Rebalance Check," "Post-Mortem Review") to `data/copilot_workflows/`. One-click invocation.
- Effort: ~8h.

### Phase 3 — v3.57.0 — plan mode + sandbox tier distinction (Patterns 4, 9)

- Distinguish tool tiers: `read_only` (current 10 tools — auto-run), `sim` (compute_scenario, simulated rebalance — confirm), `live` (place_order, modify_variant — explicit chat-confirmation).
- Plan Mode toggle: "plan" vs "execute." In plan mode, copilot describes what it WOULD do but doesn't call live tools. User approves → switches to execute.
- Adversarial-review gate (already shipped v3.51) auto-fires before any LIVE tool runs.
- Effort: ~6h. Note: today the trader doesn't HAVE live tools — trading is via cron, not chat. Phase 3 lays the foundation for chat-driven trade approvals.

### Phase 4 — v3.58.0 — command bar + hotkey vocabulary (Pro patterns 1, 6)

- `Cmd+K` opens a typeahead command bar (Streamlit hack: `st.text_input` with autosuggest from a workflow library + tool list).
- Hotkey shortcuts: `Alt+1..9` jumps to tab, `Alt+B` opens briefing, `Alt+C` focuses copilot input, `Alt+H` shows hotkey help.
- Effort: ~4h.

### Phase 5 — v3.59.0 — grid view for multi-asset queries (Pattern 8 — Hebbia Matrix)

- New "🗂️ Grid" tab: rows = each held position, columns = user-defined questions ("day P&L," "earnings date," "sector vs SPY today," "AI sentiment last 7d").
- Each cell auto-fills via Copilot tool call. Cells show provenance (which tool produced the value) on hover.
- Effort: ~10h. Requires async tool dispatch (currently sequential).

### Phase 6 — v3.60.0 — link-coloring across panels (Pro pattern 3)

- IBKR-style symbol-link coloring: pick a symbol in one tab, all coupled panels update. Streamlit's tab system limits this; would need a custom layout.
- Effort: ~12h. Requires moving away from `st.tabs()` toward a custom panel system.

### Phase 7 — v3.61.0 — information density mode (Pro pattern 5)

- A "compact" mode toggle that crushes whitespace, increases font density, restricts palette to Bloomberg-style amber/green/red on near-black background.
- Effort: ~6h.

### Phase 8 — v3.62.0 — NL screener (AI-native pattern 2 — Composer/Kavout)

- A dedicated NL search bar above the dashboard: "large-cap names with momentum > 0.8 and Sharpe > 1.0 over 30d." Copilot generates SQL, runs it, renders deterministic table.
- Becomes the universal command surface for the dashboard — most queries route through this rather than tabs.
- Effort: ~8h.

## Estimated total effort

| Phase | Description | Effort |
|---|---|---|
| 0 (shipped v3.54.0) | Copilot primary + briefing | done |
| 1 | Citations + side-panel artifacts | 6h |
| 2 | Cross-session memory + workflows | 8h |
| 3 | Plan mode + sandbox tiers | 6h |
| 4 | Command bar + hotkeys | 4h |
| 5 | Grid view (Hebbia Matrix) | 10h |
| 6 | Symbol link-coloring | 12h |
| 7 | Compact density mode | 6h |
| 8 | NL screener | 8h |
| **Total** | **Phases 1–8** | **60h** |

Four-week dedicated; ~2 months part-time alongside the v4 multi-strategy work.

## What's NOT in scope

- **Voice input (Whisper).** Cool but low ROI for solo trader.
- **Multi-user / multi-tenant.** Single-user system.
- **Mobile responsive.** Desktop-first.
- **Real-time WebSocket tick streams.** Strategy is monthly rebalance; over-engineering.
- **Public-facing chat (like Robinhood Cortex SMS).** Internal-only.
- **Voice agent (Sierra-style).** Not relevant.

## The honest assessment

What we have today (v3.54.0) is **already in the verified-shipped middle of the industry**. The 12 AI-native UX patterns we mapped show that no one has shipped chat-as-only-surface yet — even Robinhood Cortex is announced, not live. Our hybrid (chat-primary + 14 reference tabs) is exactly where Bloomberg, Robinhood, and Composer have landed.

The 8 phases above would push us from "middle of industry" to **leading edge** by mid-2026:
- Phases 1–2 = current Cursor / Claude.ai / Hebbia parity
- Phases 3–4 = Bloomberg/IBKR pro-trader feel
- Phases 5–6 = Hebbia Matrix + NinjaTrader composability
- Phases 7–8 = pro-density + NL screener (Composer-class)

Not all of this is required for value. Phases 0+1+2+3 alone (24h) gets us 80% of the perceived AI-nativeness.

## Verification trail

Research swarm produced 30+ citations with verbatim quotes from primary sources (Bloomberg press releases, Composer businesswire, Robinhood newsroom, IBKR docs, NinjaTrader blogs, Anthropic Claude help, Linear engineering blog, Hebbia blog, Cursor docs, GitHub Copilot docs, etc.). Per `docs/SWARM_VERIFICATION_PROTOCOL.md`:

- 8 of 8 trading platforms verified-real (Composer, Trade Ideas, Tickeron, Kavout, TipRanks, Bloomberg, Robinhood, TradingView)
- 8 of 8 pro workstations verified-real (Bloomberg, IBKR, Eikon, TradeStation, NinjaTrader, TradingView Pro, GS Marquee, Lightspeed)
- 14 of 14 AI agent platforms verified-real (ChatGPT, Claude, Cursor, Copilot, Perplexity, Linear, Notion, Slack, Harvey, Hebbia, Glean, Sierra, Decagon, Replit)
- Every claim cited a URL + verbatim quote per protocol
- 0 fabricated platforms (refusal-on-empty would have been acceptable; none occurred)
- Honest caveats noted: Composer + TipRanks homepages WebFetched 403; TradingView official AI integration is genuinely thin (per public docs)

Full agent transcripts saved in conversation log; this doc is the synthesized output.
