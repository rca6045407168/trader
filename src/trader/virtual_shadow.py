"""[v3.59.0 — V5 Phase 3] Virtual shadow portfolio infrastructure.

Lets you run N candidate sleeves IN-PROCESS against LIVE-executed fill
prices, with no duplicate broker calls and no duplicate API costs.
Each shadow maintains its own notional book in SQLite; reconcile + P&L
attribution happens against the shared fill stream.

This is the "5x discovery velocity" infrastructure piece from the V5
proposal — without it, every candidate sleeve needs its own paper-
trading account and 30-day shadow window in wall-clock time. With it,
you can run 5 candidates in parallel against the same fills.

Usage:

    from trader.virtual_shadow import register_shadow, on_fill, equity_curve

    # At system bootstrap, register sleeves you want to track:
    register_shadow("residual_momentum_v1", initial_equity=10_000)
    register_shadow("low_vol_60d", initial_equity=10_000)

    # In execute.place_target_weights, after every successful fill:
    on_fill(symbol="NVDA", side="buy", qty=10, price=145.50,
            decision_mid=145.40, ts="2026-05-03T13:35:00Z")

    # Each shadow's on_fill() callback is invoked. Shadows that DON'T
    # want this fill simply return without action; shadows that do
    # update their internal book.

    # Periodically (daily, in prewarm or a cron):
    curve = equity_curve("residual_momentum_v1")  # → list[(date, equity)]

Design constraints:

  • Zero broker calls. on_fill() is called BY execute.py with the real
    fill data; shadows never submit orders.
  • Zero LIVE side effects. A shadow exception must never block the
    actual rebalance. on_fill() catches every shadow error.
  • Idempotent. Same fill-id called twice produces one ledger row.
  • Persistent across container restarts. Books stored in
    data/virtual_shadows/<sleeve_id>.json (small enough to be JSON;
    upgrade to per-sleeve SQLite if any sleeve > 10k rows).

What this module does NOT do:

  • Compute factor signals. That's the sleeve's job — the shadow just
    BOOKS what the sleeve says it would have done.
  • Execute. Shadows are pure ledgers.
  • Auto-promote. The user inspects equity_curve() and decides whether
    to wire a shadow's logic into LIVE.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional


DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "virtual_shadows"
DATA_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class ShadowBook:
    sleeve_id: str
    initial_equity: float
    cash: float = 0.0
    positions: dict[str, dict] = field(default_factory=dict)  # {sym: {qty, avg_cost}}
    fills: list[dict] = field(default_factory=list)  # ledger
    equity_history: list[dict] = field(default_factory=list)  # [{date, equity}]

    @property
    def file(self) -> Path:
        return DATA_DIR / f"{self.sleeve_id}.json"

    def save(self):
        try:
            self.file.write_text(json.dumps(asdict(self), indent=2, default=str))
        except Exception:
            pass

    @classmethod
    def load(cls, sleeve_id: str) -> Optional["ShadowBook"]:
        f = DATA_DIR / f"{sleeve_id}.json"
        if not f.exists():
            return None
        try:
            d = json.loads(f.read_text())
            return cls(**d)
        except Exception:
            return None


_REGISTRY_LOCK = threading.Lock()
_REGISTRY: dict[str, "ShadowBook"] = {}
# Each sleeve registers a should_take(symbol, side, ts) -> bool callback
# so shadows can selectively take fills (e.g. low-vol sleeve only takes
# fills on the 15 lowest-vol names today).
_FILTERS: dict[str, Callable[[str, str, str], bool]] = {}


def register_shadow(sleeve_id: str, initial_equity: float = 10_000.0,
                     should_take: Optional[Callable[[str, str, str], bool]] = None):
    """Register a shadow sleeve. Idempotent — re-registering reloads
    from disk (preserves ledger across container restarts)."""
    with _REGISTRY_LOCK:
        existing = ShadowBook.load(sleeve_id)
        if existing:
            _REGISTRY[sleeve_id] = existing
        else:
            book = ShadowBook(sleeve_id=sleeve_id, initial_equity=initial_equity,
                               cash=initial_equity)
            book.save()
            _REGISTRY[sleeve_id] = book
        if should_take is not None:
            _FILTERS[sleeve_id] = should_take


def list_shadows() -> list[str]:
    return sorted(_REGISTRY.keys())


def get_book(sleeve_id: str) -> Optional[ShadowBook]:
    return _REGISTRY.get(sleeve_id)


def on_fill(symbol: str, side: str, qty: float, price: float,
             decision_mid: Optional[float] = None,
             ts: Optional[str] = None,
             fill_id: Optional[str] = None):
    """Notify every registered shadow of a real fill.

    Each shadow's filter callback decides whether to book it. Errors in
    one shadow never propagate to others or to the caller (LIVE
    execute.py).
    """
    ts = ts or datetime.utcnow().isoformat()
    fill_id = fill_id or f"{symbol}-{side}-{ts}"
    with _REGISTRY_LOCK:
        for sleeve_id, book in _REGISTRY.items():
            try:
                # Idempotency check
                if any(f.get("fill_id") == fill_id for f in book.fills[-50:]):
                    continue
                # Filter
                taker = _FILTERS.get(sleeve_id)
                if taker is not None:
                    try:
                        if not taker(symbol, side, ts):
                            continue
                    except Exception:
                        continue
                # Book the fill
                _book_fill(book, symbol, side, qty, price, ts, fill_id, decision_mid)
                book.save()
            except Exception:
                # Never let a shadow break LIVE
                continue


def _book_fill(book: ShadowBook, symbol: str, side: str, qty: float,
                price: float, ts: str, fill_id: str,
                decision_mid: Optional[float]):
    notional = qty * price
    if side.lower() in ("buy", "b"):
        book.cash -= notional
        pos = book.positions.get(symbol, {"qty": 0.0, "avg_cost": 0.0})
        new_qty = pos["qty"] + qty
        if new_qty > 0:
            new_cost = (pos["qty"] * pos["avg_cost"] + qty * price) / new_qty
            book.positions[symbol] = {"qty": new_qty, "avg_cost": new_cost}
        else:
            book.positions.pop(symbol, None)
    else:  # sell
        book.cash += notional
        pos = book.positions.get(symbol)
        if pos:
            new_qty = pos["qty"] - qty
            if new_qty <= 1e-6:
                book.positions.pop(symbol, None)
            else:
                book.positions[symbol] = {"qty": new_qty,
                                            "avg_cost": pos["avg_cost"]}
    book.fills.append({
        "fill_id": fill_id, "ts": ts, "symbol": symbol,
        "side": side, "qty": qty, "price": price,
        "decision_mid": decision_mid,
    })


def mark_to_market(sleeve_id: str, prices: dict[str, float],
                     date_str: Optional[str] = None) -> Optional[float]:
    """Compute equity = cash + sum(qty * price) and append to history.
    `prices` is {symbol: latest_price}. Returns the new equity, or None
    if the shadow doesn't exist."""
    book = _REGISTRY.get(sleeve_id)
    if not book:
        return None
    date_str = date_str or datetime.utcnow().date().isoformat()
    pos_value = sum(p["qty"] * prices.get(s, p["avg_cost"])
                    for s, p in book.positions.items())
    equity = book.cash + pos_value
    book.equity_history = [h for h in book.equity_history
                            if h.get("date") != date_str]
    book.equity_history.append({"date": date_str, "equity": equity})
    book.equity_history.sort(key=lambda h: h["date"])
    book.save()
    return equity


def equity_curve(sleeve_id: str) -> list[tuple[str, float]]:
    """[(date, equity)] sorted ascending. Empty list if no shadow."""
    book = _REGISTRY.get(sleeve_id)
    if not book:
        return []
    return [(h["date"], float(h["equity"])) for h in book.equity_history]


def reset_shadow(sleeve_id: str) -> bool:
    """Wipe a shadow's book back to initial_equity. Useful when iterating
    on a sleeve's logic. Returns True if reset, False if not found."""
    book = _REGISTRY.get(sleeve_id)
    if not book:
        return False
    fresh = ShadowBook(sleeve_id=sleeve_id,
                       initial_equity=book.initial_equity,
                       cash=book.initial_equity)
    _REGISTRY[sleeve_id] = fresh
    fresh.save()
    return True
