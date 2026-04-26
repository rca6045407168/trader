"""Nightly self-review agent.

Reads yesterday's decisions, orders, and the resulting P&L, then proposes ONE
specific tweak. The tweak is logged to the journal — NOT auto-applied — so
Richard can review the proposed change before it goes live.
"""
from datetime import datetime
from anthropic import Anthropic

from .config import POSTMORTEM_MODEL
from .journal import recent_decisions, recent_snapshots, log_postmortem

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


SYSTEM = """You are the Trading Strategy Reviewer for a personal momentum + bottom-catch system.
Review yesterday's trades vs the resulting P&L. Be brutally honest.

Output EXACTLY this format:

## What worked
<1-2 wins, naming the specific signal that fired correctly>

## What failed
<1-2 losers, naming what the system missed>

## Pattern observed
<a recurring mistake across multiple trades, OR "none — sample too small">

## Proposed tweak (ONE only)
<format: "Change [parameter] from [X] to [Y] because [evidence]". Don't propose multiple.
If no tweak warranted, write "NO CHANGE — system performing within expected variance">

Be specific. Don't propose "tune hyperparameters" — propose "set RSI threshold from 30 to 25 because 4 of 5 recent RSI<30 entries continued falling for >2 days."
Don't propose tweaks based on a single trade. Need ≥3 data points for a pattern.
"""


def build_context() -> tuple[str, float | None]:
    decisions = recent_decisions(days=2)
    snapshots = recent_snapshots(days=7)

    pnl_pct = None
    if len(snapshots) >= 2:
        today = snapshots[0]
        yest = snapshots[1]
        if yest["equity"]:
            pnl_pct = (today["equity"] - yest["equity"]) / yest["equity"]

    lines = [f"Date: {datetime.utcnow().date()}"]
    lines.append(f"Decisions in last 2 days: {len(decisions)}")
    if pnl_pct is not None:
        lines.append(f"Yesterday's P&L: {pnl_pct:+.2%}")
    lines.append("")
    lines.append("Recent decisions:")
    for d in decisions[:30]:
        final = (d.get("final") or "")[:120]
        lines.append(
            f"  [{d['ts'][:10]}] {d['ticker']:6s} {d['style']:14s} score={d['score']:.2f} -> {final}"
        )
    lines.append("")
    lines.append("Equity trajectory (last 7 snapshots):")
    for s in reversed(snapshots):
        lines.append(f"  {s['date']}: equity=${s['equity']:.0f}  cash=${s['cash']:.0f}")
    return "\n".join(lines), pnl_pct


def run_postmortem() -> dict:
    context, pnl_pct = build_context()
    if "Decisions in last 2 days: 0" in context:
        log_postmortem("No trades in window — nothing to review.", "NO CHANGE", pnl_pct)
        return {"summary": "No recent activity.", "tweak": None, "pnl_pct": pnl_pct}

    client = _get_client()
    resp = client.messages.create(
        model=POSTMORTEM_MODEL,
        max_tokens=900,
        system=SYSTEM,
        messages=[{"role": "user", "content": context}],
    )
    text = resp.content[0].text

    # Extract the proposed tweak section
    tweak = "NO CHANGE"
    if "## Proposed tweak" in text:
        tweak = text.split("## Proposed tweak", 1)[1].strip()

    log_postmortem(summary=text, tweak=tweak, pnl_pct=pnl_pct)
    return {"summary": text, "tweak": tweak, "pnl_pct": pnl_pct}
