"""Auto-generated 'morning briefing' for the Copilot's first message.

The AI-native paradigm: when the user opens the dashboard, they shouldn't
have to ASK what's happening. The Copilot proactively pulls 4-5 key signals
and summarizes in 3-5 lines. Bloomberg PORT-OPEN equivalent.

The briefing combines:
  - Live equity + day P&L vs SPY
  - Regime overlay state
  - Upcoming events in next 7 days
  - Freeze state
  - Yesterday's post-mortem highlight (if any)

Returns a structured dict that the dashboard renders OR feeds to the
Copilot as the first turn's context.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class MorningBriefing:
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    headline: str = ""
    equity_now: Optional[float] = None
    day_pl_pct: Optional[float] = None
    spy_today_pct: Optional[float] = None
    excess_today_pct: Optional[float] = None
    regime: str = ""
    regime_overlay_mult: Optional[float] = None
    regime_enabled: bool = False
    freeze_active: bool = False
    freeze_reason: str = ""
    upcoming_events_next7d: list = field(default_factory=list)
    yesterday_pm_summary: str = ""
    notable_facts: list[str] = field(default_factory=list)
    raw_data: dict = field(default_factory=dict)

    def to_markdown(self) -> str:
        """Render as markdown for dashboard display + Copilot priming."""
        bits = [f"### {self.headline}\n"]
        if self.equity_now is not None:
            eq_str = f"**${self.equity_now:,.0f}** equity"
            if self.day_pl_pct is not None:
                eq_str += f" · day P&L {self.day_pl_pct*100:+.2f}%"
            if self.excess_today_pct is not None:
                eq_str += f" · excess vs SPY {self.excess_today_pct*100:+.2f}%"
            bits.append(eq_str + "\n")
        if self.regime:
            mult_str = f" (overlay {self.regime_overlay_mult:.2f}×)" if self.regime_overlay_mult else ""
            mult_str += " [DISABLED]" if not self.regime_enabled else ""
            bits.append(f"⚡ Regime: **{self.regime.upper()}**{mult_str}\n")
        if self.freeze_active:
            bits.append(f"🚨 **FREEZE ACTIVE**: {self.freeze_reason}\n")
        if self.upcoming_events_next7d:
            n = len(self.upcoming_events_next7d)
            bits.append(f"📅 **{n} event(s) in next 7 days:** "
                        + ", ".join(
                            f"{e.get('symbol') or 'portfolio'} {e.get('type', '?')} {e.get('date', '?')}"
                            for e in self.upcoming_events_next7d[:5])
                        + (" ..." if n > 5 else "") + "\n")
        if self.yesterday_pm_summary:
            bits.append(f"📜 *Yesterday's post-mortem:* {self.yesterday_pm_summary[:200]}")
        if self.notable_facts:
            bits.append("\n**Notable:**")
            for f in self.notable_facts:
                bits.append(f"\n- {f}")
        return "\n".join(bits)


def compute_briefing() -> MorningBriefing:
    """Build the briefing by calling our existing tools. Best-effort —
    each section catches its own errors so a partial briefing always
    renders."""
    brief = MorningBriefing()

    # Live portfolio snapshot
    try:
        from .positions_live import fetch_live_portfolio
        pf = fetch_live_portfolio()
        if pf.error is None:
            brief.equity_now = pf.equity
            brief.day_pl_pct = pf.total_day_pl_pct
            brief.raw_data["n_positions"] = len(pf.positions)
    except Exception as e:
        brief.notable_facts.append(f"⚠️ live portfolio fetch failed: {type(e).__name__}")

    # SPY today + excess
    try:
        import yfinance as yf
        spy = yf.download("SPY", period="5d", progress=False, auto_adjust=True)
        if spy is not None and not spy.empty:
            closes = spy["Close"].dropna()
            if len(closes) >= 2:
                brief.spy_today_pct = float((closes.iloc[-1] - closes.iloc[-2]) / closes.iloc[-2])
                if brief.day_pl_pct is not None:
                    brief.excess_today_pct = brief.day_pl_pct - brief.spy_today_pct
    except Exception:
        pass

    # Regime overlay
    try:
        from .regime_overlay import compute_overlay
        sig = compute_overlay()
        brief.regime = sig.hmm_regime
        brief.regime_overlay_mult = sig.final_mult
        brief.regime_enabled = sig.enabled
    except Exception:
        pass

    # Freeze state
    try:
        from .config import DATA_DIR
        import json
        from pathlib import Path
        freeze_path = Path(DATA_DIR) / "risk_freeze_state.json"
        if freeze_path.exists():
            freeze = json.loads(freeze_path.read_text())
            if freeze.get("liquidation_gate_tripped"):
                brief.freeze_active = True
                brief.freeze_reason = "LIQUIDATION GATE TRIPPED — written post-mortem required"
            elif "deploy_dd_freeze_until" in freeze:
                brief.freeze_active = True
                brief.freeze_reason = f"DEPLOY-DD FREEZE until {freeze['deploy_dd_freeze_until']}"
            elif "daily_loss_freeze_until" in freeze:
                brief.freeze_active = True
                brief.freeze_reason = f"DAILY-LOSS FREEZE until {freeze['daily_loss_freeze_until']}"
    except Exception:
        pass

    # Upcoming events (next 7 days)
    # v3.56.9: PORTFOLIO-WIDE ONLY (FOMC + OPEX) by default. Per-symbol
    # earnings calendars (yfinance Ticker.get_earnings_dates) cost
    # ~200-500ms per held name = 3-7s for a 15-position book. That single
    # call dominated the 7.4s briefing cold-start. Per-symbol earnings
    # are still computed in the Events view (where the user is explicitly
    # asking for them) — the briefing is meant for instant glance.
    try:
        from .events_calendar import compute_upcoming_events
        events = compute_upcoming_events(symbols=[], days_ahead=7)
        brief.upcoming_events_next7d = [
            {"date": str(e.date), "days_until": e.days_until,
             "type": e.event_type, "symbol": e.symbol, "note": e.note}
            for e in events
        ]
    except Exception:
        pass

    # Yesterday's post-mortem
    try:
        import sqlite3
        from .config import DB_PATH
        from pathlib import Path
        if Path(DB_PATH).exists():
            with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as c:
                row = c.execute(
                    "SELECT date, summary FROM postmortems ORDER BY date DESC LIMIT 1"
                ).fetchone()
                if row:
                    brief.yesterday_pm_summary = f"({row[0]}) {row[1]}" if row[1] else ""
    except Exception:
        pass

    # Build headline
    if brief.freeze_active:
        brief.headline = "🚨 Action required"
    elif brief.upcoming_events_next7d and any(
        e.get("type") == "fomc" and e.get("days_until", 99) <= 3
        for e in brief.upcoming_events_next7d
    ):
        brief.headline = "🏦 FOMC imminent — attention"
    elif brief.regime == "bear":
        brief.headline = "🔴 Bear regime active"
    elif brief.regime == "transition":
        brief.headline = "🟡 Transition regime"
    elif brief.regime == "bull":
        brief.headline = "🟢 Bull regime"
    else:
        brief.headline = "📊 Today's briefing"

    # Notable facts
    if brief.day_pl_pct is not None and abs(brief.day_pl_pct) > 0.02:
        brief.notable_facts.append(
            f"Day P&L is {abs(brief.day_pl_pct)*100:.1f}% — outside typical ±2% band")
    if brief.excess_today_pct is not None and brief.excess_today_pct > 0.005:
        brief.notable_facts.append(
            f"Beating SPY by {brief.excess_today_pct*100:+.2f}% today")
    elif brief.excess_today_pct is not None and brief.excess_today_pct < -0.005:
        brief.notable_facts.append(
            f"Trailing SPY by {abs(brief.excess_today_pct)*100:.2f}% today")

    return brief
