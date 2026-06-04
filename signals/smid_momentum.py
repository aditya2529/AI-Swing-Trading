"""Pure-rules small/mid-cap momentum strategy with a low-volatility tilt.

STRATEGY DESIGN — momentum first, low-vol second
================================================
Inherits the MOM-2 contract end-to-end (12-1 Jegadeesh-Titman cross-
sectional ranking, monthly rotation, stateless rebalance detection, the
same defensive eligibility filters) and inserts ONE additional step
between top-N selection and order emission:

    1. Score the universe by 12-1 momentum (parent's ``_compute_scores``).
    2. Rank descending; take the top ``top_n × momentum_pool_multiplier``
       (= top-2N by default) as the MOMENTUM POOL.
    3. Apply a LIQUIDITY SANITY filter — drop any name whose median
       daily traded value (close × volume) over the last ``vol_window``
       trading days is BELOW ``min_median_traded_value``. This is the
       SMID-specific defensive guard ops asked for: with small-caps the
       "ranking" can otherwise pick illiquid names where the harness's
       slippage assumption is wildly optimistic. Default floor: Rs 1
       crore (10 M rupees) daily median — a conservative threshold that
       still admits most NIFTY Midcap-150 names.
    4. From the remaining liquid pool, KEEP the ``top_n`` names with the
       LOWEST trailing realized vol over the same ``vol_window`` (63d
       daily-return std). This is the LOW-VOLATILITY TILT — pure
       cross-sectional sort within the momentum-pre-screened pool.
       Targets the small-cap momentum failure mode (the highest-vol
       winners crash hardest in reversals).
    5. From the final ``top_n`` set, emit ExitOrders for held names that
       fell out + EnterOrders for new entrants (parent's emission
       contract — unchanged).

WHY parameter-light, not parameter-free
=======================================
* ``momentum_pool_multiplier = 2`` — standard academic convention for
  "two-step" cross-sectional sorts. Not 1 (which makes the tilt a no-op
  because the tilt set IS the pool) and not 3+ (which dilutes the
  momentum signal so much the tilt becomes the dominant ranker). Two
  hits the natural balance.
* ``vol_window = 63`` (~3 trading months) — matches the MOM-5 BSC
  standard so the two diagnostics line up.
* ``min_median_traded_value = Rs 1 crore`` — a sensible SMID liquidity
  floor that ops can tune separately for SMOM-3 if needed. Pre-registered
  here; NOT swept on results.

These are committed knobs, not historical fits. Defensible from
literature + project conventions.

INHERITED CAUSALITY GUARANTEES
==============================
The parent's stateless rebalance detection + score causality apply
unchanged. The vol read (close.pct_change().std()) on a causal slice
``view.history(sym)`` is also strictly past-only. The liquidity read
(close × volume) over the same slice is past-only. End-to-end no-leak
test added in ``tests/test_smid_momentum.py``.

INHERITS BUT DOES NOT EXTEND THESE
==================================
* ``use_absolute_filter`` (MOM-4) — toggle stays inherited, default
  False; can be combined orthogonally with the low-vol tilt.
* ``vol_target_annual`` (MOM-5 harness overlay) — orthogonal harness
  knob; SMOM-3 can opt in independently.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtesting.replay import BarView, Book, EnterOrder, ExitOrder
from config import ATR_PERIOD
from signals.momentum import MomentumStrategy


# Pre-registered SMID parameters. Public module-level so SMOM-3 can
# import them rather than re-specifying — single source of truth.
SMID_MOMENTUM_POOL_MULTIPLIER = 2          # top_n * this = momentum pool size
SMID_VOL_WINDOW = 63                       # ~3 trading months
SMID_MIN_MEDIAN_TRADED_VALUE = 1.0e7        # Rs 1 crore daily median


@dataclass
class SmidMomentumStrategy(MomentumStrategy):
    """SMID cross-sectional momentum with a low-vol tilt.

    Inherits ``MomentumStrategy``'s frozen knobs (lookback_days,
    skip_days, top_n, atr_period, use_absolute_filter). Adds three
    SMID-specific knobs:

        momentum_pool_multiplier: int    (default 2)
        vol_window: int                  (default 63)
        min_median_traded_value: float   (default Rs 1 crore)

    ``decide()`` is overridden to insert the low-vol tilt + liquidity
    sanity filter between the momentum-rank and order emission. The
    parent's stateless rebalance detection, score computation, and
    EnterOrder construction (``_maybe_enter``) are all reused.
    """
    momentum_pool_multiplier: int = SMID_MOMENTUM_POOL_MULTIPLIER
    vol_window: int = SMID_VOL_WINDOW
    min_median_traded_value: float = SMID_MIN_MEDIAN_TRADED_VALUE

    # ── decide ──────────────────────────────────────────────────────────

    def decide(self, view: BarView, book: Book) -> list:
        """Return rotation orders for day-T close.

        Non-rebalance days return ``[]`` deterministically. On a
        rebalance day:
            1. score by 12-1 momentum
            2. take top ``top_n * momentum_pool_multiplier`` as the pool
            3. filter pool by liquidity sanity (median traded value)
            4. sort remaining pool ascending by trailing-vol; keep the
               lowest ``top_n``
            5. (optional) apply ``use_absolute_filter`` to top_set
            6. emit exits for held-not-in-top + entries for top-not-held
        """
        if not self._is_rebalance_day(view):
            return []

        scores = self._compute_scores(view)
        if not scores:
            return []

        # Momentum pool: top (top_n * multiplier) names by relative
        # 12-1 rank.
        pool_size = self.top_n * self.momentum_pool_multiplier
        ranked_symbols = sorted(scores, key=lambda s: scores[s], reverse=True)
        momentum_pool = ranked_symbols[:pool_size]

        # SMID-specific: liquidity sanity + vol read for each name in
        # the momentum pool. Both reads operate on the SAME causal
        # window (``vol_window`` last bars), which keeps the diagnostic
        # internally consistent and easy to reason about.
        liquidity_ok: list[str] = []
        vols: dict[str, float] = {}
        for sym in momentum_pool:
            h = view.history(sym)
            if len(h) < self.vol_window + 1:
                # Not enough bars to compute either vol or median traded
                # value — skip. The parent's eligibility check already
                # enforced >= 274 causal bars for SCORING, but the
                # vol_window filter is a separate, tighter requirement.
                continue
            recent = h.iloc[-self.vol_window:]
            # Liquidity sanity: median daily traded value.
            traded_value = (recent["close"] * recent["volume"]).median()
            if (not pd.notna(traded_value)
                    or traded_value < self.min_median_traded_value):
                continue
            liquidity_ok.append(sym)
            # Vol read: daily return std (last vol_window bars).
            rets = recent["close"].pct_change().dropna()
            if len(rets) < 2:
                continue
            sigma = float(rets.std())
            if pd.notna(sigma) and sigma > 0:
                vols[sym] = sigma

        # Keep only the liquid names that also have a valid vol reading
        # (vol can come back NaN on degenerate flat segments — skip).
        liquid_with_vol = [s for s in liquidity_ok if s in vols]

        # Low-vol tilt — sort ASCENDING by vol; keep the LOWEST top_n.
        # This is the SMOM trick. The ranking that actually drives which
        # names we buy is the vol-sorted order; the momentum step only
        # determined which pool we drew from.
        sorted_by_vol = sorted(liquid_with_vol, key=lambda s: vols[s])
        top_set = set(sorted_by_vol[:self.top_n])

        # Optional MOM-4 absolute filter (drop names with non-positive
        # 12-1 momentum). Stays orthogonal to the low-vol tilt.
        if self.use_absolute_filter:
            top_set = {s for s in top_set if scores[s] > 0}

        orders: list = []

        # Exits FIRST (parent contract). Held names that fell out of
        # the SMID top_set get rotation_out (rotation_out_ineligible if
        # they lost scoring eligibility entirely).
        for sym in book.open_symbols():
            if sym in top_set:
                continue
            if sym in scores:
                orders.append(ExitOrder(symbol=sym, reason="rotation_out"))
            else:
                orders.append(ExitOrder(
                    symbol=sym, reason="rotation_out_ineligible"))

        # Entries — emit in MOMENTUM rank order (not vol order) so the
        # harness's MAX_POSITIONS cap trims by relative-strength rank
        # when it binds. Each candidate is in ``top_set`` (vol-screened)
        # and not currently held.
        for sym in ranked_symbols[:pool_size]:
            if sym not in top_set:
                continue
            if book.has_position(sym):
                continue
            enter_order = self._maybe_enter(view, sym)
            if enter_order is not None:
                orders.append(enter_order)

        return orders
