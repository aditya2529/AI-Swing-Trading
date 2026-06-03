"""Unit tests for the breakout swing strategy and its per-trade risk math.

Coverage:
  signals/risk.py:
    - initial_stop, chandelier_stop arithmetic + input validation
    - measured_move_target geometry + degenerate-channel rejection
    - rr_screen pass/fail + invalid-input rejection

  signals/breakout.py — one test per rule the brief calls out:
    1. Donchian break required (no break -> no order)
    2. Lookback EXCLUDES day T (T's own high cannot satisfy the break)
    3. Low-volume breakout rejected
    4. Regime OFF blocks all entries
    5. Chandelier exit triggers on close below trail
    6. Time stop fires at MAX_HOLD_DAYS
    7. R:R screen blocks shallow channels (measured-move too close)
    8. End-to-end no-leak: future-bar mutation does not change past
       decisions (same idea as the gate test, but exercised through the
       real BreakoutStrategy not a double).
"""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from backtesting.replay import BarView, Book, EnterOrder, ExitOrder, run_replay
from config import ATR_SL_MULTIPLIER, CHANDELIER_ATR_MULT, MIN_RR
from signals.breakout import BreakoutStrategy
from signals.risk import (
    chandelier_stop, initial_stop, measured_move_target, rr_screen,
)


# ── Fixture helpers ─────────────────────────────────────────────────────


def _make_bars(closes, *, highs=None, lows=None, opens=None, volumes=None,
                start="2020-01-01") -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    idx = pd.date_range(start, periods=n, freq="B")  # business-day grid
    if opens is None:
        opens = closes.copy()
    if highs is None:
        highs = closes + 0.1
    if lows is None:
        lows = closes - 0.1
    if volumes is None:
        volumes = np.full(n, 1_000_000.0)
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
    }, index=idx)


def _trending_nsei(n: int, start_close=15_000.0, drift=0.001) -> pd.DataFrame:
    """A NIFTY proxy that drifts up so the 50-DMA regime is ON for most of
    the series. Used by every test that wants regime ON; tests that want
    regime OFF use ``_falling_nsei`` instead."""
    closes = start_close * np.exp(np.cumsum(np.full(n, drift)))
    return _make_bars(closes)


def _falling_nsei(n: int, start_close=15_000.0, drift=-0.001) -> pd.DataFrame:
    """A NIFTY proxy that drifts DOWN — close < 50-DMA from day ~50 onward.

    The strategy reads ``len(h) < regime_ma`` as ``regime OFF`` (a safe
    default), so the very first 50 bars are also regime OFF, which is
    what we want for `test_regime_off_blocks_all_entries`."""
    closes = start_close * np.exp(np.cumsum(np.full(n, drift)))
    return _make_bars(closes)


# A symbol fixture with a configurable breakout on the LAST bar.
def _symbol_with_breakout(
    *, n=80, range_low=100.0, range_high=110.0, break_close=115.0,
    break_high=None, break_volume_mult=3.0, base_volume=1_000_000.0,
) -> pd.DataFrame:
    """Build a symbol DataFrame whose final bar is a clean breakout.

    Bars 0..n-2 oscillate in [range_low, range_high] with normal volume;
    bar n-1 closes at ``break_close`` (above range_high), and posts
    ``break_volume_mult × base_volume`` volume."""
    rng = np.random.default_rng(7)
    # Sample closes inside a buffered interior of the range so daily
    # noise doesn't accidentally exceed range_high; on narrow channels
    # (the shallow-RR test) the buffer collapses to a small fraction.
    width = range_high - range_low
    buffer = min(1.0, max(0.1, width * 0.1))
    closes = rng.uniform(range_low + buffer, range_high - buffer, size=n - 1)
    daily_noise = min(0.5, max(0.05, width * 0.05))
    highs = closes + rng.uniform(daily_noise * 0.2, daily_noise, size=n - 1)
    lows = closes - rng.uniform(daily_noise * 0.2, daily_noise, size=n - 1)
    # Pin one prior high to range_high and one prior low to range_low so
    # the breakout level and channel low are exactly the named values.
    highs[-1] = range_high
    lows[0] = range_low
    # Bar T — the breakout.
    closes = np.append(closes, break_close)
    if break_high is None:
        break_high = break_close + 0.5
    highs = np.append(highs, break_high)
    lows = np.append(lows, break_close - daily_noise)
    vols = np.append(
        np.full(n - 1, base_volume),
        break_volume_mult * base_volume,
    )
    return _make_bars(closes, highs=highs, lows=lows, volumes=vols)


def _build_view_book(symbol_df: pd.DataFrame, nsei_df: pd.DataFrame,
                      *, book: Book | None = None) -> tuple[BarView, Book]:
    data = {"X": symbol_df, "^NSEI": nsei_df}
    cutoff = symbol_df.index[-1]
    view = BarView(data, cutoff=cutoff)
    if book is None:
        book = Book(cash=500_000.0, equity=500_000.0, positions={})
    return view, book


# ── signals/risk.py ─────────────────────────────────────────────────────


def test_initial_stop_subtracts_atr_multiplier():
    assert initial_stop(entry=100.0, atr=2.0) == pytest.approx(
        100.0 - ATR_SL_MULTIPLIER * 2.0)


def test_initial_stop_rejects_nonpositive_atr():
    with pytest.raises(ValueError):
        initial_stop(entry=100.0, atr=0.0)


def test_chandelier_stop_subtracts_chandelier_multiplier():
    # Highest high since entry = 130; ATR = 4 -> trail = 130 - 3*4 = 118
    assert chandelier_stop(
        highest_high_since_entry=130.0, atr=4.0
    ) == pytest.approx(130.0 - CHANDELIER_ATR_MULT * 4.0)


def test_chandelier_stop_rejects_nonpositive_atr():
    with pytest.raises(ValueError):
        chandelier_stop(highest_high_since_entry=130.0, atr=-1.0)


def test_measured_move_target_projects_channel():
    # breakout=110, recent_low=100 -> target = 2*110 - 100 = 120
    assert measured_move_target(breakout_level=110.0, recent_low=100.0) == 120.0


def test_measured_move_target_rejects_degenerate_channel():
    with pytest.raises(ValueError):
        measured_move_target(breakout_level=100.0, recent_low=100.0)


def test_rr_screen_passes_when_rr_meets_min():
    # entry 100, stop 95 (risk=5), target 100 + MIN_RR*5 = 110 (for MIN_RR=2)
    assert rr_screen(entry=100.0, stop=95.0,
                      target=100.0 + MIN_RR * 5.0) is True


def test_rr_screen_fails_when_rr_below_min():
    # entry 100, stop 95 (risk=5), target only 109 -> RR 1.8 < 2.0
    assert rr_screen(entry=100.0, stop=95.0,
                      target=100.0 + (MIN_RR - 0.2) * 5.0) is False


def test_rr_screen_rejects_invalid_stop_at_or_above_entry():
    with pytest.raises(ValueError):
        rr_screen(entry=100.0, stop=100.0, target=120.0)


def test_rr_screen_returns_false_for_target_at_or_below_entry():
    """target <= entry is a legitimate 'no room to project' rejection,
    not a logic bug — returns False rather than raising."""
    assert rr_screen(entry=100.0, stop=95.0, target=100.0) is False
    assert rr_screen(entry=100.0, stop=95.0, target=99.0) is False


# ── signals/breakout.py — one test per rule ─────────────────────────────


# 1. Donchian break is REQUIRED. No break -> no entry.
def test_no_breakout_no_entry():
    """If close[T] does not exceed the prior-N high, no EnterOrder is emitted.
    Volume and regime are kept favorable to isolate the breakout condition."""
    sym = _symbol_with_breakout()
    # Squash the breakout: replace bar T's close so it lands AT the prior
    # high (not strictly above). Keep volume to prove the rule depends on
    # the price condition specifically.
    sym = sym.copy()
    prior_high = float(sym["high"].iloc[:-1].max())
    sym.loc[sym.index[-1], "close"] = prior_high  # exactly at, not above

    nsei = _trending_nsei(80)
    view, book = _build_view_book(sym, nsei)
    orders = BreakoutStrategy().decide(view, book)
    assert not any(isinstance(o, EnterOrder) for o in orders), (
        "Strategy entered without close[T] STRICTLY exceeding the prior-N high."
    )


# 2. Lookback must EXCLUDE day T. Otherwise T's own high trivially clears it.
def test_lookback_excludes_day_t():
    """Construct a series where the prior N-day high is well above T's
    close, but T's own high is the all-time high. A correct Donchian
    excluding-T sees no break (close[T] <= prior_high). An incorrect
    inclusive Donchian would see close[T] > -inf-like value and fire."""
    sym = _symbol_with_breakout(
        range_low=100.0, range_high=110.0, break_close=108.0,  # < prior high
        break_high=200.0,                                         # T's HIGH is huge
        break_volume_mult=5.0,
    )
    nsei = _trending_nsei(80)
    view, book = _build_view_book(sym, nsei)
    orders = BreakoutStrategy().decide(view, book)
    assert not any(isinstance(o, EnterOrder) for o in orders), (
        "Strategy fired on day-T's own high — lookback is incorrectly "
        "INCLUSIVE of day T (it must be strictly before T)."
    )


# 3. Low-volume breakout rejected.
def test_low_volume_breakout_rejected():
    sym = _symbol_with_breakout(break_volume_mult=0.8)   # below VOLUME_MULT=1.5
    nsei = _trending_nsei(80)
    view, book = _build_view_book(sym, nsei)
    orders = BreakoutStrategy().decide(view, book)
    assert not any(isinstance(o, EnterOrder) for o in orders), (
        "Low-volume breakout should be skipped — volume filter is the #1 "
        "fakeout guard."
    )


# 4. Regime OFF blocks all entries — even on a clean breakout setup.
def test_regime_off_blocks_all_entries():
    sym = _symbol_with_breakout()                # otherwise a clean setup
    nsei = _falling_nsei(80)                     # NSEI < 50-DMA -> regime OFF
    view, book = _build_view_book(sym, nsei)
    orders = BreakoutStrategy().decide(view, book)
    assert not any(isinstance(o, EnterOrder) for o in orders), (
        "Regime OFF should block all new entries. NSEI must be above its "
        "50-DMA for the strategy to enter."
    )


# 5. Chandelier exit triggers on close below the trail.
def test_chandelier_exit_triggers_on_close_below_trail():
    """Pre-populate the book with an open position whose highest_high
    sits well above today's close. With a normal ATR, chandelier_stop
    lands ABOVE close[T], so close[T] < trail and an ExitOrder fires.

    We do NOT trigger the time stop here (bars_held set low)."""
    # Build a series where ATR(14) is small (~1.0) and close[T] = 110.
    n = 60
    closes = np.full(n, 110.0) + np.random.default_rng(1).normal(0, 0.4, n)
    sym = _make_bars(closes)
    nsei = _trending_nsei(n)

    # The Position the harness would hand the strategy.
    from backtesting.replay import Position
    entry_idx = sym.index[-5]
    pos = Position(
        symbol="X", entry_date=entry_idx, entry_price=108.0,
        shares=100, stop=104.0, risk_per_share=4.0, cost_basis=10_800.0,
        bars_held=5, highest_high=130.0, highest_close=129.0,
    )
    book = Book(cash=400_000.0, equity=410_000.0,
                positions={"X": pos})
    view, _ = _build_view_book(sym, nsei, book=book)

    orders = BreakoutStrategy().decide(view, book)
    exit_orders = [o for o in orders if isinstance(o, ExitOrder)]
    assert any(o.symbol == "X" and "chandelier" in o.reason
                for o in exit_orders), (
        f"Expected a chandelier ExitOrder for X (close ~110 << trail "
        f"~ 130 - 3*ATR). Got: {orders!r}"
    )


# 6. Time stop fires at MAX_HOLD_DAYS.
def test_time_stop_fires_at_max_hold_days():
    """A position whose bars_held has reached the configured max must
    receive an ExitOrder even if no other condition triggers."""
    n = 60
    sym = _symbol_with_breakout(n=n, break_close=110.0, break_volume_mult=0.5)
    # Make sure neither the trailing nor the breakout rules muddy the
    # signal — disable trail by setting highest_high == close[T]
    # (so close >= trail and chandelier does NOT fire).
    from backtesting.replay import Position
    pos = Position(
        symbol="X", entry_date=sym.index[-15], entry_price=104.0,
        shares=100, stop=100.0, risk_per_share=4.0, cost_basis=10_400.0,
        bars_held=10,                  # == MAX_HOLD_DAYS by config default
        highest_high=float(sym["close"].iloc[-1]),
        highest_close=float(sym["close"].iloc[-1]),
    )
    book = Book(cash=400_000.0, equity=410_000.0,
                positions={"X": pos})
    nsei = _trending_nsei(n)
    view, _ = _build_view_book(sym, nsei, book=book)

    orders = BreakoutStrategy().decide(view, book)
    exit_orders = [o for o in orders if isinstance(o, ExitOrder)]
    assert any(o.symbol == "X" and "time_stop" in o.reason
                for o in exit_orders), (
        f"Expected a time-stop ExitOrder at bars_held == max_hold_days. "
        f"Got: {orders!r}"
    )


# 7. R:R screen blocks shallow channels (target too close to entry).
def test_rr_screen_blocks_shallow_channel():
    """A tight prior range puts the measured-move target only just above
    entry, so reward/risk < MIN_RR -> the entry is rejected."""
    # Range [109, 110] (1-point channel); breakout to 110.5.
    # measured_move target = 2*110 - 109 = 111  -> reward = 0.5
    # initial risk depends on ATR; with tight bars ATR is ~0.4 -> stop
    # ~ close - ATR_SL_MULT * 0.4 ~ 110.5 - 0.8 = 109.7 -> risk ~0.8.
    # RR ~ 0.5/0.8 = 0.625, well below MIN_RR=2.0 -> reject.
    sym = _symbol_with_breakout(
        range_low=109.0, range_high=110.0, break_close=110.5,
        break_high=110.6, break_volume_mult=3.0,
    )
    nsei = _trending_nsei(80)
    view, book = _build_view_book(sym, nsei)
    orders = BreakoutStrategy().decide(view, book)
    assert not any(isinstance(o, EnterOrder) for o in orders), (
        "R:R screen should reject a shallow channel where the measured "
        "move offers less than MIN_RR × initial_risk."
    )


# 8. End-to-end no-leak: mutating bars strictly after a cut date must
#    not change any decision <= cut. Same shape as the gate test, but
#    exercised through THIS strategy (not BreakoutDouble) — so any future
#    `.shift(-1)`-style leak in the strategy's own code turns this RED.
def test_strategy_decisions_invariant_to_future_mutation():
    # 180-bar fixture with a CLEAN breakout setup engineered into the
    # window so the strategy actually trades (otherwise the test is
    # vacuous). Pre-breakout: 100 bars range-bound [100, 110] on low
    # volume. Breakout day at index 100: close 115 on 3x volume.
    # Post-breakout: 79 bars drifting upward so the position has room
    # to manage with Chandelier + time stop.
    rng = np.random.default_rng(42)
    n = 180

    pre_closes = rng.uniform(102.0, 108.0, 100)
    pre_highs = pre_closes + rng.uniform(0.1, 0.3, 100)    # tight ranges -> small ATR
    pre_lows = pre_closes - rng.uniform(0.1, 0.3, 100)
    pre_vols = rng.uniform(8e5, 1.2e6, 100)
    # Pin the breakout level (max prior high) to exactly 110 and the
    # channel low (min prior low) to exactly 100. CRITICAL: both pins
    # must land inside the strategy's 20-bar lookback at the breakout
    # day (T = index 100), i.e. indices [80, 99]. A pin at index 50 is
    # outside the window and the strategy would see a much lower
    # breakout level — the test was vacuous in the first revision for
    # exactly that reason.
    pre_highs[90] = 110.0
    pre_lows[85] = 90.0    # deeper channel — pushes measured-move target
                            # well above any plausible warm-up ATR risk so
                            # the strategy actually clears the RR screen on
                            # the breakout day (channel = 20, target = 130,
                            # reward 19 vs risk ~5 at ATR-warm-up = RR ~3.8).

    # Breakout day — modest pop above the level + tight H-L so the
    # gap-day TR doesn't slam ATR upward.
    break_close, break_high, break_low, break_vol = 111.0, 111.5, 110.7, 3e6

    post_closes = 111.0 + np.cumsum(rng.normal(0.10, 0.4, 79))
    post_highs = post_closes + rng.uniform(0.1, 0.3, 79)
    post_lows = post_closes - rng.uniform(0.1, 0.3, 79)
    post_vols = rng.uniform(8e5, 1.5e6, 79)

    closes = np.concatenate([pre_closes, [break_close], post_closes])
    highs = np.concatenate([pre_highs, [break_high], post_highs])
    lows = np.concatenate([pre_lows, [break_low], post_lows])
    vols = np.concatenate([pre_vols, [break_vol], post_vols])
    sym = _make_bars(closes, highs=highs, lows=lows, volumes=vols)
    nsei = _trending_nsei(n)

    data = {"X": sym, "^NSEI": nsei}
    base = run_replay(copy.deepcopy(data), BreakoutStrategy(),
                       record_decisions=True)

    # Cut AFTER the breakout so the breakout-day decision is inside
    # base_le_cut; mutation must not perturb it.
    cut = sym.index[150]
    mutated = copy.deepcopy(data)
    future = mutated["X"].index > cut
    # Replace post-cut symbol bars with FRESH random data (different
    # seed). Multiplicative scaling alone preserves the relative ratios
    # the strategy compares (close vs prior-N high, volume vs prior-W
    # average, measured-move target vs entry), so a leak-free strategy
    # would emit the same decisions even with × 2.5 — that mutation
    # would make the vacuous-check fire spuriously. Random replacement
    # destroys structure unambiguously: any leak-free strategy's
    # decisions <= cut are unchanged, and decisions > cut almost
    # certainly differ.
    fut_n_x = int(future.sum())
    rng2 = np.random.default_rng(999)
    new_closes = 200.0 * np.exp(np.cumsum(rng2.normal(0.002, 0.02, fut_n_x)))
    mutated["X"].loc[future, "open"] = new_closes
    mutated["X"].loc[future, "high"] = new_closes * 1.01
    mutated["X"].loc[future, "low"] = new_closes * 0.99
    mutated["X"].loc[future, "close"] = new_closes
    mutated["X"].loc[future, "volume"] = rng2.uniform(5e5, 5e6, fut_n_x)
    # Leave ^NSEI alone — we want to isolate the strategy's symbol-side
    # causality from regime fluctuations; if a leak existed only through
    # the regime read, the gate's own future-mutation test catches it.
    after = run_replay(mutated, BreakoutStrategy(), record_decisions=True)

    # Sanity FIRST: the strategy must actually fire on the fixture
    # (otherwise the leak-check below is trivially satisfied by a no-op
    # strategy). With the engineered breakout at index 100 the strategy
    # opens AND later closes the position — at least 2 non-empty
    # decisions are present before cut.
    base_non_empty_le_cut = [d for d in base["decisions"]
                              if d[0] <= cut and d[1]]
    assert len(base_non_empty_le_cut) >= 2, (
        f"Fixture is vacuous — strategy emitted only "
        f"{len(base_non_empty_le_cut)} non-empty decisions <= cut. "
        f"The leak-check below would not catch a real leak in a strategy "
        f"that simply does nothing. Adjust the fixture so the engineered "
        f"breakout actually fires.")

    base_le_cut = [d for d in base["decisions"] if d[0] <= cut]
    after_le_cut = [d for d in after["decisions"] if d[0] <= cut]
    assert base_le_cut == after_le_cut, (
        "BreakoutStrategy decisions on days <= cut changed when future "
        "bars were mutated — a look-ahead leak exists in the strategy "
        "or in something it transitively reads.")
