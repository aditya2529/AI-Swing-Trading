"""WEEKLY-SWEEP #4 — Gap-down reversal.

ENTRY (weekly):
    open[T] / close[T-1] < 0.95   (gap-down ≥ 5%)
    AND close[T] > MA(200)        (uptrend filter — no falling knives)
EXIT (daily):
    close > entry_price (bounce above the entry-fill price) OR
    bars_held >= 3 (time stop)

Source: classic gap-reversal pattern — gap-down in uptrend often
bounces. 5% threshold is the standard "meaningful" gap; 3-day hold
matches a short mean-reversion window. NOT tuned.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtesting.replay import BarView, Book, EnterOrder, ExitOrder
from config import ATR_PERIOD, MR_TREND_MA
from signals.weekly._common import (
    is_iso_week_boundary, make_enter_with_atr_stop,
)


@dataclass
class GapReversal:
    gap_threshold: float = 0.05         # 5% gap-down
    trend_ma: int = MR_TREND_MA          # 200
    max_hold_days: int = 3
    atr_period: int = ATR_PERIOD

    def decide(self, view: BarView, book: Book) -> list:
        orders: list = []

        # ── Exits: bounce or 3-day time stop, daily ────────────────
        for sym, pos in book.positions.items():
            if pos.bars_held >= self.max_hold_days:
                orders.append(ExitOrder(
                    symbol=sym,
                    reason=f"time_stop_{self.max_hold_days}d"))
                continue
            h = view.history(sym)
            if h.empty:
                continue
            close_t = float(h["close"].iloc[-1])
            if close_t > pos.entry_price:
                orders.append(ExitOrder(symbol=sym, reason="bounce_above_entry"))

        # ── Entries: weekly cadence ────────────────────────────────
        if not is_iso_week_boundary(view):
            return orders

        for sym in view.symbols():
            if sym.startswith("^"):
                continue
            if book.has_position(sym):
                continue
            h = view.history(sym)
            if len(h) < self.trend_ma + 2:
                continue
            open_t = float(h["open"].iloc[-1])
            close_t = float(h["close"].iloc[-1])
            close_prev = float(h["close"].iloc[-2])
            if close_prev <= 0:
                continue
            # Gap-down filter
            if not (open_t / close_prev < (1.0 - self.gap_threshold)):
                continue
            # Trend filter (no falling knives)
            ma = float(h["close"].iloc[-self.trend_ma:].mean())
            if not (close_t > ma):
                continue

            order = make_enter_with_atr_stop(
                view, sym, atr_period=self.atr_period,
                reason=f"gap_down>{self.gap_threshold:.0%}+close>{self.trend_ma}dma")
            if order is not None:
                orders.append(order)

        return orders
