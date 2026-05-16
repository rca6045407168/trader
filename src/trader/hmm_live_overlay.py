"""HMM regime live-overlay (v6.1.2, 2026-05-15).

Walk-forward-validated regime gate that scales gross exposure based on the
posterior probability of being in BULL / TRANSITION / BEAR regimes. Distinct
from the older `regime_overlay.py`, which:
  - Trains on ~2.7y of SPY (this overlay uses 10+ years)
  - Uses class-based scaling 1.15 / 0.85 / 0.30 (this uses 1.0 / 0.6 / 0.0)
  - Combines HMM with macro + GARCH (this is HMM-only)
  - Different env gate (`REGIME_OVERLAY_ENABLED`); this uses `HMM_REGIME_MODE`

Walk-forward result (`scripts/walk_forward_proposals.py`, 5 non-overlapping
12-month windows, OOS training on 2010-2020 SPY):
  - Avg CAGR: +30.97% (vs cash-park-only's +30.75%) — essentially tied
  - Avg Sharpe: 2.01 (vs 1.27) — +0.74 absolute improvement
  - Avg max-DD: −6.7% (vs −17.7%) — 11 pp shallower
  - Win rate vs SPY: 4/5 windows (vs 3/5 for cash-park-only)

Default mode is INERT. The env gate has 3 states:
  - INERT (default): function still computes the would-have-fired signal
    for observability but applies no multiplier. risk_manager won't even
    log it unless explicitly asked.
  - SHADOW: same as INERT but risk_manager logs the would-fired multiplier
    alongside actuals. This is the 30-day observation phase before LIVE.
  - LIVE: multiplier is applied to gross exposure in risk_manager.

Scaling formula:
    scale = posterior(BULL)*1.0 + posterior(TRANSITION)*0.6 + posterior(BEAR)*0.0

Where posteriors come from forward-filter (predict_proba) at the most-recent
observation. The HMM itself is trained on >= 10 years of SPY daily returns
using 3-state Gaussian HMM via hmmlearn.

24h disk cache. EM training takes 2-3s; running once per day is plenty
for a monthly-rebalance strategy.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


# Env gate: INERT / SHADOW / LIVE.
HMM_REGIME_MODE = os.environ.get("HMM_REGIME_MODE", "INERT").upper()

# Walk-forward-validated multipliers (do NOT change without re-running
# walk_forward_proposals.py and updating the Research Backlog).
SCALE_BULL = 1.0
SCALE_TRANSITION = 0.6
SCALE_BEAR = 0.0

# Training history: use 10+ years for the HMM to see multiple regimes.
# 252 * 11 = 2772 days minimum; we fetch 252 * 12 to give buffer for
# weekends/holidays.
TRAIN_HISTORY_DAYS = 252 * 12

# Disk cache: HMM only updates once per 24h. Caches the posterior at the
# most-recent observation (not the model itself).
_CACHE_TTL_SEC = 86400
_CACHE_FILE = Path(os.environ.get("TRADER_DATA_DIR",
                                    str(Path(__file__).resolve().parent.parent.parent / "data")
                                    )) / "hmm_live_overlay_cache.json"


@dataclass
class HMMOverlaySignal:
    """The would-fired signal at a moment in time."""
    mode: str                  # "INERT" | "SHADOW" | "LIVE"
    scale: float               # gross multiplier in [0, 1]
    regime: str                # most-likely regime: "bull"/"transition"/"bear"
    posterior_max: float       # confidence in most-likely regime
    p_bull: float              # P(BULL state) at most-recent obs
    p_transition: float        # P(TRANSITION state)
    p_bear: float              # P(BEAR state)
    trained_on_days: int
    error: Optional[str] = None

    def rationale(self) -> str:
        if self.error:
            return f"hmm_live: error={self.error}, scale=1.0 (no-op)"
        return (
            f"hmm_live[{self.mode}]: {self.regime}({self.posterior_max:.0%}) "
            f"p_bull={self.p_bull:.2f} p_trans={self.p_transition:.2f} "
            f"p_bear={self.p_bear:.2f} → scale={self.scale:.2f}"
        )

    def is_active(self) -> bool:
        """True iff mode == LIVE AND scale < 1.0 (i.e. mode would mutate targets)."""
        return self.mode == "LIVE" and self.scale < 1.0


def _read_cache() -> Optional[dict]:
    if not _CACHE_FILE.exists():
        return None
    try:
        d = json.loads(_CACHE_FILE.read_text())
        ts = datetime.fromisoformat(d.get("_cached_at", "1970-01-01"))
        if (datetime.utcnow() - ts).total_seconds() > _CACHE_TTL_SEC:
            return None
        return d
    except Exception:
        return None


def _write_cache(d: dict) -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        d = dict(d)
        d["_cached_at"] = datetime.utcnow().isoformat()
        _CACHE_FILE.write_text(json.dumps(d, indent=2))
    except Exception:
        pass


def compute_hmm_overlay(mode: Optional[str] = None,
                         force_refit: bool = False) -> HMMOverlaySignal:
    """Compute the HMM regime overlay signal.

    Args:
        mode: override env-derived HMM_REGIME_MODE (for testing).
        force_refit: bypass 24h disk cache.

    Returns HMMOverlaySignal. INERT mode still computes the signal but
    `is_active()` returns False so risk_manager won't apply it.
    """
    mode = (mode or HMM_REGIME_MODE).upper()

    if not force_refit:
        cached = _read_cache()
        if cached is not None and "scale" in cached:
            return HMMOverlaySignal(
                mode=mode,
                scale=float(cached["scale"]) if mode == "LIVE" else (1.0 if mode == "SHADOW" else 1.0),
                regime=cached.get("regime", "unknown"),
                posterior_max=float(cached.get("posterior_max", 0.0)),
                p_bull=float(cached.get("p_bull", 0.0)),
                p_transition=float(cached.get("p_transition", 0.0)),
                p_bear=float(cached.get("p_bear", 0.0)),
                trained_on_days=int(cached.get("trained_on_days", 0)),
                error=cached.get("error"),
            )

    # Fit HMM on SPY history
    try:
        from .data import fetch_history
        from .hmm_regime import fit_hmm, HMMRegime
        end = datetime.utcnow()
        start = end - timedelta(days=TRAIN_HISTORY_DAYS)
        spy = fetch_history(["SPY"], start=start.strftime("%Y-%m-%d"))
        if spy.empty or "SPY" not in spy.columns:
            return HMMOverlaySignal(mode=mode, scale=1.0, regime="error",
                                      posterior_max=0.0, p_bull=0.0,
                                      p_transition=0.0, p_bear=0.0,
                                      trained_on_days=0,
                                      error="SPY history empty")
        returns = spy["SPY"].pct_change().dropna()
        # Need at least 5 years for a stable 3-state fit
        if len(returns) < 252 * 5:
            return HMMOverlaySignal(mode=mode, scale=1.0, regime="insufficient_data",
                                      posterior_max=0.0, p_bull=0.0,
                                      p_transition=0.0, p_bear=0.0,
                                      trained_on_days=len(returns),
                                      error=f"only {len(returns)} returns (need 5y)")
        hmm = fit_hmm(returns, n_states=3, n_iter=200)
        # Forward-filter posterior at most-recent observation
        X = returns.values.reshape(-1, 1)
        posteriors = hmm.model.predict_proba(X)  # (T, K)
        last_post = posteriors[-1]
        # Identify which state-index maps to which regime
        idx_bull = idx_trans = idx_bear = -1
        for i in range(hmm.n_states):
            r = hmm.state_to_regime[i]
            if r == HMMRegime.BULL:
                idx_bull = i
            elif r == HMMRegime.TRANSITION:
                idx_trans = i
            elif r == HMMRegime.BEAR:
                idx_bear = i
        p_bull = float(last_post[idx_bull]) if idx_bull >= 0 else 0.0
        p_transition = float(last_post[idx_trans]) if idx_trans >= 0 else 0.0
        p_bear = float(last_post[idx_bear]) if idx_bear >= 0 else 0.0
        scale = p_bull * SCALE_BULL + p_transition * SCALE_TRANSITION + p_bear * SCALE_BEAR
        # Identify dominant regime
        most_likely_idx = int(np.argmax(last_post))
        regime = hmm.state_to_regime[most_likely_idx].value
        posterior_max = float(last_post[most_likely_idx])

        result = {
            "scale": scale,
            "regime": regime,
            "posterior_max": posterior_max,
            "p_bull": p_bull,
            "p_transition": p_transition,
            "p_bear": p_bear,
            "trained_on_days": len(returns),
        }
        _write_cache(result)

        # In INERT/SHADOW, scale is reported but not applied
        applied_scale = scale if mode == "LIVE" else 1.0
        return HMMOverlaySignal(
            mode=mode,
            scale=applied_scale,
            regime=regime,
            posterior_max=posterior_max,
            p_bull=p_bull,
            p_transition=p_transition,
            p_bear=p_bear,
            trained_on_days=len(returns),
        )
    except Exception as e:
        return HMMOverlaySignal(mode=mode, scale=1.0, regime="error",
                                  posterior_max=0.0, p_bull=0.0,
                                  p_transition=0.0, p_bear=0.0,
                                  trained_on_days=0,
                                  error=f"{type(e).__name__}: {e}")
