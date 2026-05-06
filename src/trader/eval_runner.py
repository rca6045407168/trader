"""v3.73.7 — Constant-evaluation runner.

Runs every registered strategy on the same point-in-time prices and
journals its picks + period-forward returns. The harness is what
turns "we have 10 strategies" into "we know which one is winning."

Two operations:

  1. evaluate_at(asof) — for each strategy, compute the picks it
     WOULD have made AS-OF asof. Persist (asof, strategy, picks_json)
     to journal.strategy_eval. Idempotent on (asof, strategy).

  2. settle_returns(period_end) — for every (asof, strategy) row
     where forward returns are unknown, compute the holding-period
     return from asof → period_end and persist.

Designed to run as part of the daily orchestrator: at end-of-day,
(a) settle yesterday's evaluations against today's close, then
(b) record today's picks for every strategy.

Schema (auto-created on first run):

    CREATE TABLE strategy_eval (
        id INTEGER PRIMARY KEY,
        asof TEXT,                  -- date the picks would have been made
        strategy TEXT,              -- name in eval_strategies registry
        picks_json TEXT,            -- {ticker: weight}
        n_picks INTEGER,
        period_end TEXT,            -- date the return is settled to (NULL until settled)
        period_return REAL,         -- portfolio return over [asof, period_end]
        spy_return REAL,            -- SPY return over same window
        active_return REAL,
        created_at TEXT,
        UNIQUE(asof, strategy)
    )

The dashboard reads this to render the leaderboard.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd

DEFAULT_JOURNAL_DB = Path(__file__).resolve().parent.parent.parent / "data" / "journal.db"

# v3.73.9 — Transaction-cost model. Charges per rebalance based on
# turnover (= sum of |new_weight - prior_weight|). 5bps round-trip
# per dollar of turnover is the typical model for liquid US large-caps
# at retail size. SPY ETF charges ~0bps (it's the benchmark; assume
# costless). This makes the leaderboard apples-to-apples — strategies
# that trade more pay more.
DEFAULT_TURNOVER_COST_BPS = 5.0


# ============================================================
# Schema
# ============================================================
def ensure_schema(db_path: Path = DEFAULT_JOURNAL_DB) -> None:
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE IF NOT EXISTS strategy_eval (
            id INTEGER PRIMARY KEY,
            asof TEXT NOT NULL,
            strategy TEXT NOT NULL,
            picks_json TEXT NOT NULL,
            n_picks INTEGER NOT NULL,
            period_end TEXT,
            period_return REAL,
            spy_return REAL,
            active_return REAL,
            created_at TEXT NOT NULL,
            UNIQUE(asof, strategy)
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_eval_strategy_asof "
        "ON strategy_eval(strategy, asof)"
    )
    con.commit()
    con.close()


# ============================================================
# Record picks
# ============================================================
def evaluate_at(
    asof: pd.Timestamp,
    universe: list[str],
    prices: Optional[pd.DataFrame] = None,
    db_path: Path = DEFAULT_JOURNAL_DB,
) -> int:
    """Run every registered strategy at `asof` and persist picks.
    Returns count of strategies recorded. Idempotent on (asof, strategy)."""
    from . import eval_strategies  # registers on import
    from .data import fetch_history

    ensure_schema(db_path)

    if prices is None:
        end = asof if isinstance(asof, pd.Timestamp) else pd.Timestamp(asof)
        start = (end - pd.DateOffset(months=18)).strftime("%Y-%m-%d")
        prices = fetch_history(universe, start=start)
        prices = prices.dropna(axis=1, how="any")
        if not prices.empty and end in prices.index:
            prices = prices[prices.index <= end]

    asof_str = asof.strftime("%Y-%m-%d") if isinstance(asof, pd.Timestamp) else str(asof)
    now = datetime.datetime.utcnow().isoformat()
    n = 0
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    for spec in eval_strategies.all_strategies():
        try:
            picks = spec.fn(asof, prices)
        except Exception as e:
            picks = {"_error": str(e)}
        # v3.73.13 CORRECTNESS FIX: don't journal empty picks. Empty
        # picks at the START of a backtest (before 12mo of history is
        # available to score momentum) caused the leaderboard to count
        # those periods as "0% portfolio vs SPY moving" — inflating
        # cum_active by the SPY drag during the warmup window. Rows
        # with errors (`_error`) are still journaled so the operator
        # sees them.
        n_real = len([t for t in picks if not t.startswith("_")])
        if n_real == 0 and "_error" not in picks:
            continue
        cur.execute(
            """INSERT OR IGNORE INTO strategy_eval
               (asof, strategy, picks_json, n_picks, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (asof_str, spec.name, json.dumps(picks),
             n_real, now),
        )
        n += cur.rowcount
    con.commit()
    con.close()
    return n


# ============================================================
# Settle returns
# ============================================================
def _prior_picks(cur, strategy: str, asof: str) -> dict:
    """Return the most-recent prior picks dict for `strategy` strictly
    before `asof`. Used to compute turnover for the cost model."""
    row = cur.execute(
        "SELECT picks_json FROM strategy_eval WHERE strategy=? AND asof<? "
        "ORDER BY asof DESC LIMIT 1",
        (strategy, asof),
    ).fetchone()
    if not row:
        return {}
    try:
        prior = json.loads(row[0])
        return {k: v for k, v in prior.items() if not k.startswith("_")}
    except Exception:
        return {}


def _turnover(prior: dict, current: dict) -> float:
    """Sum of |new_weight - prior_weight| across all tickers (one-side
    turnover). Range: [0, 2 * gross]."""
    syms = set(prior) | set(current)
    return sum(abs(current.get(s, 0.0) - prior.get(s, 0.0)) for s in syms)


def settle_returns(
    period_end: pd.Timestamp,
    prices: Optional[pd.DataFrame] = None,
    spy_close: Optional[float] = None,
    cost_bps: float = DEFAULT_TURNOVER_COST_BPS,
    db_path: Path = DEFAULT_JOURNAL_DB,
) -> int:
    """Settle the period_return + spy_return + active_return for every
    unsettled row. Caller passes period_end (typically today's close
    date); we look up the asof_close and period_end_close per name.

    Returns count of rows settled.
    """
    from .data import fetch_history

    ensure_schema(db_path)
    end = period_end if isinstance(period_end, pd.Timestamp) else pd.Timestamp(period_end)
    end_str = end.strftime("%Y-%m-%d")

    con = sqlite3.connect(db_path)
    cur = con.cursor()
    rows = cur.execute(
        "SELECT id, asof, strategy, picks_json FROM strategy_eval "
        "WHERE period_end IS NULL AND asof < ?",
        (end_str,),
    ).fetchall()

    if not rows:
        con.close()
        return 0

    # Collect every ticker we need + the asof dates
    asof_dates = set(r[1] for r in rows)
    all_tickers = set()
    for _, _, _, pj in rows:
        d = json.loads(pj)
        all_tickers.update(t for t in d if not t.startswith("_"))
    all_tickers.add("SPY")  # for benchmark

    # Pull prices spanning earliest asof to period_end
    earliest = min(asof_dates)
    if prices is None:
        start = (pd.Timestamp(earliest) - pd.DateOffset(days=10)).strftime("%Y-%m-%d")
        prices = fetch_history(list(all_tickers), start=start)

    n = 0
    for row_id, asof, strategy, pj in rows:
        picks = json.loads(pj)
        if "_error" in picks:
            cur.execute(
                "UPDATE strategy_eval SET period_end=?, period_return=NULL, "
                "spy_return=NULL, active_return=NULL WHERE id=?",
                (end_str, row_id),
            )
            continue
        try:
            asof_ts = pd.Timestamp(asof)
            # Find first available trading-day price >= asof (asof
            # close) and last <= end (period close)
            def _close(sym, target_lo, target_hi):
                if sym not in prices.columns:
                    return None
                s = prices[sym].dropna()
                lo = s[s.index >= target_lo]
                hi = s[s.index <= target_hi]
                if lo.empty or hi.empty:
                    return None, None
                return float(lo.iloc[0]), float(hi.iloc[-1])

            port_ret = 0.0
            priced_any = False
            for ticker, weight in picks.items():
                p0p1 = _close(ticker, asof_ts, end)
                if not p0p1:
                    continue
                p0, p1 = p0p1
                if p0 > 0:
                    port_ret += weight * (p1 / p0 - 1)
                    priced_any = True

            spy_p = _close("SPY", asof_ts, end)
            spy_priced = bool(spy_p)
            if spy_p:
                p0, p1 = spy_p
                spy_ret = (p1 / p0 - 1) if p0 > 0 else 0.0
            elif spy_close is not None:
                spy_ret = spy_close
                spy_priced = True
            else:
                spy_ret = 0.0

            # If we couldn't price ANYTHING (no picks priced AND no
            # SPY data), don't pretend we settled — leave the row
            # unsettled so a later call can retry with better data.
            if not priced_any and not spy_priced:
                continue

            # v3.73.9: charge transaction cost based on turnover from
            # prior picks. cost_bps applied to one-side turnover (5bps
            # round-trip on US large caps is conservative-realistic).
            # SPY assumed costless (it's the benchmark via ETF).
            prior = _prior_picks(cur, strategy, asof)
            turnover = _turnover(prior, picks)
            net_cost = turnover * (cost_bps / 10000.0)
            net_port_ret = port_ret - net_cost

            active = net_port_ret - spy_ret

            cur.execute(
                "UPDATE strategy_eval SET period_end=?, period_return=?, "
                "spy_return=?, active_return=? WHERE id=?",
                (end_str, net_port_ret, spy_ret, active, row_id),
            )
            n += 1
        except Exception:
            continue

    con.commit()
    con.close()
    return n


# ============================================================
# Read leaderboard
# ============================================================
def leaderboard(
    db_path: Path = DEFAULT_JOURNAL_DB,
    days_back: int = 30,
) -> list[dict]:
    """Aggregate per-strategy stats over the last N days of settled
    evaluations: count, mean active return, win rate, IR, cum return."""
    ensure_schema(db_path)
    cutoff = (datetime.date.today() - datetime.timedelta(days=days_back)).isoformat()
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    rows = cur.execute(
        """SELECT strategy, asof, period_return, spy_return, active_return
           FROM strategy_eval
           WHERE period_end IS NOT NULL
             AND asof >= ?
             AND active_return IS NOT NULL""",
        (cutoff,),
    ).fetchall()
    con.close()

    grouped: dict[str, list[tuple]] = {}
    for s, a, p, sp, ar in rows:
        grouped.setdefault(s, []).append((a, p, sp, ar))

    out = []
    for strategy, recs in grouped.items():
        active = [r[3] for r in recs]
        port = [r[1] for r in recs]
        spy = [r[2] for r in recs]
        n = len(active)
        if n == 0:
            continue
        mean_active = sum(active) / n
        wins = sum(1 for a in active if a > 0)
        cum_port = 1.0
        cum_spy = 1.0
        for p, s in zip(port, spy):
            cum_port *= (1 + (p or 0))
            cum_spy *= (1 + (s or 0))
        if n > 1:
            mean_a = mean_active
            sd_a = (sum((a - mean_a) ** 2 for a in active) / (n - 1)) ** 0.5
        else:
            sd_a = 0
        # v3.73.13 BUGFIX: monthly returns annualize with sqrt(12),
        # NOT sqrt(252). Earlier code used sqrt(252) — overstated IR
        # by sqrt(252/12) ≈ 4.58x for the entire v3.73.7 → v3.73.12
        # leaderboard. Caught by the cross-validation harness.
        ir = (mean_active / sd_a * (12 ** 0.5)) if sd_a > 0 else 0
        out.append({
            "strategy": strategy,
            "n_obs": n,
            "cum_active_pct": (cum_port - cum_spy) * 100,
            "cum_port_pct": (cum_port - 1) * 100,
            "cum_spy_pct": (cum_spy - 1) * 100,
            "mean_active_pct": mean_active * 100,
            "win_rate": wins / n,
            "ir": ir,
        })
    out.sort(key=lambda x: -x["cum_active_pct"])
    return out
