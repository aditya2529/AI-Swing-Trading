"""MOM-1 — backfill the MOMENTUM_UNIVERSE names not yet in market_data.db.

Iterates ``data.universe.MOMENTUM_NEW_TO_DB`` (112 symbols at the
MOM-1 commit), calls ``data.ingestion.fetch_and_store(sym, years=12,
source='yfinance')`` for each, and writes a per-run summary at the
end. Error-tolerant: a failed fetch is logged and skipped — one bad
symbol cannot abort the run.

PRE-REQUISITES (asserted at startup)
====================================
* ``backups/market_data_<ts>_pre_mom1_backfill.db`` must exist (the
  LAW 7 backup). The script aborts if no such file is found.
* ``market_data.db`` must exist.

USAGE
=====
    py -3.11 scripts/mom1_backfill_universe.py

Re-runnable. ``fetch_and_store`` uses ``upsert_ohlcv`` so a re-fetch
of an already-stored symbol is idempotent at the DB level (no
duplicate rows). Sequential — no parallelism — to be polite to
yfinance and keep the failure surface small.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB = PROJECT_ROOT / "market_data.db"
BACKUPS_DIR = PROJECT_ROOT / "backups"
LOG_PATH = PROJECT_ROOT / "logs" / "mom1_backfill.log"

# Inter-symbol pause (be polite to yfinance — also reduces the chance
# they fingerprint us as a scraper if many runs accumulate).
INTER_SYMBOL_SLEEP_SECS = 0.3

# Tighten yfinance's logger so its per-request noise doesn't drown our
# per-symbol progress output.
logging.getLogger("yfinance").setLevel(logging.ERROR)


def _check_prerequisites() -> tuple[bool, str]:
    if not DB.exists():
        return False, f"market_data.db not found at {DB}"
    backups = sorted(BACKUPS_DIR.glob("market_data_*_pre_mom1_backfill.db"))
    if not backups:
        return False, ("No pre-MOM1 backup found in backups/ — refusing to "
                       "run without one. Run scripts/mom1_pre_backfill_backup.py "
                       "first.")
    return True, f"backup OK: {backups[-1].name}"


def _row_count(con: sqlite3.Connection, sym: str | None = None) -> int:
    sql = "SELECT COUNT(*) FROM ohlcv WHERE resolution = '1d'"
    args: tuple = ()
    if sym is not None:
        sql += " AND symbol = ?"
        args = (sym,)
    return int(con.execute(sql, args).fetchone()[0])


def main() -> int:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-7s %(name)s  %(message)s"))
    root_logger = logging.getLogger()
    root_logger.addHandler(fh)
    root_logger.setLevel(logging.INFO)

    ok, msg = _check_prerequisites()
    print(f"[MOM-1 backfill] prereq check: {msg}")
    if not ok:
        return 1

    from data.universe import MOMENTUM_NEW_TO_DB
    from data.ingestion import fetch_and_store

    symbols = list(MOMENTUM_NEW_TO_DB)
    n = len(symbols)
    print(f"[MOM-1 backfill] {n} symbols to fetch via yfinance, 12y daily")

    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    pre_rows = _row_count(con)
    pre_size_mb = DB.stat().st_size / 1024 / 1024
    con.close()
    print(f"[MOM-1 backfill] pre-run: {pre_rows} rows / {pre_size_mb:.1f} MB")

    succeeded: list[tuple[str, int]] = []   # (symbol, n_bars)
    failed: list[tuple[str, str]] = []      # (symbol, error message)
    t0 = time.time()
    for i, sym in enumerate(symbols, start=1):
        try:
            df = fetch_and_store(sym, years=12, resolution="1d",
                                   source="yfinance")
            n_bars = len(df)
            succeeded.append((sym, n_bars))
            print(f"  [{i:>3}/{n}] {sym:<18} {n_bars:>5} bars")
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            failed.append((sym, err))
            print(f"  [{i:>3}/{n}] {sym:<18}  FAIL: {err}")
        time.sleep(INTER_SYMBOL_SLEEP_SECS)

    elapsed = time.time() - t0

    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    post_rows = _row_count(con)
    con.close()
    post_size_mb = DB.stat().st_size / 1024 / 1024

    print()
    print(f"[MOM-1 backfill] === summary ===")
    print(f"  wall time:         {elapsed:.0f} s "
          f"({elapsed / 60:.1f} min)")
    print(f"  succeeded:         {len(succeeded)} / {n}")
    print(f"  failed:            {len(failed)} / {n}")
    print(f"  pre/post rows:     {pre_rows} -> {post_rows} "
          f"(+{post_rows - pre_rows})")
    print(f"  pre/post DB size:  {pre_size_mb:.1f} MB -> "
          f"{post_size_mb:.1f} MB (+{post_size_mb - pre_size_mb:.1f} MB)")
    if succeeded:
        total_bars = sum(b for _, b in succeeded)
        avg_bars = total_bars / len(succeeded)
        min_bars = min(b for _, b in succeeded)
        max_bars = max(b for _, b in succeeded)
        print(f"  bars/symbol:       min {min_bars} / avg {avg_bars:.0f} "
              f"/ max {max_bars}")
    if failed:
        print()
        print(f"[MOM-1 backfill] FAILED symbols (full list):")
        for sym, err in failed:
            print(f"  {sym:<18} {err}")
    print()
    print(f"[MOM-1 backfill] log written to "
          f"{LOG_PATH.relative_to(PROJECT_ROOT)}")
    return 0 if not failed else 0   # success-of-run independent of per-symbol failures


if __name__ == "__main__":
    sys.exit(main())
