"""LLM narrative generator for the daily report.

Takes the structured run state (positions, P&L, decisions, market context)
and asks Claude to write three sections:
  1. WHY DECISIONS — explain in plain English why each meaningful decision was made
  2. SHORT-TERM FACTORS — what's affecting performance today / this week
  3. LONG-TERM FACTORS — what's the multi-month / multi-year picture

Cost: ~$0.01-0.02 per report (Sonnet, ~2k input + ~800 output tokens).
Falls back gracefully to None if API key missing or call fails.
"""
from __future__ import annotations

import os
from typing import Any


SYSTEM_PROMPT = """You are an honest quantitative trading analyst writing a daily portfolio commentary for a sophisticated retail investor (the system operator).

You have full numeric context. Write three sections:

1. WHY DECISIONS — explain why the system took (or didn't take) the decisions it did.
   - If orders were skipped as 'below_min', explain that current allocation already matches target within the rebalance threshold (no action needed).
   - If momentum picks rotated, explain what changed in the rankings.
   - If bottom-catch fired or didn't, explain why (signal threshold logic).
   - If risk gates tripped (vol scaling, drawdown), name the trigger.

2. SHORT-TERM FACTORS — what's affecting today and this week.
   - Reference today's SPY move, VIX level, and named events (FOMC tomorrow, earnings season, etc.) IF the data shows them.
   - **For any position that moved >2% in the OPPOSITE direction of SPY, USE web_search to find the catalyst.**
     Search query format: "<ticker> stock today" or "<ticker> news <today's date>"
     If you find a real catalyst (earnings miss, M&A, sector news, regulatory), state it concretely.
     Example: "AMD -4% likely on WSJ report that OpenAI missed revenue targets, sparking AI compute spending concerns."
   - Flag concentration risk if multiple positions move together for the same reason.
   - Be honest when moves are unexplained noise.

3. LONG-TERM FACTORS — multi-month and multi-year picture.
   - Reference the deflated-Sharpe-corrected expectation (10-12% CAGR, 0.5-0.7 Sharpe).
   - Note position aging, momentum strategy crowding, regime considerations.
   - Highlight what would change the long-term thesis (e.g., "regime change if SPY breaks 200d MA").

Style:
- Plain English, no jargon
- 3-5 sentences per section
- No hedging fluff ("could", "might", "may"). Be direct: "The system did X because Y."
- No emoji
- Don't repeat the numeric data; reference it analytically
- It's OK to say "no notable factors today" if true

Format your response as exactly three sections separated by blank lines:

WHY DECISIONS
<text>

SHORT-TERM FACTORS
<text>

LONG-TERM FACTORS
<text>"""


def generate_narrative(state: dict[str, Any]) -> str | None:
    """Call Claude with structured state. Returns formatted three-section text or None on failure.

    state should contain:
      - account: {equity, cash, deployed, day_pnl, day_pct, cum_pnl, cum_pct, yesterday_equity}
      - market: {spy_today_return, vix}
      - decisions: {momentum_picks: [{ticker, trailing_return, atr_pct}], bottom_candidates_count}
      - sleeve_alloc: {momentum, bottom, method}
      - risk_warnings: [...]
      - orders: [{symbol, side, notional, status}]
      - positions: {ticker: {market_value, unrealized_pl, unrealized_plpc, entry, current}}
      - anomalies_today: [{name, confidence, alpha_bps, rationale}]
      - run_id: ...
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    try:
        from anthropic import Anthropic
    except ImportError:
        return None

    user_prompt = _format_state_for_prompt(state)

    try:
        client = Anthropic()
        # v2.8: enable Anthropic web search so the LLM can pull TODAY's news
        # for positions that moved >2σ vs SPY. Cost ~$0.05-0.10 per call extra.
        resp = client.messages.create(
            model=os.getenv("CRITIC_MODEL", "claude-sonnet-4-6"),
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
            messages=[{"role": "user", "content": user_prompt}],
        )
        # Extract text content from response (may include tool_use blocks)
        text_parts = []
        for block in resp.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)
        return "\n".join(text_parts) if text_parts else "(narrative produced no text)"
    except Exception as e:
        # Fall back to no-tool call if web search isn't available on this account
        try:
            resp = client.messages.create(
                model=os.getenv("CRITIC_MODEL", "claude-sonnet-4-6"),
                max_tokens=900,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return resp.content[0].text
        except Exception as e2:
            return f"(narrative unavailable: {type(e2).__name__}: {e2})"


def _format_state_for_prompt(state: dict[str, Any]) -> str:
    """Compact, structured representation of run state."""
    a = state.get("account", {})
    m = state.get("market", {})
    d = state.get("decisions", {})
    sa = state.get("sleeve_alloc", {})
    rw = state.get("risk_warnings", [])
    o = state.get("orders", [])
    p = state.get("positions", {})
    an = state.get("anomalies_today", [])

    lines = [f"DAILY STATE for {state.get('run_id', '?')}"]

    lines.append("\nACCOUNT:")
    lines.append(f"  Equity ${a.get('equity', 0):,.0f}, Cash ${a.get('cash', 0):,.0f}, Deployed ${a.get('deployed', 0):,.0f}")
    lines.append(f"  Day P&L ${a.get('day_pnl', 0):+,.2f} ({a.get('day_pct', 0)*100:+.2f}%); since-start ${a.get('cum_pnl', 0):+,.2f} ({a.get('cum_pct', 0)*100:+.2f}%)")
    lines.append(f"  Yesterday equity ${a.get('yesterday_equity') or 0:,.0f}")

    lines.append("\nMARKET TODAY:")
    spy = m.get("spy_today_return")
    if spy is not None:
        lines.append(f"  SPY {spy*100:+.2f}%; alpha {(a.get('day_pct', 0) - spy)*100:+.2f}%")
    else:
        lines.append("  SPY: data unavailable")
    vix = m.get("vix")
    if vix is not None:
        lines.append(f"  VIX {vix:.1f}")

    lines.append("\nDECISIONS:")
    for pick in d.get("momentum_picks", []):
        lines.append(f"  Momentum: {pick.get('ticker')} (12m {pick.get('trailing_return', 0)*100:+.1f}%, ATR {pick.get('atr_pct', 0)*100:.1f}%)")
    if d.get("bottom_candidates_count", 0) == 0:
        lines.append("  Bottom-catch: 0 candidates passed threshold today")
    else:
        lines.append(f"  Bottom-catch: {d['bottom_candidates_count']} candidates")
    lines.append(f"  Sleeve weights: momentum {sa.get('momentum', 0)*100:.0f}% / bottom {sa.get('bottom', 0)*100:.0f}% (method: {sa.get('method', '?')})")

    if rw:
        lines.append("\nRISK GATES TRIGGERED:")
        for w in rw:
            lines.append(f"  {w}")

    lines.append("\nORDERS:")
    skipped = sum(1 for x in o if x.get("status") == "below_min")
    submitted = sum(1 for x in o if x.get("status") == "submitted")
    lines.append(f"  {submitted} submitted, {skipped} skipped (below $50 min-rebalance threshold)")

    lines.append("\nPOSITIONS:")
    for sym, pos in p.items():
        lines.append(
            f"  {sym}: ${pos.get('market_value', 0):,.0f} mkt, P&L ${pos.get('unrealized_pl', 0):+,.0f} ({pos.get('unrealized_plpc', 0)*100:+.2f}%) "
            f"entry ${pos.get('avg_entry_price', 0):.2f} → ${pos.get('current_price', 0):.2f}"
        )

    if an:
        lines.append("\nANOMALIES FIRING TODAY:")
        for x in an:
            lines.append(f"  {x.name} ({x.confidence}, +{x.expected_alpha_bps}bps): {x.rationale}")
    else:
        lines.append("\nANOMALIES: none firing today")

    lines.append("\nNow write the three-section commentary.")
    return "\n".join(lines)
