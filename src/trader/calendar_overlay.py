"""Calendar-effect overlay on alpha-sleeve gross.

Bundles the seasonal anomalies in trader.anomalies into a single
multiplicative scalar applied to the alpha-sleeve targets. The
component anomalies (turn-of-month, OPEX, pre-FOMC, year-end
reversal, pre-holiday) each have small but empirically-documented
edges; stacked together they account for ~30-50 bps/yr of
additional return when sized small.

Mechanism:
  - On each daily run, scan all anomalies for the current date.
  - Each ACTIVE anomaly contributes a small boost (e.g. +2 % gross)
    based on its empirical edge magnitude and our confidence.
  - The combined boost is capped at +10 % (so the overlay never
    creates a meaningful directional bet on its own).
  - Below 0 boost (BEARISH anomalies — currently none in the bundle)
    the boost can subtract from gross.

Why not a separate sleeve?
  - The anomalies fire infrequently (typically 1-3 days/month) and
    only affect timing, not stock selection. A sleeve would require
    its own picks; that's not how these signals work.
  - Multiplicative overlay preserves cross-sectional allocations
    (whatever the alpha sleeve picked stays picked) while letting
    the calendar tilt the GROSS up/down.

Disable via CALENDAR_OVERLAY_ENABLED=0 (default on).
"""
from __future__ import annotations

import os
from datetime import date
from typing import Optional

from .anomalies import scan_anomalies, Anomaly


# Anomaly boost mapping uses the dataclass's `name` field. The
# `expected_alpha_bps` already encodes empirical edge magnitude;
# we convert bps → fractional multiplier with a damping factor
# so a 50-bps anomaly only nudges gross by ~0.5 %, not 50 %.
#
# Damping = 0.10 means a 100 bps anomaly produces a 0.10*100/100
# = 0.10 = +10% gross boost. Adjust DAMPING_BPS_TO_BOOST in env
# if you want more or less aggressive sizing.
DAMPING_BPS_TO_BOOST = float(
    os.environ.get("CALENDAR_OVERLAY_DAMPING", "0.05")
)
# 0.05 = a 100 bps anomaly produces +5% gross. A 20 bps anomaly is
# basically a rounding-error +1%.

# Maximum total boost when multiple anomalies stack.
MAX_TOTAL_BOOST = float(os.environ.get("CALENDAR_OVERLAY_MAX", "0.10"))
MIN_TOTAL_BOOST = float(os.environ.get("CALENDAR_OVERLAY_MIN", "-0.05"))


def calendar_gross_scalar(asof: Optional[date] = None) -> tuple[float, list[Anomaly]]:
    """Return (scalar, active_anomalies) for today.

    scalar=1.0 means no effect. >1.0 boosts gross, <1.0 reduces.
    Sign of the boost matches the anomaly direction (long_spy = +,
    short_spy = -).
    """
    if asof is None:
        asof = date.today()
    actives = scan_anomalies(asof)
    if not actives:
        return 1.0, []
    total = 0.0
    for a in actives:
        bps = float(getattr(a, "expected_alpha_bps", 0) or 0)
        boost = (bps / 100.0) * DAMPING_BPS_TO_BOOST  # bps→pct→damped
        if getattr(a, "expected_direction", "long_spy") == "short_spy":
            boost = -boost
        total += boost
    total = max(MIN_TOTAL_BOOST, min(total, MAX_TOTAL_BOOST))
    return 1.0 + total, actives


def apply_calendar_overlay(targets: dict[str, float],
                             asof: Optional[date] = None) -> tuple[dict, dict]:
    """Multiplicatively scale every weight by the calendar scalar.

    Returns (new_targets, info_dict). info_dict includes the scalar,
    the active anomalies, and the gross before/after.

    Default-on. Disable via CALENDAR_OVERLAY_ENABLED=0.
    """
    if not os.environ.get("CALENDAR_OVERLAY_ENABLED", "1") == "1":
        return targets, {"scalar": 1.0, "actives": [], "enabled": False}
    scalar, actives = calendar_gross_scalar(asof=asof)
    if abs(scalar - 1.0) < 1e-9 or not targets:
        return targets, {
            "scalar": 1.0,
            "actives": [a.name for a in actives],
            "enabled": True,
            "before_gross": sum(targets.values()) if targets else 0.0,
            "after_gross": sum(targets.values()) if targets else 0.0,
        }
    new_targets = {t: w * scalar for t, w in targets.items()}
    return new_targets, {
        "scalar": scalar,
        "actives": [a.name for a in actives],
        "enabled": True,
        "before_gross": sum(targets.values()),
        "after_gross": sum(new_targets.values()),
    }
