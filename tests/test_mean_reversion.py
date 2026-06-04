"""Unit tests for the mean-reversion swing strategy.

One test per rule the MR-1 ticket called out:
  1. Uptrend filter blocks names whose close is BELOW the trend MA
     (no falling-knife dip-buys).
  2. RSI < MR_RSI_OVERSOLD is REQUIRED (sufficiently oversold).
  3. RSI >= MR_RSI_OVERSOLD blocks entry (not oversold enough).
  4. Bounce exit fires when RSI > MR_RSI_EXIT.
  5. Time stop fires at MR_MAX_HOLD_DAYS.
  6. Hard stop fires on close < position.stop.
  7. Strategy never reads beyond cutoff (end-to-end future-mutation check
     through the real MeanReversionStrategy — same shape as the gate's
     own canonical leak detector, but exercised through THIS strategy).
"""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from backtesting.replay import (
    BarView, Book, EnterOrder, ExitOrder, Position, run_replay,
)
from config import (
    ATR_SL_MULTIPLIER, MR_MAX_HOLD_DAYS, MR_RSI_EXIT, MR_RSI_OVERSOLD,
    MR_TREND_MA,
)
from signals.mean_reversion import MeanReversionStrategy


# ── Fixture helpers ─────────────────────────────────────────────────────


def _make_bars(closes, *, highs=None, lows=None, opens=None, volumes=None,
                start="2019-01-01") -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    idx = pd.date_range(start, periods=n, freq="B")
    if opens is None:
        opens = closes.copy()
    if highs is None:
        highs = closes + 0.2
    if lows is None:
        lows = closes - 0.2
    if volumes is None:
        volumes = np.full(n, 1_000_000.0)
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
    }, index=idx)


def _uptrend_with_dip(*, n: int = 260, base: float = 100.0,
                       drift: float = 0.003, dip_depth: float = 0.18,
                       dip_window: int = 7, seed: int = 11) -> pd.DataFrame:
    """Long uptrend + a sharp recent dip that pushes RSI below 30 while
    keeping close > 200-day MA. The last bar is the dip's bottom — i.e.
    the candidate decision day.

    Calibration of ``drift`` (the key parameter):
        We need close[T] > MA[T] AT the dip bottom (uptrend filter) AND
        RSI[T] < 30 (oversold filter). With ``dip_depth = 0.18``:
            close[T] = peak × 0.82
            MA[T]    ≈ peak / exp(drift × 100)   (mid-window approx.)
        Inequality requires  drift > -ln(0.82) / 100 ≈ 0.00198.
        Using 0.003 gives a comfortable margin against noise while
        keeping the price path realistic.
    """
    rng = np.random.default_rng(seed)
    n_pre = n - dip_window
    # Tighter noise so the trend dominates the fixture's behaviour.
    pre = base * np.exp(np.cumsum(rng.normal(drift, 0.005, n_pre)))
    peak = pre[-1]
    floor = peak * (1.0 - dip_depth)
    # Smooth linear fall from peak -> floor over dip_window bars.
    dip = np.linspace(peak, floor, dip_window + 1)[1:]
    closes = np.concatenate([pre, dip])
    # Modest H-L band for tight ATR (matches a real-world dip — narrow
    # ranges as the move plays out).
    band = closes * 0.005
    highs = closes + band
    lows = closes - band
    return _make_bars(closes, highs=highs, lows=lows,
                      volumes=rng.uniform(8e5, 1.2e6, n))


def _downtrend(n: int = 260, base: float = 100.0,
                drift: float = -0.0008, seed: int = 17) -> pd.DataFrame:
    """A persistent downtrend — close stays BELOW the 200-day MA.
    Used to verify the uptrend filter blocks falling-knife dip-buys."""
    rng = np.random.default_rng(seed)
    closes = base * np.exp(np.cumsum(rng.normal(drift, 0.012, n)))
    return _make_bars(closes, volumes=rng.uniform(8e5, 1.2e6, n))


def _build_view_book(symbol_df: pd.DataFrame,
                      *, book: Book | None = None) -> tuple[BarView, Book]:
    """Construct a BarView at the last bar of ``symbol_df``. No ^NSEI is
    needed — MR has no market regime gate (see signals/mean_reversion.py
    docstring), so this fixture stays minimal."""
    data = {"X": symbol_df}
    cutoff = symbol_df.index[-1]
    view = BarView(data, cutoff=cutoff)
    if book is None:
        book = Book(cash=500_000.0, equity=500_000.0, positions={})
    return view, book


# ── 1. Uptrend filter blocks downtrend names ────────────────────────────


def test_uptrend_filter_blocks_downtrend():
    """Even with RSI well below the oversold threshold, the strategy
    must NOT enter if close[T] is below the trend MA — that is the
    falling-knife setup the filter exists to block."""
    sym = _downtrend()
    view, book = _build_view_book(sym)
    orders = MeanReversionStrategy().decide(view, book)
    assert not any(isinstance(o, EnterOrder) for o in orders), (
        f"Strategy entered a name whose close is BELOW its "
        f"{MR_TREND_MA}-day MA — falling-knife filter is broken."
    )


# ── 2. RSI < threshold is REQUIRED ──────────────────────────────────────


def test_rsi_oversold_triggers_entry():
    """The canonical setup: a long uptrend + sharp recent dip that
    pushes RSI below the threshold while keeping the trend filter
    happy. The strategy SHOULD enter."""
    sym = _uptrend_with_dip()
    view, book = _build_view_book(sym)
    orders = MeanReversionStrategy().decide(view, book)
    enter = [o for o in orders if isinstance(o, EnterOrder)]
    assert len(enter) == 1 and enter[0].symbol == "X", (
        f"Expected exactly one EnterOrder for X (uptrend + RSI < "
        f"{MR_RSI_OVERSOLD}). Got: {orders!r}.")


# ── 3. RSI above threshold blocks entry ─────────────────────────────────


def test_rsi_above_threshold_blocks_entry():
    """A name in a steady uptrend without a deep dip — RSI sits in the
    middle of its range, well above the oversold threshold. No entry."""
    rng = np.random.default_rng(23)
    n = 260
    # Smooth uptrend — no dip. RSI hovers around 50-60.
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0.0010, 0.006, n)))
    sym = _make_bars(closes)
    view, book = _build_view_book(sym)
    orders = MeanReversionStrategy().decide(view, book)
    assert not any(isinstance(o, EnterOrder) for o in orders), (
        "Strategy entered a name without RSI being below the oversold "
        "threshold — the RSI filter is broken or too permissive.")


# ── 4. Bounce exit fires when RSI > exit threshold ──────────────────────


def test_bounce_exit_fires_on_rsi_above_threshold():
    """Hold a position whose RSI[T] is above MR_RSI_EXIT — the bounce
    exit should fire. Use a steady-uptrend fixture (no dip) so RSI is
    well above the exit threshold at T."""
    rng = np.random.default_rng(31)
    closes = 100.0 * np.cumprod(1.0 + rng.normal(0.005, 0.005, 60))
    sym = _make_bars(closes)
    # Open position from 5 bars ago, with a stop well below today's close
    # so the hard-stop doesn't fire and the bounce check is what runs.
    close_t = float(sym["close"].iloc[-1])
    entry_idx = sym.index[-5]
    entry_price = float(sym["close"].iloc[-5])
    pos = Position(
        symbol="X", entry_date=entry_idx, entry_price=entry_price,
        shares=100, stop=entry_price * 0.85, risk_per_share=entry_price * 0.15,
        cost_basis=entry_price * 100, bars_held=5,
        highest_high=close_t, highest_close=close_t)
    book = Book(cash=400_000.0, equity=410_000.0, positions={"X": pos})
    view, _ = _build_view_book(sym, book=book)

    orders = MeanReversionStrategy().decide(view, book)
    exit_orders = [o for o in orders if isinstance(o, ExitOrder)]
    assert any(o.symbol == "X" and "bounce" in o.reason
                for o in exit_orders), (
        f"Expected a bounce ExitOrder when RSI[T] > {MR_RSI_EXIT}. "
        f"Got: {orders!r}.")


# ── 5. Time stop fires at max hold days ─────────────────────────────────


def test_time_stop_fires_at_max_hold_days():
    """A position whose bars_held has reached the configured cap must
    receive an ExitOrder even if no other condition triggers."""
    sym = _uptrend_with_dip()
    close_t = float(sym["close"].iloc[-1])
    entry_idx = sym.index[-15]
    pos = Position(
        symbol="X", entry_date=entry_idx, entry_price=close_t * 1.05,
        shares=100, stop=close_t * 0.85, risk_per_share=close_t * 0.2,
        cost_basis=close_t * 105, bars_held=MR_MAX_HOLD_DAYS,
        highest_high=close_t * 1.10, highest_close=close_t)
    book = Book(cash=400_000.0, equity=410_000.0, positions={"X": pos})
    view, _ = _build_view_book(sym, book=book)

    orders = MeanReversionStrategy().decide(view, book)
    exit_orders = [o for o in orders if isinstance(o, ExitOrder)]
    assert any(o.symbol == "X" and "time_stop" in o.reason
                for o in exit_orders), (
        f"Expected a time-stop ExitOrder at bars_held == "
        f"MR_MAX_HOLD_DAYS ({MR_MAX_HOLD_DAYS}). Got: {orders!r}.")


# ── 6. Hard stop fires on close below position.stop ─────────────────────


def test_hard_stop_fires_on_close_below_stop():
    """close[T] < pos.stop must trigger an exit, regardless of RSI or
    time-stop state. The harness fills the exit at T+1 open per the
    no-intrabar-fill execution model."""
    sym = _uptrend_with_dip()
    close_t = float(sym["close"].iloc[-1])
    # Stop is ABOVE close[T] — the position is underwater.
    entry_idx = sym.index[-3]
    pos = Position(
        symbol="X", entry_date=entry_idx, entry_price=close_t * 1.20,
        shares=100, stop=close_t * 1.05,
        risk_per_share=close_t * 0.15, cost_basis=close_t * 120,
        bars_held=3, highest_high=close_t * 1.20, highest_close=close_t * 1.18)
    book = Book(cash=400_000.0, equity=410_000.0, positions={"X": pos})
    view, _ = _build_view_book(sym, book=book)

    orders = MeanReversionStrategy().decide(view, book)
    exit_orders = [o for o in orders if isinstance(o, ExitOrder)]
    assert any(o.symbol == "X" and "hard_stop" in o.reason
                for o in exit_orders), (
        f"Expected a hard_stop ExitOrder when close[T]={close_t:.2f} "
        f"< pos.stop={pos.stop:.2f}. Got: {orders!r}.")


# ── 7. End-to-end no-leak check ─────────────────────────────────────────


def test_strategy_decisions_invariant_to_future_mutation():
    """Mutate bars STRICTLY AFTER a cut date and verify that no decision
    on bars <= cut changes. Random-data replacement (not a multiplicative
    scaling) because mean-reversion's filters are scale-invariant under
    a single global multiplier, which would make the gate-style mutation
    pattern vacuous. Sanity check: base must emit at least 2 non-empty
    decisions <= cut so the leak-check has teeth."""
    # 280-bar fixture: strong uptrend through day ~252, then a 7-bar
    # dip (~18%), then a recovery. The drift is calibrated so close at
    # the dip bottom stays above the 200-day MA (same calibration as
    # _uptrend_with_dip; see that helper's docstring).
    rng = np.random.default_rng(41)
    n_pre = 253
    pre = 100.0 * np.exp(np.cumsum(rng.normal(0.003, 0.005, n_pre)))
    dip = np.linspace(pre[-1], pre[-1] * 0.82, 8)[1:]
    n_post = 280 - n_pre - len(dip)
    post = dip[-1] * np.exp(np.cumsum(rng.normal(0.002, 0.010, n_post)))
    closes = np.concatenate([pre, dip, post])
    band = closes * 0.005
    sym = _make_bars(closes, highs=closes + band, lows=closes - band,
                      volumes=rng.uniform(8e5, 1.2e6, len(closes)))

    data = {"X": sym}
    base = run_replay(copy.deepcopy(data), MeanReversionStrategy(),
                       record_decisions=True)

    cut = sym.index[265]
    mutated = copy.deepcopy(data)
    future = mutated["X"].index > cut
    fut_n = int(future.sum())
    rng2 = np.random.default_rng(797)
    new_closes = 150.0 * np.exp(np.cumsum(rng2.normal(0.001, 0.025, fut_n)))
    mutated["X"].loc[future, "open"] = new_closes
    mutated["X"].loc[future, "high"] = new_closes * 1.01
    mutated["X"].loc[future, "low"] = new_closes * 0.99
    mutated["X"].loc[future, "close"] = new_closes
    mutated["X"].loc[future, "volume"] = rng2.uniform(5e5, 5e6, fut_n)
    after = run_replay(mutated, MeanReversionStrategy(), record_decisions=True)

    # Sanity FIRST: strategy must actually fire on the base fixture.
    base_non_empty_le_cut = [d for d in base["decisions"]
                              if d[0] <= cut and d[1]]
    assert len(base_non_empty_le_cut) >= 2, (
        f"Fixture is vacuous — strategy emitted only "
        f"{len(base_non_empty_le_cut)} non-empty decisions <= cut. "
        f"Adjust the fixture so the engineered dip actually triggers.")

    base_le_cut = [d for d in base["decisions"] if d[0] <= cut]
    after_le_cut = [d for d in after["decisions"] if d[0] <= cut]
    assert base_le_cut == after_le_cut, (
        "MeanReversionStrategy decisions on days <= cut changed when "
        "future bars were mutated — a look-ahead leak exists in the "
        "strategy or in something it transitively reads.")
