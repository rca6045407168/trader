# CONTRIBUTING.md

A short rulebook for anyone (human or agent) working on this repo.

## The principal rule: **go all the way**

No half-finished iterations. If you start something, finish it.

Concretely:

1. **Backtest scripts must be RUN, not just written.** Commit results, not just code.
2. **Bug fixes must be tested.** Add the regression test that proves the bug is fixed. Run the full suite. Commit.
3. **Strategy enhancements must be walk-forwarded.** In-sample is not deployment. OOS Sharpe + decay measurement before any "DEPLOYED" label.
4. **Scheduled tasks must be verified to fire.** Either trigger via fireAt + verify, or the task is not done.
5. **Citations are not backtests.** Don't drop an academic claim into the codebase without an empirical test on real data. If it's untested, mark `confidence="untested"` not `confidence="high"`.
6. **"Should work" is a smell.** Either it works (demonstrated) or it doesn't (named blocker).

If you have to choose between starting a new iteration and finishing the current one, finish the current one. A complete loop teaches you more than three half-finished ones.

## Definition of done for any change

- [ ] Code committed
- [ ] Tests pass (`pytest tests/`)
- [ ] Regression check passes (`python scripts/regression_check.py`)
- [ ] If it's a strategy change: walk-forward result documented
- [ ] If it's a fix: regression test added
- [ ] Pushed to GitHub
- [ ] Notable findings written to `CAVEATS.md`

## Things that will get reverted

- Strategies deployed without walk-forward evidence
- "Improvements" that lower OOS Sharpe even if in-sample looks better
- Auto-applied parameter changes without 3 weeks of shadow comparison
- Filters / overlays added on top of working strategies (per v1.0 finding: most hurt)

## What this project is NOT trying to be

- Renaissance Medallion (we don't have their data, infrastructure, or expertise)
- An HFT system (wrong infrastructure)
- A way to beat SPY by 30%/yr (real edge is 3-7% of alpha; expect 17% CAGR not 30%)
- A get-rich-quick mechanism

It IS trying to be: an honest, testable, observable, gradually-improving personal trading system that compounds small alphas + risk discipline.
