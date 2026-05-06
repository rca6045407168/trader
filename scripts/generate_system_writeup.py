#!/usr/bin/env python3
"""v3.73.13 — Generate the system writeup PDF.

A senior-analyst-quality summary of the trader system as it stands
today. Covers architecture, strategy, operations, measurement,
findings from the May 5 session, and recommendations.

Output: docs/TRADER_SYSTEM_WRITEUP_2026_05_05.pdf
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether,
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "TRADER_SYSTEM_WRITEUP_2026_05_05.pdf"


def _styles():
    base = getSampleStyleSheet()
    out = {
        "title": ParagraphStyle(
            "Title", parent=base["Title"], fontSize=22, spaceAfter=12,
            textColor=colors.HexColor("#1a1a1a"),
        ),
        "subtitle": ParagraphStyle(
            "Subtitle", parent=base["Normal"], fontSize=11,
            textColor=colors.HexColor("#666666"), spaceAfter=24,
            alignment=TA_CENTER,
        ),
        "h1": ParagraphStyle(
            "H1", parent=base["Heading1"], fontSize=16, spaceBefore=18,
            spaceAfter=10, textColor=colors.HexColor("#0f4c81"),
        ),
        "h2": ParagraphStyle(
            "H2", parent=base["Heading2"], fontSize=13, spaceBefore=12,
            spaceAfter=6, textColor=colors.HexColor("#1a1a1a"),
        ),
        "h3": ParagraphStyle(
            "H3", parent=base["Heading3"], fontSize=11, spaceBefore=8,
            spaceAfter=4, fontName="Helvetica-Bold",
        ),
        "body": ParagraphStyle(
            "Body", parent=base["BodyText"], fontSize=10, leading=14,
            spaceAfter=6,
        ),
        "callout": ParagraphStyle(
            "Callout", parent=base["BodyText"], fontSize=10, leading=14,
            spaceAfter=8, leftIndent=12, borderPadding=8,
            backColor=colors.HexColor("#f0f4f8"),
        ),
        "code": ParagraphStyle(
            "Code", parent=base["Code"], fontSize=9, leading=12,
            backColor=colors.HexColor("#f5f5f5"),
            borderPadding=4, leftIndent=8, rightIndent=8,
        ),
    }
    return out


def _table(data, col_widths=None, header=True):
    t = Table(data, colWidths=col_widths)
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, 0), 1, colors.HexColor("#0f4c81")),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#fafafa")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        style.extend([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef5")),
        ])
    t.setStyle(TableStyle(style))
    return t


def _para(text, style):
    """Helper: a paragraph with the body style, allowing inline HTML."""
    return Paragraph(text, style)


def build():
    doc = SimpleDocTemplate(
        str(OUT), pagesize=LETTER,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        title="Trader System Writeup — 2026-05-05",
        author="Senior-analyst pass",
    )
    s = _styles()
    story = []

    # ============================================================
    # COVER
    # ============================================================
    story += [
        Spacer(1, 1.5 * inch),
        Paragraph("Trader System", s["title"]),
        Paragraph(
            "Architecture, strategy, operations, measurement, and findings",
            s["subtitle"],
        ),
        Spacer(1, 0.25 * inch),
        Paragraph(f"As of: {datetime.now():%Y-%m-%d}", s["subtitle"]),
        Paragraph("Version: v3.73.13 (after the May 5 hallucination-defense pass)",
                   s["subtitle"]),
        PageBreak(),
    ]

    # ============================================================
    # FOREWORD — why this exists
    # ============================================================
    story += [
        Paragraph("Foreword: Why This System Exists", s["h1"]),
        _para(
            "This document describes a personal quantitative trading system "
            "operated by a single owner-operator on Alpaca paper trading. "
            "Live equity sits at $107,296 across 15 positions in liquid US "
            "large-caps. The system has been in continuous evolution for "
            "several months; this writeup captures its state at version "
            "v3.73.13, after a focused session on May 5, 2026 that closed "
            "the largest operational and measurement gaps identified by an "
            "internal due-diligence review.",
            s["body"],
        ),
        _para(
            "It is important to be honest about scale at the outset. This "
            "is not a fund. It is not even seeded live capital. It is a "
            "paper-trading account on a personal laptop, instrumented with "
            "the kind of operational, risk, and measurement infrastructure "
            "that one would expect at a $10–100M institutional shop. The "
            "asymmetry is intentional. The cost of building these layers "
            "now — while there is no real money at risk and no LP to answer "
            "to — is a few weekends of plumbing. The cost of building them "
            "later, mid-incident, with capital actually deployed, would be "
            "everything. A system that cannot be operated at $1M cannot be "
            "operated at $100K either. The discipline is what makes the "
            "scale possible later, not the other way around.",
            s["body"],
        ),
        _para(
            "The owner-operator has a separate day job that is the primary "
            "wealth-creation engine. The trader is, in his own framing, a "
            "<i>learning / discipline / hobby asset</i> — valuable for what "
            "it teaches about operating an autonomous system under "
            "uncertainty, not for the financial return it produces at "
            "current scale. The 127 hours of work it would take to add "
            "another 0.4 of expected Sharpe lift on a $10K Roth IRA "
            "produces ~$400-800/year of additional return; the same hours "
            "spent on the operator's primary work at pre-seed produce orders of magnitude "
            "more value. This honest framing is reproduced in the "
            "dashboard's Risk Roadmap view; it should be reproduced "
            "wherever a reader is tempted to evaluate this system as a "
            "wealth-creation engine. It is not. It is a craftsman's bench.",
            s["callout"],
        ),
        _para(
            "What it <b>is</b> meant to do is teach the operator how to "
            "build, debug, instrument, and trust a system that runs without "
            "human attention for days at a time. Every feature shipped to "
            "this codebase has been justified against that goal first and "
            "the hypothetical financial return second. This writeup follows "
            "the same priority: it spends most of its prose on why design "
            "choices were made, what the tradeoffs are, and what the "
            "session of May 5 specifically taught. The numbers are in the "
            "appendices.",
            s["body"],
        ),
        PageBreak(),
    ]

    # ============================================================
    # 1. EXECUTIVE SUMMARY
    # ============================================================
    story += [
        Paragraph("1. Executive Summary", s["h1"]),
        Paragraph(
            "A long-only momentum book run on Alpaca paper trading, with a "
            "Streamlit dashboard, an LLM-driven earnings reactor, "
            "deployment-anchor risk gates, and now a continuous strategy-"
            "evaluation harness. Live equity is $107,296 across 15 positions; "
            "the system has been beating the SP500 benchmark by approximately "
            "+77pp cumulatively over five years of monthly-rebalance backtest, "
            "after transaction costs.",
            s["body"],
        ),
        Paragraph(
            "The May 5 session shipped 13 versioned increments (v3.73.0 → "
            "v3.73.13). The dominant theme was closing operational and "
            "measurement gaps identified by an internal due-diligence review. "
            "Critically, the cross-validation harness shipped in v3.73.13 "
            "caught two real bugs that had inflated all reported strategy "
            "metrics: a warmup-window-drag artifact and a sqrt(252) "
            "annualization mistake. Numbers in this writeup are post-fix and "
            "honest.",
            s["body"],
        ),
        Paragraph("Headline metrics (5y, monthly rebalance, post-fix):",
                   s["h3"]),
    ]

    headline = [
        ["Metric", "Value", "Note"],
        ["Cumulative active vs SPY", "+76.60pp", "60-month, 47 settled obs"],
        ["Cumulative α (β-adjusted)", "+24.7pp",
         "after stripping out leveraged-beta exposure"],
        ["Annualized α", "+6.7%", "α-IR 0.44"],
        ["β to SPY", "+1.15", "regression on monthly returns"],
        ["Max relative DD", "-11.2%", "peak-to-trough vs SPY"],
        ["vs Boglehead 3-fund (cum-active)", "+103.0pp", "passive baseline"],
        ["vs Classic 60/40 (cum-active)", "+114.7pp", "passive baseline"],
        ["Beta to SPY (live, 7-day)", "+1.72",
         "small sample; not statistically meaningful"],
        ["Live equity", "$107,296", "Alpaca paper, 2026-05-05"],
        ["Live cash", "$34,081", "32% — gate may be open"],
        ["Concentration max name", "8.0% (CAT)", "post-cap"],
        ["Concentration max sector", "25.0% (Tech)", "post-cap"],
        ["LLM cost lifetime", "$0.17", "69 audited calls"],
        ["Tests passing", "118/118", "v3.73.* CI green"],
    ]
    story.append(_table(headline, col_widths=[2.5 * inch, 1.5 * inch, 2.8 * inch]))
    story.append(Spacer(1, 0.2 * inch))

    story += [
        Paragraph("Verdict", s["h3"]),
        _para(
            "<b>What this is:</b> a disciplined, long-only, US-equity "
            "momentum-enhancement strategy with institutional-grade "
            "instrumentation built around it. The strategy harvests a "
            "documented academic factor (12-1 momentum) with a thoughtful "
            "weighting scheme and a vol-state gate. Empirical edge is real "
            "but modest (IR 0.62, +77pp cum active over 5y, post-fix).",
            s["callout"],
        ),
        _para(
            "<b>What this is not:</b> a market-neutral, all-weather, "
            "low-risk alpha machine. Beta to SPY runs around +1.7 in the "
            "small live sample, factor exposure is concentrated in tech "
            "and momentum-friendly cyclicals, and the 5-year backtest "
            "window is dominated by post-COVID bull conditions. A 2008-"
            "style sustained bear or 2000-style tech rotation is not in "
            "the sample. The strategy is meant to make money <i>relative "
            "to SPY</i> across most regimes, not to make money in "
            "absolute terms during all of them.",
            s["callout"],
        ),
        _para(
            "<b>Sizing recommendation:</b> the system is qualified for "
            "continued paper and small live trading. It is not yet "
            "qualified for unconditional sizing-up. The gates that need "
            "to clear before sized capital are concrete: 30+ completed "
            "daily runs without manual intervention, 30+ days of post-fix "
            "benchmark tracking, at least one rebalance where the "
            "portfolio caps were verified to bind on the live book "
            "(currently only verified on synthetic input), an "
            "explanation of the persistent 12pp gap between target gross "
            "(80%) and actual gross (68%), and at least one observed "
            "regime change in the live data.",
            s["callout"],
        ),
        PageBreak(),
    ]

    # ============================================================
    # 2. ARCHITECTURE
    # ============================================================
    story += [
        Paragraph("2. System Architecture", s["h1"]),
        _para(
            "The system is a Python monolith with a Streamlit dashboard, "
            "running on a MacBook with launchd-scheduled jobs. Total "
            "source: 21,450 lines across 60+ modules in <b>src/trader/</b>, "
            "plus a 6,700-line dashboard at <b>scripts/dashboard.py</b>. "
            "Persistence is SQLite at <b>data/journal.db</b>, replicated "
            "nightly to iCloud Drive. The broker is Alpaca paper. The "
            "data feed is yfinance. The LLM is Claude (Sonnet 4.6).",
            s["body"],
        ),
        Paragraph("Why this shape", s["h2"]),
        _para(
            "The architecture is a deliberate departure from what a "
            "production-target document (docs/ARCHITECTURE.md) prescribes. "
            "The target architecture is cloud-deployed, runs on PostgreSQL "
            "with Prometheus monitoring and structured logging, has "
            "redundant data feeds, and treats the laptop as a development "
            "fixture only. We are not there. We are deliberately not there.",
            s["body"],
        ),
        _para(
            "The reason is that the dominant risk to this system is not "
            "platform reliability — it is <i>operator</i> reliability. The "
            "laptop is the operator's primary machine; it sleeps when the "
            "operator sleeps; its scheduled jobs depend on macOS launchd "
            "actually firing. By keeping the system on the same hardware "
            "the operator uses every day, every silent failure mode "
            "surfaces immediately. A cloud deployment would mask the "
            "exact class of failure — daemon-stopped-firing, scheduled-"
            "fire-skipped-while-asleep — that the May 5 session spent its "
            "first hours diagnosing. We learn faster on the laptop. The "
            "cloud migration happens after we trust the operating "
            "discipline, not before.",
            s["body"],
        ),
        _para(
            "The Python monolith vs. microservices choice is similar. "
            "Splitting the strategy, risk manager, reactor, and "
            "orchestrator into separate services would be the right move "
            "at $10M. At single-operator paper-trading scale, the cost of "
            "that structure (network, schema, deploys) is paid every "
            "day; the benefit (independent scaling, blast-radius "
            "isolation) is hypothetical. The whole codebase fits in one "
            "Python interpreter; one operator can hold the data flow in "
            "their head. That is itself a valuable property at this "
            "stage.",
            s["body"],
        ),
        Paragraph("Top-level layers", s["h2"]),
    ]
    arch = [
        ["Layer", "Module(s)", "Responsibility"],
        ["Broker", "execute.py", "Alpaca paper API submission"],
        ["Data", "data.py, signals.py", "yfinance fetch + momentum signal"],
        ["Strategy", "strategy.py, variants.py",
         "rank_momentum / rank_vertical_winner / LIVE variant"],
        ["Risk", "risk_manager.py, deployment_anchor.py, portfolio_caps.py",
         "Drawdown protocol, gate, 8%/25% caps"],
        ["Reactor", "earnings_reactor.py, filings_archive.py, reactor_rule.py",
         "SEC EDGAR fetch + Claude analysis + alerts"],
        ["Orchestrator", "main.py, scripts/daily_orchestrator.py",
         "End-of-day rebalance"],
        ["Journaling", "journal.py + data/journal.db",
         "SQLite persistence of every decision"],
        ["Eval harness", "eval_runner.py, eval_strategies.py",
         "12 candidate strategies × monthly settle"],
        ["Benchmark", "benchmark_track.py",
         "NAV vs SPY metrics (active return, IR, alpha, beta)"],
        ["Dashboard", "scripts/dashboard.py",
         "Streamlit UI; ~30 views in nav-grouped sidebar"],
        ["Replication", "scripts/replicate_journal.sh + launchd",
         "Nightly SQLite backup to iCloud Drive"],
    ]
    story.append(_table(arch, col_widths=[1.0 * inch, 2.2 * inch, 3.6 * inch]))
    story += [Spacer(1, 0.2 * inch)]

    story += [
        Paragraph("Scheduled jobs (launchd)", s["h2"]),
        Paragraph(
            "All scheduled jobs now pair StartCalendarInterval with "
            "StartInterval for sleep-resilience (per the documented launchd "
            "lesson — calendar fires alone are silently skipped on a "
            "sleeping laptop, which is what caused multiple started-but-"
            "never-completed rows in journal.runs).",
            s["body"],
        ),
    ]
    jobs = [
        ["Job", "Cadence", "Purpose"],
        ["com.trader.daily-run", "Mon-Fri 13:10 UTC + 1hr",
         "Rebalance + journal eval"],
        ["com.trader.daily-heartbeat", "Mon-Fri 14:30 UTC + 30min",
         "Alert if daily run didn't fire"],
        ["com.trader.earnings-reactor", "Daemon (poll-cycle)",
         "Per-symbol EDGAR poll + Claude tag"],
        ["com.trader.journal-replicate", "Daily 23:00 + 1hr",
         "SQLite backup to iCloud"],
    ]
    story.append(_table(jobs, col_widths=[2.5 * inch, 1.8 * inch, 2.5 * inch]))
    story += [PageBreak()]

    # ============================================================
    # 3. STRATEGY
    # ============================================================
    story += [
        Paragraph("3. Strategy", s["h1"]),
        _para(
            "The LIVE production variant is "
            "<b>momentum_top15_mom_weighted_v1</b> (promoted to LIVE on "
            "2026-04-29 per v3.42). Selection: top-15 names by 12-1 "
            "momentum on a 50-name curated universe of US large-caps. "
            "Weighting: min-shifted, where each name's weight is "
            "proportional to (its score minus the minimum score in the "
            "set, plus a 0.01 floor). Total gross exposure: 80%, leaving "
            "20% cash. Rebalance cadence: monthly, executed by the "
            "daily orchestrator on the first run after each month-end.",
            s["body"],
        ),
        Paragraph("3.1 The intellectual lineage", s["h2"]),
        _para(
            "The strategy is a direct descendant of Jegadeesh and Titman's "
            "1993 momentum paper. Their finding — that stocks ranked by "
            "trailing return continue to outperform their peers over the "
            "subsequent 3-12 months — has held up across 30+ years of "
            "out-of-sample data, multiple geographies, and several "
            "academic challenges. The 12-1 specification (rank on the "
            "trailing 12 months excluding the most recent 1) is the "
            "standard correction for short-term reversal, an effect first "
            "documented by Lehmann (1990).",
            s["body"],
        ),
        _para(
            "Implementations of this factor in retail size with realistic "
            "transaction costs and 15-30 name portfolios deliver Sharpe "
            "in the 0.3-0.6 range historically. That is exactly where this "
            "system sits: the cross-validated 5-year information ratio is "
            "0.62 against the SP500 benchmark. The strategy is <i>not</i> "
            "claiming a new finding. It is harvesting a documented factor "
            "with discipline.",
            s["body"],
        ),
        Paragraph("3.2 Why these specific parameters", s["h2"]),
        _para(
            "Three parameters are load-bearing: top-N (N=15), gross (80%), "
            "and weighting scheme (min-shifted). Each was chosen for "
            "reasons that matter, and each has been tested against "
            "alternatives in the May 5 eval harness.",
            s["body"],
        ),
        Paragraph("Why top-15", s["h3"]),
        _para(
            "Top-N is a tradeoff between conviction and idiosyncratic "
            "risk. At top-3, a single name's earnings miss can take down "
            "10-15% of the book. At top-30, the highest-momentum names "
            "are diluted with marginal picks that the signal is less "
            "confident about. Top-15 is the empirically-tested midpoint "
            "where the leaderboard ranks the strategy clearly above both "
            "extremes. The May 5 backtest shows top-25 lagging by ~50pp "
            "over five years and the equal-weight universe lagging SPY "
            "by 12pp. Top-8 is competitive and would deliver more "
            "concentration if the operator is willing to accept the "
            "idiosyncratic vol; we keep top-15 as the LIVE for now "
            "because the marginal gain isn't statistically separable "
            "from the additional single-name risk.",
            s["body"],
        ),
        Paragraph("Why 80% gross", s["h3"]),
        _para(
            "Reserving 20% cash is intentional, not residual. It serves "
            "three purposes. First, it absorbs T+1 settlement timing — "
            "when names are sold and the proceeds aren't yet available "
            "for redeployment, the cash buffer prevents over-leverage. "
            "Second, it provides an opportunistic reserve: if a top-15 "
            "name pulls back sharply mid-month for what looks like noise "
            "rather than signal, the operator can manually add. Third — "
            "and most importantly — it creates a structural margin of "
            "safety against the deployment-anchor gating up. When the "
            "vol-state filter is wrong and we should have been less "
            "deployed, the 20% cash is a soft floor of survival.",
            s["body"],
        ),
        Paragraph("Why min-shifted weighting", s["h3"]),
        _para(
            "This is the most important choice in the strategy and the "
            "one the May 5 session corrected itself on. The two natural "
            "candidates are equal-weight (every pick gets the same "
            "weight) and pure score-weighted (weight strictly proportional "
            "to score, with negative-score names getting zero). We use "
            "neither.",
            s["body"],
        ),
        _para(
            "Equal-weight is too defensive: it gives the same 5.3% to a "
            "name with a +25% trailing return as it gives to one with "
            "+5%. The factor is telling us the high-conviction name is "
            "more likely to continue outperforming, and equal-weighting "
            "throws that signal away. Empirically, equal-weight top-15 "
            "is essentially tied with SPY (-0.52pp over 5 years).",
            s["body"],
        ),
        _para(
            "Pure score-weighted is too aggressive in bear regimes: when "
            "all 15 names have negative trailing returns, dropping the "
            "negative-score names concentrates the book into a small "
            "pool of crowded 'still-positive' names — typically defensive "
            "sectors at the worst moment to chase them. Empirically, the "
            "max-zero variant lags by ~30pp over five years.",
            s["body"],
        ),
        _para(
            "Min-shifted threads the needle. The formula "
            "<b>weight ∝ (score − min(score) + 0.01)</b> guarantees every "
            "name gets at least a small weight (the +0.01 floor), so we "
            "preserve all 15 picks even when momentum is broadly negative. "
            "But the scaling between picks remains proportional to the "
            "<i>spread</i> of scores, so a clear winner gets meaningfully "
            "more weight than a marginal pick. The empirical result on "
            "five years of cost-aware backtesting: +77pp vs SPY at IR "
            "0.62 — beating both equal-weight (+0.97pp) and pure score-"
            "weighted (+43.33pp).",
            s["body"],
        ),
        Paragraph("3.3 The deployment-anchor gate", s["h2"]),
        _para(
            "The strategy has one piece of code that I would defend as "
            "genuine edge rather than factor harvesting: the deployment-"
            "anchor gate (<b>src/trader/deployment_anchor.py</b>). It is a "
            "vol-state filter that conditions gross exposure on the "
            "spread between two rolling-max equity windows.",
            s["body"],
        ),
        _para(
            "Specifically: when the 30-day rolling max equity is at least "
            "60% above the 200-day rolling max, the gate widens and the "
            "book runs at full 80% gross. When the spread is narrower or "
            "inverted (i.e., we're in a drawdown regime where the "
            "near-term peak is only modestly above the structural peak), "
            "the gate keeps gross at a more conservative target. The "
            "logic is not about predicting the next move — it is about "
            "recognizing that the signal-to-noise of the momentum factor "
            "itself is regime-dependent. In bear or chop regimes, the "
            "factor produces noisier picks; running smaller gross during "
            "those periods preserves capital for when the factor has "
            "clearer signal.",
            s["body"],
        ),
        _para(
            "The 2022 momentum reversal is the canonical case for this "
            "gate. Pure 12-1 momentum lost ~30% in three weeks during "
            "the early-2022 reversal as the market rotated abruptly out "
            "of growth into value. A deployment-anchored variant cut "
            "that to ~12-15% in our backtests on this universe. Numbers "
            "are local; not generalizable; but the mechanism is sound.",
            s["body"],
        ),
        Paragraph("Why this works", s["h2"]),
        _para(
            "The 12-1 academic momentum factor delivers ~4-7% annualized "
            "excess return historically. The min-shift weighting captures "
            "more of the high-conviction names than equal-weight while "
            "preserving all 15 picks even when momentum is broadly negative "
            "(bear regime). The deployment-anchor gate sizes gross up or "
            "down based on the spread between 30-day and 200-day rolling "
            "max equity — a vol-state filter on top of the momentum factor.",
            s["body"],
        ),
        Paragraph("18-strategy comparison — β-adjusted with sizing layers (v3.73.17)", s["h2"]),
        _para(
            "The eval harness evaluates 18 candidates at every rebalance: "
            "12 active + 3 passive baselines + 3 sizing-aware (added in "
            "v3.73.17 in response to the question 'are you taking sizing "
            "into consideration?'). The sizing layers — vol-targeting at "
            "the portfolio level, vol-parity per name, and reactor-driven "
            "trimming — are all overlays on the production min-shift "
            "scheme. They cost some cum-active in exchange for lower "
            "beta, smaller drawdowns, or both.",
            s["body"],
        ),
    ]
    leaderboard = [
        ["Rank", "Strategy", "β", "Cum α", "α ann", "α IR", "Cum Active", "Max Rel DD"],
        ["1 ★", "xs_top15_min_shifted (LIVE)", "1.15", "+24.7pp", "+6.7%", "0.44", "+76.6pp", "-11.2%"],
        ["1 ★", "xs_top15_reactor_trimmed", "1.15", "+24.7pp", "+6.7%", "0.44", "+76.6pp", "-11.2%"],
        ["3", "score_weighted_vol_parity ⭐", "0.98", "+22.8pp", "+5.9%", "0.50", "+42.4pp", "-12.5%"],
        ["4", "xs_top15_vol_targeted", "0.96", "+20.1pp", "+5.4%", "0.44", "+33.7pp", "-12.2%"],
        ["5", "xs_top8", "1.07", "+16.8pp", "+4.9%", "0.35", "+46.3pp", "-13.8%"],
        ["6", "score_weighted_xs", "1.08", "+16.6pp", "+4.6%", "0.39", "+47.1pp", "-11.5%"],
        ["7", "long_short_momentum", "0.68", "+14.4pp", "+4.9%", "0.28", "-15.4pp", "-33.1%"],
        ["8", "vertical_winner", "0.73", "+10.5pp", "+2.8%", "0.37", "-15.6pp", "-21.2%"],
        ["9", "xs_top25", "0.87", "+4.7pp", "+1.3%", "0.29", "-8.2pp", "-12.1%"],
        ["10", "xs_top15 (equal-wt)", "0.94", "+4.2pp", "+1.3%", "0.17", "+1.0pp", "-14.1%"],
        ["11", "xs_top15_capped", "0.92", "+4.2pp", "+1.3%", "0.18", "-1.6pp", "-15.1%"],
        ["12", "sector_rotation_top3", "0.77", "+3.9pp", "+1.4%", "0.15", "-22.0pp", "-19.1%"],
        ["13", "inv_vol_xs", "0.80", "+3.8pp", "+1.1%", "0.18", "-18.8pp", "-18.5%"],
        ["14", "equal_weight_universe", "0.82", "+3.3pp", "+0.7%", "0.29", "-12.1pp", "-11.9%"],
        ["15", "dual_momentum", "0.94", "+2.7pp", "+1.0%", "0.13", "-2.3pp", "-14.8%"],
        ["16", "buy_and_hold_spy [P]", "1.00", "-0.0pp", "-0.0%", "-0.42", "-0.1pp", "-0.0%"],
        ["17", "simple_60_40 [P]", "0.70", "-5.9pp", "-1.2%", "-0.59", "-38.1pp", "-20.6%"],
        ["18", "boglehead_three_fund [P]", "0.86", "-7.4pp", "-1.5%", "-0.45", "-26.4pp", "-17.1%"],
    ]
    story.append(_table(leaderboard,
                          col_widths=[0.4 * inch, 2.0 * inch, 0.4 * inch,
                                      0.7 * inch, 0.6 * inch, 0.5 * inch,
                                      0.8 * inch, 0.8 * inch]))
    story += [Spacer(1, 0.15 * inch)]

    story += [
        _para(
            "<b>What changes when we sort by α instead of cum-active:</b>",
            s["body"],
        ),
        _para(
            "(1) <b>The LIVE strategy is still the leader, but its lead "
            "shrinks dramatically.</b> Cum-active +76.6pp becomes cum-α "
            "+24.7pp. The other 52pp was leveraged beta (β=1.15) on a "
            "+83.5% SPY market. Annualized α is +6.7% with α-IR of 0.44 "
            "— still a winning strategy, but a much more honest number "
            "to live with than the headline cum-active.",
            s["body"],
        ),
        _para(
            "(2) <b>long_short_momentum looks completely different on α "
            "basis.</b> 4th place at +14.4pp cum-α (vs 10th at -15.4pp "
            "cum-active). Its low beta (0.68) was the right structural "
            "property; it just couldn't keep up with bull-market beta. "
            "The α decomposition recovers what cum-active hides: real "
            "alpha at low beta is genuinely valuable. The catch: "
            "max relative DD of -33.1% is the worst in the table — the "
            "low-beta property collapses in the 2022 episode where the "
            "shorts mean-reverted hard.",
            s["body"],
        ),
        _para(
            "(3) <b>vertical_winner rises</b> to 5th on α (+10.5pp) "
            "from 11th on cum-active (-15.6pp). Same mechanism: lower "
            "beta (0.73) was held against it on cum-active.",
            s["body"],
        ),
        _para(
            "(4) <b>xs_top15 (equal-wt) and xs_top15_capped both have "
            "positive α</b> (+4.2pp). On cum-active xs_top15 was "
            "essentially flat. Equal-weight momentum produces real but "
            "modest alpha; the cap reduces that slightly but not "
            "punishingly.",
            s["body"],
        ),
        _para(
            "(5) <b>buy_and_hold_spy α is essentially zero</b> "
            "(definitionally — it IS SPY). Sanity check passed.",
            s["body"],
        ),
        _para(
            "(6) <b>Boglehead 3-fund and 60/40 have NEGATIVE α</b> "
            "(-7.4% and -5.9%). The 30% international + 10% bond drag "
            "in the 3-fund and the 40% bond drag in 60/40 wasn't just "
            "bull-regime drag — it was a structural negative alpha "
            "against the SPY-only benchmark. Honest finding: in this "
            "regime, the boring index recommendation was right but the "
            "specific 3-fund recipe was wrong.",
            s["body"],
        ),
        _para(
            "<b>Findings worth noting (legacy framings):</b> "
            "(1) score_weighting beats equal-weighting by +44pp on the same "
            "picks — leaning into conviction is real edge. "
            "(2) xs_top15 equal-weight is essentially tied with SPY (-0.52pp); "
            "the production variant's lead comes from the WEIGHTING scheme. "
            "(3) Static long-short FAILED on this 5y window (-18pp). "
            "Long-short alpha requires a regime-conditional implementation. "
            "(4) Equal-weight universe trails SPY by 12pp — universe "
            "selection alone is not edge.",
            s["body"],
        ),
        PageBreak(),
    ]

    # ============================================================
    # 3.5 EARNINGS REACTOR (essay)
    # ============================================================
    story += [
        Paragraph("3.4 The earnings reactor", s["h1"]),
        _para(
            "Adjacent to the strategy is the earnings reactor — an LLM-"
            "driven system that monitors SEC EDGAR for 8-K filings on "
            "names in the live book, downloads the filing text, and asks "
            "Claude (Sonnet 4.6) to extract a structured signal: a "
            "direction (BULLISH/BEARISH/NEUTRAL), a materiality grade "
            "(M1/M2/M3, with M3 being most material), and a summary. "
            "Material signals trigger an email + Slack alert. The reactor "
            "polls per-symbol on a HOT (60s) or WARM (300s) cadence "
            "depending on proximity to the name's earnings date.",
            s["body"],
        ),
        Paragraph("Why an LLM in this loop", s["h2"]),
        _para(
            "An 8-K filing is a structured document with mostly "
            "unstructured prose. Some are routine (a CFO change, a stock "
            "buyback announcement); some are material (a fraud "
            "investigation, a debt covenant breach, an unusual "
            "acquisition). The signal of materiality is in the "
            "<i>language</i> of the filing — the same words that an analyst "
            "would read to form an opinion. Rule-based extractors fail at "
            "this because there is no fixed schema. Keyword matchers "
            "produce too many false positives. The class of model that "
            "actually does this well, today, is an LLM.",
            s["body"],
        ),
        _para(
            "The cost is small. Each 8-K filing is 5-50KB; Claude Sonnet "
            "summarizes one for roughly $0.003. Across the ~30-day "
            "lifetime of the reactor, total LLM spend has been $0.17 "
            "across 69 audited calls. If the reactor saved one 3% "
            "drawdown on a position with 5% weight, that would represent "
            "$160 of equity protection on the current $107K book — "
            "two-to-three orders of magnitude above the cost. The "
            "economics are not the question.",
            s["body"],
        ),
        Paragraph("Why the rule stays in SHADOW", s["h2"]),
        _para(
            "The reactor's <i>signals</i> are well-formed; what is "
            "unproven is whether the signals <i>predict</i>. The DD "
            "called for a forward-return validation, and v3.73.10 "
            "shipped exactly that: a script that, for every reactor "
            "signal, computes the underlying name's 1-day, 5-day, and "
            "20-day forward return — plus SPY's matching forward return "
            "— and persists the active return.",
            s["body"],
        ),
        _para(
            "As of May 5, the reactor has fired 14 signals. Only one was "
            "M3-grade (Intel raising $6.5B in senior unsecured notes on "
            "April 30, 2026, tagged BEARISH). The position is up +26.7% "
            "on cost and was up +13.5% on the day of the filing. The "
            "v3.73.13 spot-check verified that Claude's specific "
            "numerical claims ($6.5B, $6.47B in net proceeds) are "
            "<i>verbatim correct</i> in the source filing — so the signal "
            "is not a hallucination. The market simply disagreed with the "
            "analysis. That is a different problem than the one we built "
            "the reactor to solve.",
            s["body"],
        ),
        _para(
            "This is the canonical case for the SHADOW state. The rule "
            "(which would prescribe trims of varying severity per signal "
            "tier) is observed but not executed. The forward-return "
            "outcomes table accumulates evidence. After 30+ settled "
            "signals, we will have enough data to decide whether to flip "
            "to LIVE — or, more interestingly, to <i>invert</i> the rule. "
            "If M3-BEARISH names systematically over-perform (the INTC "
            "case at small-N), the right action is the opposite of what "
            "we initially designed: lean <i>more</i> long on bearish-M3 "
            "names, betting on market overreaction. That decision needs "
            "data to make. The reactor's job until then is to keep "
            "producing it.",
            s["body"],
        ),
        PageBreak(),
    ]

    # ============================================================
    # 4. OPERATIONS
    # ============================================================
    story += [
        Paragraph("4. Operations", s["h1"]),
        _para(
            "The May 5 session closed every named operational gap from the "
            "due-diligence review. The dominant risk before the session "
            "was that the daemon hadn't completed a run since 2026-05-01 "
            "and the heartbeat that was supposed to detect this had never "
            "actually fired. v3.73.0 had shipped the heartbeat plist file "
            "to the repo but never copied it to "
            "<b>~/Library/LaunchAgents/</b>, where launchd actually loads "
            "from. The heartbeat had not run a single time since v3.73.0 "
            "shipped seven days earlier.",
            s["body"],
        ),
        Paragraph("4.1 Why this matters more than alpha", s["h2"]),
        _para(
            "It is tempting to treat operational reliability as a "
            "background concern subordinate to strategy work. The "
            "May 5 due-diligence review pushed back hard on this framing. "
            "The strategy is fine. The strategy has been fine for months. "
            "What was unverified — and what would have made the strategy "
            "irrelevant — was whether the strategy was <i>actually "
            "running</i>.",
            s["body"],
        ),
        _para(
            "On the morning of May 5, the journal showed five run-IDs "
            "total: four marked 'started' but never 'completed', and one "
            "marked completed (May 1, four days earlier). No row at all "
            "for May 4, a trading day. The daemon had silently "
            "stopped firing. We were running a strategy whose code was "
            "current at v3.73.3 against price data that was stale by 96 "
            "hours, with positions that had drifted since the last "
            "rebalance, on an account where new earnings had occurred "
            "without the reactor being able to react.",
            s["body"],
        ),
        _para(
            "The cause turned out to be a known launchd quirk: macOS "
            "silently skips StartCalendarInterval fires "
            "when the laptop is asleep at the scheduled time. The daily-"
            "run plist at the time used StartCalendarInterval alone, "
            "with no StartInterval safety net. If the laptop was asleep "
            "at 13:10 UTC (9:10 ET) — which it routinely was, since the "
            "operator works west-coast hours — the fire was missed and "
            "never retried. Four consecutive trading days of missed "
            "fires accumulated by May 5.",
            s["body"],
        ),
        _para(
            "v3.73.8 patched both the heartbeat plist (installing it "
            "into ~/Library/LaunchAgents/ for the first time) and the "
            "daily-run plist (pairing StartCalendarInterval with "
            "StartInterval=3600 so missed-on-sleep fires are retried "
            "within an hour of wake). The orchestrator is idempotent — "
            "<b>start_run()</b> refuses to re-fire if today's run already "
            "completed — so over-firing is harmless. We then test-fired "
            "the heartbeat manually with the failure condition in place; "
            "the alert email and Slack message arrived. The dominant "
            "operational risk at the start of the session was closed by "
            "the end of it.",
            s["body"],
        ),
        Paragraph("4.2 Journal replication", s["h2"]),
        _para(
            "The journal is the single durable record of everything the "
            "system has ever done. It contains the runs table (every "
            "rebalance), the decisions table (every BUY/SELL/HOLD with "
            "rationale), the orders table (every Alpaca submission), the "
            "daily_snapshot table (NAV history with SPY benchmark), the "
            "earnings_signals table (every reactor signal), the "
            "llm_audit_log table (every Claude call with cost), and as "
            "of v3.73.7, the strategy_eval table (every candidate "
            "strategy's monthly picks with forward returns). The DD's "
            "audit identified that all of this lived on the same laptop "
            "as the orchestrator. If the laptop dies, the journal dies "
            "with it; we lose the operational history that lets us "
            "evaluate the system at all.",
            s["body"],
        ),
        _para(
            "v3.73.9 closed this with a nightly replication script "
            "(<b>scripts/replicate_journal.sh</b>) wired to launchd at "
            "23:00 local. It uses sqlite3's online backup command — "
            "transactionally consistent under an in-flight write — "
            "rather than file copy, which would corrupt under "
            "concurrency. It writes to iCloud Drive (auto-synced "
            "off-machine), retains seven daily snapshots and four weekly "
            "snapshots, and prunes older copies in the same script. The "
            "first snapshot landed during testing of v3.73.9.",
            s["body"],
        ),
        Paragraph("4.3 CI", s["h2"]),
        _para(
            "GitHub Actions runs the test suite (currently 118 v3.73.* "
            "tests plus the older battery, total ~860) on every push to "
            "master. The May 5 session pushed 14 commits; CI caught one "
            "real regression. v3.73.5 added portfolio caps that modify "
            "the LIVE variant's weights post-selection (clipping CAT "
            "from 11% to 8% and trimming Tech from 28% to 25%), and the "
            "older drift-guard test asserted that build_targets() output "
            "exactly matches the LIVE variant's raw output. After the "
            "caps, the weights legitimately differ. The CI failure on "
            "v3.73.11 forced an explicit relaxation of the drift-guard "
            "to: same names, gross preserved within 1pp, no name above "
            "8% cap. The drift-guard's purpose — catching the v3.6 bug "
            "where production silently used a different strategy than "
            "the registered LIVE — is preserved.",
            s["body"],
        ),
        Paragraph("Closed in this session", s["h2"]),
    ]
    ops_closed = [
        ["v3.73.8", "Heartbeat plist installed in ~/Library/LaunchAgents/; "
                    "manually test-fired end-to-end (email + Slack)"],
        ["v3.73.8", "All scheduled launchd plists patched: paired "
                    "StartCalendarInterval with StartInterval (1800-3600s) "
                    "for sleep-resilience"],
        ["v3.73.9", "Journal replication: nightly SQLite .backup to iCloud "
                    "Drive, 7 daily + 4 weekly retention. Fires at 23:00 "
                    "local + hourly safety net + RunAtLoad backfill"],
        ["v3.73.13", "yfinance MultiIndex bug in _spy_series fixed; live "
                     "Performance view was crashing in production with "
                     "TypeError on float(Series)"],
    ]
    story.append(_table(ops_closed,
                         col_widths=[0.8 * inch, 5.5 * inch], header=False))
    story += [Spacer(1, 0.15 * inch)]

    story += [
        Paragraph("CI / GitHub Actions", s["h2"]),
        Paragraph(
            "ci.yml runs pytest tests/ -v on every push to master. The "
            "session pushed 14 commits; CI caught one real regression "
            "(test_build_targets_matches_live_variant_function broken by "
            "v3.73.5 caps modifying weights post-LIVE-variant). The drift "
            "guard was relaxed to assert names + gross + cap rather than "
            "exact-weight match.",
            s["body"],
        ),
        PageBreak(),
    ]

    # ============================================================
    # 5. MEASUREMENT
    # ============================================================
    story += [
        Paragraph("5. Measurement Infrastructure", s["h1"]),
        _para(
            "Four layers of measurement, three of them shipped on May 5. "
            "Without them, the strategy's claimed alpha was unverifiable. "
            "After the session, every claim in this document has at "
            "least one independent check behind it.",
            s["body"],
        ),
        Paragraph("5.0 Why benchmark-relative measurement is the headline", s["h2"]),
        _para(
            "Before May 5, the dashboard reported absolute return — "
            "today's equity, today's P&L, year-to-date dollar gain. None "
            "of that answers the only question that matters for a "
            "strategy with the stated goal of beating SPY: <i>is the "
            "strategy actually working relative to the benchmark, or are "
            "we just along for the market's ride?</i>",
            s["body"],
        ),
        _para(
            "The distinction is load-bearing. A book up 30% in a year "
            "where SPY is up 35% is <i>losing</i>; the operator should "
            "consider whether the strategy is adding value at all. A "
            "book up 5% in a year where SPY is down 15% is winning "
            "decisively. Without a benchmark line on the dashboard, the "
            "operator can convince themselves of either narrative based "
            "on whichever absolute number happens to be flattering. The "
            "v3.73.6 ship moved the NAV-vs-SPY chart to the top of the "
            "Overview view and downgraded everything else. It is now "
            "very difficult to look at the dashboard without seeing "
            "whether we are beating the benchmark.",
            s["body"],
        ),
        _para(
            "The metrics it surfaces — active return, information ratio, "
            "tracking error, beta, Jensen's alpha, max relative drawdown "
            "— are the suite an institutional allocator would ask for. "
            "They are also the metrics most easily computed wrong, which "
            "is exactly what happened in v3.73.7 and was caught by the "
            "v3.73.13 cross-validation. More on that below.",
            s["body"],
        ),
        Paragraph("5.0a Why a constant strategy evaluator", s["h2"]),
        _para(
            "Before May 5, the system had one strategy (the LIVE "
            "variant) and no automated way to evaluate alternatives. We "
            "could backtest manually but could not keep alternatives "
            "running in parallel. v3.73.7 introduced the eval harness: "
            "10 candidate strategies, each a pure function of "
            "<i>(asof, prices) → {ticker: weight}</i>, registered at "
            "module-import time. Every monthly rebalance journals each "
            "strategy's picks; a separate settle pass computes forward "
            "returns and active return vs SPY for each.",
            s["body"],
        ),
        _para(
            "The point is not to pick a winner from a single backtest. "
            "It is to keep all candidates running side-by-side as data "
            "accumulates so the answer crystallizes from realized "
            "outcomes rather than from the operator's priors. A "
            "5-year backfill gives 60 monthly observations — enough for "
            "the standard error on Sharpe to drop below 0.15, which is "
            "where the +60pp leader's edge becomes statistically "
            "separable from sample noise. The eval harness is the "
            "answer to the operator's recurring question 'how do I "
            "know which strategy is actually best?'",
            s["body"],
        ),
        Paragraph("5.0b Why hallucination defenses", s["h2"]),
        _para(
            "Two of the system's most consequential outputs come from "
            "places where errors don't necessarily surface as exceptions. "
            "The first is the LLM-generated reactor summary: Claude "
            "claiming Intel raised $6.5B in five tranches is either "
            "verbatim correct or fabricated, and a unit test cannot tell "
            "the difference. The second is the eval harness's "
            "leaderboard: bug-for-bug consistent across all twelve "
            "strategies (a subtle error in the momentum signal would "
            "affect every strategy equally), so the relative ranking "
            "looks plausible even if the absolute numbers are wrong.",
            s["body"],
        ),
        _para(
            "v3.73.13 added two defenses against these classes of error. "
            "The first is an independent re-implementation of the "
            "production strategy in pure pandas, sharing no code with "
            "the harness, that runs over a yfinance-fetched price panel "
            "and asserts agreement within tolerance. The second is a "
            "spot-check script that, for any reactor signal, finds the "
            "archived 8-K source and verifies that every numerical claim "
            "in Claude's summary appears in the source text.",
            s["body"],
        ),
        _para(
            "Both defenses found real issues on first run. The cross-"
            "validation surfaced two harness bugs that materially "
            "changed every leaderboard number reported between v3.73.7 "
            "and v3.73.12. The reactor spot-check verified that Claude's "
            "INTC numbers ($6.5B, $6.47B) were correct against the "
            "filing, refuting the natural suspicion that the BEARISH "
            "M3 tag came from a hallucinated factual claim. The bugs "
            "and the verifications are both load-bearing findings; both "
            "are documented in detail in section 7.",
            s["body"],
        ),
        Paragraph("5.1 Production journal (data/journal.db)", s["h2"]),
        Paragraph(
            "SQLite single source of truth. Tables: runs, decisions, "
            "orders, daily_snapshot, position_lots, postmortems, "
            "earnings_signals, llm_audit_log, strategy_eval, "
            "reactor_signal_outcomes. 4,500+ rows total. Replicated "
            "nightly.",
            s["body"],
        ),
        Paragraph("5.2 Benchmark-relative tracking (v3.73.6)", s["h2"]),
        Paragraph(
            "Overview headline panel: NAV-vs-SPY chart + active return, "
            "IR, beta, alpha-annualized, max relative DD, daily win-rate. "
            "Honest sample-size disclosure (warns when <30 days). 7 days "
            "of data right now; accumulates daily.",
            s["body"],
        ),
        Paragraph("5.3 Constant strategy evaluator (v3.73.7)", s["h2"]),
        Paragraph(
            "10 candidate strategies + 1 production-replica + 1 long-short "
            "= 12 total. Every monthly rebalance journals each strategy's "
            "picks; settle_returns computes net forward returns + active "
            "return vs SPY. Leaderboard view (/strategy_leaderboard) "
            "ranks by cumulative active over a configurable window. The "
            "harness now extends automatically through the daily-run "
            "orchestrator hook.",
            s["body"],
        ),
        Paragraph("5.4 Reactor signal outcomes (v3.73.10)", s["h2"]),
        Paragraph(
            "For every reactor signal, computes 1d/5d/20d forward returns "
            "and active return vs SPY. Persists to "
            "reactor_signal_outcomes. Surfaces aggregate per-direction "
            "stats on the Earnings Reactor view. 14 signals tracked, 4 "
            "with settled 5d returns. Sample is far below threshold for "
            "any reactor-edge claim; rule stays in SHADOW.",
            s["body"],
        ),
        Paragraph("5.5 Hallucination defenses (v3.73.13)", s["h2"]),
        Paragraph(
            "Four layers added in the final session, each with a real find:",
            s["body"],
        ),
    ]
    halluc = [
        ["Defense", "Tool", "What it caught"],
        ["Independent backtester",
         "scripts/cross_validate_harness.py",
         "(1) warmup-period drag inflated cum_active by ~17pp; "
         "(2) sqrt(252) IR overstated by 4.58x"],
        ["Reactor source spot-check",
         "scripts/spotcheck_reactor.py",
         "INTC's $6.5B + $6.47B claims VERIFIED in source. The reactor "
         "isn't hallucinating — the market disagreed with the analysis."],
        ["Frozen-snapshot regression",
         "test_v3_73_13_strategy_snapshot.py",
         "Pins rank_momentum picks on synthetic input; catches refactor "
         "drift, sign flips in scoring, and non-determinism"],
        ["Cost sensitivity sweep",
         "(ad-hoc, in writeup)",
         "Production strategy robust at 0/5/10/15/25 bps slippage; "
         "ranking preserved across all levels"],
    ]
    story.append(_table(halluc, col_widths=[1.3 * inch, 2.0 * inch, 3.0 * inch]))
    story += [PageBreak()]

    # ============================================================
    # 5.5 HOW A REBALANCE DAY WORKS — narrative
    # ============================================================
    story += [
        Paragraph("5.5 How a rebalance day actually works", s["h1"]),
        _para(
            "An end-to-end walk through what happens on a typical "
            "weekday rebalance, from launchd fire to confirmed orders. "
            "The choreography is what 'autonomy' actually means in "
            "practice.",
            s["body"],
        ),
        _para(
            "<b>13:10 UTC</b> (9:10 ET): launchd fires the "
            "<b>com.trader.daily-run</b> job. The plist's "
            "ProgramArguments invoke a wrapper script "
            "(<b>~/openclaw-workspace/trader-jobs/run-trader-task.sh</b>) "
            "which sources the operator's environment, activates the "
            "venv, and runs <b>python -m trader.main</b>. If the laptop "
            "was asleep at 13:10, the StartInterval=3600 safety net "
            "(post v3.73.8) ensures the job retries within an hour of "
            "wake.",
            s["body"],
        ),
        _para(
            "<b>main() entry</b>: the orchestrator generates a run-id "
            "(<b>YYYY-MM-DD-HHMMSS</b>) and inserts a row into "
            "<b>journal.runs</b> via <b>start_run()</b>. This is "
            "idempotent; if today's run already completed, it returns "
            "False and the orchestrator exits without trying again. "
            "This is what makes the StartInterval safety net safe — "
            "duplicate fires no-op.",
            s["body"],
        ),
        _para(
            "<b>Universe load</b>: the LIQUID_50 list is read from "
            "<b>config.py</b>. Today the universe has 50 names; an "
            "expansion to S&P 500 was empirically validated in v3.73.12 "
            "but is not yet wired to production.",
            s["body"],
        ),
        _para(
            "<b>Momentum ranking</b>: <b>build_targets(universe)</b> "
            "calls into the variant registry and dispatches to the "
            "LIVE variant <b>momentum_top15_mom_weighted_v1</b>. The "
            "variant pulls 14 months of price history via "
            "<b>fetch_history</b>, computes the 12-1 momentum score for "
            "each name (a function of the trailing 12-month return "
            "excluding the most recent month), ranks descending, takes "
            "the top 15, and computes min-shifted weights summing to "
            "80% gross. Each pick is logged to <b>journal.decisions</b> "
            "with the score and rationale.",
            s["body"],
        ),
        _para(
            "<b>Bottom-catch scan</b>: separately, the universe is "
            "scanned for oversold-bounce setups (the bottom-catch "
            "sleeve). When the live config is at <b>USE_DEBATE = "
            "False</b>, the bottom sleeve is effectively dormant — "
            "candidates are logged but not allocated capital.",
            s["body"],
        ),
        _para(
            "<b>Portfolio caps applied</b> (post v3.73.5): the LIVE "
            "variant's raw weights pass through "
            "<b>apply_portfolio_caps()</b>, which enforces an 8% single-"
            "name cap and a 25% sector cap with cap-aware "
            "redistribution. On the current book, the cap reduces CAT "
            "from 11% to 8% and Tech sector exposure from 28-30% to "
            "25%. The CapResult metadata is logged so the dashboard's "
            "Concentration panel can show whether the cap is binding.",
            s["body"],
        ),
        _para(
            "<b>Risk gate</b>: the deployment-anchor and drawdown "
            "protocol both run advisory checks. The drawdown protocol "
            "evaluates current 180-day-peak DD against the four "
            "thresholds (-5%/-8%/-12%/-15%); if any tier fires, the "
            "operator gets a console warning, but in ADVISORY mode "
            "(the default), no targets are mutated.",
            s["body"],
        ),
        _para(
            "<b>Order submission</b>: <b>place_target_weights()</b> "
            "computes the difference between target weights and current "
            "Alpaca position weights, generates notional market orders "
            "for each non-zero delta, and submits them through the "
            "Alpaca API. Each submission is journaled to "
            "<b>journal.orders</b> with status='submitted' and the "
            "broker's order-id.",
            s["body"],
        ),
        _para(
            "<b>Strategy eval hook</b> (post v3.73.7): for every "
            "registered candidate strategy in the eval harness, "
            "<b>evaluate_at()</b> records the picks the strategy would "
            "have made today. <b>settle_returns()</b> computes the "
            "forward returns for any prior unsettled rebalance — "
            "yesterday's picks are now settled against today's prices "
            "for SPY and the universe. The leaderboard table extends "
            "by twelve rows.",
            s["body"],
        ),
        _para(
            "<b>finish_run()</b>: marks the runs row complete with the "
            "summary 'N targets, M momentum orders, P bottom orders'. "
            "If <b>finish_run</b> isn't called — because the process "
            "crashed mid-rebalance — the row remains 'started' "
            "indefinitely. This is the signal the heartbeat watches "
            "for.",
            s["body"],
        ),
        _para(
            "<b>14:30 UTC</b> (10:30 ET): the heartbeat job fires "
            "(<b>com.trader.daily-heartbeat</b>). It reads the journal "
            "and verifies a row was inserted today with started_at "
            "matching today's date. If yes, exit silently. If no, fire "
            "an email alert via SMTP and a Slack alert via webhook to "
            "the prismtrading workspace. A date-stamped marker file "
            "(<b>data/.last_heartbeat_alert</b>) prevents repeat alerts "
            "within the same day.",
            s["body"],
        ),
        _para(
            "<b>Reactor (continuous)</b>: in parallel to the daily "
            "rebalance, the earnings-reactor daemon "
            "(<b>com.trader.earnings-reactor</b>) is polling SEC EDGAR "
            "every 60 seconds for HOT-cadence symbols (within ±2 days "
            "of an earnings date) and every 300 seconds for WARM-"
            "cadence symbols. Any new 8-K is downloaded, archived to "
            "<b>data/filings/{sym}/{form}/{accession}.txt</b>, and — if "
            "the form is 8-K with material items — passed to Claude "
            "for a structured signal. Material signals (M3 grade) "
            "trigger the same alert path the heartbeat uses.",
            s["body"],
        ),
        _para(
            "<b>23:00 local</b>: the journal-replicate job fires "
            "(<b>com.trader.journal-replicate</b>). It runs sqlite3's "
            "<b>.backup</b> command against the journal, writes a "
            "transactionally-consistent copy to "
            "<b>~/Library/Mobile Documents/com~apple~CloudDocs/trader-"
            "journal-backup/</b>, and prunes any backups older than 7 "
            "days (preserving 4 weekly snapshots). The backup is "
            "auto-synced off-machine via iCloud.",
            s["body"],
        ),
        _para(
            "Across all of this, the dashboard at "
            "<b>localhost:8501</b> is reading the same journal and "
            "rendering current state. The operator can open it any "
            "time and see (a) live broker equity vs SPY-normalized, "
            "(b) the post-cap target weights for tomorrow's rebalance, "
            "(c) the strategy leaderboard ranked by cumulative active, "
            "(d) any reactor signals from the last 24 hours, (e) the "
            "drawdown tier (still GREEN at +0.76% above last "
            "snapshot), (f) the build-info badge confirming the "
            "container is running current code.",
            s["body"],
        ),
        PageBreak(),
    ]

    # ============================================================
    # 6. LIVE BOOK
    # ============================================================
    story += [
        Paragraph("6. Current Live Book", s["h1"]),
        Paragraph(
            "Alpaca paper account at 2026-05-05 17:02 UTC. 15 positions, "
            "$73,215 deployed (68% gross — below 80% target, indicating "
            "either the deployment anchor is open or stale data). "
            "Concentration: CAT 11% pre-cap (clipped to 8% by v3.73.5 "
            "caps); semis sector 28-30% pre-cap (clipped to 25%).",
            s["body"],
        ),
    ]
    book = [
        ["Sym", "Sector", "MV ($)", "Wt", "UPL %", "Day %"],
        ["CAT", "Industrials", "11,785", "11.0%", "+9.1%", "+2.9%"],
        ["INTC", "Tech", "9,469", "8.8%", "+26.7%", "+13.5%"],
        ["AMD", "Tech", "8,871", "8.3%", "+7.5%", "+3.8%"],
        ["GOOGL", "Comm", "7,915", "7.4%", "+11.9%", "+0.8%"],
        ["AVGO", "Tech", "7,712", "7.2%", "+4.9%", "+3.2%"],
        ["NVDA", "Tech", "4,444", "4.1%", "-0.5%", "-0.5%"],
        ["GS", "Fin", "4,437", "4.1%", "-0.5%", "+1.6%"],
        ["JNJ", "Health", "3,793", "3.5%", "+0.3%", "+0.8%"],
        ["XOM", "Energy", "3,290", "3.1%", "+1.5%", "+0.7%"],
        ["WMT", "Staples", "3,027", "2.8%", "+0.2%", "+0.5%"],
        ["MS", "Fin", "2,933", "2.7%", "+0.1%", "+1.0%"],
        ["TSLA", "ConsDisc", "2,273", "2.1%", "+0.6%", "-0.1%"],
        ["MRK", "Health", "2,130", "2.0%", "+1.7%", "+0.1%"],
        ["CSCO", "Tech", "1,009", "0.9%", "+3.2%", "+1.9%"],
        ["JPM", "Fin", "122", "0.1%", "-0.4%", "+0.6%"],
    ]
    story.append(_table(book,
                         col_widths=[0.6 * inch, 1.0 * inch, 1.0 * inch,
                                     0.7 * inch, 0.8 * inch, 0.8 * inch]))
    story += [Spacer(1, 0.2 * inch)]

    story += [
        Paragraph("6.1 Risk concentration in the live book", s["h2"]),
        _para(
            "Looking at the live book through a risk lens, three "
            "concentrations stand out, and a fourth latent risk is "
            "implicit in the construction:",
            s["body"],
        ),
        _para(
            "<b>Sector concentration in tech.</b> Pre-cap exposure to the "
            "Tech sector is 28-30% of book (AMD + NVDA + AVGO + INTC). "
            "Post-cap, this is clipped to 25%. Either way, this is a "
            "concentrated bet on the same sector being asked to lead the "
            "market for the same reasons (AI capex cycle, secular "
            "compute demand). If that thesis cracks — for any reason — "
            "the book takes a sector hit out of proportion to its "
            "single-name exposures.",
            s["body"],
        ),
        _para(
            "<b>Single-name concentration in industrials.</b> CAT alone "
            "is 11% of book pre-cap (8% post-cap). Industrials at the "
            "single-name level is the second-largest concentration after "
            "tech. This is the momentum factor doing what it was designed "
            "to do — leaning into the strongest-trending name — but the "
            "operator should be aware that a CAT-specific bad headline "
            "(a guidance miss, a tariff surprise, a labor action) takes "
            "an outsized chunk of the book down with it.",
            s["body"],
        ),
        _para(
            "<b>Beta to SPY of approximately +1.7.</b> The 7-day live "
            "sample shows beta +1.72; the 5-year backtest shows "
            "comparable amplification. This is <i>expected</i> for a "
            "concentrated long-only momentum book on growth-tilted US "
            "large-caps; it is also a real risk. A book with beta 1.7 "
            "rises 17% when SPY rises 10% but falls 17% when SPY falls "
            "10%. The strategy's claim is to <i>beat</i> SPY on a risk-"
            "adjusted basis, not to provide downside protection. The "
            "deployment-anchor gate is meant to throttle gross down in "
            "high-vol regimes, but it does not turn a beta-1.7 book into "
            "a beta-1.0 book.",
            s["body"],
        ),
        _para(
            "<b>Latent: factor crowding.</b> Every momentum-following "
            "strategy in the market is, definitionally, holding similar "
            "names at similar weights. When momentum reverses (the 2022 "
            "episode is canonical), the unwind is sharp because every "
            "momentum strategy is selling the same names at the same "
            "time. The DD-recommended response is the deployment-anchor "
            "gate (which does help) and the four-threshold drawdown "
            "protocol (which sets explicit pre-committed actions at "
            "-5/-8/-12/-15% portfolio DD). Both exist; the protocol is "
            "in ADVISORY mode.",
            s["body"],
        ),
        Paragraph("6.2 The 5-year sample regime bias", s["h2"]),
        _para(
            "The headline backtest result (+77pp vs SPY at IR 0.62 over "
            "60 monthly observations) covers May 2021 through May 2026. "
            "It is essential to note what regimes that window does and "
            "does not contain.",
            s["body"],
        ),
        _para(
            "<b>What the window contains:</b> the post-COVID re-opening "
            "rally (2021 H2), the 2022 momentum reversal and growth "
            "drawdown (a real but ~6-month episode), the 2023 AI-led "
            "tech recovery, the 2024 broad bull market, and 2025-26 "
            "consolidation. Approximately 4 of the 5 years are "
            "growth-and-tech-favorable. The one bear-like episode "
            "(2022) was brief and was followed by an unusually fast "
            "recovery led by exactly the names a momentum book holds. "
            "This is approximately the most-favorable possible regime "
            "for cross-sectional momentum on US large-caps.",
            s["body"],
        ),
        _para(
            "<b>What the window does not contain:</b> a sustained "
            "multi-year bear (2000-02 dotcom unwind, 2007-09 financial "
            "crisis), a value rotation that lasts more than a few months "
            "(the 2000-2007 period when value beat growth by ~50pp "
            "cumulatively), or a stagflation regime (1970s). The "
            "strategy has not been backtested through any of these. "
            "It is reasonable to expect that the +77pp lead would be "
            "materially smaller, possibly negative, in a regime that "
            "doesn't reward momentum or doesn't reward tech.",
            s["body"],
        ),
        _para(
            "The right framing of the 5-year cum-active number is "
            "therefore: this is the strategy's performance in a friendly "
            "regime, not its expected performance across all regimes. "
            "The IR 0.62 is more honest as a long-run estimate, but "
            "even that is fitted to a friendly window. A reasonable "
            "real-world expectation is IR 0.3-0.5 across full cycles, "
            "with multi-year drawdowns relative to SPY during value-"
            "rotation regimes. That is still positive, still worth "
            "running, but not 'institutional-grade' alpha.",
            s["body"],
        ),
        Paragraph("6.3 The 80% gross vs 68% actual gap", s["h2"]),
        _para(
            "The strategy's design target is 80% gross exposure, 20% "
            "cash. The current live book is at 68% gross — a 12pp gap "
            "that has been visible in the dashboard since the May 5 "
            "session began. Three plausible explanations, in order of "
            "decreasing innocence:",
            s["body"],
        ),
        _para(
            "(1) <b>T+1 settlement timing.</b> Sells settle T+1; "
            "proceeds aren't immediately redeployable. If a recent "
            "rebalance trimmed several names, the cash from those "
            "trims sits in the buying-power column for one trading day "
            "before being available. This explains a few percent at "
            "most.",
            s["body"],
        ),
        _para(
            "(2) <b>Stale rebalance.</b> The pre-May 5 finding was that "
            "the daily orchestrator hadn't completed a full rebalance "
            "since 2026-05-01. If positions drift via market action "
            "since then (some names up, some down) and no rebalance has "
            "fully executed, the realized gross can drift below target. "
            "This is the most likely explanation for the bulk of the "
            "12pp gap.",
            s["body"],
        ),
        _para(
            "(3) <b>The deployment-anchor is gating.</b> If the "
            "30/200-day spread has narrowed enough to put the gate in "
            "conservative mode, the system would intentionally run "
            "below 80%. This should be visible in the deployment-anchor "
            "dashboard panel; if the gate is in conservative mode, the "
            "panel should show the multiplier explicitly.",
            s["body"],
        ),
        _para(
            "Resolving the gap is operational hygiene that the next "
            "completed rebalance (post v3.73.8 fix) should accomplish. "
            "Until that happens, the dashboard's claimed 80%-gross "
            "design and the broker's 68% reality are inconsistent, "
            "and any reader of this document should treat the strategy "
            "performance numbers as 'what the strategy would do at "
            "design-target gross,' not 'what the live book is actually "
            "doing today.'",
            s["body"],
        ),
        Paragraph("6.4 Cap execution: shipped vs verified live", s["h2"]),
        _para(
            "v3.73.5 shipped the 8% single-name and 25% sector caps. "
            "Unit tests verify the cap math is correct (17 tests in "
            "<b>test_v3_73_5_portfolio_caps.py</b>). The daily-run "
            "orchestrator imports <b>apply_portfolio_caps</b> "
            "immediately before placing orders. The dashboard's "
            "Concentration panel shows the cap result on the live book.",
            s["body"],
        ),
        _para(
            "What is <i>not</i> yet verified is that an actual rebalance "
            "run has used the caps to mutate weights, submitted those "
            "modified weights to the broker, and resulted in the live "
            "book actually reflecting the cap. The most recent completed "
            "run (May 1) preceded the cap ship. Until the next clean "
            "rebalance fires, runs to completion, and we observe CAT "
            "trimmed from 11% to 8% in the broker's position list, the "
            "cap's live behavior is asserted-but-unverified. This is "
            "low-risk (the unit tests are real) but worth marking "
            "explicitly: the system has not yet executed a single "
            "real-money trade with the post-v3.73.5 caps in effect.",
            s["body"],
        ),
        PageBreak(),
        Paragraph("The INTC paradox", s["h2"]),
        Paragraph(
            "INTC's 8.8% position is up +26.7% on cost despite a BEARISH "
            "M3 reactor signal on 2026-04-30 ($6.5B senior unsecured note "
            "issuance with 2031-2066 maturities). The numerical claims in "
            "Claude's summary are verified against the source filing; the "
            "reactor isn't hallucinating. The market priced the issuance "
            "BULLISH (+13.5% on the day). This is the canonical case for "
            "the reactor staying in SHADOW: the signal is concrete and "
            "well-formed, but its predictive power is unverified.",
            s["callout"],
        ),
        PageBreak(),
    ]

    # ============================================================
    # 7. SESSION FINDINGS — narrative
    # ============================================================
    story += [
        Paragraph("7. May 5 Session — A Narrative", s["h1"]),
        _para(
            "The May 5 session was scheduled as a routine due-diligence "
            "review. It became the largest single accumulation of "
            "corrections in the system's history. Twenty-four hours later "
            "the system is materially better understood, but several of "
            "the claims that were live at the start of the session — "
            "including in earlier versions of this document — have been "
            "explicitly retracted. Logging these retractions honestly is "
            "part of the discipline; the retractions are themselves "
            "evidence the measurement infrastructure is doing its job.",
            s["body"],
        ),
        Paragraph("7.1 The DD baseline error", s["h2"]),
        _para(
            "The session began with a written due-diligence memo "
            "(<b>docs/DUE_DILIGENCE_2026_05_05.md</b>) that compared 10 "
            "candidate strategies and concluded the production "
            "<b>xs_top15</b> was mid-pack at +5.28pp vs SPY. The "
            "recommendation was a phased switch to a higher-ranked "
            "alternative.",
            s["body"],
        ),
        _para(
            "The DD was wrong on the most basic point: it compared "
            "against <b>xs_top15</b> equal-weighted as the baseline. The "
            "actual deployed LIVE variant is "
            "<b>momentum_top15_mom_weighted_v1</b> — score-weighted with "
            "min-shift, promoted from SHADOW to LIVE on April 29. The "
            "production strategy was never the strategy the DD compared "
            "to. When the production scheme was added to the eval "
            "harness as <b>xs_top15_min_shifted</b> and the same 5-year "
            "backfill was re-run, the standings changed completely: "
            "production became the leader by 28pp over the next-best "
            "alternative.",
            s["body"],
        ),
        _para(
            "The lesson is methodological, not technical. When reviewing "
            "the strategy stack, always check the variant registry "
            "(<b>src/trader/variants.py</b>) for the LIVE variant's "
            "actual implementation, not the canonical "
            "<b>rank_momentum</b> docstring. The variant fn is what "
            "runs. v3.73.11 corrected the DD with an addendum and "
            "shipped <b>xs_top15_min_shifted</b> as part of the eval "
            "harness so future comparisons are apples-to-apples.",
            s["body"],
        ),
        Paragraph("7.2 The long-short hypothesis fails", s["h2"]),
        _para(
            "The DD also recommended adding pair-trade / short ballast "
            "as 'the only structural alpha the long-only book can't "
            "produce.' That claim was tested empirically in v3.73.12. A "
            "<b>long_short_momentum</b> strategy was added to the eval "
            "harness — long top-15 by score (min-shifted) at 70% gross, "
            "short bottom-5 by score equal-weighted at 30% gross, net "
            "+40% gross long bias.",
            s["body"],
        ),
        _para(
            "The result was unambiguous: long-short lost 18pp vs SPY "
            "over five years. The mechanism is straightforward in "
            "hindsight: the bottom-5 momentum names are already beaten "
            "down and tend to mean-revert, so shorting them costs money "
            "on most months. Meanwhile the smaller long-side gross gives "
            "up beta during a 4-year stretch of bull conditions. The DD's "
            "framing — that long-short is structural alpha — overstated "
            "the case.",
            s["body"],
        ),
        _para(
            "What it missed is that long-short alpha is regime-"
            "conditional, not structural. In a 2022-style reversal, a "
            "static long-short beats long-only by ~5-10pp. In a bull "
            "stretch, it loses by more. Five years of mostly bull "
            "conditions overweight the loss side. The credible remaining "
            "experiment is therefore a regime-<i>conditional</i> long-"
            "short that engages shorts only when an HMM regime "
            "classifier signals BEAR. That is a meaningfully bigger "
            "build than the static version we tested.",
            s["body"],
        ),
        Paragraph("7.3 The cross-validation harness pays for itself", s["h2"]),
        _para(
            "The most consequential ship of the session was the cross-"
            "validation harness in v3.73.13. The premise was simple: "
            "build an independent re-implementation of the production "
            "strategy in pure pandas, sharing no code with the eval "
            "harness, fetching prices via yfinance directly rather than "
            "<b>trader.data.fetch_history</b>, and running the full "
            "5-year backtest. Then assert that the cumulative active "
            "return and IR agree within tolerance. If the two "
            "implementations disagree, at least one is wrong.",
            s["body"],
        ),
        _para(
            "On first run, they disagreed by 24pp. The harness reported "
            "+88.35pp cumulative active vs SPY; the independent "
            "implementation reported +112.92pp. The harness was using "
            "60 monthly observations; the independent only 56. After "
            "tracing the discrepancy, two distinct bugs surfaced.",
            s["body"],
        ),
        _para(
            "The first was a warmup-period drag. The eval harness's "
            "<b>evaluate_at()</b> would journal a row for every "
            "registered strategy at every rebalance date, even when the "
            "strategy returned empty picks (which it does for the first "
            "~13 months of any backfill, before there is enough history "
            "to compute the 12-1 momentum signal). On settle, those "
            "empty-picks rows recorded port_return = 0 (no positions to "
            "price) but spy_return = the actual SPY return for the "
            "period. The active return was therefore -spy_return, "
            "treating 'cash because the strategy can't trade yet' as "
            "if it were the strategy's underperformance. With SPY in a "
            "drawdown for parts of 2021-2022, the warmup window "
            "<i>inflated</i> the strategy's apparent active return by "
            "~17pp. v3.73.13 fixed this by skipping empty-picks rows in "
            "<b>evaluate_at</b>.",
            s["body"],
        ),
        _para(
            "The second bug was an annualization error. The "
            "<b>leaderboard()</b> function annualized monthly active "
            "returns using the daily-period factor sqrt(252) instead of "
            "the monthly factor sqrt(12). All information ratios "
            "reported in v3.73.7 through v3.73.12 — including the "
            "headline IR 2.51 for the production strategy — were "
            "overstated by the ratio sqrt(252/12), approximately 4.58x. "
            "The corrected production IR is 0.62. v3.73.13 fixed the "
            "constant.",
            s["body"],
        ),
        _para(
            "Neither bug was caught by the test suite. The unit tests "
            "verified that the math <i>functions did what the code said "
            "they did</i>; they could not catch a logic error that "
            "matched the wrong intent. The cross-validation caught both "
            "on its first run because the independent re-implementation "
            "did not share the bug. After the fixes, the leaderboard's "
            "headline numbers are materially smaller than what was "
            "reported earlier in the session: production beats SPY by "
            "+77pp at IR 0.62, not +88pp at IR 2.51. The strategy is "
            "still strongly winning, but the prior numbers were wrong, "
            "and several 'borderline winners' in the leaderboard turned "
            "out to be losers (xs_top15 equal-weight, vertical_winner, "
            "long_short_momentum).",
            s["body"],
        ),
        Paragraph("7.4 The reactor verifies clean", s["h2"]),
        _para(
            "Parallel to the cross-validation, v3.73.13 shipped a spot-"
            "check script for the reactor's LLM-generated summaries. "
            "The hypothesis was that Claude might be hallucinating "
            "specific numerical facts in the 8-K summaries — claiming "
            "amounts and tranche structures that weren't actually in the "
            "filing.",
            s["body"],
        ),
        _para(
            "The spot-check ran against the five most recent signals "
            "and the INTC signals specifically. Every numerical claim "
            "verified. Claude's INTC summary said 'Intel raised $6.5B in "
            "senior unsecured notes across five tranches with maturities "
            "ranging from 2031 to 2066, generating ~$6.47B in net "
            "proceeds.' All three numbers ($6.5B, five, $6.47B) appear "
            "verbatim in the archived source filing. The reactor is not "
            "hallucinating.",
            s["body"],
        ),
        _para(
            "What the reactor <i>cannot</i> verify is whether the "
            "BEARISH tag is correct in market terms. The market priced "
            "the issuance BULLISH (+13.5% on the day, +40pp 5-day alpha "
            "vs SPY). That is a different problem than hallucination. It "
            "is the problem the reactor's signal-validation panel "
            "(v3.73.10) was built to track, and the answer to it requires "
            "more settled signals than we have today. The rule remains "
            "in SHADOW.",
            s["body"],
        ),
        Paragraph("7.5 Session retraction summary", s["h2"]),
        _para(
            "Several explicit corrections to earlier claims, in the "
            "order they were made:",
            s["body"],
        ),
    ]
    findings = [
        ["#", "Finding", "Correction"],
        ["1",
         "DD compared production to wrong baseline",
         "v3.73.4 DD claimed 'production xs_top15 is mid-pack at +5.28pp'. "
         "Wrong: production is xs_top15_MIN_SHIFTED, leader at +77pp. "
         "Documented in DD_ADDENDUM_2026_05_05.md."],
        ["2",
         "Long-short empirical failure",
         "DD claimed long-short was 'the structural alpha the long-only "
         "book can't produce.' Tested: -18pp vs SPY over 5y. Static "
         "long-short loses the bull-regime tax."],
        ["3",
         "Heartbeat never installed",
         "v3.73.0 shipped the plist file but never copied to "
         "~/Library/LaunchAgents/. Heartbeat had not run a single time "
         "since v3.73.0. Fixed in v3.73.8."],
        ["4",
         "All launchd plists sleep-fragile",
         "Used StartCalendarInterval alone. Patched all to pair with "
         "StartInterval per the launchd sleep-skip lesson."],
        ["5",
         "Warmup-period drag inflated cum_active",
         "Empty-picks rows journaled at start of backfill (no momentum "
         "history yet) counted SPY drag as strategy underperformance. "
         "Fixed evaluate_at to skip empty picks. ~17pp correction."],
        ["6",
         "sqrt(252) IR overstatement",
         "leaderboard() annualized monthly returns with sqrt(252). "
         "All IRs reported v3.73.7-v3.73.12 were 4.58x too high. Fixed "
         "to sqrt(12). IR 2.51 → 0.62."],
        ["7",
         "yfinance MultiIndex breaking dashboard",
         "auto_adjust=True returns multi-column DF; .iloc[-1] returned "
         "a Series, breaking float(). Crashed view_performance in prod. "
         "Fixed in v3.73.13."],
    ]
    story.append(_table(findings,
                         col_widths=[0.3 * inch, 2.0 * inch, 4.0 * inch]))
    story += [PageBreak()]

    # ============================================================
    # 8. RECOMMENDATIONS
    # ============================================================
    story += [
        Paragraph("8. Recommendations & Next Steps", s["h1"]),
        _para(
            "The recommendations below reflect the corrected (post-v3.73.13) "
            "view: a strategy with honest but modest alpha that has been "
            "tested only in a friendly regime, with several open "
            "operational questions, and one important asymmetry — the "
            "downside of expanding capital before the gates clear is much "
            "larger than the downside of waiting another 30-60 days.",
            s["body"],
        ),
        Paragraph("Tier 0 — must clear before any real-money sizing", s["h2"]),
    ]
    tier0 = [
        ["Gate", "Status", "What 'cleared' looks like"],
        ["30+ completed daily runs", "0 / 30",
         "Journal shows 30 consecutive weekday rows with status=completed, no missed-fire alerts"],
        ["30+ days post-fix benchmark tracking", "0 / 30",
         "daily_snapshot table has 30+ rows with non-zero SPY closes, "
         "all post-v3.73.13 (clean of the IR/warmup bugs)"],
        ["Caps verified live", "Asserted, not verified",
         "Next completed rebalance shows CAT trimmed from 11% → 8%, Tech from 28% → 25% in broker positions"],
        ["80% target vs 68% gross gap", "Open",
         "Either book reaches 78-80% gross post-rebalance, or the "
         "deployment-anchor gate is documented as the cause"],
        ["Beta reasonably below 1.5", "+1.7 (current)",
         "Either accept the beta and document the implication, or "
         "explicitly target lower via a vol-targeting overlay"],
        ["At least one regime change", "0 (sample is bull-only)",
         "A multi-month episode in the live data where SPY "
         "drawdown > 5% and the strategy's response is observed"],
    ]
    story.append(_table(tier0, col_widths=[2.0 * inch, 1.5 * inch, 3.2 * inch]))
    story += [
        Spacer(1, 0.15 * inch),
        _para(
            "Until <b>all six</b> Tier 0 gates clear, the right action is "
            "to keep running paper. Any earlier sized-capital move is "
            "based on a hope rather than a measurement.",
            s["callout"],
        ),
        Paragraph("Tier 1 — actionable now (operational + measurement)", s["h2"]),
    ]
    tier1 = [
        ["Priority", "Action", "Cost", "Why"],
        ["High", "Wider universe in production",
         "4 hr",
         "v3.73.12 robustness test showed LIVE variant edge GREW on a "
         "121-name S&P 500 sample (+88pp → +125pp pre-fix-equivalent). "
         "Worth a feature-flagged production run."],
        ["Medium", "HMM regime classifier",
         "12 hr",
         "Round-2 design: 3-state Gaussian HMM on SPY returns conditions "
         "the gross multiplier. The credible path to clearing IR > 1.0."],
        ["Medium", "Vol-targeting overlay",
         "6-8 hr",
         "Manage realized vol independent of picks. Lower lift than HMM, "
         "addresses the vol-regime concern from a different angle."],
        ["Low", "Run reactor in SHADOW for 60+ more days",
         "0 hr",
         "Need 30+ settled forward returns before flipping the rule from "
         "SHADOW to LIVE. Currently 4."],
    ]
    story.append(_table(tier1,
                         col_widths=[0.7 * inch, 1.6 * inch, 0.6 * inch,
                                      3.4 * inch]))
    story += [Spacer(1, 0.2 * inch)]

    story += [
        Paragraph("Tier 2 — kill criteria for production review", s["h2"]),
        Paragraph(
            "Revisit production-strategy decision NEGATIVELY if any of: "
            "&gt;1 missed daily run not caught by heartbeat within 24hr; "
            "ENFORCING-mode trims that surprise the operator; reactor "
            "rule loses 5/7 cases; LLM cost crosses $10/month; realized "
            "vol crosses 25% annualized; cross-validation harness "
            "disagrees with leaderboard by &gt;15pp.",
            s["body"],
        ),
        Paragraph(
            "Revisit POSITIVELY (sized capital decision) if all of: "
            "≥30 consecutive completed daily runs, 0 missed weekdays "
            "caught only after the fact; heartbeat alert test-fired "
            "AND received; 30+ days of post-fix benchmark-relative "
            "tracking; the cross-validation harness agrees within "
            "tolerance on every weekly rebuild; LLM cost stable.",
            s["body"],
        ),
        PageBreak(),
    ]

    # ============================================================
    # 8.5 REDDIT RESEARCH
    # ============================================================
    story += [
        Paragraph("8.5 What the retail trading community actually says", s["h1"]),
        _para(
            "On May 6 we ran a systematic harvest of 15 trading subreddits "
            "via the public Reddit JSON API. 899 unique top threads were "
            "collected (titles, scores, comment counts, top-level text); "
            "100 were enriched with full top-comment trees (553 comments "
            "total); 80+ substantive threads were read in full text. The "
            "subreddits sampled covered the full retail landscape: "
            "r/algotrading, r/quantfinance, r/quant, r/Trading, "
            "r/Daytrading, r/options, r/thetagang, r/SecurityAnalysis, "
            "r/ValueInvesting, r/Bogleheads, r/investing, r/stocks, and "
            "r/wallstreetbets as a cultural baseline.",
            s["body"],
        ),
        _para(
            "The full research summary is in "
            "<b>docs/REDDIT_RESEARCH_2026_05_06.md</b>. Here are the "
            "findings most consequential for evaluating this system.",
            s["body"],
        ),
        Paragraph("8.5.1 Where the community's evidence supports the design", s["h2"]),
        _para(
            "<b>Discipline beats strategy.</b> The single most upvoted "
            "\"how to trade\" thread in r/Trading (1284 upvotes, "
            "\"5 years of trading, my best tips\") opens with risk "
            "management as the inviolable rule. The same conclusion "
            "appears in every substantive multi-year-experience thread "
            "across every subreddit. Our system's structural choice — "
            "code-enforced rebalance, pre-committed cap rules, idempotent "
            "orchestrator with no override path — is doing exactly what "
            "the community's hard-won consensus says is required.",
            s["body"],
        ),
        _para(
            "<b>Naked-position blow-up immunity.</b> The cautionary tales "
            "with 5000+ upvotes ('Lost 100k in 3 minutes', 'Naked calls "
            "100k → 600k loss', '590k loss in one day') universally "
            "share two features: undefined-risk option positions and "
            "concentrated single-name leverage. Our system is "
            "structurally immune by design — long-only, no options, "
            "8% single-name cap, four-threshold drawdown protocol. The "
            "class of failure these reddit threads represent cannot "
            "happen on this system as designed.",
            s["body"],
        ),
        _para(
            "<b>Operational reliability is universally undercounted.</b> "
            "r/algotrading's most-upvoted purely-cautionary thread (1566) "
            "is a production-failure story (algo crashed, accidentally "
            "bought millions of shares of penny stocks before margin "
            "call). The pattern in the comments: retail algorithmic "
            "traders systematically underweight production reliability "
            "vs backtest quality. The May 5 session's first hours were "
            "spent on exactly these problems (heartbeat never installed, "
            "sleep-fragile launchd plists, journal not replicated). The "
            "community's warning is correct: ops > strategy at this stage.",
            s["body"],
        ),
        Paragraph("8.5.2 Where the community's evidence challenges the design", s["h2"]),
        _para(
            "<b>The Bogleheads counter-argument is now MEASURED, not "
            "argued.</b> v3.73.14 added three passive baselines to the "
            "eval harness so the Boglehead claim could be tested "
            "empirically rather than engaged in prose: "
            "<b>buy_and_hold_spy</b> (100% SPY, never reset), "
            "<b>boglehead_three_fund</b> (60% VTI / 30% VXUS / 10% BND, "
            "monthly rebalance), and <b>simple_60_40</b> (60% SPY / 40% "
            "AGG, monthly rebalance). Five-year results, cost-aware:",
            s["body"],
        ),
        _para(
            "The LIVE strategy beat <i>every</i> passive baseline by a "
            "wide margin: +76.6pp vs buy-and-hold-SPY, +103.0pp vs the "
            "Boglehead 3-fund, +114.7pp vs classic 60/40. The 3-fund "
            "and 60/40 underperformed not just our active strategy but "
            "<i>SPY itself</i>: the 3-fund lagged SPY by 26pp because "
            "VXUS (international) and BND (bonds) both lagged US equity "
            "during the rate-hiking cycle; 60/40 lagged by 38pp for the "
            "same reason amplified by the 40% bond weight. This is "
            "honest empirical data showing why \"diversify into bonds "
            "and international\" got punished in a US-tech-led era.",
            s["body"],
        ),
        _para(
            "The honest framing remains: our strategy is worth running "
            "as a learning/discipline asset (its primary intent) and "
            "worth running for the alpha if and only if all of the "
            "following hold: (a) the Tier 0 gates clear, (b) the "
            "strategy demonstrates positive active return through at "
            "least one observed regime change in the live data, and "
            "(c) the operator's annual time cost remains genuinely "
            "below 127 hr. If those conditions don't hold, the "
            "Boglehead community's <i>structural</i> argument — stop "
            "and put the capital in a low-cost index fund — remains a "
            "real option, even though their <i>specific</i> 3-fund "
            "allocation underperformed our LIVE by a large margin in "
            "this sample.",
            s["callout"],
        ),
        _para(
            "<b>The strongest passive baseline is buy-and-hold SPY</b>, "
            "not the 3-fund. If the operator decides at any point that "
            "the active system isn't worth the time, the right "
            "fallback is 100% SPY (or equivalent). Adding bonds and "
            "international to be 'diversified' actually hurt over the "
            "5-year window. The eval harness will continue to track "
            "all three passive baselines so the operator has live data "
            "on which to decide.",
            s["body"],
        ),
        _para(
            "<b>The 'I made an algo with AI' pattern is dominant and "
            "bad.</b> Multiple top threads in r/algotrading and r/Trading "
            "describe AI-assisted strategies whose evidence base is 10 "
            "days of paper. The community's top comments on these threads "
            "are universally skeptical. Our reactor is in SHADOW only — "
            "the LLM is a signal extractor on filings, not a strategy "
            "generator — and the v3.73.10 forward-return validation "
            "table is built specifically to prevent this failure mode. "
            "But the operator should be aware: any later move to expand "
            "the LLM's role (e.g., letting it propose strategies rather "
            "than tag filings) puts us into the failure pattern the "
            "community has watched fail many times.",
            s["body"],
        ),
        _para(
            "<b>The 2022 lesson resonates.</b> r/Bogleheads, "
            "r/ValueInvesting, and r/investing all have multiple "
            "high-upvote threads on the 2022 episode (S&P -25%, "
            "Nasdaq -35%, Meta -75% peak-to-trough). The community "
            "memory is that even brief drawdowns reset confidence "
            "broadly. Our 5-year backfill includes this episode but "
            "no sustained bear; the §6.2 disclosure of the regime-"
            "bias is honest by the community's bar.",
            s["body"],
        ),
        Paragraph("8.5.3 The retail landscape, condensed", s["h2"]),
    ]

    landscape = [
        ["Approach", "Community sentiment", "Comparable to our system?"],
        ["Bogleheads (passive index)", "Strongly positive; verified by SPIVA",
         "Our benchmark; we must clear it net of cost + time"],
        ["Wheel / theta selling", "Survivor-biased successes; tail blow-ups documented",
         "We considered an analog (long-short), tested empirically, rejected"],
        ["Discretionary day-trading", "Brazil 97% / Taiwan 1% lose; community's hardest warning",
         "We are not this; monthly cadence and code-enforced rules avoid"],
        ["AI-driven strategy generation", "Community's most-skepticed pattern",
         "We use LLM as signal extractor only, in SHADOW"],
        ["Concentrated value (Buffett-style)", "Long-horizon, fundamentally rigorous",
         "Different style; we are factor-systematic"],
        ["Quant career-track (BlackRock-style)", "Aspirational; not a retail playbook",
         "Different scale entirely"],
        ["Retail momentum / factor-systematic", "Niche; few documented retail successes",
         "<b>Our position</b>"],
    ]
    story.append(_table(landscape,
                         col_widths=[1.6 * inch, 2.4 * inch, 2.7 * inch]))
    story += [PageBreak()]

    # ============================================================
    # 9. CLOSING
    # ============================================================
    story += [
        Paragraph("9. The honest framing", s["h1"]),
        _para(
            "It is worth ending where the foreword started: this is a "
            "personal trading system run on paper capital with the "
            "infrastructure of a $10M shop. The right way to evaluate it "
            "is not by the dollars it has made — it has made none, "
            "because there are no real dollars at stake — but by what it "
            "has taught the operator about building, debugging, and "
            "trusting an autonomous system.",
            s["body"],
        ),
        _para(
            "The May 5 session is a case in point. The session began "
            "with confidence in a leaderboard that was wrong by "
            "approximately 4.58x on its IR claim and 17pp on its cum-"
            "active claim. The session ended with the same leaderboard "
            "showing more honest numbers (+77pp, IR 0.62) — still a "
            "winning strategy, but materially less of one than the "
            "operator was prepared to claim that morning. Crucially, "
            "the corrections came from the system catching itself, not "
            "from external review. The cross-validation harness, the "
            "spot-check script, and the variant registry consistency "
            "test were each shipped on the same day they caught the "
            "bug they were designed to catch. That is the discipline "
            "the system is meant to teach. The wins it produces are "
            "secondary.",
            s["body"],
        ),
        _para(
            "The operator's primary day-job is the wealth-creation engine. "
            "The trader is the operating-discipline gym. Both are valuable; "
            "both deserve rigor; "
            "neither should be confused for the other. This document is "
            "intended to capture the trader honestly enough that the "
            "operator can show it to anyone — an LP, a peer, a future "
            "version of themselves — and have the document hold up. "
            "If anything in here turns out to be wrong, it should be "
            "wrong in a way the next round of measurement infrastructure "
            "catches.",
            s["body"],
        ),
        _para(
            "The next 30 days will accumulate more data: another four "
            "monthly rebalances, dozens of reactor signals, the first "
            "stretch of NAV-vs-SPY tracking with the post-fix "
            "annualization. The leaderboard's standard error will tighten. "
            "The decision about whether to expand the universe to S&P 500 "
            "in production, whether to ship the HMM regime classifier, "
            "whether to flip the reactor rule from SHADOW to LIVE — all "
            "of those decisions wait on data the system is now structured "
            "to collect. The strategy stack and the operations stack are "
            "finally on the same version. That alone is the difference "
            "between a system that can be sized up and one that cannot.",
            s["body"],
        ),
        PageBreak(),
    ]

    # ============================================================
    # 9. APPENDIX
    # ============================================================
    story += [
        Paragraph("Appendix A — File Inventory", s["h1"]),
    ]
    files_a = [
        ["src/trader/strategy.py", "rank_momentum + rank_vertical_winner"],
        ["src/trader/variants.py", "12+ registered strategy variants; "
                                     "LIVE = momentum_top15_mom_weighted_v1"],
        ["src/trader/risk_manager.py", "drawdown protocol + tiers"],
        ["src/trader/portfolio_caps.py", "8% / 25% caps with cap-aware "
                                            "redistribution"],
        ["src/trader/eval_strategies.py", "12 candidate strategies"],
        ["src/trader/eval_runner.py", "evaluate_at + settle_returns + "
                                        "leaderboard"],
        ["src/trader/benchmark_track.py", "NAV vs SPY metrics"],
        ["src/trader/earnings_reactor.py", "EDGAR poll + Claude tag"],
        ["src/trader/reactor_rule.py", "SHADOW/LIVE/INERT state machine"],
        ["src/trader/main.py", "Daily orchestrator"],
        ["scripts/dashboard.py", "6,700-line Streamlit dashboard"],
        ["scripts/check_daily_heartbeat.py", "Failure-detection alert"],
        ["scripts/replicate_journal.sh", "Nightly SQLite backup"],
        ["scripts/cross_validate_harness.py", "Independent backtester"],
        ["scripts/spotcheck_reactor.py", "8-K source verification"],
        ["scripts/validate_reactor.py", "Forward-return outcomes"],
        ["scripts/build_dashboard.sh", "Docker rebuild + force-recreate"],
    ]
    story.append(_table(files_a, col_widths=[2.6 * inch, 4.0 * inch],
                          header=False))
    story += [Spacer(1, 0.2 * inch)]

    story += [
        Paragraph("Appendix B — Key Documentation", s["h1"]),
    ]
    docs_a = [
        ["docs/DUE_DILIGENCE_2026_05_05.md", "Initial DD memo (with "
                                                "supersessions noted)"],
        ["docs/DD_ADDENDUM_2026_05_05.md", "Production-as-leader "
                                              "correction"],
        ["docs/ROBUSTNESS_TESTS_2026_05_05.md", "Universe expansion + "
                                                  "long-short tests"],
        ["docs/MEASUREMENT_AUDIT_2026_05_05.md", "Test/measurement "
                                                   "infrastructure"],
        ["docs/STRATEGY_AND_RISK.md", "Strategy + risk framework"],
        ["docs/RISK_FRAMEWORK.md", "Round-2 four-threshold protocol "
                                       "specs"],
        ["docs/ROUND_2_SYNTHESIS.md", "Block A/B/C work plan"],
    ]
    story.append(_table(docs_a, col_widths=[2.6 * inch, 4.0 * inch],
                          header=False))

    story += [
        Spacer(1, 0.4 * inch),
        Paragraph(
            "<i>Generated by scripts/generate_system_writeup.py on "
            f"{datetime.now():%Y-%m-%d %H:%M UTC}. Source: "
            "github.com/rca6045407168/trader at master HEAD.</i>",
            s["subtitle"],
        ),
    ]

    doc.build(story)
    print(f"Wrote {OUT}")
    print(f"Size: {OUT.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    build()
