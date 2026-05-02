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
                   "workflow run. Requires `gh` CLI authenticated.")

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
st.caption(f"v3.49.x · journal: `{DB_PATH}` · last UI refresh: {datetime.now().strftime('%H:%M:%S')}")


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


# Top metrics row
col1, col2, col3, col4, col5 = st.columns(5)

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

    # 30d return
    if len(snaps) >= 2:
        first_eq = float(snaps.iloc[-1]["equity"])
        ret_30d = (eq - first_eq) / first_eq if first_eq > 0 else 0
        col4.metric("Recent return", f"{ret_30d:+.2%}", f"over {len(snaps)} snapshots")
else:
    col1.metric("Equity", "n/a", "no snapshots in journal")

# Freeze state badge
freeze = read_state_file(str(ROOT / "data" / "risk_freeze_state.json"))
if freeze.get("liquidation_gate_tripped"):
    col5.error("🚨 LIQUIDATION GATE TRIPPED")
elif "deploy_dd_freeze_until" in freeze:
    until = datetime.fromisoformat(freeze["deploy_dd_freeze_until"])
    col5.warning(f"❄️ DEPLOY-DD FREEZE until {until.strftime('%m/%d %H:%M')}")
elif "daily_loss_freeze_until" in freeze:
    until = datetime.fromisoformat(freeze["daily_loss_freeze_until"])
    col5.warning(f"❄️ DAILY-LOSS FREEZE until {until.strftime('%m/%d %H:%M')}")
else:
    col5.success("✅ No freeze active")

# ============================================================
# Tabs
# ============================================================
tabs = st.tabs([
    "🏠 Overview",
    "🎯 Decisions",
    "📦 Positions",
    "🌡️ Regime overlay",
    "👥 Shadow variants",
    "⚡ Intraday risk",
    "📈 Performance",
    "📜 Postmortems",
    "🔧 Manual",
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

    decisions = query(str(DB_PATH),
                      "SELECT ts, ticker, action, style, score, final FROM decisions "
                      "ORDER BY ts DESC LIMIT 50")
    if not decisions.empty:
        st.dataframe(decisions, use_container_width=True, hide_index=True)
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

# ---------------- Performance ----------------
with tabs[6]:
    st.subheader("Equity curve")
    if not snaps.empty and len(snaps) >= 2:
        chart_data = snaps[["date", "equity"]].copy()
        chart_data["date"] = pd.to_datetime(chart_data["date"])
        chart_data = chart_data.sort_values("date").set_index("date")
        st.line_chart(chart_data["equity"])

        # Drawdown chart
        eq = chart_data["equity"]
        peak = eq.cummax()
        dd = (eq / peak - 1) * 100
        dd_chart = pd.DataFrame({"drawdown_pct": dd})
        st.subheader("Drawdown (%)")
        st.area_chart(dd_chart)
    else:
        st.caption("need ≥ 2 daily snapshots to draw curves")

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

# ---------------- Manual actions ----------------
with tabs[8]:
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

# ============================================================
# Auto-refresh timer (must be at end so all UI renders first)
# ============================================================
if ENABLE_AUTO:
    placeholder = st.empty()
    placeholder.caption(f"auto-refresh in {REFRESH_SEC}s · uncheck in sidebar to pause")
    time.sleep(REFRESH_SEC)
    st.rerun()
