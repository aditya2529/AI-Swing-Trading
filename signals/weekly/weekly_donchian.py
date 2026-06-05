"""WEEKLY-SWEEP #3 — Weekly Donchian breakout.

ENTRY (weekly):
    close > prior-20-day high AND volume > 1.5 x 20-day mean volume
EXIT (daily):
    Chandelier trail = highest_high since entry - 3 x ATR(14)
    Time stop at 10 bars held.

Source: classic turtle/Donchian breakout, volume-confirmed.
20-day lookback + 3-ATR chandelier + 10-day time stop are the
project's standard breakout defaults (config.BREAKOUT_LOOKBACK,
CHANDELIER_ATR_MULT, MAX_HOLD_DAYS). NOT tuned for this experiment.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtesting.replay import BarView, Book, EnterOrder, ExitOrder
from config import (
    ATR_PERIOD, BREAKOUT_LOOKBACK, CHANDELIER_ATR_MULT, MAX_HOLD_DAYS,
    VOLUME_AVG_WINDOW, VOLUME_MULT,
)
from features.engineer import compute_atr
from signals.weekly._common import (
    is_iso_week_boundary, make_enter_with_atr_stop,
)


@dataclass
class WeeklyDonchian:
    lookback: int = BREAKOUT_LOOKBACK
    vol_window: int = VOLUME_AVG_WINDOW
    vol_mult: float = VOLUME_MULT
    chandelier_mult: float = CHANDELIER_ATR_MULT
    max_hold_days: int = MAX_HOLD_DAYS
    atr_period: int = ATR_PERIOD

    def decide(self, view: BarView, book: Book) -> list:
        orders: list = []

        # ── Exits: chandelier trail + time stop, daily ─────────────
        for sym, pos in book.positions.items():
            if pos.bars_held >= self.max_hold_days:
                orders.append(ExitOrder(
                    symbol=sym,
                    reason=f"time_stop_{self.max_hold_days}d"))
                continue
            h = view.history(sym)
            if len(h) < self.atr_period * 2:
                continue
            atr_series = compute_atr(h, period=self.atr_period)
            if atr_series.empty:
                continue
            atr_t = float(atr_series.iloc[-1])
            if not pd.notna(atr_t) or atr_t <= 0:
                continue
            close_t = float(h["close"].iloc[-1])
            trail = pos.highest_high - self.chandelier_mult * atr_t
            if close_t < trail:
                orders.append(ExitOrder(symbol=sym, reason="chandelier"))

        # ── Entries: weekly cadence ────────────────────────────────
        if not is_iso_week_boundary(view):
            return orders

        for sym in view.symbols():
            if sym.startswith("^"):
                continue
            if book.has_position(sym):
                continue
            h = view.history(sym)
            needed = max(self.lookback, self.vol_window,
                          self.atr_period) + 1
            if len(h) < needed:
                continue

            close_t = float(h["close"].iloc[-1])
            vol_t = float(h["volume"].iloc[-1])
            prior_window = h.iloc[-(self.lookback + 1):-1]
            breakout_level = float(prior_window["high"].max())
            if not (close_t > breakout_level):
                continue
            vol_prior = h["volume"].iloc[-(self.vol_window + 1):-1]
            vol_avg = float(vol_prior.mean())
            if vol_avg <= 0 or not (vol_t > self.vol_mult * vol_avg):
                continue

            order = make_enter_with_atr_stop(
                view, sym, atr_period=self.atr_period,
                reason=f"donchian{self.lookback}+vol{self.vol_mult}x")
            if order is not None:
                orders.append(order)

        return orders
