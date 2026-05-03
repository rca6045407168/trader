"""Live local dashboard for the trader.

Read-only Streamlit UI showing decisions, positions, regime overlay, freeze
state, shadow variants, and performance. Auto-refreshes every N seconds.

Run locally:
    streamlit run scripts/dashboard.py
Or in Docker:
    docker compose up -d dashboard
    open http://localhost:8501

The dashboard reads from data/journal.db (SQLite) — the same file that
GitHub Actions writes via the trader-journal artifact, and that local
docker-run smoke tests write to. Pull latest from GitHub via the sidebar
button (requires `gh` CLI authenticated to the repo).

Why read-only by default: this is a viewer, not a trader. The actual
trading happens via cron (GitHub Actions today, GCP Cloud Run after
migration). Manual trigger buttons are gated behind a confirmation +
the existing peek_counter (anything you do here counts toward the
3/30d limit).
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
# Sidebar — refresh + data source + GitHub sync
# ============================================================
st.sidebar.title("trader dashboard")
st.sidebar.caption("read-only viewer · refreshes from journal.db")

DEFAULT_DB = ROOT / "data" / "journal.db"
DB_PATH = Path(st.sidebar.text_input("journal path", str(DEFAULT_DB)))

REFRESH_SEC = st.sidebar.slider("auto-refresh (seconds)", 5, 300, 30)
ENABLE_AUTO = st.sidebar.checkbox("auto-refresh", value=True)

st.sidebar.divider()
st.sidebar.subheader("Sync from GitHub")
st.sidebar.caption("Pulls the latest trader-journal artifact from any "
                   "workflow run. Auth options (in priority order): "
                   "(1) GH_TOKEN env var — fastest, set on host before "
                   "`docker compose up`; (2) bind-mount `~/.config/gh` "
                   "with token auth (NOT keyring — macOS Keychain doesn't "
                   "survive a bind-mount). On host: `gh auth login` → "
                   "choose 'Paste an authentication token'.")

if st.sidebar.button("⬇️  Pull latest journal artifact"):
    with st.spinner("running gh api..."):
        try:
            # Find latest non-expired trader-journal artifact across all workflows
            res = subprocess.run(
                ["gh", "api",
                 "repos/{owner}/{repo}/actions/artifacts?name=trader-journal&per_page=10",
                 "--jq",
                 "[.artifacts[] | select(.expired == false)] | sort_by(.created_at) | "
                 "reverse | .[0] | {id: .id, run_id: .workflow_run.id, created_at: .created_at}"],
                cwd=ROOT, capture_output=True, text=True, timeout=30,
            )
            if res.returncode != 0:
                st.sidebar.error(f"gh api failed: {res.stderr}")
            else:
                meta = json.loads(res.stdout)
                if not meta or not meta.get("run_id"):
                    st.sidebar.warning("no trader-journal artifact found")
                else:
                    st.sidebar.info(f"downloading run {meta['run_id']} (created {meta['created_at']})...")
                    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                    dl = subprocess.run(
                        ["gh", "run", "download", str(meta["run_id"]),
                         "-n", "trader-journal", "-D", str(DB_PATH.parent)],
                        cwd=ROOT, capture_output=True, text=True, timeout=60,
                    )
                    if dl.returncode != 0:
                        st.sidebar.error(f"download failed: {dl.stderr}")
                    else:
                        st.sidebar.success(f"journal updated from run {meta['run_id']}")
                        st.cache_data.clear()
                        st.rerun()
        except FileNotFoundError:
            st.sidebar.error("`gh` CLI not installed in container. Run from host instead.")
        except Exception as e:
            st.sidebar.error(f"{type(e).__name__}: {e}")

st.sidebar.divider()
st.sidebar.subheader("Data freshness")
if DB_PATH.exists():
    mtime = datetime.fromtimestamp(DB_PATH.stat().st_mtime)
    age = datetime.now() - mtime
    age_str = f"{int(age.total_seconds() // 60)}m {int(age.total_seconds() % 60)}s ago"
    st.sidebar.caption(f"journal.db updated **{age_str}** ({mtime.strftime('%Y-%m-%d %H:%M')})")
else:
    st.sidebar.warning("journal.db not found at this path")

# ============================================================
# Top header — system state + headline metrics
# ============================================================
st.title("trader · live dashboard")
st.caption(f"v3.50.2 · journal: `{DB_PATH}` · last UI refresh: {datetime.now().strftime('%H:%M:%S')}")


@st.cache_data(ttl=10)
def query(path_str: str, sql: str, params: tuple = ()) -> pd.DataFrame:
    """Read-only query against the journal."""
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


# Top metrics row — 6 cols now: Equity / Cash / vs anchor / Window / Regime / Freeze
col1, col2, col3, col4, col5, col6 = st.columns(6)

# Compute regime overlay snapshot up-front so the headline can show it.
# Cached 5 min so we don't recompute on every dashboard refresh.
@st.cache_data(ttl=300)
def _headline_overlay():
    try:
        from trader.regime_overlay import compute_overlay
        return compute_overlay()
    except Exception as e:
        return None

overlay_top = _headline_overlay()

snaps = query(str(DB_PATH), "SELECT * FROM daily_snapshot ORDER BY date DESC LIMIT 30")
if not snaps.empty:
    latest = snaps.iloc[0]
    yest = snaps.iloc[1] if len(snaps) > 1 else latest
    eq = float(latest["equity"])
    eq_change = eq - float(yest["equity"])
    col1.metric("Equity", f"${eq:,.0f}", f"{eq_change:+,.0f}")
    col2.metric("Cash", f"${float(latest['cash']):,.0f}",
                f"{(float(latest['cash'])/eq*100):.1f}% of book")

    # vs deployment anchor
    anchor = read_state_file(str(ROOT / "data" / "deployment_anchor.json"))
    if anchor:
        anchor_eq = float(anchor.get("equity_at_deploy", 0))
        if anchor_eq > 0:
            dd = (eq - anchor_eq) / anchor_eq
            col3.metric("vs deployment anchor", f"{dd:+.2%}", f"${anchor_eq:,.0f} baseline")

    # Window return — needs >=2 daily snapshots. If we only have 1 (e.g. just
    # synced a fresh GitHub artifact), use deployment_anchor as the baseline
    # so the user still sees the +6.50% number they expect.
    if len(snaps) >= 2:
        first_eq = float(snaps.iloc[-1]["equity"])
        ret_window = (eq - first_eq) / first_eq if first_eq > 0 else 0
        col4.metric("Window return", f"{ret_window:+.2%}", f"{len(snaps)} snapshots")
    else:
        if anchor and float(anchor.get("equity_at_deploy", 0)) > 0:
            anchor_eq = float(anchor["equity_at_deploy"])
            since_anchor = (eq - anchor_eq) / anchor_eq
            col4.metric("Since deployment", f"{since_anchor:+.2%}",
                        f"only 1 snapshot — anchor baseline")
        else:
            col4.metric("Window return", "need ≥2 snapshots", "sync from GitHub")
else:
    col1.metric("Equity", "n/a", "no snapshots in journal")

# Regime headline (col5) — shows HMM regime + final overlay multiplier so user
# sees market state at a glance without clicking into the Regime overlay tab.
if overlay_top is not None:
    regime_label = overlay_top.hmm_regime.upper() if overlay_top.hmm_regime else "?"
    regime_emoji = {"BULL": "🟢", "BEAR": "🔴", "TRANSITION": "🟡"}.get(regime_label, "⚪")
    col5.metric(f"{regime_emoji} Regime", regime_label,
                f"overlay {overlay_top.final_mult:.2f}×"
                + (" (DISABLED)" if not overlay_top.enabled else ""))
else:
    col5.metric("Regime", "computing…", "see overlay tab")

# Freeze state badge (col6)
freeze = read_state_file(str(ROOT / "data" / "risk_freeze_state.json"))
if freeze.get("liquidation_gate_tripped"):
    col6.error("🚨 LIQ GATE")
elif "deploy_dd_freeze_until" in freeze:
    until = datetime.fromisoformat(freeze["deploy_dd_freeze_until"])
    col5.warning(f"❄️ DEPLOY-DD FREEZE until {until.strftime('%m/%d %H:%M')}")
elif "daily_loss_freeze_until" in freeze:
    until = datetime.fromisoformat(freeze["daily_loss_freeze_until"])
    col6.warning(f"❄️ DAILY-LOSS FREEZE")
else:
    col6.success("✅ No freeze")

# ============================================================
# Tabs
# ============================================================
# v3.52.0: Bloomberg-inspired additions appended at the end. Original 10
# tabs unchanged; new 4 (Live positions, Events, Attribution, Sleeve health)
# at indices 10-13.
tabs = st.tabs([
    "🏠 Overview",            # 0  TODAY
    "🎯 Decisions",           # 1  TIME
    "📦 Positions (lots)",    # 2  TIME
    "🌡️ Regime overlay",      # 3  TODAY
    "👥 Shadow variants",     # 4  RESEARCH
    "⚡ Intraday risk",        # 5  TODAY
    "📈 Performance",         # 6  TIME
    "📜 Postmortems",         # 7  TIME
    "📄 Reports",             # 8  TIME
    "🔧 Manual",              # 9  RESEARCH
    "💼 Live positions",     # 10 TODAY (Bloomberg MON, v3.52.0)
    "📅 Events",             # 11 TODAY (Bloomberg EVTS, v3.52.0)
    "📊 Attribution",        # 12 TIME (Bloomberg PORT / Brinson, v3.52.1)
    "🔍 Sleeve health",       # 13 RESEARCH (sleeve correlation + decay, v3.51.0)
    "🤖 Copilot",            # 14 PRIMARY (AI Copilot, v3.53.0) — chat-first interface
])

# ---------------- Overview ----------------
with tabs[0]:
    st.subheader("Pre-flight gate state")

    c1, c2, c3 = st.columns(3)

    with c1:
        anchor = read_state_file(str(ROOT / "data" / "deployment_anchor.json"))
        st.markdown("**Deployment anchor**")
        if anchor:
            st.json({
                "equity_at_deploy": f"${float(anchor.get('equity_at_deploy', 0)):,.0f}",
                "deploy_timestamp": anchor.get("deploy_timestamp", "?"),
                "source": anchor.get("source", "?"),
                "notes": anchor.get("notes", ""),
            })
        else:
            st.caption("not yet set (first daily-run will set it)")

    with c2:
        override = read_state_file(str(ROOT / "data" / "override_delay_state.json"))
        st.markdown("**Override-delay**")
        if override:
            st.json(override)
        else:
            st.caption("no SHA recorded yet")

    with c3:
        peek_log = read_state_file(str(ROOT / "data" / "peek_log.json"))
        st.markdown("**Peek counter** (manual triggers)")
        if isinstance(peek_log, list):
            cutoff = datetime.utcnow() - timedelta(days=30)
            recent = [e for e in peek_log
                      if datetime.fromisoformat(e.get("ts", "")) > cutoff]
            st.metric("manual triggers / 30d", len(recent))
            if len(recent) >= 3:
                st.warning(f"⚠️ {len(recent)} ≥ 3 — peek_counter alert threshold")
        else:
            st.caption("no events logged")

    st.divider()

    # ---- v3.52.0: Bloomberg IMAP-style sector heatmap ----
    st.subheader("🗺️ Sector heatmap (live, 30s cache)")
    st.caption("Tile size = position weight. Color = today's P&L %. Bloomberg IMAP-style.")
    try:
        from trader.positions_live import fetch_live_portfolio
        from trader.portfolio_heatmap import heatmap_dataframe_dict, sector_summary

        @st.cache_data(ttl=30)
        def _heatmap_portfolio():
            return fetch_live_portfolio()

        live_hm = _heatmap_portfolio()
        if live_hm.error:
            st.caption(f"_heatmap unavailable: {live_hm.error}_")
        elif not live_hm.positions:
            st.caption("_no live positions to chart_")
        else:
            try:
                import plotly.express as px
                import pandas as _pd
                hm = heatmap_dataframe_dict(live_hm.positions)
                if hm["symbol"]:
                    # v3.52.3 FIX: use path=[Constant, sector, symbol] so plotly
                    # auto-builds the parent hierarchy. The previous names/
                    # parents pattern silently rendered empty when sector
                    # parent rows weren't present in `names`.
                    df = _pd.DataFrame({
                        "sector": hm["sector"],
                        "symbol": hm["symbol"],
                        "weight": hm["weight"],
                        "day_pl_pct": hm["day_pl_pct"],
                    })
                    fig = px.treemap(
                        df,
                        path=[px.Constant("Portfolio"), "sector", "symbol"],
                        values="weight", color="day_pl_pct",
                        color_continuous_scale="RdYlGn",
                        color_continuous_midpoint=0,
                        range_color=[-3, 3],
                        hover_data={"weight": ":.2f", "day_pl_pct": ":.2f"},
                    )
                    fig.update_layout(height=420, margin=dict(t=10, l=10, r=10, b=10))
                    st.plotly_chart(fig, use_container_width=True)
                ss = sector_summary(live_hm.positions)
                if ss:
                    st.markdown("**Sector summary**")
                    st.dataframe(ss, use_container_width=True, hide_index=True)
            except ImportError:
                st.info("plotly not installed in this image — rebuild dashboard")
    except Exception as e:
        st.caption(f"_heatmap error: {type(e).__name__}: {e}_")

    st.divider()
    st.subheader("Latest run")

    runs = query(str(DB_PATH),
                 "SELECT * FROM runs ORDER BY started_at DESC LIMIT 5")
    if not runs.empty:
        st.dataframe(runs, use_container_width=True, hide_index=True)
    else:
        st.caption("no runs in journal")

# ---------------- Decisions ----------------
with tabs[1]:
    st.subheader("Recent decisions (last 50)")
    st.caption("Each row is a decision the LIVE variant made. The **why** column "
               "is parsed from the rationale stored at decision time — typically "
               "the trailing 12-1 momentum return that drove the score, plus any "
               "variant-specific reasoning. The **final** column shows the "
               "variant_id that owns the decision and the resulting weight.")

    decisions = query(str(DB_PATH),
                      "SELECT ts, ticker, action, style, score, rationale_json, final "
                      "FROM decisions ORDER BY ts DESC LIMIT 50")
    if not decisions.empty:
        # Parse rationale_json into a 'why' column for human reading.
        def _format_why(raw):
            if not raw:
                return ""
            try:
                d = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                return str(raw)[:120]
            if not isinstance(d, dict):
                return str(d)[:120]
            # Common rationale fields produced by rank_momentum / find_bottoms
            tr = d.get("trailing_return", d.get("momentum"))
            why_explicit = d.get("why", "")
            bits = []
            if tr is not None:
                bits.append(f"12-1 mom {tr*100:+.1f}%")
            if d.get("rsi") is not None:
                bits.append(f"RSI {d['rsi']:.0f}")
            if d.get("z_score") is not None:
                bits.append(f"z {d['z_score']:+.2f}")
            if why_explicit:
                bits.append(why_explicit)
            return " · ".join(bits) if bits else (str(d)[:120] if d else "")
        decisions["why"] = decisions["rationale_json"].apply(_format_why)
        # Reorder + drop the raw json column
        view = decisions[["ts", "ticker", "action", "style", "score", "why", "final"]]
        st.dataframe(view, use_container_width=True, hide_index=True)
    else:
        st.caption("no decisions in journal yet")

    st.subheader("Recent orders (last 50)")
    orders = query(str(DB_PATH),
                   "SELECT ts, ticker, side, notional, alpaca_order_id, status, error "
                   "FROM orders ORDER BY ts DESC LIMIT 50")
    if not orders.empty:
        st.dataframe(orders, use_container_width=True, hide_index=True)
    else:
        st.caption("no orders in journal yet")

# ---------------- Positions ----------------
with tabs[2]:
    st.subheader("Open position lots (sleeve-tagged)")

    lots = query(str(DB_PATH),
                 "SELECT id, symbol, sleeve, opened_at, qty, open_price, open_order_id "
                 "FROM position_lots WHERE closed_at IS NULL ORDER BY opened_at DESC")
    if not lots.empty:
        # Compute current value if we have latest snapshot
        st.dataframe(lots, use_container_width=True, hide_index=True)

        # Sleeve summary
        sleeve_summary = lots.groupby("sleeve").agg(
            symbols=("symbol", "count"),
            total_qty=("qty", "sum"),
        ).reset_index()
        st.markdown("**Sleeve summary**")
        st.dataframe(sleeve_summary, use_container_width=True, hide_index=True)
    else:
        st.caption("no open lots in journal")

    st.subheader("Closed lots (last 30)")
    closed = query(str(DB_PATH),
                   "SELECT symbol, sleeve, opened_at, closed_at, qty, "
                   "open_price, close_price, realized_pnl FROM position_lots "
                   "WHERE closed_at IS NOT NULL ORDER BY closed_at DESC LIMIT 30")
    if not closed.empty:
        st.dataframe(closed, use_container_width=True, hide_index=True)
    else:
        st.caption("no closed lots yet")

# ---------------- Regime overlay (live computation) ----------------
with tabs[3]:
    st.subheader("Live regime overlay state")
    st.caption("Recomputes from current SPY data + macro + GARCH every refresh. "
               "If REGIME_OVERLAY_ENABLED=false (default), the multiplier is "
               "computed for observability but NOT applied to LIVE allocation.")

    if st.button("🔄 Recompute now"):
        st.cache_data.clear()

    @st.cache_data(ttl=300)  # heavy — 5 min cache
    def compute_overlay():
        try:
            from trader.regime_overlay import compute_overlay as fn
            sig = fn()
            return {
                "enabled": sig.enabled,
                "final_mult": sig.final_mult,
                "rationale": sig.rationale,
                "hmm": {"mult": sig.hmm_mult, "regime": sig.hmm_regime,
                        "posterior": sig.hmm_posterior, "error": sig.hmm_error},
                "macro": {"mult": sig.macro_mult, "curve_inverted": sig.macro_curve_inverted,
                          "credit_widening": sig.macro_credit_widening, "error": sig.macro_error},
                "garch": {"mult": sig.garch_mult, "vol_forecast_annual": sig.garch_vol_forecast_annual,
                          "error": sig.garch_error},
                "timestamp": sig.timestamp,
            }
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    with st.spinner("computing HMM + macro + GARCH..."):
        sig = compute_overlay()

    if "error" in sig:
        st.error(sig["error"])
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("HMM regime", sig["hmm"]["regime"],
                  f"posterior {sig['hmm']['posterior']:.2%}")
        c1.caption(f"sub-mult: {sig['hmm']['mult']:.2f}")

        macro_state = ("inv+wide" if sig["macro"]["curve_inverted"] and sig["macro"]["credit_widening"]
                       else "inv" if sig["macro"]["curve_inverted"]
                       else "wide" if sig["macro"]["credit_widening"]
                       else "ok")
        c2.metric("Macro", macro_state)
        c2.caption(f"sub-mult: {sig['macro']['mult']:.2f}")

        c3.metric("GARCH vol fc",
                  f"{sig['garch']['vol_forecast_annual']*100:.1f}%" if sig['garch']['vol_forecast_annual'] else "n/a",
                  f"target 15%")
        c3.caption(f"sub-mult: {sig['garch']['mult']:.2f}")

        c4.metric("Final multiplier", f"{sig['final_mult']:.2f}",
                  "DISABLED" if not sig["enabled"] else "ENABLED")

        st.code(sig["rationale"], language=None)
        with st.expander("Raw signal"):
            st.json(sig)

# ---------------- Shadow variants ----------------
with tabs[4]:
    st.subheader("Shadow variant decisions (last 7 days)")
    with st.expander("ℹ️ What is a shadow variant?", expanded=False):
        st.markdown("""
A **shadow variant** is a strategy that runs alongside the LIVE strategy on every
daily run but **places NO orders and uses NO real capital**. It just records what
it *would* have done. We have ~12 of these registered today (see `variants.py`):
top-3 concentrated, top-3 full deploy, residual momentum, HMM-aggressive, etc.

**Why we use shadows:**
1. New strategy ideas are dangerous — 80%+ of backtested strategies fail live.
2. Running them as shadows lets us collect 30+ days of evidence on **real** market
   conditions (not just historical backtest) before risking any capital.
3. The **3-gate promotion pipeline** (survivor 5-regime → PIT → CPCV) decides if a
   shadow is allowed to graduate to LIVE. 40+ candidates have failed this pipeline
   and been killed (`docs/CRITIQUE.md`).
4. The current LIVE variant `momentum_top15_mom_weighted_v1` was a shadow itself
   for 30+ days before promotion in v3.42 — the only candidate to survive PIT
   validation (Sharpe stayed within sampling noise of the prior baseline).

**How to read this tab:**
- Each row is one shadow's daily decision (last 7 days)
- `n_picks` = number of names the shadow chose
- `gross` = total % of capital the shadow would have deployed
- `top5` = the largest 5 picks by weight
- A shadow that consistently beats LIVE by Sharpe ≥0.2 over 30+ days is a
  promotion candidate — then it has to pass CPCV before going LIVE.
""")

    cutoff_iso = (datetime.utcnow() - timedelta(days=7)).isoformat()
    shadows = query(str(DB_PATH),
                    "SELECT variant_id, ts, targets_json FROM shadow_decisions "
                    "WHERE ts >= ? ORDER BY ts DESC LIMIT 200",
                    (cutoff_iso,))
    if not shadows.empty:
        # Parse targets_json into name/weight rows
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
    else:
        st.caption("no shadow decisions in last 7 days")

    st.subheader("Registered variants")
    variants = query(str(DB_PATH),
                     "SELECT variant_id, name, version, status, description "
                     "FROM variants ORDER BY status, variant_id")
    if not variants.empty:
        st.dataframe(variants, use_container_width=True, hide_index=True)

# ---------------- Intraday risk ----------------
with tabs[5]:
    st.subheader("Intraday risk log (last 200 entries)")
    st.caption("Updated by intraday-risk-watch.yml every 30 min during market hours.")

    intraday = read_state_file(str(ROOT / "data" / "intraday_risk_log.json"))
    if isinstance(intraday, list) and intraday:
        df = pd.DataFrame(intraday[-200:])
        # Reverse so newest first
        df = df.iloc[::-1].reset_index(drop=True)
        st.dataframe(df, use_container_width=True, hide_index=True)

        # Summary metrics
        recent = [e for e in intraday[-50:] if e.get("equity_now")]
        if recent:
            actions = pd.Series([e.get("action", "ok") for e in recent]).value_counts()
            st.markdown("**Action breakdown (last 50 checks)**")
            st.dataframe(actions.reset_index().rename(
                columns={"index": "action", 0: "count"}),
                use_container_width=True, hide_index=True)
    else:
        st.caption("no intraday log yet (first run will populate)")

# ---------------- Performance (v3.52.1: Bloomberg GP-style overlay) ----------------
with tabs[6]:
    st.subheader("Equity curve vs SPY benchmark")
    st.caption("Equity (green line) + SPY normalized to same start (gray dashed) "
               "+ drawdown from rolling peak (red area). Bloomberg GP-style "
               "single-chart overlay so the eye can compare excess return + DD "
               "in one glance.")
    if not snaps.empty and len(snaps) >= 2:
        chart_data = snaps[["date", "equity"]].copy()
        chart_data["date"] = pd.to_datetime(chart_data["date"])
        chart_data = chart_data.sort_values("date").set_index("date")
        eq = chart_data["equity"]
        peak = eq.cummax()
        dd_pct = (eq / peak - 1) * 100

        # Pull SPY benchmark over the same window
        spy_normalized = None
        try:
            import yfinance as yf
            spy_df = yf.download("SPY", start=eq.index.min().strftime("%Y-%m-%d"),
                                  end=(eq.index.max() + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                                  progress=False, auto_adjust=True)
            if spy_df is not None and not spy_df.empty:
                spy_close = spy_df["Close"].dropna()
                # Normalize SPY to start at the same equity value
                spy_normalized = (spy_close / spy_close.iloc[0]) * float(eq.iloc[0])
        except Exception:
            spy_normalized = None

        try:
            import plotly.graph_objects as go
            fig = go.Figure()
            # Equity line
            fig.add_trace(go.Scatter(
                x=eq.index, y=eq.values, name="equity",
                line=dict(color="#16a34a", width=2),
                hovertemplate="%{x|%Y-%m-%d}<br>$%{y:,.0f}<extra></extra>",
            ))
            # SPY benchmark dashed
            if spy_normalized is not None and not spy_normalized.empty:
                fig.add_trace(go.Scatter(
                    x=spy_normalized.index, y=spy_normalized.values,
                    name="SPY (normalized)",
                    line=dict(color="#888888", width=1.5, dash="dash"),
                    hovertemplate="%{x|%Y-%m-%d}<br>SPY $%{y:,.0f}<extra></extra>",
                ))
            # Drawdown on a secondary y-axis (red area, below 0)
            fig.add_trace(go.Scatter(
                x=dd_pct.index, y=dd_pct.values, name="drawdown %",
                yaxis="y2", fill="tozeroy",
                line=dict(color="rgba(220,38,38,0.4)"),
                hovertemplate="%{x|%Y-%m-%d}<br>DD %{y:.2f}%<extra></extra>",
            ))
            fig.update_layout(
                height=500,
                hovermode="x unified",
                yaxis=dict(title="equity ($)", side="left"),
                yaxis2=dict(title="drawdown (%)", side="right",
                            overlaying="y", showgrid=False, range=[-50, 5]),
                margin=dict(t=20, l=10, r=10, b=10),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            st.plotly_chart(fig, use_container_width=True)

            # Excess return summary
            if spy_normalized is not None and not spy_normalized.empty and len(eq) >= 2:
                eq_total_ret = (float(eq.iloc[-1]) - float(eq.iloc[0])) / float(eq.iloc[0])
                spy_total_ret = (float(spy_normalized.iloc[-1]) - float(spy_normalized.iloc[0])) / float(spy_normalized.iloc[0])
                excess = eq_total_ret - spy_total_ret
                ec1, ec2, ec3, ec4 = st.columns(4)
                ec1.metric("Equity total return", f"{eq_total_ret*100:+.2f}%")
                ec2.metric("SPY total return", f"{spy_total_ret*100:+.2f}%")
                ec3.metric("Excess vs SPY", f"{excess*100:+.2f}%")
                ec4.metric("Worst DD", f"{dd_pct.min():.2f}%")
        except ImportError:
            # plotly missing — fall back to two stacked charts
            st.line_chart(chart_data["equity"])
            st.subheader("Drawdown (%)")
            st.area_chart(pd.DataFrame({"drawdown_pct": dd_pct}))
    else:
        st.caption("need ≥ 2 daily snapshots to draw curves — sync from GitHub "
                   "via sidebar to populate")

# ---------------- Postmortems ----------------
with tabs[7]:
    st.subheader("Recent post-mortem analyses (last 14 days)")
    pm = query(str(DB_PATH),
               "SELECT date, pnl_pct, summary, proposed_tweak FROM postmortems "
               "ORDER BY date DESC LIMIT 14")
    if not pm.empty:
        for _, r in pm.iterrows():
            with st.expander(f"{r['date']} · pnl {r['pnl_pct']*100:+.2f}%"):
                st.markdown(f"**Summary**: {r['summary']}")
                st.markdown(f"**Proposed tweak**: {r['proposed_tweak']}")
    else:
        st.caption("no postmortems in journal")

# ---------------- Reports ----------------
with tabs[8]:
    st.subheader("Per-run decision reports")
    st.caption("Permanent Markdown reports written by `decision_report.write_report` "
               "at the end of each daily-run. Survives even if email isn't configured. "
               "Diffable across runs to see what changed.")

    try:
        sys.path.insert(0, str(ROOT / "src"))
        from trader.decision_report import list_reports, REPORTS_DIR
        reports = list_reports(limit=200)
    except Exception as e:
        st.error(f"could not list reports: {e}")
        reports = []

    if not reports:
        st.info(f"No reports yet at `{ROOT / 'data' / 'reports'}/`. "
                f"They'll appear after the next daily-run completes "
                f"(GitHub Actions cron OR a local `docker run ... scripts/run_daily.py`).")
    else:
        labels = [f"{r.name}  ({datetime.fromtimestamp(r.stat().st_mtime).strftime('%Y-%m-%d %H:%M')})"
                  for r in reports]
        choice = st.selectbox(f"Select report ({len(reports)} available, newest first)",
                              labels, index=0)
        chosen = reports[labels.index(choice)]
        st.caption(f"Path: `{chosen}` · {chosen.stat().st_size:,} bytes")

        col_a, col_b = st.columns([1, 4])
        with col_a:
            st.download_button("⬇️ Download", chosen.read_bytes(),
                                file_name=chosen.name,
                                mime="text/markdown")
            if len(reports) > 1:
                if st.checkbox("Diff vs previous report"):
                    prev = reports[1]
                    import difflib
                    diff = difflib.unified_diff(
                        prev.read_text().splitlines(keepends=True),
                        chosen.read_text().splitlines(keepends=True),
                        fromfile=prev.name, tofile=chosen.name, n=3,
                    )
                    diff_text = "".join(diff)
                    if diff_text:
                        st.code(diff_text, language="diff")
                    else:
                        st.success("no differences vs previous report")
        with col_b:
            st.markdown("---")
            st.markdown(chosen.read_text())


# ---------------- Manual actions ----------------
with tabs[9]:
    st.subheader("Manual triggers")
    st.warning("⚠️ Every manual trigger increments the **peek_counter**. "
               "More than 3 in a 30-day window will alert. The whole point of "
               "the cron pattern is that you don't need to push buttons. "
               "Use these only for genuine ops events.")

    c1, c2 = st.columns(2)

    with c1:
        st.markdown("**Trigger workflows on GitHub** (counted by peek_counter)")
        wf_options = {
            "trader-daily-run": "daily-run.yml",
            "trader-hourly-reconcile": "hourly-reconcile.yml",
            "trader-intraday-risk-watch": "intraday-risk-watch.yml",
            "trader-readiness-and-dd-alerts": "readiness-and-dd-alerts.yml",
            "trader-backfill-journal": "backfill-journal.yml",
        }
        wf_choice = st.selectbox("Workflow to dispatch", list(wf_options.keys()))
        confirm = st.text_input("Type 'I-MEANT-TO' to enable button", key="confirm_dispatch")
        if confirm == "I-MEANT-TO":
            if st.button(f"⚡ Dispatch {wf_choice}"):
                try:
                    res = subprocess.run(
                        ["gh", "workflow", "run", wf_options[wf_choice]],
                        cwd=ROOT, capture_output=True, text=True, timeout=30,
                    )
                    if res.returncode == 0:
                        st.success(f"dispatched. Watch at https://github.com/.../actions")
                    else:
                        st.error(f"failed: {res.stderr}")
                except FileNotFoundError:
                    st.error("`gh` CLI not available in this environment")
                except Exception as e:
                    st.error(f"{type(e).__name__}: {e}")
        else:
            st.button(f"⚡ Dispatch {wf_choice}", disabled=True,
                      help="type 'I-MEANT-TO' above to enable")

    with c2:
        st.markdown("**Local cache management**")
        if st.button("🗑️  Clear Streamlit cache"):
            st.cache_data.clear()
            st.success("cache cleared; next refresh recomputes everything")

        if st.button("🔄 Force UI refresh"):
            st.rerun()

# ---------------- v3.52.0: Live positions (Bloomberg MON) ----------------
with tabs[10]:
    st.subheader("💼 Live positions — mark-to-market")
    st.caption("Pulls Alpaca positions every 30s. Day P&L vs yesterday's "
               "close (yfinance) + total unrealized P&L vs avg cost. **The "
               "between-rebalances trading view.**")
    try:
        from trader.positions_live import fetch_live_portfolio

        @st.cache_data(ttl=30)
        def _live_portfolio_tab():
            return fetch_live_portfolio()

        live = _live_portfolio_tab()
        if live.error:
            st.warning(f"broker fetch failed: {live.error}")
        else:
            cc1, cc2, cc3, cc4 = st.columns(4)
            cc1.metric("Equity (live)", f"${live.equity:,.0f}" if live.equity else "n/a")
            cc2.metric("Cash", f"${live.cash:,.0f}" if live.cash else "n/a")
            cc3.metric(
                "Day P&L",
                f"${live.total_day_pl_dollar:+,.0f}" if live.total_day_pl_dollar is not None else "n/a",
                f"{live.total_day_pl_pct:+.2%}" if live.total_day_pl_pct is not None else None,
            )
            cc4.metric("Total unrealized", f"${live.total_unrealized_pl:+,.0f}")

            if live.positions:
                rows = []
                for p in live.positions:
                    rows.append({
                        "symbol": p.symbol,
                        "sector": p.sector or "",
                        "qty": f"{p.qty:.4f}",
                        "avg_cost": f"${p.avg_cost:.2f}" if p.avg_cost else "",
                        "last": f"${p.last_price:.2f}" if p.last_price else "",
                        "weight": f"{p.weight_of_book*100:.1f}%" if p.weight_of_book else "",
                        "market_val": f"${p.market_value:,.0f}" if p.market_value else "",
                        "day_$": f"{p.day_pl_dollar:+,.0f}" if p.day_pl_dollar is not None else "",
                        "day_%": f"{p.day_pl_pct*100:+.2f}%" if p.day_pl_pct is not None else "",
                        "total_$": f"{p.unrealized_pl:+,.0f}" if p.unrealized_pl is not None else "",
                        "total_%": f"{p.unrealized_pl_pct*100:+.2f}%" if p.unrealized_pl_pct is not None else "",
                    })
                st.dataframe(rows, use_container_width=True, hide_index=True)
            else:
                st.info("no open positions in broker account")
    except Exception as e:
        st.warning(f"live positions tab unavailable: {type(e).__name__}: {e}")

# ---------------- v3.52.0: Events calendar (Bloomberg EVTS) ----------------
with tabs[11]:
    st.subheader("📅 Upcoming events: held names + portfolio-wide")
    st.caption("Earnings + ex-div per held name (yfinance) + FOMC (hard-coded "
               "2026 calendar) + monthly OPEX (3rd Friday). The 'what could "
               "blow up my book this week' view.")
    try:
        from trader.positions_live import fetch_live_portfolio
        from trader.events_calendar import compute_upcoming_events

        @st.cache_data(ttl=600)
        def _events():
            live = fetch_live_portfolio()
            symbols = [p.symbol for p in (live.positions or [])]
            return compute_upcoming_events(symbols, days_ahead=30)

        with st.spinner("fetching earnings calendar..."):
            events = _events()

        if not events:
            st.info("no upcoming events in next 30 days")
        else:
            rows = []
            for e in events:
                emoji = {"earnings": "📊", "ex_div": "💵",
                         "fomc": "🏦", "opex": "🎯"}.get(e.event_type, "📌")
                rows.append({
                    "date": str(e.date),
                    "in days": e.days_until,
                    "type": f"{emoji} {e.event_type}",
                    "symbol": e.symbol or "(portfolio-wide)",
                    "note": e.note,
                    "confidence": e.confidence,
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)
    except Exception as e:
        st.warning(f"events tab unavailable: {type(e).__name__}: {e}")

# ---------------- v3.52.1: Attribution (Bloomberg PORT / Brinson) ----------------
with tabs[12]:
    st.subheader("📊 Brinson PnL attribution")
    st.caption("Decomposes day P&L into **allocation effect** (sector "
               "over/underweighting) + **selection effect** (within-sector "
               "name picking) + **interaction**. Tells you WHY you made/lost "
               "money, not just how much. Brinson, Hood, Beebower (1986).")
    try:
        from trader.positions_live import fetch_live_portfolio
        from trader.brinson_attribution import compute_brinson, SECTOR_ETF_MAP

        @st.cache_data(ttl=300)
        def _brinson_today():
            """Today's Brinson, day-level. Uses live mark-to-market sector
            returns + SPDR sector ETF returns as benchmark."""
            live = fetch_live_portfolio()
            if not live.positions:
                return None, "no positions"

            # Aggregate portfolio sector weights + sector returns (cap-weighted)
            sec_w_p: dict = {}
            sec_r_num: dict = {}
            sec_r_den: dict = {}
            total_eq = sum((p.market_value or 0) for p in live.positions)
            for p in live.positions:
                sec = p.sector or "Unknown"
                w = (p.market_value or 0) / total_eq if total_eq > 0 else 0
                sec_w_p[sec] = sec_w_p.get(sec, 0) + w
                if p.day_pl_pct is not None and (p.market_value or 0) > 0:
                    sec_r_num[sec] = sec_r_num.get(sec, 0) + (p.day_pl_pct * (p.market_value or 0))
                    sec_r_den[sec] = sec_r_den.get(sec, 0) + (p.market_value or 0)
            sec_r_p = {s: (sec_r_num.get(s, 0) / sec_r_den.get(s, 1)) for s in sec_w_p}

            # Pull benchmark (SPDR sector ETF) returns via yfinance
            try:
                import yfinance as yf
                etf_syms = [SECTOR_ETF_MAP.get(s) for s in sec_w_p if SECTOR_ETF_MAP.get(s)]
                if etf_syms:
                    df = yf.download(" ".join(etf_syms), period="5d",
                                      progress=False, auto_adjust=True, group_by="ticker")
                    sec_r_b: dict = {}
                    for sec, etf in SECTOR_ETF_MAP.items():
                        try:
                            if len(etf_syms) == 1:
                                closes = df["Close"].dropna()
                            else:
                                closes = df[(etf, "Close")].dropna() if (etf, "Close") in df.columns else df[etf]["Close"].dropna()
                            if len(closes) >= 2:
                                sec_r_b[sec] = (float(closes.iloc[-1]) - float(closes.iloc[-2])) / float(closes.iloc[-2])
                        except Exception:
                            continue
                else:
                    sec_r_b = {}
            except Exception:
                sec_r_b = {}

            # Crude SPY-equal-weight benchmark weights — proxy until we have
            # historical SPY sector weights cached. Each sector = its share of
            # SPDR sector ETFs by current ETF AUM is the right answer; using
            # equal-weight as a placeholder so the math runs.
            n = len(SECTOR_ETF_MAP) or 1
            sec_w_b = {s: 1.0 / n for s in SECTOR_ETF_MAP}

            return compute_brinson(
                portfolio_weights=sec_w_p,
                portfolio_sector_returns=sec_r_p,
                benchmark_weights=sec_w_b,
                benchmark_sector_returns=sec_r_b,
            ), None

        report, err = _brinson_today()
        if err:
            st.info(err)
        elif report:
            cm1, cm2, cm3 = st.columns(3)
            cm1.metric("Allocation effect", f"{report.sum_allocation*100:+.3f}%")
            cm2.metric("Selection effect", f"{report.sum_selection*100:+.3f}%")
            cm3.metric("Active return", f"{report.active_return*100:+.3f}%")
            rows = []
            for s in report.by_sector:
                rows.append({
                    "sector": s.sector,
                    "port_w": f"{s.portfolio_weight*100:.1f}%",
                    "bench_w": f"{s.benchmark_weight*100:.1f}%",
                    "port_ret": f"{s.portfolio_sector_return*100:+.2f}%",
                    "bench_ret": f"{s.benchmark_sector_return*100:+.2f}%",
                    "alloc_eff": f"{s.allocation_effect*100:+.3f}%",
                    "select_eff": f"{s.selection_effect*100:+.3f}%",
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)
            with st.expander("How to read this"):
                st.markdown("""
- **Allocation effect**: P&L from over/underweighting sectors that the
  benchmark moved on. Positive if you overweighted winners.
- **Selection effect**: P&L from picking outperforming names within a
  sector. Positive if your picks beat the sector ETF.
- **Interaction effect**: cross-term; usually small.
- **Caveat**: benchmark sector weights are currently equal-weight
  placeholder. Real SPY sector weights need historical cap-weighted data
  (todo: cache via yfinance — v3.52.2).
                """)
    except Exception as e:
        st.warning(f"attribution unavailable: {type(e).__name__}: {e}")

# ---------------- v3.51.0: Sleeve health ----------------
with tabs[13]:
    st.subheader("🔍 Sleeve health — correlation + decay monitor")
    st.caption("Cross-sleeve correlation (60d rolling Pearson) + per-sleeve "
               "rolling Sharpe (90d) + auto-demote recommendations. Defensive "
               "observability over the multi-sleeve thesis.")
    try:
        from trader.sleeve_health import compute_health

        @st.cache_data(ttl=600)
        def _health():
            return compute_health()

        rep = _health()
        health_color = {"green": "✅", "yellow": "⚠️", "red": "🚨"}.get(
            rep.overall_health, "❔")
        st.markdown(f"### {health_color} Overall: **{rep.overall_health.upper()}**")
        st.caption(rep.rationale)

        st.markdown("**Per-sleeve rolling Sharpe (90d)**")
        if rep.per_sleeve:
            sleeve_rows = []
            for s in rep.per_sleeve:
                sleeve_rows.append({
                    "sleeve": s.sleeve_id,
                    "status": s.status,
                    "n_obs": s.n_observations,
                    "sharpe": f"{s.rolling_sharpe:.2f}" if s.rolling_sharpe is not None else "n/a",
                    "sortino": f"{s.rolling_sortino:.2f}" if s.rolling_sortino is not None else "n/a",
                    "vol_ann": f"{s.rolling_vol_annual*100:.1f}%" if s.rolling_vol_annual else "n/a",
                    "flagged": "⚠️" if s.flagged_for_demote else "",
                    "reason": s.flag_reason,
                })
            st.dataframe(sleeve_rows, use_container_width=True, hide_index=True)
        else:
            st.caption("no sleeve data yet")

        st.markdown("**Cross-sleeve correlations (60d)**")
        if rep.correlations:
            corr_rows = [{"a": c.sleeve_a, "b": c.sleeve_b,
                          "correlation": f"{c.correlation:+.3f}",
                          "n": c.n_observations,
                          "alert": "⚠️ over threshold" if c.over_threshold else ""}
                         for c in rep.correlations]
            st.dataframe(corr_rows, use_container_width=True, hide_index=True)
        else:
            st.caption("need ≥2 sleeves with closed lots to compute correlations")

        if rep.demote_recommendations:
            st.warning(f"{len(rep.demote_recommendations)} demote recommendation(s)")
            for d in rep.demote_recommendations:
                with st.expander(f"⚠️ {d['sleeve_id']} → {d['proposed_status']}"):
                    st.json(d)
    except Exception as e:
        st.warning(f"sleeve health unavailable: {type(e).__name__}: {e}")

# ---------------- v3.53.0: AI Copilot (the AI-native primary surface) ----------------
with tabs[14]:
    st.subheader("🤖 Trader Copilot")
    st.caption("Chat-first AI interface. Ask anything about your portfolio, "
               "recent decisions, regime, performance — the copilot has 10 "
               "tools (live portfolio, regime overlay, decisions, attribution, "
               "sleeve health, events, journal SQL, post-mortems, scenario sim, "
               "period summary) and uses them autonomously.")

    # Suggested questions panel
    with st.expander("💡 Suggested questions", expanded=False):
        st.markdown("""
- *Why am I down today?*
- *What's my exposure to FOMC tomorrow?*
- *Show me my best and worst-performing positions this week.*
- *What's the regime overlay saying right now — should I be worried?*
- *If NVDA gaps -10% tomorrow, what's the portfolio impact?*
- *Are any of my sleeves showing decay?*
- *Summarize what changed between this week and last.*
- *What did the post-mortem agent flag yesterday?*
        """)

    # Initialize chat history in session state
    if "copilot_messages" not in st.session_state:
        st.session_state.copilot_messages = []

    # Render existing messages
    for msg in st.session_state.copilot_messages:
        if msg["role"] == "user":
            with st.chat_message("user"):
                st.markdown(msg.get("display_text", str(msg.get("content", ""))))
        elif msg["role"] == "assistant":
            with st.chat_message("assistant"):
                st.markdown(msg.get("display_text", ""))
                if msg.get("tool_calls"):
                    with st.expander(f"🔧 {len(msg['tool_calls'])} tool call(s)", expanded=False):
                        for tc in msg["tool_calls"]:
                            st.markdown(f"**{tc['name']}**")
                            st.json(tc.get("input", {}), expanded=False)
                            st.caption("result:")
                            st.json(tc.get("result", {}), expanded=False)

    # Input box
    user_input = st.chat_input("Ask the copilot anything about your portfolio...")

    if user_input:
        # Append user msg + render
        st.session_state.copilot_messages.append({
            "role": "user",
            "display_text": user_input,
            "content": user_input,
        })
        with st.chat_message("user"):
            st.markdown(user_input)

        # Build the API messages list (display_text is for UI only; API needs role+content)
        api_messages = []
        for m in st.session_state.copilot_messages:
            if m["role"] == "user":
                api_messages.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant" and m.get("api_content"):
                api_messages.append({"role": "assistant", "content": m["api_content"]})

        # Stream the response
        with st.chat_message("assistant"):
            text_placeholder = st.empty()
            tool_log_placeholder = st.empty()
            accumulated_text = ""
            tool_calls_log = []

            try:
                from trader.copilot import stream_response
                for event in stream_response(api_messages):
                    if event["type"] == "text_delta":
                        accumulated_text += event["text"]
                        text_placeholder.markdown(accumulated_text + "▌")
                    elif event["type"] == "tool_use_start":
                        tool_calls_log.append({
                            "name": event["name"],
                            "input": event.get("input", {}),
                            "result": None,
                        })
                        tool_log_placeholder.caption(
                            f"🔧 calling `{event['name']}`...")
                    elif event["type"] == "tool_result":
                        if tool_calls_log and tool_calls_log[-1]["name"] == event["name"]:
                            tool_calls_log[-1]["result"] = event["result"]
                        tool_log_placeholder.caption(
                            f"🔧 `{event['name']}` returned ({len(tool_calls_log)} call(s) so far)")
                    elif event["type"] == "complete":
                        text_placeholder.markdown(accumulated_text)
                        tool_log_placeholder.empty()
                        # Persist this turn for next round
                        st.session_state.copilot_messages.append({
                            "role": "assistant",
                            "display_text": accumulated_text,
                            "api_content": event["messages"][-1]["content"]
                                            if event["messages"] else accumulated_text,
                            "tool_calls": tool_calls_log,
                        })
                        # Render tool log expander
                        if tool_calls_log:
                            with st.expander(f"🔧 {len(tool_calls_log)} tool call(s)", expanded=False):
                                for tc in tool_calls_log:
                                    st.markdown(f"**{tc['name']}**")
                                    st.json(tc.get("input", {}), expanded=False)
                                    st.caption("result:")
                                    st.json(tc.get("result", {}), expanded=False)
                        break
                    elif event["type"] == "error":
                        text_placeholder.error(f"Copilot error: {event['error']}")
                        break
            except Exception as e:
                text_placeholder.error(f"{type(e).__name__}: {e}")

# ============================================================
# Auto-refresh timer (must be at end so all UI renders first)
# ============================================================
# v3.53.0: auto-refresh disabled while Copilot has unsent state. Otherwise
# a mid-stream refresh kills the in-flight chat.
if ENABLE_AUTO:
    placeholder = st.empty()
    placeholder.caption(f"auto-refresh in {REFRESH_SEC}s · uncheck in sidebar to pause "
                         · "(disabled if Copilot is streaming)")
    time.sleep(REFRESH_SEC)
    st.rerun()
