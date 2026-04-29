# Pre-Mortem Template (variant promotion)

**Use this BEFORE promoting any shadow variant to LIVE.** Write the document.
If you can't fill it out clearly, you don't understand the change well enough.

---

## Variant under consideration

- **Variant ID:** ___________________________________________
- **Hypothesis (1 sentence):** _______________________________
- **Source paper / replication record:** ____________________

## Backtest evidence

- Survivor backtest mean Sharpe: ______
- **PIT validation mean Sharpe: ______** (must be ≥ PIT baseline +0.10)
- Worst-MaxDD vs LIVE: ______ pp
- Win rate vs LIVE across 5 regimes: ___ / 5

## Live shadow evidence

- Days of shadow_decisions logged: ______ (must be ≥30)
- Realized shadow Sharpe vs LIVE: ______
- `paired_test()` p-value: ______ (must be < 0.05)

## Pre-mortem questions (answer all)

### What could go wrong if I promote this?

(Write 3-5 sentences. Be specific. Don't bullet "things might be worse" —
say HOW.)

```
[fill in]
```

### What's the worst regime for this variant historically?

(Cite which of the 5 regimes had the worst Sharpe / total return for
this variant. Quantify.)

```
[fill in]
```

### What's my exit criteria if this variant underperforms LIVE post-promotion?

(Concrete: "If realized Sharpe < LIVE - 0.3 over 60 trading days, I revert
to LIVE." Don't say "we'll see" — pre-commit numerically.)

```
[fill in]
```

### What did the previous LIVE owner know that I might be missing?

(Re-read the killed-list in CLAUDE.md. Has any prior variant been killed
for a reason that applies here? Especially: bolt-on stress signals,
survivor-bias-only edges, lookahead-biased fundamental data.)

```
[fill in]
```

### Have I run the spec test?

- [ ] `pytest tests/test_variant_consistency.py -v` passes
- [ ] `build_targets()` output exactly matches `live.fn()` output for the new variant

## Promotion gates (all must pass)

- [ ] Survivor backtest: ≥ 3 of 5 regime wins, no worse worst-MaxDD vs LIVE
- [ ] **PIT validation: beats PIT baseline (+0.98) by ≥ 0.10 mean Sharpe**
- [ ] ≥ 30 days of live shadow evidence
- [ ] paired_test() p-value < 0.05
- [ ] Pre-mortem written and answered above
- [ ] Spec test passes
- [ ] Behavioral pre-commit re-read

## Sign-off

- Promoting from version: ______
- Promoting to LIVE: ______
- Old LIVE marked retired: ______
- Date: ______

(Never amend this document after promotion. Write a new one for the next change.)
