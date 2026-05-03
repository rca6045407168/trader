"""Live local dashboard for the trader (v3.55.0).

v3.55.0 — LEFT SIDEBAR NAV refactor (FlexHaul-style):
  Sidebar (left): vertical nav with sections:
    - Primary action: 🤖 Chat (default selected)
    - VIEWS: Overview, Live positions, Decisions, Lots, Performance,
             Attribution, Events, Regime, Intraday risk
    - RESEARCH: Shadow variants, Sleeve health, Postmortems, Reports
    - SYSTEM: Manual triggers, Settings
  Main area: renders the selected view (one at a time, no horizontal tabs).

This replaces v3.54.x's 'top metrics + chat above 14 tabs' layout. The user
wanted FlexHaul-app-style left nav with chat as the primary surface and
settings (journal path, sync, refresh) moved out of the sidebar into a
dedicated Settings view.

State model: st.session_state["active_view"] holds the currently selected
view key. Sidebar buttons mutate it; main-area dispatch renders the
matching view function.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

st.set_page_config(
    page_title="trader · live dashboard",
    layout="wide",
    page_icon="📊",
    initial_sidebar_state="expanded",
)

# ============================================================
# v3.55.1: Sleek dark aesthetic via CSS injection
# ============================================================
st.markdown("""
<style>
  /* Hide Streamlit chrome */
  /* v3.56.2: stop fighting Streamlit's header — just hide the
     hamburger menu, footer, and Streamlit-cloud deploy banner.
     Header stays in its default state so the sidebar collapse/expand
     toggle ALWAYS works (Streamlit moves that button between header
     and a standalone position when sidebar is collapsed; our previous
     whitelist couldn't catch all positions). */
  #MainMenu, footer { visibility: hidden !important; height: 0 !important; }
  header[data-testid="stHeader"] {
    background: transparent !important;
  }
  /* Only hide Streamlit-cloud's deploy button, not the entire header */
  [data-testid="stToolbarActions"],
  button[kind="deploy"],
  [data-testid="stStatusWidget"] {
    display: none !important;
  }
  div[data-testid="stToolbar"] { display: none; }
  div[data-testid="stDecoration"] { display: none; }

  /* Tighter top padding */
  div.block-container {
    padding-top: 1.5rem !important;
    padding-bottom: 2rem !important;
    max-width: 1400px;
  }

  /* Typography — system font stack for sans, JetBrains Mono for data */
  html, body {
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI",
                 "Helvetica Neue", sans-serif;
    font-feature-settings: "cv11", "ss01", "ss03";
    letter-spacing: -0.005em;
  }
  /* v3.55.2 FIX: preserve Material Icons font. The previous
     [class*="st"] rule was overriding the icon font globally,
     leaving fallback text like "keyboard_double_arrow_left",
     "expand_more", "face" (chat avatars), "smart_toy" visible
     instead of glyph icons. Scope the body font to text elements
     only and explicitly restore the icon font on icon spans. */
  span[class*="material-icons"],
  span[class*="material-symbols"],
  i[class*="material-"],
  [data-testid="stIconMaterial"],
  [class*="MaterialSymbol"] {
    font-family: 'Material Symbols Outlined', 'Material Symbols Rounded',
                 'Material Icons', 'Material Icons Extended' !important;
    font-feature-settings: 'liga' !important;
    -webkit-font-feature-settings: 'liga' !important;
    font-style: normal !important;
    font-weight: normal !important;
    text-transform: none !important;
    letter-spacing: 0 !important;
    word-wrap: normal !important;
    direction: ltr !important;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }
  /* Hide Streamlit's auto-generated default chat avatars entirely
     (icon-fontless environment shows them as 'face' / 'smart_toy'
     plain text otherwise). User can still see who said what via
     name labels. */
  [data-testid="stChatMessageAvatarUser"],
  [data-testid="stChatMessageAvatarAssistant"],
  [data-testid="chatAvatarIcon-user"],
  [data-testid="chatAvatarIcon-assistant"] {
    display: none !important;
  }
  code, pre, [data-testid="stCode"] {
    font-family: "JetBrains Mono", "SF Mono", "Menlo", "Consolas", monospace !important;
    font-size: 0.85em;
  }

  /* Headings: tighter, lower weight than Streamlit default */
  h1 { font-size: 1.75rem !important; font-weight: 600 !important;
       letter-spacing: -0.02em; margin-bottom: 0.25rem !important; }
  h2 { font-size: 1.25rem !important; font-weight: 600 !important;
       letter-spacing: -0.015em; margin-top: 1.5rem !important; }
  h3 { font-size: 1rem !important; font-weight: 600 !important;
       letter-spacing: -0.01em; }

  /* Captions: smaller, muted */
  div[data-testid="stCaption"] {
    font-size: 0.8rem !important; color: #9ca3af !important;
    letter-spacing: 0.005em;
  }

  /* Sidebar polish */
  section[data-testid="stSidebar"] {
    background-color: #0a0a0b;
    border-right: 1px solid #1f1f23;
    padding-top: 0.5rem;
  }
  section[data-testid="stSidebar"] > div { padding-top: 1rem; }
  section[data-testid="stSidebar"] h3 {
    font-size: 1.05rem !important; font-weight: 600 !important;
    margin-bottom: 0.25rem !important;
  }

  /* Sidebar buttons — cleaner, less Streamlit-default */
  section[data-testid="stSidebar"] button {
    border: 1px solid transparent !important;
    background: transparent !important;
    color: #d1d5db !important;
    font-weight: 400 !important;
    text-align: left !important;
    padding: 0.4rem 0.75rem !important;
    margin: 0 !important;
    border-radius: 6px !important;
    transition: background-color 120ms ease, border-color 120ms ease;
    font-size: 0.875rem !important;
  }
  section[data-testid="stSidebar"] button:hover {
    background: #18181b !important;
    border-color: #27272a !important;
  }
  section[data-testid="stSidebar"] button[kind="primary"] {
    background: #1e293b !important;
    border-color: #3b82f6 !important;
    color: #ffffff !important;
    font-weight: 500 !important;
  }

  /* Section labels in sidebar (the — VIEWS — captions) */
  section[data-testid="stSidebar"] [data-testid="stCaption"] {
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: 0.65rem !important;
    color: #6b7280 !important;
    margin-top: 1rem !important;
    margin-bottom: 0.25rem !important;
    padding-left: 0.5rem;
  }

  /* Metrics: cleaner cards */
  [data-testid="stMetric"] {
    background: #111114;
    border: 1px solid #1f1f23;
    padding: 0.75rem 1rem;
    border-radius: 8px;
  }
  [data-testid="stMetricValue"] {
    font-size: 1.5rem !important;
    font-weight: 600 !important;
    letter-spacing: -0.01em;
  }
  [data-testid="stMetricLabel"] {
    font-size: 0.75rem !important;
    color: #9ca3af !important;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 500 !important;
  }
  [data-testid="stMetricDelta"] { font-size: 0.8rem !important; }

  /* DataFrames: tighter rows */
  [data-testid="stDataFrame"] {
    border: 1px solid #1f1f23 !important;
    border-radius: 8px;
  }

  /* Containers (used for chat box) */
  [data-testid="stVerticalBlockBorderWrapper"] {
    border: 1px solid #1f1f23 !important;
    border-radius: 10px !important;
    background: #0d0d0f;
  }

  /* Chat messages: cleaner backgrounds */
  [data-testid="stChatMessage"] {
    background: transparent !important;
    border: none !important;
    padding: 0.5rem 0 !important;
  }

  /* Chat input bar: pill shape */
  [data-testid="stChatInput"] {
    border-radius: 24px !important;
    background: #18181b !important;
    border: 1px solid #27272a !important;
  }
  [data-testid="stChatInput"]:focus-within {
    border-color: #3b82f6 !important;
  }

  /* Expanders */
  [data-testid="stExpander"] {
    border: 1px solid #1f1f23 !important;
    border-radius: 8px !important;
    background: #0d0d0f;
  }
  [data-testid="stExpander"] summary {
    font-weight: 500 !important;
    color: #d1d5db !important;
  }

  /* Buttons in main area: cleaner */
  div[data-testid="stHorizontalBlock"] button,
  .main button {
    border-radius: 6px !important;
    font-weight: 500 !important;
    font-size: 0.875rem !important;
    transition: background-color 120ms ease, border-color 120ms ease;
  }

  /* Dividers: subtler */
  hr {
    border-color: #1f1f23 !important;
    margin: 1.5rem 0 !important;
  }

  /* Code blocks */
  code:not(pre code) {
    background: #18181b !important;
    color: #93c5fd !important;
    padding: 1px 5px !important;
    border-radius: 4px !important;
    font-size: 0.85em !important;
  }

  /* st.json: monospace */
  [data-testid="stJson"] {
    font-family: "JetBrains Mono", "SF Mono", monospace !important;
    font-size: 0.8rem !important;
  }

  /* Subtler scrollbars in chat container */
  [data-testid="stVerticalBlockBorderWrapper"]::-webkit-scrollbar {
    width: 6px;
  }
  [data-testid="stVerticalBlockBorderWrapper"]::-webkit-scrollbar-thumb {
    background: #2a2a2e; border-radius: 3px;
  }
  [data-testid="stVerticalBlockBorderWrapper"]::-webkit-scrollbar-track {
    background: transparent;
  }
</style>
""", unsafe_allow_html=True)

# ============================================================
# Session state defaults
# ============================================================
if "active_view" not in st.session_state:
    st.session_state.active_view = "chat"
if "db_path" not in st.session_state:
    st.session_state.db_path = str(ROOT / "data" / "journal.db")
if "refresh_sec" not in st.session_state:
    st.session_state.refresh_sec = 30
if "auto_refresh_enabled" not in st.session_state:
    st.session_state.auto_refresh_enabled = False  # off by default in v3.55 — chat-first
if "copilot_messages" not in st.session_state:
    st.session_state.copilot_messages = []
# v3.56.0: chat-thread persistence state
if "current_thread_id" not in st.session_state:
    st.session_state.current_thread_id = None
if "current_thread_title" not in st.session_state:
    st.session_state.current_thread_title = "(new chat)"
if "current_thread_created_at" not in st.session_state:
    st.session_state.current_thread_created_at = ""


# ============================================================
# Sidebar — left nav (FlexHaul-style)
# ============================================================
with st.sidebar:
    st.markdown("### 📊 trader")
    st.caption("v3.55.0 · chat-first AI dashboard")
    st.divider()

    # Primary action up top
    if st.button("💬 New chat", use_container_width=True, type="primary"):
        # v3.56.0: save current thread before starting a new one
        try:
            from trader.copilot_storage import new_thread, save_thread, ChatThread
            if st.session_state.copilot_messages:
                # Persist whatever was in the active thread first
                _cur_id = st.session_state.get("current_thread_id")
                if _cur_id:
                    cur = ChatThread(
                        id=_cur_id,
                        title=st.session_state.get("current_thread_title", "(new chat)"),
                        created_at=st.session_state.get("current_thread_created_at", ""),
                        updated_at="",
                        messages=st.session_state.copilot_messages,
                    )
                    save_thread(cur)
            t = new_thread()
            st.session_state.current_thread_id = t.id
            st.session_state.current_thread_title = t.title
            st.session_state.current_thread_created_at = t.created_at
        except Exception:
            pass
        st.session_state.copilot_messages = []
        st.session_state.active_view = "chat"
        st.rerun()
    st.write("")

    # v3.56.0: chat threads list (newest first, max 20 visible)
    # v3.56.2: cached at 30s so the sidebar doesn't disk-scan on every rerun
    @st.cache_data(ttl=30, show_spinner=False)
    def _cached_thread_list():
        try:
            from trader.copilot_storage import list_threads
            return [(t.id, t.title, t.created_at, t.updated_at)
                    for t in list_threads(limit=20)]
        except Exception:
            return []
    try:
        threads_data = _cached_thread_list()
        # Reconstruct lightweight thread objects (we only need id/title for display)
        from collections import namedtuple
        _T = namedtuple("_T", ["id", "title", "created_at", "updated_at"])
        threads = [_T(*x) for x in threads_data]
        if threads:
            st.caption("— CHATS —")
            for t in threads:
                is_active_thread = (st.session_state.get("current_thread_id") == t.id)
                btype = "primary" if is_active_thread else "secondary"
                # Truncate title for sidebar width
                disp_title = t.title if len(t.title) <= 32 else t.title[:30] + "…"
                if st.button(disp_title, key=f"thread_{t.id}",
                             use_container_width=True, type=btype):
                    # Save current first if dirty
                    try:
                        from trader.copilot_storage import save_thread, ChatThread
                        cur_id = st.session_state.get("current_thread_id")
                        if cur_id and cur_id != t.id and st.session_state.copilot_messages:
                            cur = ChatThread(
                                id=cur_id,
                                title=st.session_state.get("current_thread_title", "(new chat)"),
                                created_at=st.session_state.get("current_thread_created_at", ""),
                                updated_at="",
                                messages=st.session_state.copilot_messages,
                            )
                            save_thread(cur)
                    except Exception:
                        pass
                    # Load the clicked thread
                    st.session_state.current_thread_id = t.id
                    st.session_state.current_thread_title = t.title
                    st.session_state.current_thread_created_at = t.created_at
                    st.session_state.copilot_messages = list(t.messages)
                    st.session_state.active_view = "chat"
                    st.rerun()
    except Exception:
        pass

    NAV = [
        ("🤖 Chat", "chat"),
        ("— VIEWS —", None),
        ("🏠 Overview", "overview"),
        ("💼 Live positions", "live_positions"),
        ("🎯 Decisions", "decisions"),
        ("📦 Position lots", "lots"),
        ("📈 Performance", "performance"),
        ("📊 Attribution", "attribution"),
        ("📅 Events", "events"),
        ("🌡️ Regime overlay", "regime"),
        ("⚡ Intraday risk", "intraday"),
        ("— RESEARCH —", None),
        ("👥 Shadow variants", "shadows"),
        ("🔍 Sleeve health", "sleeve_health"),
        ("📜 Postmortems", "postmortems"),
        ("📄 Reports", "reports"),
        ("— SYSTEM —", None),
        ("🔧 Manual triggers", "manual"),
        ("⚙️ Settings", "settings"),
    ]
    for label, key in NAV:
        if key is None:
            st.caption(label)
            continue
        is_active = st.session_state.active_view == key
        # Highlight active item with a distinct button type
        btype = "primary" if is_active else "secondary"
        if st.button(label, key=f"nav_{key}",
                     use_container_width=True,
                     type=btype if is_active else "secondary"):
            st.session_state.active_view = key
            st.rerun()

    st.divider()
    # Compact data-freshness indicator at bottom of sidebar
    db_path_obj = Path(st.session_state.db_path)
    if db_path_obj.exists():
        mtime = datetime.fromtimestamp(db_path_obj.stat().st_mtime)
        age_sec = (datetime.now() - mtime).total_seconds()
        if age_sec < 60:
            age_str = f"{int(age_sec)}s ago"
        elif age_sec < 3600:
            age_str = f"{int(age_sec // 60)}m ago"
        else:
            age_str = f"{int(age_sec // 3600)}h ago"
        st.caption(f"📁 journal: **{age_str}**")
    else:
        st.caption("⚠️ no journal")

# ============================================================
# Helpers (used by all views)
# ============================================================
DB_PATH = Path(st.session_state.db_path)


@st.cache_data(ttl=10)
def query(path_str: str, sql: str, params: tuple = ()) -> pd.DataFrame:
    if not Path(path_str).exists():
        return pd.DataFrame()
    try:
        with sqlite3.connect(f"file:{path_str}?mode=ro", uri=True) as c:
            return pd.read_sql_query(sql, c, params=params)
    except Exception as e:
        st.error(f"query failed: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=10)
def read_state_file(path_str: str) -> dict:
    p = Path(path_str)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


@st.cache_data(ttl=60, show_spinner=False)
def _live_portfolio():
    try:
        from trader.positions_live import fetch_live_portfolio
        return fetch_live_portfolio()
    except Exception as e:
        class E:
            error = f"{type(e).__name__}: {e}"
            equity = None; cash = None; buying_power = None
            total_unrealized_pl = 0; total_day_pl_dollar = 0; total_day_pl_pct = None
            positions = []
            timestamp = datetime.utcnow().isoformat()
        return E()


@st.cache_data(ttl=300)
def _morning_briefing():
    try:
        from trader.copilot_briefing import compute_briefing
        return compute_briefing()
    except Exception:
        return None


@st.cache_data(ttl=300)
def _overlay_signal():
    try:
        from trader.regime_overlay import compute_overlay
        return compute_overlay()
    except Exception:
        return None


@st.cache_data(ttl=30, show_spinner=False)
def _cached_snapshots(db_path: str):
    return query(db_path, "SELECT * FROM daily_snapshot ORDER BY date DESC LIMIT 30")


def _headline_metrics():
    """Render the headline metrics row used at top of Overview + Chat views."""
    snaps = _cached_snapshots(str(DB_PATH))
    cols = st.columns(6)
    if not snaps.empty:
        latest = snaps.iloc[0]
        eq = float(latest["equity"])
        cols[0].metric("Equity", f"${eq:,.0f}")
        cols[1].metric("Cash", f"${float(latest['cash']):,.0f}",
                       f"{(float(latest['cash'])/eq*100):.1f}% of book")
        anchor = read_state_file(str(ROOT / "data" / "deployment_anchor.json"))
        if anchor:
            anchor_eq = float(anchor.get("equity_at_deploy", 0))
            if anchor_eq > 0:
                dd = (eq - anchor_eq) / anchor_eq
                cols[2].metric("vs anchor", f"{dd:+.2%}", f"${anchor_eq:,.0f} baseline")
        if len(snaps) >= 2:
            first_eq = float(snaps.iloc[-1]["equity"])
            ret_window = (eq - first_eq) / first_eq if first_eq > 0 else 0
            cols[3].metric("Window return", f"{ret_window:+.2%}", f"{len(snaps)} snaps")
        else:
            cols[3].metric("Window", "≥1 snap needed")
    else:
        cols[0].metric("Equity", "n/a", "sync from GitHub")

    overlay = _overlay_signal()
    if overlay is not None:
        regime_label = overlay.hmm_regime.upper() if overlay.hmm_regime else "?"
        emoji = {"BULL": "🟢", "BEAR": "🔴", "TRANSITION": "🟡"}.get(regime_label, "⚪")
        cols[4].metric(f"{emoji} Regime", regime_label,
                       f"overlay {overlay.final_mult:.2f}×"
                       + (" (DISABLED)" if not overlay.enabled else ""))
    else:
        cols[4].metric("Regime", "computing...")

    freeze = read_state_file(str(ROOT / "data" / "risk_freeze_state.json"))
    if freeze.get("liquidation_gate_tripped"):
        cols[5].error("🚨 LIQ GATE")
    elif "deploy_dd_freeze_until" in freeze:
        cols[5].warning("❄️ DD FREEZE")
    elif "daily_loss_freeze_until" in freeze:
        cols[5].warning("❄️ DAILY-LOSS FREEZE")
    else:
        cols[5].success("✅ No freeze")


# ============================================================
# View: Chat (primary, default)
# ============================================================
def view_chat():
    st.title("🤖 Copilot")
    st.caption("Ask anything about your portfolio, decisions, regime, performance. "
               "The Copilot has 10 tools and uses them autonomously.")

    # Compact briefing as opening callout
    brief = _morning_briefing()
    if brief is not None:
        with st.expander("📰 Today's briefing", expanded=True):
            st.markdown(brief.to_markdown())

    # Suggested prompts
    sug_cols = st.columns(4)
    suggested = [
        "Why am I up/down today?",
        "What's coming up this week?",
        "Show best/worst positions",
        "What did the post-mortem flag?",
    ]
    for i, sg in enumerate(suggested):
        if sug_cols[i].button(sg, key=f"sg_{i}", use_container_width=True):
            st.session_state["_pending_user_input"] = sg

    # Fixed-height chat box
    chat_box = st.container(height=520, border=True)
    with chat_box:
        if not st.session_state.copilot_messages:
            st.caption("_no messages yet — click a suggested prompt or type below_")
        for msg in st.session_state.copilot_messages:
            if msg["role"] == "user":
                with st.chat_message("user"):
                    st.markdown(msg.get("display_text", str(msg.get("content", ""))))
            else:
                with st.chat_message("assistant"):
                    st.markdown(msg.get("display_text", ""))
                    if msg.get("tool_calls"):
                        with st.expander(f"🔧 {len(msg['tool_calls'])} tool call(s)", expanded=False):
                            for tc in msg["tool_calls"]:
                                st.markdown(f"**{tc['name']}**")
                                st.json(tc.get("input", {}), expanded=False)
                                st.caption("result:")
                                st.json(tc.get("result", {}), expanded=False)

    # Input below the box
    typed_input = st.chat_input("Ask the copilot...")
    pending = st.session_state.pop("_pending_user_input", None)
    user_input = typed_input or pending
    if user_input:
        st.session_state.copilot_messages.append({
            "role": "user", "display_text": user_input, "content": user_input,
        })
        with chat_box:
            with st.chat_message("user"):
                st.markdown(user_input)
            api_messages = []
            for m in st.session_state.copilot_messages:
                if m["role"] == "user":
                    api_messages.append({"role": "user", "content": m["content"]})
                elif m["role"] == "assistant" and m.get("api_content"):
                    api_messages.append({"role": "assistant", "content": m["api_content"]})
            with st.chat_message("assistant"):
                text_ph = st.empty()
                tool_ph = st.empty()
                acc = ""
                tool_log = []
                try:
                    from trader.copilot import stream_response
                    for ev in stream_response(api_messages):
                        if ev["type"] == "text_delta":
                            acc += ev["text"]
                            text_ph.markdown(acc + "▌")
                        elif ev["type"] == "tool_use_start":
                            tool_log.append({"name": ev["name"],
                                              "input": ev.get("input", {}),
                                              "result": None})
                            tool_ph.caption(f"🔧 calling `{ev['name']}`...")
                        elif ev["type"] == "tool_result":
                            if tool_log and tool_log[-1]["name"] == ev["name"]:
                                tool_log[-1]["result"] = ev["result"]
                            tool_ph.caption(
                                f"🔧 `{ev['name']}` returned ({len(tool_log)} call(s))")
                        elif ev["type"] == "complete":
                            text_ph.markdown(acc)
                            tool_ph.empty()
                            st.session_state.copilot_messages.append({
                                "role": "assistant",
                                "display_text": acc,
                                "api_content": ev["messages"][-1]["content"]
                                                if ev["messages"] else acc,
                                "tool_calls": tool_log,
                            })
                            # v3.56.0: persist thread on every assistant response
                            try:
                                from trader.copilot_storage import save_thread, ChatThread, new_thread as _new
                                cur_id = st.session_state.get("current_thread_id")
                                if not cur_id:
                                    _t = _new()
                                    cur_id = _t.id
                                    st.session_state.current_thread_id = _t.id
                                    st.session_state.current_thread_created_at = _t.created_at
                                cur = ChatThread(
                                    id=cur_id,
                                    title=st.session_state.get("current_thread_title", "(new chat)"),
                                    created_at=st.session_state.get("current_thread_created_at", ""),
                                    updated_at="",
                                    messages=st.session_state.copilot_messages,
                                )
                                save_thread(cur)
                                st.session_state.current_thread_title = cur.title
                                # v3.56.2: invalidate the sidebar list cache so
                                # the new/updated thread title appears
                                try:
                                    _cached_thread_list.clear()
                                except Exception:
                                    pass
                            except Exception:
                                pass
                            if tool_log:
                                with st.expander(f"🔧 {len(tool_log)} tool call(s)", expanded=False):
                                    for tc in tool_log:
                                        st.markdown(f"**{tc['name']}**")
                                        st.json(tc.get("input", {}), expanded=False)
                                        st.caption("result:")
                                        st.json(tc.get("result", {}), expanded=False)
                            break
                        elif ev["type"] == "error":
                            text_ph.error(f"Copilot error: {ev['error']}")
                            break
                except Exception as e:
                    text_ph.error(f"{type(e).__name__}: {e}")


# ============================================================
# View: Overview
# ============================================================
def view_overview():
    st.title("🏠 Overview")
    st.caption("Headline metrics + sector heatmap + last 5 runs.")
    _headline_metrics()
    st.divider()

    st.subheader("🗺️ Sector heatmap")
    st.caption("Tile size = position weight. Color = today's P&L %. Bloomberg IMAP-style.")
    try:
        from trader.portfolio_heatmap import heatmap_dataframe_dict, sector_summary
        live_hm = _live_portfolio()
        if getattr(live_hm, "error", None):
            st.caption(f"_heatmap unavailable: {live_hm.error}_")
        elif not live_hm.positions:
            st.caption("_no live positions to chart_")
        else:
            try:
                import plotly.express as px
                hm = heatmap_dataframe_dict(live_hm.positions)
                if hm["symbol"]:
                    df = pd.DataFrame({
                        "sector": hm["sector"], "symbol": hm["symbol"],
                        "weight": hm["weight"], "day_pl_pct": hm["day_pl_pct"],
                    })
                    fig = px.treemap(df, path=[px.Constant("Portfolio"), "sector", "symbol"],
                                      values="weight", color="day_pl_pct",
                                      color_continuous_scale="RdYlGn",
                                      color_continuous_midpoint=0, range_color=[-3, 3])
                    fig.update_layout(height=420, margin=dict(t=10, l=10, r=10, b=10))
                    st.plotly_chart(fig, use_container_width=True)
                ss = sector_summary(live_hm.positions)
                if ss:
                    st.markdown("**Sector summary**")
                    st.dataframe(ss, use_container_width=True, hide_index=True)
            except ImportError:
                st.info("plotly not installed in this image")
    except Exception as e:
        st.caption(f"_heatmap error: {type(e).__name__}: {e}_")

    st.divider()
    st.subheader("Latest runs")
    runs = query(str(DB_PATH), "SELECT * FROM runs ORDER BY started_at DESC LIMIT 5")
    if not runs.empty:
        st.dataframe(runs, use_container_width=True, hide_index=True)
    else:
        st.caption("_no runs in journal_")


# ============================================================
# View: Live positions
# ============================================================
def view_live_positions():
    st.title("💼 Live positions")
    st.caption("Mark-to-market, refreshed every 30s. Bloomberg MON-style. "
               "Day P&L vs yesterday's close (yfinance) + total unrealized vs avg cost.")
    live = _live_portfolio()
    if getattr(live, "error", None):
        st.warning(f"broker fetch failed: {live.error}")
        return
    cc = st.columns(4)
    cc[0].metric("Equity", f"${live.equity:,.0f}" if live.equity else "n/a")
    cc[1].metric("Cash", f"${live.cash:,.0f}" if live.cash else "n/a")
    cc[2].metric("Day P&L",
                 f"${live.total_day_pl_dollar:+,.0f}" if live.total_day_pl_dollar is not None else "n/a",
                 f"{live.total_day_pl_pct:+.2%}" if live.total_day_pl_pct is not None else None)
    cc[3].metric("Total unrealized", f"${live.total_unrealized_pl:+,.0f}")
    if live.positions:
        rows = [{
            "symbol": p.symbol, "sector": p.sector or "",
            "qty": f"{p.qty:.4f}",
            "avg_cost": f"${p.avg_cost:.2f}" if p.avg_cost else "",
            "last": f"${p.last_price:.2f}" if p.last_price else "",
            "weight": f"{p.weight_of_book*100:.1f}%" if p.weight_of_book else "",
            "market_val": f"${p.market_value:,.0f}" if p.market_value else "",
            "day_$": f"{p.day_pl_dollar:+,.0f}" if p.day_pl_dollar is not None else "",
            "day_%": f"{p.day_pl_pct*100:+.2f}%" if p.day_pl_pct is not None else "",
            "total_$": f"{p.unrealized_pl:+,.0f}" if p.unrealized_pl is not None else "",
            "total_%": f"{p.unrealized_pl_pct*100:+.2f}%" if p.unrealized_pl_pct is not None else "",
        } for p in live.positions]
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("_no open positions_")


# ============================================================
# View: Decisions
# ============================================================
def view_decisions():
    st.title("🎯 Decisions")
    st.caption("Each row = a decision the LIVE variant made. The **why** column "
               "is parsed from rationale stored at decision time. The **final** "
               "column shows variant_id + resulting weight.")
    decisions = query(str(DB_PATH),
                      "SELECT ts, ticker, action, style, score, rationale_json, final "
                      "FROM decisions ORDER BY ts DESC LIMIT 50")
    if decisions.empty:
        st.caption("_no decisions in journal_")
        return
    def fmt_why(raw):
        if not raw:
            return ""
        try:
            d = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return str(raw)[:120]
        if not isinstance(d, dict):
            return str(d)[:120]
        bits = []
        tr = d.get("trailing_return", d.get("momentum"))
        if tr is not None:
            bits.append(f"12-1 mom {tr*100:+.1f}%")
        if d.get("rsi") is not None:
            bits.append(f"RSI {d['rsi']:.0f}")
        if d.get("z_score") is not None:
            bits.append(f"z {d['z_score']:+.2f}")
        return " · ".join(bits) if bits else (str(d)[:120] if d else "")
    decisions["why"] = decisions["rationale_json"].apply(fmt_why)
    view = decisions[["ts", "ticker", "action", "style", "score", "why", "final"]]
    st.dataframe(view, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Recent orders (last 50)")
    orders = query(str(DB_PATH),
                   "SELECT ts, ticker, side, notional, alpaca_order_id, status, error "
                   "FROM orders ORDER BY ts DESC LIMIT 50")
    if not orders.empty:
        st.dataframe(orders, use_container_width=True, hide_index=True)
    else:
        st.caption("_no orders_")


# ============================================================
# View: Position lots
# ============================================================
def view_lots():
    st.title("📦 Position lots")
    st.caption("Sleeve-tagged open + closed lots. Realized P&L per closed lot.")
    lots = query(str(DB_PATH),
                 "SELECT id, symbol, sleeve, opened_at, qty, open_price, open_order_id "
                 "FROM position_lots WHERE closed_at IS NULL ORDER BY opened_at DESC")
    if not lots.empty:
        st.dataframe(lots, use_container_width=True, hide_index=True)
        sleeve_summary = lots.groupby("sleeve").agg(
            symbols=("symbol", "count"), total_qty=("qty", "sum")).reset_index()
        st.markdown("**Sleeve summary**")
        st.dataframe(sleeve_summary, use_container_width=True, hide_index=True)
    else:
        st.caption("_no open lots_")
    st.divider()
    st.subheader("Closed lots (last 30)")
    closed = query(str(DB_PATH),
                   "SELECT symbol, sleeve, opened_at, closed_at, qty, "
                   "open_price, close_price, realized_pnl FROM position_lots "
                   "WHERE closed_at IS NOT NULL ORDER BY closed_at DESC LIMIT 30")
    if not closed.empty:
        st.dataframe(closed, use_container_width=True, hide_index=True)
    else:
        st.caption("_no closed lots yet_")


# ============================================================
# View: Performance (equity vs SPY overlay)
# ============================================================
def view_performance():
    st.title("📈 Performance")
    st.caption("Equity (green) + SPY normalized (gray dashed) + drawdown (red). "
               "Bloomberg GP-style overlay.")
    snaps = query(str(DB_PATH), "SELECT * FROM daily_snapshot ORDER BY date DESC LIMIT 30")
    if snaps.empty or len(snaps) < 2:
        st.caption("_need ≥2 daily snapshots; sync from GitHub_")
        return
    chart_data = snaps[["date", "equity"]].copy()
    chart_data["date"] = pd.to_datetime(chart_data["date"])
    chart_data = chart_data.sort_values("date").set_index("date")
    eq = chart_data["equity"]
    peak = eq.cummax()
    dd_pct = (eq / peak - 1) * 100
    spy_norm = None
    try:
        import yfinance as yf
        spy_df = yf.download("SPY", start=eq.index.min().strftime("%Y-%m-%d"),
                              end=(eq.index.max() + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                              progress=False, auto_adjust=True)
        if spy_df is not None and not spy_df.empty:
            spy_close = spy_df["Close"].dropna()
            spy_norm = (spy_close / spy_close.iloc[0]) * float(eq.iloc[0])
    except Exception:
        pass
    try:
        import plotly.graph_objects as go
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=eq.index, y=eq.values, name="equity",
                                  line=dict(color="#16a34a", width=2)))
        if spy_norm is not None and not spy_norm.empty:
            fig.add_trace(go.Scatter(x=spy_norm.index, y=spy_norm.values, name="SPY (norm)",
                                      line=dict(color="#888888", width=1.5, dash="dash")))
        fig.add_trace(go.Scatter(x=dd_pct.index, y=dd_pct.values, name="drawdown %",
                                  yaxis="y2", fill="tozeroy",
                                  line=dict(color="rgba(220,38,38,0.4)")))
        fig.update_layout(
            height=500, hovermode="x unified",
            yaxis=dict(title="equity ($)", side="left"),
            yaxis2=dict(title="drawdown (%)", side="right", overlaying="y",
                         showgrid=False, range=[-50, 5]),
            margin=dict(t=20, l=10, r=10, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.02))
        st.plotly_chart(fig, use_container_width=True)
        if spy_norm is not None and not spy_norm.empty and len(eq) >= 2:
            eq_ret = (float(eq.iloc[-1]) - float(eq.iloc[0])) / float(eq.iloc[0])
            spy_ret = (float(spy_norm.iloc[-1]) - float(spy_norm.iloc[0])) / float(spy_norm.iloc[0])
            ec = st.columns(4)
            ec[0].metric("Equity total return", f"{eq_ret*100:+.2f}%")
            ec[1].metric("SPY total return", f"{spy_ret*100:+.2f}%")
            ec[2].metric("Excess vs SPY", f"{(eq_ret - spy_ret)*100:+.2f}%")
            ec[3].metric("Worst DD", f"{dd_pct.min():.2f}%")
    except ImportError:
        st.line_chart(chart_data["equity"])
        st.area_chart(pd.DataFrame({"drawdown_pct": dd_pct}))


# ============================================================
# View: Attribution (Brinson)
# ============================================================
def view_attribution():
    st.title("📊 Attribution")
    st.caption("Brinson decomposition: allocation (sector tilt) + selection (within-sector picks). "
               "Tells you WHY today's P&L moved.")
    try:
        from trader.brinson_attribution import compute_brinson, SECTOR_ETF_MAP
        live = _live_portfolio()
        if not live.positions:
            st.info("_no positions_")
            return
        sec_w_p, sec_r_num, sec_r_den = {}, {}, {}
        total_eq = sum((p.market_value or 0) for p in live.positions) or 1
        for p in live.positions:
            sec = p.sector or "Unknown"
            w = (p.market_value or 0) / total_eq
            sec_w_p[sec] = sec_w_p.get(sec, 0) + w
            if p.day_pl_pct is not None and (p.market_value or 0) > 0:
                sec_r_num[sec] = sec_r_num.get(sec, 0) + p.day_pl_pct * (p.market_value or 0)
                sec_r_den[sec] = sec_r_den.get(sec, 0) + (p.market_value or 0)
        sec_r_p = {s: (sec_r_num.get(s, 0) / sec_r_den.get(s, 1)) for s in sec_w_p}
        n = len(SECTOR_ETF_MAP) or 1
        sec_w_b = {s: 1.0 / n for s in SECTOR_ETF_MAP}
        try:
            import yfinance as yf
            etf_syms = list(SECTOR_ETF_MAP.values())
            df = yf.download(" ".join(etf_syms), period="5d", progress=False,
                              auto_adjust=True, group_by="ticker")
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
        cm = st.columns(3)
        cm[0].metric("Allocation", f"{rep.sum_allocation*100:+.3f}%")
        cm[1].metric("Selection", f"{rep.sum_selection*100:+.3f}%")
        cm[2].metric("Active return", f"{rep.active_return*100:+.3f}%")
        rows = [{
            "sector": s.sector,
            "port_w": f"{s.portfolio_weight*100:.1f}%",
            "bench_w": f"{s.benchmark_weight*100:.1f}%",
            "port_ret": f"{s.portfolio_sector_return*100:+.2f}%",
            "bench_ret": f"{s.benchmark_sector_return*100:+.2f}%",
            "alloc_eff": f"{s.allocation_effect*100:+.3f}%",
            "select_eff": f"{s.selection_effect*100:+.3f}%",
        } for s in rep.by_sector]
        st.dataframe(rows, use_container_width=True, hide_index=True)
    except Exception as e:
        st.warning(f"attribution unavailable: {type(e).__name__}: {e}")


# ============================================================
# View: Events
# ============================================================
def view_events():
    st.title("📅 Events")
    st.caption("FOMC + OPEX + earnings + ex-div for held names, next 30 days.")
    try:
        from trader.events_calendar import compute_upcoming_events
        live = _live_portfolio()
        symbols = [p.symbol for p in (live.positions or [])]
        events = compute_upcoming_events(symbols, days_ahead=30)
        if not events:
            st.info("_no upcoming events in 30 days_")
            return
        rows = []
        for e in events:
            emoji = {"earnings": "📊", "ex_div": "💵", "fomc": "🏦", "opex": "🎯"}.get(e.event_type, "📌")
            rows.append({
                "date": str(e.date), "in days": e.days_until,
                "type": f"{emoji} {e.event_type}",
                "symbol": e.symbol or "(portfolio-wide)",
                "note": e.note, "confidence": e.confidence,
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
    except Exception as e:
        st.warning(f"events unavailable: {type(e).__name__}: {e}")


# ============================================================
# View: Regime overlay
# ============================================================
def view_regime():
    st.title("🌡️ Regime overlay")
    st.caption("Live HMM + macro + GARCH composite. Disabled by default — "
               "computed for observability, not applied to LIVE allocation.")
    if st.button("🔄 Recompute now"):
        st.cache_data.clear()
    sig = _overlay_signal()
    if sig is None:
        st.error("could not compute overlay")
        return
    c = st.columns(4)
    c[0].metric("HMM regime", sig.hmm_regime, f"posterior {sig.hmm_posterior:.2%}")
    c[0].caption(f"sub-mult: {sig.hmm_mult:.2f}")
    macro_state = ("inv+wide" if sig.macro_curve_inverted and sig.macro_credit_widening
                   else "inv" if sig.macro_curve_inverted
                   else "wide" if sig.macro_credit_widening else "ok")
    c[1].metric("Macro", macro_state)
    c[1].caption(f"sub-mult: {sig.macro_mult:.2f}")
    c[2].metric("GARCH vol fc",
                f"{sig.garch_vol_forecast_annual*100:.1f}%" if sig.garch_vol_forecast_annual else "n/a",
                "target 15%")
    c[2].caption(f"sub-mult: {sig.garch_mult:.2f}")
    c[3].metric("Final mult", f"{sig.final_mult:.2f}",
                "DISABLED" if not sig.enabled else "ENABLED")
    st.code(sig.rationale, language=None)
    with st.expander("Raw signal"):
        st.json({
            "enabled": sig.enabled, "final_mult": sig.final_mult,
            "hmm": {"mult": sig.hmm_mult, "regime": sig.hmm_regime,
                    "posterior": sig.hmm_posterior, "error": sig.hmm_error},
            "macro": {"mult": sig.macro_mult, "curve_inverted": sig.macro_curve_inverted,
                      "credit_widening": sig.macro_credit_widening, "error": sig.macro_error},
            "garch": {"mult": sig.garch_mult,
                      "vol_forecast_annual": sig.garch_vol_forecast_annual,
                      "error": sig.garch_error},
        })


# ============================================================
# View: Intraday risk
# ============================================================
def view_intraday():
    st.title("⚡ Intraday risk")
    st.caption("Defensive intraday DD monitor. Updated every 30 min during market hours.")
    intraday = read_state_file(str(ROOT / "data" / "intraday_risk_log.json"))
    if isinstance(intraday, list) and intraday:
        df = pd.DataFrame(intraday[-200:]).iloc[::-1].reset_index(drop=True)
        st.dataframe(df, use_container_width=True, hide_index=True)
        recent = [e for e in intraday[-50:] if e.get("equity_now")]
        if recent:
            actions = pd.Series([e.get("action", "ok") for e in recent]).value_counts()
            st.markdown("**Action breakdown (last 50 checks)**")
            st.dataframe(actions.reset_index().rename(
                columns={"index": "action", 0: "count"}),
                use_container_width=True, hide_index=True)
    else:
        st.caption("_no intraday log yet_")


# ============================================================
# View: Shadow variants
# ============================================================
def view_shadows():
    st.title("👥 Shadow variants")
    with st.expander("ℹ️ What is a shadow variant?"):
        st.markdown("""A **shadow variant** runs alongside LIVE every day but places NO orders.
It records what it would have done. We have ~12 shadows. The 3-gate
promotion pipeline (survivor → PIT → CPCV) lets us collect 30+ days of
live evidence before any candidate graduates to LIVE. 40+ candidates have
been killed via this pipeline (see docs/CRITIQUE.md).""")
    cutoff_iso = (datetime.utcnow() - timedelta(days=7)).isoformat()
    shadows = query(str(DB_PATH),
                    "SELECT variant_id, ts, targets_json FROM shadow_decisions "
                    "WHERE ts >= ? ORDER BY ts DESC LIMIT 200", (cutoff_iso,))
    if shadows.empty:
        st.caption("_no shadow decisions in last 7 days_")
        return
    rows = []
    for _, r in shadows.iterrows():
        try:
            targets = json.loads(r["targets_json"])
            if not targets:
                rows.append({"variant_id": r["variant_id"], "ts": r["ts"],
                             "n_picks": 0, "gross": 0.0, "top5": "(empty)"})
            else:
                sorted_t = sorted(targets.items(), key=lambda kv: -kv[1])
                top5 = ", ".join(f"{k}({v*100:.1f}%)" for k, v in sorted_t[:5])
                rows.append({"variant_id": r["variant_id"], "ts": r["ts"],
                             "n_picks": len(targets),
                             "gross": sum(targets.values()),
                             "top5": top5})
        except Exception as e:
            rows.append({"variant_id": r["variant_id"], "ts": r["ts"],
                         "n_picks": -1, "gross": 0.0, "top5": f"err: {e}"})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ============================================================
# View: Sleeve health
# ============================================================
def view_sleeve_health():
    st.title("🔍 Sleeve health")
    st.caption("Cross-sleeve correlation + per-sleeve rolling Sharpe + auto-demote recommendations.")
    try:
        from trader.sleeve_health import compute_health
        @st.cache_data(ttl=600)
        def _health():
            return compute_health()
        rep = _health()
        emoji = {"green": "✅", "yellow": "⚠️", "red": "🚨"}.get(rep.overall_health, "❔")
        st.markdown(f"### {emoji} **{rep.overall_health.upper()}**")
        st.caption(rep.rationale)
        if rep.per_sleeve:
            st.markdown("**Per-sleeve rolling Sharpe (90d)**")
            sleeve_rows = [{
                "sleeve": s.sleeve_id, "status": s.status, "n_obs": s.n_observations,
                "sharpe": f"{s.rolling_sharpe:.2f}" if s.rolling_sharpe is not None else "n/a",
                "sortino": f"{s.rolling_sortino:.2f}" if s.rolling_sortino is not None else "n/a",
                "vol_ann": f"{s.rolling_vol_annual*100:.1f}%" if s.rolling_vol_annual else "n/a",
                "flagged": "⚠️" if s.flagged_for_demote else "",
            } for s in rep.per_sleeve]
            st.dataframe(sleeve_rows, use_container_width=True, hide_index=True)
        if rep.correlations:
            st.markdown("**Cross-sleeve correlations (60d)**")
            corr_rows = [{"a": c.sleeve_a, "b": c.sleeve_b,
                          "correlation": f"{c.correlation:+.3f}",
                          "n": c.n_observations,
                          "alert": "⚠️ over threshold" if c.over_threshold else ""}
                         for c in rep.correlations]
            st.dataframe(corr_rows, use_container_width=True, hide_index=True)
        if rep.demote_recommendations:
            st.warning(f"{len(rep.demote_recommendations)} demote recommendation(s)")
            for d in rep.demote_recommendations:
                with st.expander(f"⚠️ {d['sleeve_id']} → {d['proposed_status']}"):
                    st.json(d)
    except Exception as e:
        st.warning(f"sleeve health unavailable: {type(e).__name__}: {e}")


# ============================================================
# View: Postmortems
# ============================================================
def view_postmortems():
    st.title("📜 Postmortems")
    st.caption("Nightly self-review (Claude reads yesterday's decisions + today's reaction).")
    pm = query(str(DB_PATH),
               "SELECT date, pnl_pct, summary, proposed_tweak FROM postmortems "
               "ORDER BY date DESC LIMIT 14")
    if pm.empty:
        st.caption("_no postmortems_")
        return
    for _, r in pm.iterrows():
        with st.expander(f"{r['date']} · pnl {r['pnl_pct']*100:+.2f}%"):
            st.markdown(f"**Summary**: {r['summary']}")
            st.markdown(f"**Proposed tweak**: {r['proposed_tweak']}")


# ============================================================
# View: Reports
# ============================================================
def view_reports():
    st.title("📄 Reports")
    st.caption("Per-run Markdown decision reports (data/reports/run_*.md). "
               "Survives even if email isn't configured. Diffable across runs.")
    try:
        from trader.decision_report import list_reports, REPORTS_DIR
        reports = list_reports(limit=200)
    except Exception as e:
        st.error(f"could not list reports: {e}")
        return
    if not reports:
        st.info("_no reports yet_")
        return
    labels = [f"{r.name}  ({datetime.fromtimestamp(r.stat().st_mtime).strftime('%Y-%m-%d %H:%M')})"
              for r in reports]
    choice = st.selectbox(f"Report ({len(reports)} available, newest first)",
                          labels, index=0)
    chosen = reports[labels.index(choice)]
    col_a, col_b = st.columns([1, 4])
    with col_a:
        st.download_button("⬇️ Download", chosen.read_bytes(),
                            file_name=chosen.name, mime="text/markdown")
        if len(reports) > 1 and st.checkbox("Diff vs previous"):
            prev = reports[1]
            import difflib
            diff = difflib.unified_diff(
                prev.read_text().splitlines(keepends=True),
                chosen.read_text().splitlines(keepends=True),
                fromfile=prev.name, tofile=chosen.name, n=3)
            diff_text = "".join(diff)
            if diff_text:
                st.code(diff_text, language="diff")
            else:
                st.success("no differences")
    with col_b:
        st.markdown("---")
        st.markdown(chosen.read_text())


# ============================================================
# View: Manual triggers
# ============================================================
def view_manual():
    st.title("🔧 Manual triggers")
    st.warning("⚠️ Every manual workflow_dispatch increments the **peek_counter**. "
               "More than 3 in a 30-day window will alert. Use sparingly.")
    wf_options = {
        "trader-daily-run": "daily-run.yml",
        "trader-hourly-reconcile": "hourly-reconcile.yml",
        "trader-intraday-risk-watch": "intraday-risk-watch.yml",
        "trader-readiness-and-dd-alerts": "readiness-and-dd-alerts.yml",
        "trader-backfill-journal": "backfill-journal.yml",
    }
    wf_choice = st.selectbox("Workflow to dispatch", list(wf_options.keys()))
    confirm = st.text_input("Type 'I-MEANT-TO' to enable", key="confirm_dispatch")
    if confirm == "I-MEANT-TO":
        if st.button(f"⚡ Dispatch {wf_choice}"):
            try:
                res = subprocess.run(["gh", "workflow", "run", wf_options[wf_choice]],
                                      cwd=ROOT, capture_output=True, text=True, timeout=30)
                if res.returncode == 0:
                    st.success("dispatched")
                else:
                    st.error(f"failed: {res.stderr}")
            except FileNotFoundError:
                st.error("`gh` CLI not available")
            except Exception as e:
                st.error(f"{type(e).__name__}: {e}")
    else:
        st.button(f"⚡ Dispatch {wf_choice}", disabled=True,
                  help="type 'I-MEANT-TO' above to enable")


# ============================================================
# View: Settings
# ============================================================
def view_settings():
    st.title("⚙️ Settings")
    st.caption("Journal path, GitHub sync, data freshness, refresh config, system info.")

    st.subheader("Journal")
    new_path = st.text_input("journal path", value=st.session_state.db_path)
    if new_path != st.session_state.db_path:
        st.session_state.db_path = new_path
        st.cache_data.clear()
        st.success("journal path updated")
        st.rerun()
    if Path(st.session_state.db_path).exists():
        mtime = datetime.fromtimestamp(Path(st.session_state.db_path).stat().st_mtime)
        age = datetime.now() - mtime
        st.caption(f"journal.db updated **{int(age.total_seconds() // 60)}m {int(age.total_seconds() % 60)}s ago** ({mtime.strftime('%Y-%m-%d %H:%M')})")
    else:
        st.warning("journal.db not found at that path")

    st.subheader("Sync from GitHub")
    st.caption("Pulls the latest trader-journal artifact. Auth options: "
               "(1) GH_TOKEN env var (set on host before docker compose up); "
               "(2) bind-mount ~/.config/gh with token auth (NOT keyring — "
               "macOS Keychain doesn't survive a bind-mount).")
    if st.button("⬇️ Pull latest journal artifact"):
        with st.spinner("running gh api..."):
            try:
                res = subprocess.run(
                    ["gh", "api",
                     "repos/{owner}/{repo}/actions/artifacts?name=trader-journal&per_page=10",
                     "--jq",
                     "[.artifacts[] | select(.expired == false)] | sort_by(.created_at) | "
                     "reverse | .[0] | {id: .id, run_id: .workflow_run.id, created_at: .created_at}"],
                    cwd=ROOT, capture_output=True, text=True, timeout=30)
                if res.returncode != 0:
                    st.error(f"gh api failed: {res.stderr}")
                else:
                    meta = json.loads(res.stdout)
                    if not meta or not meta.get("run_id"):
                        st.warning("no trader-journal artifact found")
                    else:
                        st.info(f"downloading run {meta['run_id']}...")
                        Path(st.session_state.db_path).parent.mkdir(parents=True, exist_ok=True)
                        dl = subprocess.run(
                            ["gh", "run", "download", str(meta["run_id"]),
                             "-n", "trader-journal", "-D",
                             str(Path(st.session_state.db_path).parent)],
                            cwd=ROOT, capture_output=True, text=True, timeout=60)
                        if dl.returncode != 0:
                            st.error(f"download failed: {dl.stderr}")
                        else:
                            st.success(f"updated from run {meta['run_id']}")
                            st.cache_data.clear()
                            st.rerun()
            except FileNotFoundError:
                st.error("`gh` CLI not installed")
            except Exception as e:
                st.error(f"{type(e).__name__}: {e}")

    st.subheader("Auto-refresh")
    st.session_state.refresh_sec = st.slider(
        "refresh interval (seconds)", 5, 300, st.session_state.refresh_sec)
    st.session_state.auto_refresh_enabled = st.checkbox(
        "auto-refresh", value=st.session_state.auto_refresh_enabled,
        help="WARNING: refreshes interrupt in-progress chat streams. Off by default.")

    st.subheader("Cache")
    if st.button("🗑️ Clear all caches"):
        st.cache_data.clear()
        st.success("caches cleared")

    st.subheader("System info")
    st.json({
        "version": "v3.55.0",
        "journal_path": st.session_state.db_path,
        "data_dir": str(ROOT / "data"),
        "reports_dir": str(ROOT / "data" / "reports"),
        "reference_docs": [
            "docs/AI_NATIVE_REFACTOR_DESIGN.md",
            "docs/V4_PARADIGM_SHIFT.md",
            "docs/SWARM_VERIFICATION_PROTOCOL.md",
            "docs/CRITIQUE.md",
            "docs/BEHAVIORAL_PRECOMMIT.md",
        ],
    }, expanded=False)


# ============================================================
# Main dispatch
# ============================================================
VIEW_DISPATCH = {
    "chat": view_chat,
    "overview": view_overview,
    "live_positions": view_live_positions,
    "decisions": view_decisions,
    "lots": view_lots,
    "performance": view_performance,
    "attribution": view_attribution,
    "events": view_events,
    "regime": view_regime,
    "intraday": view_intraday,
    "shadows": view_shadows,
    "sleeve_health": view_sleeve_health,
    "postmortems": view_postmortems,
    "reports": view_reports,
    "manual": view_manual,
    "settings": view_settings,
}

active = st.session_state.active_view
view_fn = VIEW_DISPATCH.get(active, view_chat)
view_fn()

# ============================================================
# Auto-refresh (off by default in v3.55; user must enable in Settings)
# ============================================================
if st.session_state.auto_refresh_enabled and active != "chat":
    # Don't auto-refresh while on chat view — it interrupts stream
    placeholder = st.empty()
    placeholder.caption(f"auto-refresh in {st.session_state.refresh_sec}s · disable in Settings")
    time.sleep(st.session_state.refresh_sec)
    st.rerun()
