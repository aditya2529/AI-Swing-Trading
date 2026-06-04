"""MOM-1 — pre-backfill backup of market_data.db.

Per LAW 7: snapshot the current DB to a timestamped file in
``backups/`` BEFORE the MOM-1 backfill runs. Lets us roll back the
DB in one ``Copy-Item`` if yfinance returns weird data, the validator
rejects something we didn't expect, or the run is interrupted partway.

USAGE
=====
    py -3.11 scripts/mom1_pre_backfill_backup.py

The script is idempotent in the sense that it always writes a
fresh timestamped file — running it twice produces two backups,
neither overwriting the other. It does NOT call yfinance or write
to the DB; it just copies bytes.
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
        print(f"[MOM-1 backup] ABORT — DB not found at {DB}")
        return 1
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    dest = BACKUPS_DIR / f"market_data_{ts}_pre_mom1_backfill.db"
    size_mb = DB.stat().st_size / 1024 / 1024
    print(f"[MOM-1 backup] copying {DB.name} ({size_mb:.1f} MB) "
          f"-> {dest.relative_to(PROJECT_ROOT)} ...")
    shutil.copy2(DB, dest)
    if not dest.exists():
        print(f"[MOM-1 backup] FAILED — destination missing after copy")
        return 2
    dest_size_mb = dest.stat().st_size / 1024 / 1024
    if abs(dest_size_mb - size_mb) > 0.01:
        print(f"[MOM-1 backup] WARN — size mismatch "
              f"(src {size_mb:.2f} MB, dest {dest_size_mb:.2f} MB)")
    print(f"[MOM-1 backup] done. Restore with:")
    print(f"  Copy-Item '{dest.relative_to(PROJECT_ROOT)}' "
          f"'market_data.db' -Force")
    return 0


if __name__ == "__main__":
    sys.exit(main())
