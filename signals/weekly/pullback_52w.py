"""WEEKLY-SWEEP #6 — 52-week-high pullback.

ENTRY (weekly):
    Within 5% of the trailing 252-day high
    AND RSI(14) < 40
EXIT (daily):
    RSI(14) > 55 OR bars_held >= 10

Source: classic "buy the dip in a leader" pattern (e.g., Minervini
style: only buy stocks near their 52-week high; the RSI dip filters
for actual pullbacks). 5% / RSI<40 / RSI>55 / 10-day are standard
parameters — NOT tuned.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtesting.replay import BarView, Book, EnterOrder, ExitOrder
from config import ATR_PERIOD
from features.engineer import compute_rsi
from signals.weekly._common import (
    is_iso_week_boundary, make_enter_with_atr_stop,
)


@dataclass
class Pullback52W:
    high_lookback: int = 252                # 1 trading year
    near_high_threshold: float = 0.05        # within 5% of 252d high
    rsi_period: int = 14
    rsi_oversold: float = 40.0
    rsi_exit: float = 55.0
    max_hold_days: int = 10
    atr_period: int = ATR_PERIOD

    def decide(self, view: BarView, book: Book) -> list:
        orders: list = []

        # ── Exits: RSI > 55 or 10-day time stop, daily ─────────────
        for sym, pos in book.positions.items():
            if pos.bars_held >= self.max_hold_days:
                orders.append(ExitOrder(
                    symbol=sym,
                    reason=f"time_stop_{self.max_hold_days}d"))
                continue
            h = view.history(sym)
            if len(h) < self.rsi_period * 2:
                continue
            rsi = compute_rsi(h["close"], period=self.rsi_period)
            if rsi.empty:
                continue
            rsi_t = float(rsi.iloc[-1])
            if pd.notna(rsi_t) and rsi_t > self.rsi_exit:
                orders.append(ExitOrder(
                    symbol=sym, reason=f"rsi14>{self.rsi_exit:.0f}"))

        # ── Entries: weekly cadence ────────────────────────────────
        if not is_iso_week_boundary(view):
            return orders

        for sym in view.symbols():
            if sym.startswith("^"):
                continue
            if book.has_position(sym):
                continue
            h = view.history(sym)
            if len(h) < self.high_lookback + 1:
                continue

            close_t = float(h["close"].iloc[-1])
            high_252 = float(h["high"].iloc[-self.high_lookback:].max())
            if high_252 <= 0:
                continue
            # Within 5% of 252-day high
            if not ((high_252 - close_t) / high_252 < self.near_high_threshold):
                continue
            rsi = compute_rsi(h["close"], period=self.rsi_period)
            if rsi.empty:
                continue
            rsi_t = float(rsi.iloc[-1])
            if not pd.notna(rsi_t) or not (rsi_t < self.rsi_oversold):
                continue

            order = make_enter_with_atr_stop(
                view, sym, atr_period=self.atr_period,
                reason=f"52wh_pullback+rsi14<{self.rsi_oversold:.0f}")
            if order is not None:
                orders.append(order)

        return orders
