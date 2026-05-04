"""UI helpers extracted from scripts/dashboard.py (v3.67.0 split).

Pure rendering + state-access helpers that don't depend on dashboard.py's
module-level constants (DB_PATH, ROOT). Each helper either takes the
dashboard's state via parameters or reads from `st.session_state`.

This module exists because dashboard.py grew past 5,600 lines. By
extracting the rendering surface, view functions in dashboard.py shrink
and these helpers become unit-testable in isolation (`from
trader.dashboard_ui import ...`).

What lives here:
- Market session detection wrapper (`_market_session`)
- Canonical equity-state accessor (`get_equity_state`, `equity_state_cached`)
- Day-P&L card (`render_day_pl_card`) — DRY for live/perf consumers
- Sticky market ribbon (`render_market_ribbon`)
- Big-block price headline (`render_price_headline`)
- Floating Ask-HANK FAB (`render_floating_hank_fab`)
- Timeframe chips (`render_timeframe_chips`, `TIMEFRAME_CHIPS`)
- Hebbia-style citation pills + tool artifacts

What stays in dashboard.py:
- Sidebar rendering (depends on session-state init order)
- View functions (one per route, deeply entangled with own logic)
- `_headline_metrics` (depends on freeze + overlay state from dashboard)
- `_overlay_signal`, `_morning_briefing` (data layer, separate split target)
"""
from __future__ import annotations

from collections import namedtuple
from datetime import datetime as _dt
from pathlib import Path
from typing import Optional

import streamlit as st


# ============================================================
# Market session
# ============================================================
def market_session():
    """Wrapper around trader.market_session.market_session_now so views
    can branch on OPEN vs CLOSED_* without importing the module each
    time. Returns SessionState.

    Loud-fails (st.warning + return CLOSED) when the underlying module
    errors so we never silently revert to a synthetic OPEN — that bug
    re-enabled the v3.65.x phantom-day-P&L behavior."""
    try:
        from trader.market_session import market_session_now
        return market_session_now()
    except Exception as e:
        st.warning(
            f"⚠️ market session detection failed ({type(e).__name__}: {e}) "
            "— treating as CLOSED to avoid showing misleading day P&L. "
            "Day deltas will be hidden until this is fixed."
        )
        Fake = namedtuple("Fake", ["label", "is_open", "last_trading_day",
                                    "next_trading_day", "et_now", "reason"])
        today = _dt.utcnow().date()
        return Fake("CLOSED_OVERNIGHT", False, today, today, _dt.utcnow(),
                     "session helper errored")


# ============================================================
# Equity state — single source of truth (v3.66.0)
# ============================================================
@st.cache_data(ttl=30, show_spinner=False)
def equity_state_cached(db_path: str, briefing_cache_path: str):
    """Cached EquityState. 30s TTL: short enough that during RTH the
    user always sees a fresh broker mark; long enough that one page
    load doesn't fan out into 10 broker calls."""
    try:
        from trader.equity_state import get_equity_state
        return get_equity_state(
            journal_db=Path(db_path),
            briefing_cache=Path(briefing_cache_path),
        )
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def get_equity_state(db_path: str, briefing_cache_path: str):
    """Public accessor. Returns EquityState (or a stand-in 'none' state
    if the cached helper errored)."""
    s = equity_state_cached(db_path, briefing_cache_path)
    if isinstance(s, dict) and "_error" in s:
        from trader.equity_state import EquityState
        from trader.market_session import market_session_now
        sess = market_session_now()
        return EquityState(
            equity_now=None, cash=None, n_positions=0,
            today_pl_dollar=None, today_pl_pct=None,
            last_session_pl_dollar=None, last_session_pl_pct=None,
            last_session_date=sess.last_trading_day.isoformat(),
            source="none", source_age_seconds=0.0, session=sess,
            error=s["_error"],
        )
    return s


def render_day_pl_card(col, state):
    """Render one Streamlit column as a session-aware day-P&L metric.

    Centralized so every consumer gets identical labels, tooltips, and
    closed-market handling. Pre-v3.66.0 this branch was inlined in
    multiple views with subtle copy-paste drift.

    `col`   = a Streamlit column (or `st` itself for full-width)
    `state` = EquityState from get_equity_state()"""
    sess = state.session
    if state.equity_now is None:
        col.metric("Day P&L", "n/a", help="No equity source reachable.")
        return
    if sess.is_open:
        if state.today_pl_dollar is None:
            col.metric("Day P&L", "computing…")
            return
        col.metric(
            "Day P&L",
            f"${state.today_pl_dollar:+,.0f}",
            f"{state.today_pl_pct*100:+.2f}%"
            if state.today_pl_pct is not None else None,
        )
    else:
        last_str = sess.last_trading_day.strftime("%a %b %-d")
        if state.last_session_pl_dollar is None:
            col.metric(f"Last session ({last_str})", "n/a")
            return
        col.metric(
            f"Last session ({last_str})",
            f"${state.last_session_pl_dollar:+,.0f}",
            f"{state.last_session_pl_pct*100:+.2f}%"
            if state.last_session_pl_pct is not None else None,
            help=("Markets closed — this is the most recent trading "
                  "session's move, not 'today'. See sticky ribbon for "
                  "session state."),
        )


# ============================================================
# Sticky market ribbon (v3.65.0 + v3.65.1)
# ============================================================
@st.cache_data(ttl=120, show_spinner=False)
def ribbon_market_snapshot() -> dict:
    """Pull SPY / QQQ / VIX last-2-day closes once every 2 min for the
    sticky ribbon. Returns {} on any failure (offline, rate limit) so the
    ribbon degrades gracefully to '—'."""
    out: dict = {}
    try:
        import yfinance as yf
        tickers = yf.download(
            ["SPY", "QQQ", "^VIX"], period="5d", progress=False,
            auto_adjust=True, threads=False,
        )
        if tickers is None or tickers.empty:
            return out
        closes = (tickers["Close"]
                  if "Close" in tickers.columns.get_level_values(0)
                  else tickers)
        for sym, key in (("SPY", "spy"), ("QQQ", "qqq"), ("^VIX", "vix")):
            try:
                col = closes[sym].dropna()
                if len(col) >= 2:
                    last = float(col.iloc[-1])
                    prev = float(col.iloc[-2])
                    out[key] = {"last": last,
                                 "pct": (last - prev) / prev if prev else None}
            except Exception:
                continue
    except Exception:
        return out
    return out


def render_market_ribbon(overlay=None):
    """Thin, always-on horizontal market ribbon — SPY / QQQ / VIX +
    regime overlay multiplier + market-session badge. Pattern: Yahoo
    Finance's persistent market-stats rail.

    `overlay` = optional regime overlay signal (passed in from dashboard
                because it depends on dashboard.py's disk cache)."""
    snap = ribbon_market_snapshot()
    session = market_session()

    parts: list[str] = []

    # Session badge always first so the user sees session state
    # before reading numbers.
    if session.is_open:
        parts.append('<span style="background:#052e16;color:#4ade80;'
                      'padding:2px 8px;border-radius:4px;font-weight:600">'
                      '● MARKET OPEN</span>')
    else:
        last_str = session.last_trading_day.strftime("%a %b %-d")
        parts.append(f'<span style="background:#1e293b;color:#94a3b8;'
                      f'padding:2px 8px;border-radius:4px;font-weight:600">'
                      f'○ CLOSED · last close {last_str}</span>')

    def _fmt(symbol: str, key: str, dec: int = 2) -> str:
        v = snap.get(key)
        if not v or v.get("last") is None:
            return f'<span style="color:#888">{symbol} —</span>'
        last = v["last"]
        pct = v.get("pct")
        if pct is None:
            return (f'<span style="color:#cbd5e1">{symbol} '
                    f'{last:,.{dec}f}</span>')
        color = "#16a34a" if pct >= 0 else "#dc2626"
        sign = "▲" if pct >= 0 else "▼"
        return (f'<span style="color:#cbd5e1">{symbol} '
                f'<b style="font-family:JetBrains Mono,monospace">'
                f'{last:,.{dec}f}</b> '
                f'<span style="color:{color}">{sign} {abs(pct)*100:.2f}%'
                f'</span></span>')

    parts.append(_fmt("SPY", "spy"))
    parts.append(_fmt("QQQ", "qqq"))
    parts.append(_fmt("VIX", "vix"))

    if overlay is not None and getattr(overlay, "hmm_regime", None):
        regime = overlay.hmm_regime.upper()
        emoji = {"BULL": "🟢", "BEAR": "🔴",
                  "TRANSITION": "🟡"}.get(regime, "⚪")
        suffix = "" if overlay.enabled else " (off)"
        parts.append(
            f'<span style="color:#cbd5e1">{emoji} {regime} '
            f'<span style="color:#94a3b8">overlay {overlay.final_mult:.2f}×'
            f'{suffix}</span></span>')
    else:
        parts.append('<span style="color:#888">⚪ regime —</span>')

    sep = '<span style="color:#475569;margin:0 14px">·</span>'
    html = (
        '<div style="background:#0f172a;border:1px solid #1e293b;'
        'border-radius:6px;padding:8px 14px;margin-bottom:14px;'
        'font-size:13px;line-height:1.4;'
        'display:flex;flex-wrap:wrap;align-items:center">'
        + sep.join(parts) +
        '</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


# ============================================================
# Big-block price headline (Nasdaq/CNBC pattern)
# ============================================================
def render_price_headline(state):
    """Big-block equity headline. `state` = EquityState from
    get_equity_state(). Suppresses day delta when market closed
    (v3.65.1) and shows source provenance (v3.66.0)."""
    if state.equity_now is None:
        st.caption(
            f"_no equity source reachable_ "
            f"({state.error or 'broker, journal, briefing all empty'})"
        )
        return

    eq_now = state.equity_now
    sess = state.session

    if sess.is_open and state.today_pl_dollar is not None:
        if state.today_pl_pct >= 0:
            bg, fg = "#052e16", "#bbf7d0"
            arrow_color, arrow = "#4ade80", "▲"
        else:
            bg, fg = "#450a0a", "#fecaca"
            arrow_color, arrow = "#f87171", "▼"
        delta_html = (
            f'<span style="color:{arrow_color};font-size:24px;'
            f'margin-left:18px">{arrow} '
            f'${state.today_pl_dollar:+,.0f} '
            f'({state.today_pl_pct*100:+.2f}%)</span>')
    else:
        bg, fg = "#1e293b", "#e2e8f0"
        last_str = sess.last_trading_day.strftime("%a %b %-d, %Y")
        delta_html = (
            f'<span style="color:#94a3b8;font-size:14px;margin-left:18px;'
            f'font-style:italic">Markets closed · last session '
            f'{last_str}</span>')

    age = state.source_age_seconds
    if age < 60:
        age_str = f"{int(age)}s ago"
    elif age < 3600:
        age_str = f"{int(age/60)}m ago"
    else:
        age_str = f"{int(age/3600)}h ago"
    stale_color = "#fbbf24" if state.is_stale else "#64748b"
    src_html = (
        f'<div style="color:{stale_color};font-size:10px;'
        f'margin-top:6px;font-family:JetBrains Mono,monospace">'
        f'src: {state.source} · {age_str}</div>'
    )

    html = (
        f'<div style="background:{bg};border-radius:10px;'
        f'padding:18px 26px;margin-bottom:16px">'
        f'<div style="color:#94a3b8;font-size:11px;text-transform:uppercase;'
        f'letter-spacing:0.08em">Equity</div>'
        f'<div style="margin-top:4px">'
        f'<span style="color:{fg};font-size:48px;font-weight:700;'
        f'font-family:JetBrains Mono,monospace">${eq_now:,.0f}</span>'
        f'{delta_html}'
        f'</div>'
        f'{src_html}'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


# ============================================================
# Floating Ask-HANK pill (TipRanks 'Ask Samuel AI' pattern)
# ============================================================
def render_floating_hank_fab():
    """Bottom-right floating 'Ask HANK' pill on every non-chat view."""
    st.markdown("""
    <style>
      div[data-testid="column"]:has(button[kind="secondary"][data-testid="baseButton-secondary"].fab-hank) {
        position: fixed; right: 24px; bottom: 24px; z-index: 9999;
      }
      .hank-fab-row { position: fixed !important; right: 24px;
                       bottom: 24px; z-index: 9999;
                       background: transparent !important; }
      .hank-fab-row .stButton > button {
        background: #2563eb;
        color: white !important; border: none; border-radius: 999px;
        padding: 12px 20px; font-weight: 600; font-size: 14px;
        box-shadow: 0 4px 14px rgba(37,99,235,.35);
      }
      .hank-fab-row .stButton > button:hover {
        background: #1d4ed8;
      }
    </style>
    """, unsafe_allow_html=True)
    st.markdown('<div class="hank-fab-row">', unsafe_allow_html=True)
    if st.button("🧠 Ask HANK", key="fab_ask_hank",
                  help="Open the HANK chat (you can also press Cmd+K)"):
        st.session_state.active_view = "chat"
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)


# ============================================================
# Timeframe chips (Yahoo / Nasdaq / CNBC / TipRanks pattern)
# ============================================================
TIMEFRAME_CHIPS = [
    ("1D", 1), ("5D", 5), ("1M", 21), ("3M", 63),
    ("6M", 126), ("YTD", "ytd"), ("1Y", 252), ("5Y", 1260), ("ALL", "all"),
]


def render_timeframe_chips(state_key: str,
                            default_label: str = "3M") -> int:
    """Horizontal row of timeframe chip buttons. Returns the chosen
    window in trading days. Stores selection in
    session_state[state_key]."""
    if state_key not in st.session_state:
        st.session_state[state_key] = default_label
    cols = st.columns(len(TIMEFRAME_CHIPS))
    for col, (label, _) in zip(cols, TIMEFRAME_CHIPS):
        is_active = st.session_state[state_key] == label
        btype = "primary" if is_active else "secondary"
        if col.button(label, key=f"tf_{state_key}_{label}",
                       use_container_width=True, type=btype):
            st.session_state[state_key] = label
            st.rerun()
    label = st.session_state[state_key]
    val = dict(TIMEFRAME_CHIPS)[label]
    if val == "ytd":
        today = _dt.utcnow().date()
        days_since_jan1 = (today - today.replace(month=1, day=1)).days
        return max(int(days_since_jan1 * 252 / 365), 5)
    if val == "all":
        return 1260 * 5  # ~25y; backtest helpers cap to available data
    return int(val)


# ============================================================
# Hebbia/Perplexity-style citation pills + tool artifacts (v3.57.1)
# ============================================================
def tier_emoji(tier: str) -> str:
    return {"read_only": "📖", "sim": "🧪", "live": "🚨"}.get(tier, "🔧")


def render_citation_pills(tool_log: list[dict]):
    """Row of [1][2][3] citation pills for the just-completed tool calls."""
    if not tool_log:
        return
    pills = []
    for i, tc in enumerate(tool_log, start=1):
        emoji = tier_emoji(tc.get("tier", "read_only"))
        name = tc.get("name", "?")
        pills.append(f"`[{i}]` {emoji} {name}")
    st.markdown("**Sources:** " + " · ".join(pills))


def render_tool_artifact(idx: int, tc: dict):
    """One tool call as an inline artifact card. Tabular results promote
    to st.dataframe; everything else falls back to st.json."""
    name = tc.get("name", "?")
    tier = tc.get("tier", "read_only")
    st.markdown(f"**[{idx}] {tier_emoji(tier)} `{name}`** _(tier: {tier})_")
    args = tc.get("input", {})
    if args:
        st.caption(f"args: `{args}`")
    result = tc.get("result", {})
    if isinstance(result, dict):
        for key in ("rows", "positions", "decisions", "events", "lots",
                    "postmortems", "shadow_decisions", "orders"):
            if key in result and isinstance(result[key], list) and result[key]:
                st.caption(f"{key} ({len(result[key])} rows):")
                try:
                    st.dataframe(result[key], use_container_width=True,
                                 hide_index=True)
                except Exception:
                    st.json(result[key], expanded=False)
                remainder = {k: v for k, v in result.items() if k != key}
                if remainder:
                    st.caption("metadata:")
                    st.json(remainder, expanded=False)
                return
    st.json(result, expanded=False)
