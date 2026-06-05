"""Tests for the deploy-hardening modules.

Covered:
  - Paper-ledger schema + CRUD (positions / cash / trades / equity / runs).
  - Idempotency: a second run_eod call for the same date is a NO-OP
    (no new orders, no trades, no equity row mutation beyond MTM).
  - Safety guard: hard-fails when PAPER_MODE != "1" or a forbidden
    env var is set.
  - Bar-finality check.
  - End-to-end run_eod with a mocked Telegram + mocked strategy +
    synthetic universe.
  - Health-check identifies a missing daily run.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import date as date_cls, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from live import paper_ledger as ledger
from live import telegram as tg
from live.eod_runner import (
    RunnerPaths, bar_finality_check, mark_to_market, reconstruct_book,
    run_eod,
)
from live.safety_guard import SafetyGuardError, assert_paper_mode


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def tmp_paths(tmp_path: Path) -> RunnerPaths:
    """A self-contained tmp project layout: empty market DB, no
    paper-ledger yet, no backups."""
    market_db = tmp_path / "market_data.db"
    # Seed an empty market DB with the ohlcv table.
    with sqlite3.connect(market_db) as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS ohlcv (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT 'NSE',
                resolution TEXT NOT NULL DEFAULT '1d',
                time TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL,
                volume INTEGER,
                UNIQUE(symbol, resolution, time)
            );
        """)
    return RunnerPaths(
        project_root=tmp_path,
        market_data_db=market_db,
        paper_ledger_db=tmp_path / "paper_ledger.db",
        backups_dir=tmp_path / "backups",
        logs_dir=tmp_path / "logs",
    )


def _seed_market_data(paths: RunnerPaths, symbol: str, dates: list[str],
                        prices: list[float] | None = None):
    """Insert OHLCV rows for ``symbol`` on each ISO date."""
    if prices is None:
        prices = [100.0 + i for i in range(len(dates))]
    rows = [
        (symbol, "NSE", "1d", d, p, p + 0.5, p - 0.5, p, 1_000_000)
        for d, p in zip(dates, prices)
    ]
    with sqlite3.connect(paths.market_data_db) as con:
        con.executemany(
            "INSERT OR REPLACE INTO ohlcv "
            "(symbol, market, resolution, time, open, high, low, "
            " close, volume) VALUES (?,?,?,?,?,?,?,?,?)",
            rows)


@pytest.fixture
def paper_env(monkeypatch):
    """Enable paper mode + suppress real Telegram during tests."""
    monkeypatch.setenv("PAPER_MODE", "1")
    for var in ("UPSTOX_ACCESS_TOKEN", "ZERODHA_API_KEY",
                  "BROKER_LIVE_KEY"):
        monkeypatch.delenv(var, raising=False)
    # Stub out Telegram so tests are pure.
    sent: list[tuple] = []
    monkeypatch.setattr(tg, "send",
                          lambda msg, **kw: sent.append(("msg", msg)) or True)
    monkeypatch.setattr(tg, "send_success",
                          lambda **kw: sent.append(("ok", kw)) or True)
    monkeypatch.setattr(tg, "send_error",
                          lambda **kw: sent.append(("err", kw)) or True)
    monkeypatch.setattr(tg, "send_health_alert",
                          lambda **kw: sent.append(("health", kw)) or True)
    return sent


# ── Safety guard ──────────────────────────────────────────────────────


def test_safety_guard_requires_paper_mode_one(monkeypatch):
    monkeypatch.delenv("PAPER_MODE", raising=False)
    with pytest.raises(SafetyGuardError, match="PAPER_MODE"):
        assert_paper_mode()


def test_safety_guard_rejects_broker_credential_env(monkeypatch):
    monkeypatch.setenv("PAPER_MODE", "1")
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "live-token")
    with pytest.raises(SafetyGuardError, match="UPSTOX_ACCESS_TOKEN"):
        assert_paper_mode()


def test_safety_guard_passes_when_paper_mode_set_no_creds(monkeypatch):
    monkeypatch.setenv("PAPER_MODE", "1")
    for v in ("UPSTOX_ACCESS_TOKEN", "ZERODHA_API_KEY",
                "BROKER_LIVE_KEY"):
        monkeypatch.delenv(v, raising=False)
    assert_paper_mode() is None    # raises nothing


# ── Paper ledger CRUD ─────────────────────────────────────────────────


def test_ledger_init_creates_schema_and_seeds_cash(tmp_paths):
    ledger.init_ledger(tmp_paths.paper_ledger_db, initial_capital=500_000.0)
    assert tmp_paths.paper_ledger_db.exists()
    assert ledger.get_cash(tmp_paths.paper_ledger_db) == 500_000.0


def test_ledger_init_is_idempotent_does_not_reset_cash(tmp_paths):
    ledger.init_ledger(tmp_paths.paper_ledger_db, initial_capital=500_000.0)
    ledger.set_cash(tmp_paths.paper_ledger_db, 250_000.0)
    # Re-init shouldn't reset cash.
    ledger.init_ledger(tmp_paths.paper_ledger_db, initial_capital=500_000.0)
    assert ledger.get_cash(tmp_paths.paper_ledger_db) == 250_000.0


def test_ledger_position_upsert_and_load(tmp_paths):
    ledger.init_ledger(tmp_paths.paper_ledger_db, initial_capital=500_000.0)
    pos = ledger.StoredPosition(
        symbol="X.NS", entry_date="2026-06-01", entry_price=100.0,
        shares=10, stop=90.0, risk_per_share=10.0, cost_basis=1003.0,
        bars_held=2, highest_high=105.0, highest_close=103.0,
        last_close=102.0)
    ledger.upsert_position(tmp_paths.paper_ledger_db, pos)
    loaded = ledger.load_positions(tmp_paths.paper_ledger_db)
    assert "X.NS" in loaded
    assert loaded["X.NS"] == pos
    # Update.
    pos2 = ledger.StoredPosition(**{**pos.__dict__, "shares": 15})
    ledger.upsert_position(tmp_paths.paper_ledger_db, pos2)
    loaded2 = ledger.load_positions(tmp_paths.paper_ledger_db)
    assert loaded2["X.NS"].shares == 15


def test_ledger_delete_position(tmp_paths):
    ledger.init_ledger(tmp_paths.paper_ledger_db, initial_capital=500_000.0)
    pos = ledger.StoredPosition(
        symbol="X.NS", entry_date="2026-06-01", entry_price=100.0,
        shares=10, stop=90.0, risk_per_share=10.0, cost_basis=1003.0,
        bars_held=2, highest_high=105.0, highest_close=103.0,
        last_close=102.0)
    ledger.upsert_position(tmp_paths.paper_ledger_db, pos)
    ledger.delete_position(tmp_paths.paper_ledger_db, "X.NS")
    assert ledger.load_positions(tmp_paths.paper_ledger_db) == {}


def test_ledger_runs_table_idempotency_lock(tmp_paths):
    ledger.init_ledger(tmp_paths.paper_ledger_db, initial_capital=500_000.0)
    rec = ledger.RunRecord(
        run_date="2026-06-05",
        run_at_utc=datetime.now(timezone.utc).isoformat(),
        status="ok", n_orders=3, equity=510_000.0, cash=200_000.0,
        error_message=None)
    ledger.upsert_run(tmp_paths.paper_ledger_db, rec)
    got = ledger.get_run(tmp_paths.paper_ledger_db, "2026-06-05")
    assert got == rec
    # Re-upsert the same date is allowed (e.g. retried success).
    rec2 = ledger.RunRecord(**{**rec.__dict__, "status": "no_op",
                                  "n_orders": 0})
    ledger.upsert_run(tmp_paths.paper_ledger_db, rec2)
    got2 = ledger.get_run(tmp_paths.paper_ledger_db, "2026-06-05")
    assert got2.status == "no_op"
    # last_successful_run_date sees both ok and no_op.
    assert ledger.last_successful_run_date(
        tmp_paths.paper_ledger_db) == "2026-06-05"


# ── Bar finality ──────────────────────────────────────────────────────


def test_bar_finality_passes_when_majority_has_today(tmp_paths):
    target = pd.Timestamp("2026-06-05")
    idx = pd.bdate_range("2026-05-01", target)
    data = {
        "A": pd.DataFrame({"close": np.arange(len(idx))}, index=idx),
        "B": pd.DataFrame({"close": np.arange(len(idx))}, index=idx),
        "C": pd.DataFrame({"close": np.arange(len(idx) - 1)},
                            index=idx[:-1]),   # missing today
    }
    ok, fraction, n_with = bar_finality_check(data, target_date=target)
    assert ok
    assert n_with == 2
    assert fraction == pytest.approx(2 / 3)


def test_bar_finality_fails_when_most_missing(tmp_paths):
    target = pd.Timestamp("2026-06-05")
    idx = pd.bdate_range("2026-05-01", target)
    data = {
        "A": pd.DataFrame({"close": np.arange(len(idx))}, index=idx),
        "B": pd.DataFrame({"close": np.arange(len(idx) - 1)},
                            index=idx[:-1]),
        "C": pd.DataFrame({"close": np.arange(len(idx) - 1)},
                            index=idx[:-1]),
    }
    ok, fraction, n_with = bar_finality_check(data, target_date=target)
    assert not ok


def test_bar_finality_empty_data_fails(tmp_paths):
    ok, fraction, n_with = bar_finality_check(
        {}, target_date=pd.Timestamp("2026-06-05"))
    assert not ok


# ── End-to-end EOD runner ─────────────────────────────────────────────


class _NoOpStrategy:
    """Decides nothing — proves the pipeline runs even when the
    strategy emits zero orders (most days)."""
    def decide(self, view, book):
        return []


def test_run_eod_succeeds_with_empty_strategy(tmp_paths, paper_env):
    target = "2026-06-05"
    dates = [d.isoformat()
              for d in pd.bdate_range("2026-05-01", target).date]
    for sym in ("A.NS", "B.NS", "C.NS"):
        _seed_market_data(tmp_paths, sym, dates)

    rc = run_eod(
        strategy_factory=_NoOpStrategy,
        universe=["A.NS", "B.NS", "C.NS"],
        paths=tmp_paths,
        today_override=target)
    assert rc == 0
    rec = ledger.get_run(tmp_paths.paper_ledger_db, target)
    assert rec is not None
    assert rec.status == "ok"
    assert rec.n_orders == 0
    # Telegram success was sent.
    assert any(kind == "ok" for kind, _ in paper_env)


def test_run_eod_idempotent_second_run_is_no_op(tmp_paths, paper_env):
    target = "2026-06-05"
    dates = [d.isoformat()
              for d in pd.bdate_range("2026-05-01", target).date]
    for sym in ("A.NS",):
        _seed_market_data(tmp_paths, sym, dates)
    # First run.
    rc1 = run_eod(strategy_factory=_NoOpStrategy,
                    universe=["A.NS"], paths=tmp_paths,
                    today_override=target)
    assert rc1 == 0
    paper_env.clear()
    # Second run on the same date.
    rc2 = run_eod(strategy_factory=_NoOpStrategy,
                    universe=["A.NS"], paths=tmp_paths,
                    today_override=target)
    assert rc2 == 0
    rec = ledger.get_run(tmp_paths.paper_ledger_db, target)
    # Stays 'ok' (idempotency short-circuit doesn't update the row).
    assert rec.status == "ok"
    # A NO-OP message was sent.
    msgs = [m for kind, m in paper_env if kind == "msg"]
    assert any("NO-OP" in m for m in msgs)


def test_run_eod_aborts_on_stale_data_records_error(tmp_paths, paper_env):
    target = "2026-06-05"
    # Seed bars only through yesterday → finality check fails.
    yesterday = (pd.Timestamp(target) - pd.Timedelta(days=1)).date().isoformat()
    dates = [d.isoformat()
              for d in pd.bdate_range("2026-05-01", yesterday).date]
    for sym in ("A.NS", "B.NS"):
        _seed_market_data(tmp_paths, sym, dates)

    rc = run_eod(strategy_factory=_NoOpStrategy,
                  universe=["A.NS", "B.NS"], paths=tmp_paths,
                  today_override=target)
    assert rc == 1
    rec = ledger.get_run(tmp_paths.paper_ledger_db, target)
    assert rec.status == "error"
    assert "BAR FINALITY" in (rec.error_message or "")
    # Telegram error was sent.
    assert any(kind == "err" for kind, _ in paper_env)


def test_run_eod_safety_guard_failure_records_error(tmp_paths, monkeypatch,
                                                       paper_env):
    monkeypatch.delenv("PAPER_MODE", raising=False)
    rc = run_eod(strategy_factory=_NoOpStrategy,
                  universe=["A.NS"], paths=tmp_paths,
                  today_override="2026-06-05")
    assert rc == 1
    # An error record is in the runs table.
    rec = ledger.get_run(tmp_paths.paper_ledger_db, "2026-06-05")
    assert rec is not None
    assert rec.status == "error"
    assert "PAPER" in (rec.error_message or "").upper()


# ── Health check ──────────────────────────────────────────────────────


def test_health_check_alerts_when_no_run_today(tmp_paths, monkeypatch,
                                                  paper_env):
    """If there's no row for today and today is a market day, the
    health check sends an alert."""
    ledger.init_ledger(tmp_paths.paper_ledger_db, initial_capital=500_000.0)
    from live import health_check as hc
    monkeypatch.setattr(hc, "now_ist",
                          lambda: datetime(2026, 6, 5, 16, 30))   # Friday
    rc = hc.main(paths=tmp_paths)
    assert rc == 0
    assert any(kind == "health" for kind, _ in paper_env)


def test_health_check_silent_when_today_ran_ok(tmp_paths, monkeypatch,
                                                  paper_env):
    ledger.init_ledger(tmp_paths.paper_ledger_db, initial_capital=500_000.0)
    rec = ledger.RunRecord(
        run_date="2026-06-05",
        run_at_utc=datetime.now(timezone.utc).isoformat(),
        status="ok", n_orders=0, equity=500_000.0, cash=500_000.0,
        error_message=None)
    ledger.upsert_run(tmp_paths.paper_ledger_db, rec)
    from live import health_check as hc
    monkeypatch.setattr(hc, "now_ist",
                          lambda: datetime(2026, 6, 5, 16, 30))
    rc = hc.main(paths=tmp_paths)
    assert rc == 0
    # NO alert sent.
    assert not any(kind == "health" for kind, _ in paper_env)


def test_health_check_silent_on_weekend(tmp_paths, monkeypatch, paper_env):
    ledger.init_ledger(tmp_paths.paper_ledger_db, initial_capital=500_000.0)
    from live import health_check as hc
    monkeypatch.setattr(hc, "now_ist",
                          lambda: datetime(2026, 6, 6, 16, 30))   # Saturday
    rc = hc.main(paths=tmp_paths)
    assert rc == 0
    assert not any(kind == "health" for kind, _ in paper_env)
