"""Live position monitor (v3.52.0 / Bloomberg MON-style).

The single highest-value Bloomberg-inspired addition for our dashboard.
Between monthly rebalances, THIS is the trading view.

Pulls current Alpaca positions, enriches with last-trade prices and
yesterday's close, computes per-name day P&L + total P&L. Cached 30s
so the dashboard auto-refresh doesn't hammer the broker API.

Returns a list of LivePosition dicts (or empty list with error_msg if
the broker call fails). Never raises; always returns a structured result
so the dashboard can render gracefully.

Cost model: Alpaca paper API has no rate limit issues at 30s cadence; we
use get_all_positions() + one get_stock_latest_trade() per position.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class LivePosition:
    symbol: str
    qty: float
    avg_cost: float
    last_price: Optional[float] = None
    day_open_price: Optional[float] = None
    market_value: Optional[float] = None
    unrealized_pl: Optional[float] = None
    unrealized_pl_pct: Optional[float] = None
    day_pl_dollar: Optional[float] = None
    day_pl_pct: Optional[float] = None
    weight_of_book: Optional[float] = None  # market_value / total_equity
    sector: Optional[str] = None


@dataclass
class LivePortfolio:
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    equity: Optional[float] = None
    cash: Optional[float] = None
    buying_power: Optional[float] = None
    positions: list[LivePosition] = field(default_factory=list)
    total_unrealized_pl: float = 0.0
    total_day_pl_dollar: float = 0.0
    total_day_pl_pct: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "equity": self.equity,
            "cash": self.cash,
            "buying_power": self.buying_power,
            "positions": [vars(p) for p in self.positions],
            "total_unrealized_pl": self.total_unrealized_pl,
            "total_day_pl_dollar": self.total_day_pl_dollar,
            "total_day_pl_pct": self.total_day_pl_pct,
            "error": self.error,
        }


def fetch_live_portfolio() -> LivePortfolio:
    """One snapshot of the live portfolio. Safe to call from dashboard
    auto-refresh — caches at the dashboard layer (st.cache_data ttl=30).
    """
    out = LivePortfolio()
    try:
        from .execute import get_client, get_last_price
        from .sectors import get_sector
    except Exception as e:
        out.error = f"import failed: {e}"
        return out

    try:
        client = get_client()
        acct = client.get_account()
        out.equity = float(acct.equity)
        out.cash = float(acct.cash)
        out.buying_power = float(getattr(acct, "buying_power", 0) or 0)
        positions_raw = client.get_all_positions()
    except Exception as e:
        out.error = f"broker fetch: {type(e).__name__}: {e}"
        return out

    yest_close_cache = _yesterday_closes([p.symbol for p in positions_raw])

    total_un_pl = 0.0
    total_day_pl = 0.0
    yesterday_equity_estimate = 0.0

    for p in positions_raw:
        sym = p.symbol
        qty = float(p.qty)
        avg_cost = float(p.avg_entry_price)
        try:
            last = get_last_price(sym)
        except Exception:
            last = None
        market_value = (last * qty) if last else float(p.market_value or 0)
        un_pl = market_value - (avg_cost * qty) if last else float(p.unrealized_pl or 0)
        un_pl_pct = (un_pl / (avg_cost * qty)) if avg_cost and qty else None

        yest_close = yest_close_cache.get(sym)
        day_pl_dollar = None
        day_pl_pct = None
        if last and yest_close and yest_close > 0:
            day_pl_dollar = (last - yest_close) * qty
            day_pl_pct = (last - yest_close) / yest_close
            yesterday_equity_estimate += yest_close * qty

        try:
            sector = get_sector(sym)
        except Exception:
            sector = "Unknown"

        weight = market_value / out.equity if out.equity and out.equity > 0 else None

        out.positions.append(LivePosition(
            symbol=sym, qty=qty, avg_cost=avg_cost,
            last_price=last, day_open_price=yest_close,
            market_value=market_value,
            unrealized_pl=un_pl, unrealized_pl_pct=un_pl_pct,
            day_pl_dollar=day_pl_dollar, day_pl_pct=day_pl_pct,
            weight_of_book=weight, sector=sector,
        ))
        total_un_pl += un_pl or 0.0
        if day_pl_dollar is not None:
            total_day_pl += day_pl_dollar

    out.total_unrealized_pl = total_un_pl
    out.total_day_pl_dollar = total_day_pl
    if yesterday_equity_estimate > 0 and out.cash is not None:
        # equity yesterday ≈ cash + sum(qty * yest_close); approximate (cash drifts intra-day)
        approx_yest_eq = (out.cash or 0) + yesterday_equity_estimate
        if approx_yest_eq > 0:
            out.total_day_pl_pct = total_day_pl / approx_yest_eq
    out.positions.sort(key=lambda x: -(x.market_value or 0))
    return out


def _yesterday_closes(symbols: list[str]) -> dict[str, float]:
    """Last-2-day closes via yfinance, returns {symbol: yesterday_close}.
    Best-effort; missing symbols just don't get day P&L populated.
    """
    if not symbols:
        return {}
    try:
        import yfinance as yf
        # batch call: yf accepts space-separated tickers
        df = yf.download(" ".join(symbols), period="5d",
                          progress=False, auto_adjust=True, group_by="ticker")
        if df is None or df.empty:
            return {}
        out = {}
        for sym in symbols:
            try:
                if len(symbols) == 1:
                    closes = df["Close"].dropna()
                else:
                    closes = df[(sym, "Close")].dropna() if (sym, "Close") in df.columns else df[sym]["Close"].dropna()
                if len(closes) >= 2:
                    # Yesterday's close = second-to-last value (most recent finished session)
                    out[sym] = float(closes.iloc[-2])
                elif len(closes) >= 1:
                    out[sym] = float(closes.iloc[-1])
            except Exception:
                continue
        return out
    except Exception:
        return {}
