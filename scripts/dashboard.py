"""Live local dashboard for the trader (v4.0.0 freeze).

v3.73.3 — Risk roadmap dashboard view. Surfaces the 6 Round-2
advisory-swarm docs (RISK_FRAMEWORK / ADVERSARIAL_THREAT_MODEL /
TAIL_RISK_PLAYBOOK / FUND_FAILURE_CASE_STUDIES /
INFORMATION_THEORY_ALPHA + the synthesis) inside the dashboard,
and tracks the prioritized Block A pre-LIVE TODO list with auto-
resolved 'shipped / pending' status — each Block A item checks
for an implementation artifact (file existence, function presence,
constant value) so future commits flip status without editing this
view.

Block A status today:
  ✅ #2 hash-pinned deps (resolved by detecting --hash=sha256: in
        requirements.txt)
  ⬜ #3 four-threshold drawdown protocol (✓ shipped in v3.73.2 —
        flips to ✅ on next dashboard reload)
  ⬜ #6 cron heartbeat (✓ shipped in v3.73.0)
  Other items pending or v5-specific.

Adds 🛡️ Risk roadmap to the Diagnostics nav group.

v3.73.2 — Four-threshold drawdown protocol (Round-2 Block A item #3).
Per docs/RISK_FRAMEWORK.md §6, extends the existing single -8% kill
into four tiers with pre-committed response actions:

  -5% YELLOW         pause new sizing; weekly→biweekly review
  -8% RED            existing kill — halt all rebalancing (unchanged)
  -12% ESCALATION    trim core to top 5; raise cash to 50%
  -15% CATASTROPHIC  liquidate all; manual re-arm + 30d cool-off

Defaults to ADVISORY mode (logs the tier without mutating targets).
ENFORCING mode wires the response actions into check_account_risk —
opt in via DRAWDOWN_PROTOCOL_MODE=ENFORCING env. Same SHADOW/LIVE/
INERT pattern as the v3.69.0 ReactorSignalRule.

Adds a "🛡️ Drawdown protocol" expander to Overview that surfaces:
  - Current tier (with emoji indicator)
  - Tier-strip showing which thresholds have been crossed
  - The response action verbatim from RISK_FRAMEWORK.md §6
  - Mode badge (ADVISORY vs ENFORCING) + the env var to flip it

The existing -8% kill remains binding regardless of mode — this
release ADDS tiers around it, not replaces it.

v3.73.1 — Build-info badge + drift detector + production-pickling fix.

Three things ship together because they share a root cause: today's
"Overview shows Friday's $106K" episode revealed that container
drift goes invisible without active discipline. v3.73.1 makes drift
loud + fixes the latent crash that surfaced once the container was
finally rebuilt.

  - Dockerfile.dashboard now bakes /app/BUILD_INFO.txt with git
    commit + UTC timestamp at image-build time. docker-compose.yml
    passes them via build args (BUILD_COMMIT, BUILD_TIMESTAMP).
  - scripts/build_dashboard.sh wraps `docker compose build` so the
    operator doesn't have to remember the env-var dance.
  - dashboard.py sidebar reads BUILD_INFO and compares `built_at` to
    the file mtime of dashboard.py. If host code is >60s newer than
    the image, fires a yellow warning with the exact drift duration
    + the exact commands to fix.
  - **Production crash fix:** _read_disk_overlay() returned a
    local-class instance which pickle.dumps cannot serialize. The
    new container had empty Streamlit cache → first call to
    _overlay_signal (@st.cache_data) hit the disk path → AttrError.
    Replaced with types.SimpleNamespace which IS picklable. This
    bug was latent pre-v3.66.0 because the cache was pre-warmed
    from the dataclass path. Caught by today's container rebuild.

v3.73.0 — Daily-orchestrator heartbeat alert. Silent cron failure was
the top operational-risk blindspot. Real evidence at the time of this
build: yesterday (Mon May 4) had ZERO rows in journal.runs — the daily
orchestrator did not fire and there was no
alert.

  - scripts/check_daily_heartbeat.py runs idempotently. Detects
    "trading day + no run started today" and fires an email + Slack
    alert via the existing notify pipeline. Date-stamped marker file
    suppresses repeat alerts within the same day.
  - infra/launchd/com.trader.daily-heartbeat.plist fires Mon-Fri at
    14:30 UTC (= 10:30 ET) — after the 13:10 UTC daily-run window so
    healthy runs have ~80 min to complete before the check fires.
  - Tests verify the state machine: skip on weekends/holidays, alert
    on no-run-today, idempotent within a day, marker resets daily,
    plist scheduled correctly.

To install the launchd job: `bash scripts/install_launchd_earnings.sh`
already supports the pattern; add a similar one for the heartbeat or
manually `launchctl load ~/Library/LaunchAgents/com.trader.daily-heartbeat.plist`.

v3.72.2 — Operational fix: docker-compose healthcheck used `wget` but
Dockerfile.dashboard only installs `curl`. Result: 4593 consecutive
healthcheck failures across 38h while the dashboard actually ran
fine. Switched to `curl -fs`. Plus a regression test guarding the
binary used by the healthcheck against the binaries actually
installed in the image.

This release does NOT include code changes to dashboard.py beyond
the version label — the SOT bug the user reported ("Overview tab
still shows Friday's number") was caused by their container running
a 38h-old image that predates v3.66.0's EquityState refactor. Fix is
operational: `docker compose build dashboard && docker compose up -d
--force-recreate dashboard` to pick up everything from v3.65.0 →
v3.72.2.

v3.72.1 — Structured "Why we own it" panel in the per-symbol modal.
Replaces the old single-line `12-1 mom +35.5%` rationale with four
explicit sections answering the questions every position implies:

  📐 The case            12-1 score, rank in universe, top-15 cutoff
                          buffer, strategy lineage
  🧮 Weight math         score-shifted normalization derivation that
                          produces the actual weight (not a black box)
  👁️ Recent disclosures   reactor signals from last 30d + rule-action
                          implication ("would trim" / "no trim" / why)
  🚪 What drops this     score threshold, risk gates, reactor rule,
                          earnings rule — explicit list of exit
                          conditions

Renders ABOVE the HANK interpretive summary because this content is
deterministic + recomputable; HANK is narrative on top of grounded
structured data.

v3.72.0 — Backtest harness for the v3.69.0 ReactorSignalRule. Answers
"if I'd flipped REACTOR_RULE_STATUS=LIVE on day X, what would the
cumulative P&L impact have been?" via:
  - Replay of every historical rebalance × every earnings_signal in
    the journal
  - Counterfactual target weights for each trim-worthy event
  - yfinance forward-price pulls (T+5/10/20) to compute saved/lost
    dollars per trim
  - Parameter sweep across (min_materiality × trim_pct) grid

Honest behavior on sparse data: when no rebalance has 20 forward days
yet (current: May 1 rebalance, only 3 forward days exist), the
harness reports "fwd=n/a, impact=n/a" rather than silently returning 0.

Added "📊 Rule backtest" panel inside the 📞 Earnings reactor view.

v3.71.0 — Parallel reactor + 10-Q/10-K archiving. User feedback:
"things should be as parallelized as possible. for example, getting
all 8ks, 10ks, should be automatic."

  - react_for_positions now runs symbols concurrently via
    ThreadPoolExecutor (EDGAR_PARALLEL_WORKERS=5). Bounded under
    SEC's 10 req/sec rate limit.
  - Claude calls separately bounded via threading.BoundedSemaphore
    (CLAUDE_PARALLEL_WORKERS=3) so concurrent reactor iters don't
    trip Anthropic per-key rate limits.
  - react_for_symbol now fetches 8-K + 10-Q + 10-K (was 8-K only).
    Claude analysis fires only on material 8-Ks; 10-Q/10-K are
    archive-only — diff-vs-prior-quarter analysis is a future v3.7x.
  - Per-symbol exception isolation: one bad symbol returns [], doesn't
    poison others' results.

Verified live on first reload: archived 8 new 10-Qs alongside the 10
existing 8-Ks. Iter time: 7.4s for 15 symbols (vs ~12s sequential =
1.7× speedup at 5 workers — limited by per-symbol fetch+download
serialization within each worker; tighter speedup possible with
intra-symbol parallelism, deferred).

v3.70.0 — Per-symbol poll cadence (HOT around earnings, WARM otherwise).
User insight: 8-K earnings releases are pre-announced. We can poll
faster on earnings days (catch the print within seconds) without
paying for it on the other 60+ days/year per name.

  - HOT (60s cadence): symbol is within ±2 days of next earnings
  - WARM (300s cadence): every other day. Still catches unscheduled
    8-Ks (debt raises, officer changes, M&A) — ~50% of v3.68.x
    material flags came from these. Earnings-only would miss them.
  - Schedule rebuilt at every UTC-midnight roll inside the daemon
  - 📞 Earnings reactor view shows the per-symbol schedule + which
    symbols are HOT today

Empirical first-build: AMD is HOT today (earnings within window),
14 others WARM. AMD now polls every 60s; others every 300s.

v3.69.2 — Email alert format + test isolation. Two pieces:

  - Body now includes EDGAR URL link, current position weight, and
    the ReactorSignalRule action hint (e.g. "WOULD trim to 50%
    (status SHADOW)" or "NO trim (M3 below threshold M4)"). The
    "do I need to do anything?" question is answered inline.
  - Subject line surfaces the trim tag when the rule will act:
    "[trader] INTC M4 BEARISH → would trim — Intel raised $6.5B…"
  - tests/conftest.py auto-stubs SMTP/Slack creds at session +
    test scope so a misconfigured test can never leak real
    notifications again (the v3.69.1 incident).

v3.69.1 — Slack alerts to **prismtrading** workspace. Reactor's
material-signal alerts (M≥3) now push to BOTH email AND Slack via
Incoming Webhook (set SLACK_WEBHOOK in .env). Channels are independent
— email failure doesn't block Slack and vice versa. Either delivering
counts as success for the notified_at idempotency gate.

Setup: see docs/AUTOMATION.md → Slack alerts section.

v3.69.0 — ReactorSignalRule wires the v3.68.x earnings reactor into
the rebalance gate. When a held name has a recent (≤14d) M≥4 BEARISH
signal, the rule trims that position's target weight to 50% of
original at the next monthly rebalance.

Default status: SHADOW (logs would-be trims without executing). User
flips to LIVE via REACTOR_RULE_STATUS=LIVE env when comfortable.
Direction-gated (BULLISH never auto-boosts); materiality-gated
(M≥4 = "warrants position adjustment", M3 too low for auto-cut);
recency-gated (only signals from last 14d).

This crosses the analysis→decision boundary deliberately. The trim is
bounded (50% reduction max) and the rule status is single-flag-revertible
so the user keeps a stop-button.

  - new trader/reactor_rule.py with ReactorSignalRule class
  - wired into main.py after EarningsRule, before validate_targets
  - 📞 Earnings reactor view shows rule status + would-trim list

v3.68.4 — Robustness pass on the v3.68.x earnings stack:
  - **Bug fix:** ProcessType=Background in the launchd plist let
    macOS App Nap throttle the daemon's sleep timers — observed
    12-min iter intervals on a configured 5-min cadence. Switched
    to ProcessType=Adaptive + LowPriorityIO=false. Empirically
    verified: iter cadence now hits the configured 5 min (318s
    actual vs 312s expected).
  - **API fix:** earnings_reactor.recent_signals() bound its
    journal_db default at function-definition time — monkeypatching
    the module attribute didn't work. Now reads DEFAULT_JOURNAL_DB
    at call time. Surfaced by a HANK-tool dispatch test.
  - **Coverage:** 19 new tests (v3.68.4) — HANK tool round-trips
    (read_filings + get_earnings_signals), reactor edge cases
    (malformed Claude JSON, embedded JSON in prose, unknown CIK,
    HTML edge cases), plist regression guards (ProcessType +
    LowPriorityIO), daemon SIGTERM clean-shutdown via subprocess,
    pre-v3.68.2 schema migration safety.

v3.68.3 — Earnings reactor in daemon mode. v3.68.1 was a launchd job
that respawned every 4h ("constantly looking" was a UI illusion —
actual cadence was 6 fires/day). v3.68.3 makes the reactor a
persistent process polling every 5 min via the new --watch CLI mode.

  - new --watch + --watch-interval flags in scripts/earnings_reactor.py
  - clean SIGTERM handling (no aborted Claude calls on launchd reload)
  - per-iter try/except so transient EDGAR / Claude errors don't kill
    the daemon
  - launchd plist switched to KeepAlive=true + ThrottleInterval=60s
    (auto-respawn on crash, no tight loops on bug)
  - line-buffered stdout so `tail -f` shows progress in real time

Latency: 4h → 5 min from 8-K filing → email. Cost unchanged
(idempotency at accession level means most polls cost $0).

v3.68.2 — Email alerts for material reactor signals. When the reactor
flags a M≥3 (worth-a-PM's-attention) signal it pushes via the existing
trader.notify pipeline (SMTP). Idempotent via a notified_at column on
earnings_signals so re-runs of the launchd job don't spam.

Threshold configurable via REACTOR_ALERT_MIN_MATERIALITY env (default
3). Anti-stub guarded — the email body always exceeds 80 chars of real
content (summary + bull/bear quotes + accession + dashboard reference).

Backfilled the INTC M3 ($6.5B debt raise) signal that v3.68.1's first
auto-fire produced — that email landed in the inbox before this commit.

  - new alert helpers in trader/earnings_reactor.py
  - new --no-alerts and --backfill-alerts CLI flags

v3.68.1 — Auto-fire the earnings reactor via launchd. Mac launchd job
at infra/launchd/com.trader.earnings-reactor.plist fires weekdays 17:05
ET (post-close), every 4h via StartInterval (sleep-resilient), and on
laptop wake. Idempotent: over-firing costs zero Claude tokens because
the reactor's UNIQUE constraint on (symbol, accession) makes re-runs
free when no new 8-Ks have landed.

  - infra/launchd/com.trader.earnings-reactor.plist (version-controlled
    in the repo so plist edits land via git)
  - scripts/install_launchd_earnings.sh (idempotent; --uninstall flag)
  - docs/AUTOMATION.md describing the 3-layer automation model

Verified end-to-end on first install: 13 8-Ks archived for our LIVE
positions, 1 material flag (INTC $6.5B debt raise → BEARISH).

v3.68.0 — Earnings reactor + persistent filings archive. Mirrors the
Sand Grove / FT pattern (LLMQuant 2026-05-04 article): AI compresses
100-page-doc → structured-thesis time, decision layer stays human.

  - New trader/filings_archive.py — on-disk archive of SEC filings +
    transcripts indexed in SQLite. data/filings/{symbol}/{form}/
    {accession}.txt + sidecar JSON. Persistent across container
    restarts.
  - New trader/sec_filings.py — free SEC EDGAR fetcher (no API key).
    Pulls 8-K / 10-Q / 10-K via data.sec.gov.
  - New trader/earnings_reactor.py — orchestrator. For each LIVE
    position, fetches new 8-Ks, archives them, and runs Claude for a
    structured signal (direction, materiality 1-5, guidance change,
    surprise direction, summary, bullish/bearish quotes). Persists to
    journal.earnings_signals. Logs every Claude call via llm_audit.
  - scripts/earnings_reactor.py — CLI (--skip-claude for archive-only)
  - 📞 Earnings reactor + 📂 Filings archive views in Discovery group
  - HANK gains read_filings + get_earnings_signals tools
  - prewarm.py auto-archives 8-Ks daily (no Claude — that's manual)

v3.67.2 — Hotfix for the v3.66.0 single-source-of-truth refactor.
Caught one consumer site I missed: `_headline_metrics()` (the 6-up
metric grid below the price headline) was still reading directly from
`_cached_snapshots()`, returning the journal's daily_snapshot value
($106K from Friday). Meanwhile the big-block headline above it (also
on Overview) was reading from EquityState ($104K live). Same page,
two equity numbers, both labeled "Equity." Now `_headline_metrics`
consumes `_get_equity_state()` like every other v3.66.0+ consumer.

v3.67.1 — Nav glossary + ambiguous-label fix. Six pages had names that
sounded interchangeable ("Shadow signals" / "Shadow variants" / "V5
sleeves" / "Sleeve health" / "Validation" / "Stress test"). Each is
now renamed with a one-word qualifier and grouped into a sub-section
that telegraphs its purpose. See docs/GLOSSARY.md for the full
disambiguation:
  - 🔬 Research = sleeve construction + validation
  - 👁️ Shadow track = real-time, not-yet-enforced
  - 🩺 Diagnostics = observation, slippage, postmortems

v3.67.0 — File split. dashboard.py grew past 5,600 lines; pure helpers
now live in:
  - trader/dashboard_ui.py   (rendering helpers — ribbon, headline,
                               FAB, chips, day-P&L card, citations)
  - trader/dashboard_data.py (data layer — query, read_state_file,
                               live_portfolio, cached_snapshots)

dashboard.py keeps view functions, sidebar, and dispatch — those are
deeply entangled with their own logic and don't benefit from extraction.
The new modules are independently importable + unit-testable.

v3.66.0 — Single-source-of-truth refactor. Resolves the v3.65.x bug
class where journal_snapshot + briefing_cache + live_broker +
_cached_snapshots all returned different "equity" values, leading to
"why does my account show $107K here and $106K there?" confusion.

  - New trader/equity_state.py — get_equity_state() returns one
    EquityState dataclass with equity_now, today_pl_*, last_session_pl_*,
    source, source_age_seconds, session. Every view consumes it.
  - DRY: new _render_day_pl_card(state) helper replaces the duplicated
    session-aware label branch (was in 2 views; will grow to N if not
    consolidated).
  - Color audit per UI_BENCHMARK pattern #7: reserve green/red for P&L
    direction only. FAB gradient → flat blue. Headline-block colors
    softened. Status chips use neutral gray with a colored border.
  - _market_session() now loud-fails (st.warning) instead of silently
    pretending the market is open when the underlying module errors.

v3.65.1 — Market-session awareness. Alpaca's `account.equity` keeps
ticking on extended-hours / weekend marks while `account.last_equity`
doesn't roll over until the next session opens. The result: a phantom
"+0.6% day P&L" labeled as TODAY when checked on Saturday/Sunday/before
Monday open. We now detect the session (OPEN / CLOSED_WEEKEND /
CLOSED_HOLIDAY / CLOSED_PREMARKET / CLOSED_AFTERHOURS) via
`trader.market_session` and either suppress the day-P&L delta or
relabel it as "Last session ({date})" so the user knows what they're
looking at. Adds a CLOSED · last close badge to the sticky ribbon.

v3.65.0 — UI BENCHMARK pass (per docs/UI_BENCHMARK.md):
  - Sticky market ribbon at the top of every view (SPY/QQQ/VIX/regime)
  - Bigger price headline on Overview (Nasdaq/CNBC big-block treatment)
  - Floating "Ask HANK" pill bottom-right on every non-chat view
  - Performance view: industry-standard timeframe chips
    (1D 5D 1M 3M 6M YTD 1Y 5Y) replacing the "Lookback" selectbox
  - Sidebar version label bumped

v3.55.0 — LEFT SIDEBAR NAV refactor (operator-style):
  Sidebar (left): vertical nav with sections:
    - Primary action: 🤖 Chat (default selected)
    - VIEWS: Overview, Live positions, Decisions, Lots, Performance,
             Attribution, Events, Regime, Intraday risk
    - RESEARCH: Shadow variants, Sleeve health, Postmortems, Reports
    - SYSTEM: Manual triggers, Settings
  Main area: renders the selected view (one at a time, no horizontal tabs).

This replaces v3.54.x's 'top metrics + chat above 14 tabs' layout. The user
wanted operator-style left nav with chat as the primary surface and
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

# v3.57.1 (Phase 4): hotkey vocabulary — pro-trader feel.
# Cmd+K opens command bar (focuses the cmd_bar selectbox).
# Alt+H opens an alert with the hotkey reference.
# (True per-key tab jumps would need a Streamlit components.v1 round-trip,
# which can re-trigger reruns mid-stream and break chat. Keep it simple.)
st.markdown("""
<script>
window.addEventListener('keydown', function(e) {
  // Cmd+K / Ctrl+K → focus the command bar selectbox
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault();
    const cb = window.parent.document.querySelector('div[data-testid="stSelectbox"] input');
    if (cb) cb.focus();
  }
  // Alt+H → hotkey help
  if (e.altKey && e.key === 'h') {
    e.preventDefault();
    alert('⌨️ Hotkeys:\\n  Cmd/Ctrl+K   command bar\\n  Alt+H        this help\\n  Esc          close menus\\n\\nNav: click sidebar items.\\nWorkflows: pick from command bar.');
  }
});
</script>
""", unsafe_allow_html=True)

# ============================================================
# v3.55.1: Sleek dark aesthetic via CSS injection
# ============================================================
st.markdown("""
<style>
  /* Hide Streamlit chrome */
  /* v3.56.3: previous v3.55.x/v3.56.2 attempts to selectively show the
     sidebar toggle were unreliable — Streamlit's collapsed-sidebar
     control rendered in different positions across DOM updates and
     was sometimes invisible against the dark background. Final fix:
     hide the collapse button ENTIRELY so the user can't collapse the
     sidebar and get stuck. The sidebar is always visible, period. */
  #MainMenu, footer { visibility: hidden !important; height: 0 !important; }
  header[data-testid="stHeader"] {
    background: transparent !important;
  }
  /* Hide deploy + toolbar */
  [data-testid="stToolbarActions"],
  button[kind="deploy"],
  [data-testid="stStatusWidget"] {
    display: none !important;
  }
  /* Hide the sidebar collapse button — sidebar always open */
  [data-testid="stSidebarCollapseButton"],
  [data-testid="stSidebarCollapsedControl"],
  [data-testid="collapsedControl"],
  button[aria-label*="collapse" i],
  button[aria-label*="Close sidebar" i],
  button[aria-label*="hide" i] {
    display: none !important;
  }
  /* Force the sidebar visible regardless of session-state collapse */
  section[data-testid="stSidebar"] {
    transform: translateX(0) !important;
    visibility: visible !important;
    min-width: 280px !important;
    width: 280px !important;
    margin-left: 0 !important;
  }
  section[data-testid="stSidebar"][aria-expanded="false"] {
    transform: translateX(0) !important;
    margin-left: 0 !important;
    width: 280px !important;
  }
  /* Adjust main content padding so it doesn't go under a phantom collapsed sidebar */
  .main, [data-testid="stAppViewContainer"] > section.main {
    margin-left: 0 !important;
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
# v3.57.2 (Phase 6): cross-panel symbol link. Views that show per-symbol
# detail read this and render a "🔗 Set focus" button to write to it.
# Pattern: IBKR instrument-link / NinjaTrader grouping blocks. Streamlit's
# tab system makes color-coding hard, but a single shared "selected symbol"
# is a useful 80% solution: pick AAPL in Live positions → Decisions, Lots,
# Events all jump to the AAPL row.
if "linked_symbol" not in st.session_state:
    st.session_state.linked_symbol = ""


# ============================================================
# v3.73.1 — Build-info badge / drift detector
#
# Reads /app/BUILD_INFO.txt (or ./BUILD_INFO.txt for dev runs) which
# is baked at image build time with the git commit + UTC timestamp.
# Compares to the file mtime of dashboard.py — if the file is newer
# than the build timestamp, the container is running stale code
# and the badge fires a yellow warning.
#
# The drift warning catches the failure mode that produced the
# 39-hour Friday-equity-bug episode on 2026-05-05: dashboard.py was
# edited 18 times on the host, container kept serving the
# 2026-05-03 frozen copy, no signal anywhere told the user the
# container was stale.
# ============================================================
def _read_build_info() -> dict:
    """Parse BUILD_INFO.txt baked into the image. Returns
    {commit: str, built_at: str} with empty strings on failure."""
    candidates = [
        Path("/app/BUILD_INFO.txt"),                     # in-container path
        ROOT / "BUILD_INFO.txt",                          # dev / out-of-container
    ]
    info = {"commit": "", "built_at": ""}
    for p in candidates:
        if not p.exists():
            continue
        try:
            for line in p.read_text().splitlines():
                if "=" in line:
                    k, _, v = line.partition("=")
                    info[k.strip()] = v.strip()
            return info
        except Exception:
            continue
    return info


def _build_info_drift_seconds() -> Optional[float]:
    """How many seconds AHEAD of the build timestamp is the latest
    edit to dashboard.py? Positive means the host code has moved past
    the image — drift. Returns None if BUILD_INFO is unset (e.g. dev
    `streamlit run`) so we don't false-alarm in non-Docker contexts."""
    info = _read_build_info()
    built_at_raw = info.get("built_at", "")
    if not built_at_raw:
        return None
    try:
        # Accept either ISO-8601 with or without 'Z' suffix
        built_at_str = built_at_raw.rstrip("Z")
        built_at = datetime.fromisoformat(built_at_str)
    except ValueError:
        return None
    try:
        dashboard_mtime = datetime.utcfromtimestamp(
            (ROOT / "scripts" / "dashboard.py").stat().st_mtime)
    except OSError:
        return None
    return (dashboard_mtime - built_at).total_seconds()


def _render_build_info_badge() -> None:
    """Sidebar badge: build commit + timestamp + drift warning.

    Three states:
      - No BUILD_INFO present (dev mode): caption "(local dev)"
      - BUILD_INFO present + no drift: caption "built {ts} · {commit}"
      - BUILD_INFO present + drift > 60s: yellow warning "container
        stale, rebuild" with the exact drift number for forensics
    """
    info = _read_build_info()
    commit = info.get("commit", "")
    built_at = info.get("built_at", "")
    if not commit and not built_at:
        st.caption("_(local dev — no BUILD_INFO)_")
        return

    short = (commit[:7] if commit else "(unknown)")
    drift = _build_info_drift_seconds()
    if drift is None or drift <= 60:
        # Healthy — code matches container
        ts_short = built_at.split("T")[0] if "T" in built_at else built_at
        st.caption(f"_built {ts_short} · {short}_")
        return

    # Drift exceeds 60s — host code has moved past the image
    if drift < 3600:
        age_str = f"{int(drift / 60)}m"
    elif drift < 86400:
        age_str = f"{int(drift / 3600)}h"
    else:
        age_str = f"{int(drift / 86400)}d"
    st.warning(
        f"⚠️ Container stale — host code moved {age_str} ahead of image "
        f"(built {built_at[:16]}, commit `{short}`). "
        f"Run `docker compose build dashboard && "
        f"docker compose up -d --force-recreate dashboard` to refresh."
    )


from typing import Optional  # noqa: E402  — used by _build_info_drift_seconds


# ============================================================
# Sidebar — left nav (operator-style)
# ============================================================
with st.sidebar:
    st.markdown("### 📊 trader")
    st.caption("v3.73.23 · chat-first AI dashboard")
    # v3.73.1: build-info badge — surfaces the commit + build timestamp
    # baked into the running image, plus a drift warning when host
    # code has moved past what's in the container. Catches the
    # "container running stale code" failure mode that produced the
    # 39-hour Friday-equity-bug episode on 2026-05-05.
    _render_build_info_badge()
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

    # v3.56.0: chat threads list (newest first, max 50 visible)
    # v3.56.2: cached at 30s so the sidebar doesn't disk-scan on every rerun
    # v3.56.5: ALWAYS show the section (even when empty) — Claude-style.
    #          Empty state explains how to start the first chat.
    @st.cache_data(ttl=30, show_spinner=False)
    def _cached_thread_list():
        try:
            from trader.copilot_storage import list_threads
            return [(t.id, t.title, t.created_at, t.updated_at)
                    for t in list_threads(limit=50)]
        except Exception:
            return []
    try:
        threads_data = _cached_thread_list()
        from collections import namedtuple
        _T = namedtuple("_T", ["id", "title", "created_at", "updated_at"])
        threads = [_T(*x) for x in threads_data]
        # v3.56.6: collapsible chat-history section. Default-expanded only
        # if there are <= 8 chats; collapsed by default if more, since 50
        # chats would blow up the sidebar height. Active chat always
        # shown above the expander so the user knows where they are.
        active_id = st.session_state.get("current_thread_id")
        active_thread = next((t for t in threads if t.id == active_id), None)
        if active_thread:
            st.caption("💬 ACTIVE")
            disp = active_thread.title if len(active_thread.title) <= 28 else active_thread.title[:26] + "…"
            # v3.56.8: ACTIVE button is now clickable — jumps to chat view
            # AND reloads the full thread from disk. Previously it was
            # disabled (just a label) which was confusing UX.
            if st.button(disp, key=f"active_thread_{active_thread.id}",
                         use_container_width=True, type="primary",
                         help=f"{active_thread.title}  ·  click to open in chat"):
                try:
                    from trader.copilot_storage import load_thread
                    full = load_thread(active_thread.id)
                    if full:
                        st.session_state.copilot_messages = list(full.messages)
                except Exception:
                    pass
                st.session_state.active_view = "chat"
                st.rerun()
        # Default expansion: open if few chats, closed if many
        default_open = len(threads) <= 8 and not active_thread
        with st.expander(f"💬 RECENTS ({len(threads)})",
                         expanded=default_open):
            if not threads:
                st.caption("_no chats yet — type below to start_")
            # Show non-active threads inside the expander
            visible = [t for t in threads if t.id != active_id]
            for t in visible:
                btype = "secondary"
                # Format relative time for hover tooltip
                try:
                    from datetime import datetime
                    ts = datetime.fromisoformat(t.updated_at)
                    age_sec = (datetime.utcnow() - ts).total_seconds()
                    if age_sec < 60:
                        age = f"{int(age_sec)}s"
                    elif age_sec < 3600:
                        age = f"{int(age_sec // 60)}m"
                    elif age_sec < 86400:
                        age = f"{int(age_sec // 3600)}h"
                    else:
                        age = f"{int(age_sec // 86400)}d"
                except Exception:
                    age = ""
                disp_title = t.title if len(t.title) <= 26 else t.title[:24] + "…"
                if st.button(disp_title, key=f"thread_{t.id}",
                             use_container_width=True, type=btype,
                             help=f"{t.title}  ·  {age} ago"):
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
                    # v3.56.8 FIX: load the FULL thread from disk because
                    # the cached namedtuple `t` only has id/title/timestamps
                    # (no messages — to keep the sidebar list cache cheap).
                    # Previously this set copilot_messages=list(t.messages)
                    # which raised AttributeError silently, leaving the
                    # conversation empty when the user clicked a thread.
                    try:
                        from trader.copilot_storage import load_thread
                        full = load_thread(t.id)
                        loaded_messages = list(full.messages) if full else []
                    except Exception:
                        loaded_messages = []
                    st.session_state.current_thread_id = t.id
                    st.session_state.current_thread_title = t.title
                    st.session_state.current_thread_created_at = t.created_at
                    st.session_state.copilot_messages = loaded_messages
                    st.session_state.active_view = "chat"
                    st.rerun()
    except Exception:
        pass

    # v3.56.7: removed standalone "🤖 Chat" nav item — redundant with the
    # "💬 New chat" CTA above and the RECENTS list which both route to the
    # chat view. User: 'i think you don't need chat, recent is good enough'.
    # v3.62.0: nav reorg. Task-oriented top groups, each collapsible.
    # Reduces 27+ flat items to 5 always-visible top-level entries +
    # collapsible sub-sections that hide ~20 deeper tools by default.
    # The user-task framing: "what am I doing?" → which group.
    # v4.0.0 freeze: viewer-only navigation. Research/diagnostics/
    # shadow-track groups are gone with their underlying apparatus.
    NAV_GROUPS = [
        ("__top__", None, [
            ("🏠 Overview", "overview"),
            ("📈 Performance", "performance"),
            ("🔔 Alerts", "alerts"),
        ]),
        ("📊 Portfolio", None, [
            ("💼 Live positions", "live_positions"),
            ("🎯 Decisions", "decisions"),
            ("📦 Position lots", "lots"),
            ("🌳 TLH", "tlh"),
            ("📊 Attribution", "attribution"),
        ]),
        ("📰 Discovery", None, [
            ("📰 News", "news"),
            ("📅 Events", "events"),
            ("👁️ Watchlist", "watchlist"),
        ]),
        ("🩺 Diagnostics", None, [
            ("⚡ Intraday risk", "intraday"),
            ("⚡ Slippage", "slippage"),
            ("🩺 Sleeve health (correlation)", "sleeve_health"),
        ]),
        ("⚙️ System", None, [
            ("🔧 Manual triggers", "manual"),
            ("🛑 Manual override", "manual_override"),
            ("⚙️ Settings", "settings"),
        ]),
    ]

    def _render_nav_button(label: str, key: str):
        is_active = st.session_state.active_view == key
        btype = "primary" if is_active else "secondary"
        if st.button(label, key=f"nav_{key}",
                     use_container_width=True,
                     type=btype):
            st.session_state.active_view = key
            st.rerun()

    # Render groups. Top tier = bare buttons. Collapsibles = expanders.
    for group_label, _placeholder, items in NAV_GROUPS:
        if group_label == "__top__":
            for label, key in items:
                _render_nav_button(label, key)
            st.divider()
        else:
            # Auto-expand if the user is currently inside one of this
            # group's tabs, so they don't lose orientation
            keys_in_group = {key for _, key in items}
            in_group = st.session_state.active_view in keys_in_group
            with st.expander(group_label, expanded=in_group):
                for label, key in items:
                    _render_nav_button(label, key)

    st.divider()

    # v3.57.2 (Phase 6): cross-panel symbol link. Views read
    # st.session_state.linked_symbol; setting it here makes every view
    # show that symbol's detail by default.
    cur_link = st.session_state.get("linked_symbol", "")
    new_link = st.text_input(
        "🔗 Linked symbol",
        value=cur_link,
        placeholder="AAPL",
        help="Set a focus symbol — Decisions, Lots, Events views jump to it."
    ).upper().strip()
    if new_link != cur_link:
        st.session_state.linked_symbol = new_link
    if new_link:
        if st.button(f"✖ Clear {new_link}", key="clear_link",
                     use_container_width=True):
            st.session_state.linked_symbol = ""
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


# v3.67.0: data helpers extracted to trader.dashboard_data so they can
# be unit-tested without instantiating Streamlit. Thin re-exports below
# preserve the call-site names the views were written against.
from trader import dashboard_data as _data  # noqa: E402

query = _data.query
read_state_file = _data.read_state_file
_live_portfolio = _data.live_portfolio


# v3.56.4: disk-backed briefing cache so cold-start (container restart,
# fresh browser load, after-hours wake-up) doesn't have to wait for
# HMM + macro + GARCH. We write the briefing to data/briefing_cache.json
# with a timestamp; if the file is < 300s old, return it instantly.
# Otherwise recompute + persist.
_BRIEFING_CACHE_FILE = ROOT / "data" / "briefing_cache.json"
# v3.56.9: bumped from 300s (5min) to 3600s (1h). The briefing is a
# 'today at a glance' view; market state doesn't change meaningfully on
# 5-min granularity for a monthly-rebalance strategy. 1h cache means
# the user only eats the cold-compute once per hour at most, NOT every
# 5 min throughout the day. Disk-backed so container restarts don't
# wipe it.
_BRIEFING_TTL_SEC = 3600


def _read_disk_briefing():
    """Try to load briefing from disk. Returns dict or None."""
    if not _BRIEFING_CACHE_FILE.exists():
        return None
    try:
        data = json.loads(_BRIEFING_CACHE_FILE.read_text())
        ts = datetime.fromisoformat(data.get("_cached_at", "1970-01-01"))
        age = (datetime.utcnow() - ts).total_seconds()
        if age > _BRIEFING_TTL_SEC:
            return None
        # Reconstruct the MorningBriefing dataclass
        from trader.copilot_briefing import MorningBriefing
        b = data.get("briefing", {})
        return MorningBriefing(
            timestamp=b.get("timestamp", ""),
            headline=b.get("headline", ""),
            equity_now=b.get("equity_now"),
            day_pl_pct=b.get("day_pl_pct"),
            spy_today_pct=b.get("spy_today_pct"),
            excess_today_pct=b.get("excess_today_pct"),
            regime=b.get("regime", ""),
            regime_overlay_mult=b.get("regime_overlay_mult"),
            regime_enabled=b.get("regime_enabled", False),
            freeze_active=b.get("freeze_active", False),
            freeze_reason=b.get("freeze_reason", ""),
            upcoming_events_next7d=b.get("upcoming_events_next7d", []),
            yesterday_pm_summary=b.get("yesterday_pm_summary", ""),
            notable_facts=b.get("notable_facts", []),
            raw_data=b.get("raw_data", {}),
        )
    except Exception:
        return None


def _write_disk_briefing(brief):
    """Persist briefing to disk with timestamp."""
    try:
        _BRIEFING_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        b_dict = {
            "timestamp": brief.timestamp,
            "headline": brief.headline,
            "equity_now": brief.equity_now,
            "day_pl_pct": brief.day_pl_pct,
            "spy_today_pct": brief.spy_today_pct,
            "excess_today_pct": brief.excess_today_pct,
            "regime": brief.regime,
            "regime_overlay_mult": brief.regime_overlay_mult,
            "regime_enabled": brief.regime_enabled,
            "freeze_active": brief.freeze_active,
            "freeze_reason": brief.freeze_reason,
            "upcoming_events_next7d": brief.upcoming_events_next7d,
            "yesterday_pm_summary": brief.yesterday_pm_summary,
            "notable_facts": brief.notable_facts,
            "raw_data": brief.raw_data,
        }
        _BRIEFING_CACHE_FILE.write_text(json.dumps({
            "_cached_at": datetime.utcnow().isoformat(),
            "briefing": b_dict,
        }, indent=2, default=str))
    except Exception:
        pass


@st.cache_data(ttl=3600, show_spinner="📰 Computing today's briefing (HMM + macro + GARCH)...")
def _morning_briefing():
    """Get the morning briefing. Tries disk cache first (instant), else
    recomputes via compute_briefing() (~3s on cold start, was 7s before
    we removed per-symbol earnings calendar from the briefing path) and
    persists to disk for the next session.
    """
    disk_cached = _read_disk_briefing()
    if disk_cached is not None:
        return disk_cached
    try:
        from trader.copilot_briefing import compute_briefing
        brief = compute_briefing()
        if brief is not None:
            _write_disk_briefing(brief)
        return brief
    except Exception:
        return None


_OVERLAY_CACHE_FILE = ROOT / "data" / "overlay_cache.json"


def _read_disk_overlay():
    """Disk-cached overlay signal, attribute-accessible.

    v3.73.1: was returning a local-class instance (`class O: pass`)
    which pickle.dumps cannot serialize, breaking @st.cache_data
    downstream. Pre-v3.66.0 the cache was pre-warmed from the
    dataclass path so the bug was latent. The new container has
    empty Streamlit cache → first call hits the disk path → AttrError
    on pickle. Use types.SimpleNamespace, which IS picklable and
    supports the same attribute access pattern."""
    if not _OVERLAY_CACHE_FILE.exists():
        return None
    try:
        from types import SimpleNamespace
        data = json.loads(_OVERLAY_CACHE_FILE.read_text())
        ts = datetime.fromisoformat(data.get("_cached_at", "1970-01-01"))
        if (datetime.utcnow() - ts).total_seconds() > 300:
            return None
        return SimpleNamespace(**data.get("overlay", {}))
    except Exception:
        return None


def _write_disk_overlay(sig):
    try:
        _OVERLAY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _OVERLAY_CACHE_FILE.write_text(json.dumps({
            "_cached_at": datetime.utcnow().isoformat(),
            "overlay": {
                "enabled": sig.enabled,
                "final_mult": sig.final_mult,
                "rationale": sig.rationale,
                "hmm_mult": sig.hmm_mult,
                "hmm_regime": sig.hmm_regime,
                "hmm_posterior": sig.hmm_posterior,
                "hmm_error": sig.hmm_error,
                "macro_mult": sig.macro_mult,
                "macro_curve_inverted": sig.macro_curve_inverted,
                "macro_credit_widening": sig.macro_credit_widening,
                "macro_error": sig.macro_error,
                "garch_mult": sig.garch_mult,
                "garch_vol_forecast_annual": sig.garch_vol_forecast_annual,
                "garch_error": sig.garch_error,
            },
        }, indent=2, default=str))
    except Exception:
        pass


@st.cache_data(ttl=300, show_spinner="⚙️ Computing regime overlay...")
def _overlay_signal():
    disk = _read_disk_overlay()
    if disk is not None:
        return disk
    try:
        from trader.regime_overlay import compute_overlay
        sig = compute_overlay()
        if sig is not None:
            _write_disk_overlay(sig)
        return sig
    except Exception:
        return None


_cached_snapshots = _data.cached_snapshots


def _headline_metrics():
    """Render the headline metrics row used at top of Overview + Chat views.

    v3.67.2: was reading "Equity" + "Cash" directly from the journal's
    daily_snapshot table, which on Mondays/Tuesdays returns Friday's
    pre-rebalance number — disagreeing with the big-block price
    headline above (which uses the canonical live broker mark). Now
    consumes _get_equity_state() like every other v3.66.0+ consumer.

    The "Window return" + "vs anchor" cards still derive from the
    journal because those are explicitly multi-day metrics; using the
    canonical equity_now as the latest endpoint keeps them coherent
    with the big-block headline."""
    state = _get_equity_state()
    snaps = _cached_snapshots(str(DB_PATH))
    cols = st.columns(6)

    if state.equity_now is not None:
        eq = state.equity_now
        cols[0].metric("Equity", f"${eq:,.0f}",
                       help=f"src: {state.source} · "
                             f"{int(state.source_age_seconds)}s ago")
        if state.cash is not None:
            cols[1].metric("Cash", f"${state.cash:,.0f}",
                           f"{(state.cash/eq*100):.1f}% of book")
        else:
            cols[1].metric("Cash", "—",
                           help="Cash unavailable from this source")
    elif not snaps.empty:
        # Fallback: journal snapshot (offline mode)
        latest = snaps.iloc[0]
        eq = float(latest["equity"])
        cols[0].metric("Equity", f"${eq:,.0f}",
                       help="src: journal_snapshot (broker unreachable)")
        cols[1].metric("Cash", f"${float(latest['cash']):,.0f}",
                       f"{(float(latest['cash'])/eq*100):.1f}% of book")
    else:
        eq = None
        cols[0].metric("Equity", "n/a", "sync from GitHub")

    if eq is not None:
        anchor = read_state_file(str(ROOT / "data" / "deployment_anchor.json"))
        if anchor:
            anchor_eq = float(anchor.get("equity_at_deploy", 0))
            if anchor_eq > 0:
                dd = (eq - anchor_eq) / anchor_eq
                cols[2].metric("vs anchor", f"{dd:+.2%}",
                               f"${anchor_eq:,.0f} baseline")
        if not snaps.empty and len(snaps) >= 2:
            # Window return: today's canonical equity vs oldest snapshot
            first_eq = float(snaps.iloc[-1]["equity"])
            ret_window = (eq - first_eq) / first_eq if first_eq > 0 else 0
            cols[3].metric("Window return", f"{ret_window:+.2%}",
                           f"{len(snaps)} snaps")
        else:
            cols[3].metric("Window", "≥1 snap needed")

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
# UI helpers — v3.67.0 SPLIT
#
# Most rendering helpers (sticky ribbon, price headline, FAB,
# timeframe chips, day-P&L card, equity state, citation pills, tool
# artifacts) now live in trader/dashboard_ui.py so they can be
# unit-tested without instantiating Streamlit. The thin wrappers below
# preserve the underscore-prefixed names that views call so the view
# bodies didn't have to be rewritten in the same commit.
# ============================================================
from trader import dashboard_ui as _ui  # noqa: E402

_BRIEFING_CACHE_FILE_PATH = ROOT / "data" / "briefing_cache.json"

_market_session = _ui.market_session
_render_day_pl_card = _ui.render_day_pl_card
_render_floating_hank_fab = _ui.render_floating_hank_fab
_render_timeframe_chips = _ui.render_timeframe_chips
_tier_emoji = _ui.tier_emoji
_render_citation_pills = _ui.render_citation_pills
_render_tool_artifact = _ui.render_tool_artifact
_ribbon_market_snapshot = _ui.ribbon_market_snapshot
TIMEFRAME_CHIPS = _ui.TIMEFRAME_CHIPS


def _get_equity_state():
    """Pass dashboard's DB_PATH + briefing cache into the shared helper."""
    return _ui.get_equity_state(str(DB_PATH), str(_BRIEFING_CACHE_FILE_PATH))


def _equity_state_cached():
    """Backwards-compat alias used by tests. Real cache lives inside
    _ui.equity_state_cached."""
    return _ui.equity_state_cached(str(DB_PATH), str(_BRIEFING_CACHE_FILE_PATH))


def _render_market_ribbon():
    """Pass the disk-cached overlay signal into the shared ribbon
    helper (overlay reading stays in dashboard.py because it depends on
    a disk cache initialized at module load)."""
    _ui.render_market_ribbon(overlay=_overlay_signal())


def _render_price_headline():
    _ui.render_price_headline(_get_equity_state())


# ============================================================
# View: Chat (primary, default)
# ============================================================
def view_chat():
    st.title("🤖 HANK")
    st.caption("**H**onest **A**nalytical **N**umerical **K**opilot — your trading research assistant. "
               "Uses 10 tools autonomously to answer questions about portfolio, "
               "decisions, performance.")

    # v3.57.1 (Phase 4): command bar above chat — Cmd+K-style typeahead
    # backed by saved workflows + suggested prompts.
    try:
        from trader.copilot_memory import list_workflows as _list_workflows
        _wfs = _list_workflows()
    except Exception:
        _wfs = []
    # v3.62.0: placeholder option text replaces the previous empty
    # string — user couldn't see what the box was for.
    PLACEHOLDER = "⌘K  pick a workflow or suggested prompt..."
    cmd_options = [PLACEHOLDER] + [f"⚡ {w['name']}" for w in _wfs] + [
        "💡 Why am I up/down today?",
        "💡 What's coming up this week?",
        "💡 Show best/worst positions",
        "💡 What did the post-mortem flag?",
        "💡 Run pre-rebalance check",
    ]
    cmd_pick = st.selectbox(
        "Command bar",
        options=cmd_options,
        index=0,
        key="cmd_bar",
        label_visibility="collapsed",
    )
    # Treat placeholder as empty
    if cmd_pick == PLACEHOLDER:
        cmd_pick = ""
    # v3.59.0 fix: Streamlit forbids writing to a widget's key after it
    # instantiates. Track last_cmd_pick separately to detect the change
    # and only fire ONCE per new selection. Re-selecting the same option
    # won't re-fire — pick "" first, then re-pick.
    last_pick = st.session_state.get("_last_cmd_pick", "")
    if cmd_pick and cmd_pick != last_pick:
        st.session_state["_last_cmd_pick"] = cmd_pick
        if cmd_pick.startswith("⚡ "):
            wf_name = cmd_pick[2:]
            wf = next((w for w in _wfs if w["name"] == wf_name), None)
            if wf and wf.get("prompts"):
                st.session_state["_pending_user_input"] = "\n\n".join(wf["prompts"])
        elif cmd_pick.startswith("💡 "):
            st.session_state["_pending_user_input"] = cmd_pick[2:]
    elif not cmd_pick:
        # Reset the change-detector when user clears the selectbox
        st.session_state["_last_cmd_pick"] = ""

    # v3.57.1 (Phase 3): Plan Mode toggle. When ON, sim/live tools are stubbed
    # so the model describes the intended action without executing it.
    pm_col1, pm_col2 = st.columns([3, 1])
    with pm_col2:
        plan_mode = st.toggle("🧭 Plan mode", value=False,
                              help="Sim/live tools stubbed — model describes intent only.")
    with pm_col1:
        if plan_mode:
            st.caption("🧭 **Plan mode ON** — read-only tools run; sim/live tools "
                       "are stubbed.")
        else:
            st.caption("Ask anything about your portfolio. 10 tools, used autonomously.")
    st.session_state["plan_mode"] = plan_mode

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

    # v4.0.x viewer-honesty: surface conversation cost/budget so users
    # can see when they're approaching the 200K context window. Zero new
    # deps — char/4 heuristic, ~15% error vs cl100k_base, enough for a
    # budget-feel readout. No live keystroke counter — the React-style
    # "updates as you type" UX is structurally impossible in Streamlit;
    # the widget loop is server-side and only reruns on submit. Per-
    # conversation total is what's actually useful in this stack.
    if st.session_state.copilot_messages:
        try:
            _chat_chars = 0
            for _m in st.session_state.copilot_messages:
                _chat_chars += len(str(_m.get("display_text", "")))
                _chat_chars += len(str(_m.get("content", "")))
                for _tc in _m.get("tool_calls", []) or []:
                    _chat_chars += len(str(_tc.get("input", "")))
                    _chat_chars += len(str(_tc.get("result", "")))
            _est_tokens = max(1, _chat_chars // 4)
            _ctx_window = 200_000  # claude-sonnet-4-6 / claude-opus-4-7
            _pct = _est_tokens / _ctx_window * 100
            _flag = "🟢" if _pct < 50 else "🟡" if _pct < 80 else "🔴"
            st.caption(
                f"{_flag} Conversation: **~{_est_tokens:,} tokens** / "
                f"{_ctx_window:,} context window ({_pct:.1f}%) — "
                f"approximate (chars÷4). Anthropic billing uses exact tokens."
            )
        except Exception:
            pass

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
                        _render_citation_pills(msg["tool_calls"])
                        with st.expander(f"🔧 {len(msg['tool_calls'])} tool call(s)", expanded=False):
                            for i, tc in enumerate(msg["tool_calls"], start=1):
                                _render_tool_artifact(i, tc)

    # Input below the box
    typed_input = st.chat_input("Ask the copilot...")
    pending = st.session_state.pop("_pending_user_input", None)
    user_input = typed_input or pending
    if user_input:
        st.session_state.copilot_messages.append({
            "role": "user", "display_text": user_input, "content": user_input,
        })
        # v3.56.5: persist the thread IMMEDIATELY on first user message so
        # even if the assistant API errors, the chat appears in the recents
        # list and the user can resume from another session.
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
            try:
                _cached_thread_list.clear()
            except Exception:
                pass
        except Exception:
            pass
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
                    from trader.copilot import stream_response, tier_of
                    pm = bool(st.session_state.get("plan_mode", False))
                    for ev in stream_response(api_messages, plan_mode=pm):
                        if ev["type"] == "text_delta":
                            acc += ev["text"]
                            text_ph.markdown(acc + "▌")
                        elif ev["type"] == "tool_use_start":
                            tool_log.append({"name": ev["name"],
                                              "input": ev.get("input", {}),
                                              "result": None,
                                              "tier": tier_of(ev["name"])})
                            tool_ph.caption(f"🔧 calling `{ev['name']}`...")
                        elif ev["type"] == "plan_blocked":
                            tool_ph.caption(
                                f"🧭 plan mode blocked `{ev['name']}` ({ev['tier']})")
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
                                _render_citation_pills(tool_log)
                                with st.expander(f"🔧 {len(tool_log)} tool call(s)", expanded=False):
                                    for i, tc in enumerate(tool_log, start=1):
                                        _render_tool_artifact(i, tc)
                            break
                        elif ev["type"] == "error":
                            text_ph.error(f"Copilot error: {ev['error']}")
                            break
                except Exception as e:
                    text_ph.error(f"{type(e).__name__}: {e}")


# ============================================================
# View: Overview
# ============================================================
def _render_drawdown_protocol_panel() -> None:
    """v3.73.2: Show current 180d-peak DD + which tier we're in.

    Reads daily_snapshot for the rolling 180d window, computes peak,
    derives current DD, evaluates the tier (GREEN/YELLOW/RED/ESCALATION/
    CATASTROPHIC), surfaces the response action verbatim from
    docs/RISK_FRAMEWORK.md §6.

    The panel is the ADVISORY surface in v3.73.2 — it doesn't change
    target weights. ENFORCING mode (env var) wires the responses into
    check_account_risk; that's a separate behavior toggle.
    """
    try:
        from trader.risk_manager import (
            evaluate_drawdown_tier, drawdown_protocol_mode,
            DRAWDOWN_YELLOW_PCT, DRAWDOWN_RED_PCT,
            DRAWDOWN_ESCALATION_PCT, DRAWDOWN_CATASTROPHIC_PCT,
            DD_PEAK_LOOKBACK_DAYS,
        )
    except Exception as e:
        st.caption(f"_drawdown protocol panel unavailable: {e}_")
        return

    state = _get_equity_state()
    if state.equity_now is None:
        return  # nothing to evaluate

    snaps = _cached_snapshots(str(DB_PATH))
    if snaps.empty or "equity" not in snaps.columns:
        return  # no snapshot history

    # 180-day-peak DD (matching check_account_risk's existing logic)
    eqs = snaps["equity"].dropna()
    if eqs.empty:
        return
    peak = float(eqs.max())
    if peak <= 0:
        return
    dd_pct = (state.equity_now - peak) / peak  # negative for actual DD
    tier = evaluate_drawdown_tier(dd_pct)
    mode = drawdown_protocol_mode()

    # Build the tier-strip display: which tiers we've crossed
    tier_emoji = {
        "GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴",
        "ESCALATION": "🟠", "CATASTROPHIC": "⛔",
    }.get(tier.name, "⚪")

    # Show the panel — collapsed by default unless we're past GREEN
    title = (f"🛡️ Drawdown protocol · {tier_emoji} **{tier.label}** "
             f"({dd_pct*100:+.2f}%)  ·  mode: {mode}")
    with st.expander(title, expanded=(tier.name != "GREEN")):
        # Tier strip
        cc = st.columns(4)
        for i, (label, threshold) in enumerate([
            ("Yellow", DRAWDOWN_YELLOW_PCT),
            ("Red (existing kill)", DRAWDOWN_RED_PCT),
            ("Escalation", DRAWDOWN_ESCALATION_PCT),
            ("Catastrophic", DRAWDOWN_CATASTROPHIC_PCT),
        ]):
            crossed = abs(dd_pct) >= threshold
            indicator = "🔴" if crossed else "⚪"
            cc[i].metric(
                f"{indicator} {label}",
                f"-{threshold*100:.0f}%",
                f"{'CROSSED' if crossed else 'clear'}",
            )

        st.markdown(f"**Current tier:** {tier_emoji} {tier.label}")
        if tier.name != "GREEN":
            st.markdown(f"**Pre-committed response.** {tier.response}")
            if mode == "ADVISORY":
                st.caption(
                    "_ADVISORY mode — this panel surfaces the tier + "
                    "response but does NOT mutate target weights at "
                    "rebalance. Set `DRAWDOWN_PROTOCOL_MODE=ENFORCING` "
                    "in `.env` to actually apply the response actions._"
                )
            else:
                st.caption(
                    f"_ENFORCING mode — the daily orchestrator WILL "
                    f"apply this tier's response action at next "
                    f"rebalance: {tier.enforce_action}._"
                )
        else:
            st.caption(
                f"_180d-peak: ${peak:,.0f}. Current equity: "
                f"${state.equity_now:,.0f}. Buffer to YELLOW (-5%): "
                f"{(DRAWDOWN_YELLOW_PCT - abs(dd_pct))*100:.2f}pp._"
            )

        # v4.0.0 freeze: tier definitions live in src/trader/risk_manager.py.
        st.markdown(
            f"_Window: {DD_PEAK_LOOKBACK_DAYS}-day rolling peak._"
        )


def _render_benchmark_panel() -> None:
    """v3.73.6: NAV-vs-SPY headline panel. The goal of the system is
    to beat SP500; this panel is how we know if we are.

    Renders:
      - Big chart: portfolio NAV (normalized to 100 at start) vs SPY
        NAV on the same axis
      - 4-up KPI tile: active return YTD, information ratio, beta,
        alpha-annualized
      - Honest sample-size disclosure when we have <30 days of history
        (IR / alpha need ≥30-60 daily obs to be statistically meaningful)
      - Backfill button if no data found

    Reads from journal.daily_snapshot (date, equity, benchmark_spy_close).
    The backfill is one-time-on-demand; daily orchestrator extends.
    """
    st.subheader("🎯 vs SP500 (the actual scoreboard)")
    st.caption(
        "Portfolio NAV vs SPY, normalized to 100 at first snapshot. "
        "All metrics are RELATIVE to SPY because absolute return is "
        "not the same question as 'is the strategy working.'"
    )

    try:
        from trader.benchmark_track import (
            load_snapshots, compute_metrics, nav_series_for_chart,
            backfill_journal,
        )
    except Exception as e:
        st.caption(f"_panel unavailable: {e}_")
        return

    snaps = load_snapshots()
    if not snaps:
        st.warning("No NAV-vs-SPY data on disk.")
        if st.button("Backfill from Alpaca + yfinance now"):
            with st.spinner("Backfilling..."):
                n = backfill_journal(period="6M")
            st.success(f"Backfilled {n} snapshots. Reloading...")
            st.rerun()
        return

    metrics = compute_metrics(snaps)
    if metrics is None:
        st.info("Not enough snapshots to compute metrics yet "
                "(need ≥5 days; have %d)." % len(snaps))
        return

    # ---- Headline KPI tile ----
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        delta_color = "normal" if metrics.is_winning else "inverse"
        st.metric(
            "Active return",
            f"{metrics.active_return_pct:+.2f}pp",
            delta=f"{'beating' if metrics.is_winning else 'trailing'} SPY",
            delta_color=delta_color,
            help=(
                f"Portfolio: {metrics.portfolio_return_pct:+.2f}% · "
                f"SPY: {metrics.benchmark_return_pct:+.2f}%"
            ),
        )
    with c2:
        st.metric(
            "Information ratio",
            f"{metrics.information_ratio:+.2f}",
            help=(
                "Annualized active return / tracking error. "
                ">1 is institutional-good; >0 is positive alpha. "
                f"TE: {metrics.tracking_error_annualized:.2f}% ann."
            ),
        )
    with c3:
        beta_warn = "high" if metrics.beta > 1.3 else ("low" if metrics.beta < 0.7 else "balanced")
        st.metric(
            "Beta to SPY",
            f"{metrics.beta:+.2f}",
            delta=beta_warn,
            delta_color="off",
            help=(
                f"Daily covariance of port vs SPY divided by SPY variance. "
                f"Correlation: {metrics.correlation:+.2f}."
            ),
        )
    with c4:
        st.metric(
            "Alpha (annualized)",
            f"{metrics.alpha_annualized:+.1f}%",
            help=(
                "Jensen's alpha: portfolio return minus β × SPY return, "
                "annualized. The 'unexplained' return after stripping "
                "out the benchmark beta exposure."
            ),
        )

    # ---- Sample-size honesty ----
    if metrics.period_days < 30:
        st.warning(
            f"⚠️ Only {metrics.period_days} days of history. IR / alpha / "
            "beta are NOT statistically meaningful at this sample size "
            "(need 30-60 daily obs minimum). Track the trend over time, "
            "don't act on a single point."
        )
    elif metrics.period_days < 90:
        st.info(
            f"ℹ️ {metrics.period_days} days of history — preliminary signal. "
            "Confidence improves materially past 90 days."
        )

    # ---- Chart ----
    dates, port, spy = nav_series_for_chart(snaps)
    chart_df = {
        "date": [d.isoformat() for d in dates],
        "Portfolio": port,
        "SPY": spy,
    }
    try:
        import pandas as pd
        df = pd.DataFrame(chart_df).set_index("date")
        st.line_chart(df, use_container_width=True)
    except Exception:
        st.caption("_chart render failed_")

    # ---- Secondary detail row ----
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric(
            "Daily win-rate vs SPY",
            f"{metrics.win_rate*100:.0f}%",
            help="Fraction of days where portfolio daily return > SPY daily return.",
        )
    with c2:
        st.metric(
            "Max relative DD",
            f"{metrics.max_relative_drawdown:.2f}%",
            help="Worst sustained underperformance vs SPY (peak-to-trough on the relative-equity curve).",
        )
    with c3:
        st.metric(
            "Period",
            f"{metrics.period_days} days",
            help="Days of NAV-vs-SPY history available.",
        )

    # ---- Refresh / extend backfill ----
    with st.expander("Backfill / refresh data"):
        st.caption(
            "Re-pulls Alpaca portfolio history + yfinance SPY closes. "
            "Idempotent — overwrites existing snapshot rows by date."
        )
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            if st.button("Backfill 6M"):
                with st.spinner("Backfilling..."):
                    n = backfill_journal(period="6M")
                st.success(f"Wrote {n} rows."); st.rerun()
        with col_b:
            if st.button("Backfill 1Y"):
                with st.spinner("Backfilling..."):
                    n = backfill_journal(period="1Y")
                st.success(f"Wrote {n} rows."); st.rerun()
        with col_c:
            if st.button("Backfill ALL"):
                with st.spinner("Backfilling..."):
                    n = backfill_journal(period="all")
                st.success(f"Wrote {n} rows."); st.rerun()


def _render_effective_exposure_panel() -> None:
    """v3.73.19: surface every vol-scaling layer in one dashboard
    panel.

    The v3.73.18 critique exposed that a major sizing layer (VIX-based
    risk gate) existed but wasn't documented; the live book at 68%
    gross looked inconsistent with the 80% target because the VIX
    gate's 0.85 multiplier was hidden in the orchestrator and only
    surfaced in the per-run decision report.

    This panel makes the multiplication chain explicit:
       base_target × deployment_anchor × VIX_gate × regime_overlay
       × drawdown_protocol = effective_target_gross

    If actual_gross deviates from effective_target_gross by >2pp,
    flag for investigation.
    """
    import os

    st.subheader("🎚️ Effective exposure decomposition")
    st.caption(
        "Every layer that scales gross. The product is the target the "
        "orchestrator should produce; if actual gross deviates, the "
        "control plane has a bug."
    )

    # Gather the active multipliers
    base = 0.80
    layers = []  # (label, multiplier, source)

    # Deployment anchor — uses 60-day rolling-max equity vs 200-day
    try:
        from trader.deployment_anchor import get_anchor_pct
        anchor_pct = get_anchor_pct()
        layers.append(("Deployment anchor", anchor_pct,
                        "30/200-day equity rolling-max ratio"))
    except (ImportError, AttributeError):
        # Different version of deployment_anchor; mark as 1.0 fallback
        layers.append(("Deployment anchor", 1.0, "module API mismatch"))
    except Exception as e:
        layers.append(("Deployment anchor", 1.0, f"error: {type(e).__name__}"))

    # VIX-based risk gate
    try:
        import yfinance as yf
        vix_data = yf.download("^VIX", period="5d", progress=False, auto_adjust=False)
        if vix_data is not None and not vix_data.empty:
            vix = float(vix_data["Close"].iloc[-1])
            # Match the production rule: VIX>20 → 0.7, VIX>16 → 0.85, else 1.0
            if vix > 20:
                vix_mult = 0.70
            elif vix > 16:
                vix_mult = 0.85
            else:
                vix_mult = 1.0
            layers.append(("VIX risk gate", vix_mult,
                            f"VIX={vix:.1f} → ×{vix_mult}"))
        else:
            layers.append(("VIX risk gate", 1.0, "VIX unavailable"))
    except Exception as e:
        layers.append(("VIX risk gate", 1.0, f"error: {type(e).__name__}"))

    # Regime overlay (HMM + GARCH + macro)
    try:
        from trader.regime_overlay import get_gross_multiplier
        regime_mult = get_gross_multiplier()
        layers.append(("Regime overlay", regime_mult,
                        "HMM × GARCH × macro (env-gated)"))
    except (ImportError, AttributeError):
        # Disabled or fallback path
        layers.append(("Regime overlay", 1.0, "DISABLED or fallback"))
    except Exception as e:
        layers.append(("Regime overlay", 1.0, f"error: {type(e).__name__}"))

    # Drawdown protocol — read mode + currently-fired tier
    try:
        from trader.risk_manager import drawdown_protocol_mode
        dd_mode = drawdown_protocol_mode()
        # In ADVISORY mode, the protocol doesn't scale; it warns only
        dd_mult = 1.0  # ADVISORY default; scales only in ENFORCING
        layers.append(("Drawdown protocol", dd_mult,
                        f"mode={dd_mode} (multiplier {dd_mult} unless ENFORCING + tier fired)"))
    except Exception as e:
        layers.append(("Drawdown protocol", 1.0, f"error: {type(e).__name__}"))

    # Vol-target overlay (env-flagged)
    if os.environ.get("VOL_TARGET_ENABLED", "0") == "1":
        layers.append(("Vol-target overlay", "computed at run-time",
                        "VOL_TARGET_ENABLED=1 (active)"))
    else:
        layers.append(("Vol-target overlay", 1.0,
                        "VOL_TARGET_ENABLED=0 (off)"))

    # Compute effective target
    effective = base
    rows = [
        {"layer": "Base target gross", "multiplier": "—",
         "value": f"{base*100:.1f}%", "source": "STRATEGY_AND_RISK.md"}
    ]
    for label, mult, src in layers:
        if isinstance(mult, (int, float)):
            effective *= mult
            rows.append({
                "layer": label,
                "multiplier": f"×{mult:.3f}",
                "value": f"{effective*100:.2f}%",
                "source": src,
            })
        else:
            rows.append({
                "layer": label,
                "multiplier": f"({mult})",
                "value": "see run-time",
                "source": src,
            })

    # Pull live actual gross
    try:
        from trader.positions_live import fetch_live_portfolio
        p = fetch_live_portfolio()
        if p.equity:
            deployed = sum((x.market_value or 0) for x in p.positions)
            actual_gross = deployed / p.equity
        else:
            actual_gross = None
    except Exception:
        actual_gross = None

    # Headline metrics
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Base target", f"{base*100:.0f}%")
    with c2:
        st.metric("Effective target",
                   f"{effective*100:.1f}%",
                   delta=f"after {len(layers)} layers",
                   delta_color="off")
    with c3:
        if actual_gross is not None:
            drift = actual_gross - effective
            color = "off" if abs(drift) < 0.02 else "inverse"
            st.metric("Actual gross",
                       f"{actual_gross*100:.1f}%",
                       delta=f"{drift*100:+.2f}pp vs effective",
                       delta_color=color)
        else:
            st.metric("Actual gross", "n/a")

    # Decomposition table
    st.dataframe(rows, use_container_width=True, hide_index=True)

    if actual_gross is not None and abs(actual_gross - effective) > 0.02:
        st.warning(
            f"⚠️ Actual gross ({actual_gross*100:.1f}%) deviates from "
            f"effective target ({effective*100:.1f}%) by "
            f"{(actual_gross - effective)*100:+.2f}pp. Either a layer "
            "is missing from this decomposition or the orchestrator "
            "didn't fully execute the target. Investigate before next "
            "rebalance."
        )
    elif actual_gross is not None:
        st.success(
            f"✅ Actual gross matches effective target within 2pp. "
            f"All vol-scaling layers accounted for."
        )


def _render_portfolio_caps_panel() -> None:
    """v3.73.5: surface whether the 8% single-name + 25% sector caps
    are binding on the live book right now. Reads the live portfolio,
    computes weights, and applies the caps as if rebalancing today —
    without actually mutating anything. Output:

      - Pre-cap top-3 names + top-3 sectors
      - Which cap (if any) bound, and on what
      - Post-cap deltas

    The DD analysis (v3.73.4) showed the sector cap is binding (Tech
    @ 28-29% in our universe; cap at 25%); the name cap was claimed
    non-binding at top-15 equal-weight, but on the LIVE book it IS
    binding because positions drift past their initial weights via
    market action. CAT is currently 10.9% — over the 8% cap.
    """
    st.subheader("📐 Concentration caps")
    st.caption(
        "8% single-name cap, 25% sector cap. Applied at score-to-"
        "weight conversion. Per the v3.73.4 DD: the sector cap is the "
        "binding one and primarily reduces vol/DD without sacrificing "
        "return; the name cap is defensive against future weight drift."
    )

    try:
        from trader.positions_live import fetch_live_portfolio
        from trader.portfolio_caps import (
            apply_portfolio_caps,
            SINGLE_NAME_CAP_PCT, SECTOR_CAP_PCT,
        )
        from trader.sectors import get_sector
    except Exception as e:
        st.caption(f"_panel unavailable: {e}_")
        return

    p = fetch_live_portfolio()
    if not p.positions or not p.equity:
        st.caption("_no live positions to evaluate_")
        return

    pre = {
        pos.symbol: (pos.market_value or 0) / p.equity
        for pos in p.positions
        if pos.market_value
    }
    res = apply_portfolio_caps(pre, get_sector,
                                name_cap=SINGLE_NAME_CAP_PCT,
                                sector_cap=SECTOR_CAP_PCT)

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric(
            "Max single name",
            f"{res.pre_cap_max_name*100:.1f}%",
            delta=(f"{(res.post_cap_max_name - res.pre_cap_max_name)*100:+.1f}pp"
                   if res.name_cap_bound else None),
            delta_color="inverse" if res.name_cap_bound else "off",
        )
    with c2:
        st.metric(
            "Max sector",
            f"{res.pre_cap_max_sector*100:.1f}% ({res.pre_cap_max_sector_name})",
            delta=(f"{(res.post_cap_max_sector - res.pre_cap_max_sector)*100:+.1f}pp"
                   if res.sector_cap_bound else None),
            delta_color="inverse" if res.sector_cap_bound else "off",
        )
    with c3:
        binding = []
        if res.name_cap_bound: binding.append("name")
        if res.sector_cap_bound: binding.append("sector")
        st.metric(
            "Caps binding",
            ", ".join(binding) if binding else "none",
            delta=(f"{res.redistributed_pct*100:.1f}pp redistributed"
                   if binding else None),
            delta_color="off",
        )

    if res.name_cap_bound or res.sector_cap_bound:
        st.info(f"**Cap result:** {res.summary()}")
    else:
        st.success(
            "**No cap binding.** The book is currently within the "
            "8% / 25% limits."
        )

    # Show top names that would change at the next rebalance
    deltas = {
        t: res.targets[t] - pre[t]
        for t in pre
        if abs(res.targets[t] - pre[t]) > 1e-4
    }
    if deltas:
        rows = [
            {
                "ticker": t,
                "sector": get_sector(t),
                "current %": f"{pre[t]*100:.2f}%",
                "post-cap %": f"{res.targets[t]*100:.2f}%",
                "Δ pp": f"{(res.targets[t] - pre[t])*100:+.2f}",
            }
            for t in sorted(deltas, key=lambda x: -abs(deltas[x]))
        ]
        with st.expander("Per-name change at next rebalance"):
            st.dataframe(rows, use_container_width=True, hide_index=True)


def view_overview():
    st.title("🏠 Overview")
    st.caption("Headline metrics + sector heatmap + last 5 runs.")
    # v5.0.0 — auto-router LIVE pick widget at top of Overview.
    # Surfaces what strategy the daily orchestrator will route to
    # on the next rebalance. Reads from journal.runs.notes (set by
    # main.py's auto-router persistence) + a fresh select_live() call
    # to show what would be picked RIGHT NOW.
    try:
        from trader.auto_router import (
            select_live,
            MIN_EVIDENCE_MONTHS,
            MAX_BETA,
            HYSTERESIS_MARGIN,
        )
        decision = select_live()
        cols = st.columns([2, 1, 1, 1])
        if decision.selected:
            cols[0].metric(
                "🎯 LIVE auto-routed to",
                decision.selected,
                f"runner-up: {decision.runner_up}" if decision.runner_up else None,
            )
        else:
            cols[0].metric("🎯 LIVE auto-routed to", "(no candidate eligible)")
        cols[1].metric("Eligible candidates", decision.eligible_count)
        cols[2].metric("Hysteresis", "applied" if decision.hysteresis_applied else "—")
        cols[3].metric("Incumbent", decision.incumbent or "—")
        st.caption(
            f"_v5.0.0 auto-router · min_evidence={MIN_EVIDENCE_MONTHS}mo · "
            f"max_β={MAX_BETA:.2f} · hysteresis={HYSTERESIS_MARGIN:.2f} IR points · "
            f"{decision.reason}_"
        )
        st.divider()
    except Exception as e:
        st.caption(f"_auto-router widget unavailable: {e}_")
        st.divider()

    # v3.65.0: big-block price headline (Nasdaq/CNBC pattern) above the
    # 6-up metric grid. The grid still ships the supporting numbers
    # (cash, vs anchor, regime, freeze) — but the dominant equity number
    # gets its own oversized treatment so the user sees it instantly.
    _render_price_headline()
    _headline_metrics()

    # v3.73.6: SP500 BENCHMARK panel — the goal of the system is to
    # beat SPY, so this panel is THE headline measurement. NAV-vs-SPY
    # chart + active return + IR + beta + alpha. Sized large because
    # without it, every other metric is absolute and disconnected
    # from the only question that matters.
    st.divider()
    _render_benchmark_panel()

    # v3.73.2: four-threshold drawdown protocol panel. Surfaces the
    # current 180d-peak DD against the four tiers (-5/-8/-12/-15) per
    # docs/RISK_FRAMEWORK.md §6. Default ADVISORY — shows the tier
    # without mutating targets. Caller flips to ENFORCING via env when
    # comfortable.
    _render_drawdown_protocol_panel()

    st.divider()

    # v3.73.5: portfolio concentration caps panel. Surfaces whether
    # the 8% single-name cap and 25% sector cap are binding RIGHT NOW
    # on the live book (vs. the equal-weighted top-15 the strategy
    # would produce on the next rebalance). Without this surface, the
    # operator can't tell whether the cap is doing real work.
    _render_portfolio_caps_panel()

    st.divider()

    # v3.73.19: effective exposure decomposition. Shows EVERY
    # vol-scaling layer in one place so 'why is the live book at
    # 68% gross when the target is 80%' can be answered at a glance
    # rather than requiring a forensic investigation.
    _render_effective_exposure_panel()

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
    # v3.66.0: pull from canonical EquityState so the equity / day-P&L
    # cards match what the Overview headline shows. The day-P&L card is
    # rendered through _render_day_pl_card so the OPEN vs CLOSED label
    # logic lives in one place.
    state = _get_equity_state()
    cc = st.columns(4)
    cc[0].metric("Equity", f"${live.equity:,.0f}" if live.equity else "n/a")
    cc[1].metric("Cash", f"${live.cash:,.0f}" if live.cash else "n/a")
    _render_day_pl_card(cc[2], state)
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

        # v3.58.2 — per-symbol drill-down + linked-symbol shortcut
        st.caption("🔍 Drill into a symbol:")
        sym_pick = st.selectbox(
            "Symbol",
            options=[""] + [p.symbol for p in live.positions],
            label_visibility="collapsed",
            key="live_drill_pick",
        )
        bcols = st.columns(2)
        if bcols[0].button("🔍 Open detail", use_container_width=True,
                           disabled=not sym_pick, key="live_drill_open"):
            st.session_state.symbol_drill_down = sym_pick
            st.rerun()
        if bcols[1].button("🔗 Set as linked symbol", use_container_width=True,
                           disabled=not sym_pick, key="live_drill_link"):
            st.session_state.linked_symbol = sym_pick
            st.rerun()
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
    # v3.57.2 (Phase 6): respect the cross-panel linked symbol.
    link = st.session_state.get("linked_symbol", "")
    if link:
        st.info(f"🔗 Filtered to **{link}** — clear via the sidebar to see all.")
        decisions = query(str(DB_PATH),
                          "SELECT ts, ticker, action, style, score, rationale_json, final "
                          "FROM decisions WHERE ticker = ? "
                          "ORDER BY ts DESC LIMIT 200",
                          params=(link,))
    else:
        decisions = query(str(DB_PATH),
                          "SELECT ts, ticker, action, style, score, rationale_json, final "
                          "FROM decisions ORDER BY ts DESC LIMIT 50")
    if decisions.empty:
        st.caption("_no decisions in journal_")
        return
    # v6.0.x: module-level helpers (testable, no Streamlit dep)
    from trader.decisions_renderer import (
        fmt_why, fmt_reasoning,
    )
    decisions["why"] = decisions["rationale_json"].apply(fmt_why)
    view = decisions[["ts", "ticker", "action", "style", "score", "why", "final"]]
    st.dataframe(view, use_container_width=True, hide_index=True)

    # v6.0.x: per-decision paragraph reasoning. Each row becomes an
    # expander so the user can scan the compact table above for grep,
    # then drill into any row for the full explanation. Limited to the
    # 20 most recent to keep the page snappy.
    st.divider()
    st.subheader("📝 Detailed reasoning")
    st.caption("Click any row to expand the full explanation — what signal "
                "fired, why the strategy picked this name, and how the auto-router "
                "decided the sizing.")
    for _, row in decisions.head(20).iterrows():
        ticker = row.get("ticker", "?")
        action = row.get("action") or ""
        ts = row.get("ts", "")
        final = row.get("final") or ""
        # Compact header: timestamp · ticker · action · sizing
        score_disp = f" (score {row.get('score'):.3f})" if row.get("score") not in (None, 0) else ""
        header = f"**{ts[:19]}** · {ticker} · {action}{score_disp}"
        with st.expander(header, expanded=False):
            st.markdown(fmt_reasoning(row.to_dict()))
            if final:
                st.caption(f"Final: `{final}`")

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
    # v3.57.2 (Phase 6): respect linked_symbol
    link = st.session_state.get("linked_symbol", "")
    if link:
        st.info(f"🔗 Filtered to **{link}** — clear via the sidebar to see all.")
        lots = query(str(DB_PATH),
                     "SELECT id, symbol, sleeve, opened_at, qty, open_price, open_order_id "
                     "FROM position_lots WHERE closed_at IS NULL AND symbol = ? "
                     "ORDER BY opened_at DESC",
                     params=(link,))
    else:
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
    if link:
        closed = query(str(DB_PATH),
                       "SELECT symbol, sleeve, opened_at, closed_at, qty, "
                       "open_price, close_price, realized_pnl FROM position_lots "
                       "WHERE closed_at IS NOT NULL AND symbol = ? "
                       "ORDER BY closed_at DESC LIMIT 30",
                       params=(link,))
    else:
        closed = query(str(DB_PATH),
                       "SELECT symbol, sleeve, opened_at, closed_at, qty, "
                       "open_price, close_price, realized_pnl FROM position_lots "
                       "WHERE closed_at IS NOT NULL ORDER BY closed_at DESC LIMIT 30")
    if not closed.empty:
        st.dataframe(closed, use_container_width=True, hide_index=True)
    else:
        st.caption("_no closed lots yet_")


# ============================================================
# View: TLH (tax-loss harvesting)
# v6.0.x: surfaces the YTD realized loss + projected tax savings
# from the direct-index core sleeve. Pulls from position_lots.
# ============================================================
@st.cache_data(ttl=30, show_spinner=False)
def _cached_tlh_year(db_path_str: str, year: int):
    """Cached fetch of loss-close events for a tax year."""
    from scripts.tlh_year_end import (
        fetch_loss_closes, find_wash_sale_flags
    )
    closes = fetch_loss_closes(db_path_str, year)
    flags = find_wash_sale_flags(db_path_str, closes)
    return closes, flags


def view_tlh():
    st.title("🌳 Tax-loss harvesting")
    st.caption("v6.0.x — direct-index core sleeve. Realized losses from "
                "harvest swaps, available to offset capital gains + ordinary "
                "income at tax time.")

    # --- Master gate notice ---
    tlh_on = os.environ.get("TLH_ENABLED", "false").lower() == "true"
    core_pct = float(os.environ.get("DIRECT_INDEX_CORE_PCT", "0.70"))
    if not tlh_on:
        st.warning(
            "**TLH is OFF.** Set `TLH_ENABLED=true` in the daemon env "
            "and restart `com.trader.daily-run` to start harvesting. "
            "Until then, only the auto-router alpha sleeve runs."
        )
    else:
        st.success(
            f"**TLH is ON.** Direct-index core: {core_pct*100:.0f}% of capital. "
            f"Alpha sleeve: {(1-core_pct)*100:.0f}%."
        )

    # --- Tax rate inputs ---
    with st.expander("⚙️ Marginal tax rates (drives savings estimate)",
                      expanded=False):
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            fed_rate = st.slider("Federal marginal", 0.10, 0.37, 0.32, 0.01,
                                   help="Default 32% is the bracket for "
                                        "$200-500k MFJ in 2025-26.")
        with col_b:
            state_rate = st.slider("State marginal", 0.00, 0.133, 0.05, 0.005,
                                     help="Default 5%. CA top is 13.3%; NY "
                                          "top is 10.9%; TX/FL/WA are 0%.")
        with col_c:
            cg_assumed = st.number_input(
                "Realized capital gains this yr ($)",
                value=0.0, step=1000.0,
                help="Losses offset these FIRST (unlimited). After that, "
                     "$3k/yr offsets ordinary income, rest carries forward."
            )

    # --- Year selector ---
    cur_year = datetime.utcnow().year
    year = st.selectbox("Tax year",
                         options=list(range(cur_year, cur_year - 4, -1)),
                         index=0)

    closes, flags = _cached_tlh_year(str(DB_PATH), year)

    if not closes:
        st.info(
            f"No loss-realizing closes recorded for {year} yet.\n\n"
            "If you just turned TLH on, positions need to accumulate "
            "5%+ losses (the harvest threshold) before swaps fire. "
            "Typical first-year ramp: 6–12 months to full throughput."
        )
        return

    # --- Headline metrics ---
    total_loss = sum(ev.realized_pnl for ev in closes)
    lt_loss = sum(ev.realized_pnl for ev in closes if ev.is_long_term)
    st_loss = total_loss - lt_loss

    from scripts.tlh_year_end import estimate_tax_savings, aggregate_by_symbol
    est = estimate_tax_savings(
        total_loss=total_loss,
        federal_rate=fed_rate,
        state_rate=state_rate,
        capital_gains_offset=cg_assumed,
    )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Loss-closes", f"{len(closes)}")
    col2.metric("Realized loss YTD", f"${total_loss:,.0f}",
                 help="Negative number — this is what offsets gains.")
    col3.metric("★ Tax saved (est)", f"${est['total_savings']:,.0f}",
                 help=f"At {est['combined_rate']:.0%} combined rate. "
                      f"Includes both cap-gain and ordinary offsets.")
    col4.metric("Carry-forward", f"${est['carry_forward']:,.0f}",
                 help="Offsets future years' gains, no expiry.")

    # --- Breakdown ---
    st.divider()
    st.subheader("Where the savings come from")
    st.markdown(
        f"- Capital-gains offset: **${est['cg_offset']:,.0f}** "
        f"(of ${cg_assumed:,.0f} assumed gains) "
        f"→ saves **${est['cg_savings']:,.0f}**\n"
        f"- Ordinary-income offset: **${est['ordinary_offset']:,.0f}** "
        f"(IRS cap $3,000/yr) "
        f"→ saves **${est['ordinary_savings']:,.0f}**\n"
        f"- Carry-forward to future years: **${est['carry_forward']:,.0f}**"
    )
    if st_loss < 0 or lt_loss < 0:
        st.caption(f"Term breakdown: ST ${st_loss:,.0f}  ·  LT ${lt_loss:,.0f}  "
                    f"(ST losses offset ST gains 1:1; LT-then-ST netting per Form 8949)")

    # --- Per-ticker table ---
    st.divider()
    st.subheader("Per-ticker rollup")
    rows = aggregate_by_symbol(closes)
    if rows:
        df_rows = pd.DataFrame([
            {
                "Symbol": r["symbol"],
                "Closes": r["count"],
                "Realized loss": round(r["total_loss"], 2),
                "ST closes": r["st_count"],
                "LT closes": r["lt_count"],
                "Sleeves": r["sleeves"],
            }
            for r in rows
        ])
        st.dataframe(df_rows, use_container_width=True, hide_index=True)

    # --- Wash-sale flags ---
    st.divider()
    st.subheader("Wash-sale flags")
    if not flags:
        st.success("✅ None detected within the 31-day window. "
                    "The planner avoids them by design (sector-matched, "
                    "not substantially-identical replacements).")
    else:
        st.warning(
            f"⚠️ {len(flags)} potential wash-sale event(s) detected — "
            "the broker 1099-B is authoritative; hand this list to your "
            "accountant for review."
        )
        flag_rows = pd.DataFrame([
            {
                "Symbol": f.symbol,
                "Loss closed": f.loss_closed_at[:19],
                "Re-bought": f.repurchase_at[:19],
                "Days between": f.days_between,
                "$ at risk": round(f.loss_amount, 2),
            }
            for f in flags
        ])
        st.dataframe(flag_rows, use_container_width=True, hide_index=True)

    # --- Recent harvest events ---
    st.divider()
    st.subheader("Most recent loss-closes (last 20)")
    recent_rows = sorted(closes, key=lambda e: e.closed_at, reverse=True)[:20]
    df_recent = pd.DataFrame([
        {
            "Symbol": ev.symbol,
            "Sleeve": ev.sleeve,
            "Opened": ev.opened_at[:10],
            "Closed": ev.closed_at[:10],
            "Held (days)": ev.holding_period_days,
            "Term": "LT" if ev.is_long_term else "ST",
            "Qty": ev.qty,
            "Loss $": round(ev.realized_pnl, 2),
        }
        for ev in recent_rows
    ])
    st.dataframe(df_recent, use_container_width=True, hide_index=True)

    # --- CSV download ---
    st.divider()
    st.subheader("Year-end export")
    st.caption("Same CSV that `python scripts/tlh_year_end.py --csv-out` produces. "
                "Columns mirror 1099-B / Form 8949. Hand to your accountant.")
    import io
    buf = io.StringIO()
    import csv as _csv
    w = _csv.writer(buf)
    w.writerow(["symbol", "sleeve", "date_acquired", "date_sold", "qty",
                 "proceeds", "cost_basis", "realized_loss",
                 "holding_period_days", "term"])
    for ev in closes:
        w.writerow([
            ev.symbol, ev.sleeve, ev.opened_at[:10], ev.closed_at[:10],
            f"{ev.qty:.6f}",
            f"{ev.qty * ev.close_price:.2f}",
            f"{ev.qty * ev.open_price:.2f}",
            f"{ev.realized_pnl:.2f}",
            ev.holding_period_days,
            "LT" if ev.is_long_term else "ST",
        ])
    st.download_button(
        label=f"⬇️ Download tlh_{year}.csv",
        data=buf.getvalue(),
        file_name=f"tlh_{year}.csv",
        mime="text/csv",
    )

    # --- How to read this ---
    with st.expander("📚 How to read this — what these numbers mean",
                       expanded=False):
        st.markdown("""
**What the system does.** Each daily run, the TLH planner scans every
position in the direct-index core (Book A) and checks: is this name
down ≥ 5% from cost basis? If yes, AND a sector-matched replacement
is available outside the 31-day wash-sale window, the system sells
the loser and buys the replacement. The realized loss is journaled.
Market exposure stays the same — only the tax lot changes.

**What "tax saved" means.** The estimated $ saved is your realized
loss × your combined marginal rate. The IRS lets losses offset:
1. **Realized capital gains** (any amount, no limit)
2. **Ordinary income** (up to $3,000/year)
3. **Future years** (carry-forward, indefinite)

Losses are applied in that order — gains first, then ordinary, then
carry forward. The "Where the savings come from" section above
breaks out which bucket is doing the work.

**Why the estimate may differ from your accountant.**
- We use combined federal + state rates as-entered above. Your
  actual rate depends on AGI, deductions, AMT, NIIT, and state-
  specific quirks.
- LT capital gains use a lower rate (15-20% + state, vs 32%+ here).
  If most of your offsetting gains are LT, the savings are smaller.
  Override `Realized capital gains this yr` to 0 and add LT-only
  manually if you want term-aware math.
- Wash-sale recapture: if the broker 1099-B reports a disallowed
  loss (which the system tries to avoid), the actual tax savings
  drops by that amount. Check the wash-sale flags table above.

**When you should DO something.**
- Anytime the "⚠️ Wash-sale flags" panel lights up: surface to
  accountant; don't ignore.
- Year-end (December): run `python scripts/tlh_year_end.py` for
  the printable accountant-handoff version. Or use the CSV
  download above.
        """)


# ============================================================
# View: Performance (equity vs SPY overlay)
# ============================================================
# v4.0.1: TTL dropped from 600s to 30s. The headline was showing
# end-of-prior-day numbers mid-session because (a) daily_snapshot is
# only written at orchestrator close and (b) the cache held results
# 10min anyway. live_equity is injected per-call so the headline
# tracks the broker, not the journal write cycle. 30s ttl keeps
# Streamlit's redraw bursts cheap without lying about staleness.
@st.cache_data(ttl=30, show_spinner="📊 Computing performance metrics...")
def _cached_performance(window_days: int, live_equity: float | None = None):
    from trader.analytics import compute_performance
    return compute_performance(window_days=window_days, live_equity=live_equity)


# v4.0.1: TTL dropped 600s -> 60s on these three sibling Performance
# panels. They feed off the same daily_snapshot table as the headline
# and were getting cached-stale for up to 10 minutes. 60s is enough
# to amortize Streamlit's redraw bursts without hiding new data on click.
@st.cache_data(ttl=60, show_spinner=False)
def _cached_rolling_sharpe(window: int, days: int):
    from trader.analytics import compute_rolling_sharpe
    return compute_rolling_sharpe(window=window, days=days)


@st.cache_data(ttl=60, show_spinner=False)
def _cached_drawdown_periods(days: int):
    from trader.analytics import compute_drawdown_periods
    return compute_drawdown_periods(days=days)


@st.cache_data(ttl=60, show_spinner=False)
def _cached_monthly_returns(days: int):
    from trader.analytics import compute_monthly_returns
    return compute_monthly_returns(days=days)


def view_performance():
    st.title("📈 Performance")
    st.caption("Risk-adjusted returns, drawdown analysis, and SPY-relative attribution. "
               "All metrics annualized where applicable; cached 10 min.")

    # v3.65.0: industry-standard timeframe chips (Yahoo / Nasdaq / CNBC /
    # TipRanks all use this exact set). Replaces the previous Streamlit
    # selectbox which was unfamiliar to traders.
    window = _render_timeframe_chips("perf_window", default_label="3M")

    # v4.0.1: pull live broker equity once per click and inject into
    # compute_performance so the headline reflects current intraday
    # state, not the last journal write.
    _live_state = _get_equity_state()
    _live_eq = _live_state.equity_now if _live_state.error is None else None
    perf = _cached_performance(window, live_equity=_live_eq)

    # Freshness caption — explicit about WHAT the headline is computed from
    if _live_eq is not None:
        st.caption(
            f"_As of {datetime.now():%Y-%m-%d %H:%M:%S} · headline includes "
            f"live broker equity ${_live_eq:,.2f} (source: "
            f"{_live_state.source}) · cache 30s_"
        )
    else:
        # Live broker unreachable — fall back to last journal snapshot
        st.caption(
            f"_⚠️ Live broker unreachable ({_live_state.error}). Headline "
            f"reflects last journal snapshot only (typically end-of-prior-day)._"
        )
    if perf.n_obs < 2:
        st.warning(
            f"⚠️ **Performance metrics need ≥2 daily snapshots; we have "
            f"{perf.n_obs}.** This page is data-thin until the trading "
            f"system has accumulated daily snapshots over a real trading "
            f"window."
        )
        # Show whatever live state we DO have
        st.subheader("📍 Current account snapshot")
        try:
            from trader.copilot import dispatch_tool
            ports = dispatch_tool("get_portfolio_status", {})
            if not ports.get("error"):
                cc = st.columns(4)
                cc[0].metric("Equity", f"${ports.get('equity', 0):,.0f}")
                cc[1].metric("Cash", f"${ports.get('cash', 0):,.0f}")
                # v3.66.0: shared day-P&L card helper (was inlined here +
                # in view_live_positions; now one definition)
                _render_day_pl_card(cc[2], _get_equity_state())
                cc[3].metric("# Positions", ports.get('n_positions', 0))
            else:
                st.caption(f"Could not load live portfolio: {ports['error']}")
        except Exception as e:
            st.caption(f"Live portfolio unavailable: {e}")

        st.divider()
        st.subheader("📚 What this page WILL show once data accumulates")
        st.markdown("""
Once ≥2 daily snapshots exist, this page renders:

- **Headline metrics** — total return, Sharpe ratio, max drawdown, CAGR
- **Risk/reward** — Sortino, Calmar, Information ratio, beta vs SPY
- **After-cost numbers** — gross vs net (after spread, turnover, tax)
- **Equity curve vs SPY** — cumulative P&L overlay with drawdown shading
- **LowVolSleeve shadow** — second-sleeve curve for diversification check
- **Rolling Sharpe** — 30-day window
- **Drawdown periods** — start, end, depth, duration
- **Monthly returns table** — calendar P&L grid

**To populate this page:**
1. Run the daily orchestrator: `python -m trader.main` (writes `daily_snapshot` row)
2. Or sync historical snapshots from GitHub via ⚙️ Settings → Sync state
3. Or wait for the cron to fire daily

Snapshots are written to `data/journal.db` table `daily_snapshot`.
""")
        return

    # ---- HEADLINE: 4 columns, the things that matter most ----
    st.subheader("Headline")
    c = st.columns(4)
    c[0].metric("Total return",
                f"{perf.total_return*100:+.2f}%" if perf.total_return is not None else "n/a",
                f"vs SPY {perf.excess_total_return*100:+.2f}%" if perf.excess_total_return is not None else None)
    c[1].metric("Sharpe (annualized)",
                f"{perf.sharpe:.2f}" if perf.sharpe is not None else "n/a",
                "good >1.0 / great >2.0" if perf.sharpe is not None else None)
    c[2].metric("Max drawdown",
                f"{perf.max_drawdown*100:+.2f}%" if perf.max_drawdown is not None else "n/a",
                f"now {perf.drawdown_now*100:+.2f}%" if perf.drawdown_now is not None else None)
    c[3].metric("CAGR",
                f"{perf.cagr*100:+.2f}%" if perf.cagr is not None else "n/a",
                "annualized")

    # ---- RISK / REWARD: separate row ----
    st.subheader("Risk / reward")
    c = st.columns(4)
    c[0].metric("Sortino ratio",
                f"{perf.sortino:.2f}" if perf.sortino is not None else "n/a",
                "downside-only Sharpe")
    c[1].metric("Calmar ratio",
                f"{perf.calmar:.2f}" if perf.calmar is not None else "n/a",
                "CAGR / |max DD|")
    c[2].metric("Information ratio",
                f"{perf.information_ratio:.2f}" if perf.information_ratio is not None else "n/a",
                "active return / TE")
    c[3].metric("Beta vs SPY",
                f"{perf.beta_vs_spy:.2f}" if perf.beta_vs_spy is not None else "n/a",
                "1.0 = market exposure")

    # ---- v3.58.1 NetCostModel — gross vs after-cost ----
    try:
        from trader.v358_world_class import NetCostModel
        nc = NetCostModel()
        if nc.status() in ("LIVE", "SHADOW"):
            st.subheader("After costs (v3.58 NetCostModel — SHADOW)")
            st.caption(
                "Backtest Sharpe is gross of spread, borrow, and tax drag. "
                "These columns subtract the realistic cost stack so you see "
                "**after-cost** numbers — which is what actually compounds in "
                "the account."
            )
            drag_bps = nc.annual_drag_bps()
            net_cagr = nc.net_return(perf.cagr) if perf.cagr is not None else None
            # Approx net Sharpe ≈ gross Sharpe × (1 - drag_share_of_return).
            # If drag is 60bps and CAGR 19%, drag is ~3% of return — Sharpe
            # drops the same fraction. Conservative.
            if perf.sharpe is not None and perf.cagr and perf.cagr > 0:
                drag_share = (drag_bps / 1e4) / max(perf.cagr, 1e-6)
                net_sharpe = perf.sharpe * (1 - drag_share) * (1 - nc.st_cap_gains_pct)
            else:
                net_sharpe = None
            cc = st.columns(4)
            cc[0].metric(
                "Annual cost drag",
                f"{drag_bps:.1f} bps",
                f"{nc.spread_bps:.1f}bp/side × {nc.monthly_turnover_pct*100:.0f}% turnover × 12mo",
            )
            cc[1].metric(
                "Net CAGR (after-cost, after-tax)",
                f"{net_cagr*100:+.2f}%" if net_cagr is not None else "n/a",
                f"vs gross {perf.cagr*100:+.2f}%" if perf.cagr is not None else None,
            )
            cc[2].metric(
                "Net Sharpe (approx)",
                f"{net_sharpe:.2f}" if net_sharpe is not None else "n/a",
                f"vs gross {perf.sharpe:.2f}" if perf.sharpe is not None else None,
            )
            cc[3].metric(
                "Tax assumption",
                f"{nc.st_cap_gains_pct*100:.0f}% ST",
                "monthly turnover → ST cap gains",
            )
            st.caption(
                "💡 If net Sharpe is dramatically lower than gross, the "
                "biggest fixes are (a) reduce monthly turnover via stickier "
                "rebalance, (b) hold > 365d for LT cap gains rate, (c) tighter "
                "execution to cut spread drag."
            )
    except Exception as e:
        st.caption(f"_NetCostModel unavailable: {type(e).__name__}: {e}_")

    # ---- ALPHA / VOL ----
    c = st.columns(4)
    c[0].metric("Alpha (Jensen, annual)",
                f"{perf.alpha_vs_spy_annual*100:+.2f}%" if perf.alpha_vs_spy_annual is not None else "n/a",
                "skill-adjusted return")
    c[1].metric("Volatility (annual)",
                f"{perf.vol_annual*100:.2f}%" if perf.vol_annual is not None else "n/a",
                "std × √252")
    c[2].metric("Tracking error",
                f"{perf.tracking_error_annual*100:.2f}%" if perf.tracking_error_annual is not None else "n/a",
                "vs SPY, annual")
    c[3].metric("Win rate",
                f"{perf.win_rate*100:.1f}%" if perf.win_rate is not None else "n/a",
                f"profit factor {perf.profit_factor:.2f}" if perf.profit_factor is not None else None)

    # Drawdown context
    if perf.drawdown_now is not None and perf.drawdown_now < -0.001:
        st.warning(f"⚠️ Currently in drawdown of **{perf.drawdown_now*100:.2f}%** "
                   f"({perf.days_in_drawdown} days). Worst this window: **{perf.max_drawdown*100:.2f}%**.")

    st.divider()

    # ---- EQUITY + SPY OVERLAY (existing chart) ----
    st.subheader("Equity vs SPY")
    snaps = _cached_snapshots(str(DB_PATH))
    if not snaps.empty and len(snaps) >= 2:
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
                height=400, hovermode="x unified",
                yaxis=dict(title="equity ($)", side="left"),
                yaxis2=dict(title="drawdown (%)", side="right", overlaying="y",
                             showgrid=False, range=[-50, 5]),
                margin=dict(t=20, l=10, r=10, b=10),
                legend=dict(orientation="h", yanchor="bottom", y=1.02))
            st.plotly_chart(fig, use_container_width=True)
        except ImportError:
            st.line_chart(chart_data["equity"])

    # ---- v3.58.3: LowVolSleeve shadow overlay ----
    try:
        lowvol_csv = ROOT / "data" / "low_vol_shadow.csv"
        if lowvol_csv.exists():
            lv = pd.read_csv(lowvol_csv)
            if not lv.empty and "cum_equity" in lv.columns and len(lv) >= 5:
                st.subheader("📊 LowVolSleeve shadow vs LIVE momentum")
                st.caption(
                    "Daily shadow run of the second sleeve (lowest-vol top-15 from "
                    "the LIVE universe). Both curves normalized to 1.0 at the start "
                    "of the LowVol shadow window. **If LowVol consistently beats or "
                    "diversifies LIVE, that's the case for promoting it.**"
                )
                lv["date"] = pd.to_datetime(lv["date"])
                lv = lv.sort_values("date").set_index("date")
                # Re-normalize LIVE equity to start at the same point as LowVol
                if not snaps.empty:
                    eq_live = snaps[["date", "equity"]].copy()
                    eq_live["date"] = pd.to_datetime(eq_live["date"])
                    eq_live = eq_live.sort_values("date").set_index("date")
                    overlap_start = max(lv.index.min(), eq_live.index.min())
                    eq_live_clip = eq_live.loc[eq_live.index >= overlap_start]
                    if len(eq_live_clip) > 0:
                        eq_live_norm = eq_live_clip["equity"] / eq_live_clip["equity"].iloc[0]
                    else:
                        eq_live_norm = None
                else:
                    eq_live_norm = None
                lv_norm = lv["cum_equity"] / lv["cum_equity"].iloc[0]
                try:
                    import plotly.graph_objects as go
                    fig = go.Figure()
                    if eq_live_norm is not None:
                        fig.add_trace(go.Scatter(
                            x=eq_live_norm.index, y=eq_live_norm.values,
                            name="LIVE momentum",
                            line=dict(color="#16a34a", width=2),
                        ))
                    fig.add_trace(go.Scatter(
                        x=lv_norm.index, y=lv_norm.values,
                        name="LowVolSleeve (SHADOW)",
                        line=dict(color="#3b82f6", width=2, dash="dot"),
                    ))
                    fig.update_layout(
                        height=320, hovermode="x unified",
                        yaxis=dict(title="normalized equity"),
                        margin=dict(t=20, l=10, r=10, b=10),
                        legend=dict(orientation="h", yanchor="bottom", y=1.02),
                    )
                    st.plotly_chart(fig, use_container_width=True)
                    # Stats
                    sc = st.columns(3)
                    sc[0].metric("LowVol total return",
                                 f"{(lv_norm.iloc[-1] - 1)*100:+.2f}%",
                                 f"over {len(lv_norm)} days")
                    if eq_live_norm is not None and len(eq_live_norm) > 0:
                        sc[1].metric("LIVE total return",
                                     f"{(eq_live_norm.iloc[-1] - 1)*100:+.2f}%",
                                     f"same window")
                        # Correlation of daily returns
                        try:
                            lv_daily = lv["day_return"].astype(float).reset_index()
                            eq_daily = eq_live_clip["equity"].pct_change().dropna().reset_index()
                            merged = lv_daily.merge(eq_daily, on="date", how="inner")
                            if len(merged) >= 10:
                                corr = merged["day_return"].corr(merged["equity"])
                                sc[2].metric(
                                    "Correlation (daily)",
                                    f"{corr:.2f}",
                                    "below 0.5 → diversifying" if corr < 0.5 else "high — overlapping",
                                )
                        except Exception:
                            pass
                except ImportError:
                    st.line_chart(lv_norm)
            else:
                st.caption("_LowVol shadow CSV exists but has < 5 rows yet — run the prewarm a few more days_")
    except Exception as e:
        st.caption(f"_LowVol overlay failed: {type(e).__name__}: {e}_")

    # ---- ROLLING SHARPE ----
    st.subheader("Rolling 30-day Sharpe")
    rs = _cached_rolling_sharpe(window=30, days=window)
    if not rs.empty:
        try:
            import plotly.graph_objects as go
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(x=rs["date"], y=rs["rolling_sharpe"],
                                       name="rolling Sharpe",
                                       line=dict(color="#3b82f6", width=2),
                                       fill="tozeroy",
                                       fillcolor="rgba(59,130,246,0.1)"))
            fig2.add_hline(y=1.0, line_dash="dash", line_color="#16a34a",
                            annotation_text="good (1.0)")
            fig2.add_hline(y=0.0, line_dash="dot", line_color="#888")
            fig2.update_layout(height=300, margin=dict(t=20, l=10, r=10, b=10),
                                yaxis=dict(title="annualized Sharpe"))
            st.plotly_chart(fig2, use_container_width=True)
        except ImportError:
            st.line_chart(rs.set_index("date")["rolling_sharpe"])
    else:
        st.caption(f"_need ≥30 days for rolling Sharpe; have {perf.n_obs}_")

    # ---- DRAWDOWN PERIODS ----
    dd_periods = _cached_drawdown_periods(days=window)
    if dd_periods:
        st.subheader(f"Drawdown periods ({len(dd_periods)})")
        rows = []
        for p in dd_periods[:10]:
            rows.append({
                "peak": p.get("peak_date"),
                "trough": p.get("trough_date"),
                "recovery": p.get("recovery_date") or "ongoing",
                "max_dd": f"{p.get('trough_dd', 0)*100:+.2f}%",
                "days": p.get("days_in_dd", 0),
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)

    # ---- MONTHLY RETURNS HEATMAP ----
    monthly = _cached_monthly_returns(days=365)
    if not monthly.empty and len(monthly) >= 3:
        st.subheader("Monthly returns")
        try:
            import plotly.graph_objects as go
            pivot = monthly.pivot(index="year", columns="month", values="return_pct")
            fig3 = go.Figure(data=go.Heatmap(
                z=pivot.values, x=pivot.columns, y=pivot.index,
                colorscale="RdYlGn", zmid=0,
                text=pivot.values, texttemplate="%{text:+.2f}%",
                colorbar=dict(title="%")))
            fig3.update_layout(height=200, margin=dict(t=20, l=10, r=10, b=10),
                                xaxis=dict(title="month"), yaxis=dict(title="year"))
            st.plotly_chart(fig3, use_container_width=True)
        except ImportError:
            st.dataframe(monthly, use_container_width=True, hide_index=True)

    # ---- HOW TO READ ----
    with st.expander("📚 How to read this — what each metric means"):
        st.markdown("""
- **Sharpe ratio**: return per unit of risk. Above **1.0 is good**, above **2.0 is exceptional** for retail. Our PIT-honest target is ~0.96.
- **Sortino**: like Sharpe but only counts downside vol. Higher than Sharpe = your losses are less violent than your wins.
- **Calmar**: CAGR / |max DD|. Measures "how much pain for the gain." >1.0 means you grow faster than your worst drawdown.
- **Information ratio**: skill-adjusted excess return vs SPY. >0.5 is solid; >1.0 is rare.
- **Beta vs SPY**: how much you move with the market. 1.0 = same as SPY. Our momentum strategy typically runs 1.1-1.3.
- **Alpha (Jensen, annualized)**: return AFTER subtracting market beta exposure. The pure skill component. Annualized.
- **Tracking error**: how different your day-to-day returns are from SPY's. Higher = bigger active risk.
- **Win rate**: % of days that closed positive.
- **Profit factor**: $ won / $ lost. >1.5 is good, >2.0 is great.
- **Max drawdown**: worst peak-to-trough loss in the window. Our backtest worst was -33%.

**The trade-off you care about most:** Sharpe says "is this good risk-adjusted?" Calmar says "is this good drawdown-adjusted?" If both are >1.0, you're winning the battle.
""")


# ============================================================
# View: Attribution (per-position waterfall + Brinson + sector pie)
# ============================================================
@st.cache_data(ttl=60, show_spinner="📊 Computing attribution...")
def _cached_brinson():
    """Brinson decomposition with current live data + SPDR sector ETF benchmark."""
    from trader.brinson_attribution import compute_brinson, SECTOR_ETF_MAP
    live = _live_portfolio()
    if not live.positions:
        return None, None
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
    return compute_brinson(sec_w_p, sec_r_p, sec_w_b, sec_r_b), live.positions


def view_attribution():
    st.title("📊 Attribution — where today's P&L came from")
    st.caption("Three layers: per-name contribution waterfall (which holdings moved you), "
               "sector tilt vs SPY (Brinson), and sector allocation pie. "
               "All cached 5 min.")

    # ---- 1. Per-position contribution waterfall (the headline) ----
    live = _live_portfolio()
    if getattr(live, "error", None) or not live.positions:
        st.warning("Cannot compute attribution — no live positions or broker fetch failed.")
        return

    from trader.analytics import position_contribution
    contrib = position_contribution(live.positions)

    if contrib:
        st.subheader("Per-position contribution to today's P&L")
        c = st.columns(4)
        total_contrib = sum(r["contribution_pct"] for r in contrib)
        positive = sum(r["contribution_pct"] for r in contrib if r["contribution_pct"] > 0)
        negative = sum(r["contribution_pct"] for r in contrib if r["contribution_pct"] < 0)
        c[0].metric("Total today", f"{total_contrib:+.3f}%",
                    "sum of (weight × day return) per name")
        c[1].metric("Positive contributors", f"{positive:+.3f}%",
                    f"{sum(1 for r in contrib if r['contribution_pct']>0)} names")
        c[2].metric("Negative contributors", f"{negative:+.3f}%",
                    f"{sum(1 for r in contrib if r['contribution_pct']<0)} names")
        if abs(total_contrib) > 0.001:
            ratio = positive / abs(negative) if negative < 0 else float("inf")
            c[3].metric("Win/loss ratio", f"{ratio:.2f}" if ratio != float("inf") else "∞",
                        "today's $ won / $ lost")

        # Waterfall chart
        try:
            import plotly.graph_objects as go
            fig = go.Figure(go.Waterfall(
                orientation="v",
                x=[r["symbol"] for r in contrib],
                y=[r["contribution_pct"] for r in contrib],
                text=[f"{r['contribution_pct']:+.3f}%" for r in contrib],
                connector={"line": {"color": "#666"}},
                increasing={"marker": {"color": "#16a34a"}},
                decreasing={"marker": {"color": "#dc2626"}},
            ))
            fig.update_layout(height=320, margin=dict(t=20, l=10, r=10, b=10),
                               yaxis=dict(title="contribution to portfolio % today"))
            st.plotly_chart(fig, use_container_width=True)
        except ImportError:
            pass

        # Top contributors / detractors tables
        cl, cr = st.columns(2)
        with cl:
            st.markdown("**🟢 Top contributors**")
            top5 = [r for r in contrib if r["contribution_pct"] > 0][:5]
            if top5:
                st.dataframe([{
                    "symbol": r["symbol"],
                    "weight": f"{r['weight_pct']:.1f}%",
                    "day": f"{r['day_pl_pct']:+.2f}%",
                    "contrib": f"{r['contribution_pct']:+.3f}%",
                } for r in top5], use_container_width=True, hide_index=True)
            else:
                st.caption("_no positive contributors today_")
        with cr:
            st.markdown("**🔴 Top detractors**")
            bot5 = [r for r in contrib if r["contribution_pct"] < 0][-5:]
            if bot5:
                st.dataframe([{
                    "symbol": r["symbol"],
                    "weight": f"{r['weight_pct']:.1f}%",
                    "day": f"{r['day_pl_pct']:+.2f}%",
                    "contrib": f"{r['contribution_pct']:+.3f}%",
                } for r in bot5], use_container_width=True, hide_index=True)
            else:
                st.caption("_no negative contributors today_")

    st.divider()

    # ---- 2. Brinson sector decomposition ----
    st.subheader("Sector-level Brinson decomposition")
    try:
        rep, _ = _cached_brinson()
        if rep is None:
            st.info("_no Brinson — broker fetch failed_")
        else:
            cm = st.columns(3)
            cm[0].metric("Allocation effect", f"{rep.sum_allocation*100:+.3f}%",
                          "from over/underweighting sectors")
            cm[1].metric("Selection effect", f"{rep.sum_selection*100:+.3f}%",
                          "from picking outperformers in-sector")
            cm[2].metric("Active vs benchmark", f"{rep.active_return*100:+.3f}%",
                          "alloc + selection + interaction")
            sector_rows = [{
                "sector": s.sector,
                "port_w": f"{s.portfolio_weight*100:.1f}%",
                "bench_w": f"{s.benchmark_weight*100:.1f}%",
                "port_ret": f"{s.portfolio_sector_return*100:+.2f}%",
                "bench_ret": f"{s.benchmark_sector_return*100:+.2f}%",
                "alloc_eff": f"{s.allocation_effect*100:+.3f}%",
                "select_eff": f"{s.selection_effect*100:+.3f}%",
            } for s in rep.by_sector]
            st.dataframe(sector_rows, use_container_width=True, hide_index=True)
    except Exception as e:
        st.caption(f"_Brinson failed: {type(e).__name__}_")

    st.divider()

    # ---- 3. Sector allocation pie ----
    st.subheader("Sector allocation")
    sector_w: dict = {}
    for p in live.positions:
        sec = p.sector or "Unknown"
        sector_w[sec] = sector_w.get(sec, 0) + (p.weight_of_book or 0)
    if sector_w:
        try:
            import plotly.graph_objects as go
            secs = list(sector_w.keys())
            vals = [sector_w[s] * 100 for s in secs]
            fig_pie = go.Figure(data=[go.Pie(labels=secs, values=vals, hole=0.4,
                                               textinfo="label+percent")])
            fig_pie.update_layout(height=300, margin=dict(t=20, l=10, r=10, b=10))
            st.plotly_chart(fig_pie, use_container_width=True)
        except ImportError:
            st.dataframe([{"sector": s, "weight": f"{sector_w[s]*100:.1f}%"}
                           for s in sorted(sector_w, key=sector_w.get, reverse=True)],
                          use_container_width=True, hide_index=True)

    # ---- HOW TO READ ----
    with st.expander("📚 How to read this — interpreting the numbers"):
        st.markdown("""
**Per-position contribution** = position weight × day return. Tells you *how many basis points* each name added/subtracted from your day. A 10% NVDA position up 2% adds **+0.20%** to your portfolio.

**Allocation effect**: how much of today's active return came from being **overweight or underweight** a sector. If you have 40% in Tech and Tech ETF (XLK) was +1%, your tech tilt contributed +1% × (your weight − benchmark weight).

**Selection effect**: how much came from **stock picking within sectors**. If your tech holdings returned +2% while the tech ETF returned +1%, you outperformed via name selection.

**Reading the trade-off:**
- High allocation effect + low selection = **factor exposure is winning** (you're betting on the right sector)
- Low allocation + high selection = **stock picking is winning** (you're picking outperformers)
- Both negative = consider whether your strategy needs adjustment, OR you're just having a normal bad day

**Caveat:** benchmark sector weights are currently equal-weight placeholder. Real SPY sector weights would tighten the "active return" signal — todo for v3.57.1.
""")


# ============================================================
# View: Events (with portfolio exposure column + filter)
# ============================================================
@st.cache_data(ttl=900, show_spinner="📅 Fetching event calendar (yfinance per-symbol)...")
def _cached_events(symbols: tuple, days_ahead: int):
    """Cached at 15min. Symbols passed as tuple for hashability."""
    from trader.events_calendar import compute_upcoming_events
    return compute_upcoming_events(list(symbols), days_ahead=days_ahead)


def view_events():
    st.title("📅 Events — what could move your book")
    st.caption("Calendar of FOMC + OPEX + earnings + ex-div for held names, next 30 days. "
               "Each event tagged with portfolio % exposure (for per-name events) so you "
               "know which dates actually matter for YOUR positions.")

    live = _live_portfolio()
    if getattr(live, "error", None):
        st.warning(f"broker fetch failed: {live.error}")
        return
    symbols = tuple(p.symbol for p in (live.positions or []))
    if not symbols:
        st.info("_no positions to compute exposure_")

    days_ahead = st.slider("Lookahead (days)", 7, 60, 30)
    events = _cached_events(symbols, days_ahead)

    if not events:
        st.info(f"_no upcoming events in next {days_ahead} days_")
        return

    # Compute exposure
    from trader.analytics import event_exposure
    rows = event_exposure(events, live.positions)

    # ---- Filter chips ----
    type_options = ["all", "fomc", "earnings", "ex_div", "opex"]
    sel_type = st.radio("Filter", type_options, horizontal=True, label_visibility="collapsed")
    if sel_type != "all":
        rows = [r for r in rows if sel_type in r["type"]]

    # ---- Headline: this-week alert ----
    this_week = [r for r in rows if (r.get("days_until") or 99) <= 7]
    high_exposure = [r for r in this_week if r.get("exposure_pct", 0) > 5]

    c = st.columns(4)
    c[0].metric("Events in window", len(rows))
    c[1].metric("This week", len(this_week))
    c[2].metric("High-exposure (>5%)", len(high_exposure))
    earnings_rows = [r for r in rows if "earnings" in r["type"]]
    if earnings_rows:
        max_exp = max(r.get("exposure_pct", 0) for r in earnings_rows)
        max_exp_sym = next(r["symbol"] for r in earnings_rows
                            if r.get("exposure_pct", 0) == max_exp)
        c[3].metric("Biggest earnings exposure",
                    f"{max_exp_sym} {max_exp:.1f}%",
                    "single name's earnings")
    else:
        c[3].metric("Biggest earnings exposure", "—", "no earnings in window")

    st.divider()

    # ---- Calendar table ----
    display_rows = []
    for r in rows:
        emoji = {"earnings": "📊", "ex_div": "💵", "fomc": "🏦", "opex": "🎯"}.get(r["type"], "📌")
        days = r.get("days_until", "?")
        warn = "⚠️ " if r.get("exposure_pct", 0) > 5 else ""
        display_rows.append({
            "date": r["date"],
            "in days": days,
            "type": f"{emoji} {r['type']}",
            "symbol": r["symbol"],
            "exposure": f"{warn}{r['exposure_pct']:.1f}%" if r["exposure_pct"] else "—",
            "note": r["note"],
        })
    st.dataframe(display_rows, use_container_width=True, hide_index=True)

    # ---- Per-event highlight cards for the most impactful ----
    if high_exposure:
        st.subheader("⚠️ High-exposure events this week")
        for r in high_exposure[:3]:
            with st.container(border=True):
                st.markdown(f"**{r['symbol']}** {r['type']} on **{r['date']}** "
                            f"({r['days_until']} days) — **{r['exposure_pct']:.1f}%** of book")
                st.caption(r['note'])

    # ---- HOW TO READ ----
    with st.expander("📚 How to read this — what each event means for your portfolio"):
        st.markdown("""
- **🏦 FOMC**: Federal Reserve rate decision. Whole portfolio reacts to surprise hawkish/dovish stance. Pre-FOMC drift is one of our shadow-tested anomalies. Implied vol typically rises into the meeting and crushes after.

- **🎯 OPEX**: monthly options expiration (third Friday). Heavy hedging-flow days. SPY tends to be range-bound that morning, can break out after 4 PM. Rarely triggers a rebalance for monthly strategies but can spike vol.

- **📊 Earnings**: per-name event. **Look at the exposure column.** A 1% position in NVDA earnings is rounding error; a 10% position is a real binary risk. The momentum strategy doesn't trade earnings directly — but post-earnings drift (PEAD) is on our V4 sleeve roadmap (`docs/V4_PARADIGM_SHIFT.md`).

- **💵 Ex-div**: ex-dividend date. Stock drops by ~the dividend amount that morning. Affects total-return tracking. Not actionable for our strategy.

**The single thing to watch:** which events have **>5% portfolio exposure**. Those are the events that move your book by >0.5% on a 10% earnings surprise. Anything <2% exposure isn't worth your attention.

**Caveat:** earnings dates from yfinance can be stale or wrong by a day. Confirm critical dates via the company's IR page before acting.
""")


# ============================================================
# v3.62.2: cached SPY history for crash-detector / regime views.
# yfinance hits the network; cache 1h. Used by view_pnl_readiness +
# any other view that wants long SPY return history.
# ============================================================
@st.cache_data(ttl=3600, show_spinner=False)
def _cached_spy_returns(days: int = 900) -> list[float]:
    """Returns list of SPY daily returns over the last `days`.
    Empty list on network failure."""
    try:
        import yfinance as yf
        from datetime import timedelta as _td, datetime as _dt
        df = yf.download("SPY", start=(_dt.utcnow().date() - _td(days=days)).isoformat(),
                          end=_dt.utcnow().date().isoformat(),
                          progress=False, auto_adjust=True)
        if df is None or df.empty:
            return []
        closes = df["Close"].dropna()
        if hasattr(closes, "columns"):
            closes = closes.iloc[:, 0]
        rets = []
        for i in range(1, len(closes)):
            try:
                p = float(closes.iloc[i - 1])
                c = float(closes.iloc[i])
            except (TypeError, ValueError):
                continue
            if p > 0:
                rets.append((c / p) - 1)
        return rets
    except Exception:
        return []


# ============================================================
# View: Regime overlay (with historical context + per-regime stats)
# ============================================================
@st.cache_data(ttl=3600, show_spinner=False)
def _cached_regime_history():
    from trader.analytics import regime_history_summary
    return regime_history_summary()


def view_intraday():
    st.title("⚡ Intraday risk — what's the worst that could happen today?")
    st.caption("Forward-looking risk metrics: VaR, CVaR, position concentration, "
               "stress scenarios. Plus the intraday-watch log of monitor events.")

    risk, live = _cached_risk_metrics()

    # ---- HEADLINE: TODAY'S RISK ----
    if risk is None or live is None:
        st.warning("Cannot compute risk — broker fetch failed or no positions.")
    else:
        eq = live.equity or 0
        st.subheader("Today's risk envelope")
        c = st.columns(4)
        c[0].metric("Equity at risk", f"${eq:,.0f}")
        c[1].metric("1-day VaR (95%)",
                    f"${risk.var_95_parametric:,.0f}" if risk.var_95_parametric else "n/a",
                    f"{risk.var_95_parametric/eq*100:.2f}% of book" if (risk.var_95_parametric and eq) else None)
        c[2].metric("1-day VaR (99%)",
                    f"${risk.var_99_parametric:,.0f}" if risk.var_99_parametric else "n/a",
                    f"{risk.var_99_parametric/eq*100:.2f}% of book" if (risk.var_99_parametric and eq) else None)
        c[3].metric("Expected shortfall (95%)",
                    f"${risk.cvar_95:,.0f}" if risk.cvar_95 else "n/a",
                    "avg loss in worst 5%")

        st.divider()

        # ---- CONCENTRATION ----
        st.subheader("Concentration")
        c = st.columns(4)
        c[0].metric("HHI", f"{risk.concentration_hhi:.4f}" if risk.concentration_hhi else "n/a",
                    "0=infinite diversification, 1=single name")
        c[1].metric("Top-5 weight",
                    f"{risk.top_5_weight*100:.1f}%" if risk.top_5_weight else "n/a",
                    "5 biggest positions combined")
        c[2].metric("Largest position",
                    f"{risk.largest_position_symbol} {risk.largest_position_pct*100:.1f}%" if risk.largest_position_symbol else "n/a",
                    "single-name limit: 16%")
        c[3].metric("Largest sector",
                    f"{risk.sector_max_name} {risk.sector_max_weight*100:.1f}%" if risk.sector_max_name else "n/a",
                    "sector limit: 35%")

        # Concentration warnings
        if risk.largest_position_pct and risk.largest_position_pct > 0.16:
            st.error(f"⚠️ {risk.largest_position_symbol} is {risk.largest_position_pct*100:.1f}% — over 16% single-name cap")
        if risk.sector_max_weight and risk.sector_max_weight > 0.35:
            st.error(f"⚠️ {risk.sector_max_name} sector is {risk.sector_max_weight*100:.1f}% — over 35% cap")
        if risk.top_5_weight and risk.top_5_weight > 0.60:
            st.warning(f"⚠️ Top-5 names = {risk.top_5_weight*100:.1f}% of book — high concentration")

        st.divider()

        # ---- STRESS SCENARIOS ----
        st.subheader("Stress scenarios (assumes portfolio beta-scales with SPY)")
        c = st.columns(3)
        c[0].metric("If SPY −5% tomorrow",
                    f"${risk.stress_spy_minus_5:,.0f}" if risk.stress_spy_minus_5 else "n/a",
                    f"{risk.stress_spy_minus_5/eq*100:.2f}% of book" if (risk.stress_spy_minus_5 and eq) else None)
        c[1].metric("If SPY −10%",
                    f"${risk.stress_spy_minus_10:,.0f}" if risk.stress_spy_minus_10 else "n/a",
                    f"{risk.stress_spy_minus_10/eq*100:.2f}% of book" if (risk.stress_spy_minus_10 and eq) else None)
        c[2].metric("If SPY −20% (crisis)",
                    f"${risk.stress_spy_minus_20:,.0f}" if risk.stress_spy_minus_20 else "n/a",
                    f"{risk.stress_spy_minus_20/eq*100:.2f}% of book" if (risk.stress_spy_minus_20 and eq) else None)

    st.divider()

    # ---- INTRADAY MONITOR LOG ----
    st.subheader("Intraday monitor log (every 30 min during market hours)")
    intraday = read_state_file(str(ROOT / "data" / "intraday_risk_log.json"))
    if isinstance(intraday, list) and intraday:
        recent = [e for e in intraday[-50:] if e.get("equity_now")]
        if recent:
            actions = pd.Series([e.get("action", "ok") for e in recent]).value_counts()
            ac = st.columns(min(len(actions), 4))
            for i, (action, count) in enumerate(actions.items()):
                ac[i % len(ac)].metric(action, int(count))
        df = pd.DataFrame(intraday[-200:]).iloc[::-1].reset_index(drop=True)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.caption("_no intraday log yet — workflow runs every 30 min during market hours_")

    # ---- HOW TO READ ----
    with st.expander("📚 How to read this — interpreting risk numbers"):
        st.markdown("""
**VaR (Value at Risk)**: "with 95% confidence, you won't lose more than $X tomorrow." Parametric — assumes returns are normally distributed (which they aren't, but it's a useful first approximation). Computed as: equity × daily_vol × 1.645 (for 95%).

**CVaR / Expected Shortfall**: "if you DO have a 95th-percentile bad day, the average loss is $Y." Always larger than VaR. This is the metric professional risk managers actually use because it captures tail behavior better.

**HHI (Herfindahl index)**: sum of squared weights. 1.0 = single name, ~0.067 = 15 equally-weighted names. Below 0.10 = well-diversified, above 0.20 = concentrated. Our cap-heavy momentum book typically runs ~0.08-0.12.

**Stress scenarios**: linear beta-scaling. If SPY −10% and your beta is 1.2, you'd lose ~12% of book. **This is a floor estimate** — in real crashes, beta tends to spike (correlations go to 1) and momentum names get hit harder.

**The decisions this informs:**
- **VaR/CVaR**: am I sized for what I can stomach? If 95% VaR > 5% of equity, you might be overlevered.
- **Concentration**: is one name capable of nuking my book? If largest position × max-realistic-loss > 5% of equity, you have name-specific risk.
- **Stress scenarios**: in a real correction, what's my expected drawdown? Compare to your behavioral pre-commit threshold (usually 25-33%).

**The escalation ladder (already wired into v3.46 risk_manager):**
- Day P&L < −6% → **48h FREEZE** (no new positions)
- Equity vs deployment anchor < −25% → **30-day FREEZE**
- Equity vs deployment anchor < −33% → **LIQUIDATION GATE** (requires written post-mortem to clear)

This view shows where we are relative to those thresholds.
""")


# ============================================================
# View: Shadow variants
# ============================================================
def view_sleeve_health():
    st.title("🩺 Sleeve health (correlation)")
    st.caption("Cross-sleeve correlation + per-sleeve rolling Sharpe + "
               "auto-demote recommendations. **Operational** health of "
               "LIVE sleeves — NOT a backtest. See [GLOSSARY.md → Sleeve "
               "health](../docs/GLOSSARY.md).")
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

    st.divider()

    # v3.57.1: Density mode + Copilot memory + workflow editor
    st.subheader("🎨 Density mode")
    st.caption("Compact = Bloomberg-style tighter padding + smaller fonts. "
               "Comfortable = current default.")
    if "density_mode" not in st.session_state:
        st.session_state.density_mode = "comfortable"
    new_density = st.radio("Density",
                            ["comfortable", "compact"],
                            index=0 if st.session_state.density_mode == "comfortable" else 1,
                            horizontal=True, label_visibility="collapsed")
    if new_density != st.session_state.density_mode:
        st.session_state.density_mode = new_density
        st.rerun()

    st.divider()

    st.subheader("🧠 Copilot memory")
    st.caption("Long-form preferences loaded into every Copilot system prompt. "
               "Edit freely. Saved to data/copilot_memory.md.")
    try:
        from trader.copilot_memory import read_memory, write_memory, reset_memory_to_default
        current_memory = read_memory()
        edited = st.text_area("Memory (Markdown)", value=current_memory, height=300,
                                key="memory_editor")
        c1, c2 = st.columns(2)
        if c1.button("💾 Save memory"):
            if write_memory(edited):
                st.success("memory saved — next Copilot turn will load it")
            else:
                st.error("save failed")
        if c2.button("🔄 Reset to default"):
            reset_memory_to_default()
            st.success("memory reset to default")
            st.rerun()
    except Exception as e:
        st.warning(f"memory editor unavailable: {type(e).__name__}: {e}")

    st.divider()

    st.subheader("⚡ Saved workflows")
    st.caption("Named multi-prompt sequences for one-click invocation in Chat. "
               "Saved to data/copilot_workflows.json.")
    try:
        from trader.copilot_memory import list_workflows, save_workflows, delete_workflow
        workflows = list_workflows()
        for i, wf in enumerate(workflows):
            with st.expander(f"{wf.get('name', '(unnamed)')}"):
                st.text_area(f"Prompts (one per line)",
                              value="\n".join(wf.get("prompts", [])),
                              height=80, key=f"wf_prompts_{i}",
                              disabled=True)
                if st.button("🗑️ Delete", key=f"wf_delete_{i}"):
                    delete_workflow(wf["name"])
                    st.rerun()

        with st.expander("➕ Add new workflow"):
            new_name = st.text_input("Workflow name", key="new_wf_name")
            new_prompts = st.text_area("Prompts (one per line)", key="new_wf_prompts",
                                         height=100)
            if st.button("Save workflow"):
                prompts_list = [p.strip() for p in new_prompts.split("\n") if p.strip()]
                if new_name and prompts_list:
                    workflows.append({"name": new_name, "prompts": prompts_list})
                    if save_workflows(workflows):
                        st.success(f"saved workflow '{new_name}'")
                        st.rerun()
    except Exception as e:
        st.warning(f"workflows unavailable: {type(e).__name__}: {e}")

    st.divider()

    st.subheader("System info")
    st.json({
        "version": "v3.57.1",
        "journal_path": st.session_state.db_path,
        "data_dir": str(ROOT / "data"),
        "reports_dir": str(ROOT / "data" / "reports"),
        "memory_file": str(ROOT / "data" / "copilot_memory.md"),
        "workflows_file": str(ROOT / "data" / "copilot_workflows.json"),
        "reference_docs": [
            "docs/AI_NATIVE_REFACTOR_DESIGN.md",
            "docs/V4_PARADIGM_SHIFT.md",
            "docs/SWARM_VERIFICATION_PROTOCOL.md",
            "docs/CRITIQUE.md",
            "docs/BEHAVIORAL_PRECOMMIT.md",
        ],
    }, expanded=False)


# ============================================================
# View: Grid (Hebbia Matrix-style multi-asset query)  — v3.57.1 (Phase 5)
# ============================================================
def _grid_default_questions() -> list[str]:
    return [
        "day_pnl_pct",
        "total_unrealized_pnl_pct",
        "weight_pct",
        "sector",
    ]


def _grid_value_for(symbol: str, question: str, ports: dict) -> str:
    """Look up one cell value from the live portfolio. Heuristic mapping —
    the columns the user picks must match keys in the position dict, otherwise
    we mark the cell with a '?' so they know it didn't resolve."""
    pos_list = ports.get("positions", []) or []
    pos = next((p for p in pos_list if str(p.get("symbol", "")).upper() == symbol.upper()), None)
    if not pos:
        return "—"
    val = pos.get(question)
    if val is None:
        return "?"
    if isinstance(val, float):
        return f"{val:.2f}"
    return str(val)


def view_alerts():
    st.title("🔔 Alerts — every state change in chronological order")
    st.caption(
        "Single feed of every meaningful event the system has emitted: "
        "freezes, breaker checks, kill-switch trips, earnings trims, "
        "halts, slippage outliers. Replaces hunting through stdout + Slack."
    )

    # v3.63.0: earnings calendar source status — fixes the v3.58.1 INERT bug
    with st.expander("📅 Earnings calendar sources", expanded=False):
        try:
            from trader.earnings_calendar import status as _earnings_status
            es = _earnings_status()
            cc = st.columns(4)
            cc[0].metric("Polygon",
                          "✅ wired" if es["polygon_configured"] else "⚪ not set",
                          help="Set POLYGON_API_KEY env var")
            cc[1].metric("Finnhub",
                          "✅ wired" if es["finnhub_configured"] else "⚪ not set",
                          help="Set FINNHUB_API_KEY env var")
            cc[2].metric("Alpha Vantage",
                          "✅ wired" if es["alpha_vantage_configured"] else "⚪ not set",
                          help="Set ALPHA_VANTAGE_KEY env var")
            cc[3].metric("Cached entries", es["cache_entries"],
                          help=f"Cached at {es['cache_file']}")
            if not es["any_paid_source_configured"]:
                st.warning(
                    "⚠️ No paid earnings source configured. Falling back to "
                    "yfinance which silently returns empty for major tickers — "
                    "this means **EarningsRule LIVE has been DOING NOTHING**. "
                    "Set any one of POLYGON_API_KEY / FINNHUB_API_KEY / "
                    "ALPHA_VANTAGE_KEY to fix."
                )
            else:
                st.success("✅ At least one reliable earnings source configured. "
                           "EarningsRule LIVE will actually trim positions.")
        except Exception as e:
            st.caption(f"_earnings calendar status: {e}_")

    # v3.62.1: notification setup status — answers "do I get emails?"
    # v3.64.0: fixed the SMTP_USER+PASS check (was looking at SMTP_HOST
    # which has a default value)
    with st.expander("📬 How notifications work", expanded=False):
        import os as _os
        slack = _os.getenv("SLACK_WEBHOOK", "")
        # Email IS wired in trader.notify since v2.0 — just needs SMTP_USER+PASS
        email_smtp = _os.getenv("SMTP_USER", "") and _os.getenv("SMTP_PASS", "")
        ntfy = _os.getenv("NTFY_TOPIC", "")

        st.markdown("""
This page is an **in-dashboard log** — it shows everything that happened
since the system started, no notifications required.

For PUSH notifications (when something breaks while you're away), the
system can send to:
""")
        col1, col2, col3 = st.columns(3)
        with col1:
            if slack:
                st.success("✅ **Slack** — wired")
                st.caption("Set via `SLACK_WEBHOOK` env. "
                            "Used by `trader.notify.notify()`.")
            else:
                st.info("**Slack** — not wired")
                st.caption("Set `SLACK_WEBHOOK` env to enable.")
        with col2:
            if email_smtp:
                st.success("✅ **Email** — wired")
                st.caption(
                    f"Sends to `{_os.getenv('EMAIL_TO', '?')}` via "
                    f"`{_os.getenv('SMTP_HOST', 'smtp.gmail.com')}`. "
                    f"Used by `trader.notify._send_email()`."
                )
                # Test button so user can verify it actually works
                if st.button("✉️ Send test email", key="alerts_test_email"):
                    try:
                        from trader.notify import notify
                        notify("HANK test email — if you see this, email "
                                "alerts are working.", level="info")
                        st.success("Test email queued. Check your inbox.")
                    except Exception as e:
                        st.error(f"Send failed: {e}")
            else:
                st.warning("**Email** — credentials missing")
                st.caption(
                    "Adapter EXISTS (`trader.notify`) but needs "
                    "`SMTP_USER` + `SMTP_PASS` env vars. For Gmail: "
                    "[create app password](https://myaccount.google.com/"
                    "apppasswords). Defaults: `EMAIL_TO=" +
                    _os.getenv("EMAIL_TO", "richard.chen.1989@gmail.com") +
                    "`, host `smtp.gmail.com:587`."
                )
        with col3:
            if ntfy:
                st.success("✅ **ntfy.sh** — wired")
            else:
                st.info("**ntfy.sh** — not wired")
                st.caption(
                    "Free push-to-phone. Set `NTFY_TOPIC` env to "
                    "your unique topic (e.g. `trader-richard-7x9k`)."
                )

        st.markdown("""
**What triggers a notification:**

- 🔴 Kill-switch trip (catastrophic / data-quality / liquidation)
- 🔴 Drawdown circuit breaker fired
- 🔴 Daily-loss freeze triggered (-6% in a day)
- 🟡 Order errors / partial fills
- 🟡 Reconcile mismatches (expected vs actual positions)
- 🟢 Daily run completed (info-level)

**To get emails specifically:** the email adapter is the single
biggest UX gap. Until shipped, set `SLACK_WEBHOOK` for the same
real-time alerting via Slack DM/channel — that's the default
production path.
""")

    # Pull from the journal: runs (status), orders (errors), decisions
    # (final), postmortems, plus the v3.58 slippage_log we just started
    # writing.
    rows = []
    db = str(DB_PATH)

    # 1) Run-level events
    try:
        runs = query(db,
                     "SELECT run_id, started_at, completed_at, status, notes "
                     "FROM runs ORDER BY started_at DESC LIMIT 200")
        for _, r in runs.iterrows():
            sev = "info" if r.get("status") == "ok" else "warn"
            rows.append({
                "ts": r.get("started_at"), "type": "run", "severity": sev,
                "summary": f"run {r.get('run_id', '?')[:8]} → {r.get('status')}",
                "detail": str(r.get("notes") or "")[:300],
            })
    except Exception:
        pass

    # 2) Order errors / status anomalies
    try:
        ords = query(db,
                     "SELECT ts, ticker, side, notional, status, error "
                     "FROM orders WHERE status != 'submitted' "
                     "ORDER BY ts DESC LIMIT 200")
        for _, r in ords.iterrows():
            rows.append({
                "ts": r.get("ts"), "type": "order", "severity": "warn",
                "summary": f"{r.get('side')} {r.get('ticker')} → {r.get('status')}",
                "detail": str(r.get("error") or "")[:300],
            })
    except Exception:
        pass

    # 3) Postmortems
    try:
        pms = query(db,
                    "SELECT date, pnl_pct, summary, proposed_tweak "
                    "FROM postmortems ORDER BY date DESC LIMIT 50")
        for _, r in pms.iterrows():
            sev = "warn" if (r.get("pnl_pct") or 0) < -0.01 else "info"
            rows.append({
                "ts": r.get("date"), "type": "postmortem", "severity": sev,
                "summary": f"PM {r.get('date')}  P&L {(r.get('pnl_pct') or 0)*100:+.2f}%",
                "detail": str(r.get("summary") or "")[:300],
            })
    except Exception:
        pass

    # 4) v3.58 slippage outliers (>30bps)
    try:
        sl = query(db,
                   "SELECT ts, symbol, side, slippage_bps, notional, status "
                   "FROM slippage_log WHERE slippage_bps IS NOT NULL "
                   "AND ABS(slippage_bps) > 30 ORDER BY ts DESC LIMIT 100")
        for _, r in sl.iterrows():
            rows.append({
                "ts": r.get("ts"), "type": "slippage", "severity": "warn",
                "summary": (f"slippage outlier {r.get('symbol')} {r.get('side')} "
                            f"{r.get('slippage_bps'):.1f}bps"),
                "detail": f"notional ${r.get('notional', 0):,.0f}",
            })
    except Exception:
        pass

    # 5) Freeze state file (read direct — not in journal)
    try:
        freeze = _check_freeze_state()
        if freeze:
            for k, v in freeze.items():
                rows.append({
                    "ts": v if isinstance(v, str) else "",
                    "type": "freeze", "severity": "error",
                    "summary": f"FREEZE active: {k}",
                    "detail": f"until: {v}",
                })
    except Exception:
        pass

    if not rows:
        st.info("_no events yet — system is quiet_")
        return

    rows.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)

    # Filters
    fcols = st.columns([1, 1, 2])
    sev_pick = fcols[0].selectbox("Severity",
                                   ["all", "error", "warn", "info"], index=0)
    type_opts = ["all"] + sorted({r["type"] for r in rows})
    type_pick = fcols[1].selectbox("Type", type_opts, index=0)
    n = fcols[2].slider("Show last N", 10, 500, 100, step=10)

    filtered = [r for r in rows
                if (sev_pick == "all" or r["severity"] == sev_pick)
                and (type_pick == "all" or r["type"] == type_pick)][:n]

    # Counters
    cc = st.columns(4)
    cc[0].metric("Total events", len(rows))
    cc[1].metric("Errors", sum(1 for r in rows if r["severity"] == "error"))
    cc[2].metric("Warnings", sum(1 for r in rows if r["severity"] == "warn"))
    cc[3].metric("Showing", len(filtered))

    SEV_EMOJI = {"error": "🔴", "warn": "🟡", "info": "🟢"}
    for r in filtered:
        emoji = SEV_EMOJI.get(r["severity"], "⚪")
        with st.expander(f"{emoji} **{r['ts']}** · `{r['type']}` · {r['summary']}",
                         expanded=False):
            st.caption(r.get("detail") or "_no detail_")


def _check_freeze_state() -> dict:
    """Read the freeze state file used by risk_manager."""
    try:
        from trader.risk_manager import _load_freeze_state
        return _load_freeze_state() or {}
    except Exception:
        return {}


# ----- #2 Slippage execution dashboard -------------------------------------
def view_slippage():
    st.title("⚡ Slippage — execution quality dashboard")
    st.caption(
        "Every order writes a row to `slippage_log` (decision_mid + notional). "
        "Once reconcile fills in fill_price, slippage_bps is computed. "
        "30d rolling avg + per-symbol breakdown + worst fills surface here."
    )

    db = str(DB_PATH)
    try:
        df = query(db,
                   "SELECT ts, symbol, side, decision_mid, notional, "
                   "fill_price, slippage_bps, status "
                   "FROM slippage_log ORDER BY ts DESC LIMIT 1000")
    except Exception as e:
        st.warning(
            f"slippage_log table not yet populated. Will fill once you've "
            f"placed your first order under v3.58.1+. ({e})"
        )
        return

    if df.empty:
        st.info("_no slippage rows yet — first order under v3.58.1 will start the log_")
        return

    # Summary metrics
    closed = df.dropna(subset=["slippage_bps"])
    cc = st.columns(4)
    cc[0].metric("Total fills tracked", len(closed))
    cc[1].metric(
        "30d avg slippage",
        f"{closed.head(60)['slippage_bps'].mean():.1f} bps" if len(closed) else "n/a",
        "lower = better fills",
    )
    cc[2].metric(
        "Worst fill (30d)",
        f"{closed.head(60)['slippage_bps'].max():.1f} bps" if len(closed) else "n/a",
    )
    cc[3].metric(
        "Total notional traded",
        f"${df['notional'].sum():,.0f}",
    )

    if not closed.empty:
        # Trend chart
        try:
            import plotly.graph_objects as go
            closed_chart = closed.copy()
            closed_chart["ts"] = pd.to_datetime(closed_chart["ts"])
            closed_chart = closed_chart.sort_values("ts")
            closed_chart["rolling_30"] = closed_chart["slippage_bps"].rolling(30, min_periods=5).mean()
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=closed_chart["ts"], y=closed_chart["slippage_bps"],
                mode="markers", name="per-fill bps",
                marker=dict(size=5, opacity=0.5),
            ))
            fig.add_trace(go.Scatter(
                x=closed_chart["ts"], y=closed_chart["rolling_30"],
                mode="lines", name="rolling 30 fills",
                line=dict(width=2),
            ))
            fig.update_layout(
                height=320, hovermode="x unified",
                yaxis=dict(title="slippage (bps)"),
                xaxis=dict(title=""), showlegend=True,
            )
            st.plotly_chart(fig, use_container_width=True)
        except Exception:
            pass

    # Per-symbol breakdown
    st.subheader("Per-symbol slippage")
    if not closed.empty:
        per_sym = closed.groupby("symbol").agg(
            n_fills=("symbol", "count"),
            avg_bps=("slippage_bps", "mean"),
            worst_bps=("slippage_bps", "max"),
            total_notional=("notional", "sum"),
        ).reset_index().sort_values("avg_bps", ascending=False)
        st.dataframe(per_sym, use_container_width=True, hide_index=True)
    else:
        st.caption("_awaiting fills with reconciled prices_")

    # Recent fills
    st.subheader("Recent fills (raw)")
    st.dataframe(df.head(50), use_container_width=True, hide_index=True)


# ----- #1 Shadow signal panel ----------------------------------------------
def view_watchlist():
    st.title("👁️ Watchlist — what you're not holding but tracking")
    st.caption(
        "Bottom-15 momentum (your shorts if long/short went LIVE), next-strongest "
        "5 outside your top-15, and any user-pinned symbols. The 30 names "
        "around your book."
    )

    # Pinned symbols persist via session_state + a journal-side table
    # (lightweight: keep in session for now, persist via copilot_memory if needed)
    if "pinned_watch" not in st.session_state:
        st.session_state.pinned_watch = []

    # Add/remove pinned
    cols = st.columns([3, 1])
    add_sym = cols[0].text_input("📌 Pin a symbol",
                                  placeholder="AAPL",
                                  key="watch_pin_input").upper().strip()
    if cols[1].button("Pin", use_container_width=True):
        if add_sym and add_sym not in st.session_state.pinned_watch:
            st.session_state.pinned_watch.append(add_sym)
            st.rerun()

    if st.session_state.pinned_watch:
        st.caption("**Pinned:** " + " · ".join(
            f"`{s}`" for s in st.session_state.pinned_watch))
        if st.button(f"✖ Clear all pinned ({len(st.session_state.pinned_watch)})",
                     key="watch_clear_pinned"):
            st.session_state.pinned_watch = []
            st.rerun()

    st.divider()

    # Try to compute live ranking. Heavy operation — cached 10 min.
    with st.spinner("Ranking universe by momentum (cached 10 min)..."):
        ranked = _cached_full_ranking()

    if not ranked:
        st.warning("Could not rank universe — broker or yfinance unavailable.")
        return

    # Live held set
    try:
        from trader.copilot import dispatch_tool
        ports = dispatch_tool("get_portfolio_status", {})
        held = {p.get("symbol") for p in (ports.get("positions") or []) if p.get("symbol")}
    except Exception:
        held = set()

    # Identify cohorts
    top15 = ranked[:15]
    next5 = [r for r in ranked[15:30] if r["symbol"] not in held][:5]
    bottom15 = ranked[-15:]
    pinned = [r for r in ranked if r["symbol"] in st.session_state.pinned_watch]

    st.subheader(f"⭐ Top-15 momentum (your LIVE picks — {sum(1 for r in top15 if r['symbol'] in held)}/{len(top15)} held)")
    _render_watchlist_table(top15, held, st.session_state.pinned_watch)

    st.subheader("🔜 Next-5 outside top-15")
    if next5:
        _render_watchlist_table(next5, held, st.session_state.pinned_watch)
    else:
        st.caption("_no candidates_")

    st.subheader("⬇️ Bottom-15 momentum (would-be shorts)")
    _render_watchlist_table(bottom15, held, st.session_state.pinned_watch)

    if pinned:
        st.subheader("📌 User-pinned")
        _render_watchlist_table(pinned, held, st.session_state.pinned_watch)


@st.cache_data(ttl=600, show_spinner=False)
def _cached_full_ranking() -> list[dict]:
    """Rank the LIVE universe by momentum. Returns highest first."""
    try:
        from trader.universe import DEFAULT_LIQUID_50
        from trader.strategy import rank_momentum
        # Get a much larger top_n so we have a full ranked list
        candidates = rank_momentum(DEFAULT_LIQUID_50, top_n=len(DEFAULT_LIQUID_50))
        return [{"symbol": c.ticker,
                 "score": c.score,
                 "atr_pct": c.atr_pct,
                 "rationale": c.rationale}
                for c in candidates]
    except Exception:
        return []


def _render_watchlist_table(rows: list[dict], held: set, pinned: list[str]):
    if not rows:
        return
    out = []
    for r in rows:
        sym = r["symbol"]
        marks = []
        if sym in held: marks.append("✅")
        if sym in pinned: marks.append("📌")
        out.append({
            "symbol": sym,
            "marks": " ".join(marks),
            "momentum_score": f"{r['score']:.4f}",
            "ATR_%": f"{(r.get('atr_pct') or 0)*100:.2f}%",
        })
    st.dataframe(out, use_container_width=True, hide_index=True)


# ----- #3 Per-symbol drill-down modal --------------------------------------
@st.cache_data(ttl=900, show_spinner=False)
def _hank_symbol_summary_cached(symbol: str, pos_signature: str) -> str:
    """v3.64.0: cached LLM summary per (symbol, position-snapshot).
    pos_signature is a stringified key including weight/day_pl/unrealized
    so the cache invalidates when material changes happen."""
    try:
        import os as _os
        if not _os.getenv("ANTHROPIC_API_KEY"):
            return ""
        from anthropic import Anthropic
        from trader.copilot import dispatch_tool, MODEL
        # Pull the actual context — recent decisions, last 5 events, lots
        try:
            decisions = dispatch_tool("get_recent_decisions", {"n": 5})
        except Exception:
            decisions = {}
        try:
            events = dispatch_tool("get_upcoming_events", {"days_ahead": 14})
        except Exception:
            events = {}
        # Filter to this ticker
        sym_decisions = [d for d in (decisions.get("decisions") or [])
                          if str(d.get("ticker", "")).upper() == symbol.upper()][:3]
        sym_events = [e for e in (events.get("events") or [])
                       if str(e.get("symbol", "")).upper() == symbol.upper()][:3]
        prompt = (
            f"Symbol: {symbol}\n"
            f"Position: {pos_signature}\n"
            f"Recent decisions on this name: {sym_decisions}\n"
            f"Upcoming events on this name: {sym_events}\n\n"
            "Write a 3-bullet HANK-voice summary for the trader. Each bullet "
            "≤ 2 sentences. First bullet: current position context. "
            "Second: most recent decision rationale. Third: notable upcoming "
            "events or risks. NO filler. Numbers + sources only. If a bullet "
            "has nothing meaningful, write '— no signal'."
        )
        client = Anthropic(api_key=_os.getenv("ANTHROPIC_API_KEY"))
        resp = client.messages.create(
            model=MODEL, max_tokens=400,
            system="You are HANK. Tight, numerate, no filler.",
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(getattr(b, "text", "") for b in resp.content
                       if getattr(b, "type", None) == "text").strip()
        # v3.64.0: compliance audit log
        try:
            from trader.llm_audit import log_llm_call
            log_llm_call(
                context="hank_symbol_summary",
                user_input=f"summarize {symbol}",
                response_text=text, model=MODEL,
                input_tokens=getattr(resp.usage, "input_tokens", 0) if hasattr(resp, "usage") else 0,
                output_tokens=getattr(resp.usage, "output_tokens", 0) if hasattr(resp, "usage") else 0,
            )
        except Exception:
            pass
        return text
    except Exception as e:
        return f"_summary failed: {type(e).__name__}: {e}_"


def _hank_symbol_summary(symbol: str, pos: dict | None) -> str:
    """Build cache-stable signature + delegate to cached helper."""
    if pos:
        sig = (f"weight={pos.get('weight_pct', 0):.1f} "
               f"day={pos.get('day_pl_pct', 0):+.2f} "
               f"unr={pos.get('unrealized_pl_pct', 0):+.2f} "
               f"sector={pos.get('sector', '?')}")
    else:
        sig = "not_held"
    return _hank_symbol_summary_cached(symbol, sig)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_universe_momentum(_seed: str = ""):
    """Score every symbol in DEFAULT_LIQUID_50 by 12-1 momentum.
    Cached 5 min so opening multiple symbol modals doesn't re-fetch
    yfinance. The `_seed` arg lets callers force a refresh."""
    try:
        from trader.universe import DEFAULT_LIQUID_50
        from trader.strategy import rank_momentum
        # rank_momentum returns ALL ranked candidates if we ask for the
        # whole universe. Pull the full ranked list so we can compute
        # this symbol's rank and the #15 cutoff.
        cands = rank_momentum(DEFAULT_LIQUID_50, top_n=len(DEFAULT_LIQUID_50))
        return [(c.ticker, float(c.score)) for c in cands]
    except Exception as e:
        return [("__error__", str(e))]


def _render_position_why(symbol: str, pos: dict | None) -> None:
    """v3.72.1 — structured 'why we own it' panel.

    Answers the four questions every position implies but the prior
    UI buried:
      1. CASE: what's the score and where does it rank?
      2. WEIGHT MATH: why this specific %?
      3. WHAT WE WATCH: recent reactor signals + rule action
      4. WHAT DROPS US: score / rule conditions that would exit

    Shown above the HANK summary because this content is deterministic
    (recomputable from raw data); HANK is interpretive narrative on top.
    """
    sym_u = symbol.upper()

    # ---- 1. CASE — score + rank ----
    ranked = _cached_universe_momentum()
    if ranked and ranked[0][0] == "__error__":
        st.caption(f"_momentum data unavailable: {ranked[0][1]}_")
        return

    rank = None
    score = None
    cutoff_15 = None
    universe_size = len(ranked)
    for i, (t, s) in enumerate(ranked):
        if t == sym_u:
            rank = i + 1
            score = s
            break
    if len(ranked) >= 15:
        cutoff_15 = ranked[14][1]  # the #15-place score

    st.markdown("**📐 The case**")
    if score is not None and rank is not None:
        in_basket = rank <= 15
        verdict = ("✓ inside top-15 (currently held)" if in_basket
                   else "✗ outside top-15 (would not be in book)")
        margin = ""
        if cutoff_15 is not None and rank <= 15:
            buffer = (score - cutoff_15) * 100
            margin = (f" · buffer over #15 cutoff ({cutoff_15*100:+.1f}%): "
                      f"+{buffer:.1f}pp")
        st.markdown(
            f"- 12-1 momentum score: **{score*100:+.1f}%** (trailing "
            f"12-month return ending 1 month ago)\n"
            f"- Rank in {universe_size}-name universe: "
            f"**#{rank} of {universe_size}**  ·  {verdict}{margin}\n"
            f"- Strategy: `momentum_top15_mom_weighted_v1` (LIVE since "
            f"v3.42; PIT Sharpe +0.95)"
        )
    elif rank is None:
        st.markdown(
            f"- {sym_u} not in `DEFAULT_LIQUID_50` universe — held via "
            f"manual override or legacy decision\n"
            f"- Strategy variant: `momentum_top15_mom_weighted_v1`"
        )

    # ---- 2. WEIGHT MATH ----
    st.markdown("**🧮 Weight math**")
    if score is not None and rank is not None and rank <= 15:
        # Replicate the variant's weighting:
        #   shifted = score - min_top15_score + 0.01
        #   weight  = 0.80 × shifted / sum_of_shifted
        top15 = [s for _, s in ranked[:15]]
        min_s = min(top15)
        shifted = [s - min_s + 0.01 for s in top15]
        total = sum(shifted)
        idx_in_top15 = rank - 1
        sym_shifted = shifted[idx_in_top15]
        sym_weight = 0.80 * (sym_shifted / total) if total > 0 else 0
        st.markdown(
            f"- Score-shift: {score*100:+.1f}% − min_top15 "
            f"({min_s*100:+.1f}%) + 0.01 = **{sym_shifted:.4f}**\n"
            f"- Sum of shifted top-15 scores: {total:.4f}\n"
            f"- Target weight: 0.80 × ({sym_shifted:.4f} / {total:.4f}) "
            f"= **{sym_weight*100:.2f}%** of book\n"
            f"- Per-position cap: 16% (this position is "
            f"{'under' if sym_weight < 0.16 else 'AT/over'})"
        )
    elif pos and pos.get("weight_pct"):
        st.markdown(
            f"- Current actual weight: **{pos['weight_pct']:.2f}%**\n"
            f"- Variant weighting math n/a — symbol is below #15 in "
            f"current ranking, so this is a stale position waiting for "
            f"the next rebalance to drop it."
        )
    else:
        st.caption("_weight math unavailable_")

    # ---- 3. WHAT WE WATCH — reactor signals + rule action ----
    st.markdown("**👁️ Recent material disclosures (last 30d)**")
    try:
        from trader.earnings_reactor import recent_signals
        sigs = recent_signals(symbol=sym_u, since_days=30, limit=5)
    except Exception as e:
        sigs = []
        st.caption(f"_signal lookup failed: {e}_")

    if not sigs:
        st.markdown("- _no reactor signals in the last 30 days_")
    else:
        for s in sigs:
            mat = s.get("materiality") or 0
            arrow = {"BEARISH": "🔴", "BULLISH": "🟢",
                      "SURPRISE": "⚡"}.get(s.get("direction", ""), "⚪")
            st.markdown(
                f"- {arrow} **M{mat}/5 {s['direction']}** filed "
                f"{s['filed_at']}: {s.get('summary', '')[:140]}"
            )

    # ---- Rule action implication for the most recent signal ----
    if sigs:
        try:
            from trader.reactor_rule import ReactorSignalRule
            rsr = ReactorSignalRule()
            top_sig = sigs[0]  # most recent
            mat = top_sig.get("materiality") or 0
            direction = top_sig.get("direction", "")
            sd = top_sig.get("surprise_direction", "")
            triggers = (
                direction == "BEARISH"
                or (direction == "SURPRISE" and sd == "MISSED")
            )
            crosses_threshold = mat >= rsr.min_materiality
            if triggers and crosses_threshold:
                if rsr.status() == "LIVE":
                    action = (f"**Will trim** to {rsr.trim_to_pct*100:.0f}% "
                              f"of target weight at next rebalance")
                else:
                    action = (f"**Would trim** to {rsr.trim_to_pct*100:.0f}% "
                              f"(rule status SHADOW — would fire if LIVE)")
            elif triggers and not crosses_threshold:
                action = (f"No trim — M{mat} below threshold "
                          f"M≥{rsr.min_materiality}")
            else:
                action = (f"No trim — direction {direction} is not "
                          f"trim-eligible (only BEARISH and "
                          f"SURPRISE/MISSED trigger)")
            st.markdown(f"- Rule action: {action}")
        except Exception:
            pass

    # ---- 4. WHAT WOULD DROP US ----
    st.markdown("**🚪 What would drop this position**")
    drop_conditions = []
    if cutoff_15 is not None and score is not None:
        drop_conditions.append(
            f"Next monthly rebalance: 12-1 momentum drops below "
            f"**{cutoff_15*100:+.1f}%** (current #15 cutoff). "
            f"Today {sym_u} is at {score*100:+.1f}%."
        )
    drop_conditions.append(
        "Risk gate trip: deployment-DD < -25% (30d freeze) or < -33% "
        "(liquidation gate); daily loss > -6% (48h freeze)."
    )
    drop_conditions.append(
        "Reactor rule (LIVE): M≥4 BEARISH / SURPRISE-MISSED 8-K "
        "in last 14d → 50% weight trim at next rebalance "
        "(NOT a full exit)."
    )
    drop_conditions.append(
        "EarningsRule: T-1 day before earnings → trim to 50% of "
        "target until T+1 day after print."
    )
    for c in drop_conditions:
        st.markdown(f"- {c}")


@st.dialog("🔍 Symbol detail")
def _symbol_detail_modal(symbol: str):
    """Per-symbol drill-down: AI summary + recent decisions, lots, events, slippage, news."""
    st.subheader(f"📊 {symbol}")
    db = str(DB_PATH)

    # Live position info
    pos = None
    try:
        from trader.copilot import dispatch_tool
        ports = dispatch_tool("get_portfolio_status", {})
        pos = next((p for p in (ports.get("positions") or [])
                    if str(p.get("symbol", "")).upper() == symbol.upper()), None)
        if pos:
            st.markdown(f"**LIVE position** · "
                        f"weight {(pos.get('weight_pct') or 0):.1f}% · "
                        f"day {(pos.get('day_pl_pct') or 0):+.2f}% · "
                        f"unrealized {(pos.get('unrealized_pl_pct') or 0):+.2f}% · "
                        f"sector {pos.get('sector', '?')}")
        else:
            st.caption("_not currently held_")
    except Exception:
        pass

    # v3.72.1: structured "why we own it" — deterministic, recomputable,
    # answers the four questions every position implies. Renders ABOVE
    # the HANK summary because this content is grounded; HANK is
    # interpretive narrative on top of it.
    with st.expander("🔍 Why we own it (structured)", expanded=True):
        try:
            _render_position_why(symbol, pos)
        except Exception as e:
            st.caption(f"_why panel failed: {type(e).__name__}: {e}_")

    # v3.64.0: HANK summary — Bloomberg-style AI synthesis at top of drill-down
    with st.expander("🧠 HANK summary (interpretive)", expanded=False):
        summary = _hank_symbol_summary(symbol, pos)
        if summary:
            st.markdown(summary)
        else:
            st.caption("_AI summary unavailable — set ANTHROPIC_API_KEY_")

    tabs = st.tabs(["🎯 Decisions", "📦 Lots", "📅 Events",
                    "⚡ Slippage", "📈 Chart"])

    with tabs[0]:
        try:
            d = query(db,
                      "SELECT ts, action, style, score, rationale_json, final "
                      "FROM decisions WHERE ticker = ? "
                      "ORDER BY ts DESC LIMIT 50",
                      params=(symbol,))
            if not d.empty:
                st.dataframe(d, use_container_width=True, hide_index=True)
            else:
                st.caption("_no decisions in journal_")
        except Exception as e:
            st.caption(f"_query failed: {e}_")

    with tabs[1]:
        try:
            lots_open = query(db,
                              "SELECT id, sleeve, opened_at, qty, open_price "
                              "FROM position_lots WHERE symbol = ? AND closed_at IS NULL",
                              params=(symbol,))
            lots_closed = query(db,
                                "SELECT sleeve, opened_at, closed_at, qty, "
                                "open_price, close_price, realized_pnl "
                                "FROM position_lots WHERE symbol = ? AND closed_at IS NOT NULL "
                                "ORDER BY closed_at DESC LIMIT 20",
                                params=(symbol,))
            st.markdown("**Open lots:**")
            if not lots_open.empty:
                st.dataframe(lots_open, use_container_width=True, hide_index=True)
            else:
                st.caption("_none_")
            st.markdown("**Closed lots (last 20):**")
            if not lots_closed.empty:
                st.dataframe(lots_closed, use_container_width=True, hide_index=True)
            else:
                st.caption("_none_")
        except Exception as e:
            st.caption(f"_query failed: {e}_")

    with tabs[2]:
        try:
            from trader.events_calendar import compute_upcoming_events
            events = compute_upcoming_events([symbol], days_ahead=90)
            evs = [e for e in events if e.symbol == symbol]
            if evs:
                rows = [{"date": e.date.isoformat(), "type": e.event_type,
                         "days_until": e.days_until, "note": e.note}
                        for e in evs]
                st.dataframe(rows, use_container_width=True, hide_index=True)
            else:
                st.caption("_no upcoming events_")
        except Exception as e:
            st.caption(f"_events failed: {e}_")

    with tabs[3]:
        try:
            sl = query(db,
                       "SELECT ts, side, decision_mid, fill_price, slippage_bps, notional "
                       "FROM slippage_log WHERE symbol = ? ORDER BY ts DESC LIMIT 50",
                       params=(symbol,))
            if not sl.empty:
                st.dataframe(sl, use_container_width=True, hide_index=True)
            else:
                st.caption("_no slippage history (table is new in v3.58.1)_")
        except Exception as e:
            st.caption(f"_slippage query failed: {e}_")

    with tabs[4]:
        try:
            from trader.data import fetch_history
            import plotly.graph_objects as go
            from datetime import timedelta as _td, datetime as _dt
            start = (_dt.today() - _td(days=400)).strftime("%Y-%m-%d")
            hist = fetch_history([symbol], start=start)
            if symbol in hist.columns:
                series = hist[symbol].dropna()
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=series.index, y=series.values, mode="lines",
                    name=symbol, line=dict(width=1.5),
                ))
                # Overlay decision entry markers
                d = query(db,
                          "SELECT ts, action FROM decisions "
                          "WHERE ticker = ? AND action IN ('BUY','SELL') "
                          "ORDER BY ts DESC LIMIT 30",
                          params=(symbol,))
                if not d.empty:
                    for _, r in d.iterrows():
                        try:
                            t = pd.to_datetime(r["ts"])
                            fig.add_vline(x=t, line_dash="dot",
                                          line_color=("green" if r["action"] == "BUY" else "red"),
                                          opacity=0.3)
                        except Exception:
                            pass
                fig.update_layout(height=400, hovermode="x unified",
                                  showlegend=False, margin=dict(l=10,r=10,t=20,b=10))
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.caption("_no price history available_")
        except Exception as e:
            st.caption(f"_chart failed: {e}_")


def _maybe_open_symbol_modal():
    """Helper: if session_state.symbol_drill_down is set, open the modal."""
    sym = st.session_state.pop("symbol_drill_down", None)
    if sym:
        try:
            _symbol_detail_modal(sym)
        except Exception as e:
            st.error(f"modal failed: {e}")


# ============================================================
# View: Walk-forward + sensitivity + chaos (v3.59.5)
# ============================================================
def view_news():
    st.title("📰 News — US + Asian financial streams")
    st.caption(
        "Free-tier news adapters per `docs/DATA_INTEGRATIONS_ROADMAP.md`. "
        "US: Reuters / WSJ / MarketWatch / SeekingAlpha / SEC EDGAR / "
        "Yahoo. Asian: Caixin / Yicai / Sina / Nikkei / Yonhap. "
        "Sentiment scoring via Claude (when ANTHROPIC_API_KEY set)."
    )

    try:
        from trader.news_sources import (
            SOURCE_REGISTRY, fetch_all as _fetch_all_raw,
            fetch_per_ticker as _fetch_per_ticker_raw,
        )
    except Exception as e:
        st.error(f"news_sources unavailable: {e}")
        return

    # v3.62.2: cache RSS calls 10 min — each region group hits 5 sites,
    # ~2-5s round trip. Hash by (regions tuple, limit) so different
    # filters don't share a cache slot.
    @st.cache_data(ttl=600, show_spinner=False)
    def fetch_all(regions=None, per_source_limit=10):
        return _fetch_all_raw(regions=list(regions) if regions else None,
                                per_source_limit=per_source_limit)

    @st.cache_data(ttl=600, show_spinner=False)
    def fetch_per_ticker(ticker: str, limit: int = 10):
        return _fetch_per_ticker_raw(ticker, limit=limit)

    # Source counts by region
    cc = st.columns(4)
    by_region: dict[str, int] = {}
    for meta in SOURCE_REGISTRY.values():
        by_region[meta["region"]] = by_region.get(meta["region"], 0) + 1
    cc[0].metric("US sources", by_region.get("US", 0))
    cc[1].metric("CN sources", by_region.get("CN", 0))
    cc[2].metric("JP sources", by_region.get("JP", 0))
    cc[3].metric("KR sources", by_region.get("KR", 0))

    st.divider()

    fc1, fc2 = st.columns([1, 1])
    region_pick = fc1.multiselect(
        "Region filter",
        options=["US", "CN", "JP", "KR"],
        default=["US"],
        key="news_region",
    )
    n_per_source = fc2.slider("Items per source", 3, 20, 8, key="news_n")

    if st.button("🔄 Fetch latest", key="news_fetch"):
        with st.spinner(f"Fetching {n_per_source} per source × "
                          f"{len(region_pick)} regions..."):
            try:
                items = fetch_all(regions=region_pick or None,
                                    per_source_limit=n_per_source)
                st.session_state["_news_items"] = items
            except Exception as e:
                st.error(f"fetch failed: {e}")

    items = st.session_state.get("_news_items", [])
    if not items:
        st.info("Click 'Fetch latest' to pull headlines.")
        return

    st.success(f"Pulled {len(items)} items.")

    # Per-source counts
    src_counts: dict[str, int] = {}
    for it in items:
        src_counts[it.source] = src_counts.get(it.source, 0) + 1
    st.caption(" · ".join(f"`{s}` {n}" for s, n in
                            sorted(src_counts.items(), key=lambda x: -x[1])))

    # Sentiment scoring (optional, costs Claude tokens)
    if st.button("🧠 Score sentiment via Claude (uses API)",
                  key="news_score"):
        try:
            from trader.news_sentiment import score_items, aggregate_per_ticker
            with st.spinner(f"Scoring {len(items)} items..."):
                scores = score_items(items[:50])  # cap at 50 to control cost
            st.session_state["_news_scores"] = scores
            agg = aggregate_per_ticker(scores)
            st.session_state["_news_agg"] = agg
        except Exception as e:
            st.error(f"scoring failed: {e}")

    scores = st.session_state.get("_news_scores", [])
    agg = st.session_state.get("_news_agg", {})

    # Per-ticker aggregate sentiment
    if agg:
        st.subheader("📊 Aggregate sentiment by ticker")
        rows = []
        for ticker, stats in sorted(agg.items(),
                                       key=lambda x: -x[1]["weighted_score"]):
            rows.append({
                "ticker": ticker,
                "n_items": stats["n_items"],
                "weighted_sentiment": f"{stats['weighted_score']:+.2f}",
                "mean_sentiment": f"{stats['mean_score']:+.2f}",
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)

    # Headlines feed
    st.subheader("📰 Headlines")
    score_by_url = {s.url: s for s in scores}
    for it in items[:30]:
        s = score_by_url.get(it.url)
        if s:
            emoji = "🟢" if s.score > 0.2 else ("🔴" if s.score < -0.2 else "⚪")
            sent_str = f" [{emoji} {s.score:+.2f}]"
        else:
            sent_str = ""
        with st.expander(
                f"[{it.region}] {it.title[:100]}{sent_str}"):
            st.caption(f"_{it.source} · {it.ts[:19]} · {it.language}_")
            if it.body_snippet:
                st.markdown(it.body_snippet)
            if s and s.translated_title:
                st.markdown(f"**EN:** {s.translated_title}")
            if s and s.tickers:
                st.caption("Tickers: " + ", ".join(f"`{t}`" for t in s.tickers))
            if s and s.reasoning:
                st.caption(f"_Reasoning: {s.reasoning}_")
            if it.url:
                st.markdown(f"[source]({it.url})")

    st.divider()

    # Per-ticker fetch
    st.subheader("🔍 Per-ticker news")
    tcol = st.columns([3, 1])
    tk = tcol[0].text_input("Ticker", key="news_ticker_input").upper().strip()
    if tcol[1].button("Fetch", key="news_ticker_fetch", disabled=not tk):
        with st.spinner(f"Fetching {tk} news..."):
            tk_items = fetch_per_ticker(tk, limit=10)
        if tk_items:
            for it in tk_items:
                st.markdown(f"**{it.title}**")
                st.caption(f"_{it.source} · {it.ts[:19]}_")
                if it.url:
                    st.markdown(f"[source]({it.url})")
                st.divider()
        else:
            st.info("No news returned.")


# ============================================================
# View: Strategy Lab (v3.61.0) — every strategy in the codebase
# ============================================================
def view_manual_override():
    st.title("🛑 Manual override — kill-glass actions")
    st.caption(
        "Per-symbol actions that bypass the cron rebalance. "
        "**2-step confirmation**: (1) Plan — pure read, generates a token. "
        "(2) Execute — refuses unless `MANUAL_OVERRIDE_ALLOWED=true` env "
        "is set AND the token is < 60s old. Default mode is DRY_RUN; flip "
        "`MANUAL_OVERRIDE_DRY_RUN=false` to actually submit."
    )

    import os as _os
    allowed = _os.getenv("MANUAL_OVERRIDE_ALLOWED", "false").lower() == "true"
    dry_run = _os.getenv("MANUAL_OVERRIDE_DRY_RUN", "true").lower() == "true"

    sc = st.columns(3)
    sc[0].metric(
        "MANUAL_OVERRIDE_ALLOWED",
        "🟢 true" if allowed else "🔴 false",
        "set in container env to enable execute()",
    )
    sc[1].metric(
        "MANUAL_OVERRIDE_DRY_RUN",
        "🟡 true" if dry_run else "🔴 LIVE",
        "true = no broker call, plan only",
    )
    sc[2].metric(
        "Plan token TTL",
        "60s",
        "re-plan if you walk away",
    )

    if not allowed:
        st.warning(
            "Manual override **disabled**. Plan buttons work; Execute "
            "buttons will refuse. To enable, set `MANUAL_OVERRIDE_ALLOWED=true` "
            "in your docker-compose env and restart the dashboard."
        )

    if not dry_run and allowed:
        st.error(
            "🚨 **LIVE manual override is enabled.** Execute buttons will "
            "submit real orders. Set `MANUAL_OVERRIDE_DRY_RUN=true` to disarm."
        )

    st.divider()

    try:
        from trader import manual_override as mo
        from trader.copilot import dispatch_tool
        ports = dispatch_tool("get_portfolio_status", {})
        positions = (ports.get("positions") or []) if not ports.get("error") else []
        symbols = [p.get("symbol") for p in positions if p.get("symbol")]
    except Exception as e:
        st.error(f"could not load: {e}")
        return

    # ---- Action 1: Flatten position ----
    st.subheader("1️⃣ Flatten a position (close 100%)")
    fl_cols = st.columns([2, 1, 1])
    fl_sym = fl_cols[0].selectbox("Symbol to flatten",
                                    [""] + symbols, key="mo_flat_sym")
    if fl_cols[1].button("📋 Plan", key="mo_flat_plan",
                          use_container_width=True,
                          disabled=not fl_sym):
        plan = mo.plan_flatten(fl_sym)
        if not plan.get("ok"):
            st.error(plan.get("reason"))
        else:
            st.session_state["mo_flat_plan"] = plan
            st.success(f"Plan ready: {plan['summary']}")
            st.caption(f"_token: `{plan['plan_token']}` (expires in 60s)_")
    plan_cached = st.session_state.get("mo_flat_plan")
    if plan_cached and plan_cached.get("symbol") == fl_sym:
        if fl_cols[2].button("⚡ Execute", key="mo_flat_exec",
                              use_container_width=True,
                              type="primary", disabled=not allowed):
            res = mo.execute_flatten(plan_cached["plan_token"])
            st.session_state.pop("mo_flat_plan", None)
            if res.get("refused"):
                st.warning(f"Refused: {res['refused']}")
            elif res.get("dry_run"):
                st.info("DRY RUN — would have flattened. No order submitted.")
                st.json(res)
            elif res.get("executed"):
                st.success(f"✅ Flattened {res['symbol']}.")
            else:
                st.error(f"Failed: {res.get('error')}")

    st.divider()

    # ---- Action 2: Trim position by % ----
    st.subheader("2️⃣ Trim a position by %")
    tc = st.columns([2, 1, 1, 1])
    tr_sym = tc[0].selectbox("Symbol", [""] + symbols, key="mo_trim_sym")
    tr_pct = tc[1].slider("Trim %", 5, 95, 50, step=5, key="mo_trim_pct") / 100
    if tc[2].button("📋 Plan", key="mo_trim_plan",
                     use_container_width=True, disabled=not tr_sym):
        plan = mo.plan_trim(tr_sym, tr_pct)
        if not plan.get("ok"):
            st.error(plan.get("reason"))
        else:
            st.session_state["mo_trim_plan"] = plan
            st.success(f"Plan ready: {plan['summary']}")
    pc = st.session_state.get("mo_trim_plan")
    if pc and pc.get("symbol") == tr_sym and pc.get("pct") == tr_pct:
        if tc[3].button("⚡ Execute", key="mo_trim_exec",
                         use_container_width=True, type="primary",
                         disabled=not allowed):
            res = mo.execute_trim(pc["plan_token"])
            st.session_state.pop("mo_trim_plan", None)
            if res.get("refused"):
                st.warning(f"Refused: {res['refused']}")
            elif res.get("dry_run"):
                st.info("DRY RUN — would have trimmed. No order submitted.")
            elif res.get("executed"):
                st.success(f"✅ Submitted trim. Order id: `{res['order_id']}`")
            else:
                st.error(f"Failed: {res.get('error')}")

    st.divider()

    # ---- Action 3: Force pause (deploy-DD freeze) ----
    st.subheader("3️⃣ Force pause (30-day no-new-position freeze)")
    st.caption(
        "Trips the deployment-DD freeze in risk_manager. All future runs "
        "halt new orders for 30 days. Existing positions remain held. "
        "Use when you need to step away or re-evaluate the strategy."
    )
    pc2 = st.columns([2, 1, 1])
    fp_reason = pc2[0].text_input("Reason (logged)", key="mo_fp_reason",
                                    placeholder="taking a break / market regime shift / etc")
    if pc2[1].button("📋 Plan", key="mo_fp_plan", use_container_width=True):
        plan = mo.plan_force_pause(fp_reason or "manual")
        st.session_state["mo_fp_plan"] = plan
        st.success(f"Plan ready: {plan['summary']}")
    fpc = st.session_state.get("mo_fp_plan")
    if fpc:
        if pc2[2].button("⚡ Execute", key="mo_fp_exec",
                          use_container_width=True, type="primary",
                          disabled=not allowed):
            res = mo.execute_force_pause(fpc["plan_token"])
            st.session_state.pop("mo_fp_plan", None)
            if res.get("refused"):
                st.warning(f"Refused: {res['refused']}")
            elif res.get("dry_run"):
                st.info("DRY RUN — would have triggered freeze.")
            elif res.get("executed"):
                st.success(f"✅ Freeze triggered. {res.get('note')}")
            else:
                st.error(f"Failed: {res.get('error')}")

    with st.expander("📚 How to safely use this panel"):
        st.markdown("""
- **Default state is safe.** `MANUAL_OVERRIDE_ALLOWED=false` and `MANUAL_OVERRIDE_DRY_RUN=true` mean every Execute button refuses and every Plan is a pure read.
- **First-time wiring:** flip `MANUAL_OVERRIDE_ALLOWED=true` in `docker-compose.yml`, leave DRY_RUN=true. Verify the dry-run output looks correct.
- **Going live:** set `MANUAL_OVERRIDE_DRY_RUN=false`. The big red banner appears. Each Execute now submits a real order.
- **Plan tokens expire in 60s.** If you walk away mid-flow, you'll have to re-Plan — by design, so you don't accidentally execute an outdated plan.
- **Audit trail:** every executed action writes to `journal.orders`. Review in the Decisions tab.
""")


# ============================================================
# View: World-class gaps (v3.58.0) — surfaces every item from the
# "if you were a world-class trader, what's still missing" review.
# ============================================================
VIEW_DISPATCH = {
    # v4.0.0 freeze: viewer-only. The apparatus views (strategy lab,
    # P&L readiness, V5 sleeves, validation, stress test, regime,
    # shadow signals, world-class gaps, risk roadmap, strategy
    # leaderboard, postmortems, reports, earnings reactor, filings
    # archive, screener, grid) were ripped along with their gate
    # framework. What remains shows what's in the paper account.
    "chat": view_chat,
    "overview": view_overview,
    "live_positions": view_live_positions,
    "decisions": view_decisions,
    "lots": view_lots,
    "tlh": view_tlh,
    "performance": view_performance,
    "attribution": view_attribution,
    "events": view_events,
    "intraday": view_intraday,
    "news": view_news,
    "watchlist": view_watchlist,
    "slippage": view_slippage,
    "alerts": view_alerts,
    "sleeve_health": view_sleeve_health,
    "manual": view_manual,
    "manual_override": view_manual_override,
    "settings": view_settings,
}

active = st.session_state.active_view
view_fn = VIEW_DISPATCH.get(active, view_chat)

# v3.65.0: sticky market ribbon at the top of every non-chat view.
# Skipped on chat to keep the conversation surface clean (chat already
# has its own context line + the FAB is redundant when you're already
# in chat).
if active != "chat":
    _render_market_ribbon()

view_fn()

# v3.58.2 — open per-symbol modal if any view set the trigger this run.
_maybe_open_symbol_modal()

# v3.65.0: floating Ask-HANK pill bottom-right on every non-chat view.
# Skipped on the chat view itself (you're already there).
if active != "chat":
    _render_floating_hank_fab()

# ============================================================
# Auto-refresh (off by default in v3.55; user must enable in Settings)
# ============================================================
if st.session_state.auto_refresh_enabled and active != "chat":
    # Don't auto-refresh while on chat view — it interrupts stream
    placeholder = st.empty()
    placeholder.caption(f"auto-refresh in {st.session_state.refresh_sec}s · disable in Settings")
    time.sleep(st.session_state.refresh_sec)
    st.rerun()
