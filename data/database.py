"""SQLite OHLCV store.

Schema is market/resolution-agnostic: one ``ohlcv`` table keyed by
(symbol, resolution, time). Daily swing bars use resolution='1d'.

Connection pattern (carried over from the intraday project, where it
fixed a real heap-corruption crash): open an isolated, per-call
``sqlite3.connect(check_same_thread=False)`` and never run
``PRAGMA journal_mode=WAL`` on the hot path. WAL is set once,
persistently, by ``init_db()``.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pandas as pd

from config import DB_PATH


@contextmanager
def get_connection():
    """Per-call SQLite connection: commits on clean exit, rolls back on
    exception, and ALWAYS closes (the bare ``with sqlite3.connect(...)``
    form on Python < 3.12 does not close, leaking shared C state)."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    # WAL is written into the DB header once here and survives across
    # processes — not re-run on every connect.
    with get_connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ohlcv (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol     TEXT NOT NULL,
                market     TEXT NOT NULL DEFAULT 'NSE',
                resolution TEXT NOT NULL DEFAULT '1d',
                time       TEXT NOT NULL,
                open       REAL,
                high       REAL,
                low        REAL,
                close      REAL,
                volume     INTEGER,
                UNIQUE(symbol, resolution, time)
            );
            CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_time
                ON ohlcv (symbol, resolution, time DESC);
            """
        )


def upsert_ohlcv(df: pd.DataFrame, symbol: str, market: str = "NSE",
                 resolution: str = "1d"):
    """Insert or replace OHLCV rows. ``df`` must have columns:
    time, open, high, low, close, volume."""
    rows = [
        (symbol, market, resolution, str(row.time), row.open, row.high,
         row.low, row.close, int(row.volume))
        for row in df.itertuples()
    ]
    with get_connection() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO ohlcv
               (symbol, market, resolution, time, open, high, low, close, volume)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            rows,
        )


def load_ohlcv(symbol: str, resolution: str = "1d",
               limit: int | None = None) -> pd.DataFrame:
    """Load bars for one symbol as a DataFrame indexed by ``time``
    (DatetimeIndex), ascending."""
    sql = """
        SELECT time, open, high, low, close, volume
        FROM ohlcv
        WHERE symbol = ? AND resolution = ?
        ORDER BY time ASC
    """
    params: list = [symbol, resolution]
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        df = pd.read_sql_query(sql, conn, params=params, parse_dates=["time"])
    finally:
        conn.close()
    df.set_index("time", inplace=True)
    return df


def list_symbols() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM ohlcv ORDER BY symbol"
        ).fetchall()
    return [r[0] for r in rows]


def list_tradeable_symbols(resolution: str = "1d") -> list[str]:
    """Tradeable symbols only — excludes macro indices (^NSEI, ^INDIAVIX)
    and filters by resolution."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM ohlcv WHERE resolution = ? ORDER BY symbol",
            (resolution,),
        ).fetchall()
    return [r[0] for r in rows if not r[0].startswith("^")]
