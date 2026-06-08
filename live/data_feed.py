"""Daily price feed.

Fetches recent daily bars for the trading universe from yfinance and
UPSERTS them into ``market_data.db`` so the EOD runner always has the
latest (today's) bar. Idempotent — safe to re-run. Run in the EVENING,
after NSE daily bars have finalised on yfinance.

This is the piece that was missing at first deploy: the DB held a static
snapshot (latest 2026-06-03) and nothing refreshed it, so the runner's
bar-finality guard correctly refused to trade on stale data every day.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB = PROJECT_ROOT / "market_data.db"

_UPSERT = (
    "INSERT INTO ohlcv (symbol, market, resolution, time, open, high, low, "
    "close, volume) VALUES (?, 'NSE', '1d', ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(symbol, resolution, time) DO UPDATE SET "
    "open=excluded.open, high=excluded.high, low=excluded.low, "
    "close=excluded.close, volume=excluded.volume")


def _universe() -> list[str]:
    sys.path.insert(0, str(PROJECT_ROOT))
    from data.universe import SMID_UNIVERSE
    return list(SMID_UNIVERSE)


def fetch_and_store(period: str = "7d", chunk: int = 20,
                    pause: float = 1.5, retries: int = 4) -> tuple[int, int, str | None]:
    """Fetch `period` of daily bars in SMALL CHUNKS (yfinance rate-limits
    large bursts) and upsert. Returns (n_symbols_updated, n_rows, latest)."""
    import time
    import yfinance as yf

    syms = _universe()
    con = sqlite3.connect(str(DB))
    n_syms = n_rows = 0
    latest: str | None = None
    try:
        for i in range(0, len(syms), chunk):
            batch = syms[i:i + chunk]
            df = None
            for attempt in range(retries):
                try:
                    df = yf.download(batch, period=period, auto_adjust=True,
                                     group_by="ticker", threads=False, progress=False)
                    if df is not None and not df.empty:
                        break
                except Exception:  # noqa: BLE001
                    df = None
                time.sleep(pause * (attempt + 2))   # exponential-ish backoff
            if df is None or df.empty:
                print(f"[data_feed] chunk {i // chunk} got nothing (rate limit?)")
                time.sleep(pause)
                continue
            multi = hasattr(df.columns, "levels")
            for sym in batch:
                try:
                    sub = df[sym] if multi else df
                except Exception:  # noqa: BLE001
                    continue
                if sub is None or sub.empty:
                    continue
                sub = sub.dropna(subset=["Close"])
                if sub.empty:
                    continue
                rows = []
                for ts, r in sub.iterrows():
                    t = pd.Timestamp(ts).strftime("%Y-%m-%d 00:00:00")
                    vol = int(r["Volume"]) if pd.notna(r["Volume"]) else 0
                    rows.append((sym, t, float(r["Open"]), float(r["High"]),
                                 float(r["Low"]), float(r["Close"]), vol))
                    if latest is None or t > latest:
                        latest = t
                con.executemany(_UPSERT, rows)
                n_syms += 1
                n_rows += len(rows)
            con.commit()
            print(f"[data_feed] chunk {i // chunk}: cumulative {n_syms} symbols, latest {latest}")
            time.sleep(pause)
    finally:
        con.close()
    print(f"[data_feed] DONE — upserted {n_rows} rows across {n_syms}/{len(syms)} "
          f"symbols; latest bar {latest}")
    return (n_syms, n_rows, latest)


if __name__ == "__main__":   # pragma: no cover
    n, _, latest = fetch_and_store()
    # Non-zero exit if we got essentially nothing, so the cron log is honest.
    sys.exit(0 if n > 0 else 1)
