"""Validator tests — the daily-vs-intraday outlier policy.

A >4σ DAILY move is usually a real event (crash/split/gap) and must be
KEPT; an intraday spike is a bad tick and is smoothed. Locking this stops
a future refactor from silently re-erasing the fallen names' crashes.
"""
from __future__ import annotations

import pandas as pd

from data.validator import validate_and_clean


def _flat_with_crash(crash_idx: int = 20, crash_value: float = 50.0):
    """40 bars flat at 100.0 that step DOWN to ``crash_value`` at
    ``crash_idx`` and stay there — a single, clean >4σ down-move (a
    crash-and-recover would inflate std and split into two weaker ones)."""
    idx = pd.bdate_range("2020-01-01", periods=40)
    close = [100.0] * crash_idx + [crash_value] * (len(idx) - crash_idx)
    df = pd.DataFrame({
        "time": idx, "open": close, "high": close, "low": close,
        "close": close, "volume": [1000] * len(idx),
    })
    return df, idx[crash_idx], crash_value


def test_daily_crash_is_preserved():
    df, crash_time, crash_value = _flat_with_crash()
    out = validate_and_clean(df.copy(), "YESBANK.NS", resolution="1d")
    row = out.loc[out["time"] == crash_time]
    assert not row.empty
    assert abs(float(row["close"].iloc[0]) - crash_value) < 1e-6   # KEPT


def test_intraday_outlier_is_smoothed():
    df, crash_time, crash_value = _flat_with_crash()
    out = validate_and_clean(df.copy(), "X", resolution="5m")
    row = out.loc[out["time"] == crash_time]
    assert not row.empty
    assert abs(float(row["close"].iloc[0]) - crash_value) > 1e-6   # replaced


def test_daily_timestamps_normalised_to_midnight():
    idx = pd.to_datetime(["2016-06-03 09:15:00", "2016-06-06 00:00:00"])
    df = pd.DataFrame({
        "time": idx, "open": [1.0, 2.0], "high": [1.0, 2.0], "low": [1.0, 2.0],
        "close": [1.0, 2.0], "volume": [10, 20],
    })
    out = validate_and_clean(df, "X", resolution="1d")
    assert (out["time"].dt.normalize() == out["time"]).all()        # all at midnight
