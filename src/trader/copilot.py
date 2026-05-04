"""AI Copilot for the trader (v3.53.0).

The paradigm shift the dashboard had been missing. Conversational primary
surface. The AI has tools and uses them autonomously to answer anything.
Streaming response with thinking visible. Memory per session.

Pattern is Anthropic tool use (https://docs.anthropic.com/en/docs/build-with-claude/tool-use):
the model decides which tools to call, calls them, reads results, and
generates a final response. We define the tools as Python functions that
wrap existing modules (regime_overlay, sleeve_health, etc.).

Why chat-first instead of tabs:
  - Trader workflow is question-driven ("why am I down today?",
    "what's my exposure to FOMC?") not data-browse driven
  - The 14 existing tabs are still useful as REFERENCE views, but the
    AI can answer most questions without the user clicking through them
  - LLMs are uniquely suited to combining data across multiple sources
    (journal + live portfolio + regime overlay + research docs) into a
    coherent answer
  - The verifier (agent_verifier.py) gates any claim that cites research

This module exposes:
  - TOOLS: list of tool definitions for the Anthropic API
  - dispatch_tool(name, args): execute one tool call, return JSON-serializable result
  - SYSTEM_PROMPT: the copilot's persona + grounding in our docs

The dashboard layer wires this to a chat UI with streaming.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Any, Optional

from .config import DATA_DIR, DB_PATH


ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = os.getenv("COPILOT_MODEL", "claude-sonnet-4-6")


SYSTEM_PROMPT = """You are HANK — the trading copilot for a personal automated equity
trading system operated by Richard Chen. The system trades a Roth IRA via
Alpaca paper today, planned for Public.com live deployment at day 90 of paper
validation.

YOUR PERSONA:
  - Name: HANK (Honest Analytical Numerical Kopilot — yes, the K is on purpose)
  - Voice: tight, specific, numerate. Talks like a senior quant analyst, not
    a chatbot. Uses concrete numbers + sources, not adjectives.
  - Honesty discipline: when evidence is thin, say so. When a claim is
    refuted on our backtest, refuse to recommend the underlying strategy
    even if it's well-published.
  - Never starts a response with "Great question!" or other filler.
  - Uses Github-flavored markdown; never emojis unless they carry information
    (✅ verified / ❌ refuted / ⚠️ caveat).

Current LIVE strategy: `momentum_top15_mom_weighted_v1` — top-15 by 12-1
momentum, momentum-weighted, 80% gross. PIT-honest expected Sharpe ~0.96,
CAGR ~19%, worst-DD ~-33%.

YOUR JOB:
  - Answer questions about the portfolio, decisions, regime, performance
  - Use the tools available to you autonomously — don't ask permission to
    query data; just do it and present findings
  - When the user asks open-ended questions ("why am I down?", "should I
    rebalance?"), break them into sub-questions and call multiple tools
  - Surface contradictions or surprises in the data; don't smooth them over
  - Cite specific values and timestamps. Numbers without sources are noise.

YOU CANNOT:
  - Place orders. The user explicitly asks the broker for any trade.
  - Modify the LIVE variant. That requires manual code change + override-
    delay 24h cool-off + adversarial review per docs/SWARM_VERIFICATION_PROTOCOL.md
  - Cite academic papers without verbatim quotes from a real source
  - Recommend more frequent trading. Strategy is monthly rebalance by design.
    Per docs/CRITIQUE.md, overtrading is the #1 retail blow-up mode.

KEY CONTEXT:
  - 4-layer defense: code / custodian / human / document. Don't suggest
    bypasses to any layer.
  - 3-gate promotion: survivor → PIT → CPCV. Any new strategy claim must
    cite which gates it has passed.
  - Honest performance numbers vs in-sample: PIT Sharpe 0.96 (not 1.16),
    expected DD -33% (not -27%). Don't oversell.
  - 40+ killed candidates documented in docs/CRITIQUE.md. Don't re-propose
    things that have failed CPCV.
  - Agent output that informs trading decisions must pass TRUST/VERIFY/
    ABSTAIN gate per docs/SWARM_VERIFICATION_PROTOCOL.md

STYLE:
  - Concise. The user is technical. Skip preambles like "That's a great
    question!"
  - Lead with the answer. Then the reasoning. Then the data.
  - Show your tool calls and what they returned, briefly. The user wants
    to see your work.
  - When you're uncertain, say so. Saying "I don't know" earns trust.
  - When asked about v4 / future direction, reference docs/V4_PARADIGM_SHIFT.md
  - When asked about a strategy candidate, check docs/CRITIQUE.md for prior failures.

GREET on the first message of a session with: a 2-3 line briefing of
"things to know today" pulled from get_portfolio_status + get_regime_state
+ get_upcoming_events. Then ask what they want to dig into.
"""


# ============================================================
# Tool implementations
# ============================================================

def _conn_ro() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def tool_get_portfolio_status() -> dict:
    """Live portfolio: equity, cash, positions with day P&L."""
    try:
        from .positions_live import fetch_live_portfolio
        pf = fetch_live_portfolio()
        return {
            "equity": pf.equity,
            "cash": pf.cash,
            "buying_power": pf.buying_power,
            "total_unrealized_pl": pf.total_unrealized_pl,
            "total_day_pl_dollar": pf.total_day_pl_dollar,
            "total_day_pl_pct": pf.total_day_pl_pct,
            "n_positions": len(pf.positions),
            "positions": [{
                "symbol": p.symbol, "qty": p.qty,
                "weight_pct": (p.weight_of_book or 0) * 100,
                "day_pl_pct": (p.day_pl_pct or 0) * 100,
                "unrealized_pl_pct": (p.unrealized_pl_pct or 0) * 100,
                "sector": p.sector,
            } for p in pf.positions],
            "error": pf.error,
            "timestamp": pf.timestamp,
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def tool_get_regime_state() -> dict:
    """Live HMM + macro + GARCH regime overlay state."""
    try:
        from .regime_overlay import compute_overlay
        sig = compute_overlay()
        return {
            "hmm_regime": sig.hmm_regime,
            "hmm_posterior": sig.hmm_posterior,
            "hmm_mult": sig.hmm_mult,
            "macro_curve_inverted": sig.macro_curve_inverted,
            "macro_credit_widening": sig.macro_credit_widening,
            "macro_mult": sig.macro_mult,
            "garch_vol_forecast_annual": sig.garch_vol_forecast_annual,
            "garch_mult": sig.garch_mult,
            "final_mult": sig.final_mult,
            "enabled": sig.enabled,
            "rationale": sig.rationale,
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def tool_get_recent_decisions(n: int = 20) -> dict:
    """Last N decisions from journal with rationale + final action."""
    try:
        n = max(1, min(int(n), 200))
        with _conn_ro() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT ts, ticker, action, style, score, rationale_json, final "
                "FROM decisions ORDER BY ts DESC LIMIT ?", (n,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("rationale_json"):
                try:
                    d["rationale"] = json.loads(d["rationale_json"])
                except Exception:
                    d["rationale"] = d["rationale_json"]
                d.pop("rationale_json", None)
            out.append(d)
        return {"n_returned": len(out), "decisions": out}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def tool_get_attribution_today() -> dict:
    """Today's Brinson PnL decomposition (allocation + selection effects)."""
    try:
        from .positions_live import fetch_live_portfolio
        from .brinson_attribution import compute_brinson, SECTOR_ETF_MAP
        pf = fetch_live_portfolio()
        if not pf.positions:
            return {"error": "no positions"}
        # Aggregate sector weights + cap-weighted sector returns
        sec_w_p, sec_r_num, sec_r_den = {}, {}, {}
        total_eq = sum((p.market_value or 0) for p in pf.positions) or 1
        for p in pf.positions:
            sec = p.sector or "Unknown"
            w = (p.market_value or 0) / total_eq
            sec_w_p[sec] = sec_w_p.get(sec, 0) + w
            if p.day_pl_pct is not None and (p.market_value or 0) > 0:
                sec_r_num[sec] = sec_r_num.get(sec, 0) + p.day_pl_pct * (p.market_value or 0)
                sec_r_den[sec] = sec_r_den.get(sec, 0) + (p.market_value or 0)
        sec_r_p = {s: (sec_r_num.get(s, 0) / sec_r_den.get(s, 1)) for s in sec_w_p}
        # Equal-weight benchmark placeholder
        n = len(SECTOR_ETF_MAP) or 1
        sec_w_b = {s: 1.0 / n for s in SECTOR_ETF_MAP}
        # Try to fetch benchmark sector ETF day returns
        try:
            import yfinance as yf
            etfs = list(SECTOR_ETF_MAP.values())
            df = yf.download(" ".join(etfs), period="5d", progress=False, auto_adjust=True, group_by="ticker")
            sec_r_b = {}
            for sec, etf in SECTOR_ETF_MAP.items():
                try:
                    closes = df[(etf, "Close")].dropna() if (etf, "Close") in df.columns else df[etf]["Close"].dropna()
                    if len(closes) >= 2:
                        sec_r_b[sec] = (float(closes.iloc[-1]) - float(closes.iloc[-2])) / float(closes.iloc[-2])
                except Exception:
                    continue
        except Exception:
            sec_r_b = {}
        rep = compute_brinson(sec_w_p, sec_r_p, sec_w_b, sec_r_b)
        return rep.to_dict()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def tool_get_sleeve_health() -> dict:
    """Cross-sleeve correlation + per-sleeve rolling Sharpe + decay flags."""
    try:
        from .sleeve_health import compute_health
        rep = compute_health()
        return rep.to_dict()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def tool_get_upcoming_events(days_ahead: int = 14) -> dict:
    """FOMC + OPEX + earnings + ex-div for held names in next N days."""
    try:
        from .positions_live import fetch_live_portfolio
        from .events_calendar import compute_upcoming_events
        pf = fetch_live_portfolio()
        symbols = [p.symbol for p in (pf.positions or [])]
        events = compute_upcoming_events(symbols, days_ahead=int(days_ahead))
        return {
            "n_events": len(events),
            "events": [{
                "date": str(e.date), "days_until": e.days_until,
                "type": e.event_type, "symbol": e.symbol, "note": e.note,
                "confidence": e.confidence,
            } for e in events],
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def tool_query_journal(sql: str, limit: int = 100) -> dict:
    """Read-only SQL against journal. SELECT only."""
    sql_lower = sql.strip().lower()
    if not sql_lower.startswith("select"):
        return {"error": "only SELECT statements allowed"}
    if any(bad in sql_lower for bad in ("insert", "update", "delete", "drop",
                                          "alter", "create", "replace", "truncate")):
        return {"error": "write statements blocked"}
    try:
        limit = max(1, min(int(limit), 500))
        with _conn_ro() as c:
            c.row_factory = sqlite3.Row
            # Append LIMIT if not present
            if "limit " not in sql_lower:
                sql = sql.rstrip(";") + f" LIMIT {limit}"
            rows = [dict(r) for r in c.execute(sql).fetchall()]
        return {"n_rows": len(rows), "rows": rows}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def tool_get_postmortem_history(n: int = 5) -> dict:
    """Last N post-mortem self-reviews from the journal."""
    try:
        n = max(1, min(int(n), 50))
        with _conn_ro() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT date, pnl_pct, summary, proposed_tweak FROM postmortems "
                "ORDER BY date DESC LIMIT ?", (n,)
            ).fetchall()
        return {"n_returned": len(rows), "postmortems": [dict(r) for r in rows]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def tool_compute_scenario(symbol: str, pct_move: float) -> dict:
    """What-if: if SYMBOL moves by pct_move (e.g. -0.10 for -10%), what's the
    portfolio P&L impact? Pure math — no real trade."""
    try:
        from .positions_live import fetch_live_portfolio
        pf = fetch_live_portfolio()
        if not pf.positions:
            return {"error": "no positions"}
        pct = float(pct_move)
        impacted = None
        for p in pf.positions:
            if p.symbol.upper() == symbol.upper():
                impacted = p
                break
        if not impacted or not impacted.market_value:
            return {"error": f"{symbol} not in current portfolio"}
        dollar_impact = impacted.market_value * pct
        portfolio_impact_pct = dollar_impact / pf.equity if pf.equity else None
        return {
            "symbol": symbol,
            "pct_move_simulated": pct,
            "position_market_value": impacted.market_value,
            "position_weight_pct": (impacted.weight_of_book or 0) * 100,
            "dollar_impact": dollar_impact,
            "portfolio_impact_pct": portfolio_impact_pct * 100 if portfolio_impact_pct else None,
            "new_equity": pf.equity + dollar_impact if pf.equity else None,
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def tool_read_filings(query: str, symbol: str = "",
                       limit: int = 5) -> dict:
    """Search the on-disk SEC filings archive for `query` (substring,
    case-insensitive). Returns matching filings + ±300-char context
    around each hit. Idempotent + cheap (no LLM call here)."""
    try:
        from . import filings_archive
        sym = symbol.strip().upper() if symbol else None
        limit = max(1, min(int(limit or 5), 20))
        matches = filings_archive.search(query, symbol=sym, limit=limit)
        out = []
        q_lower = query.lower()
        for f in matches:
            text = filings_archive.read_text(f.accession) or ""
            idx = text.lower().find(q_lower)
            context = ""
            if idx >= 0:
                start = max(0, idx - 300)
                end = min(len(text), idx + len(query) + 300)
                context = text[start:end]
            out.append({
                "symbol": f.symbol, "form_type": f.form_type,
                "accession": f.accession, "filed_at": f.filed_at,
                "items": f.items, "url": f.url,
                "n_chars": f.n_chars,
                "context": context,
            })
        return {"query": query, "n_matches": len(out), "filings": out}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def tool_get_earnings_signals(symbol: str = "", since_days: int = 30,
                                min_materiality: int = 1) -> dict:
    """Recent Claude-flagged earnings signals from the reactor."""
    try:
        from . import earnings_reactor
        sym = symbol.strip().upper() if symbol else None
        rows = earnings_reactor.recent_signals(
            since_days=int(since_days), symbol=sym, limit=100,
        )
        rows = [r for r in rows if (r.get("materiality") or 0) >= int(min_materiality)]
        return {"n_signals": len(rows), "signals": rows}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def tool_summarize_period(start_date: str, end_date: str) -> dict:
    """Performance summary between two ISO dates (YYYY-MM-DD)."""
    try:
        with _conn_ro() as c:
            c.row_factory = sqlite3.Row
            snaps = c.execute(
                "SELECT date, equity, cash FROM daily_snapshot "
                "WHERE date BETWEEN ? AND ? ORDER BY date", (start_date, end_date)
            ).fetchall()
            decisions = c.execute(
                "SELECT COUNT(*) as n FROM decisions WHERE ts BETWEEN ? AND ?",
                (start_date, end_date)
            ).fetchone()
            orders = c.execute(
                "SELECT COUNT(*) as n FROM orders WHERE status='submitted' "
                "AND ts BETWEEN ? AND ?",
                (start_date, end_date)
            ).fetchone()
        if not snaps:
            return {"error": f"no daily_snapshot rows between {start_date} and {end_date}"}
        first, last = snaps[0], snaps[-1]
        return {
            "period_start": first["date"], "period_end": last["date"],
            "n_snapshots": len(snaps),
            "start_equity": first["equity"], "end_equity": last["equity"],
            "total_return_pct": ((last["equity"] - first["equity"]) / first["equity"] * 100
                                  if first["equity"] else None),
            "n_decisions": decisions["n"], "n_orders": orders["n"],
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ============================================================
# Tool definitions for Anthropic API
# ============================================================

TOOLS = [
    {
        "name": "get_portfolio_status",
        "description": "Get live Alpaca portfolio: equity, cash, buying power, list of positions with per-name weight, day P&L %, total unrealized P&L %, sector. Use this for any question about current holdings or today's performance.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_regime_state",
        "description": "Get live HMM regime classification (bull/transition/bear) + macro stress signals (yield curve inversion, credit spread widening) + GARCH vol forecast. Use this for questions about market regime or what the overlay says about exposure.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_recent_decisions",
        "description": "Pull the last N decisions from the journal with timestamp, ticker, action, score, full rationale (parsed JSON), and final outcome. Use for 'why did we buy X?' or 'what happened on date Y?'",
        "input_schema": {"type": "object", "properties": {"n": {"type": "integer", "description": "How many recent decisions to fetch (default 20, max 200)"}}, "required": []},
    },
    {
        "name": "get_attribution_today",
        "description": "Brinson decomposition of today's P&L into allocation effect (sector over/underweighting) + selection effect (within-sector picks) + interaction. Returns per-sector breakdown. Use to answer 'where did today's P&L come from?'",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_sleeve_health",
        "description": "Cross-sleeve correlation matrix + per-sleeve rolling 90d Sharpe + auto-demote recommendations. Use for questions about strategy decay or whether sleeves are uncorrelated.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_upcoming_events",
        "description": "FOMC + OPEX + earnings dates + ex-div for currently held names in the next N days. Use for 'what's coming up that affects my book?' or 'are there earnings on any of my holdings this week?'",
        "input_schema": {"type": "object", "properties": {"days_ahead": {"type": "integer", "description": "Lookahead window (default 14, max 90)"}}, "required": []},
    },
    {
        "name": "query_journal",
        "description": "Run a read-only SELECT query against the SQLite journal. Tables: decisions (id, ts, ticker, action, style, score, rationale_json, final), orders (id, ts, ticker, side, notional, status, error), daily_snapshot (date, equity, cash), position_lots (symbol, sleeve, opened_at, qty, open_price, closed_at, close_price, realized_pnl), runs (run_id, started_at, completed_at, status, notes), postmortems (date, pnl_pct, summary, proposed_tweak), variants (variant_id, name, status, description), shadow_decisions (variant_id, ts, targets_json). Use for arbitrary structured questions the other tools can't answer.",
        "input_schema": {"type": "object", "properties": {"sql": {"type": "string", "description": "SELECT statement only. INSERT/UPDATE/DELETE blocked."}, "limit": {"type": "integer", "description": "Row limit (default 100, max 500)"}}, "required": ["sql"]},
    },
    {
        "name": "get_postmortem_history",
        "description": "Last N nightly post-mortems (Claude self-review of yesterday's decisions vs today's reaction). Each has date, pnl_pct, summary, and proposed_tweak.",
        "input_schema": {"type": "object", "properties": {"n": {"type": "integer", "description": "How many to fetch (default 5, max 50)"}}, "required": []},
    },
    {
        "name": "compute_scenario",
        "description": "What-if scenario: if SYMBOL moves by pct_move (e.g. -0.10 for -10%), what's the dollar P&L impact and portfolio % impact? Pure math, no real trade. Use for risk questions like 'what's my drawdown if NVDA gaps -10%?'",
        "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}, "pct_move": {"type": "number", "description": "Decimal: 0.05 = +5%, -0.10 = -10%"}}, "required": ["symbol", "pct_move"]},
    },
    {
        "name": "summarize_period",
        "description": "Performance summary between two ISO dates (YYYY-MM-DD): start equity, end equity, total return %, n decisions, n orders submitted.",
        "input_schema": {"type": "object", "properties": {"start_date": {"type": "string"}, "end_date": {"type": "string"}}, "required": ["start_date", "end_date"]},
    },
    {
        "name": "read_filings",
        "description": "Search the on-disk SEC filings archive (8-K / 10-Q / 10-K) for a symbol and substring. Returns matching filing snippets with accession + filed_at + matched context (±300 chars around hit). Use for 'what did NVDA say about supply chain in Q3?' or 'has GOOGL mentioned regulation in any filing this year?'. The archive is populated by `python scripts/earnings_reactor.py` from SEC EDGAR.",
        "input_schema": {"type": "object", "properties": {"symbol": {"type": "string", "description": "Ticker. Optional — leave empty to search all archived symbols."}, "query": {"type": "string", "description": "Substring to find (case-insensitive). E.g. 'guidance', 'supply chain', 'AI capex'."}, "limit": {"type": "integer", "description": "Max number of matching filings (default 5, max 20)"}}, "required": ["query"]},
    },
    {
        "name": "get_earnings_signals",
        "description": "Recent Claude-extracted earnings signals from the reactor: per-position direction (BULLISH/NEUTRAL/BEARISH/SURPRISE), materiality 1-5, guidance change, surprise direction, summary, bullish/bearish quotes. Use for 'any material earnings news on my book this month?'.",
        "input_schema": {"type": "object", "properties": {"symbol": {"type": "string", "description": "Optional ticker filter"}, "since_days": {"type": "integer", "description": "Lookback in days (default 30)"}, "min_materiality": {"type": "integer", "description": "Filter to signals with materiality ≥ N (default 1)"}}, "required": []},
    },
]


_TOOL_DISPATCH = {
    "get_portfolio_status": lambda args: tool_get_portfolio_status(),
    "get_regime_state": lambda args: tool_get_regime_state(),
    "get_recent_decisions": lambda args: tool_get_recent_decisions(**args),
    "get_attribution_today": lambda args: tool_get_attribution_today(),
    "get_sleeve_health": lambda args: tool_get_sleeve_health(),
    "get_upcoming_events": lambda args: tool_get_upcoming_events(**args),
    "query_journal": lambda args: tool_query_journal(**args),
    "get_postmortem_history": lambda args: tool_get_postmortem_history(**args),
    "compute_scenario": lambda args: tool_compute_scenario(**args),
    "summarize_period": lambda args: tool_summarize_period(**args),
    "read_filings": lambda args: tool_read_filings(**args),
    "get_earnings_signals": lambda args: tool_get_earnings_signals(**args),
}


# v3.57.1 (Phase 3): Tool tiers — Cursor/Replit-style sandboxing.
# Today every tool is read_only. compute_scenario is pure-math what-if (sim).
# When chat-driven trade approval lands, place_order/modify_variant become "live".
TOOL_TIERS: dict[str, str] = {
    "get_portfolio_status": "read_only",
    "get_regime_state": "read_only",
    "get_recent_decisions": "read_only",
    "get_attribution_today": "read_only",
    "get_sleeve_health": "read_only",
    "get_upcoming_events": "read_only",
    "query_journal": "read_only",
    "get_postmortem_history": "read_only",
    "compute_scenario": "sim",
    "summarize_period": "read_only",
    "read_filings": "read_only",
    "get_earnings_signals": "read_only",
}


def tier_of(tool_name: str) -> str:
    """Returns 'read_only' / 'sim' / 'live' / 'unknown'."""
    return TOOL_TIERS.get(tool_name, "unknown")


def dispatch_tool(name: str, args: dict) -> Any:
    """Execute one tool call and return its JSON-serializable result.
    Returns {"error": "..."} on unknown name or dispatch failure.
    """
    fn = _TOOL_DISPATCH.get(name)
    if not fn:
        return {"error": f"unknown tool: {name}"}
    try:
        return fn(args or {})
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ============================================================
# Conversation loop with streaming + tool use
# ============================================================

def translate_nl_to_sql(question: str) -> dict:
    """v3.57.1 (Phase 8 — NL screener): translate plain-English to a SELECT.

    Returns {"sql": "...", "explanation": "..."} on success or {"error": "..."}.
    Uses Claude as a one-shot SQL translator with the journal schema baked in.
    SELECT-only. Caller must still run via tool_query_journal which enforces
    the read-only mode at the SQLite layer.
    """
    if not ANTHROPIC_API_KEY:
        return {"error": "ANTHROPIC_API_KEY not set"}
    try:
        from anthropic import Anthropic
    except ImportError:
        return {"error": "anthropic SDK not installed"}

    schema_brief = (
        "Tables (all SELECT-only):\n"
        "- decisions(id, ts, ticker, action, style, score, rationale_json, final)\n"
        "- orders(id, ts, ticker, side, notional, alpaca_order_id, status, error)\n"
        "- daily_snapshot(date, equity, cash, positions_json)\n"
        "- position_lots(id, symbol, sleeve, opened_at, qty, open_price, "
        "closed_at, close_price, realized_pnl)\n"
        "- runs(run_id, started_at, completed_at, status, notes)\n"
        "- postmortems(id, date, pnl_pct, summary, proposed_tweak)\n"
        "- variants(variant_id, name, version, status, description)\n"
        "- shadow_decisions(id, variant_id, ts, targets_json, rationale)\n"
        "Date columns are ISO strings. Use SQLite syntax. SELECT only."
    )
    sys = (
        "You translate natural-language analytics questions into a single SQLite "
        "SELECT statement against the trader journal. Output ONLY a JSON object "
        '{"sql": "SELECT ...", "explanation": "<one sentence>"}. No prose, no '
        "markdown. SELECT-only. If the question cannot be answered from the "
        'schema, return {"error": "<one sentence why>"} instead.\n\n'
        + schema_brief
    )
    try:
        client = Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=sys,
            messages=[{"role": "user", "content": question}],
        )
        text = "".join(
            getattr(b, "text", "") for b in resp.content
            if getattr(b, "type", None) == "text"
        ).strip()
        # Strip optional code fences
        if text.startswith("```"):
            text = text.strip("`").lstrip("json").strip()
        parsed = json.loads(text)
        if "error" in parsed:
            return {"error": parsed["error"]}
        sql = parsed.get("sql", "").strip()
        if not sql.upper().startswith("SELECT"):
            return {"error": "translator produced non-SELECT output"}
        return {"sql": sql, "explanation": parsed.get("explanation", "")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _build_system_prompt() -> str:
    """v3.57.1: append user-editable memory file to the base system prompt.
    Cross-session preferences persist via data/copilot_memory.md."""
    base = SYSTEM_PROMPT
    try:
        from .copilot_memory import read_memory
        memory = read_memory()
        if memory and memory.strip():
            base = base + "\n\n=== USER MEMORY (from data/copilot_memory.md) ===\n" + memory
    except Exception:
        pass
    return base


def stream_response(messages: list[dict],
                     max_tool_iterations: int = 8,
                     plan_mode: bool = False):
    """Generator yielding events from one chat turn.

    Each event is a dict with one of:
      {"type": "text_delta", "text": "..."}        — streaming model output
      {"type": "tool_use_start", "name": "...", "input": {...}}
      {"type": "tool_result", "name": "...", "result": {...}}
      {"type": "complete", "messages": [...]}     — final messages list
      {"type": "error", "error": "..."}
      {"type": "plan_blocked", "name": "...", "tier": "..."} — emitted when
        plan_mode=True and the model tried to call a sim/live tool. The tool
        is NOT executed; instead a stub result is fed back to the model so it
        can describe the plan in natural language.

    Caller (the dashboard) renders these in real-time. Conversation history
    survives in the returned messages list.

    plan_mode (Phase 3): when True, sim/live-tier tools are stubbed with a
    "would call X with args Y" placeholder. read_only tools still run so the
    plan stays grounded in real numbers. Default False = full execute mode.
    """
    if not ANTHROPIC_API_KEY:
        yield {"type": "error", "error": "ANTHROPIC_API_KEY not set"}
        return
    try:
        from anthropic import Anthropic
    except ImportError:
        yield {"type": "error", "error": "anthropic SDK not installed"}
        return

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    iteration = 0
    while iteration < max_tool_iterations:
        iteration += 1
        accumulated_text = ""
        tool_uses = []  # collected for this assistant turn
        try:
            with client.messages.stream(
                model=MODEL,
                max_tokens=4096,
                system=_build_system_prompt(),
                tools=TOOLS,
                messages=messages,
            ) as stream:
                for event in stream:
                    if event.type == "content_block_start":
                        block = getattr(event, "content_block", None)
                        if block and getattr(block, "type", None) == "tool_use":
                            tool_uses.append({
                                "id": block.id, "name": block.name,
                                "input": {},
                            })
                    elif event.type == "content_block_delta":
                        d = getattr(event, "delta", None)
                        if d and getattr(d, "type", None) == "text_delta":
                            text = getattr(d, "text", "")
                            accumulated_text += text
                            yield {"type": "text_delta", "text": text}
                    elif event.type == "message_stop":
                        break
                final = stream.get_final_message()
        except Exception as e:
            yield {"type": "error", "error": f"{type(e).__name__}: {e}"}
            return

        # Extract tool_use blocks from final message
        tool_use_blocks = [b for b in final.content if getattr(b, "type", None) == "tool_use"]
        if not tool_use_blocks:
            # No tool calls — we're done. Append assistant turn and yield complete.
            messages.append({"role": "assistant", "content": final.content})
            # v3.64.0: log to compliance audit trail. Best-effort; never
            # blocks the LIVE chat path.
            try:
                from .llm_audit import log_llm_call
                user_input = ""
                for m in messages:
                    if m.get("role") == "user" and isinstance(m.get("content"), str):
                        user_input = m["content"]
                response_text = "".join(
                    getattr(b, "text", "") for b in final.content
                    if getattr(b, "type", None) == "text"
                )
                log_llm_call(
                    context="copilot_chat",
                    user_input=user_input[-500:],  # last user msg
                    response_text=response_text,
                    model=MODEL,
                    tools_called=[],  # this branch had no tool calls
                    influenced_trade=False,
                    input_tokens=getattr(final.usage, "input_tokens", 0) if hasattr(final, "usage") else 0,
                    output_tokens=getattr(final.usage, "output_tokens", 0) if hasattr(final, "usage") else 0,
                )
            except Exception:
                pass
            yield {"type": "complete", "messages": messages}
            return

        # Append the assistant turn (with tool_use blocks) to messages
        messages.append({"role": "assistant", "content": final.content})

        # Execute each tool call
        tool_results = []
        for tu in tool_use_blocks:
            yield {"type": "tool_use_start", "name": tu.name, "input": tu.input}
            tier = tier_of(tu.name)
            if plan_mode and tier in ("sim", "live"):
                yield {"type": "plan_blocked", "name": tu.name, "tier": tier}
                result = {
                    "plan_mode": True,
                    "tier": tier,
                    "would_call": tu.name,
                    "with_args": dict(tu.input or {}),
                    "note": (
                        "Plan mode: this tool was NOT executed. Describe the "
                        "intended action and what would change. The user must "
                        "exit plan mode and re-ask to actually run it."
                    ),
                }
            else:
                result = dispatch_tool(tu.name, dict(tu.input or {}))
            yield {"type": "tool_result", "name": tu.name, "result": result}
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": json.dumps(result, default=str)[:50000],  # cap large results
            })
        # Append the tool_result user turn
        messages.append({"role": "user", "content": tool_results})
        # Loop back — model will see tool results and continue

    yield {"type": "error", "error": f"max tool iterations ({max_tool_iterations}) exceeded"}
