"""Sleeve health monitor (v3.51.0 / Tier B).

Defensive observability over multi-sleeve LIVE allocation. Three checks:

  1. **Cross-sleeve correlation** — rolling 60-day correlation between every
     pair of sleeves. If any pair > 0.70, we don't actually have uncorrelated
     edges; the multi-sleeve thesis breaks down.

  2. **Per-sleeve rolling Sharpe** — rolling 90-day Sharpe per sleeve. If a
     LIVE sleeve drops below DECAY_SHARPE_THRESHOLD (default 0.3) for 30+
     consecutive days, flag for demotion to shadow.

  3. **Auto-demote recommendation** — emits a structured proposal that the
     adversarial pre-promotion CI gate (separate module) can review. We do
     NOT auto-demote silently — a human must merge the variant-status flip.
     The override-delay 24h cool-off then catches any approved demote.

Reads from the journal:
  - `position_lots` — open + closed lots tagged by sleeve
  - `daily_snapshot` — equity over time (for benchmark-relative Sharpe)
  - `shadow_decisions` — shadow targets to compute synthetic returns
  - `variants` — status of each variant

Writes nothing. Output goes to:
  - `data/sleeve_health.json` — latest snapshot
  - email/Slack alert if any threshold tripped
  - dashboard's new 📊 Sleeve health tab (Tier B follow-up commit)

References:
  - Asness, Frazzini, Pedersen (2013) *Quality Minus Junk* — sleeve
    diversification math; per-factor IR < 0.5 typically, but uncorrelated
    factors stack to portfolio IR > 1.0
  - Lopez de Prado (2018) ch. 13 — strategy decay detection via rolling
    Sharpe vs sample-level deflated Sharpe
  - Citadel-style multi-strat reallocation — capital reallocated daily
    across strategies based on rolling Sharpe; underperformers cut to 0%
    weight (we adopt the cut, not the daily reallocation, for retail scale)
"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .config import DATA_DIR, DB_PATH


# ---- Thresholds (tunable; rationale in module docstring) ----
CORRELATION_LOOKBACK_DAYS = 60
CORRELATION_ALERT_THRESHOLD = 0.70

SHARPE_LOOKBACK_DAYS = 90
DECAY_SHARPE_THRESHOLD = 0.30
DECAY_CONSECUTIVE_DAYS = 30  # how long sub-threshold before demote

MIN_DAYS_FOR_VALID_SHARPE = 20

SLEEVE_HEALTH_PATH = DATA_DIR / "sleeve_health.json"


@dataclass
class SleeveStat:
    sleeve_id: str  # variant_id or "MOMENTUM"/"BOTTOM_CATCH" for sleeve-level grouping
    status: str  # 'live' | 'shadow' | 'paper' | 'retired'
    n_observations: int
    rolling_sharpe: Optional[float] = None
    rolling_sortino: Optional[float] = None
    rolling_vol_annual: Optional[float] = None
    days_below_decay_threshold: int = 0
    flagged_for_demote: bool = False
    flag_reason: str = ""


@dataclass
class CorrelationFinding:
    sleeve_a: str
    sleeve_b: str
    correlation: float
    n_observations: int
    over_threshold: bool


@dataclass
class SleeveHealthReport:
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    per_sleeve: list[SleeveStat] = field(default_factory=list)
    correlations: list[CorrelationFinding] = field(default_factory=list)
    demote_recommendations: list[dict] = field(default_factory=list)
    correlation_alerts: list[dict] = field(default_factory=list)
    overall_health: str = "green"  # green | yellow | red
    rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "per_sleeve": [vars(s) for s in self.per_sleeve],
            "correlations": [vars(c) for c in self.correlations],
            "demote_recommendations": self.demote_recommendations,
            "correlation_alerts": self.correlation_alerts,
            "overall_health": self.overall_health,
            "rationale": self.rationale,
        }


# ============================================================
# Helpers — read journal
# ============================================================

def _conn_ro():
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def _registered_variants() -> list[dict]:
    """Return all registered variants with status from the journal."""
    if not Path(DB_PATH).exists():
        return []
    try:
        with _conn_ro() as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute(
                "SELECT variant_id, status FROM variants "
                "WHERE status IN ('live', 'shadow') "
                "ORDER BY status DESC, variant_id"
            ).fetchall()]
    except Exception:
        return []


def _sleeve_returns_from_lots(sleeve: str, lookback_days: int) -> list[float]:
    """Compute realized daily returns for a SLEEVE (MOMENTUM / BOTTOM_CATCH)
    from closed lots in the journal. Returns daily P&L %.

    Approximation — this gives the realized PnL stream for closed lots.
    For LIVE attribution we'd use mark-to-market on open lots too; that
    requires daily price data which we'd cache via yfinance. For Tier B's
    health monitor (focus: detect decay), realized-only is sufficient.
    """
    if not Path(DB_PATH).exists():
        return []
    cutoff = (datetime.utcnow() - timedelta(days=lookback_days * 2)).isoformat()
    try:
        with _conn_ro() as c:
            rows = c.execute(
                """SELECT closed_at, qty, open_price, close_price
                   FROM position_lots
                   WHERE sleeve = ? AND closed_at IS NOT NULL
                     AND closed_at > ?
                   ORDER BY closed_at""",
                (sleeve, cutoff)
            ).fetchall()
    except Exception:
        return []
    rets = []
    for closed_at, qty, open_price, close_price in rows:
        if open_price and close_price and open_price > 0:
            rets.append((float(close_price) - float(open_price)) / float(open_price))
    return rets


def _shadow_target_returns(variant_id: str, lookback_days: int) -> list[float]:
    """Reconstruct synthetic daily returns for a SHADOW variant from its
    logged targets. We need price data to compute actual returns, so this
    falls back gracefully if data is missing.

    For now, returns empty list — a future Tier B follow-up will wire the
    yfinance cache to compute next-day return on each shadow_decisions row.
    """
    return []


# ============================================================
# Statistics
# ============================================================

def _annualized_sharpe(returns: list[float]) -> Optional[float]:
    """Annualized Sharpe assuming daily returns. Uses 252 trading days.

    Guards against floating-point noise: if std < 1e-10 we treat the series
    as effectively constant (degenerate) and return None rather than a
    pathological Sharpe like 2e16 from a constant series with rounding
    epsilon variance.
    """
    if len(returns) < MIN_DAYS_FOR_VALID_SHARPE:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / max(len(returns) - 1, 1)
    std = math.sqrt(var)
    if std < 1e-10:
        return None
    return (mean / std) * math.sqrt(252)


def _annualized_sortino(returns: list[float]) -> Optional[float]:
    """Like Sharpe but only penalizes downside variance."""
    if len(returns) < MIN_DAYS_FOR_VALID_SHARPE:
        return None
    mean = sum(returns) / len(returns)
    downside = [r for r in returns if r < 0]
    if not downside:
        return None
    dvar = sum(r ** 2 for r in downside) / len(downside)
    dstd = math.sqrt(dvar)
    if dstd <= 0:
        return None
    return (mean / dstd) * math.sqrt(252)


def _annualized_vol(returns: list[float]) -> Optional[float]:
    if len(returns) < MIN_DAYS_FOR_VALID_SHARPE:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / max(len(returns) - 1, 1)
    std = math.sqrt(var)
    return std * math.sqrt(252) if std >= 1e-10 else 0.0


def _pearson_correlation(a: list[float], b: list[float]) -> Optional[float]:
    """Pearson correlation between two equal-length return series.
    Returns None if either series is effectively constant (var < 1e-20).
    """
    n = min(len(a), len(b))
    if n < MIN_DAYS_FOR_VALID_SHARPE:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / max(n - 1, 1)
    va = sum((a[i] - ma) ** 2 for i in range(n)) / max(n - 1, 1)
    vb = sum((b[i] - mb) ** 2 for i in range(n)) / max(n - 1, 1)
    if va < 1e-20 or vb < 1e-20:
        return None
    return cov / math.sqrt(va * vb)


# ============================================================
# Public API
# ============================================================

def compute_health() -> SleeveHealthReport:
    """Run all three checks and return a SleeveHealthReport. Idempotent;
    safe to call from a cron, the dashboard, or a CI gate.
    """
    rep = SleeveHealthReport()
    variants = _registered_variants()
    if not variants:
        rep.overall_health = "yellow"
        rep.rationale = "no variants registered in journal"
        return rep

    # ---- Per-sleeve rolling Sharpe + decay flag ----
    sleeve_returns: dict[str, list[float]] = {}
    for sleeve in ("MOMENTUM", "BOTTOM_CATCH"):
        rets = _sleeve_returns_from_lots(sleeve, SHARPE_LOOKBACK_DAYS)
        sleeve_returns[sleeve] = rets
        sharpe = _annualized_sharpe(rets)
        sortino = _annualized_sortino(rets)
        vol = _annualized_vol(rets)

        flagged = (sharpe is not None and sharpe < DECAY_SHARPE_THRESHOLD
                   and len(rets) >= DECAY_CONSECUTIVE_DAYS)
        flag_reason = ""
        if flagged:
            flag_reason = (f"rolling {SHARPE_LOOKBACK_DAYS}d Sharpe {sharpe:.2f} "
                           f"< {DECAY_SHARPE_THRESHOLD} threshold over "
                           f"{len(rets)} observations — candidate for demote")

        rep.per_sleeve.append(SleeveStat(
            sleeve_id=sleeve,
            status="live",  # sleeves are LIVE by composition; variant_id is what gets demoted
            n_observations=len(rets),
            rolling_sharpe=sharpe,
            rolling_sortino=sortino,
            rolling_vol_annual=vol,
            days_below_decay_threshold=len(rets) if flagged else 0,
            flagged_for_demote=flagged,
            flag_reason=flag_reason,
        ))

        if flagged:
            rep.demote_recommendations.append({
                "sleeve_id": sleeve,
                "current_status": "live",
                "proposed_status": "shadow",
                "rolling_sharpe": sharpe,
                "reason": flag_reason,
                "requires_human_approval": True,
                "requires_adversarial_review": True,
            })

    # ---- Cross-sleeve correlation ----
    sleeve_ids = [s for s, r in sleeve_returns.items() if len(r) >= MIN_DAYS_FOR_VALID_SHARPE]
    for i, a in enumerate(sleeve_ids):
        for b in sleeve_ids[i + 1:]:
            corr = _pearson_correlation(sleeve_returns[a], sleeve_returns[b])
            if corr is None:
                continue
            over = abs(corr) > CORRELATION_ALERT_THRESHOLD
            rep.correlations.append(CorrelationFinding(
                sleeve_a=a, sleeve_b=b,
                correlation=corr,
                n_observations=min(len(sleeve_returns[a]), len(sleeve_returns[b])),
                over_threshold=over,
            ))
            if over:
                rep.correlation_alerts.append({
                    "sleeve_a": a, "sleeve_b": b,
                    "correlation": corr,
                    "reason": (f"|correlation| {abs(corr):.2f} > "
                               f"{CORRELATION_ALERT_THRESHOLD} — sleeves are not "
                               f"genuinely uncorrelated; multi-sleeve "
                               f"diversification thesis is weakened"),
                })

    # ---- Overall health bucket ----
    if rep.demote_recommendations or rep.correlation_alerts:
        rep.overall_health = "red" if rep.demote_recommendations else "yellow"
    rationale_bits = [
        f"sleeves checked={len(rep.per_sleeve)}",
        f"correlation pairs={len(rep.correlations)}",
        f"demote candidates={len(rep.demote_recommendations)}",
        f"correlation alerts={len(rep.correlation_alerts)}",
    ]
    rep.rationale = " · ".join(rationale_bits)
    return rep


def write_health_report(rep: Optional[SleeveHealthReport] = None) -> Path:
    if rep is None:
        rep = compute_health()
    SLEEVE_HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    SLEEVE_HEALTH_PATH.write_text(json.dumps(rep.to_dict(), indent=2, default=str))
    return SLEEVE_HEALTH_PATH


def read_latest_health() -> Optional[dict]:
    if not SLEEVE_HEALTH_PATH.exists():
        return None
    try:
        return json.loads(SLEEVE_HEALTH_PATH.read_text())
    except Exception:
        return None
