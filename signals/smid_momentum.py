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

# SMID-WEEKLY: tranched-portfolio pre-registered constant. 4 sleeves
# matches the canonical 4-week rhythm. Each sleeve rebalances every
# 4th week on a rotating cycle so the book trades on a WEEKLY rhythm
# while each name's individual turnover stays roughly MONTHLY.
SMID_TRANCHED_N_SLEEVES = 4


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
    # SMID-WEEKLY: pluggable rebalance cadence.
    #   'monthly' -> first trading day of each calendar month (the
    #                MOM-2 / SMOM-3 contract; default for byte-equivalence
    #                with SMOM-3's reported numbers).
    #   'weekly'  -> first trading day of each ISO week.
    #   'always'  -> every call is a rebalance day (used by the
    #                tranched wrapper which has its own gating).
    rebalance_freq: str = "monthly"

    # ── Rebalance gate override (SMID-WEEKLY) ──────────────────────────

    def _is_rebalance_day(self, view: BarView) -> bool:
        """Dispatch on ``rebalance_freq``. Default ``'monthly'`` defers
        to the parent's month-boundary detection so existing callers
        (SMOM-3 reports) are byte-equivalent. ``'weekly'`` fires on the
        first trading day of each ISO week. ``'always'`` makes every
        call a rebalance day — used by ``TranchedSmidMomentumStrategy``,
        which has already gated by week + active-sleeve at the wrapper
        layer before delegating in.
        """
        if self.rebalance_freq == "monthly":
            return super()._is_rebalance_day(view)
        if self.rebalance_freq == "weekly":
            return self._is_iso_week_boundary(view)
        if self.rebalance_freq == "always":
            return True
        raise ValueError(
            f"unknown rebalance_freq {self.rebalance_freq!r}; expected "
            f"'monthly' | 'weekly' | 'always'")

    def _is_iso_week_boundary(self, view: BarView) -> bool:
        """True iff the cutoff bar is in a different ISO week than the
        immediately-prior causal bar. Stateless / data-derived (same
        pattern as the parent's month-boundary detection)."""
        cutoff = view.cutoff
        cw = cutoff.isocalendar()
        for sym in view.symbols():
            if sym.startswith("^"):
                continue
            h = view.history(sym)
            if len(h) < 2:
                continue
            prior = h.index[-2]
            pw = prior.isocalendar()
            return (cw.year != pw.year) or (cw.week != pw.week)
        return False

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


@dataclass
class TranchedSmidMomentumStrategy:
    """Tranched ("overlapping portfolios") wrapper around SMID momentum.

    DESIGN — Antonacci-style time-diversified momentum
    ==================================================
    Split the capital implicitly across ``n_tranches`` sleeves
    (default 4). On the first trading day of every ISO week, ONLY
    ONE sleeve rebalances — the sleeve whose index matches the
    current ISO-week number modulo ``n_tranches``. Each sleeve
    therefore rotates every ``n_tranches`` weeks (~monthly per-name
    turnover), but the BOOK trades a few names EVERY week — the
    weekly rhythm ops asked for.

    Per-sleeve top-N is the global ``top_n`` divided by
    ``n_tranches`` (default 15 // 4 = 3 with a small remainder).
    The four sleeves' positions sum to at most ``top_n``
    simultaneously; the harness's ``max_positions`` cap still
    applies globally.

    Sleeve membership is INFERRED from each open position's
    ``entry_date`` (its ISO-week index mod ``n_tranches``). No
    instance state is kept between ``decide()`` calls — the
    mapping is fully reconstructible from the harness's book.
    This preserves the stateless contract of the underlying SMID
    strategy and keeps the no-leak guarantees inherited.

    Pre-registered: ``n_tranches = 4``. Matches the canonical
    4-week month and is NOT tuned. Other tranche counts (2, 6)
    would be separate experiments; we do not sweep this knob.
    """
    # Reuse the same SMID knobs as the underlying strategy so a
    # caller doesn't have to specify them twice.
    lookback_days: int = 252                  # MOM_LOOKBACK_DAYS
    skip_days: int = 21                        # MOM_SKIP_DAYS
    top_n: int = 15                            # MOM_TOP_N
    atr_period: int = ATR_PERIOD
    use_absolute_filter: bool = False
    momentum_pool_multiplier: int = SMID_MOMENTUM_POOL_MULTIPLIER
    vol_window: int = SMID_VOL_WINDOW
    min_median_traded_value: float = SMID_MIN_MEDIAN_TRADED_VALUE
    n_tranches: int = SMID_TRANCHED_N_SLEEVES

    @staticmethod
    def _iso_week_index(ts) -> int:
        """A monotonic integer week index for an arbitrary timestamp
        (ISO calendar). Combines (iso_year, iso_week) into a single
        int so modulo arithmetic for the sleeve assignment is robust
        across year boundaries.
        """
        cal = pd.Timestamp(ts).isocalendar()
        return int(cal.year) * 53 + int(cal.week)

    def _is_iso_week_boundary(self, view: BarView) -> bool:
        """Re-implemented at the wrapper to avoid instantiating a
        throwaway ``SmidMomentumStrategy`` just for the gate check."""
        cutoff = view.cutoff
        cw = cutoff.isocalendar()
        for sym in view.symbols():
            if sym.startswith("^"):
                continue
            h = view.history(sym)
            if len(h) < 2:
                continue
            prior = h.index[-2]
            pw = prior.isocalendar()
            return (cw.year != pw.year) or (cw.week != pw.week)
        return False

    def decide(self, view: BarView, book: Book) -> list:
        """Tranched rebalance: gate on ISO-week boundary, pick the
        active sleeve, build a sub-book of just that sleeve's
        positions, delegate to ``SmidMomentumStrategy(top_n=...,
        rebalance_freq='always')`` for the actual SMID logic.
        """
        # 1) Weekly cadence at the wrapper layer.
        if not self._is_iso_week_boundary(view):
            return []

        # 2) Which sleeve's turn is it?
        cutoff_wk = self._iso_week_index(view.cutoff)
        active_sleeve = cutoff_wk % self.n_tranches

        # 3) Build a sub-book containing only positions that belong to
        # the active sleeve (sleeve = week-index-of-entry mod n).
        sleeve_positions = {
            sym: pos for sym, pos in book.positions.items()
            if self._iso_week_index(pos.entry_date) % self.n_tranches
                == active_sleeve
        }
        sub_book = Book(cash=book.cash, equity=book.equity,
                         positions=sleeve_positions)

        # 4) Per-sleeve top-N. With top_n=15 and 4 sleeves, each sleeve
        # targets 3 names. The book sum (across sleeves) is therefore
        # ~12 of the harness's 15-slot ceiling under steady state —
        # leaves some headroom for fills to actually land. Never below
        # 1 (a single sleeve must hold at least one name).
        per_sleeve_top_n = max(1, self.top_n // self.n_tranches)

        sub_strat = SmidMomentumStrategy(
            lookback_days=self.lookback_days,
            skip_days=self.skip_days,
            top_n=per_sleeve_top_n,
            atr_period=self.atr_period,
            use_absolute_filter=self.use_absolute_filter,
            momentum_pool_multiplier=self.momentum_pool_multiplier,
            vol_window=self.vol_window,
            min_median_traded_value=self.min_median_traded_value,
            rebalance_freq="always",   # wrapper already gated
        )
        return sub_strat.decide(view, sub_book)
