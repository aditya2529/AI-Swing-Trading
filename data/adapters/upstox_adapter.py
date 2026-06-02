"""Upstox API v2 adapter — daily OHLCV for the Phase 0 backfill.

OHLCV fetches hit REAL production market data regardless of ``UPSTOX_ENV``
(per Upstox's 2026-05-24 addendum: only ORDER endpoints are sandbox-
simulated). The adapter is therefore safe to call with sandbox keys —
the candles are the same exchange data yfinance returns.

Scope: DAILY (and weekly/monthly) only. The intraday v3 minute path from
the predecessor project is intentionally dropped — this is a daily swing
system. Symbol→instrument-key resolution uses the static 25-symbol map in
``upstox_symbol_map`` (no network instrument-master download).

Token resolution: read ``UPSTOX_ENV`` from .env on every call, then pick
the matching ``UPSTOX_{ENV}_ACCESS_TOKEN``. No legacy single-key model.

NOTE (validate in Step D): the timestamp normalisation below assumes
Upstox returns tz-aware IST ISO strings for daily candles. Confirm the
exact shape against a live response during the backfill and reconcile the
resulting date labels against yfinance / the existing daily history.
"""
from __future__ import annotations

import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
from dotenv import dotenv_values

from data.adapters import upstox_symbol_map
from data.adapters.base import DataAdapter


UPSTOX_BASE_URL = "https://api.upstox.com/v2"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_ENV_PATH = _PROJECT_ROOT / ".env"
_TIMEOUT_SECONDS = 30

# Upstox historical-candle interval keys (v2).
_INTERVAL_MAP = {
    "1d": "day", "day": "day",
    "1w": "week", "week": "week",
    "1mo": "month", "month": "month",
}


def _active_access_token(env_path: str | None = None) -> str | None:
    """Return the active env's access token, or None if missing/invalid.

    Not gated by any kill-switch — OHLCV is read-only data, not orders.
    """
    env = dotenv_values(str(env_path or _DEFAULT_ENV_PATH))
    upstox_env = env.get("UPSTOX_ENV")
    if upstox_env not in ("sandbox", "prod"):
        return None
    token = env.get(f"UPSTOX_{upstox_env.upper()}_ACCESS_TOKEN")
    return token.strip() if token and token.strip() else None


class UpstoxRateLimiter:
    """Token-bucket triad for Upstox limits (per-sec / per-min / per-30min),
    enforced concurrently. A 25-symbol daily backfill is one request per
    symbol, so this is mostly belt-and-suspenders, but it keeps repeated
    re-backfills polite to the API."""

    def __init__(self, per_sec: int = 25, per_min: int = 250,
                 per_30min: int = 1000) -> None:
        self._buckets = [
            (per_sec, 1.0, deque()),
            (per_min, 60.0, deque()),
            (per_30min, 30.0 * 60.0, deque()),
        ]

    def acquire(self) -> None:
        while True:
            now = time.monotonic()
            longest_wait = 0.0
            for cap, window, dq in self._buckets:
                while dq and now - dq[0] >= window:
                    dq.popleft()
                if len(dq) >= cap:
                    wait_secs = window - (now - dq[0])
                    longest_wait = max(longest_wait, wait_secs)
            if longest_wait <= 0:
                now = time.monotonic()
                for _, _, dq in self._buckets:
                    dq.append(now)
                return
            time.sleep(longest_wait)


class UpstoxAdapter(DataAdapter):
    """Read-only daily OHLCV via Upstox v2 ``/historical-candle``."""

    _rate_limiter = UpstoxRateLimiter()

    def fetch_ohlcv(self, symbol: str, years: int = 10,
                    resolution: str = "1d") -> pd.DataFrame:
        token = _active_access_token()
        if not token:
            raise EnvironmentError(
                "No active Upstox access token in .env. Set UPSTOX_ENV + the "
                "matching UPSTOX_{ENV}_ACCESS_TOKEN, or use --source yfinance."
            )

        instrument_key = upstox_symbol_map.lookup(symbol)

        interval = _INTERVAL_MAP.get(resolution)
        if interval is None:
            raise ValueError(
                f"Unsupported resolution {resolution!r}. This daily adapter "
                f"supports {sorted(set(_INTERVAL_MAP))}."
            )

        to_date = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now()
                     - timedelta(days=int(years * 365.25))).strftime("%Y-%m-%d")
        url = (f"{UPSTOX_BASE_URL}/historical-candle/"
               f"{instrument_key}/{interval}/{to_date}/{from_date}")

        self._rate_limiter.acquire()
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/json"},
            timeout=_TIMEOUT_SECONDS,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Upstox OHLCV fetch failed: HTTP {resp.status_code}: "
                f"{resp.text[:300]}"
            )

        candles = (resp.json().get("data") or {}).get("candles") or []
        if not candles:
            return pd.DataFrame(
                columns=["time", "open", "high", "low", "close", "volume"])

        # Upstox row order: [timestamp, open, high, low, close, volume, oi].
        df = pd.DataFrame(candles, columns=[
            "time", "open", "high", "low", "close", "volume", "oi",
        ]).drop(columns=["oi"])

        # Match the adapter contract: `time` is a tz-naive COLUMN (not the
        # index). Upstox daily timestamps are tz-aware IST; tz_localize(None)
        # strips the zone while preserving the IST wall-clock date label.
        ts = pd.to_datetime(df["time"], errors="coerce")
        if getattr(ts.dt, "tz", None) is not None:
            ts = ts.dt.tz_localize(None)
        df["time"] = ts
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.sort_values("time").reset_index(drop=True)

    def is_available(self) -> bool:
        """True iff an active access token is present in .env."""
        return bool(_active_access_token())
