"""Unit tests for the SMID (small/mid-cap) momentum strategy with
low-volatility tilt + liquidity sanity filter.

Contracts pinned (SMOM-2):
  1. Causal scoring & vol read — no future bars can influence today's
     pick (end-to-end future-mutation invariance through the strategy).
  2. Monthly-only rebalance (inherited from MomentumStrategy — sanity
     re-check via the subclass).
  3. Low-vol tilt selects correctly: with a controlled fixture where
     top-2N by momentum is well-defined, the strategy keeps the
     N lowest-vol from that pool — NOT the top-N by momentum.
  4. Liquidity sanity: thin-volume names in the momentum pool are
     dropped even if they would otherwise be lowest-vol.
  5. Sensible degenerate cases:
       - Empty universe (insufficient history) -> [].
       - Pool size > liquid names -> the strategy holds whatever is
         left (no error).
  6. End-to-end no-leak through SmidMomentumStrategy via ``run_replay``.

Plus a small set of harness regression tests for the new
``slippage_pct`` / ``brokerage_pct`` parameters:
  7. Defaults (no kwargs) reproduce prior behaviour byte-for-byte.
  8. Higher slippage produces a strictly worse net PnL on the same
     trade tape.
"""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from backtesting.replay import (
    BarView, Book, EnterOrder, ExitOrder, Position, run_replay,
)
from config import MOM_LOOKBACK_DAYS, MOM_SKIP_DAYS, MOM_TOP_N
from signals.smid_momentum import (
    SMID_MIN_MEDIAN_TRADED_VALUE, SMID_MOMENTUM_POOL_MULTIPLIER,
    SMID_VOL_WINDOW, SmidMomentumStrategy,
)
from signals.momentum import MomentumStrategy
from tests._doubles import FixedHoldStrategy, make_frame, random_walk


# ── Fixture helpers ─────────────────────────────────────────────────────


def _make_bars(closes: np.ndarray, *, start: str = "2019-01-01",
                volumes: np.ndarray | float = 5_000_000.0) -> pd.DataFrame:
    """SMID-friendly OHLCV: business-day index, controlled high/low band,
    parametrizable volume so the liquidity filter can be exercised."""
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    idx = pd.bdate_range(start, periods=n)
    band = closes * 0.005
    if np.isscalar(volumes):
        vol_arr = np.full(n, float(volumes))
    else:
        vol_arr = np.asarray(volumes, dtype=float)
        assert len(vol_arr) == n
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes + band,
            "low": closes - band,
            "close": closes,
            "volume": vol_arr,
        },
        index=idx,
    )


def _linear_trend_with_vol(end_return: float, *, n: int = 360,
                             daily_vol: float = 0.005,
                             seed: int = 11,
                             volume: float = 5_000_000.0,
                             start: str = "2019-01-01",
                             base: float = 100.0) -> pd.DataFrame:
    """Linear-drift price path + injected noise so we control BOTH the
    expected 12-1 momentum AND the trailing realized vol of each
    fixture. ``daily_vol`` is the std of multiplicative noise around
    the trend (e.g. 0.005 = 50bps/day).

    Calibration sanity:
        annualized_vol ≈ daily_vol × sqrt(252) ≈ daily_vol × 15.87
        daily_vol = 0.005  -> annualised ~8%
        daily_vol = 0.020  -> annualised ~32%
    """
    rng = np.random.default_rng(seed)
    rate = (1.0 + end_return) ** (1.0 / (n - 1)) - 1.0
    base_closes = np.array([base * (1.0 + rate) ** i for i in range(n)])
    noise = rng.normal(0.0, daily_vol, n)
    closes = base_closes * (1.0 + noise)
    # Force the LAST close to land near the target end-return so 12-1
    # scoring is predictable.
    closes[-1] = base * (1.0 + end_return)
    return _make_bars(closes, start=start, volumes=volume)


def _rebalance_day_cutoff(data: dict) -> pd.Timestamp:
    """First month-boundary day past the scoring eligibility threshold."""
    all_dates = sorted(set().union(*[set(df.index) for df in data.values()]))
    needed = MOM_LOOKBACK_DAYS + MOM_SKIP_DAYS + 1
    for i in range(needed, len(all_dates)):
        d, prev = all_dates[i], all_dates[i - 1]
        if d.month != prev.month:
            return d
    raise RuntimeError("no scoring-eligible rebalance day in fixture")


def _empty_book() -> Book:
    return Book(cash=500_000.0, equity=500_000.0, positions={})


# ── 1. Causal end-to-end through SmidMomentumStrategy ──────────────────


def test_no_leak_end_to_end_through_smid_strategy():
    """Mutating bars beyond a cut date must leave all decisions <= cut
    byte-identical. End-to-end leak detector for the SMID strategy
    (the vol + liquidity reads are new code paths)."""
    n = 360
    data = {
        "A": _linear_trend_with_vol(0.45, n=n, daily_vol=0.010, seed=1),
        "B": _linear_trend_with_vol(0.20, n=n, daily_vol=0.005, seed=2),
        "C": _linear_trend_with_vol(0.05, n=n, daily_vol=0.020, seed=3),
        "D": _linear_trend_with_vol(0.10, n=n, daily_vol=0.008, seed=4),
    }
    cut_idx = 310
    cut = list(data["A"].index)[cut_idx]

    s1 = SmidMomentumStrategy(top_n=2, momentum_pool_multiplier=2)
    base = run_replay(copy.deepcopy(data), s1, record_decisions=True)

    mutated = copy.deepcopy(data)
    for sym, df in mutated.items():
        mask = df.index > cut
        df.loc[mask, ["open", "high", "low", "close"]] *= 4.0

    s2 = SmidMomentumStrategy(top_n=2, momentum_pool_multiplier=2)
    after = run_replay(mutated, s2, record_decisions=True)

    base_le_cut = [d for d in base["decisions"] if d[0] <= cut]
    after_le_cut = [d for d in after["decisions"] if d[0] <= cut]
    assert base_le_cut == after_le_cut, (
        "SMID strategy decisions <= cut changed under future mutation.")


# ── 2. Monthly rebalance still respected (subclass sanity) ─────────────


def test_smid_non_rebalance_day_returns_empty():
    """SMID inherits the monthly-rebalance contract from MomentumStrategy.
    A mid-month decision returns [] regardless of book state."""
    n = 320
    data = {
        "A": _linear_trend_with_vol(0.40, n=n),
        "B": _linear_trend_with_vol(0.20, n=n),
        "C": _linear_trend_with_vol(0.10, n=n),
    }
    all_dates = sorted(data["A"].index)
    mid = None
    for i in range(1, len(all_dates)):
        d, prev = all_dates[i], all_dates[i - 1]
        if d.month == prev.month and i >= 300:
            mid = d
            break
    assert mid is not None
    view = BarView(data, cutoff=mid)
    orders = SmidMomentumStrategy(top_n=2).decide(view, _empty_book())
    assert orders == []


# ── 3. Low-vol tilt selects correctly ──────────────────────────────────


def test_low_vol_tilt_keeps_lowest_vol_from_momentum_pool():
    """Build 4 symbols all qualifying for the top-2N=4 pool by momentum.
    Two have low vol, two high vol. With top_n=2 and pool_multiplier=2,
    the strategy should pick the 2 LOWEST-VOL names — NOT the top-2 by
    momentum, which would be a pure-momentum strategy."""
    n = 360
    # All four have strong momentum (positive end_return). The pool is
    # top-4 (top_n=2 × multiplier=2), so all four enter the pool. The
    # tilt then picks the 2 with lowest trailing vol.
    data = {
        # Highest momentum but high vol — would be picked by pure
        # momentum (rank 1), should be DROPPED by the low-vol tilt.
        "HIVOL_WINNER": _linear_trend_with_vol(0.60, n=n,
                                                  daily_vol=0.025, seed=11),
        # Second-highest momentum, also high vol — also dropped.
        "HIVOL_SECOND": _linear_trend_with_vol(0.50, n=n,
                                                  daily_vol=0.022, seed=12),
        # Lower momentum but LOW vol — should be picked by the tilt.
        "LOVOL_THIRD":  _linear_trend_with_vol(0.40, n=n,
                                                  daily_vol=0.005, seed=13),
        # Even lower momentum but LOWEST vol — should be picked.
        "LOVOL_FOURTH": _linear_trend_with_vol(0.30, n=n,
                                                  daily_vol=0.003, seed=14),
    }
    cutoff = _rebalance_day_cutoff(data)
    view = BarView(data, cutoff=cutoff)
    # top_n=2, multiplier=2 -> pool=4 (all 4 symbols). Tilt picks 2.
    strat = SmidMomentumStrategy(top_n=2, momentum_pool_multiplier=2,
                                   min_median_traded_value=0.0)
    orders = strat.decide(view, _empty_book())
    enters = {o.symbol for o in orders if isinstance(o, EnterOrder)}
    assert enters == {"LOVOL_THIRD", "LOVOL_FOURTH"}, (
        f"Low-vol tilt failed — expected the two lowest-vol names from "
        f"the pool, got {enters}.")


def test_pure_momentum_baseline_picks_highest_momentum_for_compare():
    """SANITY: with the SAME fixture, MomentumStrategy (no tilt) picks
    the highest-momentum names. Together with the previous test this
    pins that the SMID tilt is producing a STRICTLY DIFFERENT selection
    than baseline — not accidentally reproducing the same set."""
    n = 360
    data = {
        "HIVOL_WINNER": _linear_trend_with_vol(0.60, n=n,
                                                  daily_vol=0.025, seed=11),
        "HIVOL_SECOND": _linear_trend_with_vol(0.50, n=n,
                                                  daily_vol=0.022, seed=12),
        "LOVOL_THIRD":  _linear_trend_with_vol(0.40, n=n,
                                                  daily_vol=0.005, seed=13),
        "LOVOL_FOURTH": _linear_trend_with_vol(0.30, n=n,
                                                  daily_vol=0.003, seed=14),
    }
    cutoff = _rebalance_day_cutoff(data)
    view = BarView(data, cutoff=cutoff)
    strat = MomentumStrategy(top_n=2)
    orders = strat.decide(view, _empty_book())
    enters = {o.symbol for o in orders if isinstance(o, EnterOrder)}
    assert enters == {"HIVOL_WINNER", "HIVOL_SECOND"}, (
        f"Baseline MomentumStrategy didn't pick the highest-momentum "
        f"names. Got {enters} — fixture sanity-check broken.")


# ── 4. Liquidity sanity ────────────────────────────────────────────────


def test_liquidity_filter_drops_thin_volume_names():
    """A name with high momentum but thin volume — close × volume below
    the floor — must be DROPPED from the SMOM pool, even if it would
    otherwise be lowest-vol."""
    n = 360
    data = {
        # Thin-volume name: high momentum + low vol but median traded
        # value ≈ Rs 100k (well under the Rs 1cr floor). Would be the
        # #1 pick under the low-vol tilt if liquidity weren't checked.
        "THIN":  _linear_trend_with_vol(0.50, n=n, daily_vol=0.003,
                                          volume=1_000.0, seed=21),
        "LIQUID_A": _linear_trend_with_vol(0.40, n=n, daily_vol=0.008,
                                              volume=10_000_000.0, seed=22),
        "LIQUID_B": _linear_trend_with_vol(0.30, n=n, daily_vol=0.005,
                                              volume=15_000_000.0, seed=23),
    }
    cutoff = _rebalance_day_cutoff(data)
    view = BarView(data, cutoff=cutoff)
    strat = SmidMomentumStrategy(top_n=2, momentum_pool_multiplier=2)
    orders = strat.decide(view, _empty_book())
    enters = {o.symbol for o in orders if isinstance(o, EnterOrder)}
    assert "THIN" not in enters, (
        f"Thin-volume name was selected despite the liquidity floor. "
        f"Got {enters}.")
    # The two liquid names are the only ones left in the pool — both
    # picked.
    assert enters == {"LIQUID_A", "LIQUID_B"}


def test_liquidity_threshold_is_configurable():
    """Lowering ``min_median_traded_value`` to 0 admits the thin-volume
    name back into the pool — proves the floor is the active filter,
    not some other accidental rejection."""
    n = 360
    data = {
        "THIN":  _linear_trend_with_vol(0.50, n=n, daily_vol=0.003,
                                          volume=1_000.0, seed=21),
        "LIQUID_A": _linear_trend_with_vol(0.40, n=n, daily_vol=0.008,
                                              volume=10_000_000.0, seed=22),
        "LIQUID_B": _linear_trend_with_vol(0.30, n=n, daily_vol=0.005,
                                              volume=15_000_000.0, seed=23),
    }
    cutoff = _rebalance_day_cutoff(data)
    view = BarView(data, cutoff=cutoff)
    strat = SmidMomentumStrategy(top_n=2, momentum_pool_multiplier=2,
                                   min_median_traded_value=0.0)
    orders = strat.decide(view, _empty_book())
    enters = {o.symbol for o in orders if isinstance(o, EnterOrder)}
    # With floor=0 the THIN name is the LOWEST-vol of the three so it
    # should be picked first.
    assert "THIN" in enters


# ── 5. Degenerate cases ────────────────────────────────────────────────


def test_smid_empty_universe_returns_empty_orders():
    """Universe with insufficient history -> no scores -> [] orders."""
    data = {
        "SHORT_A": _linear_trend_with_vol(0.40, n=100),
        "SHORT_B": _linear_trend_with_vol(0.20, n=100),
    }
    # Last bar of the fixture — no symbol has 274 bars yet.
    cutoff = list(data["SHORT_A"].index)[-1]
    view = BarView(data, cutoff=cutoff)
    orders = SmidMomentumStrategy(top_n=2).decide(view, _empty_book())
    assert orders == []


def test_smid_pool_smaller_than_top_n_holds_whatever_is_left():
    """If after liquidity + vol filtering fewer than ``top_n`` names
    remain, the strategy emits whatever IS in the final top_set rather
    than failing. (Cash-equivalent if zero remain.)"""
    n = 360
    data = {
        # Only ONE liquid name passes — pool is 4 but 3 of them are
        # thin-volume.
        "THIN_1": _linear_trend_with_vol(0.60, n=n, daily_vol=0.005,
                                           volume=1_000.0, seed=31),
        "THIN_2": _linear_trend_with_vol(0.50, n=n, daily_vol=0.005,
                                           volume=1_000.0, seed=32),
        "THIN_3": _linear_trend_with_vol(0.40, n=n, daily_vol=0.005,
                                           volume=1_000.0, seed=33),
        "LIQUID": _linear_trend_with_vol(0.30, n=n, daily_vol=0.008,
                                            volume=15_000_000.0, seed=34),
    }
    cutoff = _rebalance_day_cutoff(data)
    view = BarView(data, cutoff=cutoff)
    strat = SmidMomentumStrategy(top_n=2, momentum_pool_multiplier=2)
    orders = strat.decide(view, _empty_book())
    enters = {o.symbol for o in orders if isinstance(o, EnterOrder)}
    assert enters == {"LIQUID"}, (
        f"Strategy didn't gracefully reduce to the single liquid name; "
        f"got {enters}.")


# ── 6. Harness cost-parametrization regression tests ───────────────────


def test_replay_default_costs_byte_equivalent_to_prior_behavior():
    """Calling run_replay without slippage_pct / brokerage_pct (the
    new SMOM-2 kwargs) MUST produce byte-identical metrics, trades,
    and equity_curve as the prior signature."""
    data = {"X": make_frame(random_walk(140, seed=29))}
    base = run_replay(copy.deepcopy(data),
                       FixedHoldStrategy(["X"], hold=3))
    # Explicit defaults — must match the implicit defaults.
    from config import BROKERAGE_PCT, SLIPPAGE_PCT
    explicit = run_replay(copy.deepcopy(data),
                            FixedHoldStrategy(["X"], hold=3),
                            slippage_pct=SLIPPAGE_PCT,
                            brokerage_pct=BROKERAGE_PCT)
    assert base["metrics"] == explicit["metrics"]
    assert base["trades"].equals(explicit["trades"])
    assert base["equity_curve"].equals(explicit["equity_curve"])


def test_replay_higher_slippage_reduces_net_pnl():
    """Slippage is a real frictional cost: a higher slippage_pct should
    produce a strictly lower total trade PnL on the SAME synthetic
    data + strategy."""
    from tests._doubles import rising
    data = {"X": make_frame(rising(60))}
    base = run_replay(copy.deepcopy(data),
                       FixedHoldStrategy(["X"], hold=2),
                       slippage_pct=0.001)
    high_slip = run_replay(copy.deepcopy(data),
                            FixedHoldStrategy(["X"], hold=2),
                            slippage_pct=0.010)   # 1% slippage — brutal
    base_pnl = base["trades"]["pnl"].sum()
    high_pnl = high_slip["trades"]["pnl"].sum()
    assert high_pnl < base_pnl, (
        f"Higher slippage didn't reduce PnL: base={base_pnl:.2f}, "
        f"high={high_pnl:.2f}")


def test_replay_higher_brokerage_reduces_net_pnl():
    """Same test, brokerage axis."""
    from tests._doubles import rising
    data = {"X": make_frame(rising(60))}
    base = run_replay(copy.deepcopy(data),
                       FixedHoldStrategy(["X"], hold=2),
                       brokerage_pct=0.0003)
    high_brok = run_replay(copy.deepcopy(data),
                            FixedHoldStrategy(["X"], hold=2),
                            brokerage_pct=0.003)
    base_pnl = base["trades"]["pnl"].sum()
    high_pnl = high_brok["trades"]["pnl"].sum()
    assert high_pnl < base_pnl, (
        f"Higher brokerage didn't reduce PnL: base={base_pnl:.2f}, "
        f"high={high_pnl:.2f}")
