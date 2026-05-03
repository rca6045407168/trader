"""[v3.59.2 — STUB] Scripted forward scenarios.

Per docs/SCENARIO_LIBRARY.md §3 — 11 scripted scenarios for shocks that
haven't happened yet (Iran 2026, Taiwan, AI capex pop, Panama Canal,
exchange cyber attack, Treasury auction failure, Pandemic 2.0,
Stagflation, Iran 2026 deep, Volcker 2.0, Peace deal surprise).

⚠️  This is a SCAFFOLD. The full implementation (replay engine that
takes a base regime and overlays shock events on tickers/sectors) is a
~12h effort. This stub:

  • Defines the canonical SCENARIO list with metadata
  • Provides a placeholder run_scenario() that documents what the
    implementation must do
  • Lets the dashboard / tests reference the scenarios as named entities
    even before the engine ships

Implementation requirements (when the engine is built):
  1. For each scenario: pull the base-regime price history.
  2. On each shock-day: apply the listed return overrides to the
     affected tickers/sectors. Propagate to subsequent days using a
     decay model (most shocks revert ~50% over 60 days).
  3. Run the strategy as if those were the actual prices. Report
     portfolio path, max DD, terminal return.
  4. Compare against the expected_dd_band; flag scenarios that bust
     their band.

  5. Write data/scripted_scenarios_results.json with the full grid.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class ScriptedScenario:
    name: str
    base_start: str
    base_end: str
    shocks: list[dict]
    expected_dd_band: tuple[float, float]  # (low, high) — must land in this band
    description: str
    archetype: Optional[str] = None  # which Tier-3 historical regime informs the magnitudes


SCENARIOS: list[ScriptedScenario] = [
    ScriptedScenario(
        name="2026-Iran-attack",
        base_start="2024-10-01", base_end="2024-12-31",
        shocks=[
            {"day": 0, "spy_ret": -0.035, "vix_mult": 2.4,
             "oil_ret": +0.20, "sector": "defense+,semis-"},
            {"day": 5, "spy_ret": -0.025, "vix_mult": 1.6,
             "oil_ret": +0.15, "sector": "defense+,semis-"},
            {"day": 30, "spy_ret_cumulative": -0.08, "rate_path": "+50bp"},
        ],
        expected_dd_band=(-0.18, -0.12),
        description="Iran direct strikes US Gulf assets; oil +20% gap; semis -8%",
        archetype="1979-Iran-Revolution",
    ),
    ScriptedScenario(
        name="Taiwan-invasion",
        base_start="2025-03-01", base_end="2025-06-30",
        shocks=[
            {"day": 0, "spy_ret": -0.12, "vix_mult": 3.9,
             "semis_ret": -0.25, "defense_ret": +0.20, "usdjpy_ret": -0.08},
            {"day": 30, "semis_recovery": -0.15, "fed": "liquidity_facility"},
        ],
        expected_dd_band=(-0.35, -0.25),
        description="Chinese amphibious operation; TSMC supply chain crash",
        archetype="1991-Soviet-collapse",
    ),
    ScriptedScenario(
        name="AI-capex-bubble-pop",
        base_start="2026-03-01", base_end="2026-09-30",
        shocks=[
            {"day": 0, "hyperscaler_capex_cut": -0.625,
             "semis_ret": -0.18, "mag7_ret": -0.12, "spy_ret": -0.08},
            {"day": 90, "rotation_into": ["value", "cyclicals"],
             "momentum_sleeve_dd": -0.35},
        ],
        expected_dd_band=(-0.22, -0.15),
        description="Hyperscaler announces 62.5% AI capex cut; Mag-7 unwind",
        archetype="2000-2002-dotcom-bust",
    ),
    ScriptedScenario(
        name="Panama-Canal-closure",
        base_start="2025-07-01", base_end="2026-01-01",
        shocks=[
            {"day": 0, "shipping_costs": +0.60, "retail_ret": -0.08,
             "transports_ret": +0.15, "staples_ret": -0.10},
            {"day": 120, "rerouting_complete": True},
        ],
        expected_dd_band=(-0.12, -0.08),
        description="80% Panama throughput cut; shipping costs +60%",
        archetype=None,
    ),
    ScriptedScenario(
        name="Exchange-cyber-attack",
        base_start="2026-06-01", base_end="2026-06-30",
        shocks=[
            {"day": 0, "exchange_halt_hours": 6, "reopen_spy_ret": -0.05},
            {"day": 5, "normalcy_restored": True},
        ],
        expected_dd_band=(-0.06, -0.03),
        description="NASDAQ trading halted 6h cyber incident; reopen liquidity gap",
        archetype="2010-Flash-Crash",
    ),
    ScriptedScenario(
        name="Treasury-auction-fail",
        base_start="2026-08-01", base_end="2026-09-30",
        shocks=[
            {"day": 0, "auction_tail_bp": 8, "yield_30y_change_bp": +35,
             "spy_ret": -0.04, "dxy_ret": -0.02},
            {"day": 10, "fed_backstop_language": True},
        ],
        expected_dd_band=(-0.10, -0.05),
        description="30Y Treasury auction tails 8bp; equities -4%",
        archetype="2011-US-downgrade",
    ),
    ScriptedScenario(
        name="Pandemic-2.0-slow",
        base_start="2027-01-01", base_end="2027-06-30",
        shocks=[
            {"day": 30, "spy_ret_cumulative": -0.05},
            {"day": 60, "spy_ret_cumulative": -0.15},
            {"day": 90, "spy_ret_cumulative": -0.40},
        ],
        expected_dd_band=(-0.30, -0.20),
        description="Slower-onset pandemic; gradual drift then panic week",
        archetype="2020-COVID",
    ),
    ScriptedScenario(
        name="Stagflation-shock",
        base_start="2026-01-01", base_end="2026-06-30",
        shocks=[
            {"day": 0, "cpi_surprise_bp": +300, "yield_10y_change_bp": +50,
             "spy_ret": -0.05, "gold_ret": +0.06},
            {"day": 90, "fed_hike_bp": +50, "spy_ret_cumulative": -0.12},
        ],
        expected_dd_band=(-0.18, -0.12),
        description="CPI 6% surprise; 10Y +50bp; equities -5%",
        archetype="1973-OPEC-oil-embargo",
    ),
    ScriptedScenario(
        name="Iran-2026-deep",
        base_start="2024-10-01", base_end="2026-03-31",
        shocks=[
            {"day": 0, "spy_ret": -0.045, "vix_mult": 2.6,
             "oil_ret": +0.25, "defense_ret": +0.10},
            {"day": 30, "oil_ret_cumulative": +0.50, "cpi_surprise_bp": +180},
            {"day": 180, "oil_ret_cumulative": +0.90,
             "spy_ret_cumulative": -0.22, "fed": "hawkish"},
            {"day": 540, "oil_ret_cumulative": +1.50,
             "spy_ret_cumulative": -0.35, "regime": "stagflationary"},
        ],
        expected_dd_band=(-0.40, -0.25),
        description="Deep stagflationary regime calibrated to 1973+1979 magnitudes",
        archetype="1973-OPEC-oil-embargo",
    ),
    ScriptedScenario(
        name="Volcker-2.0-shock",
        base_start="2026-01-01", base_end="2028-12-31",
        shocks=[
            {"day": 0, "fed_funds_target": 0.08, "yield_10y_change_bp": +200},
            {"day": 90, "fed_funds_target": 0.12,
             "spy_ret_cumulative": -0.15},
            {"day": 365, "fed_funds_target": 0.14,
             "spy_ret_cumulative": -0.22, "momentum_status": "no_signal"},
            {"day": 1095, "fed_funds_target": 0.07,
             "spy_ret_cumulative": -0.05, "recovery": "slow"},
        ],
        expected_dd_band=(-0.30, -0.20),
        description="Fed forced back to 14% to break inflation re-acceleration",
        archetype="1979-82-Volcker-shock",
    ),
    ScriptedScenario(
        name="Peace-deal-surprise",
        base_start="2026-06-01", base_end="2026-09-30",
        shocks=[
            {"day": 0, "spy_ret": +0.04, "vix_mult": 0.5,
             "defense_ret": -0.15, "europe_ret": +0.08},
            {"day": 30, "rotation": "into_cyclicals_em",
             "momentum_lag": True},
        ],
        expected_dd_band=(-0.05, +0.05),
        description="Positive geopolitical surprise; momentum LAGS the bounce",
        archetype="1989-Berlin-Wall",
    ),
]


def list_scenarios() -> list[dict]:
    """Surface as JSON for the dashboard."""
    return [
        {"name": s.name, "description": s.description,
         "expected_dd_low": s.expected_dd_band[0],
         "expected_dd_high": s.expected_dd_band[1],
         "archetype": s.archetype, "n_shocks": len(s.shocks),
         "duration_days": _duration_days(s)}
        for s in SCENARIOS
    ]


def _duration_days(s: ScriptedScenario) -> int:
    from datetime import datetime as _dt
    a = _dt.fromisoformat(s.base_start).date()
    b = _dt.fromisoformat(s.base_end).date()
    return (b - a).days


def run_scenario(scenario: ScriptedScenario) -> dict:
    """Stub. Real engine TODO. Returns a documented placeholder."""
    return {
        "scenario": scenario.name,
        "status": "ENGINE_NOT_IMPLEMENTED",
        "note": (
            "Scripted-scenario replay engine is a ~12h follow-up effort. "
            "This stub returns metadata only. To implement: take base "
            "regime price history, apply per-day return overrides on "
            "shocks, propagate to next day, run strategy, compare "
            "portfolio drawdown to expected_dd_band."
        ),
        "expected_dd_band": scenario.expected_dd_band,
        "archetype": scenario.archetype,
        "n_shocks": len(scenario.shocks),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(list_scenarios(), indent=2))
