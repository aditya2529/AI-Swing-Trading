"""Test-only fixtures for the replay harness: synthetic bar builders and
deterministic strategy doubles.

These are NOT the production strategy (signals/breakout.py does not exist
yet — it is gated on the look-ahead test passing). They exist solely to
exercise the harness's causal/execution machinery from the tests.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtesting.replay import EnterOrder, ExitOrder


# ── Synthetic daily-bar builders ────────────────────────────────────────

def make_frame(closes, *, gap: float = 0.002, start: str = "2020-01-06",
               volume: float = 100_000.0) -> pd.DataFrame:
    """Build a daily OHLCV frame from a close series.

    A deterministic overnight ``gap`` makes ``open[T+1] != close[T]`` — it
    mirrors the real NSE daily convention we verified (genuine overnight
    gaps), so a fill at ``open[T+1]`` is always distinguishable from the
    decision-day ``close[T]``. That distinction is what the look-ahead
    tests rely on.
    """
    closes = np.asarray(closes, dtype=float)
    idx = pd.bdate_range(start, periods=len(closes))
    close = pd.Series(closes, index=idx)
    prev = close.shift(1)
    openp = (prev * (1.0 + gap))
    openp.iloc[0] = close.iloc[0]
    hi = pd.concat([openp, close], axis=1).max(axis=1) * 1.005
    lo = pd.concat([openp, close], axis=1).min(axis=1) * 0.995
    return pd.DataFrame({"open": openp, "high": hi, "low": lo, "close": close,
                         "volume": pd.Series(volume, index=idx)})


def rising(n: int = 80, rate: float = 0.01, base: float = 100.0):
    return base * (1.0 + rate) ** np.arange(n)


def random_walk(n: int = 160, seed: int = 7, base: float = 100.0, vol: float = 0.02):
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0, vol, n)
    return base * np.cumprod(1.0 + steps)


# ── Deterministic strategy doubles ──────────────────────────────────────

class FixedHoldStrategy:
    """Enter each symbol when flat; exit after exactly ``hold`` bars. Stop
    placed ``stop_frac`` below the last close (wide, so sizing succeeds)."""

    def __init__(self, symbols, hold: int = 3, stop_frac: float = 0.5):
        self.symbols = list(symbols)
        self.hold = hold
        self.stop_frac = stop_frac

    def decide(self, view, book):
        orders = []
        for s in self.symbols:
            if not view.has_bar(s):
                continue
            if book.has_position(s):
                if book.positions[s].bars_held >= self.hold:
                    orders.append(ExitOrder(s, reason="time"))
            else:
                last = view.latest(s)
                if last is not None:
                    stop = float(last["close"]) * (1.0 - self.stop_frac)
                    orders.append(EnterOrder(s, stop=stop, reason="test"))
        return orders


class BreakoutDouble:
    """Enter when today's close is the max close over the last ``lookback``
    visible bars; exit after ``hold`` bars. Purely a function of the causal
    view + neutral position stats — ideal for the future-mutation test."""

    def __init__(self, symbol: str, lookback: int = 20, hold: int = 5,
                 stop_frac: float = 0.08):
        self.symbol = symbol
        self.lookback = lookback
        self.hold = hold
        self.stop_frac = stop_frac

    def decide(self, view, book):
        s = self.symbol
        if not view.has_bar(s):
            return []
        h = view.history(s)
        if book.has_position(s):
            if book.positions[s].bars_held >= self.hold:
                return [ExitOrder(s, reason="time")]
            return []
        w = h["close"].tail(self.lookback)
        if len(w) >= self.lookback and h["close"].iloc[-1] >= w.max():
            stop = float(h["close"].iloc[-1]) * (1.0 - self.stop_frac)
            return [EnterOrder(s, stop=stop, reason="breakout")]
        return []


class OracleProbe:
    """A strategy that TRIES to cheat — it would love tomorrow's bar. It can
    only ever obtain ``view.history()`` (≤ cutoff), so it records the worst
    case: how many bars beyond the cutoff it ever managed to see. A correct
    harness keeps that at zero forever."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.max_seen_beyond_cutoff = 0

    def decide(self, view, book):
        s = self.symbol
        if not view.has_bar(s):
            return []
        h = view.history(s)
        beyond = int((h.index > view.cutoff).sum())
        self.max_seen_beyond_cutoff = max(self.max_seen_beyond_cutoff, beyond)
        if book.has_position(s):
            return [ExitOrder(s, reason="probe")]
        if len(h) >= 2 and h["close"].iloc[-1] > h["close"].iloc[-2]:
            return [EnterOrder(s, stop=float(h["close"].iloc[-1]) * 0.9)]
        return []
