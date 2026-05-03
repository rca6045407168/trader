"""Per-run Markdown decision report.

Writes a comprehensive, self-contained, **permanent** report of one daily-run
to `data/reports/run_<run_id>.md`. Survives even if email isn't configured;
diffable across runs to see what changed; renderable in the Streamlit
dashboard's Reports tab.

Sections (in order):
  1. Header — run_id, timestamp, version, commit SHA
  2. Pre-flight — override-delay, peek-counter, kill-switch, deployment-anchor
  3. Account state — equity, cash, vs anchor, vs SPY
  4. Regime overlay — HMM + macro + GARCH live state with sub-multipliers
  5. LIVE variant decision — top picks + per-name weights + rationale
  6. Bottom-catch — candidates + debate outcomes (if USE_DEBATE)
  7. Risk gate — warnings + final adjusted targets
  8. Orders — what was submitted (or would be in DRY_RUN)
  9. Yesterday's post-mortem — Claude's proposed tweak
 10. Shadow variants — today's targets per shadow, side-by-side
 11. Anomalies — calendar/event signals fired today
 12. Performance — last 30d equity vs SPY
 13. Footer — next scheduled run, freeze state, links

Read-only — never modifies journal. Pure rendering of existing data.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Optional

from .config import DATA_DIR, DB_PATH


REPORTS_DIR = DATA_DIR / "reports"


def _git_sha() -> Optional[str]:
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent.parent,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        return sha or None
    except Exception:
        return None


def _read_json(p: Path) -> Any:
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _query(sql: str, params: tuple = ()) -> list[dict]:
    if not Path(DB_PATH).exists():
        return []
    try:
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute(sql, params).fetchall()]
    except Exception:
        return []


def _fmt_pct(x: Optional[float], plus: bool = True) -> str:
    if x is None:
        return "n/a"
    sign = "+" if plus and x >= 0 else ""
    return f"{sign}{x * 100:.2f}%"


def _fmt_money(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    return f"${x:,.2f}"


def _md_table(rows: list[dict], columns: list[str]) -> str:
    """Render a list-of-dicts as a Markdown table."""
    if not rows:
        return "_no rows_\n"
    out = ["| " + " | ".join(columns) + " |",
           "|" + "|".join("---" for _ in columns) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(r.get(c, "")) for c in columns) + " |")
    return "\n".join(out) + "\n"


@dataclass
class RunContext:
    """Inputs collected during a run that the report needs but can't easily
    re-derive from the journal alone (e.g. risk warnings, in-flight regime
    overlay state)."""
    run_id: str
    started_at: str
    momentum_picks: list[dict] = None       # [{ticker, score, action, style, rationale}]
    bottom_candidates: list[dict] = None
    approved_bottoms: list[dict] = None
    sleeve_alloc: dict = None
    final_targets: dict = None              # {ticker: weight}
    risk_warnings: list[str] = None
    rebalance_results: list[dict] = None
    bracket_results: list[dict] = None
    vix: Optional[float] = None
    equity_before: Optional[float] = None
    equity_after: Optional[float] = None
    cash_after: Optional[float] = None
    positions_now: dict = None
    spy_today_return: Optional[float] = None
    yesterday_equity: Optional[float] = None
    anomalies_today: list = None
    overlay_signal: dict = None             # raw OverlaySignal as dict
    shadow_results: dict = None             # variant_id -> {targets, rationale}
    halted: bool = False
    halt_reason: Optional[str] = None


# Hard-coded research-citation map. Each LIVE variant should have an entry
# here so the per-run report can cite the literature backing the rule. Keep
# entries terse — markdown bullets, ideally with arxiv ID + verbatim quote.
# Per docs/SWARM_VERIFICATION_PROTOCOL.md, anything we cite here must be
# verified-real. Updates require the same TRUST/VERIFY/ABSTAIN gate.
_RESEARCH_CITATIONS: dict[str, str] = {
    "momentum_top15_mom_weighted_v1": (
        "- **12‑1 momentum factor:** Jegadeesh & Titman (1993) *Returns to "
        "Buying Winners and Selling Losers* (J. Finance) — the original "
        "finding that 12‑month return rank, skipping the most recent month, "
        "predicts next‑month returns.\n"
        "- **PIT validation requirement:** Lopez de Prado (2018) *Advances in "
        "Financial Machine Learning*, ch. 11–12 (CPCV + deflated Sharpe). "
        "This variant survived our 3‑gate pipeline (survivor 5‑regime → PIT "
        "→ CPCV) where 40+ candidates failed.\n"
        "- **Top‑15 dispersion vs top‑3 concentration:** internal v3.42 "
        "audit — top‑3 had 27% single‑name concentration; top‑15 caps each "
        "name near 10%. PIT Sharpe statistically equivalent (+0.95 vs "
        "+0.98), but materially better idiosyncratic risk profile.\n"
        "- **Momentum‑proportional weighting (vs equal):** mild Kelly‑lite "
        "tilt; consistent with Asness, Frazzini & Pedersen (2013) *Quality "
        "Minus Junk* (Appendix D) — weight by signal strength when signals "
        "are noisy and bounded."
    ),
    "momentum_top3_aggressive_v1": (
        "- **RETIRED v3.42:** Same 12‑1 momentum factor as top‑15, but with "
        "27% single‑name concentration. Demoted after CPCV testing of 40+ "
        "alpha candidates produced no replicable edge over top‑15 mom‑weighted."
    ),
    "momentum_top5_eq_v1": (
        "- **RETIRED v3.1:** Original Jegadeesh‑Titman top‑5 equal‑weight at "
        "40% sleeve. 5‑regime stress test showed top‑3 dominated by Sharpe "
        "in every regime; subsequently displaced by top‑15 mom‑weighted on "
        "PIT honesty grounds."
    ),
}


def _research_citation_for(variant_id: str) -> Optional[str]:
    """Return the research-citation block for a given variant_id, or None."""
    return _RESEARCH_CITATIONS.get(variant_id)


def render(ctx: RunContext) -> str:
    """Return the full Markdown report as a string."""
    parts: list[str] = []
    sha = _git_sha()
    now = datetime.utcnow().isoformat()

    # ---------- Header ----------
    parts.append(f"# Decision report — `{ctx.run_id}`\n")
    parts.append(f"> generated `{now}` UTC · git `{sha or 'unknown'}` · "
                 f"`{Path(__file__).parent.name}` v3.50\n")
    if ctx.halted:
        parts.append(f"\n> ## 🛑 RUN HALTED\n> {ctx.halt_reason or '(no reason captured)'}\n")

    # ---------- Pre-flight ----------
    parts.append("\n## 1. Pre-flight gates\n")
    anchor = _read_json(DATA_DIR / "deployment_anchor.json")
    override = _read_json(DATA_DIR / "override_delay_state.json")
    peek_log = _read_json(DATA_DIR / "peek_log.json")
    freeze = _read_json(DATA_DIR / "risk_freeze_state.json") or {}

    parts.append("**Deployment anchor:** "
                 + (f"${float(anchor.get('equity_at_deploy', 0)):,.0f} set "
                    f"`{anchor.get('deploy_timestamp', '?')}` "
                    f"({anchor.get('source', '?')})" if anchor else "_not set_") + "\n\n")
    parts.append("**Override-delay SHA:** "
                 + (f"`{override.get('current_sha', '?')[:12]}` recorded "
                    f"`{override.get('sha_recorded_at', '?')}`" if override else "_first run_") + "\n\n")
    if isinstance(peek_log, list):
        cutoff = datetime.utcnow() - timedelta(days=30)
        recent = [e for e in peek_log
                  if datetime.fromisoformat(e.get("ts", "1970-01-01")) > cutoff]
        parts.append(f"**Peek counter (manual triggers / 30d):** {len(recent)}\n\n")
    parts.append(f"**Freeze state:** ")
    if freeze.get("liquidation_gate_tripped"):
        parts.append(f"🚨 **LIQUIDATION GATE TRIPPED** — written post-mortem required\n\n")
    elif "deploy_dd_freeze_until" in freeze:
        parts.append(f"❄️ DEPLOY-DD FREEZE until `{freeze['deploy_dd_freeze_until']}`\n\n")
    elif "daily_loss_freeze_until" in freeze:
        parts.append(f"❄️ DAILY-LOSS FREEZE until `{freeze['daily_loss_freeze_until']}`\n\n")
    else:
        parts.append("✅ no freeze active\n\n")

    # ---------- Account state ----------
    parts.append("## 2. Account state\n")
    parts.append(f"- **Equity (start of run):** {_fmt_money(ctx.equity_before)}\n")
    parts.append(f"- **Equity (end of run):** {_fmt_money(ctx.equity_after)}\n")
    parts.append(f"- **Cash:** {_fmt_money(ctx.cash_after)}\n")
    if ctx.equity_after and anchor:
        anchor_eq = float(anchor.get("equity_at_deploy", 0))
        if anchor_eq > 0:
            parts.append(f"- **vs deployment anchor:** {_fmt_pct((ctx.equity_after - anchor_eq)/anchor_eq)} "
                         f"(anchor ${anchor_eq:,.0f})\n")
    if ctx.yesterday_equity and ctx.equity_before:
        day = (ctx.equity_before - ctx.yesterday_equity) / ctx.yesterday_equity
        parts.append(f"- **Day P&L (start vs yesterday):** {_fmt_pct(day)}\n")
    if ctx.spy_today_return is not None:
        parts.append(f"- **SPY today:** {_fmt_pct(ctx.spy_today_return)}\n")
    parts.append(f"- **VIX:** {ctx.vix:.1f}\n" if ctx.vix is not None else "- **VIX:** n/a\n")

    # ---------- Regime overlay ----------
    parts.append("\n## 3. Regime overlay (v3.49)\n")
    if ctx.overlay_signal:
        s = ctx.overlay_signal
        parts.append(f"- **HMM regime:** `{s.get('hmm_regime', '?')}` "
                     f"posterior {_fmt_pct(s.get('hmm_posterior'), plus=False)} → "
                     f"sub-mult **{s.get('hmm_mult', 1.0):.2f}**\n")
        macro_state = "ok"
        if s.get("macro_curve_inverted") and s.get("macro_credit_widening"):
            macro_state = "**curve inverted + credit widening**"
        elif s.get("macro_curve_inverted"):
            macro_state = "**curve inverted**"
        elif s.get("macro_credit_widening"):
            macro_state = "**credit widening**"
        parts.append(f"- **Macro:** {macro_state} → sub-mult **{s.get('macro_mult', 1.0):.2f}**\n")
        vol = s.get("garch_vol_forecast_annual")
        parts.append(f"- **GARCH vol forecast (annualized):** "
                     f"{f'{vol*100:.1f}%' if vol else 'n/a'} → sub-mult **{s.get('garch_mult', 1.0):.2f}**\n")
        parts.append(f"- **Final multiplier:** **{s.get('final_mult', 1.0):.2f}** "
                     f"({'**APPLIED**' if s.get('enabled') else '_DISABLED — observability only_'})\n")
    else:
        parts.append("_overlay signal not captured for this run_\n")

    # ---------- LIVE variant decision ----------
    parts.append("\n## 4. LIVE variant decision — the WHY\n")

    # Look up the LIVE variant from the registry to surface methodology + research.
    live_variant = None
    try:
        from . import variants  # noqa: F401  triggers registration
        from .ab import get_live
        live_variant = get_live()
    except Exception:
        pass

    if live_variant is not None:
        parts.append(f"### Methodology: `{live_variant.variant_id}` (v{live_variant.version})\n\n")
        parts.append(f"**Status:** `{live_variant.status}`  \n")
        parts.append(f"**Description:**  \n> {live_variant.description}\n\n")
        if live_variant.params:
            parts.append("**Parameters:**\n\n")
            param_rows = [{"key": k, "value": str(v)} for k, v in live_variant.params.items()]
            parts.append(_md_table(param_rows, ["key", "value"]))
            parts.append("\n")

        # Research citation block — hard-coded mapping of variant_id to its
        # primary academic backing. This is the WHY the strategy is rule-based
        # this way, not the WHY each individual ticker was picked.
        research = _research_citation_for(live_variant.variant_id)
        if research:
            parts.append(f"**Research backing:**\n\n{research}\n\n")

    parts.append("### Per-pick rationale\n\n")
    if ctx.momentum_picks:
        # Per-pick rationale: for momentum, the rationale is the trailing
        # 12-1 return that drove the score (rank_momentum stores this in
        # Candidate.rationale as a dict).
        rows = []
        for p in ctx.momentum_picks[:20]:
            rationale = p.get("rationale", {})
            if isinstance(rationale, dict):
                trailing = rationale.get("trailing_return", rationale.get("momentum", None))
                trailing_str = f"{trailing*100:+.1f}%" if trailing is not None else ""
                why = rationale.get("why", "")
                if not why and trailing is not None:
                    why = f"12-1 momentum {trailing_str}"
            else:
                why = str(rationale)[:120]
            weight_pct = ctx.final_targets.get(p.get("ticker"), 0) if ctx.final_targets else 0
            rows.append({
                "ticker": p.get("ticker", "?"),
                "score": f"{p.get('score', 0):.3f}",
                "weight": f"{weight_pct*100:.2f}%" if weight_pct else "-",
                "why": why or "_no rationale captured_",
            })
        parts.append(_md_table(rows, ["ticker", "score", "weight", "why"]))
    else:
        parts.append("_no picks_\n")

    # ---------- Bottom-catch ----------
    parts.append("\n## 5. Bottom-catch sleeve\n")
    if ctx.bottom_candidates:
        rows = [{"ticker": p.get("ticker", "?"),
                 "score": f"{p.get('score', 0):.2f}",
                 "rationale": (p.get("rationale", "")[:80] + "...")
                              if len(p.get("rationale", "")) > 80 else p.get("rationale", "")}
                for p in ctx.bottom_candidates[:10]]
        parts.append(_md_table(rows, ["ticker", "score", "rationale"]))
    else:
        parts.append("_no oversold candidates today_\n")

    if ctx.approved_bottoms:
        parts.append(f"\n**Approved by debate:** {len(ctx.approved_bottoms)}\n")
        for b in ctx.approved_bottoms:
            c = b.get("candidate", {})
            ticker = c.ticker if hasattr(c, "ticker") else c.get("ticker", "?")
            pct = b.get("position_pct", 0)
            parts.append(f"- `{ticker}` @ {pct*100:.1f}%\n")

    # ---------- Risk gate ----------
    parts.append("\n## 6. Risk gate\n")
    if ctx.risk_warnings:
        parts.append("**Warnings:**\n")
        for w in ctx.risk_warnings:
            parts.append(f"- {w}\n")
    if ctx.final_targets:
        parts.append("\n**Final targets (post-risk-adjustment):**\n\n")
        rows = [{"ticker": t, "weight": f"{w*100:.2f}%"}
                for t, w in sorted(ctx.final_targets.items(), key=lambda kv: -kv[1])]
        parts.append(_md_table(rows, ["ticker", "weight"]))
        total = sum(ctx.final_targets.values())
        parts.append(f"\n**Total gross:** {total*100:.1f}%\n")

    # ---------- Orders ----------
    parts.append("\n## 7. Orders\n")
    if ctx.rebalance_results:
        parts.append(f"### Momentum rebalance ({len(ctx.rebalance_results)} legs)\n\n")
        rows = [{"symbol": r.get("symbol", "?"),
                 "side": r.get("side", "?"),
                 "notional": f"${r.get('notional', 0):,.2f}" if r.get("notional") else "-",
                 "status": r.get("status", "?"),
                 "order_id": (r.get("order_id", "") or "")[:12],
                 "error": (r.get("error", "") or "")[:80]}
                for r in ctx.rebalance_results]
        parts.append(_md_table(rows, ["symbol", "side", "notional", "status", "order_id", "error"]))
    if ctx.bracket_results:
        parts.append(f"\n### Bottom-catch brackets ({len(ctx.bracket_results)})\n\n")
        rows = [{"symbol": r.get("symbol", "?"),
                 "qty": r.get("qty", "?"),
                 "limit": r.get("limit", "?"),
                 "stop": r.get("stop", "?"),
                 "take": r.get("take", "?"),
                 "status": r.get("status", "?")}
                for r in ctx.bracket_results]
        parts.append(_md_table(rows, ["symbol", "qty", "limit", "stop", "take", "status"]))

    # ---------- Yesterday's post-mortem ----------
    parts.append("\n## 8. Yesterday's post-mortem\n")
    pm = _query("SELECT * FROM postmortems ORDER BY date DESC LIMIT 1")
    if pm:
        p = pm[0]
        parts.append(f"**Date:** {p.get('date')}\n\n")
        parts.append(f"**Day P&L:** {_fmt_pct(p.get('pnl_pct'))}\n\n")
        parts.append(f"**Summary:** {p.get('summary', '')}\n\n")
        parts.append(f"**Proposed tweak:** {p.get('proposed_tweak', '')}\n")
    else:
        parts.append("_no post-mortem available_\n")

    # ---------- Shadow variants ----------
    parts.append("\n## 9. Shadow variants today\n")
    if ctx.shadow_results:
        rows = []
        for vid, info in sorted(ctx.shadow_results.items()):
            if "error" in info:
                rows.append({"variant_id": vid, "n_picks": "ERR",
                             "gross": "-", "top5": info["error"][:60]})
            else:
                tgts = info.get("targets", {})
                if not tgts:
                    rows.append({"variant_id": vid, "n_picks": 0,
                                 "gross": "0%", "top5": "(empty)"})
                else:
                    sorted_t = sorted(tgts.items(), key=lambda kv: -kv[1])
                    top5 = ", ".join(f"{k}({v*100:.1f}%)" for k, v in sorted_t[:5])
                    rows.append({"variant_id": vid, "n_picks": len(tgts),
                                 "gross": f"{sum(tgts.values())*100:.1f}%",
                                 "top5": top5})
        parts.append(_md_table(rows, ["variant_id", "n_picks", "gross", "top5"]))
    else:
        parts.append("_no shadow variants ran_\n")

    # ---------- Anomalies ----------
    parts.append("\n## 10. Anomalies fired today\n")
    if ctx.anomalies_today:
        for a in ctx.anomalies_today:
            name = getattr(a, "anomaly", None) or (a.get("anomaly") if isinstance(a, dict) else str(a))
            conf = getattr(a, "confidence", None) or (a.get("confidence") if isinstance(a, dict) else "?")
            parts.append(f"- **{name}** (confidence `{conf}`)\n")
    else:
        parts.append("_no anomalies triggered today_\n")

    # ---------- Performance (last 30d) ----------
    parts.append("\n## 11. Performance (last 30 daily snapshots)\n")
    snaps = _query("SELECT date, equity, cash FROM daily_snapshot ORDER BY date DESC LIMIT 30")
    if snaps:
        first = snaps[-1]
        last = snaps[0]
        if first.get("equity"):
            ret = (last["equity"] - first["equity"]) / first["equity"]
            parts.append(f"- **Window:** `{first['date']}` → `{last['date']}` "
                         f"({len(snaps)} snapshots)\n")
            parts.append(f"- **Equity change:** {_fmt_money(last['equity'])} "
                         f"({_fmt_pct(ret)})\n")
            equities = [s["equity"] for s in snaps if s.get("equity")]
            if equities:
                peak = max(equities)
                trough_after_peak = min(equities[:equities.index(peak)+1] or [peak])
                parts.append(f"- **Peak equity in window:** {_fmt_money(peak)}\n")
                parts.append(f"- **Worst DD in window:** {_fmt_pct((trough_after_peak - peak)/peak)}\n")
    else:
        parts.append("_no snapshots in journal_\n")

    # ---------- Footer ----------
    parts.append("\n---\n")
    parts.append(f"_Report generated by `decision_report.render` at {now} UTC._  \n")
    parts.append(f"_Run sentinel `{ctx.run_id}` started `{ctx.started_at}`._  \n")
    parts.append(f"_View live state at http://localhost:8501 (Streamlit dashboard, if running)._  \n")
    parts.append(f"_Full journal at `{DB_PATH}`._\n")

    return "".join(parts)


def write_report(ctx: RunContext) -> Path:
    """Render the report and write to data/reports/run_<run_id>.md.

    Returns the path written. Idempotent — re-writes if called twice for
    the same run_id (the report content reflects current journal state).
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    body = render(ctx)
    out = REPORTS_DIR / f"run_{ctx.run_id}.md"
    out.write_text(body)
    return out


def list_reports(limit: int = 100) -> list[Path]:
    """Return reports sorted newest-first."""
    if not REPORTS_DIR.exists():
        return []
    files = sorted(REPORTS_DIR.glob("run_*.md"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]
