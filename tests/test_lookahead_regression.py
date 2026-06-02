"""THE PHASE 0 GATE — look-ahead-bias regression tests.

Per SWING_PROJECT_BOOTSTRAP.md (LAW 1) and the project kickoff: NO
strategy/model logic may be written until these pass. They prove the
engine-replay harness is causal for DAILY bars — that a decision made at
day-T close can use bars through T but can never see, or be influenced by,
any bar at T+1 or later, and that fills land on the T+1 open (never the
decision-day close).

The strongest detector here is `test_decisions_invariant_to_future_mutation`:
if a leak existed, mutating future bars would change past decisions.
"""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd

from backtesting.replay import BarView, run_replay
from config import SLIPPAGE_PCT
from tests._doubles import (
    BreakoutDouble, FixedHoldStrategy, OracleProbe, make_frame, random_walk,
    rising,
)


def test_barview_never_exposes_bars_after_cutoff():
    """BarView.history()/latest() are hard-bounded at the cutoff day."""
    data = {"X": make_frame(random_walk(120, seed=1))}
    idx = data["X"].index
    cutoff = idx[60]
    view = BarView(data, cutoff=cutoff)

    hist = view.history("X")
    assert hist.index.max() == cutoff
    assert (hist.index <= cutoff).all()
    assert idx[61] not in hist.index            # the next bar is invisible
    assert view.latest("X").name == cutoff      # latest causal row is day T


def test_oracle_probe_can_never_see_beyond_cutoff():
    """A strategy that actively tries to peek at the future never obtains a
    single bar beyond the decision day."""
    data = {"X": make_frame(random_walk(150, seed=2))}
    probe = OracleProbe("X")
    run_replay(data, probe)
    assert probe.max_seen_beyond_cutoff == 0


def test_decisions_invariant_to_future_mutation():
    """The canonical leak detector: scramble every bar strictly after a cut
    date; all decisions on days ≤ cut must be byte-for-byte identical. A
    leak (using any ≥ T+1 data) would change them."""
    closes = random_walk(160, seed=3)
    data = {"X": make_frame(closes)}
    cut = data["X"].index[110]

    base = run_replay(copy.deepcopy(data), BreakoutDouble("X"),
                      record_decisions=True)

    mutated = copy.deepcopy(data)
    future = mutated["X"].index > cut
    mutated["X"].loc[future, ["open", "high", "low", "close"]] *= 2.5  # arbitrary
    after = run_replay(mutated, BreakoutDouble("X"), record_decisions=True)

    base_le_cut = [d for d in base["decisions"] if d[0] <= cut]
    after_le_cut = [d for d in after["decisions"] if d[0] <= cut]
    assert base_le_cut == after_le_cut
    # Sanity: the mutation DID change later decisions (otherwise the test is
    # vacuous — e.g. if the strategy never traded).
    assert base["decisions"] != after["decisions"]


def test_entries_fill_at_next_open_not_decision_close():
    """Every entry fills at the entry-day open × (1+slippage) — never the
    decision-day close. This is the exact `<=`-vs-`<` same-bar leak that
    sank the intraday project, recast for the T-close → T+1-open design."""
    data = {"X": make_frame(rising(60))}
    res = run_replay(copy.deepcopy(data), FixedHoldStrategy(["X"], hold=2),
                     record_decisions=True)
    trades = res["trades"]
    assert not trades.empty

    df = data["X"]
    for _, row in trades.iterrows():
        ed = row["entry_date"]
        expected = df.at[ed, "open"] * (1.0 + SLIPPAGE_PCT)
        assert abs(row["entry_price"] - expected) < 1e-6
        # The entry day strictly follows the decision day, and (real gap)
        # open[ed] differs from the prior close — so the fill cannot be the
        # decision-day close.
        pos = df.index.get_loc(ed)
        assert pos >= 1
        prior_close = df["close"].iloc[pos - 1]
        assert abs(row["entry_price"] - prior_close) > 1e-9


def test_monotonic_rising_is_profitable_without_omniscience():
    """A rising series is *legitimately* profitable for buy-and-hold (a real
    trend, not a leak). The point: PF > 1 is fine, but it comes from honest
    next-open fills, not from same-bar foreknowledge."""
    data = {"X": make_frame(rising(80, rate=0.012))}
    res = run_replay(copy.deepcopy(data), FixedHoldStrategy(["X"], hold=3))
    assert res["metrics"]["n_trades"] > 0
    assert res["metrics"]["profit_factor"] > 1.0


def test_last_day_orders_do_not_fill():
    """An order decided on the final day has no next-open to fill against and
    must expire — never filled from thin air (or from a future that exists
    only in a leak)."""
    data = {"X": make_frame(rising(30))}
    res = run_replay(copy.deepcopy(data), FixedHoldStrategy(["X"], hold=99),
                     close_at_end=False, record_decisions=True)
    # The decision on the last day exists in the log...
    last_day = res["end"]
    last_decisions = [d for d in res["decisions"] if d[0] == last_day]
    assert last_decisions
    # ...but no trade has an entry_date on the last day (nothing fills there).
    if not res["trades"].empty:
        assert (res["trades"]["entry_date"] != last_day).all()
