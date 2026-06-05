"""WEEKLY-SWEEP #5 — Weekly sector rotation.

ENTRY (weekly):
    1. Compute 63-day return for every eligible symbol.
    2. Aggregate to sector means (using ``SECTORS`` mapping).
    3. Rank sectors descending by mean 63-day return; take top
       ``top_n_sectors`` (= 3).
    4. From the top sectors, pick the highest-return members until we
       hit ``top_n`` (= 15) total picks. Distribute slots across
       sectors as evenly as possible (~5 per sector for 15/3).
EXIT (weekly):
    Rotation — sell anything not in the new top_set.

Source: classic top-down sector momentum, conceptually analogous to
the "GTAA" tactical asset allocation family. 63d look-back + top-3
sectors + 15-name basket are calendar-aligned defaults, NOT tuned.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtesting.replay import BarView, Book, EnterOrder, ExitOrder
from config import ATR_PERIOD, MOM_TOP_N
from data.universe import SECTORS
from signals.weekly._common import (
    is_iso_week_boundary, make_enter_with_atr_stop,
)


@dataclass
class WeeklySectorRotation:
    lookback_days: int = 63
    top_n_sectors: int = 3
    top_n: int = MOM_TOP_N
    atr_period: int = ATR_PERIOD

    def decide(self, view: BarView, book: Book) -> list:
        if not is_iso_week_boundary(view):
            return []

        needed = self.lookback_days + 1
        sym_data: list[tuple[str, float, str]] = []
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
            ret = end_price / start_price - 1.0
            sec = SECTORS.get(sym, sym)
            sym_data.append((sym, ret, sec))

        if not sym_data:
            return []

        # Sector means + per-sector member ranks.
        sector_returns: dict[str, list[float]] = {}
        sector_members: dict[str, list[tuple[str, float]]] = {}
        for sym, ret, sec in sym_data:
            sector_returns.setdefault(sec, []).append(ret)
            sector_members.setdefault(sec, []).append((sym, ret))
        sector_mean = {sec: sum(rs) / len(rs)
                        for sec, rs in sector_returns.items()}

        top_sectors = sorted(sector_mean,
                              key=lambda s: sector_mean[s],
                              reverse=True)[:self.top_n_sectors]

        # Distribute the ``top_n`` slots across sectors as evenly as
        # possible. With top_n=15 and top_n_sectors=3 -> 5 each.
        per_sector = max(1, self.top_n // self.top_n_sectors)
        top_set: set[str] = set()
        for sec in top_sectors:
            members = sorted(sector_members[sec],
                              key=lambda x: x[1], reverse=True)
            top_set.update(s for s, _ in members[:per_sector])

        orders: list = []
        # Exits
        for sym in book.open_symbols():
            if sym in top_set:
                continue
            orders.append(ExitOrder(symbol=sym, reason="sector_rotation_out"))
        # Entries — emit in member-rank order within each sector by
        # iterating top_sectors first.
        emitted = set()
        for sec in top_sectors:
            members = sorted(sector_members[sec],
                              key=lambda x: x[1], reverse=True)
            for sym, _ in members[: per_sector]:
                if sym in emitted or sym in book.positions:
                    continue
                emitted.add(sym)
                order = make_enter_with_atr_stop(
                    view, sym, atr_period=self.atr_period,
                    reason=f"sector_top{self.top_n_sectors}+{sec}")
                if order is not None:
                    orders.append(order)
        return orders
