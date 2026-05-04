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
    # v3.62.1: plain-English explainer for the dashboard. Populated from
    # PLAIN_DESCRIPTIONS dict at module-load time so the REGISTRY entries
    # themselves stay clean.
    plain_description: str = ""
    # v3.62.2: when verification == REFUTED, why? Categories:
    #   IMPLEMENTATION_BUG — the strategy never ran correctly
    #   TEST_DESIGN_FLAW   — our test measured the wrong thing
    #   PERIOD_DEPENDENT   — true on our window, may flip elsewhere
    #   GENUINE            — claim is false
    # See docs/WHY_REFUTED.md for full per-strategy analysis.
    refutation_category: Optional[str] = None
    retest_path: Optional[str] = None  # what would change the verdict


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
        status="SHADOW", verification="CALMAR_TRADE",
        last_backtest_date="2026-05-04",
        backtest_verdict="momentum-portfolio re-test: max DD -34.8% → -24.2% (+10.6pp), CAGR -1.1pp, Sharpe +0.03 — DD-protective Calmar trade",
        paper_basis="Daniel-Moskowitz 2016 'Momentum Crashes'",
        notes="v3.63.0 update: re-tested on actual momentum portfolio (was SPY proxy in v3.60.1). Result: not Sharpe-positive but cuts max DD by 10.6pp — meaningful for behavioral retail.",
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


# ============================================================
# v3.62.1: Plain-English descriptions — for the Strategy Lab UI.
# Imagine explaining to a smart 13-year-old: what does this strategy
# actually DO, and why might it make money?
# ============================================================
PLAIN_DESCRIPTIONS: dict[str, str] = {
    "vanilla_momentum_top15": (
        "**Buy what's been winning.** Each month, look at how every stock "
        "in our 50-name universe performed over the last 11 months "
        "(skipping the most recent month because it tends to reverse). "
        "Buy the top 15. Hold for a month. Repeat. Why this might work: "
        "stocks that go up tend to keep going up for a few months because "
        "investors are slow to react to good news. Most-studied edge in "
        "finance — works across 200+ years and many countries."
    ),
    "lowvol_sleeve": (
        "**Buy boring stocks** (the kind that don't move much). Each "
        "month, find the 15 stocks with the lowest day-to-day price "
        "wobble over the last ~3 months. Equal-weight them. Why this "
        "might work: most investors chase exciting stocks, so boring "
        "ones get left behind and end up mispriced. Famously studied "
        "as 'Betting Against Beta' (Frazzini-Pedersen 2014). **Our "
        "backtest:** defensive (drops less in crashes) but gives up "
        "too much return when blended with momentum to be worth "
        "flipping LIVE at our size."
    ),
    "residual_momentum": (
        "**Like vanilla momentum, but cleaner.** Strip out the part "
        "of each stock's recent return that came from broad factors "
        "(size, value, profitability). What's left is the stock-"
        "specific story. Buy the names with the strongest stock-"
        "specific story. Theoretical Sharpe lift +0.3 over vanilla. "
        "**Our backtest:** REFUTED on our 50-name universe (-564bp/yr "
        "vs vanilla). Likely refuted because the universe is too "
        "narrow + Mag-7 dominance breaks the regression."
    ),
    "bottom_catch_llm_debate": (
        "**Old idea: ask an LLM panel (Bull / Bear / Risk) to vote on "
        "oversold stocks.** Killed in v3.59.0 because LLM stock-"
        "picking is on the verified-failed list and attribution for "
        "the bottom-catch sleeve was bugged. The code remains for "
        "history but USE_DEBATE defaults to false. Don't resurrect."
    ),
    "vrp_sleeve": (
        "**Sell insurance.** Specifically, sell short-dated SPY put-"
        "spreads (defined-risk, never naked). Pension funds and "
        "insurers are forced to BUY tail protection regardless of "
        "price. Anyone willing to absorb the tail risk gets paid. "
        "Sharpe 0.5-1.0 historically. Big tail risk on Volmageddon-"
        "style events. NOT_WIRED — needs historical option chain "
        "data we don't have."
    ),
    "ml_pead": (
        "**Earnings drift, but smart.** When a company beats earnings, "
        "the stock keeps drifting up for ~60 days as investors slowly "
        "digest the news. The naive version is mined out. The ML "
        "version conditions on the SEQUENCE of prior surprises — "
        "companies with consistent beats drift more. NOT_WIRED — "
        "needs reliable earnings-date data."
    ),
    "fomc_drift": (
        "**Buy SPY the day before the Fed announces.** Lucca-Moench "
        "(2015) measured +49bps drift from market close on FOMC eve "
        "through 2pm ET on FOMC day. **Our backtest: REFUTED on free "
        "data** — the effect is INTRADAY, but yfinance only gives "
        "daily bars. Either pay for intraday data or kill the sleeve."
    ),
    "momentum_crash_detector": (
        "**Cut exposure when a momentum-crash regime is brewing.** "
        "Daniel-Moskowitz (2016) showed momentum strategies lose "
        "25-40% every few years when the regime flips. Trigger: "
        "24-month SPY return is negative AND 12-month volatility "
        "above 20%. **Our backtest: REFUTED on SPY proxy** — catches "
        "2008 GFC but burns the 2020 V-recovery. Same problem as the "
        "killed v3.x HMM regime overlay."
    ),
    "sector_neutralizer_35cap": (
        "**Don't let any one sector dominate the sleeve.** If "
        "momentum's top-15 is 60% tech, cap tech at 35% and "
        "redistribute. **Our backtest: hurts marginally** because in "
        "the Mag-7 era, the 'concentration' IS the alpha."
    ),
    "trailing_stop_15pct": (
        "**Sell any position that drops 15% from its peak.** "
        "Behavioral safety net to cap left-tail outcomes. **Our "
        "backtest: WORST possible outcome** — gives up return AND "
        "increases drawdown. Whipsaws on volatile recoveries — the "
        "stop kicks us out at the wrong moment, then the name rallies."
    ),
    "earnings_rule_t1_trim50": (
        "**Trim positions to half-size the day before earnings.** "
        "Earnings prints can move stocks 5-15%; we have no edge on "
        "direction, so reduce exposure to the random binary. "
        "**Status: LIVE but INERT** — yfinance silently returns empty "
        "earnings dates for major tickers, so the rule has been doing "
        "nothing for 3 releases."
    ),
    "drawdown_circuit_breaker": (
        "**Halt trading if account is down 10% from all-time peak.** "
        "Mechanical safety: risk_manager refuses new orders until "
        "the user manually resets. Cheap insurance even if untriggered."
    ),
    "risk_parity_inverse_vol": (
        "**Size each position by 1/volatility.** Less volatile names "
        "get more capital. Each position contributes ~equal risk to "
        "the portfolio. Replaces equal-weight which over-allocates to "
        "high-vol Mag-7 names. NOT_WIRED."
    ),
    "hrp_full_clustering": (
        "**Hierarchical risk parity** (López de Prado 2016). Cluster "
        "correlated stocks, then size by inverse-vol within each "
        "cluster. Provably outperforms equal-weight + naive risk-"
        "parity OOS."
    ),
    "moc_orders": (
        "**Submit orders as 'market on close' instead of 'market'.** "
        "MOC fills at the closing-auction print rather than at a "
        "bid-ask spread mid-session. Saves estimated 5bp per side. "
        "Off by default."
    ),
    "twap_slicer": (
        "**For big orders, slice into N pieces over a window.** "
        "Reduces market impact. No-op for our $10K size; meaningful "
        "at $1M+."
    ),
    "slippage_tracker": (
        "**Log decision-time mid price for every order.** Compute "
        "slippage in bps when the order fills. Build a rolling cost-"
        "quality dashboard. Currently SHADOW; table doesn't exist "
        "yet because no real orders have been placed."
    ),
    "tax_lot_hifo_wash_sale": (
        "**On sells, pick the highest-cost-basis lot first** "
        "(Highest-In, First-Out). Realizes the smallest gain (or "
        "biggest loss for harvesting). Also blocks rebuys within 30 "
        "days of a loss-realizing sell (wash-sale rule). No-op in "
        "Roth IRA; 50-150bp/yr in a taxable account."
    ),
    "hmm_regime_overlay": (
        "**Old: classify market state via Hidden Markov Model and "
        "cut gross exposure in 'bear' state.** KILLED in v3.x because "
        "it produced V-shape whipsaw — cut at panic lows and bought "
        "back too late, costing -34pp in 2020-Q1."
    ),
    "garch_vol_target": (
        "**Adjust position size daily based on forecast volatility.** "
        "Target a constant portfolio vol (e.g., 15% annualized). When "
        "forecast vol spikes, shrink positions. NOT_WIRED."
    ),
    "cointegration_pairs": (
        "**Find pairs of stocks whose prices move together.** When "
        "the spread widens beyond historical range, short the "
        "expensive one and buy the cheap one — bet on convergence. "
        "Statistical arbitrage classic. NOT_WIRED."
    ),
    "merger_arbitrage": (
        "**When Company A announces it'll buy Company B for $X, B's "
        "stock trades just below $X.** The gap is the premium for "
        "waiting + risk that the deal breaks. Buy B, hold until "
        "close. Mitchell-Pulvino (2001). NOT_WIRED."
    ),
    "activist_signals": (
        "**Track activist hedge funds' 13D/G filings.** When an "
        "activist takes a 5%+ stake (forced disclosure within 10 "
        "days), the target stock typically pops 5-7% over the next "
        "weeks. NOT_WIRED."
    ),
    "ml_cross_sectional_ranker": (
        "**Train an ML model to predict 1-month forward returns** "
        "from a feature vector (momentum at multiple horizons, vol, "
        "skew, sector relative). DEPRECATED in v3.59 — naive ML on "
        "cross-sectional features is mined out at mega-cap scale."
    ),
    "vol_signals": (
        "**Various volatility-derived signals** (realized-vs-implied "
        "spread, vol-of-vol, term structure). Module exists; never "
        "wired into LIVE rank."
    ),
    "anomalies_kill_list": (
        "**Aggregated kill-list of anomalies that failed our 3-gate "
        "validation.** Includes: equal-weight S&P rotation, deep-"
        "value tilt, full HMM gross-cut. Each documented in "
        "CRITIQUE.md with the reason it failed."
    ),
    "risk_targeted_momentum": (
        "**[CANDIDATE — not built yet]** Replace equal-weight in the "
        "LIVE momentum sleeve with vol-target weights. Each name "
        "sized so its expected daily P&L contribution is ~equal. "
        "Should improve Sharpe."
    ),
    "calendar_seasonality": (
        "**[CANDIDATE — not built yet]** Halloween indicator (sell-"
        "in-May), turn-of-month effect, January effect. Bouman-"
        "Jacobsen (2002). Easy to backtest; small expected effect."
    ),
    "cross_section_low_vol": (
        "**[CANDIDATE — not built yet]** Long lowest-vol decile, "
        "short highest-vol decile. Different from LowVolSleeve which "
        "is long-only."
    ),
    "quality_minus_junk": (
        "**[CANDIDATE — not built yet]** Long high-quality (high-"
        "profitability, growing, safe) names; short low-quality. "
        "Asness-Frazzini-Pedersen (2019)."
    ),
    "time_series_momentum": (
        "**[CANDIDATE — not built yet]** Per-asset trend rather than "
        "cross-sectional rank. If SPY's 12-month return is positive, "
        "stay long; if negative, go to cash. Trend-following CTAs "
        "use this."
    ),
}


# Attach descriptions to registry entries at module load.
for _s in REGISTRY:
    _s.plain_description = PLAIN_DESCRIPTIONS.get(_s.name, "")


# v3.62.2: refutation categories — see docs/WHY_REFUTED.md for analysis.
REFUTATION_CATEGORIES: dict[str, tuple[str, str]] = {
    # name → (category, retest_path)
    "earnings_rule_t1_trim50": (
        "IMPLEMENTATION_BUG",
        "Switch earnings calendar source to Polygon free / Finnhub / SEC EDGAR. "
        "yfinance silently returns empty earnings_dates for major tickers.",
    ),
    "fomc_drift": (
        "TEST_DESIGN_FLAW",
        "Lucca-Moench measured close→2pm-ET drift; we tested close-to-close. "
        "Need intraday data (Polygon free tier minute bars) for honest test.",
    ),
    "momentum_crash_detector": (
        "CALMAR_TRADE",
        "v3.63.0 re-test on actual momentum portfolio (not SPY): max DD -34.8% → "
        "-24.2% (improvement +10.6pp), CAGR -1.1pp, Sharpe +0.03. Not a Sharpe "
        "win but a meaningful drawdown reduction — the kind that matters "
        "behaviorally for retail (cuts panic-sell risk).",
    ),
    "trailing_stop_15pct": (
        "TEST_DESIGN_FLAW",
        "Test redistributed stopped-out weight to survivors instead of holding cash. "
        "Rewrite to keep stopped portion in cash for the rest of the window.",
    ),
    "residual_momentum": (
        "GENUINE_ON_OUR_UNIVERSE",
        "v3.63.0 re-test on broader 127-name universe over 2018-2026: STILL "
        "REFUTED. Vanilla CAGR +9.80% vs residual +3.49%. Sharpe lift -0.24, "
        "lift -631bp/yr. The Blitz-Hanauer claim doesn't survive on US large/"
        "mid-cap. May still hold on full SP500 + 1990-2024 + EU/Global, but "
        "for OUR universe we should treat this as genuinely refuted.",
    ),
    "lowvol_sleeve": (
        "PERIOD_DEPENDENT",
        "Defensive characteristic IS real (28/33 regime DD wins) but blend "
        "Sharpe didn't lift because correlation was +0.67 in Mag-7 era. "
        "Try regime-conditional router (RegimeRouter) instead of static blend.",
    ),
    "sector_neutralizer_35cap": (
        "PERIOD_DEPENDENT",
        "Mag-7 era penalizes concentration caps because alpha LIVES in the "
        "concentration. Make cap regime-conditional or accept ride-the-trend.",
    ),
    "bottom_catch_llm_debate": (
        "GENUINE",
        "LLM-driven trading is on the verified-failed pattern list "
        "(per CLAUDE.md). Stay killed.",
    ),
}
for _s in REGISTRY:
    if _s.name in REFUTATION_CATEGORIES:
        _s.refutation_category, _s.retest_path = REFUTATION_CATEGORIES[_s.name]
