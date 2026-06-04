"""Pure-rules cross-sectional momentum strategy for the swing replay harness.

DESIGN — "buy the winners, sell the laggards" (Jegadeesh-Titman 1993 /
AQR-style execution)
==========================================================================
At each MONTHLY REBALANCE day, rank the universe by 12-month total return
ENDING 1 month ago (the classic 12-1 momentum formulation — the skip month
defends against well-documented short-term reversal that contaminates a
raw 12-month read). Hold the top ``top_n`` names; rotate out anything no
longer in the top ``top_n``. Between rebalance days the strategy is a
no-op — positions ride.

ENTRY (on rebalance day T only):
    1. Symbol must have at least ``lookback_days + skip_days + 1`` bars
       of causal history (default 252 + 21 + 1 = 274 bars ≈ 13 months).
       Names with less history are EXCLUDED from the ranking entirely
       (per the MOM ops directive: "skip <12mo-history names regardless").
    2. ``mom_score = close[T - skip] / close[T - skip - lookback] - 1``
       — the total return from (T - skip - lookback) to (T - skip).
       Inclusive of dividends only insofar as the source data is split-
       AND dividend-adjusted (yfinance daily is; Upstox split-only).
       Carry-forward to Phase 4: the Upstox-vs-yfinance dividend seam
       must be reconciled before live trading or the live ranking will
       drift vs the backtest ranking. (Documented in CARRY_FORWARD.)
    3. Rank descending by ``mom_score``.
    4. Symbol must be in the TOP ``top_n`` (default 15).
    5. Symbol must not already be in the book.

    Order: ``EnterOrder(symbol, stop=initial_stop(close[T], atr[T]))``.
    The stop uses ``ATR_PERIOD`` (14) and ``ATR_SL_MULTIPLIER`` (2.0) —
    the standard initial-stop math, so momentum sizes on the same
    fixed-fractional risk basis as the other strategies. The harness's
    fixed-fractional sizer then translates that into shares. This is a
    "set" — the stop is NOT trailed (rotation IS the exit mechanism;
    the stop is just a catastrophe floor for the harness's risk math).

EXIT (on rebalance day T only):
    Any currently-held symbol whose rank fell out of the top ``top_n``
        -> ``ExitOrder(symbol, reason="rotation_out")``.
    Any currently-held symbol that LOST eligibility (insufficient
    history NOW vs at entry — pathological, but possible if the data
    dict is mutated mid-replay)
        -> ``ExitOrder(symbol, reason="rotation_out_ineligible")``.
    The hard stop set at entry is enforced ONLY by the harness on
    next-open gap-through (the strategy itself does NOT poll it daily —
    rotation is the exit policy).

Non-rebalance days return ``[]`` deterministically.

REBALANCE DETECTION (stateless, harness-only data)
==================================================
A day T is a rebalance day iff the immediately-prior causal bar (for
ANY universe symbol with at least 2 bars of history) is in a different
calendar month than T. This is fully stateless — the strategy keeps
NO instance memory between ``decide()`` calls — and is therefore
naturally safe for the look-ahead regression suite (no hidden state
that could leak future information across calls).

On day 1 of the timeline (no prior bar in any symbol) we ARM the
strategy WITHOUT rebalancing. The first actual rebalance happens on
the first trading day of the second calendar month covered by the
data — by which point every symbol has had a chance to build up the
12-month ranking history.

CAUSALITY
=========
Reads only ``view.history(sym)`` and ``view.cutoff``. The momentum
score uses ``close.iloc[-(skip + 1)]`` and
``close.iloc[-(skip + lookback + 1)]`` — both strictly within the
harness-clipped history (index <= T). The look-ahead regression test
(``tests/test_lookahead_regression.py::
test_decisions_invariant_to_future_mutation``) proves the harness's
clip is honest end-to-end; the momentum-specific future-mutation
test in ``tests/test_momentum.py`` exercises it through THIS strategy.

WHY 12-1, NOT 12 OR 6-1
========================
12-1 is the most-replicated academic momentum formulation (Jegadeesh
& Titman 1993; Asness, Moskowitz, Pedersen 2014; AQR live trading).
The skip month defends against short-term reversal contamination
that biases a raw 12-month read. We do NOT tune ``lookback_days``,
``skip_days`` or ``top_n`` on our historical data (LAW 9 — no
fit-to-history). The parameters come from the literature, are
committed to once in ``config.py``, and MOM-3's walk-forward proves
the OOS persistence (or doesn't).

SURVIVORSHIP CAVEAT (read this before trusting any PF from MOM-3)
=================================================================
``MOMENTUM_UNIVERSE`` is CURRENT NSE-200-ish membership, not
point-in-time. Names that were in NIFTY 200 a decade ago but have
since been delisted / merged out are entirely absent. This
systematically inflates raw historical PF (selection bias toward
names that survived to today — and survival often correlates with
historical momentum). MOM-3 MUST report PF both raw AND with the
survivorship haircut (25-30% PF discount) as the HEADLINE, per ops.
See ``MOMENTUM_SURVIVORSHIP_NOTE`` in ``data/universe.py``.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtesting.replay import BarView, Book, EnterOrder, ExitOrder
from config import (
    ATR_PERIOD, MOM_LOOKBACK_DAYS, MOM_SKIP_DAYS, MOM_TOP_N,
)
from features.engineer import compute_atr
from signals.risk import initial_stop


@dataclass
class MomentumStrategy:
    """Cross-sectional 12-1 momentum with monthly top-N rotation.

    All knobs default to the project ``config.py`` constants so the
    single source of truth lives there. Tests may override per-instance
    to exercise edges without mutating module globals.

    Statelessness: the strategy keeps NO instance memory between
    ``decide()`` calls — month-boundary detection is performed from
    the causal data alone. This means the SAME strategy instance can
    be reused across replays without leaking state, and the
    look-ahead regression suite is naturally safe.
    """
    lookback_days: int = MOM_LOOKBACK_DAYS
    skip_days: int = MOM_SKIP_DAYS
    top_n: int = MOM_TOP_N
    atr_period: int = ATR_PERIOD

    # ── decide ──────────────────────────────────────────────────────────

    def decide(self, view: BarView, book: Book) -> list:
        """Return rotation orders for day-T close.

        On a non-rebalance day this returns ``[]`` deterministically
        regardless of book state — the strategy intentionally does NOT
        poll stops or anything else between rebalances. The harness's
        own no-intrabar-fill execution still enforces the EnterOrder's
        catastrophe stop if a next-open gap takes the price through it.
        """
        if not self._is_rebalance_day(view):
            return []

        scores = self._compute_scores(view)
        if not scores:
            # Insufficient history universe-wide — nothing to rotate
            # into. Don't blindly exit existing positions either; let
            # them ride until the next rebalance day produces a real
            # ranking. (The book is initially empty, so this is mostly
            # relevant when an edge-case data dict has very short
            # series for every symbol.)
            return []

        # Top-N by 12-1 momentum, descending.
        ranked_symbols = sorted(scores, key=lambda s: scores[s], reverse=True)
        top_set = set(ranked_symbols[:self.top_n])

        orders: list = []

        # Exits FIRST — names that fell out of the top-N (or lost
        # eligibility entirely). This matches the harness's natural
        # within-tick ordering (exits before entries) so freed cash and
        # slot space accrue to today's rotation entries at the next
        # day's open. Skip exit emission for any name still in top_set.
        for sym in book.open_symbols():
            if sym in top_set:
                continue
            if sym in scores:
                orders.append(ExitOrder(symbol=sym, reason="rotation_out"))
            else:
                orders.append(ExitOrder(
                    symbol=sym, reason="rotation_out_ineligible"))

        # Entries — top-N names we don't already hold. Emit ALL of them
        # in rank order; the harness's MAX_POSITIONS / MAX_PER_SECTOR /
        # MAX_PORTFOLIO_HEAT caps then trim the actual fills. We don't
        # second-guess the cap here — the harness is the single source
        # of truth for portfolio risk constraints (LAW 6).
        for sym in ranked_symbols[:self.top_n]:
            if book.has_position(sym):
                continue
            enter_order = self._maybe_enter(view, sym)
            if enter_order is not None:
                orders.append(enter_order)

        return orders

    # ── Rebalance detection ─────────────────────────────────────────────

    def _is_rebalance_day(self, view: BarView) -> bool:
        """A day T is a rebalance day iff its immediately-prior causal
        bar (for any universe symbol with >= 2 causal bars) is in a
        different calendar month than T.

        Stateless: derived from the causal data alone. Independent of
        which symbol we look at because NSE equities share a common
        trading calendar — if any symbol has a prior bar at all, that
        prior bar's date is THE prior trading day.
        """
        cutoff = view.cutoff
        for sym in view.symbols():
            if sym.startswith("^"):
                continue   # macro indices — skip; use a real equity for clarity
            h = view.history(sym)
            if len(h) < 2:
                continue
            prior = h.index[-2]
            return prior.month != cutoff.month or prior.year != cutoff.year
        # No symbol has a prior bar yet — day 1 of the timeline. Arm
        # without rebalancing.
        return False

    # ── Scoring ─────────────────────────────────────────────────────────

    def _compute_scores(self, view: BarView) -> dict:
        """Compute 12-1 momentum scores for every eligible symbol.

        A symbol is ELIGIBLE iff its causal history has at least
        ``lookback_days + skip_days + 1`` bars (default 274). Newer
        listings (e.g. LICI listed May 2022) with shorter history are
        SKIPPED entirely — they don't appear in the ranking, they
        don't get traded, they don't displace longer-history names.
        ``^``-prefixed symbols (^NSEI, ^INDIAVIX) are never ranked
        — they are macro context, never traded.
        """
        scores: dict = {}
        needed = self.lookback_days + self.skip_days + 1
        end_idx = -(self.skip_days + 1)
        start_idx = -(self.lookback_days + self.skip_days + 1)
        for sym in view.symbols():
            if sym.startswith("^"):
                continue
            h = view.history(sym)
            if len(h) < needed:
                continue
            close = h["close"]
            end_price = float(close.iloc[end_idx])
            start_price = float(close.iloc[start_idx])
            if start_price <= 0 or not pd.notna(start_price):
                continue
            if not pd.notna(end_price):
                continue
            scores[sym] = (end_price / start_price) - 1.0
        return scores

    # ── Entry helper ────────────────────────────────────────────────────

    def _maybe_enter(self, view: BarView, sym: str) -> EnterOrder | None:
        """Build the EnterOrder for ``sym``. Returns ``None`` if the
        ATR-based stop can't be computed (degenerate price data). The
        caller has already verified ``sym`` is in the top-N and we are
        currently flat in it.
        """
        h = view.history(sym)
        # ATR(14) needs at least ~2 * period bars to warm up cleanly.
        # The scoring eligibility check (>= 274 bars) already guarantees
        # this, but be defensive — a test could pass in a short series.
        if len(h) < self.atr_period * 2 + 1:
            return None
        close_t = float(h["close"].iloc[-1])
        if not pd.notna(close_t) or close_t <= 0:
            return None
        atr_series = compute_atr(h, period=self.atr_period)
        if atr_series.empty:
            return None
        atr_t = float(atr_series.iloc[-1])
        if not pd.notna(atr_t) or atr_t <= 0:
            return None

        # Initial stop — anchored at close[T] for the screen; the
        # harness recomputes risk_per_share at the T+1 open fill.
        stop = initial_stop(entry=close_t, atr=atr_t)
        if not (stop < close_t):
            return None   # defensive — ATR can't be huge enough, but be safe.

        return EnterOrder(
            symbol=sym, stop=stop,
            reason=f"mom_12-1_top{self.top_n}")
