"""Sector x weight x day-move heatmap data prep (Bloomberg IMAP-style).

Returns a dict suitable for plotly.express.treemap, with the visual
property that:
  - Tile size = position weight in the book
  - Tile color = day P&L %  (red → white → green)
  - Tiles grouped under sector parents

No plotly import here — the dashboard does the rendering. This module
just prepares the dataframe-shaped data so it's testable without the
plotly dependency.
"""
from __future__ import annotations

from typing import Optional


def heatmap_dataframe_dict(positions: list) -> dict:
    """Convert a list of LivePosition dataclasses (from positions_live)
    into the column-oriented dict that plotly.express.treemap consumes.

    Returns:
        {
            "symbol": [...], "sector": [...],
            "weight": [...], "day_pl_pct": [...],
            "hover_text": [...],
        }
    """
    rows = {
        "symbol": [], "sector": [], "weight": [],
        "day_pl_pct": [], "market_value": [], "hover_text": [],
    }
    for p in positions or []:
        sym = getattr(p, "symbol", None) or (p.get("symbol") if isinstance(p, dict) else None)
        if not sym:
            continue
        sector = getattr(p, "sector", None) or (p.get("sector") if isinstance(p, dict) else "Unknown") or "Unknown"
        weight = getattr(p, "weight_of_book", None) or (p.get("weight_of_book") if isinstance(p, dict) else None) or 0
        day_pct = getattr(p, "day_pl_pct", None) or (p.get("day_pl_pct") if isinstance(p, dict) else None)
        mv = getattr(p, "market_value", None) or (p.get("market_value") if isinstance(p, dict) else None) or 0
        un_pl_pct = getattr(p, "unrealized_pl_pct", None) or (p.get("unrealized_pl_pct") if isinstance(p, dict) else None)

        rows["symbol"].append(sym)
        rows["sector"].append(sector)
        rows["weight"].append(float(weight) * 100 if weight else 0)
        rows["day_pl_pct"].append(float(day_pct) * 100 if day_pct is not None else 0)
        rows["market_value"].append(float(mv))
        hover = f"{sym}<br>weight {(weight or 0)*100:.1f}%<br>"
        if day_pct is not None:
            hover += f"day {day_pct*100:+.2f}%<br>"
        if un_pl_pct is not None:
            hover += f"total {un_pl_pct*100:+.2f}%"
        rows["hover_text"].append(hover)
    return rows


def sector_summary(positions: list) -> list[dict]:
    """Aggregate by sector: total weight + cap-weighted day P&L %."""
    by_sector: dict[str, dict] = {}
    for p in positions or []:
        sector = getattr(p, "sector", None) or (p.get("sector") if isinstance(p, dict) else "Unknown") or "Unknown"
        weight = getattr(p, "weight_of_book", None) or (p.get("weight_of_book") if isinstance(p, dict) else None) or 0
        day_pct = getattr(p, "day_pl_pct", None) or (p.get("day_pl_pct") if isinstance(p, dict) else None) or 0
        bucket = by_sector.setdefault(sector, {"sector": sector, "total_weight": 0,
                                                "weighted_day_pl_pct_num": 0,
                                                "weighted_day_pl_pct_den": 0,
                                                "n_positions": 0})
        bucket["total_weight"] += float(weight or 0)
        if day_pct is not None and weight:
            bucket["weighted_day_pl_pct_num"] += float(day_pct) * float(weight)
            bucket["weighted_day_pl_pct_den"] += float(weight)
        bucket["n_positions"] += 1
    out = []
    for s in by_sector.values():
        avg_day = (s["weighted_day_pl_pct_num"] / s["weighted_day_pl_pct_den"]
                   if s["weighted_day_pl_pct_den"] > 0 else 0)
        out.append({
            "sector": s["sector"],
            "total_weight_pct": s["total_weight"] * 100,
            "weighted_day_pl_pct": avg_day * 100,
            "n_positions": s["n_positions"],
        })
    out.sort(key=lambda r: -r["total_weight_pct"])
    return out
