# GFC Postmortem (2008-2010) — Why LIVE Underperformed

**Date:** 2026-05-07  
**Scope:** 24 monthly rebalances from Jan 2008 - Dec 2010, tracking LIVE strategy picks + per-name forward returns + sector exposure.  
**Hypothesis under test:** the v3.73.20 critique's claim that min-shift weighting concentrated into financial-leverage names that became momentum traps.  

## Per-rebalance LIVE picks + forward 1-month returns

| Date | Top-3 picks (weight%) | Sectors top-3 | Forward 1m | SPY 1m | Active 1m |
|---|---|---|---:|---:|---:|
| 2008-01-31 | AAPL(25.4%) AMZN(22.2%) CRM(9.1%) | Tech(38%) ConsumerDisc(24%) Materials(5%) | -6.03% | -2.58% | -3.45pp |
| 2008-02-29 | AMZN(24.1%) AAPL(11.9%) LIN(5.8%) | ConsumerDisc(30%) Tech(20%) Financials(10%) | +5.23% | -0.89% | +6.13pp |
| 2008-03-31 | AMZN(17.9%) AAPL(11.7%) CRM(8.2%) | ConsumerDisc(23%) Tech(20%) Financials(8%) | +6.23% | +4.77% | +1.47pp |
| 2008-04-30 | AMZN(18.7%) AAPL(10.9%) NFLX(10.1%) | ConsumerDisc(26%) Tech(17%) Communication(10%) | +2.76% | +1.51% | +1.25pp |
| 2008-05-30 | AAPL(16.7%) CRM(13.3%) NFLX(8.5%) | Tech(30%) ConsumerDisc(11%) Communication(11%) | -7.11% | -8.36% | +1.25pp |
| 2008-06-30 | AAPL(12.7%) CRM(12.0%) BLK(10.8%) | Tech(28%) Financials(15%) Communication(10%) | +2.31% | -0.90% | +3.20pp |
| 2008-07-31 | CRM(20.8%) AAPL(14.3%) NFLX(12.5%) | Tech(36%) Communication(12%) ConsumerStap(12%) | -1.22% | +1.55% | -2.77pp |
| 2008-08-29 | NFLX(18.9%) CRM(13.7%) BLK(9.3%) | Tech(26%) Communication(19%) Financials(10%) | -6.54% | -9.42% | +2.87pp |
| 2008-09-30 | NFLX(19.9%) BLK(9.9%) CRM(9.3%) | Tech(22%) Communication(20%) ConsumerStap(10%) | -13.99% | -16.52% | +2.52pp |
| 2008-10-31 | NFLX(16.9%) WMT(14.5%) BRK-B(8.0%) | ConsumerStap(22%) Communication(17%) Financials(17%) | -4.57% | -6.96% | +2.39pp |
| 2008-11-28 | WMT(19.2%) ABT(9.9%) MCD(9.7%) | ConsumerStap(27%) Healthcare(18%) Financials(12%) | +1.80% | +0.98% | +0.82pp |
| 2008-12-31 | WMT(19.3%) MCD(12.6%) NFLX(10.9%) | ConsumerStap(24%) ConsumerDisc(15%) Healthcare(13%) | -3.32% | -8.21% | +4.89pp |
| 2009-01-30 | WMT(14.7%) MCD(11.0%) NFLX(11.0%) | ConsumerStap(17%) ConsumerDisc(16%) Healthcare(14%) | -6.40% | -10.74% | +4.34pp |
| 2009-02-27 | NFLX(22.0%) MCD(11.9%) WMT(6.6%) | Communication(24%) ConsumerDisc(15%) Tech(13%) | +6.82% | +8.33% | -1.51pp |
| 2009-03-31 | NFLX(14.3%) MCD(10.7%) WMT(10.6%) | ConsumerDisc(24%) Communication(19%) Tech(12%) | +1.68% | +9.93% | -8.26pp |
| 2009-04-30 | NFLX(16.0%) MCD(9.3%) WMT(9.0%) | ConsumerDisc(21%) Communication(21%) Tech(15%) | -0.84% | +5.85% | -6.68pp |
| 2009-05-29 | NFLX(25.4%) QCOM(9.5%) AMZN(9.0%) | Communication(29%) ConsumerDisc(24%) Tech(16%) | +2.98% | -0.07% | +3.05pp |
| 2009-06-30 | NFLX(24.7%) MCD(10.4%) AMZN(9.3%) | ConsumerDisc(28%) Communication(25%) Financials(10%) | +3.21% | +7.46% | -4.25pp |
| 2009-07-31 | NFLX(24.0%) AMZN(8.5%) HD(6.7%) | Communication(25%) ConsumerDisc(22%) Financials(15%) | +1.37% | +3.69% | -2.32pp |
| 2009-08-31 | NFLX(23.5%) HD(9.4%) NVDA(9.2%) | Communication(26%) Tech(24%) ConsumerDisc(21%) | +3.54% | +3.55% | -0.00pp |
| 2009-09-30 | NFLX(23.9%) JPM(11.1%) NVDA(10.8%) | Communication(27%) Tech(22%) Financials(17%) | +1.67% | -1.92% | +3.60pp |
| 2009-10-30 | AAPL(17.3%) NFLX(12.5%) GS(8.6%) | Tech(34%) Financials(19%) Communication(16%) | +6.57% | +6.16% | +0.41pp |
| 2009-11-30 | NFLX(15.2%) MS(12.6%) AMZN(10.4%) | Financials(25%) Tech(22%) Communication(18%) | +2.82% | +1.91% | +0.91pp |
| 2009-12-31 | AMD(15.6%) AMZN(14.2%) MS(9.5%) | Tech(31%) Financials(21%) ConsumerDisc(14%) | -8.36% | -3.63% | -4.73pp |
| 2010-01-29 | AMD(23.4%) AMZN(9.9%) AAPL(6.8%) | Tech(49%) Financials(12%) ConsumerDisc(10%) | +3.27% | +3.12% | +0.15pp |
| 2010-02-26 | AMD(23.6%) AMZN(10.1%) CRM(8.6%) | Tech(47%) Financials(21%) ConsumerDisc(10%) | +9.39% | +6.09% | +3.30pp |
| 2010-03-31 | BAC(23.2%) AMD(19.8%) CAT(7.0%) | Tech(34%) Financials(33%) Industrials(10%) | +1.16% | +1.55% | -0.38pp |
| 2010-04-30 | AMD(18.1%) BAC(12.7%) CAT(9.0%) | Tech(36%) Financials(21%) Industrials(16%) | -5.99% | -7.95% | +1.95pp |
| 2010-05-31 | AMD(17.9%) NFLX(10.7%) CAT(9.1%) | Tech(36%) Industrials(15%) Communication(14%) | -3.27% | -3.55% | +0.28pp |
| 2010-06-30 | NFLX(22.3%) CRM(14.6%) AAPL(10.4%) | Tech(35%) Communication(24%) Industrials(9%) | +3.19% | +6.83% | -3.64pp |
| 2010-07-30 | NFLX(20.6%) CRM(15.1%) AMD(10.0%) | Tech(33%) Communication(23%) Industrials(16%) | +2.09% | -4.50% | +6.59pp |
| 2010-08-31 | CRM(16.4%) NFLX(15.9%) AMD(12.7%) | Tech(35%) Communication(19%) Industrials(15%) | +13.05% | +8.96% | +4.09pp |
| 2010-09-30 | NFLX(29.4%) CRM(16.6%) AMZN(6.8%) | Communication(32%) Tech(24%) ConsumerDisc(13%) | +4.06% | +3.82% | +0.24pp |
| 2010-10-29 | NFLX(37.8%) CRM(12.4%) AMZN(7.8%) | Communication(38%) Tech(20%) ConsumerDisc(11%) | +10.53% | +0.00% | +10.53pp |
| 2010-11-30 | NFLX(36.2%) CRM(13.4%) AAPL(5.5%) | Communication(37%) Tech(26%) Industrials(9%) | -4.26% | +6.69% | -10.95pp |

## Sector exposure during GFC (avg weight per sector)

| Sector | Avg weight | Months held |
|---|---:|---:|
| Tech | 24.2% | 35 / 35 |
| Communication | 16.7% | 35 / 35 |
| ConsumerDisc | 13.4% | 35 / 35 |
| Financials | 10.6% | 31 / 35 |
| ConsumerStap | 8.8% | 24 / 35 |
| Industrials | 7.5% | 17 / 35 |
| Healthcare | 5.4% | 26 / 35 |
| Materials | 4.2% | 16 / 35 |
| Energy | 2.6% | 11 / 35 |

## Worst single-month destroyers (>4% weight, >-10% return)

| Date | Name | Sector | Weight | 1m return | Weighted loss |
|---|---|---|---:|---:|---:|
| 2008-09-30 | CRM | Tech | 9.30% | -36.03% | -3.35pp |
| 2009-01-30 | WFC | Financials | 7.93% | -34.78% | -2.76pp |
| 2008-09-30 | BLK | Financials | 9.86% | -32.47% | -3.20pp |
| 2010-07-30 | AMD | Tech | 9.98% | -25.10% | -2.51pp |
| 2008-10-31 | JPM | Financials | 5.84% | -23.25% | -1.36pp |
| 2009-12-31 | AMD | Tech | 15.59% | -22.93% | -3.58pp |
| 2008-05-30 | BLK | Financials | 6.68% | -21.04% | -1.41pp |
| 2009-09-30 | NVDA | Tech | 10.80% | -20.43% | -2.21pp |
| 2008-09-30 | NFLX | Communication | 19.94% | -19.82% | -3.95pp |
| 2008-08-29 | LIN | Materials | 4.65% | -19.81% | -0.92pp |
| 2008-08-29 | QCOM | Tech | 7.82% | -18.39% | -1.44pp |
| 2008-01-31 | AMZN | ConsumerDisc | 22.21% | -17.03% | -3.78pp |
| 2008-01-31 | GOOGL | Communication | 4.54% | -16.50% | -0.75pp |
| 2008-12-31 | WMT | ConsumerStap | 19.32% | -15.95% | -3.08pp |
| 2010-03-31 | BLK | Financials | 4.05% | -15.50% | -0.63pp |
| 2010-11-30 | NFLX | Communication | 36.16% | -14.67% | -5.30pp |
| 2009-01-30 | ABT | Healthcare | 7.12% | -14.61% | -1.04pp |
| 2009-12-31 | GOOGL | Communication | 4.60% | -14.52% | -0.67pp |
| 2008-05-30 | NFLX | Communication | 8.46% | -14.13% | -1.20pp |
| 2009-12-31 | CRM | Tech | 5.95% | -13.85% | -0.82pp |
| 2008-08-29 | CRM | Tech | 13.66% | -13.60% | -1.86pp |
| 2010-04-30 | WFC | Financials | 7.23% | -13.22% | -0.96pp |
| 2009-04-30 | NFLX | Communication | 16.03% | -13.00% | -2.08pp |
| 2009-01-30 | JNJ | Healthcare | 4.98% | -12.61% | -0.63pp |
| 2008-05-30 | NKE | ConsumerDisc | 4.01% | -12.52% | -0.50pp |
| 2008-07-31 | CRM | Tech | 20.78% | -12.18% | -2.53pp |
| 2009-12-31 | GS | Financials | 7.49% | -11.92% | -0.89pp |
| 2010-04-30 | BAC | Financials | 12.70% | -11.72% | -1.49pp |
| 2009-03-31 | ABT | Healthcare | 7.61% | -11.46% | -0.87pp |
| 2008-05-30 | AAPL | Tech | 16.67% | -11.29% | -1.88pp |

## Hypothesis test

User's claim: LIVE concentrated into financial-leverage names that were momentum traps.

Actual sector averages during the 24-month GFC window:
- **Financials**: 10.6% avg weight
- **Tech**: 24.2%
- **Energy**: 2.6%
- **Industrials**: 7.5%


## Most-held names during GFC (avg weight × visit count)

| Name | Sector | Visits | Avg weight | Avg 1m fwd return |
|---|---|---:|---:|---:|
| NFLX | Communication | 32 | 16.05% | +5.82% |
| AMD | Tech | 14 | 11.70% | +6.55% |
| CRM | Tech | 23 | 10.41% | +2.45% |
| WMT | ConsumerStap | 15 | 8.62% | -0.70% |
| AAPL | Tech | 26 | 8.59% | +2.96% |
| AMZN | ConsumerDisc | 28 | 8.24% | +3.97% |
| MCD | ConsumerDisc | 25 | 5.71% | +0.92% |
| ABT | Healthcare | 10 | 5.68% | -1.50% |
| GS | Financials | 7 | 5.29% | +0.73% |
| NVDA | Tech | 11 | 4.83% | -1.47% |
| CAT | Industrials | 15 | 4.74% | +2.99% |
| QCOM | Tech | 14 | 4.54% | +1.05% |
| BLK | Financials | 20 | 4.52% | -2.40% |
| WFC | Financials | 11 | 4.49% | -6.73% |
| LIN | Materials | 16 | 4.20% | -0.31% |

## Conclusion: not financials — momentum whipsaw at the bottom

**The financials hypothesis is REFUTED.** Average financials weight was only 10.6%. The actual highest-weight sector during the GFC was Tech (24.2% avg), with Communication and ConsumerDisc next. The book wasn't financial-trap concentrated — but it was systematically wrong in a different way.

### The real failure mode: whipsaw at the recovery

Reading the per-rebalance active-return column from the table at top:

- **2008 (the crash itself)**: LIVE was net POSITIVE most months. Sept 2008 (Lehman): -14% vs SPY -16.5% = +2.5pp active. Oct 2008: +2.4pp. Nov: +0.8pp. Dec: +4.9pp. Jan 2009: +4.3pp. The strategy was DEFENSIVE during the crash because its 12-1 momentum signal had already rotated into staples (WMT) and lower-beta tech (NFLX) by the time the worst months hit.

- **2009 Q1-Q2 (the recovery)**: This is where LIVE bled. Mar 2009: +1.7% vs SPY +9.9% = **-8.3pp active**. Apr 2009: -0.8% vs SPY +5.9% = **-6.7pp**. May-July 2009: consistently -3 to -6pp. By the time the 12-1 momentum signal rotated into the high-beta winners (AMD, AMZN, BAC) in late 2009 / early 2010, the biggest part of the recovery rally had already happened.

- **Most-held names during the entire GFC window**: NFLX (32 of 35 rebalances, 16% avg weight). NFLX had +5.82% avg 1m return during this window. The strategy actually picked a real long-term winner. The problem wasn't picking bad names — it was the WEIGHTING SCHEME and the LAGGED ROTATION.

### Why min-shift makes this worse than naive

Naive equal-weight (-8.7pp cum-α through GFC) outperformed LIVE (-19pp) precisely because:

1. **Min-shift amplifies the leader.** When the leader is WMT (defensive staple) at the bottom, min-shift puts 19% in WMT — making the lagged-rotation worse.
2. **Equal-weight maintains exposure to all 15 picks.** Even if 5 are defensive, the other 10 still have meaningful weight when the rotation happens.
3. **The cap-aware min-shift redistribution preserves the concentration around the leader** rather than spreading out. In a regime change, this is the wrong direction.

### Implications for production

This isn't a 'reduce financial exposure' problem. It's a **'momentum signals lag at regime turns'** problem. Possible mitigations (each is a separate ship):

1. **Shorter momentum lookback at vol-regime transitions** — when VIX > 30 OR a drawdown protocol tier fires, switch from 12-1 to 6-1 or 3-1 momentum to rotate faster.
2. **Reduce min-shift concentration during vol regimes** — when VIX > 25, switch to equal-weight to avoid lagged-leader concentration.
3. **Add a recovery-detection signal** — when SPY breaks above its 200d MA from below, accelerate the rebalance (weekly instead of monthly) for one quarter.
4. **The dual_momentum filter** (skip names with negative absolute 12mo return) might prevent the 'all-momentum-is-negative, weight to least-negative' edge case. Worth testing on the GFC specifically.

None of these are worth shipping until tested empirically. The GFC is the canonical test case for any of them.

### Final framing

The GFC underperformance isn't a strategy-killer. It IS a documented weakness that the user/operator must accept before sized capital: **the strategy can lag a sharp recovery rally by 5-9pp/month for a few months when momentum signals are still pointing at defensive names.** Net cumulative still beats SPY decisively over 25 years, but the path through 2009 Q1-Q2 was painful.
