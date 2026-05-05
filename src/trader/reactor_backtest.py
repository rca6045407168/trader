"""Backtest harness for the v3.69.0 ReactorSignalRule (v3.72.0).

Question this answers: "If I'd flipped REACTOR_RULE_STATUS=LIVE on
day X, what would the cumulative P&L impact have been?"

How it works:
  1. Read every historical rebalance from `decisions` (where
     final LIKE 'LIVE_VARIANT_BUY%' — those rows carry per-symbol
     target weights at that rebalance).
  2. For each rebalance T, find earnings_signals filed in
     [T - lookback_days, T] that meet the rule's threshold.
  3. For each trim-worthy (symbol, signal) pair: counterfactual
     weight = original × trim_pct.
  4. Pull forward prices [T, T+30 trading days] from yfinance.
     Compute what trimming WOULD HAVE saved (if BEARISH right) or
     cost (if BEARISH wrong) over the next 30 days vs holding the
     full position to T+1mo (the next rebalance).
  5. Aggregate across all triggers.

Honest limits:
  - Forward returns are approximate (we use yfinance close-to-close,
    no intraday execution slippage modeling)
  - The rebalance ALSO follows momentum logic, so the trimmed
    capital doesn't just sit in cash — it gets reabsorbed into
    other names at next rebalance. The harness measures the
    1-rebalance-cycle delta only (T to T+1mo).
  - When n_trims_triggered = 0 (the realistic case for early data),
    the result is "no signal yet, keep collecting."
  - Bias risk: the journal's signals were graded by Claude AFTER
    the news, so we have no look-ahead in the signal itself. But
    they were graded in NOMINAL conditions — running a model from
    May 2026 over data filed in May 2026 is fine; running it over
    historical data we re-fetched + re-graded later would be a
    different question.

Parameter sweep: replay across the (min_materiality × trim_pct) grid
to surface which config WOULD have produced the best historical
outcome. Tells you whether default M≥4 / 50% is right or whether
M≥3 / 25% trim catches more signal at lower cost.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


DEFAULT_JOURNAL_DB = (Path(__file__).resolve().parent.parent.parent
                       / "data" / "journal.db")

# Same direction-gate logic as the rule itself
TRIM_DIRECTIONS = {"BEARISH", "SURPRISE"}
TRIM_SURPRISE_DIRECTIONS = {"MISSED"}


@dataclass
class TrimEvent:
    """One historical signal that would have triggered a trim under
    the backtest config."""
    rebalance_ts: str
    symbol: str
    accession: str
    filed_at: str
    materiality: int
    direction: str
    original_target_weight: float
    counterfactual_target_weight: float

    # Forward returns from rebalance_ts to T+N trading days (close-to-
    # close). Only filled when yfinance pulls succeed. None = data
    # unavailable for that horizon.
    fwd_return_5d: Optional[float] = None
    fwd_return_10d: Optional[float] = None
    fwd_return_20d: Optional[float] = None

    # P&L delta at the 20-day horizon: positive = trimming helped
    # (price fell after rebalance, less weight → less loss)
    pnl_impact_pct: Optional[float] = None


@dataclass
class BacktestResult:
    config: dict = field(default_factory=dict)
    n_rebalances_analyzed: int = 0
    rebalance_dates: list[str] = field(default_factory=list)
    n_signals_in_window: int = 0
    n_trims_triggered: int = 0
    trim_events: list[TrimEvent] = field(default_factory=list)

    # Aggregates — only set when n_trims_triggered > 0
    total_pnl_impact_pct: Optional[float] = None
    avg_fwd_return_5d: Optional[float] = None
    avg_fwd_return_20d: Optional[float] = None

    error: Optional[str] = None

    def summary(self) -> str:
        """One-line human-readable summary."""
        if self.error:
            return f"⚠️ {self.error}"
        if self.n_rebalances_analyzed == 0:
            return ("⚠️ no rebalances in journal — backtest needs "
                    "decisions rows with final LIKE 'LIVE_VARIANT_BUY%'")
        if self.n_trims_triggered == 0:
            return (
                f"No trims would have fired: scanned "
                f"{self.n_rebalances_analyzed} rebalance(s), "
                f"{self.n_signals_in_window} signals, "
                f"0 met M≥{self.config.get('min_materiality')} threshold. "
                f"Keep the reactor running and re-run as data accumulates."
            )
        sign = "+" if (self.total_pnl_impact_pct or 0) >= 0 else ""
        return (
            f"{self.n_trims_triggered} trim(s) across "
            f"{self.n_rebalances_analyzed} rebalance(s); "
            f"counterfactual P&L impact "
            f"{sign}{(self.total_pnl_impact_pct or 0)*100:+.2f}%"
        )

    def to_dict(self) -> dict:
        d = {
            "config": self.config,
            "n_rebalances_analyzed": self.n_rebalances_analyzed,
            "rebalance_dates": self.rebalance_dates,
            "n_signals_in_window": self.n_signals_in_window,
            "n_trims_triggered": self.n_trims_triggered,
            "total_pnl_impact_pct": self.total_pnl_impact_pct,
            "avg_fwd_return_5d": self.avg_fwd_return_5d,
            "avg_fwd_return_20d": self.avg_fwd_return_20d,
            "error": self.error,
            "summary": self.summary(),
            "trim_events": [
                {
                    "rebalance_ts": e.rebalance_ts, "symbol": e.symbol,
                    "accession": e.accession, "filed_at": e.filed_at,
                    "materiality": e.materiality, "direction": e.direction,
                    "original_weight": e.original_target_weight,
                    "counterfactual_weight": e.counterfactual_target_weight,
                    "fwd_return_5d": e.fwd_return_5d,
                    "fwd_return_20d": e.fwd_return_20d,
                    "pnl_impact_pct": e.pnl_impact_pct,
                }
                for e in self.trim_events
            ],
        }
        return d


def _load_rebalances(journal_db: Path) -> list[dict]:
    """Pull every historical rebalance run + its per-symbol targets.

    Returns list of {"ts": ..., "targets": {symbol: weight, ...}}.
    Parses the rebalance timestamp + final-string weight from the
    decisions table. If the same run has multiple decisions (one per
    symbol), they're grouped by exact rebalance timestamp."""
    if not journal_db.exists():
        return []
    out: dict[str, dict[str, float]] = {}
    with sqlite3.connect(f"file:{journal_db}?mode=ro", uri=True) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT ts, ticker, final FROM decisions "
            "WHERE final LIKE 'LIVE_VARIANT_BUY%' "
            "ORDER BY ts ASC"
        ).fetchall()
    for r in rows:
        ts = r["ts"]
        ticker = r["ticker"]
        final = r["final"] or ""
        # Parse "LIVE_VARIANT_BUY @ 5.5% (variant=...)"
        try:
            pct_str = final.split("@")[1].split("%")[0].strip()
            weight = float(pct_str) / 100.0
        except (IndexError, ValueError):
            continue
        # Group by rebalance ts ROUNDED TO SECOND (a single rebalance
        # writes 15 decisions with ts differing in microseconds)
        ts_key = ts[:19] if len(ts) >= 19 else ts
        if ts_key not in out:
            out[ts_key] = {}
        out[ts_key][ticker] = weight
    return [{"ts": ts, "targets": targets}
            for ts, targets in sorted(out.items())]


def _load_signals_in_window(
    journal_db: Path, since_iso: str, until_iso: str,
) -> list[dict]:
    """All earnings_signals with filed_at in [since, until]."""
    if not journal_db.exists():
        return []
    try:
        with sqlite3.connect(f"file:{journal_db}?mode=ro", uri=True) as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT symbol, accession, filed_at, materiality, "
                "direction, surprise_direction, summary, error "
                "FROM earnings_signals "
                "WHERE filed_at >= ? AND filed_at <= ? "
                "ORDER BY filed_at",
                (since_iso, until_iso),
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []  # table doesn't exist yet


def _is_trim_worthy(sig: dict, min_materiality: int) -> bool:
    if sig.get("error"):
        return False
    if (sig.get("materiality") or 0) < min_materiality:
        return False
    direction = (sig.get("direction") or "").upper()
    if direction == "BEARISH":
        return True
    if direction == "SURPRISE":
        return (sig.get("surprise_direction") or "").upper() in TRIM_SURPRISE_DIRECTIONS
    return False


def _pull_forward_returns(symbol: str,
                           rebalance_date: str,
                           horizons_days: tuple[int, ...] = (5, 10, 20),
                           ) -> dict[int, Optional[float]]:
    """Fetch forward close-to-close returns from yfinance.

    Returns {horizon_days: pct_return} for each horizon. Missing data
    (yfinance failure, future date, weekend) → None for that key.
    """
    out: dict[int, Optional[float]] = {h: None for h in horizons_days}
    try:
        import yfinance as yf
        from datetime import datetime as _dt
        try:
            start_dt = _dt.fromisoformat(rebalance_date[:10])
        except ValueError:
            return out
        # Pull a generous trailing window so we have enough trading days
        end_dt = start_dt + timedelta(days=max(horizons_days) * 2 + 7)
        df = yf.download(
            symbol, start=start_dt.strftime("%Y-%m-%d"),
            end=end_dt.strftime("%Y-%m-%d"),
            progress=False, auto_adjust=True, threads=False,
        )
        if df is None or df.empty:
            return out
        # Yahoo MultiIndex when single ticker can be flat or multi-col
        closes = df["Close"] if "Close" in df.columns else df
        if hasattr(closes, "squeeze"):
            try:
                closes = closes.squeeze()
            except Exception:
                pass
        if len(closes) < 2:
            return out
        anchor = float(closes.iloc[0])
        for h in horizons_days:
            if h < len(closes):
                out[h] = (float(closes.iloc[h]) - anchor) / anchor
    except Exception:
        return out
    return out


def replay(
    journal_db: Optional[Path] = None,
    min_materiality: int = 4,
    trim_pct: float = 0.5,
    lookback_days: int = 14,
    since_date: Optional[str] = None,
    pull_forward_prices: bool = True,
) -> BacktestResult:
    """Run the rule over every historical rebalance + return aggregated
    counterfactual P&L impact.

    `pull_forward_prices=False` skips the yfinance step (useful for
    fast unit tests; only the trim-trigger count is meaningful in
    that mode)."""
    if journal_db is None:
        journal_db = DEFAULT_JOURNAL_DB

    config = {
        "min_materiality": min_materiality,
        "trim_pct": trim_pct,
        "lookback_days": lookback_days,
        "since_date": since_date,
    }
    result = BacktestResult(config=config)

    rebalances = _load_rebalances(journal_db)
    if since_date:
        rebalances = [r for r in rebalances if r["ts"] >= since_date]
    result.n_rebalances_analyzed = len(rebalances)
    result.rebalance_dates = [r["ts"] for r in rebalances]

    if not rebalances:
        return result  # summary() handles the empty case

    # Aggregate signals across the full range so we can report
    # n_signals_in_window separately from triggers
    signals_seen: set[tuple[str, str]] = set()  # (symbol, accession)

    pnl_impacts: list[float] = []
    fwd_5d_list: list[float] = []
    fwd_20d_list: list[float] = []

    for rb in rebalances:
        rb_ts = rb["ts"]
        targets = rb["targets"]
        rb_dt = rb_ts[:10]
        try:
            rb_date = datetime.fromisoformat(rb_dt).date()
        except ValueError:
            continue
        window_start = (rb_date - timedelta(days=lookback_days)).isoformat()
        window_end = rb_date.isoformat()

        signals = _load_signals_in_window(
            journal_db, window_start, window_end)
        for s in signals:
            signals_seen.add((s["symbol"], s["accession"]))
            if not _is_trim_worthy(s, min_materiality):
                continue
            sym = s["symbol"]
            if sym not in targets:
                # Signal for a non-held position — skip
                continue
            orig = targets[sym]
            new = orig * trim_pct

            event = TrimEvent(
                rebalance_ts=rb_ts, symbol=sym,
                accession=s["accession"], filed_at=s["filed_at"],
                materiality=int(s["materiality"]),
                direction=s["direction"] or "",
                original_target_weight=orig,
                counterfactual_target_weight=new,
            )

            if pull_forward_prices:
                fwd = _pull_forward_returns(sym, rb_dt)
                event.fwd_return_5d = fwd.get(5)
                event.fwd_return_10d = fwd.get(10)
                event.fwd_return_20d = fwd.get(20)
                # P&L impact at 20d horizon: trimming saves you the
                # weight-delta × forward_return. If forward_return < 0
                # (BEARISH was right) → impact > 0 (you saved money).
                if event.fwd_return_20d is not None:
                    weight_delta = orig - new
                    saved = -weight_delta * event.fwd_return_20d
                    event.pnl_impact_pct = saved
                    pnl_impacts.append(saved)
                if event.fwd_return_5d is not None:
                    fwd_5d_list.append(event.fwd_return_5d)
                if event.fwd_return_20d is not None:
                    fwd_20d_list.append(event.fwd_return_20d)

            result.trim_events.append(event)
            result.n_trims_triggered += 1

    result.n_signals_in_window = len(signals_seen)
    if pnl_impacts:
        result.total_pnl_impact_pct = sum(pnl_impacts)
    if fwd_5d_list:
        result.avg_fwd_return_5d = sum(fwd_5d_list) / len(fwd_5d_list)
    if fwd_20d_list:
        result.avg_fwd_return_20d = sum(fwd_20d_list) / len(fwd_20d_list)

    return result


def parameter_sweep(
    journal_db: Optional[Path] = None,
    materialities: tuple[int, ...] = (3, 4, 5),
    trim_pcts: tuple[float, ...] = (0.25, 0.50, 0.75),
    lookback_days: int = 14,
    since_date: Optional[str] = None,
    pull_forward_prices: bool = True,
) -> list[BacktestResult]:
    """Sweep across the (M_threshold × trim_pct) grid."""
    out = []
    for m in materialities:
        for p in trim_pcts:
            out.append(replay(
                journal_db=journal_db,
                min_materiality=m, trim_pct=p,
                lookback_days=lookback_days,
                since_date=since_date,
                pull_forward_prices=pull_forward_prices,
            ))
    return out
