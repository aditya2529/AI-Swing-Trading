"""Pure-rules mean-reversion strategy for the swing-trading replay harness.

DESIGN — "buy the oversold dip in an uptrend"
=============================================
A deterministic, rules-only strategy callable. Implements ``decide(view,
book)`` against the same harness contract as ``BreakoutStrategy``. No ML,
no parameter optimization to fit history. Configuration constants live
in ``config.py`` (MR_* prefix); per-trade math lives in ``signals/risk.py``.

ENTRY (all conditions at day-T close; symbol must be FLAT):
    1. Stock-level uptrend: close[T] > MR_TREND_MA-day MA (default 200).
       Caps falling-knife risk: we will ONLY dip-buy a name that is in
       a genuine long-term uptrend.
    2. Oversold: RSI(MR_RSI_PERIOD)[T] < MR_RSI_OVERSOLD (default 30).
       Standard RSI from features.engineer — look-ahead-clean.

    Order: ``EnterOrder(symbol, stop=initial_stop(close[T], atr[T]))``.

EXIT (evaluated at T close for any symbol the book is long in):
    A. Bounce: RSI(MR_RSI_PERIOD)[T] > MR_RSI_EXIT (default 55).
       This is the DIRECT symmetric inverse of the entry — we entered
       when RSI was stretched DOWN, we exit when RSI shows the bounce
       has carried into the upper half of its range. Compared to a
       short-MA-cross exit, RSI is a more direct measure of momentum
       reversal and the simpler rule to reason about per LAW 9.
    B. Hard stop: close[T] < position.stop. The harness fills at T+1
       open per its conservative execution model (no intrabar fills —
       a gap through the stop fills worse, never better; this never
       overstates edge). The stop level was set at entry via
       initial_stop(close[entry], atr[entry]) and does NOT trail.
    C. Time stop: bars_held >= MR_MAX_HOLD_DAYS (default 10).

    Order: ``ExitOrder(symbol, reason=...)`` — fills T+1 open.

WHY NO MARKET REGIME GATE (^NSEI > 50-DMA)?
===========================================
Approved by ops for the MR-1 baseline. The entry already filters at
the SYMBOL level — stock-uptrend AND oversold — which is the more
informative signal for a mean-reversion setup. Adding the broad-index
regime would systematically over-filter the cases the strategy is
designed to capture: a quality name above its own 200-DMA experiencing
a deep dip in a NIFTY pullback is EXACTLY the textbook setup. The
breakout strategy needed the regime gate because breakouts in falling
markets are usually fakeouts; mean-reversion is the opposite problem —
choppy/sideways/mild-pullback markets are when oversold-bounces work.

The trade-off this exposes is correlated knife-catch risk: when the
whole universe sells off together (e.g. COVID-March-2020, election
crashes), the strategy would happily put on multiple oversold-buys
simultaneously, concentrating risk. MR-2 measures the max drawdown
this produces; if catastrophic, a portfolio-level DD cap is a separate
proposed ticket, NOT a baseline change here (LAW 4).

CAUSALITY
=========
Reads only ``view.history(sym)``, ``view.latest(sym)`` and ``book``.
Every accessor is harness-clipped to ``index <= T``. The gate test
``tests/test_lookahead_regression.py`` proves the harness clip is
honest; the MR-specific future-mutation test in
``tests/test_mean_reversion.py`` exercises that through THIS strategy.

CARRY-FORWARD NOTE (MR-2 / Phase 4)
===================================
Upstox historical OHLCV is split-adjusted only; yfinance live data is
split- AND dividend-adjusted. The historical backtest reads one source
(Upstox via market_data.db) and is unaffected. The seam bites only at
Phase 4 when the live engine pulls yfinance prices alongside Upstox-
indexed signals — a cumulative-dividend offset must be applied or
every large ex-dividend date will look like an artificial RSI dip.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtesting.replay import BarView, Book, EnterOrder, ExitOrder
from config import (
    ATR_PERIOD, MR_MAX_HOLD_DAYS, MR_RSI_EXIT, MR_RSI_OVERSOLD,
    MR_RSI_PERIOD, MR_TREND_MA,
)
from features.engineer import compute_atr, compute_rsi
from signals.risk import initial_stop


@dataclass
class MeanReversionStrategy:
    """Oversold-in-uptrend mean-reversion strategy.

    Defaults pull from ``config.py``; tests may override per-instance
    to exercise edges without mutating module globals.
    """
    trend_ma: int = MR_TREND_MA
    rsi_period: int = MR_RSI_PERIOD
    rsi_oversold: float = MR_RSI_OVERSOLD
    rsi_exit: float = MR_RSI_EXIT
    atr_period: int = ATR_PERIOD
    max_hold_days: int = MR_MAX_HOLD_DAYS

    # ── decide ──────────────────────────────────────────────────────────

    def decide(self, view: BarView, book: Book) -> list:
        """Return entry + exit orders for the day-T close.

        Exits are emitted before entries so freed slot space is
        implicitly available to today's new entries when they fill at
        T+1 open (matches the harness's own exits-before-entries order
        within a tick).
        """
        orders: list = []

        # Exits for currently-open positions
        for sym, pos in book.positions.items():
            exit_order = self._maybe_exit(view, sym, pos)
            if exit_order is not None:
                orders.append(exit_order)

        # Entries for symbols we're FLAT in
        for sym in view.symbols():
            if book.has_position(sym):
                continue
            enter_order = self._maybe_enter(view, sym)
            if enter_order is not None:
                orders.append(enter_order)

        return orders

    # ── Entry ───────────────────────────────────────────────────────────

    def _maybe_enter(self, view: BarView, sym: str) -> EnterOrder | None:
        h = view.history(sym)
        # Need enough bars for: trend MA + RSI warm-up + ATR warm-up + 1
        # for the decision bar itself. trend_ma dominates.
        needed = max(self.trend_ma, self.rsi_period * 5, self.atr_period * 5) + 1
        if len(h) < needed:
            return None

        close_t = float(h["close"].iloc[-1])

        # 1. Uptrend filter: close[T] > MR_TREND_MA-day MA(close).
        #    MA includes T (a moving average at T uses bars T - (N-1) .. T);
        #    causal because all bars are <= T.
        trend_ma_t = float(h["close"].iloc[-self.trend_ma:].mean())
        if not (close_t > trend_ma_t):
            return None

        # 2. Oversold RSI.
        rsi_series = compute_rsi(h["close"], period=self.rsi_period)
        if rsi_series.empty:
            return None
        rsi_t = float(rsi_series.iloc[-1])
        if not pd.notna(rsi_t) or not (rsi_t < self.rsi_oversold):
            return None

        # 3. ATR-based hard stop for sizing + later exit check.
        atr_series = compute_atr(h, period=self.atr_period)
        if atr_series.empty:
            return None
        atr_t = float(atr_series.iloc[-1])
        if not pd.notna(atr_t) or atr_t <= 0:
            return None

        stop = initial_stop(entry=close_t, atr=atr_t)
        if not (stop < close_t):
            return None  # defensive

        return EnterOrder(
            symbol=sym, stop=stop,
            reason=(f"mr_dip:close>{self.trend_ma}dma,rsi<{self.rsi_oversold}"))

    # ── Exit ────────────────────────────────────────────────────────────

    def _maybe_exit(self, view: BarView, sym: str, pos) -> ExitOrder | None:
        # Time stop — terminate at the configured ceiling regardless of state.
        if pos.bars_held >= self.max_hold_days:
            return ExitOrder(symbol=sym,
                              reason=f"time_stop_{self.max_hold_days}d")

        h = view.history(sym)
        if h.empty:
            return None
        close_t = float(h["close"].iloc[-1])

        # Hard stop — close-based check, harness fills next open.
        # This is what the brief calls "C. Hard ATR stop carried on the
        # order (harness fills at T+1 open)" — the close < stop condition
        # is the strategy's check; the actual fill is at T+1 open (per
        # the harness's no-intrabar-fill execution model).
        if close_t < pos.stop:
            return ExitOrder(symbol=sym, reason="hard_stop")

        # Bounce exit — RSI > exit threshold.
        if len(h) < self.rsi_period * 2:
            return None
        rsi_series = compute_rsi(h["close"], period=self.rsi_period)
        if rsi_series.empty:
            return None
        rsi_t = float(rsi_series.iloc[-1])
        if pd.notna(rsi_t) and rsi_t > self.rsi_exit:
            return ExitOrder(symbol=sym,
                              reason=f"bounce_rsi>{self.rsi_exit:.0f}")

        return None
