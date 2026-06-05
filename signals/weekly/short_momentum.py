"""WEEKLY-SWEEP #2 — Short-lookback (63d) cross-sectional momentum.

ENTRY (weekly):
    Rank universe by 63-day total return; keep top_n.
EXIT (weekly):
    Rotation — any held name not in the new top_n is sold.

Source: Carhart (1997) / Asness (2014) variants — short-lookback
momentum is well-documented. 63 trading days = ~1 calendar quarter,
the standard short-momentum window. NOT tuned.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtesting.replay import BarView, Book, EnterOrder, ExitOrder
from config import ATR_PERIOD, MOM_TOP_N
from signals.weekly._common import (
    is_iso_week_boundary, make_enter_with_atr_stop,
)


@dataclass
class ShortMomentum:
    lookback_days: int = 63
    top_n: int = MOM_TOP_N
    atr_period: int = ATR_PERIOD

    def decide(self, view: BarView, book: Book) -> list:
        if not is_iso_week_boundary(view):
            return []

        # ── Score every eligible symbol ─────────────────────────────
        scores: dict[str, float] = {}
        needed = self.lookback_days + 1
        for sym in view.symbols():
            if sym.startswith("^"):
                continue
            h = view.history(sym)
            if len(h) < needed:
                continue
            end_price = float(h["close"].iloc[-1])
            start_price = float(h["close"].iloc[-self.lookback_days - 1])
            if start_price <= 0 or not pd.notna(start_price):
                continue
            if not pd.notna(end_price):
                continue
            scores[sym] = end_price / start_price - 1.0

        if not scores:
            return []

        ranked = sorted(scores, key=lambda s: scores[s], reverse=True)
        top_set = set(ranked[:self.top_n])

        orders: list = []
        # Exits
        for sym in book.open_symbols():
            if sym in top_set:
                continue
            if sym in scores:
                orders.append(ExitOrder(symbol=sym, reason="rotation_out"))
            else:
                orders.append(ExitOrder(
                    symbol=sym, reason="rotation_out_ineligible"))
        # Entries (rank-ordered)
        for sym in ranked[:self.top_n]:
            if book.has_position(sym):
                continue
            order = make_enter_with_atr_stop(
                view, sym, atr_period=self.atr_period,
                reason=f"mom_{self.lookback_days}d_top{self.top_n}")
            if order is not None:
                orders.append(order)
        return orders
