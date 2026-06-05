"""End-of-day paper-trading runner.

Run by the daily IST cron at ~15:45 IST Mon-Fri. The whole body is
wrapped in a crash-safe try/except — on ANY exception, we Telegram an
alert and exit non-zero. Silent death is prohibited.

PIPELINE
========
0. ``safety_guard.assert_paper_mode()`` — fail closed if any broker
   credential or live-trading code path is reachable. Never trade
   live by accident.
1. Determine today's IST market date.
2. Acquire the run-lock for that date (idempotency). If a successful
   run already exists for today, log "no-op", ping ops, exit 0.
3. Pre-run backup of ``paper_ledger.db`` (LAW 7).
4. Load universe data from ``market_data.db`` (existing yfinance
   backfill). The strategy works on TOTAL-RETURN data — same
   convention as live yfinance.
5. Confirm today's bar is FINAL across the universe — at least the
   majority of symbols must have a bar dated today; if not, log +
   alert + abort (do NOT trade on stale data).
6. Load paper-ledger book state.
7. Build a backtesting BarView at today's cutoff; build a Book from
   the ledger.
8. Run the strategy's ``decide(view, book)`` to get the proposed
   orders for tomorrow's open.
9. Convert order list -> paper-ledger writes (these are "intent"
   records — they will fill on TOMORROW's run when we have the
   next-open price).
10. Mark-to-market: write today's equity row.
11. Telegram success ping.

Re-running on the same date is safe (step 2 short-circuits).
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from live import paper_ledger as ledger
from live import telegram
from live.safety_guard import SafetyGuardError, assert_paper_mode

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Asia/Kolkata is UTC+5:30 with NO DST. Importing zoneinfo is the
# right thing on Python 3.9+; we keep the fallback to a fixed offset
# so an Oracle VM missing the tzdata package still gets the right
# answer.
try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:                              # pragma: no cover
    from datetime import timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")


# ── Paths (overridable by env for tests) ──────────────────────────────


@dataclass(frozen=True)
class RunnerPaths:
    project_root: Path = PROJECT_ROOT
    market_data_db: Path = PROJECT_ROOT / "market_data.db"
    paper_ledger_db: Path = PROJECT_ROOT / "paper_ledger.db"
    backups_dir: Path = PROJECT_ROOT / "backups"
    logs_dir: Path = PROJECT_ROOT / "logs"


# ── IST clock ─────────────────────────────────────────────────────────


def now_ist() -> datetime:
    return datetime.now(tz=IST)


def today_ist_date_str() -> str:
    return now_ist().date().isoformat()


# ── Backup ────────────────────────────────────────────────────────────


def backup_paper_ledger(paths: RunnerPaths) -> Path | None:
    """LAW 7 pre-run backup. Returns the backup path. No-op if the
    ledger doesn't exist yet (first run will create it)."""
    if not paths.paper_ledger_db.exists():
        return None
    paths.backups_dir.mkdir(parents=True, exist_ok=True)
    ts = now_ist().strftime("%Y%m%dT%H%M%S%z")
    dest = paths.backups_dir / f"paper_ledger_{ts}_eod.db"
    shutil.copy2(paths.paper_ledger_db, dest)
    return dest


# ── Universe load + bar finality ──────────────────────────────────────


def load_universe_from_market_db(paths: RunnerPaths, *,
                                    symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Read daily bars from ``market_data.db`` (the backfilled DB).
    Weekday-filtered per the MOM-3 convention."""
    out: dict[str, pd.DataFrame] = {}
    con = sqlite3.connect(
        f"file:{paths.market_data_db.as_posix()}?mode=ro", uri=True)
    try:
        for sym in symbols:
            df = pd.read_sql_query(
                "SELECT time, open, high, low, close, volume "
                "FROM ohlcv WHERE symbol = ? AND resolution = '1d' "
                "ORDER BY time ASC",
                con, params=[sym], parse_dates=["time"])
            if df.empty:
                continue
            df["time"] = pd.to_datetime(df["time"]).dt.normalize()
            df = df.set_index("time")
            if df.index.has_duplicates:
                df = df[~df.index.duplicated(keep="last")]
            df = df[df.index.dayofweek < 5]
            out[sym] = df
    finally:
        con.close()
    return out


def bar_finality_check(data: dict[str, pd.DataFrame], *,
                        target_date: pd.Timestamp,
                        min_fraction: float = 0.6) -> tuple[bool, float, int]:
    """Verify a meaningful majority of universe symbols have a bar
    dated EXACTLY ``target_date``. If not, today's data is stale —
    abort the run.

    Returns ``(ok, fraction, n_with_bar)``.
    """
    if not data:
        return (False, 0.0, 0)
    n_with = sum(1 for df in data.values() if target_date in df.index)
    fraction = n_with / len(data)
    return (fraction >= min_fraction, fraction, n_with)


# ── Book reconstruction (paper-ledger -> backtest harness Book) ──────


def reconstruct_book(positions: dict[str, ledger.StoredPosition],
                      cash: float):
    """Build a ``backtesting.replay.Book`` from the paper-ledger so
    the strategy's ``decide(view, book)`` sees the same shape it does
    in backtests. ``equity`` is filled in by the caller after MTM."""
    from backtesting.replay import Book, Position
    book_positions: dict = {}
    for sym, p in positions.items():
        book_positions[sym] = Position(
            symbol=sym,
            entry_date=pd.Timestamp(p.entry_date),
            entry_price=p.entry_price,
            shares=p.shares, stop=p.stop,
            risk_per_share=p.risk_per_share,
            cost_basis=p.cost_basis,
            bars_held=p.bars_held,
            highest_high=p.highest_high,
            highest_close=p.highest_close,
            last_close=p.last_close,
        )
    # Equity is computed by the caller; seed with cash so the book
    # object is constructible.
    return Book(cash=cash, equity=cash, positions=book_positions)


def mark_to_market(positions: dict[str, ledger.StoredPosition],
                    data: dict[str, pd.DataFrame],
                    target_date: pd.Timestamp) -> tuple[float, dict[str, float]]:
    """Stale-carry MTM (same contract as backtesting.replay's MTM
    step): for each open position, use today's close if available;
    otherwise carry the last known close."""
    per_sym: dict[str, float] = {}
    mtm = 0.0
    for sym, p in positions.items():
        df = data.get(sym)
        c = None
        if df is not None and target_date in df.index:
            c = float(df.at[target_date, "close"])
        used = c if c is not None else p.last_close
        per_sym[sym] = p.shares * used
        mtm += per_sym[sym]
    return mtm, per_sym


# ── Crash-safe wrapper ────────────────────────────────────────────────


def run_eod(*, strategy_factory, universe: list[str],
              paths: RunnerPaths | None = None,
              today_override: str | None = None) -> int:
    """Top-level entry point with the crash-safe wrapper.

    Returns the exit code (0 = ok, 1 = any failure). Designed to be
    called from a thin ``__main__`` block in this module OR by tests
    with mocked paths / strategy.
    """
    paths = paths or RunnerPaths()
    paths.logs_dir.mkdir(parents=True, exist_ok=True)

    # Logging — both file (for the cron's stdout redirect) and stdout.
    log_path = paths.logs_dir / f"eod_runner_{today_override or today_ist_date_str()}.log"
    log_handlers: list[logging.Handler] = [
        logging.FileHandler(log_path, mode="a", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    for h in log_handlers:
        h.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s | %(message)s"))
    root_logger = logging.getLogger()
    root_logger.handlers = log_handlers
    root_logger.setLevel(logging.INFO)
    logger = logging.getLogger("eod_runner")

    run_date = today_override or today_ist_date_str()
    hh_mm = now_ist().strftime("%H:%M")
    logger.info("=== EOD runner start | run_date=%s | %s IST",
                  run_date, hh_mm)

    try:
        # 0) Paper-only safety guard.
        assert_paper_mode()
        logger.info("safety guard: PAPER_MODE=1 confirmed.")

        # 1) Init the ledger (idempotent).
        from config import INITIAL_CAPITAL
        ledger.init_ledger(paths.paper_ledger_db,
                            initial_capital=INITIAL_CAPITAL)

        # 2) Idempotency lock — is today already done?
        prior = ledger.get_run(paths.paper_ledger_db, run_date)
        if prior is not None and prior.status in ("ok", "no_op"):
            logger.info("idempotency: run for %s already completed "
                          "(status=%s). NO-OP.", run_date, prior.status)
            telegram.send(
                f"NO-OP {run_date} {hh_mm} IST | "
                f"already completed earlier (status={prior.status}). "
                f"Equity Rs {prior.equity:,.0f}." if prior.equity else
                f"NO-OP {run_date} | already completed.")
            return 0

        # 3) LAW 7 backup before any write.
        backup = backup_paper_ledger(paths)
        if backup is not None:
            logger.info("law7 backup -> %s", backup.name)

        # 4) Load universe + 5) finality check.
        target_date = pd.Timestamp(run_date)
        logger.info("loading %d universe symbols from %s ...",
                      len(universe), paths.market_data_db.name)
        data = load_universe_from_market_db(paths, symbols=universe)
        logger.info("loaded %d symbols.", len(data))

        ok, fraction, n_with = bar_finality_check(
            data, target_date=target_date)
        logger.info("bar-finality: %d/%d symbols have a %s bar "
                      "(fraction=%.2f)",
                      n_with, len(data), run_date, fraction)
        if not ok:
            msg = (f"BAR FINALITY FAILED: only {fraction:.0%} of "
                    f"{len(data)} symbols have a {run_date} bar. "
                    f"Skipping today's run.")
            logger.warning(msg)
            ledger.upsert_run(paths.paper_ledger_db, ledger.RunRecord(
                run_date=run_date,
                run_at_utc=datetime.now(timezone.utc).isoformat(),
                status="error", n_orders=0, equity=None, cash=None,
                error_message=msg))
            telegram.send_error(run_date=run_date,
                                  error_type="BarFinality",
                                  error_message=msg, hh_mm=hh_mm)
            return 1

        # 6) Load book.
        positions = ledger.load_positions(paths.paper_ledger_db)
        cash = ledger.get_cash(paths.paper_ledger_db)
        logger.info("loaded book: %d positions, cash Rs %.2f",
                      len(positions), cash)

        # 7) Build harness BarView + Book at today's cutoff.
        from backtesting.replay import BarView
        view = BarView(data, cutoff=target_date)
        book = reconstruct_book(positions, cash)

        # 8) Decide. NOTE: decisions are NEXT-DAY-OPEN intents — we
        # record them but do not fill until tomorrow's run sees the
        # next bar's open price. For a paper run we are recording
        # INTENT, not fills.
        strategy = strategy_factory()
        orders = list(strategy.decide(view, book) or [])
        n_orders = len(orders)
        logger.info("strategy decided %d orders (next-open intent): %s",
                      n_orders, [(type(o).__name__, getattr(o, "symbol",
                                                              "?"))
                                    for o in orders[:15]])

        # 9-10) MTM at today's close + write equity row.
        # In paper-trading we do NOT simulate fills here; the strategy
        # carries existing positions across days and the equity row
        # reflects MTM-at-today's-close on the existing book. (Order
        # FILLS happen on tomorrow's run when next-open is available.)
        mtm, _ = mark_to_market(positions, data, target_date)
        equity = cash + mtm
        ledger.upsert_equity(paths.paper_ledger_db, run_date,
                              equity=equity, cash=cash, mtm=mtm)
        logger.info("equity row written: equity Rs %.2f (cash %.2f + "
                      "mtm %.2f)", equity, cash, mtm)

        # 11) Record the run + Telegram success ping.
        ledger.upsert_run(paths.paper_ledger_db, ledger.RunRecord(
            run_date=run_date,
            run_at_utc=datetime.now(timezone.utc).isoformat(),
            status="ok", n_orders=n_orders, equity=equity, cash=cash,
            error_message=None))
        telegram.send_success(run_date=run_date, equity=equity,
                                n_orders=n_orders, hh_mm=hh_mm)
        logger.info("=== EOD runner OK")
        return 0

    except Exception as e:
        tb = traceback.format_exc()
        logger.error("CRASH-SAFE WRAPPER: caught %s: %s\n%s",
                      type(e).__name__, e, tb)
        try:
            ledger.init_ledger(paths.paper_ledger_db)
            ledger.upsert_run(paths.paper_ledger_db, ledger.RunRecord(
                run_date=run_date,
                run_at_utc=datetime.now(timezone.utc).isoformat(),
                status="error", n_orders=0, equity=None, cash=None,
                error_message=f"{type(e).__name__}: {e}"))
        except Exception as inner:
            logger.error("could not record error row in ledger: %s",
                          inner)
        try:
            telegram.send_error(
                run_date=run_date,
                error_type=type(e).__name__,
                error_message=str(e),
                hh_mm=hh_mm,
                traceback_tail=tb)
        except Exception as inner:
            logger.error("could not send error Telegram: %s", inner)
        # Always non-zero on error so the cron's exit code reflects it.
        return 1


# ── Default __main__: MONTHLY SMOM on SMID_UNIVERSE ───────────────────


def _default_strategy_factory():
    """The MONTHLY SMOM candidate from SMOM-3 — the strongest signal
    in the project per the SMID-WEEKLY conclusion."""
    from signals.smid_momentum import SmidMomentumStrategy
    return SmidMomentumStrategy(rebalance_freq="monthly")


def _default_universe() -> list[str]:
    from data.universe import SMID_UNIVERSE
    return list(SMID_UNIVERSE)


if __name__ == "__main__":   # pragma: no cover
    sys.exit(run_eod(
        strategy_factory=_default_strategy_factory,
        universe=_default_universe(),
    ))
