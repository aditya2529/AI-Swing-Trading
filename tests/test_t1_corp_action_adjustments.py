"""T1.B — corporate-action back-adjustment regression test.

Locks the T1.A sign-off decisions:

  APPROVED back-adjustment (continuous series across ex-date):
    - VEDL.NS         2026-04-30   Vedanta demerger
    - TATAMOTORS.NS   2025-10-14   TM CV/PV demerger

  REJECTED back-adjustment (crash signal must remain visible):
    - VEDL.NS         2020-03-23   COVID crash (NOT a demerger)
    - YESBANK.NS      2020-03-06   SBI moratorium (NOT a corp action)

The assertions are designed to be:
  * RED before scripts/r1_apply_back_adjustments.py runs (because the
    approved demergers still show their -64.9% / -40.2% ex-date gaps).
  * GREEN after T1.B applies the adjustments (continuity at ex-date,
    pre-ex prices scaled by the canonical factor, crashes still
    visible in daily returns, YESBANK absolute prices untouched).
  * Stay GREEN forever — re-applying T1.B is idempotent, and any future
    code that "fixes" a crash will turn this test RED.

The test reads the production market_data.db read-only. It does NOT
touch other tests' fixtures; it just guards against a regression in
the canonical price record.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "market_data.db"


# ── Pre-T1.B reference values (captured 2026-06-03, see r1_corp_action_audit.md) ──

# Approved demergers: factor = close[ex] / close[ex-1] from the original DB.
# After T1.B, pre-ex bars are multiplied by `factor` so the series is
# continuous across the ex-date (daily return on ex-date ≈ 0).
APPROVED_DEMERGERS = [
    {
        "symbol": "VEDL.NS",
        "ex_date": "2026-04-30",
        "prev_close_orig": 773.6000,
        "ex_close_orig":   271.5500,
        "factor":           0.351021,        # 271.5500 / 773.6000
    },
    {
        "symbol": "TATAMOTORS.NS",
        "ex_date": "2025-10-14",
        "prev_close_orig": 660.7500,
        "ex_close_orig":   395.4500,
        "factor":           0.598487,        # 395.4500 / 660.7500
    },
]

# Rejected events: crash signal must remain visible in the daily return.
# Daily-return is scale-invariant under a flat back-adjustment of all
# pre-ex bars by the same factor, so the assertion below holds regardless
# of whether any later same-symbol back-adjustment touched these bars.
REJECTED_EVENTS = [
    {
        "symbol": "VEDL.NS",
        "date": "2020-03-23",
        "prev_session": "2020-03-20",
        "expected_return": -0.239476,
        "note": "COVID crash, NIFTY -13.0% same day, VEDL bounced 40.65 -> 42.20 -> 43.80 next sessions",
    },
    {
        "symbol": "YESBANK.NS",
        "date": "2020-03-06",
        "prev_session": "2020-03-05",
        "expected_return": -0.561141,
        "note": "SBI moratorium / rescue, 7.3x panic volume",
    },
]

# YESBANK has NO approved adjustment, so its absolute prices must be
# IDENTICAL pre- and post-T1.B.
YESBANK_2020_03_06_ORIG = {
    "open":   33.1500,
    "high":   33.1500,
    "low":     5.6500,
    "close":  16.1500,
    "volume": 1264917719,
}


# ── Test helpers ────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def db_conn():
    if not DB.exists():
        pytest.skip(f"market_data.db not found at {DB}")
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    yield con
    con.close()


def _bar(con: sqlite3.Connection, sym: str, date_str: str) -> dict | None:
    row = con.execute(
        "SELECT open, high, low, close, volume FROM ohlcv "
        "WHERE symbol = ? AND resolution = '1d' AND time = ?",
        (sym, f"{date_str} 00:00:00")
    ).fetchone()
    if row is None:
        return None
    o, h, l, c, v = row
    return {"open": float(o), "high": float(h), "low": float(l),
            "close": float(c), "volume": float(v)}


def _close_or_skip(con: sqlite3.Connection, sym: str, date_str: str) -> float:
    bar = _bar(con, sym, date_str)
    if bar is None:
        pytest.skip(f"{sym} {date_str} missing from DB")
    return bar["close"]


# ── 1. Continuity at ex-date (approved demergers) ───────────────────────


@pytest.mark.parametrize("case", APPROVED_DEMERGERS,
                          ids=lambda c: f"{c['symbol']}@{c['ex_date']}")
def test_approved_demerger_continuity(db_conn, case):
    """After T1.B, the ex-date gap is gone — daily return on the ex-date
    is ~0 because the pre-ex series was multiplied by the canonical
    factor. Locks the back-adjustment as the only acceptable handling."""
    sym = case["symbol"]
    ex = case["ex_date"]
    # Walk back to the trading day immediately before ex (handles weekends).
    prev_row = db_conn.execute(
        "SELECT time FROM ohlcv WHERE symbol = ? AND resolution = '1d' "
        "AND time < ? ORDER BY time DESC LIMIT 1",
        (sym, f"{ex} 00:00:00")
    ).fetchone()
    if prev_row is None:
        pytest.skip(f"{sym}: no session before ex-date {ex}")
    prev_close = _close_or_skip(db_conn, sym, prev_row[0][:10])
    ex_close = _close_or_skip(db_conn, sym, ex)
    daily_return = ex_close / prev_close - 1.0
    # After back-adjust, the gap is removed: prev_close × factor == ex_close
    # so daily_return should be ~0. Tolerance covers tiny float rounding
    # if the adjustment is implemented via SQL UPDATE (~1e-6 tolerance).
    assert abs(daily_return) < 1e-3, (
        f"{sym} ex-date {ex}: daily return is {daily_return:+.4%}, "
        f"expected ~0% post-T1.B back-adjustment. The pre-ex series has "
        f"NOT been back-adjusted by the canonical factor "
        f"{case['factor']:.6f}."
    )


@pytest.mark.parametrize("case", APPROVED_DEMERGERS,
                          ids=lambda c: f"{c['symbol']}@{c['ex_date']}")
def test_approved_demerger_pre_ex_prices_scaled(db_conn, case):
    """The post-adjust prev-ex close must equal ex_close exactly — that's
    what `factor = close[ex] / close[ex-1]` produces by construction
    (continuity at the ex-date is the whole point of back-adjustment).

    Asserting against ex_close_orig directly is more precise than
    against ``prev_close_orig * factor`` (the truncated 6-digit factor
    in this file is the audit-report version, not the actual DB-computed
    factor). Either assertion proves the adjustment was applied; this
    one is rounding-robust.
    """
    sym = case["symbol"]
    ex = case["ex_date"]
    prev_row = db_conn.execute(
        "SELECT time FROM ohlcv WHERE symbol = ? AND resolution = '1d' "
        "AND time < ? ORDER BY time DESC LIMIT 1",
        (sym, f"{ex} 00:00:00")
    ).fetchone()
    if prev_row is None:
        pytest.skip(f"{sym}: no session before ex-date {ex}")
    prev_close_now = _close_or_skip(db_conn, sym, prev_row[0][:10])
    # Continuity at ex-date: post-adjust prev_close == ex_close exactly.
    assert abs(prev_close_now - case["ex_close_orig"]) < 1e-3, (
        f"{sym} prev-ex close is {prev_close_now:.4f}, expected "
        f"{case['ex_close_orig']:.4f} (= ex_close after back-adjust by "
        f"factor close[ex]/close[ex-1]). Back-adjustment not applied or "
        f"applied with wrong factor."
    )


@pytest.mark.parametrize("case", APPROVED_DEMERGERS,
                          ids=lambda c: f"{c['symbol']}@{c['ex_date']}")
def test_approved_demerger_ex_date_close_unchanged(db_conn, case):
    """The ex-date bar itself must NOT be back-adjusted. Only pre-ex bars
    get scaled. ex_close stays at its original value."""
    sym = case["symbol"]
    ex = case["ex_date"]
    ex_close = _close_or_skip(db_conn, sym, ex)
    assert abs(ex_close - case["ex_close_orig"]) < 1e-4, (
        f"{sym} ex-date close is {ex_close:.4f}, expected unchanged "
        f"{case['ex_close_orig']:.4f}. The ex-date bar itself was "
        f"incorrectly modified by T1.B."
    )


# ── 2. Crash signal preserved (rejected events) ─────────────────────────


@pytest.mark.parametrize("case", REJECTED_EVENTS,
                          ids=lambda c: f"{c['symbol']}@{c['date']}")
def test_rejected_event_daily_return_preserved(db_conn, case):
    """The crash on a rejected date must remain visible — daily return
    must match the reference. This holds regardless of any same-symbol
    back-adjustment because return is scale-invariant under a flat
    multiplicative adjustment of both prev_close and current close.

    This is what LOCKS the ops rejection: any future code that tries to
    'fix' the crash by changing the ratio will turn this test RED.
    """
    sym = case["symbol"]
    date = case["date"]
    prev = case["prev_session"]
    prev_close = _close_or_skip(db_conn, sym, prev)
    today_close = _close_or_skip(db_conn, sym, date)
    daily_return = today_close / prev_close - 1.0
    assert abs(daily_return - case["expected_return"]) < 1e-4, (
        f"{sym} {date}: daily return is {daily_return:+.4%}, expected "
        f"{case['expected_return']:+.4%}. The crash signal has been "
        f"altered. ({case['note']})"
    )


# ── 3. YESBANK has NO approved adjustment → absolute prices unchanged ──


def test_yesbank_2020_03_06_absolute_prices_unchanged(db_conn):
    """YESBANK is not in the approved-demerger list. The 2020-03-06 bar
    must be IDENTICAL to the pre-T1.B reference — no rounding, no
    scaling, nothing. Locks the 'keep-as-is' decision at the absolute-
    price level (stronger guarantee than the daily-return check above)."""
    bar = _bar(db_conn, "YESBANK.NS", "2020-03-06")
    if bar is None:
        pytest.skip("YESBANK.NS 2020-03-06 missing from DB")
    for k, orig in YESBANK_2020_03_06_ORIG.items():
        assert abs(bar[k] - orig) < 1e-4, (
            f"YESBANK.NS 2020-03-06 {k} is {bar[k]:.4f}, expected "
            f"unchanged {orig:.4f}. YESBANK was incorrectly touched by T1.B."
        )
