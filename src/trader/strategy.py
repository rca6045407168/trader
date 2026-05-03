"""Strategy orchestrator. Combines momentum (trend-riding) with bottom-catching."""
from dataclasses import dataclass, field
from typing import Literal
import pandas as pd

from .data import fetch_history, fetch_ohlcv
from .signals import momentum_score, bottom_catch_score, atr

Action = Literal["BUY", "SELL", "HOLD"]
Style = Literal["MOMENTUM", "BOTTOM_CATCH"]


@dataclass
class Candidate:
    ticker: str
    action: Action
    style: Style
    score: float
    rationale: dict = field(default_factory=dict)
    atr_pct: float = 0.0


def rank_momentum(
    universe: list[str],
    lookback_months: int = 12,
    skip_months: int = 1,
    top_n: int = 5,
    end_date: pd.Timestamp | str | None = None,
) -> list[Candidate]:
    """Long the top-N stocks by trailing momentum, skipping the most recent month.

    v3.59.4: end_date enables AS-OF backtesting and unlocks the determinism
    test (Cat 9 in TESTING_PRACTICES). When None (default), uses today's
    date — original behavior. When set, returns the picks the strategy
    WOULD HAVE made on that date using only data up to that date.

    The momentum_score and ATR are both computed on the price series clipped
    at end_date, so no look-ahead.
    """
    if end_date is None:
        end = pd.Timestamp.today()
    else:
        end = pd.Timestamp(end_date) if not isinstance(end_date, pd.Timestamp) else end_date
    start = (end - pd.DateOffset(months=lookback_months + skip_months + 2)).strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")
    # Pull history through end_date (inclusive). fetch_history accepts a
    # start; we slice afterwards to ensure no future-data leak.
    prices = fetch_history(universe, start=start)
    if end_date is not None and not prices.empty:
        # Strip any rows after end_date — defensive against fetch_history
        # returning extra trailing data.
        prices = prices[prices.index <= end]

    scored = []
    for t in universe:
        if t not in prices.columns:
            continue
        s = prices[t].dropna()
        m = momentum_score(s, lookback_months, skip_months)
        if not pd.isna(m):
            scored.append((t, m))

    scored.sort(key=lambda x: x[1], reverse=True)
    out: list[Candidate] = []
    for t, m in scored[:top_n]:
        atr_pct = 0.0
        try:
            ohlc = fetch_ohlcv(t, start=start)
            if end_date is not None and not ohlc.empty:
                ohlc = ohlc[ohlc.index <= end]
            if not ohlc.empty:
                a = atr(ohlc)
                last_close = float(ohlc["Close"].iloc[-1])
                atr_pct = a / last_close if last_close else 0.0
        except Exception:
            pass
        out.append(
            Candidate(
                ticker=t,
                action="BUY",
                style="MOMENTUM",
                score=m,
                rationale={"trailing_return": round(m, 4),
                            "lookback_months": lookback_months,
                            "as_of": end_str},
                atr_pct=atr_pct,
            )
        )
    return out


def find_bottoms(
    universe: list[str],
    min_score: float = 0.65,
    max_candidates: int = 10,
) -> list[Candidate]:
    """Scan the universe for high-confluence oversold-bounce setups."""
    end = pd.Timestamp.today()
    start = (end - pd.DateOffset(months=14)).strftime("%Y-%m-%d")
    out: list[Candidate] = []
    for t in universe:
        try:
            ohlc = fetch_ohlcv(t, start=start)
            score, comp = bottom_catch_score(ohlc)
            if score < min_score:
                continue
            a = atr(ohlc)
            last = float(ohlc["Close"].iloc[-1])
            atr_pct = a / last if last else 0.0
            out.append(
                Candidate(
                    ticker=t,
                    action="BUY",
                    style="BOTTOM_CATCH",
                    score=score,
                    rationale={k: (round(v, 3) if isinstance(v, float) else v) for k, v in comp.items()},
                    atr_pct=atr_pct,
                )
            )
        except Exception:
            continue
    out.sort(key=lambda c: c.score, reverse=True)
    return out[:max_candidates]
