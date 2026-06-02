"""Engine-replay contract tests (ported + extended from the intraday
project's replay-contract suite). These pin the harness's shape and
execution semantics so future changes can't silently regress them.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pandas as pd

import backtesting.replay as replay_mod
from backtesting.replay import BarView, run_replay
from config import BROKERAGE_PCT, SLIPPAGE_PCT
from tests._doubles import FixedHoldStrategy, make_frame, random_walk, rising


def test_run_replay_returns_required_keys():
    data = {"X": make_frame(rising(40))}
    res = run_replay(copy.deepcopy(data), FixedHoldStrategy(["X"], hold=3))
    for key in ("metrics", "trades", "equity_curve", "final_cash",
                "n_days", "start", "end"):
        assert key in res
    assert isinstance(res["trades"], pd.DataFrame)
    assert isinstance(res["equity_curve"], pd.Series)
    for m in ("sharpe", "max_drawdown", "cagr", "win_rate", "profit_factor",
              "n_trades"):
        assert m in res["metrics"]


def test_replay_module_touches_no_database():
    """The harness must operate purely on in-memory frames — a replay can
    never read or write live DB state (it works on a `data` dict). Guard by
    asserting the module imports nothing from data.database."""
    src = Path(replay_mod.__file__).read_text(encoding="utf-8")
    assert "data.database" not in src
    assert "import sqlite3" not in src


def test_costs_applied_entry_above_open_exit_below_open():
    data = {"X": make_frame(rising(50))}
    res = run_replay(copy.deepcopy(data), FixedHoldStrategy(["X"], hold=2),
                     close_at_end=False)
    trades = res["trades"]
    df = data["X"]
    timed = trades[trades["exit_reason"] == "time"]
    assert not timed.empty
    for _, row in timed.iterrows():
        assert row["entry_price"] > df.at[row["entry_date"], "open"]   # +slippage
        assert row["exit_price"] < df.at[row["exit_date"], "open"]     # -slippage
        # net PnL reflects round-trip brokerage + slippage drag.
        assert row["entry_price"] == df.at[row["entry_date"], "open"] * (1 + SLIPPAGE_PCT)
        assert row["exit_price"] == df.at[row["exit_date"], "open"] * (1 - SLIPPAGE_PCT)


def test_determinism_same_input_same_result():
    data = {"X": make_frame(random_walk(140, seed=9))}
    r1 = run_replay(copy.deepcopy(data), FixedHoldStrategy(["X"], hold=4))
    r2 = run_replay(copy.deepcopy(data), FixedHoldStrategy(["X"], hold=4))
    assert r1["metrics"] == r2["metrics"]
    assert r1["trades"].equals(r2["trades"])
    assert r1["equity_curve"].equals(r2["equity_curve"])


def test_max_positions_cap_respected():
    syms = [f"S{i}" for i in range(5)]
    data = {s: make_frame(rising(40, base=100 + 5 * i)) for i, s in enumerate(syms)}
    # Hold forever; with a 2-position cap only 2 of the 5 can ever be open,
    # so exactly 2 end-of-data trades come out.
    res = run_replay(copy.deepcopy(data), FixedHoldStrategy(syms, hold=999),
                     max_positions=2, max_per_sector=99, close_at_end=True)
    assert res["metrics"]["n_trades"] == 2
    assert res["trades"]["symbol"].nunique() == 2


def test_barview_history_does_not_leak_mutations_back():
    data = {"X": make_frame(rising(30))}
    view = BarView(data, cutoff=data["X"].index[10])
    h = view.history("X")
    h.loc[h.index[0], "close"] = -999.0
    # mutating the returned slice must not corrupt the source frame
    assert data["X"]["close"].iloc[0] != -999.0


def test_empty_data_raises():
    try:
        run_replay({}, FixedHoldStrategy(["X"]))
    except ValueError:
        return
    raise AssertionError("run_replay({}) should raise ValueError")
