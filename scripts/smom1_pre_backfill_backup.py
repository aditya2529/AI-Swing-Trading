"""SMOM-1 — pre-backfill backup of market_data.db (LAW 7).

Snapshots the DB to ``backups/market_data_<ts>_pre_smom1_backfill.db``
BEFORE the SMOM-1 backfill runs. Lets us roll back the DB in one
``Copy-Item`` if yfinance returns weird data, the validator rejects
something unexpected, or the run is interrupted partway.

USAGE
=====
    py -3.11 scripts/smom1_pre_backfill_backup.py

Idempotent — every run writes a fresh timestamped file. Does NOT
call yfinance and does NOT mutate the DB; only copies bytes.
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
        print(f"[SMOM-1 backup] ABORT — DB not found at {DB}")
        return 1
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    dest = BACKUPS_DIR / f"market_data_{ts}_pre_smom1_backfill.db"
    size_mb = DB.stat().st_size / 1024 / 1024
    print(f"[SMOM-1 backup] copying {DB.name} ({size_mb:.1f} MB) "
          f"-> {dest.relative_to(PROJECT_ROOT)} ...")
    shutil.copy2(DB, dest)
    if not dest.exists():
        print("[SMOM-1 backup] FAILED — destination missing after copy")
        return 2
    dest_mb = dest.stat().st_size / 1024 / 1024
    if abs(dest_mb - size_mb) > 0.01:
        print(f"[SMOM-1 backup] WARN — size mismatch "
              f"(src {size_mb:.2f} MB, dest {dest_mb:.2f} MB)")
    print("[SMOM-1 backup] done. Restore with:")
    print(f"  Copy-Item '{dest.relative_to(PROJECT_ROOT)}' "
          f"'market_data.db' -Force")
    return 0


if __name__ == "__main__":
    sys.exit(main())
