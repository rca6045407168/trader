"""[v3.61.0] Strategy registry — every strategy in this codebase + candidates.

The single source of truth for "what strategies exist, what's their
status, what's the empirical evidence." Lets the dashboard show the
full menu so you can pick / inspect / queue backtests instead of
hunting through 70+ source files.

Each Strategy entry carries:
  • name + module path + class/function
  • category (alpha / risk_overlay / execution / data)
  • horizon (intraday / daily / weekly / monthly)
  • status (LIVE / SHADOW / NOT_WIRED / DEPRECATED / REFUTED)
  • verification: VERIFIED / REFUTED / UNTESTED / CALIBRATED
  • last_backtest: ISO date or None
  • backtest_verdict: short string (or None)
  • paper_basis: the academic citation
  • notes: free-form

Used by:
  • view_strategy_lab (dashboard)
  • scripts/list_strategies.py (CLI)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Strategy:
    name: str
    module: str
    entry: str                 # function/class name
    category: str              # alpha / risk_overlay / execution / data / signal
    horizon: str               # intraday / daily / weekly / monthly / event
    status: str                # LIVE / SHADOW / NOT_WIRED / DEPRECATED / REFUTED
    verification: str          # VERIFIED / REFUTED / UNTESTED / CALIBRATED
    last_backtest_date: Optional[str] = None
    backtest_verdict: Optional[str] = None
    paper_basis: str = ""
    notes: str = ""
    expected_sharpe: Optional[float] = None  # what literature/intuition predicts
    measured_sharpe: Optional[float] = None  # what our backtest measured


REGISTRY: list[Strategy] = [
    # ============================================================
    # CORE STRATEGY (LIVE)
    # ============================================================
    Strategy(
        name="vanilla_momentum_top15",
        module="trader.strategy",
        entry="rank_momentum",
        category="alpha", horizon="monthly",
        status="LIVE", verification="VERIFIED",
        last_backtest_date="2026-05-03",
        backtest_verdict="walk-forward Sharpe +0.55 (95% CI [+0.12, +0.98]); 62% positive windows",
        paper_basis="Jegadeesh-Titman 1993; Asness-Frazzini-Israel-Moskowitz 2018",
        notes="Top-15 trailing 12-1 momentum on liquid_50, monthly rebalance, equal-weight.",
        expected_sharpe=0.96, measured_sharpe=0.55,
    ),

    # ============================================================
    # SLEEVE CANDIDATES (REFUTED on backtest)
    # ============================================================
    Strategy(
        name="lowvol_sleeve",
        module="trader.v358_world_class",
        entry="LowVolSleeve",
        category="alpha", horizon="monthly",
        status="SHADOW", verification="REFUTED",
        last_backtest_date="2026-05-03",
        backtest_verdict="defensive (28/33 regime DD wins) but blend has same Sharpe as 100% momentum, -6pp return give-up",
        paper_basis="Frazzini-Pedersen 2014 'Betting Against Beta'",
        notes="Stays SHADOW for early-warning when momentum factor breaks; not for capital allocation.",
        expected_sharpe=0.8, measured_sharpe=0.50,
    ),
    Strategy(
        name="residual_momentum",
        module="trader.residual_momentum",
        entry="top_n_residual_momentum",
        category="alpha", horizon="monthly",
        status="SHADOW", verification="REFUTED",
        last_backtest_date="2026-05-03",
        backtest_verdict="-564bp/yr WORSE than vanilla on liquid_50 2022-2026",
        paper_basis="Blitz-Hanauer-Vidojevic 2020/2024",
        notes="Likely refuted because liquid_50 is too narrow + Mag-7 dominance violates FF5 mean-reversion. Re-test on SP500.",
        expected_sharpe=1.10, measured_sharpe=-0.20,
    ),
    Strategy(
        name="bottom_catch_llm_debate",
        module="trader.strategy",
        entry="find_bottoms",
        category="alpha", horizon="weekly",
        status="DEPRECATED", verification="REFUTED",
        last_backtest_date="legacy",
        backtest_verdict="commingled attribution bug; on the kill-list (CRITIQUE.md)",
        paper_basis="(none — verified-failed pattern)",
        notes="Killed v3.59.0. Default USE_DEBATE=false. CLAUDE.md: 'No LIVE LLM-driven trading'.",
    ),
    Strategy(
        name="vrp_sleeve",
        module="trader.vrp_sleeve",
        entry="plan_today",
        category="alpha", horizon="monthly",
        status="NOT_WIRED", verification="UNTESTED",
        backtest_verdict=None,
        paper_basis="Carr-Wu 2009; Bondarenko 2014; AQR vol premium",
        notes="Defined-risk SPY put-spreads. yfinance has no historical chain — backtest blocked on data.",
    ),
    Strategy(
        name="ml_pead",
        module="trader.pead_sleeve",
        entry="expected_targets",
        category="alpha", horizon="monthly",
        status="NOT_WIRED", verification="UNTESTED",
        backtest_verdict=None,
        paper_basis="Bernard-Thomas 1989; ScienceDirect 2024 'Beyond the last surprise'",
        notes="yfinance silent failure on earnings_dates; rule has been INERT in LIVE since v3.58.1.",
    ),
    Strategy(
        name="fomc_drift",
        module="trader.fomc_drift",
        entry="compute_signal",
        category="alpha", horizon="event",
        status="SHADOW", verification="REFUTED",
        last_backtest_date="2026-05-03",
        backtest_verdict="0/3 gates fail on close-to-close 2015-2025 (88 events)",
        paper_basis="Lucca-Moench 2015",
        notes="Effect is intraday close→2pm ET. yfinance daily bars can't capture it. Need Polygon free tier or kill the sleeve.",
        expected_sharpe=2.35, measured_sharpe=0.13,
    ),

    # ============================================================
    # RISK OVERLAYS (REFUTED on backtest)
    # ============================================================
    Strategy(
        name="momentum_crash_detector",
        module="trader.momentum_crash",
        entry="compute_signal",
        category="risk_overlay", horizon="monthly",
        status="SHADOW", verification="REFUTED",
        last_backtest_date="2026-05-03",
        backtest_verdict="-64bp/yr CAGR on SPY proxy; Sharpe lift only +0.04",
        paper_basis="Daniel-Moskowitz 2016 'Momentum Crashes'",
        notes="Catches 2008 (+3.3pp) but burns 2020 V-recovery (-2.9pp). Re-test on actual momentum portfolio path before final verdict.",
    ),
    Strategy(
        name="sector_neutralizer_35cap",
        module="trader.v358_world_class",
        entry="SectorNeutralizer",
        category="risk_overlay", horizon="monthly",
        status="SHADOW", verification="REFUTED",
        last_backtest_date="2026-05-03",
        backtest_verdict="-0.05 Sharpe, -0.92pp CAGR, 0pp DD change",
        paper_basis="standard institutional risk constraint",
        notes="Cap is binding in Mag-7 era; missed-concentration regret outweighs diversification.",
    ),
    Strategy(
        name="trailing_stop_15pct",
        module="trader.v358_world_class",
        entry="TrailingStop",
        category="risk_overlay", horizon="daily",
        status="SHADOW", verification="REFUTED",
        last_backtest_date="2026-05-03",
        backtest_verdict="-0.28 Sharpe, -4.67pp CAGR, MaxDD WORSE (-25.9% vs -23.9%)",
        paper_basis="(none — generic)",
        notes="Worst possible outcome. Whipsaws on volatile recoveries. Test bug suspected (doesn't keep stopped portion in cash); even fixed, V-recovery problem persists.",
    ),
    Strategy(
        name="earnings_rule_t1_trim50",
        module="trader.v358_world_class",
        entry="EarningsRule",
        category="risk_overlay", horizon="event",
        status="LIVE", verification="REFUTED",
        last_backtest_date="2026-05-03",
        backtest_verdict="0 trims applied — yfinance silent failure on earnings_dates",
        paper_basis="Beaver 1968; institutional earnings-volatility hedging",
        notes="LIVE-wired in v3.58.1 but DOING NOTHING. Switch to Polygon free / Finnhub free / manual scrape before re-evaluating.",
    ),
    Strategy(
        name="drawdown_circuit_breaker",
        module="trader.v358_world_class",
        entry="DrawdownCircuitBreaker",
        category="risk_overlay", horizon="daily",
        status="LIVE", verification="UNTESTED",
        backtest_verdict="never tripped in production",
        paper_basis="(none — defensive engineering)",
        notes="Halts orders at -10% from all-time peak. Cheap insurance even if untriggered.",
    ),
    Strategy(
        name="risk_parity_inverse_vol",
        module="trader.v358_world_class",
        entry="RiskParitySizer",
        category="risk_overlay", horizon="monthly",
        status="SHADOW", verification="UNTESTED",
        backtest_verdict=None,
        paper_basis="Frazzini-Pedersen 2014; Roncalli 2014",
        notes="Inverse-vol weighting. Full HRP exists in trader.hrp.",
    ),
    Strategy(
        name="hrp_full_clustering",
        module="trader.hrp",
        entry="(see module)",
        category="risk_overlay", horizon="monthly",
        status="NOT_WIRED", verification="UNTESTED",
        backtest_verdict=None,
        paper_basis="López de Prado 2016 'Building Diversified Portfolios that Outperform OOS'",
    ),

    # ============================================================
    # EXECUTION
    # ============================================================
    Strategy(
        name="moc_orders",
        module="trader.execute",
        entry="place_target_weights(use_moc=True)",
        category="execution", horizon="monthly",
        status="NOT_WIRED", verification="UNTESTED",
        backtest_verdict=None,
        paper_basis="closing-auction execution literature",
        notes="USE_MOC_ORDERS=true env. Needs cron run > 15:30 ET to make the 15:50 ET cutoff.",
        expected_sharpe=None,
    ),
    Strategy(
        name="twap_slicer",
        module="trader.v358_world_class",
        entry="TwapSlicer",
        category="execution", horizon="intraday",
        status="SHADOW", verification="UNTESTED",
        backtest_verdict=None,
        paper_basis="standard execution algorithm",
        notes="No-op below ~$1M AUM (no order > 5% ADV). Gated on capacity scaling.",
    ),
    Strategy(
        name="slippage_tracker",
        module="trader.v358_world_class",
        entry="SlippageTracker",
        category="execution", horizon="event",
        status="SHADOW", verification="UNTESTED",
        backtest_verdict="slippage_log table doesn't exist — created on first order, none placed",
        paper_basis="(none — observability)",
    ),
    Strategy(
        name="tax_lot_hifo_wash_sale",
        module="trader.v358_world_class",
        entry="TaxLotManager",
        category="execution", horizon="event",
        status="SHADOW", verification="UNTESTED",
        backtest_verdict=None,
        paper_basis="standard tax-lot accounting",
        notes="No-op in Roth IRA. Becomes 50-150bp/yr in taxable account.",
    ),

    # ============================================================
    # SIGNALS / RESEARCH (NOT_WIRED)
    # ============================================================
    Strategy(
        name="hmm_regime_overlay",
        module="trader.regime_overlay",
        entry="compute_overlay",
        category="risk_overlay", horizon="monthly",
        status="DEPRECATED", verification="REFUTED",
        backtest_verdict="V-shape whipsaw — cuts at panic lows, buys back too late (cost -34pp in 2020-Q1)",
        paper_basis="Hamilton 1989; Bulla 2006",
        notes="Historical kill on KILLED list. The momentum_crash_detector is the v5 replacement attempt (also REFUTED).",
    ),
    Strategy(
        name="garch_vol_target",
        module="trader.garch_vol",
        entry="(see module)",
        category="risk_overlay", horizon="daily",
        status="NOT_WIRED", verification="UNTESTED",
        backtest_verdict=None,
        paper_basis="Bollerslev 1986; Moreira-Muir 2017 'Volatility-Managed Portfolios'",
    ),
    Strategy(
        name="cointegration_pairs",
        module="trader.cointegration",
        entry="(see module)",
        category="alpha", horizon="weekly",
        status="NOT_WIRED", verification="UNTESTED",
        backtest_verdict=None,
        paper_basis="Engle-Granger 1987; Vidyamurthy 2004",
        notes="Module exists; never wired into LIVE rank.",
    ),
    Strategy(
        name="merger_arbitrage",
        module="trader.merger_arb",
        entry="(see module)",
        category="alpha", horizon="event",
        status="NOT_WIRED", verification="UNTESTED",
        backtest_verdict=None,
        paper_basis="Mitchell-Pulvino 2001",
    ),
    Strategy(
        name="activist_signals",
        module="trader.activist_signals",
        entry="(see module)",
        category="alpha", horizon="weekly",
        status="NOT_WIRED", verification="UNTESTED",
        backtest_verdict=None,
        paper_basis="Brav-Jiang-Partnoy-Thomas 2008",
    ),
    Strategy(
        name="ml_cross_sectional_ranker",
        module="trader.ml_ranker",
        entry="(see module)",
        category="alpha", horizon="monthly",
        status="DEPRECATED", verification="UNTESTED",
        backtest_verdict=None,
        paper_basis="Gu-Kelly-Xiu 2020 'Empirical Asset Pricing via Machine Learning'",
        notes="Marked deprecated v3.59.0; will be replaced by ml_pead feature pipeline if/when that ships.",
    ),
    Strategy(
        name="vol_signals",
        module="trader.vol_signals",
        entry="(see module)",
        category="signal", horizon="daily",
        status="NOT_WIRED", verification="UNTESTED",
        backtest_verdict=None,
        paper_basis="(see module)",
    ),
    Strategy(
        name="anomalies_kill_list",
        module="trader.anomalies",
        entry="(see module)",
        category="signal", horizon="monthly",
        status="DEPRECATED", verification="REFUTED",
        backtest_verdict="aggregated kill-list — anomalies that failed CPCV in v3.x",
        paper_basis="various; CRITIQUE.md kill-list",
    ),

    # ============================================================
    # CANDIDATE STRATEGIES (not yet implemented)
    # ============================================================
    Strategy(
        name="risk_targeted_momentum",
        module="(candidate)",
        entry="(not implemented)",
        category="alpha", horizon="monthly",
        status="NOT_WIRED", verification="UNTESTED",
        backtest_verdict=None,
        paper_basis="Carver, Systematic Trading",
        notes="Replace equal-weight with vol-targeted weights on the momentum sleeve. CANDIDATE FOR NEXT SHIP.",
    ),
    Strategy(
        name="calendar_seasonality",
        module="(candidate)",
        entry="(not implemented)",
        category="alpha", horizon="monthly",
        status="NOT_WIRED", verification="UNTESTED",
        backtest_verdict=None,
        paper_basis="Bouman-Jacobsen 2002 'Halloween Indicator'; turn-of-month effect",
        notes="Sell-in-May / Halloween / January effect. Easy to backtest; small expected effect.",
    ),
    Strategy(
        name="cross_section_low_vol",
        module="(candidate)",
        entry="(not implemented)",
        category="alpha", horizon="monthly",
        status="NOT_WIRED", verification="UNTESTED",
        backtest_verdict=None,
        paper_basis="Ang-Hodrick-Xing-Zhang 2006",
        notes="Long-short low-vol vs high-vol. Different from LowVolSleeve which is long-only.",
    ),
    Strategy(
        name="quality_minus_junk",
        module="(candidate)",
        entry="(not implemented)",
        category="alpha", horizon="monthly",
        status="NOT_WIRED", verification="UNTESTED",
        backtest_verdict=None,
        paper_basis="Asness-Frazzini-Pedersen 2019 QMJ",
    ),
    Strategy(
        name="time_series_momentum",
        module="(candidate)",
        entry="(not implemented)",
        category="alpha", horizon="monthly",
        status="NOT_WIRED", verification="UNTESTED",
        backtest_verdict=None,
        paper_basis="Moskowitz-Ooi-Pedersen 2012",
        notes="Per-asset trend rather than cross-sectional. Pairs well with CTA-style risk parity.",
    ),
]


def by_category() -> dict[str, list[Strategy]]:
    out: dict[str, list[Strategy]] = {}
    for s in REGISTRY:
        out.setdefault(s.category, []).append(s)
    return out


def by_status() -> dict[str, list[Strategy]]:
    out: dict[str, list[Strategy]] = {}
    for s in REGISTRY:
        out.setdefault(s.status, []).append(s)
    return out


def by_verification() -> dict[str, list[Strategy]]:
    out: dict[str, list[Strategy]] = {}
    for s in REGISTRY:
        out.setdefault(s.verification, []).append(s)
    return out


def find(name: str) -> Strategy | None:
    for s in REGISTRY:
        if s.name == name:
            return s
    return None


def summary_counts() -> dict:
    return {
        "total": len(REGISTRY),
        "by_status": {k: len(v) for k, v in by_status().items()},
        "by_verification": {k: len(v) for k, v in by_verification().items()},
        "by_category": {k: len(v) for k, v in by_category().items()},
    }
