"""T1.B — apply back-adjustments for approved corporate actions.

Implements the ops sign-off recorded in ``logs/r1_corp_action_audit.md`` and
elaborated in the T1.A approval message:

  APPROVED  back-adjust   VEDL.NS         2026-04-30   Vedanta demerger
  APPROVED  back-adjust   TATAMOTORS.NS   2025-10-14   TM CV/PV demerger
  REJECTED  keep-as-is    VEDL.NS         2020-03-23   COVID crash
  REJECTED  keep-as-is    YESBANK.NS      2020-03-06   SBI moratorium

For each APPROVED case, multiply the OPEN/HIGH/LOW/CLOSE of every pre-ex
bar (time < ex_date) by the factor ``close[ex] / close[ex-1]``. The
ex-date bar is left untouched (it's the first bar on the new basis).
VOLUME is left UNCHANGED — share counts for the parent issuer aren't
affected by a value distribution, and the demerged entity's volume
lives under its own (different) ticker.

GUARANTEES
==========
* LAW 7 backup before any write: ``backups/market_data_<ts>_pre_t1b.db``.
* Single atomic transaction per symbol — partial failure rolls back fully.
* Idempotent: a second run is a no-op (continuity already holds).
* The regression test (``tests/test_t1_corp_action_adjustments.py``) must
  go RED -> GREEN across this commit and stay GREEN forever.

USAGE
=====
    py -3.11 scripts/r1_apply_back_adjustments.py

Re-runnable. Honest tolerance check up-front: if the DB no longer matches
the pre-T1.B reference values (someone re-backfilled), the script aborts
and asks for re-audit rather than guessing.
"""
from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# ── Configuration ───────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "market_data.db"
BACKUPS_DIR = ROOT / "backups"
REPORT_PATH = ROOT / "logs" / "r1_t1b_application.md"


@dataclass(frozen=True)
class Adjustment:
    symbol: str
    ex_date: str          # "YYYY-MM-DD"
    prev_close_orig: float
    ex_close_orig: float
    factor_expected: float
    note: str


# Approved adjustments — exact values from logs/r1_corp_action_audit.md
# captured pre-T1.B (sqlite read-only). If a re-backfill changes these,
# the script aborts safely (see _verify_pre_state).
APPROVED: list[Adjustment] = [
    Adjustment(
        symbol="VEDL.NS",
        ex_date="2026-04-30",
        prev_close_orig=773.6000,
        ex_close_orig=271.5500,
        factor_expected=0.351021,
        note="Vedanta demerger (5-way value distribution). Brief canonical."),
    Adjustment(
        symbol="TATAMOTORS.NS",
        ex_date="2025-10-14",
        prev_close_orig=660.7500,
        ex_close_orig=395.4500,
        factor_expected=0.598487,
        note="TM CV/PV demerger (NCLT-approved Sept 2025). NIFTY -0.32% same day."),
]

# Tolerance for matching DB values to the captured reference. Anything
# outside this is a "DB has changed" red flag and we abort.
ABS_TOL = 1e-3   # 1 paise on prices in the 50-1000 Rs range
REL_TOL = 1e-4   # factor accuracy

# Tolerance for "post-adjustment continuity already holds" idempotency check.
CONTINUITY_TOL = 1e-3


# ── DB helpers ──────────────────────────────────────────────────────────


def _close_at(con: sqlite3.Connection, sym: str, date_str: str) -> float | None:
    row = con.execute(
        "SELECT close FROM ohlcv WHERE symbol = ? AND resolution = '1d' "
        "AND time = ?", (sym, f"{date_str} 00:00:00")).fetchone()
    return float(row[0]) if row else None


def _prev_session_close(con: sqlite3.Connection, sym: str,
                         date_str: str) -> tuple[str, float] | None:
    row = con.execute(
        "SELECT time, close FROM ohlcv WHERE symbol = ? AND resolution = '1d' "
        "AND time < ? ORDER BY time DESC LIMIT 1",
        (sym, f"{date_str} 00:00:00")).fetchone()
    return (row[0][:10], float(row[1])) if row else None


def _count_pre_ex(con: sqlite3.Connection, sym: str, ex_date: str) -> int:
    return int(con.execute(
        "SELECT COUNT(*) FROM ohlcv WHERE symbol = ? AND resolution = '1d' "
        "AND time < ?", (sym, f"{ex_date} 00:00:00")).fetchone()[0])


# ── Pre-state verification + idempotency guard ──────────────────────────


def _verify_pre_state(con: sqlite3.Connection,
                       adj: Adjustment) -> tuple[bool, str]:
    """Return (should_apply, status_message).

    Three outcomes:
      (True, "MATCH: ...") — DB matches the captured reference, apply.
      (False, "ALREADY: ...") — continuity already holds, skip (idempotent).
      (False, "MISMATCH: ...") — DB has changed in some other way, ABORT.
    """
    ex_close = _close_at(con, adj.symbol, adj.ex_date)
    prev = _prev_session_close(con, adj.symbol, adj.ex_date)
    if ex_close is None or prev is None:
        return (False, f"MISMATCH: {adj.symbol} {adj.ex_date} or its prev "
                       f"session is missing from the DB.")
    prev_date, prev_close = prev
    ex_match = abs(ex_close - adj.ex_close_orig) < ABS_TOL
    prev_match_orig = abs(prev_close - adj.prev_close_orig) < ABS_TOL
    # Idempotency: if continuity already holds (prev_close ~= ex_close),
    # T1.B has already been applied. Safe to skip.
    daily_return_now = ex_close / prev_close - 1.0
    if abs(daily_return_now) < CONTINUITY_TOL:
        return (False, f"ALREADY: {adj.symbol} {adj.ex_date} continuity holds "
                       f"(prev={prev_close:.4f}, ex={ex_close:.4f}, "
                       f"return={daily_return_now:+.4%}). T1.B already applied.")
    if not ex_match:
        return (False, f"MISMATCH: {adj.symbol} ex-close is {ex_close:.4f}, "
                       f"reference is {adj.ex_close_orig:.4f}. DB has changed; "
                       f"re-run T1.A audit before T1.B.")
    if not prev_match_orig:
        return (False, f"MISMATCH: {adj.symbol} prev-close is {prev_close:.4f}, "
                       f"reference is {adj.prev_close_orig:.4f}. DB has changed; "
                       f"re-run T1.A audit before T1.B.")
    return (True, f"MATCH: {adj.symbol} {adj.ex_date} matches captured "
                  f"reference; ready to back-adjust.")


# ── Backup (LAW 7) ──────────────────────────────────────────────────────


def _backup_db() -> Path:
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    dest = BACKUPS_DIR / f"market_data_{ts}_pre_t1b.db"
    shutil.copy2(DB, dest)
    return dest


# ── Apply ───────────────────────────────────────────────────────────────


def _apply_one(con: sqlite3.Connection, adj: Adjustment) -> dict:
    """Apply a single back-adjustment in the current transaction.

    Returns a stats dict for the audit log."""
    # Re-fetch factor from CURRENT DB (matches the reference per verify
    # but use the live value so any tiny rounding is exact).
    ex_close = _close_at(con, adj.symbol, adj.ex_date)
    prev = _prev_session_close(con, adj.symbol, adj.ex_date)
    assert ex_close is not None and prev is not None  # _verify already checked
    prev_date, prev_close = prev
    factor = ex_close / prev_close
    # Defensive: factor must match the expected within REL_TOL.
    if abs(factor - adj.factor_expected) > REL_TOL:
        raise RuntimeError(
            f"{adj.symbol} factor {factor:.6f} drifted from expected "
            f"{adj.factor_expected:.6f}. Aborting T1.B.")
    n_pre = _count_pre_ex(con, adj.symbol, adj.ex_date)
    cur = con.execute(
        "UPDATE ohlcv SET open = open * :f, high = high * :f, "
        "low = low * :f, close = close * :f "
        "WHERE symbol = :s AND resolution = '1d' AND time < :ex",
        {"f": factor, "s": adj.symbol, "ex": f"{adj.ex_date} 00:00:00"})
    n_updated = cur.rowcount
    if n_updated != n_pre:
        raise RuntimeError(
            f"{adj.symbol}: expected to update {n_pre} pre-ex rows but "
            f"UPDATE touched {n_updated}.")
    return {
        "symbol": adj.symbol,
        "ex_date": adj.ex_date,
        "factor": factor,
        "n_rows_updated": n_updated,
        "prev_close_before": prev_close,
        "prev_close_after": prev_close * factor,
        "ex_close_unchanged": ex_close,
        "note": adj.note,
    }


def _verify_post_state(con: sqlite3.Connection, adj: Adjustment) -> tuple[bool, str]:
    """Post-apply: continuity must hold (daily return on ex-date ≈ 0)."""
    ex_close = _close_at(con, adj.symbol, adj.ex_date)
    prev = _prev_session_close(con, adj.symbol, adj.ex_date)
    if ex_close is None or prev is None:
        return (False, "post-state: missing rows")
    _, prev_close = prev
    daily_return = ex_close / prev_close - 1.0
    if abs(daily_return) >= CONTINUITY_TOL:
        return (False, f"post-state: {adj.symbol} ex-date daily return is "
                       f"{daily_return:+.4%}; expected ~0% after back-adjust.")
    return (True, f"post-state: {adj.symbol} continuity holds "
                  f"(return={daily_return:+.4%})")


# ── Report ──────────────────────────────────────────────────────────────


def _write_report(backup_path: Path, applied: list[dict],
                   skipped: list[str], verifications: list[str]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# T1.B — Back-adjustment application log")
    lines.append("")
    lines.append(f"**Timestamp:** {datetime.utcnow().isoformat()}Z")
    lines.append(f"**Branch:** `feature/t1-corp-action-audit`")
    lines.append(f"**Backup (LAW 7):** `{backup_path.relative_to(ROOT)}`")
    lines.append("")
    lines.append("## Applied adjustments")
    lines.append("")
    if not applied:
        lines.append("_No changes — all adjustments were already idempotent or skipped._")
    else:
        lines.append("| Symbol | Ex-date | Factor | Pre-ex rows updated | "
                     "prev close before | prev close after | ex close |")
        lines.append("|---|---|---:|---:|---:|---:|---:|")
        for a in applied:
            lines.append(
                f"| {a['symbol']} | {a['ex_date']} | {a['factor']:.6f} | "
                f"{a['n_rows_updated']} | {a['prev_close_before']:.4f} | "
                f"{a['prev_close_after']:.4f} | {a['ex_close_unchanged']:.4f} |")
        lines.append("")
        lines.append("**Volume handling:** unchanged. Demergers do not alter "
                     "the parent ticker's share count or trading volume — the "
                     "demerged entity becomes a separate ticker with its own "
                     "volume history. Multiplying volume by the price factor "
                     "would be wrong.")
        lines.append("")
        for a in applied:
            lines.append(f"- **{a['symbol']} {a['ex_date']}** — {a['note']}")
    lines.append("")
    lines.append("## Skipped / idempotent")
    lines.append("")
    if skipped:
        for s in skipped:
            lines.append(f"- {s}")
    else:
        lines.append("_None._")
    lines.append("")
    lines.append("## Post-state verification")
    lines.append("")
    for v in verifications:
        lines.append(f"- {v}")
    lines.append("")
    lines.append("## Carry-forward note for T2")
    lines.append("")
    lines.append("Per the T1.A audit's side observation, Upstox historical "
                 "OHLCV is split-adjusted but NOT dividend-adjusted; yfinance "
                 "live prices ARE both split- AND dividend-adjusted. T2 strategy "
                 "code must NOT naively mix yfinance live with Upstox historical "
                 "without applying an offset correction (or it will detect "
                 "phantom signals at every large dividend ex-date).")
    lines.append("")
    lines.append("## Rollback")
    lines.append("")
    lines.append(f"If T1.B needs to be rolled back, the pre-T1.B DB is preserved at")
    lines.append(f"`{backup_path.relative_to(ROOT)}` — restore with:")
    lines.append("")
    lines.append("```powershell")
    lines.append(f"Copy-Item '{backup_path.relative_to(ROOT)}' 'market_data.db' -Force")
    lines.append("```")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


# ── Main ────────────────────────────────────────────────────────────────


def main() -> int:
    if not DB.exists():
        print(f"[T1.B] ABORT: {DB} not found.")
        return 1

    print(f"[T1.B] verifying pre-state against captured references...")
    con_ro = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    to_apply: list[Adjustment] = []
    skipped: list[str] = []
    for adj in APPROVED:
        ok, msg = _verify_pre_state(con_ro, adj)
        print(f"  {msg}")
        if msg.startswith("MISMATCH"):
            print("[T1.B] ABORT: DB has changed since T1.A audit; not safe to apply.")
            con_ro.close()
            return 2
        if ok:
            to_apply.append(adj)
        else:
            skipped.append(msg)
    con_ro.close()

    if not to_apply:
        print(f"[T1.B] nothing to apply. {len(skipped)} skipped.")
        _write_report(Path(""), [], skipped, [])
        return 0

    print(f"[T1.B] backing up DB (LAW 7)...")
    backup_path = _backup_db()
    print(f"[T1.B] backup -> {backup_path.relative_to(ROOT)}")

    print(f"[T1.B] applying {len(to_apply)} adjustment(s) in a single transaction...")
    con = sqlite3.connect(str(DB))
    try:
        con.execute("BEGIN")
        applied: list[dict] = []
        for adj in to_apply:
            stats = _apply_one(con, adj)
            applied.append(stats)
            print(f"  {adj.symbol} {adj.ex_date}: {stats['n_rows_updated']} "
                  f"rows × {stats['factor']:.6f}")
        con.commit()
    except Exception:
        con.rollback()
        print(f"[T1.B] ABORT (transaction rolled back). DB unchanged.")
        con.close()
        raise
    con.close()

    print(f"[T1.B] verifying post-state...")
    verifications: list[str] = []
    con_ro = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    for adj in to_apply:
        ok, msg = _verify_post_state(con_ro, adj)
        verifications.append(msg)
        print(f"  {msg}")
        if not ok:
            print(f"[T1.B] post-state verification FAILED — restore from backup!")
            con_ro.close()
            return 3
    con_ro.close()

    _write_report(backup_path, applied, skipped, verifications)
    print(f"[T1.B] done. report -> {REPORT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
