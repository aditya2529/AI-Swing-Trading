"""Shared helpers for the WEEKLY-SWEEP strategies.

All six candidates fire ENTRIES only on the first trading day of each
ISO week (the canonical weekly cadence). EXITS depend on the
strategy: signal-based exits (RSI bounce, chandelier trail, time stop,
bounce-above-entry) fire daily as their condition triggers; rotational
exits (top-N rebalance, sector rotation) fire weekly with the entries.

The helpers here are causal — they read only the harness-clipped
``view.history(sym)`` slice. End-to-end no-leak is verified per
strategy by future-mutation invariance tests.
"""
from __future__ import annotations

import pandas as pd

from backtesting.replay import BarView, EnterOrder
from config import ATR_PERIOD
from features.engineer import compute_atr
from signals.risk import initial_stop


def is_iso_week_boundary(view: BarView) -> bool:
    """True iff the cutoff bar is in a different ISO week than the
    immediately-prior causal bar. Stateless / data-derived.
    Same pattern SmidMomentumStrategy uses for its 'weekly' freq.
    """
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


def make_enter_with_atr_stop(view: BarView, sym: str, *,
                               atr_period: int = ATR_PERIOD,
                               reason: str = "") -> EnterOrder | None:
    """Build an EnterOrder with the standard ATR-based initial stop.
    Returns ``None`` on degenerate inputs (insufficient history,
    non-positive ATR, stop not below entry). Centralised here so
    every WEEKLY-SWEEP strategy uses the same sizing-relevant stop
    contract."""
    h = view.history(sym)
    if len(h) < atr_period * 2 + 1:
        return None
    close_t = float(h["close"].iloc[-1])
    if not pd.notna(close_t) or close_t <= 0:
        return None
    atr_series = compute_atr(h, period=atr_period)
    if atr_series.empty:
        return None
    atr_t = float(atr_series.iloc[-1])
    if not pd.notna(atr_t) or atr_t <= 0:
        return None
    stop = initial_stop(entry=close_t, atr=atr_t)
    if not (stop < close_t):
        return None
    return EnterOrder(symbol=sym, stop=stop, reason=reason)
