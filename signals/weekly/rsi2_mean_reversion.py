"""WEEKLY-SWEEP #1 — RSI(2) mean-reversion (Larry Connors).

ENTRY (weekly):
    close > MA(200) AND RSI(2) < 10
EXIT (daily):
    RSI(2) > 60 OR bars_held >= 5

Source: Connors & Alvarez (2008), *Short Term Trading Strategies That Work*.
Parameter values are the BOOK's published defaults — pre-registered,
NOT tuned for this project's data.
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
class RSI2MeanReversion:
    trend_ma: int = 200
    rsi_period: int = 2
    rsi_oversold: float = 10.0
    rsi_exit: float = 60.0
    max_hold_days: int = 5
    atr_period: int = ATR_PERIOD

    def decide(self, view: BarView, book: Book) -> list:
        orders: list = []

        # ── Exits: signal-based, evaluated DAILY ───────────────────
        for sym, pos in book.positions.items():
            if pos.bars_held >= self.max_hold_days:
                orders.append(ExitOrder(
                    symbol=sym,
                    reason=f"time_stop_{self.max_hold_days}d"))
                continue
            h = view.history(sym)
            if len(h) < self.rsi_period * 4:
                continue
            rsi = compute_rsi(h["close"], period=self.rsi_period)
            if rsi.empty:
                continue
            rsi_t = float(rsi.iloc[-1])
            if pd.notna(rsi_t) and rsi_t > self.rsi_exit:
                orders.append(ExitOrder(
                    symbol=sym, reason=f"rsi2>{self.rsi_exit:.0f}"))

        # ── Entries: weekly cadence ────────────────────────────────
        if not is_iso_week_boundary(view):
            return orders

        for sym in view.symbols():
            if sym.startswith("^"):
                continue
            if book.has_position(sym):
                continue
            h = view.history(sym)
            needed = self.trend_ma + self.rsi_period * 4 + 1
            if len(h) < needed:
                continue

            close_t = float(h["close"].iloc[-1])
            ma = float(h["close"].iloc[-self.trend_ma:].mean())
            if not (close_t > ma):
                continue

            rsi = compute_rsi(h["close"], period=self.rsi_period)
            if rsi.empty:
                continue
            rsi_t = float(rsi.iloc[-1])
            if not pd.notna(rsi_t) or not (rsi_t < self.rsi_oversold):
                continue

            order = make_enter_with_atr_stop(
                view, sym, atr_period=self.atr_period,
                reason=f"rsi2<{self.rsi_oversold:.0f}+close>{self.trend_ma}dma")
            if order is not None:
                orders.append(order)

        return orders
