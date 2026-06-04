"""Tests for the yfinance adapter's retry-with-backoff wrapper.

Motivation: during the MOM-1 backfill, a single yfinance call for
GMRAIRPORT.NS raised ``AttributeError: 'int' object has no attribute
'date'`` from inside yfinance's internals. Re-running the exact same
call moments later succeeded with full 12-year history. The failure
is non-deterministic (network / internal yfinance state) and cannot
be reproduced point-blank in a test.

The durable fix is retry-with-backoff at the adapter boundary, so the
same class of transient flake on future backfills is absorbed instead
of dropping a real symbol. These tests pin the retry contract:

  1. A transient raise on the first call is recovered on retry.
  2. A genuinely-dead symbol (every attempt raises / empty) still
     bubbles up so the caller correctly records it as failed.
  3. The wrapper does NOT retry a successful call (no wasted hits).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

import data.adapters.yfinance_adapter as ya


def _ok_frame() -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=3, freq="D", name="Date")
    return pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0],
            "High": [101.0, 102.0, 103.0],
            "Low":  [99.0, 100.0, 101.0],
            "Close": [100.5, 101.5, 102.5],
            "Volume": [1000, 1100, 1200],
        },
        index=idx,
    )


def test_retry_recovers_from_one_transient_attributeerror(monkeypatch):
    """First call raises the EXACT yfinance failure mode we saw on
    GMRAIRPORT.NS; second call returns clean data. The wrapper must
    surface the second-call result without raising."""
    ticker = MagicMock()
    ticker.history.side_effect = [
        AttributeError("'int' object has no attribute 'date'"),
        _ok_frame(),
    ]
    # Zero out the backoff to keep the test instantaneous.
    monkeypatch.setattr(ya, "_RETRY_BACKOFF_SECS", 0.0)

    raw = ya._history_with_retries(ticker, start="2024-01-01",
                                    end="2024-01-04", interval="1d")
    assert not raw.empty
    assert len(raw) == 3
    assert ticker.history.call_count == 2   # retried exactly once


def test_retry_exhausts_and_raises_when_every_attempt_fails(monkeypatch):
    """A genuinely-dead symbol (every call raises) MUST still bubble up
    so the MOM-1 / future backfill loops mark it failed and skip it.
    The wrapper is not a swallow."""
    ticker = MagicMock()
    ticker.history.side_effect = AttributeError(
        "'int' object has no attribute 'date'"
    )
    monkeypatch.setattr(ya, "_RETRY_BACKOFF_SECS", 0.0)

    with pytest.raises(AttributeError):
        ya._history_with_retries(ticker, start="2024-01-01",
                                  end="2024-01-04", interval="1d")
    assert ticker.history.call_count == ya._RETRY_ATTEMPTS


def test_retry_treats_persistent_empty_frame_as_dead_symbol(monkeypatch):
    """yfinance returns an empty DataFrame (not an exception) for
    delisted / unknown tickers. The wrapper must retry the empty
    response too (in case it was a transient hiccup) but eventually
    return the empty frame so the caller's standard empty-check
    raises a clean ValueError."""
    ticker = MagicMock()
    ticker.history.return_value = pd.DataFrame()   # always empty
    monkeypatch.setattr(ya, "_RETRY_BACKOFF_SECS", 0.0)

    raw = ya._history_with_retries(ticker, start="2024-01-01",
                                    end="2024-01-04", interval="1d")
    assert raw.empty
    # All attempts used — wrapper didn't give up early on the empty.
    assert ticker.history.call_count == ya._RETRY_ATTEMPTS


def test_retry_does_not_retry_on_first_success(monkeypatch):
    """A clean first call must NOT trigger a retry — that would double
    every backfill request."""
    ticker = MagicMock()
    ticker.history.return_value = _ok_frame()
    monkeypatch.setattr(ya, "_RETRY_BACKOFF_SECS", 0.0)

    raw = ya._history_with_retries(ticker, start="2024-01-01",
                                    end="2024-01-04", interval="1d")
    assert not raw.empty
    assert ticker.history.call_count == 1
