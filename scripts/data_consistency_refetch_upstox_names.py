"""DATA-FIX — re-fetch the 25 Upstox-sourced MOMENTUM_UNIVERSE names
via yfinance so the entire 136-name universe uses the SAME total-return
adjustment convention.

WHY
===
Upstox historical OHLCV is split-adjusted ONLY; yfinance daily is split-
AND dividend-adjusted. The original POINT_IN_TIME_NSE25 swing universe
(25 names) was sourced from Upstox during the swing project's bootstrap;
the MOM-1 backfill (110 additional names) was sourced from yfinance.
The mismatch means the 25 Upstox names report SMALLER total returns than
reality (no dividend reinvest), which systematically underrates them in
the cross-sectional momentum ranking versus the yfinance names. Live
trading also uses yfinance (see ``data/adapters/yfinance_adapter.py``),
so this brings backtest data into alignment with the live feed.

PRE-REQUISITES (asserted at startup)
====================================
* ``backups/market_data_<ts>_pre_data_consistency.db`` MUST exist (the
  LAW 7 backup). The script aborts if no such file is found. Run
  ``scripts/data_consistency_pre_backup.py`` first.

WHAT IT DOES
============
For each symbol in ``data.universe.POINT_IN_TIME_NSE25`` (the 25
Upstox-sourced names), calls ``fetch_and_store(sym, years=12,
source='yfinance')``. ``upsert_ohlcv`` is INSERT-OR-REPLACE keyed on
(symbol, resolution, time), so:
    * Same-date Upstox bars get REPLACED by yfinance bars (different
      OHLC due to dividend adjustment).
    * Pre-existing-history yfinance bars BEYOND the old Upstox range
      (2014-06 -> 2016-05) get INSERTED (extending the history back
      ~2 years, matching the rest of the universe's range).
Error-tolerant: a failed fetch is logged and skipped. Sequential with
a small inter-symbol sleep (be polite to yfinance).

USAGE
=====
    py -3.11 scripts/data_consistency_refetch_upstox_names.py

Re-runnable. ``upsert_ohlcv`` is idempotent at the DB level, so a
re-run on the same symbol overwrites the same rows.
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
LOG_PATH = PROJECT_ROOT / "logs" / "data_consistency_refetch.log"

INTER_SYMBOL_SLEEP_SECS = 0.3

# Tighten yfinance's logger so the per-request noise doesn't drown our
# per-symbol progress.
logging.getLogger("yfinance").setLevel(logging.ERROR)


def _check_prerequisites() -> tuple[bool, str]:
    if not DB.exists():
        return False, f"market_data.db not found at {DB}"
    backups = sorted(BACKUPS_DIR.glob("market_data_*_pre_data_consistency.db"))
    if not backups:
        return False, ("No pre-data-consistency backup found in backups/ — "
                       "refusing to run without one. Run "
                       "scripts/data_consistency_pre_backup.py first.")
    return True, f"backup OK: {backups[-1].name}"


def _stats(con: sqlite3.Connection, sym: str | None = None) -> tuple[int, str, str]:
    sql = "SELECT COUNT(*), MIN(time), MAX(time) FROM ohlcv WHERE resolution='1d'"
    args: tuple = ()
    if sym is not None:
        sql += " AND symbol = ?"
        args = (sym,)
    row = con.execute(sql, args).fetchone()
    return int(row[0]), str(row[1] or ""), str(row[2] or "")


def main() -> int:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-7s %(name)s  %(message)s"))
    root_logger = logging.getLogger()
    root_logger.addHandler(fh)
    root_logger.setLevel(logging.INFO)

    ok, msg = _check_prerequisites()
    print(f"[DATA-FIX refetch] prereq check: {msg}")
    if not ok:
        return 1

    from data.universe import POINT_IN_TIME_NSE25
    from data.ingestion import fetch_and_store

    symbols = list(POINT_IN_TIME_NSE25)
    n = len(symbols)
    print(f"[DATA-FIX refetch] {n} POINT_IN_TIME_NSE25 symbols to "
          f"re-fetch via yfinance, 12y daily.")
    print("[DATA-FIX refetch] Effect: same-date rows REPLACED with "
          "yfinance bars (different OHLC due to dividend adjustment); "
          "pre-2016 rows INSERTED (history extends ~2y).")

    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    pre_total_rows, _, _ = _stats(con)
    pre_size_mb = DB.stat().st_size / 1024 / 1024
    pre_per_sym: dict[str, tuple[int, str, str]] = {
        s: _stats(con, s) for s in symbols}
    con.close()
    print(f"[DATA-FIX refetch] pre-run: {pre_total_rows} total rows / "
          f"{pre_size_mb:.1f} MB")

    succeeded: list[tuple[str, int]] = []
    failed: list[tuple[str, str]] = []
    t0 = time.time()
    for i, sym in enumerate(symbols, start=1):
        try:
            df = fetch_and_store(sym, years=12, resolution="1d",
                                   source="yfinance")
            n_bars = len(df)
            succeeded.append((sym, n_bars))
            pre_n, pre_min, pre_max = pre_per_sym[sym]
            print(f"  [{i:>2}/{n}] {sym:<14} {pre_n:>4} -> {n_bars:>4} bars "
                  f"(pre {pre_min[:10]}..{pre_max[:10]})")
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            failed.append((sym, err))
            print(f"  [{i:>2}/{n}] {sym:<14}  FAIL: {err}")
        time.sleep(INTER_SYMBOL_SLEEP_SECS)

    elapsed = time.time() - t0
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    post_total_rows, _, _ = _stats(con)
    con.close()
    post_size_mb = DB.stat().st_size / 1024 / 1024

    print()
    print("[DATA-FIX refetch] === summary ===")
    print(f"  wall time:        {elapsed:.0f} s ({elapsed / 60:.1f} min)")
    print(f"  succeeded:        {len(succeeded)} / {n}")
    print(f"  failed:           {len(failed)} / {n}")
    print(f"  pre/post rows:    {pre_total_rows} -> {post_total_rows} "
          f"(+{post_total_rows - pre_total_rows})")
    print(f"  pre/post DB size: {pre_size_mb:.1f} MB -> "
          f"{post_size_mb:.1f} MB (+{post_size_mb - pre_size_mb:.1f} MB)")
    if failed:
        print()
        print("[DATA-FIX refetch] FAILED symbols:")
        for sym, err in failed:
            print(f"  {sym:<14}  {err}")
    print()
    print(f"[DATA-FIX refetch] log -> "
          f"{LOG_PATH.relative_to(PROJECT_ROOT)}")
    return 0 if not failed else 0


if __name__ == "__main__":
    sys.exit(main())
