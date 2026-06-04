"""DATA-FIX — pre-re-fetch backup of market_data.db (LAW 7).

Snapshots the current DB to
``backups/market_data_<ts>_pre_data_consistency.db`` BEFORE the
``data_consistency_refetch_upstox_names.py`` re-fetch runs. Lets us
roll back in one ``Copy-Item`` if yfinance returns weird data or the
re-fetched series fails downstream sanity checks.

USAGE
=====
    py -3.11 scripts/data_consistency_pre_backup.py

Idempotent in the sense that every run writes a fresh timestamped
file — running twice gives two backups, neither overwriting the
other. Does NOT call yfinance and does NOT mutate the DB; it only
copies bytes.
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB = PROJECT_ROOT / "market_data.db"
BACKUPS_DIR = PROJECT_ROOT / "backups"


def main() -> int:
    if not DB.exists():
        print(f"[DATA-FIX backup] ABORT — DB not found at {DB}")
        return 1
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    dest = BACKUPS_DIR / f"market_data_{ts}_pre_data_consistency.db"
    size_mb = DB.stat().st_size / 1024 / 1024
    print(f"[DATA-FIX backup] copying {DB.name} ({size_mb:.1f} MB) "
          f"-> {dest.relative_to(PROJECT_ROOT)} ...")
    shutil.copy2(DB, dest)
    if not dest.exists():
        print("[DATA-FIX backup] FAILED — destination missing after copy")
        return 2
    dest_mb = dest.stat().st_size / 1024 / 1024
    if abs(dest_mb - size_mb) > 0.01:
        print(f"[DATA-FIX backup] WARN — size mismatch "
              f"(src {size_mb:.2f} MB, dest {dest_mb:.2f} MB)")
    print("[DATA-FIX backup] done. Restore with:")
    print(f"  Copy-Item '{dest.relative_to(PROJECT_ROOT)}' "
          f"'market_data.db' -Force")
    return 0


if __name__ == "__main__":
    sys.exit(main())
