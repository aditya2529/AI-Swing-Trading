"""SQLite paper-trading ledger.

Persists the live paper-trading state across daily EOD cron runs:
positions (the book), trades (the closed-out tape), equity_curve
(per-EOD MTM), and runs (idempotency lock + per-run metadata).

All four tables live in a single SQLite file (``paper_ledger.db`` by
default) so a single timestamped pre-run backup captures the entire
state. Restore = ``Copy-Item`` of the backup over ``paper_ledger.db``.
LAW 7 applies — never act without a fresh backup.

The schema is deliberately minimal and column-explicit; nothing here
mirrors the backtest harness's transient ``Book``/``Position``
classes. The mapping happens in ``live/eod_runner.py`` so the ledger
stays a dumb persistence layer.

Idempotency
===========
The ``runs`` table is keyed by ``run_date`` (the IST market date the
run was FOR). A second run on the same date sees an existing row and
exits without changes — see ``acquire_run_lock``.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date as date_cls
from pathlib import Path


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS positions (
    symbol         TEXT PRIMARY KEY,
    entry_date     TEXT NOT NULL,
    entry_price    REAL NOT NULL,
    shares         INTEGER NOT NULL,
    stop           REAL NOT NULL,
    risk_per_share REAL NOT NULL,
    cost_basis     REAL NOT NULL,
    bars_held      INTEGER NOT NULL DEFAULT 0,
    highest_high   REAL NOT NULL DEFAULT 0.0,
    highest_close  REAL NOT NULL DEFAULT 0.0,
    last_close     REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT NOT NULL,
    entry_date    TEXT NOT NULL,
    exit_date     TEXT NOT NULL,
    entry_price   REAL NOT NULL,
    exit_price    REAL NOT NULL,
    shares        INTEGER NOT NULL,
    pnl           REAL NOT NULL,
    return_pct    REAL NOT NULL,
    bars_held     INTEGER NOT NULL,
    exit_reason   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_exit_date
    ON trades (exit_date);

CREATE TABLE IF NOT EXISTS equity_curve (
    run_date  TEXT PRIMARY KEY,
    equity    REAL NOT NULL,
    cash      REAL NOT NULL,
    mtm       REAL NOT NULL
);

-- Idempotency + audit log. Keyed by run_date so a second invocation
-- on the same market date detects an existing row.
CREATE TABLE IF NOT EXISTS runs (
    run_date       TEXT PRIMARY KEY,
    run_at_utc     TEXT NOT NULL,        -- ISO-8601 of when the run actually executed
    status         TEXT NOT NULL,        -- 'ok' | 'error' | 'no_op'
    n_orders       INTEGER NOT NULL DEFAULT 0,
    equity         REAL,
    cash           REAL,
    error_message  TEXT
);

-- Cash balance (single row, id=1). Treated as a singleton for
-- simplicity — paper-trading is single-account.
CREATE TABLE IF NOT EXISTS cash_state (
    id     INTEGER PRIMARY KEY CHECK (id = 1),
    cash   REAL NOT NULL
);
"""


@dataclass
class StoredPosition:
    symbol: str
    entry_date: str           # ISO date
    entry_price: float
    shares: int
    stop: float
    risk_per_share: float
    cost_basis: float
    bars_held: int
    highest_high: float
    highest_close: float
    last_close: float


@dataclass
class RunRecord:
    run_date: str             # ISO IST market date
    run_at_utc: str
    status: str               # 'ok' | 'error' | 'no_op'
    n_orders: int
    equity: float | None
    cash: float | None
    error_message: str | None


# ── Connection helpers ─────────────────────────────────────────────────


@contextmanager
def get_connection(db_path: str | Path):
    """Per-call SQLite connection. Commits on clean exit, rolls back
    on exception. Same defensive pattern as data/database.py."""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_ledger(db_path: str | Path, *, initial_capital: float | None = None
                 ) -> None:
    """Create the schema if it doesn't exist. If ``initial_capital`` is
    provided AND the cash row is empty, seed it. Re-running with the
    same path is idempotent (does NOT reset existing state)."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with get_connection(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA_SQL)
        if initial_capital is not None:
            row = conn.execute("SELECT cash FROM cash_state WHERE id=1"
                                ).fetchone()
            if row is None:
                conn.execute("INSERT INTO cash_state (id, cash) VALUES "
                              "(1, ?)", (float(initial_capital),))


# ── Cash ───────────────────────────────────────────────────────────────


def get_cash(db_path: str | Path) -> float:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT cash FROM cash_state WHERE id=1"
                            ).fetchone()
        return float(row["cash"]) if row else 0.0


def set_cash(db_path: str | Path, cash: float) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO cash_state (id, cash) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET cash=excluded.cash",
            (float(cash),))


# ── Positions ──────────────────────────────────────────────────────────


def load_positions(db_path: str | Path) -> dict[str, StoredPosition]:
    out: dict[str, StoredPosition] = {}
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM positions").fetchall()
        for r in rows:
            out[r["symbol"]] = StoredPosition(
                symbol=r["symbol"], entry_date=r["entry_date"],
                entry_price=float(r["entry_price"]),
                shares=int(r["shares"]),
                stop=float(r["stop"]),
                risk_per_share=float(r["risk_per_share"]),
                cost_basis=float(r["cost_basis"]),
                bars_held=int(r["bars_held"]),
                highest_high=float(r["highest_high"]),
                highest_close=float(r["highest_close"]),
                last_close=float(r["last_close"]),
            )
    return out


def upsert_position(db_path: str | Path, pos: StoredPosition) -> None:
    with get_connection(db_path) as conn:
        conn.execute("""
            INSERT INTO positions (symbol, entry_date, entry_price, shares,
                                    stop, risk_per_share, cost_basis,
                                    bars_held, highest_high, highest_close,
                                    last_close)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol) DO UPDATE SET
                entry_date=excluded.entry_date,
                entry_price=excluded.entry_price,
                shares=excluded.shares,
                stop=excluded.stop,
                risk_per_share=excluded.risk_per_share,
                cost_basis=excluded.cost_basis,
                bars_held=excluded.bars_held,
                highest_high=excluded.highest_high,
                highest_close=excluded.highest_close,
                last_close=excluded.last_close
        """, (
            pos.symbol, pos.entry_date, pos.entry_price, pos.shares,
            pos.stop, pos.risk_per_share, pos.cost_basis,
            pos.bars_held, pos.highest_high, pos.highest_close,
            pos.last_close,
        ))


def delete_position(db_path: str | Path, symbol: str) -> None:
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM positions WHERE symbol=?", (symbol,))


# ── Trades ─────────────────────────────────────────────────────────────


def append_trade(db_path: str | Path, *,
                  symbol: str, entry_date: str, exit_date: str,
                  entry_price: float, exit_price: float, shares: int,
                  pnl: float, return_pct: float, bars_held: int,
                  exit_reason: str) -> None:
    with get_connection(db_path) as conn:
        conn.execute("""
            INSERT INTO trades (symbol, entry_date, exit_date, entry_price,
                                  exit_price, shares, pnl, return_pct,
                                  bars_held, exit_reason)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (symbol, entry_date, exit_date, entry_price, exit_price,
                shares, pnl, return_pct, bars_held, exit_reason))


def count_trades(db_path: str | Path) -> int:
    with get_connection(db_path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM trades")
                    .fetchone()[0])


# ── Equity curve ───────────────────────────────────────────────────────


def upsert_equity(db_path: str | Path, run_date: str, *,
                   equity: float, cash: float, mtm: float) -> None:
    with get_connection(db_path) as conn:
        conn.execute("""
            INSERT INTO equity_curve (run_date, equity, cash, mtm)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(run_date) DO UPDATE SET
                equity=excluded.equity,
                cash=excluded.cash,
                mtm=excluded.mtm
        """, (run_date, equity, cash, mtm))


# ── Idempotency: runs table ───────────────────────────────────────────


def get_run(db_path: str | Path, run_date: str) -> RunRecord | None:
    with get_connection(db_path) as conn:
        r = conn.execute("SELECT * FROM runs WHERE run_date=?",
                          (run_date,)).fetchone()
        if r is None:
            return None
        return RunRecord(
            run_date=r["run_date"], run_at_utc=r["run_at_utc"],
            status=r["status"], n_orders=int(r["n_orders"]),
            equity=float(r["equity"]) if r["equity"] is not None else None,
            cash=float(r["cash"]) if r["cash"] is not None else None,
            error_message=r["error_message"],
        )


def upsert_run(db_path: str | Path, rec: RunRecord) -> None:
    with get_connection(db_path) as conn:
        conn.execute("""
            INSERT INTO runs (run_date, run_at_utc, status, n_orders,
                              equity, cash, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_date) DO UPDATE SET
                run_at_utc=excluded.run_at_utc,
                status=excluded.status,
                n_orders=excluded.n_orders,
                equity=excluded.equity,
                cash=excluded.cash,
                error_message=excluded.error_message
        """, (rec.run_date, rec.run_at_utc, rec.status, rec.n_orders,
                rec.equity, rec.cash, rec.error_message))


def last_successful_run_date(db_path: str | Path) -> str | None:
    """The most recent run_date whose status is 'ok' or 'no_op'.
    Used by the health-check cron to detect missing daily runs."""
    with get_connection(db_path) as conn:
        r = conn.execute(
            "SELECT run_date FROM runs WHERE status IN ('ok','no_op') "
            "ORDER BY run_date DESC LIMIT 1"
        ).fetchone()
        return r["run_date"] if r else None
