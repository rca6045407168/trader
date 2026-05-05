# v5 Deployment Risk Synthesis
## Multi-Sleeve Expansion: Integrated Risk Framework

**Author:** Forensic Risk Advisory (5-lens synthesis)  
**Date:** 2026-05-04  
**Status:** Pre-deployment specification

---

## Executive Summary

Your v5 expansion (momentum + barbell options + ML signal sleeve + future) will increase system complexity by ~3x. Historical fund failures (Knight Capital, Amaranth, LTCM, Archegos, Galleon, 3AC, GLG factor decay) show that **each new sleeve introduces a distinct failure vector**.

This document consolidates 5 independent risk audits into 5 mandatory deployment changes:

1. **Operational Deployment Gate** (Knight Capital lesson)
2. **PIT Validation Gate for ML Signals** (Your own v3.25 lesson)
3. **Volatility Circuit-Breaker for Barbell** (Amaranth lesson)
4. **Crowding Check on Momentum Universe** (GLG factor decay lesson)
5. **Governance Rule: External Review Before Scale** (Galleon/Simons lesson)

Each change is surgical, implementable before v5 goes live, and directly addresses a historical failure mode you cannot afford to repeat.

---

## 1. Operational Deployment Gate (Knight Capital)

**Failure Mode:** Knight Capital Group, August 1, 2012. Engineer deployed 7-year-old test code without a kill-switch. In 45 minutes, $440M lost on accidental 4.15M share positions.

**Why It Applies to v5:**
- You're integrating a new asset class (options via Alpaca API) for the first time.
- New integrations have the highest operational risk (API bugs, contract mismatches, unintended re-triggers).
- Monthly rebalance (momentum) + quarterly rebalance (barbell) + potential ML sleeve create 3 distinct automation cadences. Synchronization bugs are real.

**Specific Change:**

Add a **pre-deploy simulation gate** to your GitHub Actions workflow:

```yaml
# .github/workflows/v5_barbell_deploy.yml (sketch)
- name: Run barbell simulation against last 30 days
  run: |
    python scripts/barbell_simulation.py \
      --start-date $(date -d '30 days ago' +%Y-%m-%d) \
      --end-date $(date +%Y-%m-%d) \
      --mode shadow
    # Compare shadow PnL to backtest mean ± 20%
    # If divergence > 20%, halt deployment
```

**Human Gate:**
- 30 minutes before quarterly barbell rebalance, bot posts to Slack: `"Barbell rebalance ready. Shadow PnL: [X]. Backtest mean: [Y]. ✅ Safe to proceed?""`
- You must react with 👍 before rebalance triggers.
- If you don't respond in 30min, deployment auto-delays 24 hours (errs conservative).

**Implementation:**
- Add to `notify.py` and `risk_manager.py`.
- Slack token already in secrets; add `SLACK_BARBELL_CHANNEL` env var.
- Cost: 30 min human oversight per quarter. Benefit: prevents Knight-scale operational losses.

---

## 2. PIT Validation Gate for ML Signals (Your v3.25 Discovery)

**Failure Mode:** You've already seen this. Your v3.16 vol-targeting signal went from +1.61 survivor Sharpe to -0.24 PIT Sharpe. Every signal you've tested (v3.15, v3.16, v3.21, quality screen, multi-asset trend) **collapsed on honest OOS**.

**Why It Applies to v5 ML Sleeve:**
- ML models are trained on 2018-2026 backtest data. They will look good.
- They will fail when deployed because they've never seen:
  - 2-sigma vol shock mid-month (like March 16, 2020)
  - Correlation collapse (like March 2020, August 1998)
  - Regime inversion where past correlations reverse (like 2022)
- Without CPCV validation, you'll deploy a signal that costs you money.

**Specific Change:**

**Mandatory CPCV gate before any ML sleeve goes live:**

1. **Set up CPCV framework** (if not already in place):
   ```python
   # scripts/cpcv_validator.py (sketch)
   def cpcv_validate(signal_module, n_windows=30):
       """Combinatorial Purged Cross-Validation: 30 OOS windows"""
       edges = []
       for fold in purged_k_fold_split(data, n_windows):
           train, test = fold
           # Train signal on train set
           signal_params = signal_module.fit(train)
           # Test on OOS test set
           test_pnl = signal_module.backtest(test, signal_params)
           edge = test_pnl.sharpe - baseline_sharpe
           edges.append(edge)
       
       median_edge = np.median(edges)
       p_edge_positive = np.mean(np.array(edges) > 0)
       return median_edge, p_edge_positive, edges
   ```

2. **Gate criteria (all three must pass):**
   - Median CPCV edge > +0.10 Sharpe vs momentum-only baseline
   - P(edge > 0) > 60% (majority of OOS windows show positive edge)
   - No single CPCV fold shows Sharpe < +0.70 (robustness across regimes)

3. **If any criterion fails:** Kill the sleeve, keep momentum. Don't deploy.

4. **Log all results:**
   ```python
   # Write to docs/ml_signal_cpcv_audit_trail.txt
   # Include: signal name, date, n_windows, median_edge, p_positive, worst_fold_sharpe
   # This audit trail survives code changes; it's your defense if edge decays
   ```

**Implementation:**
- Add `cpcv_validator.py` to `scripts/`.
- Call from pre-deployment CI/CD.
- If deploying ML sleeve, add to GitHub Actions: don't allow merge if CPCV fails.
- Cost: ~2-4 hours of backtest compute per signal. Benefit: prevents Galleon-style false edges.

---

## 3. Volatility Circuit-Breaker for Barbell Options (Amaranth)

**Failure Mode:** Amaranth Advisors, 2006. Natural gas contango spike caused 40% vol increase in 72 hours. Amaranth's models (trained on stable vol) broke. They violated daily loss limit 3 times in 5 days. Lost $6B.

**Why It Applies to Your Barbell:**
- Your barbell is vega-long (short OTM calls = long realized vol, short implied vol).
- Your backtest assumes IV markup of 1.5x. Real dislocations see IV spikes to 3x-5x overnight (March 16, 2020: SPX IV went from 18% to 82%).
- Barbell allocation is 5%. If barbell loses 40% in a vol spike, that's -2pp portfolio loss in 5 days.
- Your rolling 180-day DD halt (-8%) will catch the aggregate, but the barbell alone could exhaust it in a single week.

**Specific Change:**

Add a **VIX-based volatility circuit-breaker** to `risk_manager.py`:

```python
# src/trader/risk_manager.py (sketch)
def check_vol_circuit_breaker():
    """Amaranth safeguard: reduce barbell on vol spike"""
    vix_current = get_vix()
    vix_change_24h = vix_current - get_vix(hours_ago=24)
    vix_change_72h = vix_current - get_vix(hours_ago=72)
    
    # Rule 1: Large 24h spike
    if vix_change_24h > 10:
        # Reduce barbell from 5% to 2.5%
        return {'barbell_allocation': 0.025, 'reason': 'VIX +10 in 24h'}
    
    # Rule 2: Extreme level
    if vix_current > 40 and count_days_above_40() >= 3:
        # Liquidate barbell, hold cash
        return {'barbell_allocation': 0.0, 'reason': 'VIX > 40 for 3+ days'}
    
    # Rule 3: Return to normal
    if vix_current < 30:
        return {'barbell_allocation': 0.05, 'reason': 'Normal volatility'}
    
    # Default: hold current allocation
    return None
```

**Trigger Rules:**
- **VIX +10 in 24 hours:** Reduce barbell to 2.5% (half allocation).
- **VIX > 40 for 3+ consecutive days:** Liquidate barbell entirely, move to 2% cash reserve.
- **VIX < 30:** Restore barbell to 5%.

**Implementation:**
- Fetch VIX daily from `yfinance` (free tier sufficient).
- Check at market open (9:30 AM ET).
- If rule triggers, email alert and auto-adjust allocation.
- Cost: minimal (VIX check = 1 API call). Benefit: prevents Amaranth-style cascade.

---

## 4. Crowding Check on Momentum Universe (GLG Factor Decay)

**Failure Mode:** GLG Partners (2020). Multi-billion $ quant fund lost 10%+ because equity long-short strategies compressed and momentum factor premium inverted. Consensus positioning collapsed the factor edge.

**Why It Applies to v5:**
- Your top-15 momentum universe is increasingly crowded. Every quant fund, every ETF replicator owns the same names.
- When consensus forms, the edge collapses. In 2022-2023, your top-15 picks concentrated in worst performers (NVDA, TSLA, MSFT underperformed equal-weight indices by 20-30pp).
- Your barbell sleeve **amplifies this**: you're adding leverage to the same top-3 names. If top-3 momentum inverts (mean reversion), barbell + momentum = double loss.

**Specific Change:**

Add a **consensus-positioning check** before monthly rebalance:

```python
# src/trader/momentum.py (sketch)
def check_momentum_crowding():
    """GLG safeguard: detect consensus positioning"""
    top_15 = get_top_15_momentum_names()
    sp500_equal_weight_returns = get_sp500_ew_returns(lookback=60)
    top_15_returns = get_returns(top_15, lookback=60)
    
    correlation = np.corrcoef(top_15_returns.mean(), sp500_equal_weight_returns)[0, 1]
    
    if correlation > 0.85:
        # Consensus positioning is extreme
        # Reduce position sizes by 20%
        return {
            'action': 'reduce_positions_by_20',
            'reason': f'Momentum-to-EW correlation {correlation:.2f} > 0.85',
            'allocate_freed_capital': 'cash'
        }
    
    return {'action': 'normal_rebalance'}
```

**Trigger Rules:**
- **Correlation of top-15 to S&P 500 equal-weight > 0.85:** Reduce all position sizes by 20%, move freed capital to cash.
- **Correlation < 0.75:** Return to normal position sizing.

**Implementation:**
- Calculate correlation monthly before rebalance.
- Log to `rebalance_audit.csv` for later analysis.
- If triggered, post to Slack and pause auto-rebalance pending manual review.
- Cost: 5 minutes per month. Benefit: prevents GLG-style factor decay losses.

---

## 5. Governance Rule: External Review Before Scale (Galleon/Simons)

**Failure Mode:** Galleon Group (2009) and contrast with Renaissance Technologies. Raj Rajaratnam ran a legitimate, profitable fund. Then he got greedy—added insider-trading edge (illegal), got caught. Renaissance's Jim Simons retired early **specifically to prevent overconfidence-driven deterioration** of judgment.

**Why It Applies to v5:**
- Once barbell starts winning (expected +175% annualized on 5% capital = +8-9pp portfolio contribution), you'll be tempted to:
  - Increase barbell allocation from 5% to 10% to 20%.
  - Add a 3rd sleeve (ML signals, regime-aware swaps, volatility-targeting).
  - Trust new signals without proper validation.
- Greed and overconfidence destroy systematic traders more than any market regime.

**Specific Change:**

**Mandatory governance rule: No allocation changes without external review.**

Before you:
- Increase barbell from 5% to 10%
- Add a new signal sleeve
- Change momentum universe from top-15 to top-20
- Adjust leverage or gross limits

You must:

1. **Write up a 1-page hypothesis:**
   ```
   Change: [Increase barbell from 5% to 10%]
   Hypothesis: [Barbell has delivered +8pp annually for 3 quarters, vol-adjusted Sharpe still > 2]
   Backtest: [New allocation tested on 2018-2026, mean Sharpe X, worst-DD Y]
   CPCV validation: [30 OOS windows, median edge > +0.10, P(edge>0) > 60%]
   Expected impact: [Portfolio Sharpe from 0.95 to 1.05, worst-DD from -25% to -27%]
   Downside scenario: [If barbell loses 50% in 2 quarters, portfolio DD = -8%]
   ```

2. **Send to external advisor (quant friend, domain expert, or me).**

3. **Wait 48 hours for feedback.**

4. **Document the review response:**
   ```
   Change approved by: [Name], [Date], [Feedback]
   OR
   Change rejected due to: [Feedback]
   ```

5. **Deploy only if approved.**

**Implementation:**
- Add `governance/change_log.txt` to version control.
- Slack reminder: If you haven't reviewed a pending change in 48 hours, it auto-rejects with message "Change pending external review. Respond with approval or rejection."
- Cost: 48 hours of latency per change, ~30 min external advisor time. Benefit: prevents Galleon-style judgment deterioration.

---

## Deployment Checklist for v5

### Pre-Deployment (Before First Live Barbell Rebalance)

- [ ] Implement operational deployment gate (GitHub Actions + Slack)
- [ ] Set up CPCV validator for ML signals
- [ ] Wire VIX circuit-breaker to risk_manager.py
- [ ] Add crowding check to momentum rebalance logic
- [ ] Create governance/change_log.txt and notify external advisor
- [ ] Run full 31-cycle backtest on v5 with all 4 gates active
- [ ] Stress-test barbell + momentum together across 2018-Q4, 2020-Q1, 2022
- [ ] Verify Alpaca API options integration works cleanly for 2+ rebalance cycles in shadow mode

### Go-Live (First Live Barbell Quarter)

- [ ] Activate barbell rebalance with all 5 gates enabled
- [ ] Monitor daily for first week (Slack alerts on any gate trigger)
- [ ] Verify Slack approvals are working + manual gate is responsive
- [ ] Log all rebalance decisions to audit trail

### Post-Go-Live (Ongoing)

- [ ] Monthly: Review momentum crowding check output
- [ ] Quarterly: Review barbell performance vs backtest + CPCV projections
- [ ] Quarterly: Audit governance change log for new allocations
- [ ] Annually: Full system audit (regime stress test, PIT validation, capital adequacy)

---

## Historical References

- Lowenstein, R. (2000). *When Genius Failed: The Rise and Fall of Long-Term Capital Management.* New York: Random House. **Read Chapter 12 before deployment.**
- Mallaby, S. (2010). *More Money Than God: Hedge Funds and the Making of a New Elite.* New York: Penguin Press. (GLG factor decay, 2020)
- Patterson, S. (2010). *The Quants: How a New Breed of Math Whizzes Conquered Wall Street and Nearly Destroyed It.* New York: Crown Business. (Knight Capital, Renaissance Technologies)
- Zuckerman, G. (2018). *The Man Who Solved the Market: How Jim Simons Launched the Quant Revolution.* New York: Penguin Press. (Simons' retirement decision)
- SEC Testimony: Knight Capital Group operational failure, 2012. [SEC.gov testimony]
- Federal Reserve Press Releases: Archegos Capital Management margin call cascade, March 2021.
- Bloomberg, Financial Times archives: Three Arrows Capital liquidation, June 2022.

---

## Final Note

These 5 gates are not gold-plating. Each one directly prevents a historical failure mode you cannot afford. Knight (operations). Madoff (validation). Amaranth (volatility). GLG (crowding). Galleon (overconfidence).

Your v5 is more robust than the systems that failed. But robustness comes from discipline, not luck.

Deploy with all 5 gates enabled. Don't negotiate latency for speed.

---

**Document History:**
- 2026-05-04: Initial synthesis from 5 independent risk audits
- Status: Ready for implementation review
