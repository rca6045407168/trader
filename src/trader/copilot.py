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


SYSTEM_PROMPT = """You are the trading copilot for a personal automated equity trading system
operated by Richard Chen. The system trades a Roth IRA via Alpaca paper today,
planned for Public.com live deployment at day 90 of paper validation.

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
}


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

def stream_response(messages: list[dict],
                     max_tool_iterations: int = 8):
    """Generator yielding events from one chat turn.

    Each event is a dict with one of:
      {"type": "text_delta", "text": "..."}        — streaming model output
      {"type": "tool_use_start", "name": "...", "input": {...}}
      {"type": "tool_result", "name": "...", "result": {...}}
      {"type": "complete", "messages": [...]}     — final messages list
      {"type": "error", "error": "..."}

    Caller (the dashboard) renders these in real-time. Conversation history
    survives in the returned messages list.
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
                system=SYSTEM_PROMPT,
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
            yield {"type": "complete", "messages": messages}
            return

        # Append the assistant turn (with tool_use blocks) to messages
        messages.append({"role": "assistant", "content": final.content})

        # Execute each tool call
        tool_results = []
        for tu in tool_use_blocks:
            yield {"type": "tool_use_start", "name": tu.name, "input": tu.input}
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
