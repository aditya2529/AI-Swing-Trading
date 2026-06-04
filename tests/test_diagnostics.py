"""Sanity tests for backtesting/diagnostics.py.

The diagnostics module is a refactor of inline T3 code — most of its
behaviour is exercised end-to-end by the MR-2 backtest report. These
tests cover the contract-shaped edges that any future caller will hit:
empty trade tape, all-winners (infinite PF), correct removal arithmetic.
"""
from __future__ import annotations

import pandas as pd
import pytest

from backtesting.diagnostics import (
    binomial_p_value, bootstrap_pf_ci, concentration_flags, per_symbol_pnl,
    per_year_pnl, profit_factor, robustness,
)


def _trades_df(rows):
    return pd.DataFrame(rows, columns=["symbol", "entry_date", "exit_date",
                                         "pnl"])


def test_profit_factor_on_empty_is_nan():
    pf = profit_factor(pd.Series(dtype=float))
    assert pd.isna(pf)


def test_profit_factor_inf_when_no_losers():
    assert profit_factor(pd.Series([1.0, 2.0, 3.0])) == float("inf")


def test_profit_factor_basic():
    # wins 10, losses -5 -> 2.0
    assert profit_factor(pd.Series([3.0, 7.0, -2.0, -3.0])) == 2.0


def test_per_symbol_and_per_year_on_empty_return_empty():
    empty = pd.DataFrame(columns=["symbol", "entry_date", "exit_date", "pnl"])
    assert per_symbol_pnl(empty).empty
    assert per_year_pnl(empty).empty


def test_robustness_removes_top_symbol_correctly():
    # X has +50, Y has -10. Raw PF = 50/10 = 5. Without X: PF on Y alone
    # = 0/10 = 0 (no wins -> nan? actually 0/|-10| = 0.0 since wins=0).
    trades = _trades_df([
        ("X", "2020-01-01", "2020-01-05", 50.0),
        ("Y", "2020-02-01", "2020-02-05", -10.0),
    ])
    r = robustness(trades)
    assert r.top_symbol == "X"
    assert r.top_symbol_pnl == 50.0
    assert r.pf_raw == 5.0
    assert r.pf_ex_top_symbol == 0.0
    # Y is net negative
    assert r.n_negative_symbols == 1


def test_robustness_removes_best_year_correctly():
    # Year 2020 is the rainmaker, year 2021 is a loser. Without 2020,
    # we keep 2021's -3 only -> sum_wins=0, sum_losses=-3 -> pf 0.0.
    trades = _trades_df([
        ("X", "2020-01-01", "2020-01-05", 10.0),
        ("X", "2021-03-01", "2021-03-05", -3.0),
    ])
    r = robustness(trades)
    assert r.best_year == 2020
    assert r.best_year_pnl == 10.0
    assert r.pf_ex_best_year == 0.0


def test_concentration_flags_fire_when_threshold_crossed():
    # X carries 80 of 100 net (80% > 50%) -> one_symbol_carries True
    trades = _trades_df([
        ("X", "2020-01-01", "2020-01-05", 80.0),
        ("Y", "2020-02-01", "2020-02-05", 20.0),
    ])
    f = concentration_flags(trades, fraction_threshold=0.5)
    assert f["one_symbol_carries"] is True
    assert pytest.approx(f["top_symbol_share"], rel=1e-6) == 0.8


def test_binomial_p_value_extreme_cases():
    # All 10 wins out of 10 under null p=0.5 -> p = (0.5)^10 ~ 0.00098
    p = binomial_p_value(10, 10)
    assert 0.0009 < p < 0.0011
    # 0 trades -> p=1
    assert binomial_p_value(0, 0) == 1.0


def test_bootstrap_pf_ci_deterministic_under_seed():
    trades = _trades_df([
        ("X", "2020-01-01", "2020-01-05", 5.0),
        ("X", "2020-02-01", "2020-02-05", -3.0),
        ("X", "2020-03-01", "2020-03-05", 7.0),
        ("X", "2020-04-01", "2020-04-05", -2.0),
    ])
    a = bootstrap_pf_ci(trades, n_resamples=200, seed=42)
    b = bootstrap_pf_ci(trades, n_resamples=200, seed=42)
    assert a == b   # same seed -> identical
