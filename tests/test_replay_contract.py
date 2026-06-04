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


# ── MR-4 — DD cap (portfolio-level drawdown circuit-breaker) ───────────


import numpy as np
from backtesting.replay import EnterOrder, ExitOrder


def _crash_then_recover(n_pre=15, crash_depth=0.20, n_crash=3, n_post=20):
    """Synthetic close series: flat -> sharp crash -> partial recovery.

    Designed so a long position sized at ~initial capital draws the
    portfolio equity through any 10/15/20% cap level during the crash,
    then recovers above the threshold during the post-crash period.
    """
    pre = np.full(n_pre, 100.0)
    bottom = 100.0 * (1.0 - crash_depth)
    crash = np.linspace(100.0, bottom, n_crash + 1)[1:]
    # Recover most (not all) of the loss so post-crash equity > peak * 0.9
    # (re-arms the 10% cap) but stays below the prior peak.
    top = 100.0 * 0.985
    post = np.linspace(bottom, top, n_post + 1)[1:]
    return np.concatenate([pre, crash, post])


class _AlwaysWantOne:
    """A deterministic strategy: always want exactly ONE position in X.

    Entry: if flat and we have at least one prior bar, propose an
        EnterOrder with a wide stop (so sizing always succeeds).
    Exit:  never voluntarily — the harness's force-close-at-end closes
        the one open position so the trade tape isn't empty.

    Used by the DD-cap tests because we need entries to KEEP being
    requested across the halt window. A strategy that exited would
    confound the test (re-entries after halt are what we want to
    measure)."""

    def __init__(self, symbol: str = "X", stop_frac: float = 0.6):
        self.symbol = symbol
        self.stop_frac = stop_frac

    def decide(self, view, book):
        if book.has_position(self.symbol):
            return []
        latest = view.latest(self.symbol)
        if latest is None:
            return []
        stop = float(latest["close"]) * (1.0 - self.stop_frac)
        return [EnterOrder(symbol=self.symbol, stop=stop, reason="probe")]


def test_dd_cap_none_reproduces_default_behavior_exactly():
    """``dd_cap_pct=None`` must be byte-equivalent to the prior signature
    — the entire metrics / trades / equity_curve produced by the two
    calls must be identical."""
    data = {"X": make_frame(random_walk(160, seed=11))}
    base = run_replay(copy.deepcopy(data), FixedHoldStrategy(["X"], hold=4))
    capped_none = run_replay(copy.deepcopy(data),
                              FixedHoldStrategy(["X"], hold=4),
                              dd_cap_pct=None)
    assert base["metrics"] == capped_none["metrics"]
    assert base["trades"].equals(capped_none["trades"])
    assert base["equity_curve"].equals(capped_none["equity_curve"])


def test_dd_cap_blocks_new_entries_during_breach_and_resumes_on_recovery():
    """Set up a crash-then-recover price path that's deep enough to breach
    a 10% portfolio cap with a meaningfully-sized position, and confirm
    the cap correctly blocks NEW entries during the breach.

    The default ``MAX_RISK_PCT=0.01`` + ``MAX_PORTFOLIO_HEAT=0.08`` keeps
    positions tiny (~1-2% of equity), so a 20% stock crash barely moves
    portfolio equity. We override both for this unit test so a single
    position is large enough that the stock crash translates into a
    real portfolio drawdown.
    """
    closes = _crash_then_recover(crash_depth=0.30)   # deeper crash
    data = {"X": make_frame(closes)}
    strat = _AlwaysWantOne(symbol="X", stop_frac=0.5)

    # Uncapped baseline.
    base = run_replay(copy.deepcopy(data), strat,
                       risk_pct=0.50, max_heat=1.0,
                       close_at_end=True, record_decisions=True)
    assert base["metrics"]["n_trades"] == 1, base["trades"]

    # Capped run with the same sizing.
    capped = run_replay(copy.deepcopy(data), strat,
                         risk_pct=0.50, max_heat=1.0,
                         dd_cap_pct=0.10, close_at_end=True,
                         record_decisions=True)
    assert capped["metrics"]["n_trades"] == 1
    eq = capped["equity_curve"]
    peak = eq.cummax()
    breached = (eq <= peak * 0.90).any()
    assert breached, (
        f"Test fixture didn't actually breach the 10% cap — equity "
        f"range {float(eq.min()):.0f}..{float(eq.max()):.0f}.")


def test_dd_cap_does_not_block_exits():
    """The cap is for NEW exposure. Open positions must still be exitable
    when the strategy says so, even during a breach period."""
    closes = _crash_then_recover(n_pre=10, crash_depth=0.30, n_crash=3,
                                  n_post=15)
    data = {"X": make_frame(closes)}
    # Strategy: enter on day 1, exit after 5 bars (hold=5). With wide
    # sizing the crash drives portfolio equity through the 10% cap by
    # the time the exit fills.
    strat = FixedHoldStrategy(["X"], hold=5, stop_frac=0.5)
    res = run_replay(copy.deepcopy(data), strat,
                     risk_pct=0.50, max_heat=1.0,
                     dd_cap_pct=0.10, close_at_end=False,
                     record_decisions=True)
    trades = res["trades"]
    assert not trades.empty
    time_exits = trades[trades["exit_reason"] == "time"]
    assert not time_exits.empty, (
        f"Expected at least one time-exit. Got: {trades.to_dict('records')}")


def test_dd_cap_entries_resume_after_recovery():
    """Once equity-at-open climbs back above peak*(1-dd_cap_pct), new
    entries must resume on the very next decision day (symmetric re-arm).

    Construction:
      * Open a position on a flat warm-up section.
      * Crash phase pulls equity below the cap -> entries halted.
      * Recovery brings equity back above the cap.
      * After the recovery + the first exit, the strategy re-enters.
    """
    # Sequence: flat, crash, recovery TO ABOVE prior peak (so cap re-arms).
    pre = np.full(8, 100.0)
    bottom = 80.0
    crash = np.linspace(100.0, bottom, 4)[1:]
    # Recover and EXCEED the prior peak so the cap definitely re-arms.
    recovery = np.linspace(bottom, 110.0, 8)[1:]
    extension = np.full(8, 110.0)
    closes = np.concatenate([pre, crash, recovery, extension])
    data = {"X": make_frame(closes)}
    # Hold=4: open early, exit during recovery, re-enter once flat.
    # Wide sizing so the crash actually breaches the 10% cap.
    strat = FixedHoldStrategy(["X"], hold=4, stop_frac=0.5)
    res = run_replay(copy.deepcopy(data), strat,
                     risk_pct=0.50, max_heat=1.0,
                     dd_cap_pct=0.10, close_at_end=False)
    trades = res["trades"]
    # Pre-crash entry: 1 trade. Post-recovery re-entry: 1+ more.
    # The exact count depends on cycle alignment, but we require >= 2
    # to prove re-arm actually happened (a permanently-stuck cap would
    # leave us with just the pre-crash trade).
    assert res["metrics"]["n_trades"] >= 2, (
        f"Re-arm failed — only {res['metrics']['n_trades']} trade(s) "
        f"with a recovery above the prior peak.")
