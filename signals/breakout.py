"""Pure-rules Donchian breakout strategy for the swing-trading replay harness.

DESIGN
======
A deterministic, rules-only strategy callable. No ML, no training, no
hidden state. The class implements one method — ``decide(view, book)`` —
which the replay loop calls at each day-T close with a strictly causal
view (bars whose index <= T) and a snapshot of the open book. Returned
orders fill at T+1's open per the harness contract.

ENTRY (all conditions must hold at day-T close; symbol must be FLAT):
    1. Donchian-upper break: close[T] > max(high[T-N : T])         — EXCLUDING T
       so the break is not trivially satisfied by T's own high.
    2. Volume confirmation: volume[T] > VOLUME_MULT × mean(volume[T-W : T])
       (also excluding T from the average — that's the # 1 fakeout filter).
    3. Regime ON: ^NSEI close[T] > its REGIME_MA-day moving average,
       computed from THE SAME causal view, ending at T.
    4. R:R screen passes against the measured-move target (signals/risk.py).

    Order: ``EnterOrder(symbol, stop=initial_stop(close[T], atr[T]))``.

EXIT (evaluated at T-close for any symbol the book is long in):
    A. close[T] < chandelier_stop(p.highest_high, atr[T]); OR
    B. p.bars_held >= MAX_HOLD_DAYS  (time stop)
    -> ``ExitOrder(symbol, reason=...)`` — fills T+1 open.
    The initial hard stop carried on the EnterOrder lets the harness
    fill stop-outs at T+1 open if a gap takes the next-day open through
    it (no intrabar fills, per the harness execution model).

CAUSALITY
=========
The strategy reads ONLY ``view.history(sym)`` / ``view.latest(sym)`` /
``view.has_bar(sym)`` and ``book`` — every one of which is causally
clipped to ``index <= T`` by the harness. The gate test
(``tests/test_lookahead_regression.py::test_decisions_invariant_to_future_mutation``)
proves the harness's clip is honest; the breakout-specific mutation
test in ``tests/test_breakout.py`` exercises it through THIS strategy
end-to-end. If a future change accidentally peeks (e.g., a feature
that uses ``.shift(-1)``), both tests turn red simultaneously.

NOTE FOR T3/T4 (carry-forward, not actionable here)
===================================================
Upstox historical OHLCV is split-adjusted ONLY; yfinance live data is
split- AND dividend-adjusted. The historical backtest reads a single
source (Upstox -> market_data.db) and is unaffected. The seam bites
only at Phase 4 when the LIVE engine starts pulling yfinance prices
alongside historical Upstox features — at that point a cumulative-
dividend offset must be applied or large ex-dividend dates will
manifest as phantom breakouts at the seam. Don't solve it here.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtesting.replay import BarView, Book, EnterOrder, ExitOrder
from config import (
    ATR_PERIOD, BREAKOUT_LOOKBACK, MAX_HOLD_DAYS, MIN_RR, REGIME_INDEX,
    REGIME_MA, VOLUME_AVG_WINDOW, VOLUME_MULT,
)
from features.engineer import compute_atr
from signals.risk import (
    chandelier_stop, initial_stop, measured_move_target, rr_screen,
)


@dataclass
class BreakoutStrategy:
    """Donchian breakout + Chandelier exit + measured-move R:R screen.

    All thresholds default to the project ``config.py`` constants so the
    risk team's calibration is the single source of truth. Tests may
    override per-instance to exercise edge cases without mutating module
    globals.
    """
    lookback: int = BREAKOUT_LOOKBACK
    vol_window: int = VOLUME_AVG_WINDOW
    vol_mult: float = VOLUME_MULT
    regime_index: str = REGIME_INDEX
    regime_ma: int = REGIME_MA
    atr_period: int = ATR_PERIOD
    min_rr: float = MIN_RR
    max_hold_days: int = MAX_HOLD_DAYS

    # ── decide ──────────────────────────────────────────────────────────

    def decide(self, view: BarView, book: Book) -> list:
        """Return all entry + exit orders for day-T close.

        Exits are emitted before entries so freed cash + slot space is
        available to today's new entries when they fill at T+1 open
        (the harness already processes exits before entries within a
        single tick — this just matches the natural ordering).
        """
        orders: list = []

        # ── Regime gate — global, read once ────────────────────────────
        # If we can't compute the regime, default to OFF (safety: no new
        # entries). Existing positions still get their exits evaluated.
        regime_on = self._regime_on(view)

        # ── Exits for currently-open positions ─────────────────────────
        for sym, pos in book.positions.items():
            exit_order = self._maybe_exit(view, sym, pos)
            if exit_order is not None:
                orders.append(exit_order)

        if not regime_on:
            return orders

        # ── Entries for symbols we're FLAT in ──────────────────────────
        for sym in view.symbols():
            if sym == self.regime_index:
                continue            # macro context; never traded
            if book.has_position(sym):
                continue            # already in a position
            enter_order = self._maybe_enter(view, sym)
            if enter_order is not None:
                orders.append(enter_order)

        return orders

    # ── Regime ──────────────────────────────────────────────────────────

    def _regime_on(self, view: BarView) -> bool:
        h = view.history(self.regime_index)
        if len(h) < self.regime_ma:
            return False
        ma = h["close"].iloc[-self.regime_ma:].mean()
        return float(h["close"].iloc[-1]) > float(ma)

    # ── Entry ───────────────────────────────────────────────────────────

    def _maybe_enter(self, view: BarView, sym: str) -> EnterOrder | None:
        h = view.history(sym)
        # Need enough bars for: lookback window (BEFORE T) + 1 day for T
        # + ATR warm-up + volume window. Volume and lookback share width.
        needed = max(self.lookback, self.vol_window, self.atr_period) + 1
        if len(h) < needed:
            return None

        close_t = float(h["close"].iloc[-1])
        vol_t = float(h["volume"].iloc[-1])

        # Donchian upper — prior N HIGHS, strictly EXCLUDING day T.
        prior_window = h.iloc[-(self.lookback + 1):-1]
        breakout_level = float(prior_window["high"].max())
        if not (close_t > breakout_level):
            return None

        # Volume confirmation — prior W days, excluding T (so the
        # breakout-day's own volume isn't part of the baseline).
        vol_prior = h["volume"].iloc[-(self.vol_window + 1):-1]
        vol_avg = float(vol_prior.mean())
        if vol_avg <= 0 or not (vol_t > self.vol_mult * vol_avg):
            return None

        # ATR(14) at T — uses past TR data through T; .iloc[-1] is bar T.
        atr_series = compute_atr(h, period=self.atr_period)
        if atr_series.empty:
            return None
        atr_t = float(atr_series.iloc[-1])
        if atr_t <= 0 or not pd.notna(atr_t):
            return None

        # Initial stop — anchored at close[T] for the screen; actual
        # risk-per-share is recomputed by the harness at the T+1 open fill.
        stop = initial_stop(entry=close_t, atr=atr_t)
        if not (stop < close_t):
            return None  # defensive — ATR can't be huge enough, but be safe.

        # Measured-move target for the R:R screen — uses the SAME prior
        # N-day window as the Donchian level for the channel-height projection.
        recent_low = float(prior_window["low"].min())
        if not (recent_low < breakout_level):
            return None  # degenerate window (e.g. all bars identical).
        target = measured_move_target(
            breakout_level=breakout_level, recent_low=recent_low)
        if not rr_screen(entry=close_t, stop=stop, target=target,
                          min_rr=self.min_rr):
            return None

        return EnterOrder(
            symbol=sym, stop=stop,
            reason=(f"donchian{self.lookback}_break+vol{self.vol_mult}x"
                    f"+regime_on+rr>={self.min_rr}"))

    # ── Exit ────────────────────────────────────────────────────────────

    def _maybe_exit(self, view: BarView, sym: str, pos) -> ExitOrder | None:
        # Time stop — terminate at the configured ceiling regardless of trail.
        if pos.bars_held >= self.max_hold_days:
            return ExitOrder(symbol=sym,
                              reason=f"time_stop_{self.max_hold_days}d")

        h = view.history(sym)
        if h.empty or len(h) < self.atr_period:
            return None
        close_t = float(h["close"].iloc[-1])
        atr_series = compute_atr(h, period=self.atr_period)
        if atr_series.empty:
            return None
        atr_t = float(atr_series.iloc[-1])
        if atr_t <= 0 or not pd.notna(atr_t):
            return None

        trail = chandelier_stop(highest_high_since_entry=pos.highest_high,
                                 atr=atr_t)
        if close_t < trail:
            return ExitOrder(symbol=sym, reason="chandelier")
        return None
