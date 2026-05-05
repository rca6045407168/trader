"""v3.73.6 — Benchmark-relative performance tracking.

The goal of the system is to beat SP500. Without benchmark-relative
metrics, we can't tell whether we're winning or losing on the only
question that matters. This module is the measurement layer.

Three data flows:

  1. Backfill: pull Alpaca's portfolio/history endpoint (daily equity
     for up to 12 months) + SPY close on the same dates, persist to
     daily_snapshot. One-time bootstrap; subsequent runs are
     incremental.

  2. Compute: given the (date, equity, spy_close) time series,
     produce active return, tracking error, information ratio, beta,
     alpha, max relative drawdown.

  3. Render: the Overview view's headline gets a NAV-vs-SPY chart +
     KPI tile (active return YTD, rolling-30d IR, alpha vs SPY).

The metrics are intentionally those an allocator would ask for —
not what's pretty.
"""
from __future__ import annotations

import datetime
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_JOURNAL_DB = Path(__file__).resolve().parent.parent.parent / "data" / "journal.db"


# ============================================================
# Data fetch
# ============================================================
def fetch_portfolio_history(period: str = "6M") -> list[tuple[datetime.date, float]]:
    """Pull Alpaca's daily equity time series. Returns list of
    (date, equity) tuples. Filters out pre-funding zero rows.

    Period: '1M', '3M', '6M', '1Y', 'all' per Alpaca's API.
    """
    import requests

    key = os.environ.get("ALPACA_API_KEY")
    sec = os.environ.get("ALPACA_API_SECRET")
    if not (key and sec):
        return []

    base = ("https://paper-api.alpaca.markets/v2"
            if os.environ.get("ALPACA_PAPER", "true").lower() == "true"
            else "https://api.alpaca.markets/v2")
    r = requests.get(
        f"{base}/account/portfolio/history",
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec},
        params={"period": period, "timeframe": "1D"},
        timeout=15,
    )
    if r.status_code != 200:
        return []
    data = r.json()
    out: list[tuple[datetime.date, float]] = []
    for ts, eq in zip(data.get("timestamp", []), data.get("equity", [])):
        if eq and eq > 0:
            d = datetime.datetime.fromtimestamp(ts).date()
            out.append((d, float(eq)))
    return out


def fetch_spy_closes(
    dates: list[datetime.date],
) -> dict[datetime.date, float]:
    """Pull SPY closes for the given dates via yfinance. Returns
    {date: close} for dates where data is available."""
    if not dates:
        return {}
    try:
        import yfinance as yf
    except ImportError:
        return {}
    start = min(dates).strftime("%Y-%m-%d")
    end = (max(dates) + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    df = yf.download("SPY", start=start, end=end, progress=False, auto_adjust=True)
    if df is None or df.empty:
        return {}
    out: dict[datetime.date, float] = {}
    closes = df["Close"]
    # yfinance can return MultiIndex columns; normalize
    if hasattr(closes, "iloc") and len(closes.shape) > 1:
        closes = closes.iloc[:, 0]
    for ts, price in closes.items():
        d = ts.date() if hasattr(ts, "date") else ts
        out[d] = float(price)
    return out


# ============================================================
# Persistence
# ============================================================
def backfill_journal(
    db_path: Path = DEFAULT_JOURNAL_DB,
    period: str = "6M",
) -> int:
    """One-shot backfill of daily_snapshot from Alpaca + yfinance.
    Returns count of rows upserted. Idempotent."""
    history = fetch_portfolio_history(period)
    if not history:
        return 0
    spy = fetch_spy_closes([d for d, _ in history])
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    n = 0
    for d, eq in history:
        spy_close = spy.get(d, 0.0)
        cur.execute(
            """INSERT OR REPLACE INTO daily_snapshot
               (date, equity, cash, positions_json, benchmark_spy_close)
               VALUES (?, ?, ?, ?, ?)""",
            (d.isoformat(), eq, 0.0, "{}", spy_close),
        )
        n += 1
    con.commit()
    con.close()
    return n


def load_snapshots(
    db_path: Path = DEFAULT_JOURNAL_DB,
) -> list[tuple[datetime.date, float, float]]:
    """Load (date, equity, spy_close) snapshots in chronological order."""
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    rows = cur.execute(
        "SELECT date, equity, benchmark_spy_close FROM daily_snapshot "
        "WHERE benchmark_spy_close > 0 ORDER BY date ASC"
    ).fetchall()
    con.close()
    return [
        (datetime.date.fromisoformat(d), float(e), float(s))
        for d, e, s in rows
    ]


# ============================================================
# Metrics — what an allocator asks for
# ============================================================
@dataclass
class BenchmarkMetrics:
    period_days: int
    portfolio_return_pct: float
    benchmark_return_pct: float
    active_return_pct: float           # portfolio - benchmark
    tracking_error_annualized: float   # σ of daily active return × √252
    information_ratio: float           # mean(active) / σ(active) × √252
    beta: float                         # cov(port, bench) / var(bench)
    alpha_annualized: float            # mean(port) - β × mean(bench), annualized
    max_relative_drawdown: float        # worst sustained underperformance
    correlation: float
    win_rate: float                    # fraction of days port > bench
    is_winning: bool                   # cum active return > 0


def compute_metrics(
    snapshots: list[tuple[datetime.date, float, float]],
) -> Optional[BenchmarkMetrics]:
    """Compute the suite of benchmark-relative metrics. Needs at
    least 5 snapshots to be meaningful; returns None below that."""
    if len(snapshots) < 5:
        return None

    # Daily returns
    port_rets, bench_rets = [], []
    for i in range(1, len(snapshots)):
        _, e0, s0 = snapshots[i-1]
        _, e1, s1 = snapshots[i]
        if e0 > 0 and s0 > 0:
            port_rets.append(e1 / e0 - 1)
            bench_rets.append(s1 / s0 - 1)
    if len(port_rets) < 4:
        return None

    n = len(port_rets)
    active = [p - b for p, b in zip(port_rets, bench_rets)]
    mean_p = sum(port_rets) / n
    mean_b = sum(bench_rets) / n
    mean_a = sum(active) / n
    var_p = sum((x - mean_p) ** 2 for x in port_rets) / max(n - 1, 1)
    var_b = sum((x - mean_b) ** 2 for x in bench_rets) / max(n - 1, 1)
    var_a = sum((x - mean_a) ** 2 for x in active) / max(n - 1, 1)
    cov_pb = sum((p - mean_p) * (b - mean_b) for p, b in zip(port_rets, bench_rets)) / max(n - 1, 1)

    sd_p = var_p ** 0.5
    sd_b = var_b ** 0.5
    sd_a = var_a ** 0.5
    beta = cov_pb / var_b if var_b > 0 else 0.0
    correlation = cov_pb / (sd_p * sd_b) if (sd_p * sd_b) > 0 else 0.0

    # Cumulative
    cum_port = 1.0
    cum_bench = 1.0
    for p, b in zip(port_rets, bench_rets):
        cum_port *= (1 + p)
        cum_bench *= (1 + b)
    port_return_pct = (cum_port - 1) * 100
    bench_return_pct = (cum_bench - 1) * 100
    active_return_pct = port_return_pct - bench_return_pct

    # IR + TE annualized (252 trading days)
    SQRT_252 = 252 ** 0.5
    te_ann = sd_a * SQRT_252
    ir = (mean_a / sd_a) * SQRT_252 if sd_a > 0 else 0.0

    # Alpha (Jensen's): port - β × bench, annualized
    alpha_daily = mean_p - beta * mean_b
    alpha_ann = alpha_daily * 252

    # Max relative drawdown: cumulative portfolio_NAV / benchmark_NAV
    rel_eq = []
    cp = cb = 1.0
    for p, b in zip(port_rets, bench_rets):
        cp *= (1 + p); cb *= (1 + b)
        rel_eq.append(cp / cb if cb > 0 else 1.0)
    peak = rel_eq[0]
    max_dd = 0.0
    for r in rel_eq:
        if r > peak: peak = r
        dd = r / peak - 1
        if dd < max_dd: max_dd = dd

    win_rate = sum(1 for a in active if a > 0) / len(active)

    return BenchmarkMetrics(
        period_days=len(snapshots),
        portfolio_return_pct=port_return_pct,
        benchmark_return_pct=bench_return_pct,
        active_return_pct=active_return_pct,
        tracking_error_annualized=te_ann * 100,
        information_ratio=ir,
        beta=beta,
        alpha_annualized=alpha_ann * 100,
        max_relative_drawdown=max_dd * 100,
        correlation=correlation,
        win_rate=win_rate,
        is_winning=active_return_pct > 0,
    )


def nav_series_for_chart(
    snapshots: list[tuple[datetime.date, float, float]],
) -> tuple[list[datetime.date], list[float], list[float]]:
    """Returns (dates, port_nav_normalized_to_100, spy_nav_normalized_to_100).
    Both series start at 100 on the first snapshot date so they're
    comparable on a single axis."""
    if not snapshots:
        return [], [], []
    dates = [d for d, _, _ in snapshots]
    e0 = snapshots[0][1]
    s0 = snapshots[0][2]
    port = [e / e0 * 100 for _, e, _ in snapshots]
    spy = [s / s0 * 100 for _, _, s in snapshots] if s0 > 0 else [100.0] * len(snapshots)
    return dates, port, spy
