"""Unit tests for the cross-sectional momentum strategy (MOM-2).

Covers the four contracts ops asked for:

  1. Causal ranking — momentum scores depend only on past bars
     (mutating bars > cutoff must not change scores).
  2. Monthly-only rebalance — non-rebalance days return [] even when
     the book is out of alignment with the current top-N.
  3. Rotation enter/exit — top-N changes between rebalances produce
     ExitOrder for dropped names and EnterOrder for new top-N names.
  4. End-to-end no-leak through MomentumStrategy via run_replay
     (future-mutation invariance, the canonical leak detector).

Plus the MOM-specific eligibility contract:

  5. The 12-1 skip month is actually applied — a symbol that crashed
     in the LAST month but rose over the prior 12 still ranks by the
     prior 12, not the recent crash.
  6. Names with < 12 months of history are EXCLUDED from scoring,
     regardless of how attractive their short history looks.
  7. ``^``-prefixed symbols (^NSEI etc.) are never traded.
"""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from backtesting.replay import BarView, Book, EnterOrder, ExitOrder, run_replay
from config import MOM_LOOKBACK_DAYS, MOM_SKIP_DAYS, MOM_TOP_N
from signals.momentum import MomentumStrategy


# ── Fixture helpers ─────────────────────────────────────────────────────


def _make_bars(closes: np.ndarray, *, start: str = "2019-01-01") -> pd.DataFrame:
    """Build a daily OHLCV frame with a business-day index. Tight H-L
    band so the harness's per-position stats stay deterministic; flat
    volume keeps the structure simple."""
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    idx = pd.bdate_range(start, periods=n)
    band = closes * 0.005
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes + band,
            "low": closes - band,
            "close": closes,
            "volume": np.full(n, 1_000_000.0),
        },
        index=idx,
    )


def _linear_trend(end_return: float, *, n: int = 320,
                   start: str = "2019-01-01", base: float = 100.0
                   ) -> pd.DataFrame:
    """Deterministic linear price path from base to base*(1+end_return).
    Lets us reason about exact 12-1 scores without RNG noise."""
    closes = np.linspace(base, base * (1.0 + end_return), n)
    return _make_bars(closes, start=start)


def _build_view(data: dict, *, cutoff_idx: int = -1) -> BarView:
    """Construct a BarView whose cutoff is the ``cutoff_idx``-th bar in
    the union timeline of ``data``. By default, cutoff = last bar."""
    all_dates = sorted(set().union(*[set(df.index) for df in data.values()]))
    cutoff = all_dates[cutoff_idx]
    return BarView(data, cutoff=cutoff)


def _empty_book() -> Book:
    return Book(cash=500_000.0, equity=500_000.0, positions={})


# ── 1. Causal scoring — no future bars can influence scores ────────────


def test_scores_are_causal_only_uses_past():
    """Mutating bars strictly after the cutoff must not change any
    momentum score. The canonical leak detector at the score layer."""
    n = 320
    data1 = {
        "A": _linear_trend(0.50, n=n),   # +50% winner
        "B": _linear_trend(0.10, n=n),   # +10% medium
        "C": _linear_trend(-0.10, n=n),  # -10% loser
    }
    cutoff = list(data1["A"].index)[280]
    view1 = BarView(data1, cutoff=cutoff)
    strat = MomentumStrategy()
    scores1 = strat._compute_scores(view1)

    data2 = copy.deepcopy(data1)
    for sym, df in data2.items():
        mask = df.index > cutoff
        df.loc[mask, ["open", "high", "low", "close"]] *= 5.0
    view2 = BarView(data2, cutoff=cutoff)
    scores2 = strat._compute_scores(view2)

    assert scores1 == scores2, (
        "Momentum scores changed when bars > cutoff were mutated — "
        f"score layer leaked future data. before={scores1} after={scores2}")


# ── 2. Rebalance gate — strategy is a no-op except on month boundaries ──


def test_non_rebalance_day_returns_empty_orders():
    """Mid-month decision returns no orders, even when the book is out
    of alignment with the would-be top-N. Rotation MUST be monthly."""
    n = 320
    data = {
        "A": _linear_trend(0.50, n=n),
        "B": _linear_trend(0.30, n=n),
        "C": _linear_trend(0.05, n=n),
    }
    # Pick a cutoff that is provably mid-month: same month as the prior bar.
    all_dates = sorted(data["A"].index)
    mid = None
    for i in range(1, len(all_dates)):
        d, prev = all_dates[i], all_dates[i - 1]
        if d.month == prev.month and i >= 290:
            mid = d
            break
    assert mid is not None, "no mid-month bar found in fixture"
    view = BarView(data, cutoff=mid)
    strat = MomentumStrategy(top_n=2)

    orders = strat.decide(view, _empty_book())
    assert orders == []


def test_month_boundary_triggers_rebalance():
    """When the prior bar is in a different calendar month than the
    cutoff, the strategy MUST emit rotation orders."""
    n = 320
    data = {
        "A": _linear_trend(0.50, n=n),
        "B": _linear_trend(0.30, n=n),
        "C": _linear_trend(0.05, n=n),
    }
    all_dates = sorted(data["A"].index)
    # First bar whose month != prior bar's month, and far enough in
    # to have full 12-1 history.
    boundary = None
    for i in range(1, len(all_dates)):
        d, prev = all_dates[i], all_dates[i - 1]
        if d.month != prev.month and i >= 290:
            boundary = d
            break
    assert boundary is not None, "no month-boundary bar found in fixture"

    view = BarView(data, cutoff=boundary)
    strat = MomentumStrategy(top_n=2)
    orders = strat.decide(view, _empty_book())
    # Top-2 should be A (+0.50) and B (+0.30); both produce EnterOrders
    # from an empty book.
    enters = [o for o in orders if isinstance(o, EnterOrder)]
    assert {o.symbol for o in enters} == {"A", "B"}, (
        f"unexpected entries: {[o.symbol for o in enters]}")


# ── 3. Rotation enter/exit on rank changes ─────────────────────────────


def _rebalance_day_cutoff(data: dict) -> pd.Timestamp:
    """Helper: find the first month-boundary day in the data dict with
    enough history for a full 12-1 score (>= 274 bars)."""
    all_dates = sorted(set().union(*[set(df.index) for df in data.values()]))
    needed = MOM_LOOKBACK_DAYS + MOM_SKIP_DAYS + 1
    for i in range(needed, len(all_dates)):
        d, prev = all_dates[i], all_dates[i - 1]
        if d.month != prev.month:
            return d
    raise RuntimeError("no scoring-eligible rebalance day in fixture")


def test_rotation_exits_dropped_name_and_enters_new_top_n():
    """Book holds A and B. The current ranking puts A and C in top-2
    (B fell out). Expected: ExitOrder(B), EnterOrder(C), nothing for A."""
    n = 320
    data = {
        "A": _linear_trend(0.50, n=n),   # rank 1
        "B": _linear_trend(0.05, n=n),   # rank 3 — held but not in top-2
        "C": _linear_trend(0.30, n=n),   # rank 2 — new entrant
    }
    cutoff = _rebalance_day_cutoff(data)

    # Simulate a book holding A and B (the prior month's top-2). The
    # Position constructor needs minimum fields; the strategy reads
    # only book.open_symbols() / book.has_position(), so the exact
    # numbers don't matter.
    from backtesting.replay import Position
    positions = {
        s: Position(symbol=s, entry_date=cutoff,
                    entry_price=100.0, shares=10, stop=90.0,
                    risk_per_share=10.0, cost_basis=1000.0)
        for s in ("A", "B")
    }
    book = Book(cash=400_000.0, equity=500_000.0, positions=positions)

    view = BarView(data, cutoff=cutoff)
    strat = MomentumStrategy(top_n=2)
    orders = strat.decide(view, book)

    exit_syms = {o.symbol for o in orders if isinstance(o, ExitOrder)}
    enter_syms = {o.symbol for o in orders if isinstance(o, EnterOrder)}
    assert exit_syms == {"B"}, (
        f"expected ExitOrder for B only, got {exit_syms}")
    assert enter_syms == {"C"}, (
        f"expected EnterOrder for C only, got {enter_syms}")
    # And the rotation_out reason is set on the exit.
    b_exit = next(o for o in orders if isinstance(o, ExitOrder) and o.symbol == "B")
    assert b_exit.reason == "rotation_out"


def test_held_names_still_in_top_n_get_no_orders():
    """If our held names are STILL the top-N, no rotation orders."""
    n = 320
    data = {
        "A": _linear_trend(0.50, n=n),
        "B": _linear_trend(0.30, n=n),
        "C": _linear_trend(0.05, n=n),
    }
    cutoff = _rebalance_day_cutoff(data)
    from backtesting.replay import Position
    positions = {
        s: Position(symbol=s, entry_date=cutoff,
                    entry_price=100.0, shares=10, stop=90.0,
                    risk_per_share=10.0, cost_basis=1000.0)
        for s in ("A", "B")
    }
    book = Book(cash=400_000.0, equity=500_000.0, positions=positions)

    view = BarView(data, cutoff=cutoff)
    strat = MomentumStrategy(top_n=2)
    orders = strat.decide(view, book)

    # Neither A nor B should be in either order set.
    syms = {o.symbol for o in orders}
    assert "A" not in syms
    assert "B" not in syms


# ── 4. End-to-end no-leak through MomentumStrategy via run_replay ──────


def test_no_leak_end_to_end_through_momentum_strategy():
    """Canonical future-mutation invariance — through THIS strategy
    end-to-end. Decisions on days <= cut must be byte-identical when
    bars > cut are scrambled."""
    n = 330
    data = {
        "A": _linear_trend(0.45, n=n),
        "B": _linear_trend(0.20, n=n),
        "C": _linear_trend(-0.05, n=n),
    }
    cut_idx = 290   # well into scoring-eligible territory
    cut = list(data["A"].index)[cut_idx]

    strat1 = MomentumStrategy(top_n=2)
    base = run_replay(copy.deepcopy(data), strat1, record_decisions=True)

    mutated = copy.deepcopy(data)
    for sym, df in mutated.items():
        mask = df.index > cut
        df.loc[mask, ["open", "high", "low", "close"]] *= 3.0

    strat2 = MomentumStrategy(top_n=2)
    after = run_replay(mutated, strat2, record_decisions=True)

    base_le_cut = [d for d in base["decisions"] if d[0] <= cut]
    after_le_cut = [d for d in after["decisions"] if d[0] <= cut]
    assert base_le_cut == after_le_cut, (
        "Decisions on days <= cut diverged when future bars were mutated "
        "— strategy or harness leaked future info.")


# ── 5. Skip month is actually applied ──────────────────────────────────


def test_skip_month_excludes_recent_window_from_score():
    """A symbol that crashed in the LAST 21 bars but rose strongly in
    the preceding 252 bars must still rank by the prior 252 — that's
    the whole point of the skip month. We verify the score uses prices
    at -(skip+1) and -(skip+lookback+1), NOT -1 and -(lookback+1)."""
    n = 320
    # Build a path: linear +30% over the first (n - 21) bars, then a
    # 25% crash in the last 21 bars. 12-1 score should reflect the
    # +30% prior period because the recent crash is in the skip window.
    pre = np.linspace(100.0, 130.0, n - MOM_SKIP_DAYS)
    crash = np.linspace(130.0, 130.0 * 0.75, MOM_SKIP_DAYS + 1)[1:]
    closes = np.concatenate([pre, crash])
    data = {"X": _make_bars(closes)}
    cutoff = list(data["X"].index)[-1]
    view = BarView(data, cutoff=cutoff)

    strat = MomentumStrategy()
    scores = strat._compute_scores(view)
    assert "X" in scores
    # Score should be roughly the +30% from the pre-crash period.
    # close[-(skip+1)] is the last bar of `pre` (~130).
    # close[-(skip+lookback+1)] is somewhere ~bar 47 of `pre`
    # (start=100, linear, so ~100 + (47/(n-21)) * 30 ≈ ~104.7).
    # Score ≈ 130/104.7 - 1 ≈ +0.24.
    assert scores["X"] > 0.15, (
        f"Score {scores['X']:.4f} doesn't reflect prior-12-month gain — "
        "skip window may not be applied correctly.")
    assert scores["X"] < 0.40, (
        f"Score {scores['X']:.4f} too large — looks like the score used the "
        "raw 12-month return without the skip.")


# ── 6. <12mo history excluded from ranking ─────────────────────────────


def test_short_history_symbol_excluded_from_ranking():
    """A symbol with fewer than `lookback + skip + 1` bars does NOT
    appear in the score map, regardless of how attractive the short
    series looks. This protects MOM from newer listings (LICI, SBICARD,
    VBL, SBILIFE) hijacking the ranking with stub data."""
    n_full = 320
    n_short = 100   # well under the 274 needed
    data = {
        "FULL": _linear_trend(0.30, n=n_full),
        "SHORT": _linear_trend(2.00, n=n_short,
                                start="2020-01-01"),   # would rank #1 if eligible
    }
    cutoff = list(data["FULL"].index)[-1]
    view = BarView(data, cutoff=cutoff)
    strat = MomentumStrategy()
    scores = strat._compute_scores(view)
    assert "FULL" in scores
    assert "SHORT" not in scores, (
        "Symbol with insufficient history slipped into ranking — "
        "MOM-2 must defensively skip <12mo names regardless.")


def test_short_history_symbol_never_ordered_even_on_rebalance_day():
    """End-to-end: even on a rebalance day with an empty book, the
    short-history symbol gets no EnterOrder. This guards against a
    coding mistake where scoring exclusion and entry selection drift
    apart in the future."""
    n_full = 320
    n_short = 100
    data = {
        "FULL": _linear_trend(0.30, n=n_full),
        "SHORT": _linear_trend(2.00, n=n_short, start="2020-01-01"),
    }
    cutoff = _rebalance_day_cutoff({"FULL": data["FULL"]})
    view = BarView(data, cutoff=cutoff)
    orders = MomentumStrategy(top_n=5).decide(view, _empty_book())
    syms = {o.symbol for o in orders}
    assert "SHORT" not in syms, (
        f"Short-history symbol was ordered: {syms}")


# ── 7. ^-prefixed macro indices are never traded ───────────────────────


def test_macro_index_symbols_never_traded():
    """^NSEI / ^INDIAVIX may be in the data dict (the harness needs them
    for future regime checks), but the momentum strategy must never
    rank, buy, or sell them. They're skipped at both score and order
    layers."""
    n = 320
    data = {
        "A": _linear_trend(0.50, n=n),
        "^NSEI": _linear_trend(0.45, n=n),   # would be top-1 if eligible
        "B": _linear_trend(0.10, n=n),
    }
    cutoff = _rebalance_day_cutoff(data)
    view = BarView(data, cutoff=cutoff)
    strat = MomentumStrategy(top_n=3)

    scores = strat._compute_scores(view)
    assert "^NSEI" not in scores

    orders = strat.decide(view, _empty_book())
    syms = {o.symbol for o in orders}
    assert "^NSEI" not in syms, (
        f"Macro index appeared in orders: {syms}")


# ── 8. MOM-4 absolute-momentum filter (Antonacci dual-momentum) ────────


def test_absolute_filter_drops_negative_score_names():
    """Filter ON: names whose own 12-1 score is non-positive get dropped
    from the top-N even if they ranked into it. Mixed-sign universe -
    only the positive-score names should be ordered.

    Build A (+0.50), B (+0.20), C (-0.10), D (-0.30). With top_n=4:
      filter OFF: all four ordered (top-4 by RELATIVE rank).
      filter ON : only A and B ordered (top_set filtered to >0).
    """
    n = 320
    # Strong winners + clear losers. Linear trends produce 12-1 scores
    # closely matching the end-return; we keep margins wide so the test
    # isn't sensitive to the exact skip-window arithmetic.
    data = {
        "A": _linear_trend(0.50, n=n),
        "B": _linear_trend(0.20, n=n),
        "C": _linear_trend(-0.10, n=n),
        "D": _linear_trend(-0.30, n=n),
    }
    cutoff = _rebalance_day_cutoff(data)
    view = BarView(data, cutoff=cutoff)

    baseline = MomentumStrategy(top_n=4)
    baseline_orders = baseline.decide(view, _empty_book())
    baseline_syms = {o.symbol for o in baseline_orders if isinstance(o, EnterOrder)}
    assert baseline_syms == {"A", "B", "C", "D"}, (
        f"baseline (filter OFF) should order all 4 by relative rank, "
        f"got: {baseline_syms}")

    filtered = MomentumStrategy(top_n=4, use_absolute_filter=True)
    filtered_orders = filtered.decide(view, _empty_book())
    filtered_syms = {o.symbol for o in filtered_orders if isinstance(o, EnterOrder)}
    assert filtered_syms == {"A", "B"}, (
        f"filter ON should drop negative-score names C and D, got: "
        f"{filtered_syms}")


def test_absolute_filter_all_negative_universe_orders_zero_enters():
    """In a universe-wide downturn where EVERY symbol has negative
    absolute momentum, the dual-momentum strategy must propose ZERO
    entries (fully cash). This is the crash-avoidance behaviour the
    filter exists to deliver."""
    n = 320
    data = {
        "A": _linear_trend(-0.10, n=n),
        "B": _linear_trend(-0.20, n=n),
        "C": _linear_trend(-0.30, n=n),
    }
    cutoff = _rebalance_day_cutoff(data)
    view = BarView(data, cutoff=cutoff)

    filtered = MomentumStrategy(top_n=3, use_absolute_filter=True)
    orders = filtered.decide(view, _empty_book())
    enters = [o for o in orders if isinstance(o, EnterOrder)]
    assert enters == [], (
        f"all-negative universe should produce zero EnterOrders with "
        f"filter ON, got: {[o.symbol for o in enters]}")


def test_absolute_filter_all_negative_exits_held_positions_to_cash():
    """A book holding names that have all turned negative MUST be
    rotated out to cash on the next rebalance day with filter ON.
    The mechanism reuses the existing rotation_out exit path -- no new
    exit semantics. This is what 'falls cleanly to cash' means."""
    from backtesting.replay import Position
    n = 320
    data = {
        "A": _linear_trend(-0.10, n=n),
        "B": _linear_trend(-0.20, n=n),
        "C": _linear_trend(-0.30, n=n),
    }
    cutoff = _rebalance_day_cutoff(data)
    positions = {
        s: Position(symbol=s, entry_date=cutoff,
                    entry_price=100.0, shares=10, stop=90.0,
                    risk_per_share=10.0, cost_basis=1000.0)
        for s in ("A", "B")
    }
    book = Book(cash=400_000.0, equity=500_000.0, positions=positions)

    view = BarView(data, cutoff=cutoff)
    filtered = MomentumStrategy(top_n=3, use_absolute_filter=True)
    orders = filtered.decide(view, book)

    exit_syms = {o.symbol for o in orders if isinstance(o, ExitOrder)}
    enter_syms = {o.symbol for o in orders if isinstance(o, EnterOrder)}
    assert exit_syms == {"A", "B"}, (
        f"all held names should be rotated out with filter ON in an "
        f"all-negative universe, got exits: {exit_syms}")
    assert enter_syms == set(), (
        f"no entries should be proposed when filter rejects everything, "
        f"got enters: {enter_syms}")


def test_filter_off_reproduces_mom2_baseline_exactly():
    """Default (filter OFF) MUST produce identical orders to an explicit
    ``use_absolute_filter=False``. This pins the default-behaviour
    contract: MOM-4 cannot accidentally change MOM-2 semantics.
    Tested across a mixed universe where the filter WOULD make a
    difference if it were on -- so any drift between default and
    explicit-OFF would be visible."""
    n = 320
    data = {
        "A": _linear_trend(0.50, n=n),
        "B": _linear_trend(0.10, n=n),
        "C": _linear_trend(-0.10, n=n),
        "D": _linear_trend(-0.40, n=n),
    }
    cutoff = _rebalance_day_cutoff(data)
    view = BarView(data, cutoff=cutoff)

    s_default = MomentumStrategy(top_n=4)              # default
    s_explicit_off = MomentumStrategy(top_n=4,
                                       use_absolute_filter=False)
    orders_default = s_default.decide(view, _empty_book())
    orders_off = s_explicit_off.decide(view, _empty_book())

    # Stable comparison: serialise by (kind, symbol).
    def _ser(orders):
        return sorted(
            (("E" if isinstance(o, EnterOrder) else "X", o.symbol)
              for o in orders))

    assert _ser(orders_default) == _ser(orders_off), (
        f"default and explicit-OFF diverged. default={_ser(orders_default)} "
        f"explicit_off={_ser(orders_off)}")


def test_no_leak_end_to_end_with_filter_on():
    """Look-ahead regression -- with filter ON. Mutating bars beyond the
    cut must not change any decision <= cut. The filter inspects
    ``scores[s]`` which is derived from causal slices only, so the
    invariance should hold; this test catches accidental drift."""
    n = 330
    data = {
        "A": _linear_trend(0.45, n=n),
        "B": _linear_trend(0.20, n=n),
        "C": _linear_trend(-0.05, n=n),  # boundary candidate
    }
    cut_idx = 290
    cut = list(data["A"].index)[cut_idx]

    s1 = MomentumStrategy(top_n=3, use_absolute_filter=True)
    base = run_replay(copy.deepcopy(data), s1, record_decisions=True)

    mutated = copy.deepcopy(data)
    for sym, df in mutated.items():
        mask = df.index > cut
        df.loc[mask, ["open", "high", "low", "close"]] *= 4.0

    s2 = MomentumStrategy(top_n=3, use_absolute_filter=True)
    after = run_replay(mutated, s2, record_decisions=True)

    base_le_cut = [d for d in base["decisions"] if d[0] <= cut]
    after_le_cut = [d for d in after["decisions"] if d[0] <= cut]
    assert base_le_cut == after_le_cut, (
        "Decisions on days <= cut diverged when future bars were mutated "
        "with the absolute filter ON.")
