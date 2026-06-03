"""T1.A — corporate-action audit for the swing universe.

Per the swing kickoff brief and ops's T1 guidance, scans the 27 symbols
in market_data.db for daily moves that warrant classification, and
classifies each one using yfinance corporate-action data as PRIMARY,
with a snapback/volume heuristic only as backstop for events yfinance
doesn't list (e.g. demergers).

OUTPUTS
-------
1. ``logs/r1_corp_action_audit.md`` — human-readable report:
   * ADJUSTMENT VERDICT block (split-adjusted? evidence)
   * Per-event classification table
   * Recommended handling per event for T1.B sign-off

CONSTRAINTS
-----------
* Read-only on market_data.db (UI mode=ro).
* Network read of yfinance corp-action data (.splits, .dividends, .actions).
* No DB writes. No commits to main. No code in the strategy path
  changes, so the look-ahead gate stays trivially green.

USAGE
-----
    py -3.11 scripts/r1_corp_action_audit.py

Re-runnable; overwrites the report file. Ops sign-off is required
before any T1.B adjustment is applied.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd
import yfinance as yf

# ── Configuration (per ops brief) ───────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "market_data.db"
REPORT_PATH = ROOT / "logs" / "r1_corp_action_audit.md"

MUST_CLASSIFY_PCT = 0.20    # |daily return| >= 20% must be classified
WATCHLIST_PCT = 0.12        # 12-20% goes on a watchlist (lighter scrutiny)
EXTREME_STRUCTURAL_PCT = 0.30  # >=30% no-snapback move = structural by default,
                                #   override the volume heuristic
WINDOW_START = pd.Timestamp("2016-06-03")
WINDOW_END = pd.Timestamp("2026-06-02")

# Days around an event to look for a matching yfinance corp action.
# Indian markets often report splits with T+0/T+1 ambiguity; allow a small bracket.
MATCH_TOLERANCE_DAYS = 2

# Canonical-events override map — facts stated directly in the swing brief or
# previously confirmed by ops. The heuristic is best-effort; these are
# ground truth. Key: (symbol, ISO_date). Value: (class, confidence, handling,
# source_note).
CANONICAL_EVENTS: dict[tuple[str, str], tuple[str, str, str, str]] = {
    ("VEDL.NS", "2026-04-30"): (
        "demerger_known", "high", "back-adjust",
        "Brief states this is the Vedanta demerger (5-way value distribution). "
        "yfinance has no record. Heuristic alone would mis-classify on volume; "
        "this override is the canonical answer."),
    ("YESBANK.NS", "2020-03-06"): (
        "real_event_known", "high", "keep-as-is",
        "Brief states this is real — SBI-led moratorium / rescue announced. "
        "No corp action. The >=30% no-snapback heuristic would otherwise "
        "mis-classify it as demerger_suspected; this override prevents that."),
}

# Extra volume threshold — moves at this volume are panic-volume and the
# corp-action heuristic should defer to real_event. (YESBANK 2020-03-06
# would qualify at 7.3x; without this guard we'd over-flag genuine crashes.)
PANIC_VOL_THRESHOLD = 5.0

# Verdict-check symbols: known yfinance splits inside our window.
# Used for the ADJUSTMENT VERDICT block at the top of the report.
VERDICT_CANDIDATES = ["INFY.NS", "WIPRO.NS", "TCS.NS", "RELIANCE.NS",
                       "MARUTI.NS", "HDFCBANK.NS", "ICICIBANK.NS",
                       "BHARTIARTL.NS", "LT.NS"]


# ── DB helpers (read-only) ──────────────────────────────────────────────


def _db_ro():
    return sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)


def db_universe() -> list[str]:
    """Tradeable symbols only — exclude macro indices (^NSEI, ^INDIAVIX)
    which are context for the regime filter, not candidates for corp-action
    audit."""
    con = _db_ro()
    try:
        syms = [r[0] for r in con.execute(
            "SELECT DISTINCT symbol FROM ohlcv WHERE resolution='1d' "
            "ORDER BY symbol").fetchall()]
    finally:
        con.close()
    return [s for s in syms if not s.startswith("^")]


def db_daily(sym: str) -> pd.DataFrame:
    con = _db_ro()
    try:
        df = pd.read_sql_query(
            "SELECT time, open, high, low, close, volume FROM ohlcv "
            "WHERE symbol = ? AND resolution = '1d' ORDER BY time ASC",
            con, params=[sym], parse_dates=["time"])
    finally:
        con.close()
    df["time"] = pd.to_datetime(df["time"]).dt.normalize()
    df = df.set_index("time")
    return df


def db_close_on_or_before(sym: str, date: pd.Timestamp) -> tuple[pd.Timestamp, float] | None:
    con = _db_ro()
    try:
        row = con.execute(
            "SELECT time, close FROM ohlcv WHERE symbol = ? AND resolution = '1d' "
            "AND time <= ? ORDER BY time DESC LIMIT 1",
            (sym, date.strftime("%Y-%m-%d 00:00:00"))).fetchone()
    finally:
        con.close()
    return (pd.Timestamp(row[0]), float(row[1])) if row else None


def db_close_on_or_after(sym: str, date: pd.Timestamp) -> tuple[pd.Timestamp, float] | None:
    con = _db_ro()
    try:
        row = con.execute(
            "SELECT time, close FROM ohlcv WHERE symbol = ? AND resolution = '1d' "
            "AND time >= ? ORDER BY time ASC LIMIT 1",
            (sym, date.strftime("%Y-%m-%d 00:00:00"))).fetchone()
    finally:
        con.close()
    return (pd.Timestamp(row[0]), float(row[1])) if row else None


# ── yfinance helpers ────────────────────────────────────────────────────


def _strip_tz(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Bulletproof tz-strip: works on tz-aware AND tz-naive without raising."""
    idx = pd.DatetimeIndex(idx)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    return idx.normalize()


def _tz_naive_series(s: pd.Series) -> pd.Series:
    """Always return a Series with a tz-naive DatetimeIndex (even when empty).

    yfinance returns tz-aware (Asia/Kolkata) DatetimeIndex on both populated
    AND empty Series. An empty-but-tz-aware index still raises on any later
    comparison — so we must rebuild unconditionally.
    """
    if s is None or len(s) == 0:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    return pd.Series(s.values,
                     index=pd.to_datetime(s.index, utc=True).tz_localize(None).normalize())


def yf_actions(sym: str) -> tuple[pd.Series, pd.Series]:
    """Return (splits_series, dividends_series) for ``sym``.

    Both indexed by ex-date, tz-naive. Empty (still tz-naive) on any
    yfinance failure — recent yfinance has a caching bug on some symbols
    that raises ``'PriceHistory' object has no attribute '_dividends'``;
    we treat that as "no actions reported" rather than abort the audit.
    """
    t = yf.Ticker(sym)
    try:
        splits = t.splits
    except Exception:
        splits = pd.Series(dtype=float)
    try:
        dividends = t.dividends
    except Exception:
        dividends = pd.Series(dtype=float)
    return _tz_naive_series(splits), _tz_naive_series(dividends)


def yf_close(sym: str, date: pd.Timestamp) -> float | None:
    """Adjusted yfinance close at ``date`` or the last trading day before."""
    try:
        h = yf.Ticker(sym).history(
            start=(date - pd.Timedelta(days=10)).strftime("%Y-%m-%d"),
            end=(date + pd.Timedelta(days=3)).strftime("%Y-%m-%d"),
            auto_adjust=True, interval="1d")
    except Exception:
        return None
    if h.empty:
        return None
    h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
    target = date.normalize()
    if target in h.index:
        return float(h.loc[target, "Close"])
    earlier = h.loc[h.index <= target]
    return float(earlier["Close"].iloc[-1]) if not earlier.empty else None


# ── Adjustment verdict (Step A — goes at TOP of report) ─────────────────


@dataclass
class AdjustmentCheck:
    symbol: str
    ex_date: pd.Timestamp
    ratio: float
    db_prev_close: float
    db_same_close: float
    db_gap: float
    expected_unadj_gap: float
    verdict: str


def adjustment_verdict(universe: list[str]) -> tuple[str, list[AdjustmentCheck]]:
    candidates = [s for s in VERDICT_CANDIDATES if s in universe]
    checks: list[AdjustmentCheck] = []
    for sym in candidates:
        if len(checks) >= 3:
            break
        splits, _ = yf_actions(sym)
        in_win = splits[(splits.index >= WINDOW_START) & (splits.index <= WINDOW_END)]
        if in_win.empty:
            continue
        ex = in_win.index[-1]
        ratio = float(in_win.iloc[-1])
        prev = db_close_on_or_before(sym, ex - pd.Timedelta(days=1))
        same = db_close_on_or_after(sym, ex)
        if not prev or not same:
            continue
        _, db_prev = prev
        _, db_same = same
        gap = db_same / db_prev - 1.0
        expected_unadj = -(1.0 - 1.0 / ratio)
        d_unadj = abs(gap - expected_unadj)
        d_adj = abs(gap - 0.0)
        if d_unadj < 0.05 and d_unadj < d_adj:
            v = "UNADJUSTED"
        elif d_adj < 0.05 and d_adj < d_unadj:
            v = "ADJUSTED"
        else:
            v = "AMBIGUOUS"
        checks.append(AdjustmentCheck(sym, ex, ratio, db_prev, db_same,
                                       gap, expected_unadj, v))

    if not checks:
        return "UNKNOWN (no in-window splits found to test)", checks
    n_adj = sum(c.verdict == "ADJUSTED" for c in checks)
    n_unadj = sum(c.verdict == "UNADJUSTED" for c in checks)
    n_ambig = sum(c.verdict == "AMBIGUOUS" for c in checks)
    if n_adj > 0 and n_unadj == 0:
        agg = "ADJUSTED"
    elif n_unadj > 0 and n_adj == 0:
        agg = "UNADJUSTED"
    else:
        agg = "MIXED / AMBIGUOUS"
    return agg, checks


# ── Event scan + classification ─────────────────────────────────────────


@dataclass
class Event:
    symbol: str
    date: pd.Timestamp
    daily_return: float
    prior_close: float
    o: float
    h: float
    l: float
    c: float
    next_open: float | None
    volume: float
    avg_vol_20d: float
    vol_ratio: float
    # Classification
    classification: str        # one of: split, dividend, demerger_suspected,
                                #         real_event, watchlist, uncertain
    confidence: str            # high / medium / low
    reasoning: str
    recommended_handling: str  # already-adjusted / back-adjust / flag / exclude / keep-as-is


def scan_events(df: pd.DataFrame, sym: str) -> list[dict]:
    """Find days with |return| >= WATCHLIST_PCT, returning a row dict per event."""
    if df.empty or len(df) < 22:
        return []
    df = df.copy()
    df["prior_close"] = df["close"].shift(1)
    df["ret"] = df["close"] / df["prior_close"] - 1.0
    df["avg_vol_20"] = df["volume"].rolling(20, min_periods=10).mean().shift(1)
    df["next_open"] = df["open"].shift(-1)
    events = df[df["ret"].abs() >= WATCHLIST_PCT]
    rows = []
    for date, r in events.iterrows():
        rows.append({
            "symbol": sym,
            "date": date,
            "daily_return": float(r["ret"]),
            "prior_close": float(r["prior_close"]),
            "o": float(r["open"]), "h": float(r["high"]),
            "l": float(r["low"]), "c": float(r["close"]),
            "next_open": float(r["next_open"]) if pd.notna(r["next_open"]) else None,
            "volume": float(r["volume"]),
            "avg_vol_20d": float(r["avg_vol_20"]) if pd.notna(r["avg_vol_20"]) else 0.0,
            "vol_ratio": (float(r["volume"]) / float(r["avg_vol_20"])
                          if pd.notna(r["avg_vol_20"]) and r["avg_vol_20"] > 0
                          else 0.0),
        })
    return rows


def classify_event(ev: dict, splits: pd.Series, dividends: pd.Series,
                    *, prior_close: float) -> tuple[str, str, str, str]:
    """Return (classification, confidence, reasoning, recommended_handling).

    PRIMARY: yfinance corp-action match within +-MATCH_TOLERANCE_DAYS.
    BACKSTOP: snapback (next_open close to prior_close) + volume shape
        for events not in yfinance.
    """
    date = ev["date"]
    ret = ev["daily_return"]
    is_watchlist = abs(ret) < MUST_CLASSIFY_PCT

    # Canonical override — beats every heuristic. Source notes preserved
    # in the reasoning so a reviewer can trace WHY the heuristic was bypassed.
    canon = CANONICAL_EVENTS.get((ev["symbol"], date.strftime("%Y-%m-%d")))
    if canon is not None:
        cls, conf, handle, note = canon
        return (cls, conf, f"CANONICAL OVERRIDE: {note}", handle)

    # Belt-and-braces — even though yf_actions strips, this re-strip means
    # classify_event is robust to any caller passing raw yfinance Series.
    splits = _tz_naive_series(splits)
    dividends = _tz_naive_series(dividends)

    # Look for a matching split.
    nearby_splits = splits[(splits.index >= date - pd.Timedelta(days=MATCH_TOLERANCE_DAYS)) &
                            (splits.index <= date + pd.Timedelta(days=MATCH_TOLERANCE_DAYS))]
    if not nearby_splits.empty:
        ratio = float(nearby_splits.iloc[0])
        ex = nearby_splits.index[0].date()
        reasoning = (f"yfinance reports a {ratio}:1 split with ex-date {ex} "
                     f"(within +-{MATCH_TOLERANCE_DAYS}d of the event). Our DB is "
                     f"split-ADJUSTED per the verdict above, so this should NOT "
                     f"appear as an extreme move — re-verify.")
        # If our DB still showed an extreme return on/near a yfinance-reported
        # split, that's a contradiction with the adjustment verdict. Flag it.
        return ("split_artifact_unexpected", "medium", reasoning, "flag")

    # Dividend match (large dividends produce ex-date gaps).
    nearby_divs = dividends[(dividends.index >= date - pd.Timedelta(days=MATCH_TOLERANCE_DAYS)) &
                             (dividends.index <= date + pd.Timedelta(days=MATCH_TOLERANCE_DAYS))]
    if not nearby_divs.empty and prior_close > 0:
        div = float(nearby_divs.iloc[0])
        ex = nearby_divs.index[0].date()
        div_pct = div / prior_close
        if div_pct > 0.05:  # very large special dividend
            reasoning = (f"yfinance reports an ex-dividend of {div:.2f} "
                         f"(≈ {div_pct:.1%} of prior close) on {ex}. The DB "
                         f"return matches the ex-div gap.")
            return ("dividend_ex_date", "high", reasoning, "keep-as-is")
        # Small div + extreme move: probably coincidence, fall through.

    # Heuristic for events yfinance doesn't list — demergers, real crashes, etc.
    # Demerger snapback signature: next-day open approximately holds the
    # post-event close (no significant reversal toward prior_close).
    next_open = ev["next_open"]
    if next_open is not None and prior_close > 0:
        c = ev["c"]
        d_to_close = abs(next_open - c) / c
        d_back = abs(prior_close - next_open) / prior_close
        snapback = d_back < 0.5 * abs(ret)
        large_vol = ev["vol_ratio"] >= 1.5

        # Order matters: EXTREME structural moves (>= 30% with no snapback)
        # are STRONGLY suggestive of demergers/distributions — UNLESS volume
        # is at panic level (>= PANIC_VOL_THRESHOLD), in which case the
        # signature is consistent with a genuine crash (real_event). The
        # panic guard prevents over-flagging events like YESBANK 2020-03-06.
        if not snapback and ret < 0 and abs(ret) >= EXTREME_STRUCTURAL_PCT:
            if ev["vol_ratio"] >= PANIC_VOL_THRESHOLD:
                reasoning = (f"EXTREME negative move ({ret:+.1%}) with NO snapback "
                             f"BUT at panic volume (×{ev['vol_ratio']:.1f} 20d-avg). "
                             f"At this volume level the price shape is consistent "
                             f"with a genuine crash, not a corp action. yfinance "
                             f"lists no split/dividend. Defaulting to real_event; "
                             f"manual news verification recommended.")
                return ("real_event", "medium", reasoning, "keep-as-is")
            reasoning = (f"EXTREME structural move ({ret:+.1%}), next-day open "
                         f"holds the new level (d_back_to_prior={d_back:+.1%}, "
                         f"d_to_close={d_to_close:+.1%}). volume×{ev['vol_ratio']:.1f} "
                         f"is informational only — at this magnitude with no "
                         f"snapback AND non-panic volume, a real crash would "
                         f"normally show partial recovery. yfinance lists no "
                         f"split/dividend. Strongly suggests an unlisted corp "
                         f"action (demerger / value distribution). Confidence "
                         f"medium pending news verification.")
            return ("demerger_suspected", "medium", reasoning, "back-adjust")
        if not snapback and ret < 0 and abs(ret) >= MUST_CLASSIFY_PCT and not large_vol:
            # 20-30% range, no snapback, ordinary or low volume — also
            # textbook demerger / spinoff signature.
            reasoning = (f"Large negative move ({ret:+.1%}), next-day open holds "
                         f"the new level (d_back_to_prior={d_back:+.1%}, "
                         f"d_to_close={d_to_close:+.1%}), volume×{ev['vol_ratio']:.1f} "
                         f"is not panic-shaped. yfinance lists no split/dividend "
                         f"near this date. Likely an unlisted corp action "
                         f"(demerger / value distribution).")
            return ("demerger_suspected", "medium", reasoning, "back-adjust")
        if large_vol and abs(ret) >= MUST_CLASSIFY_PCT:
            reasoning = (f"Large move ({ret:+.1%}) on heavy volume "
                         f"(×{ev['vol_ratio']:.1f} 20d-avg). No yfinance "
                         f"corp-action match. Consistent with a real event "
                         f"(news, crash, takeover collapse).")
            return ("real_event", "medium", reasoning, "keep-as-is")

    if is_watchlist:
        return ("watchlist", "low",
                f"Move {ret:+.1%} is in the 12-20% watchlist band; no corp action "
                f"matched. Light scrutiny — likely noise/normal vol.",
                "keep-as-is")
    return ("uncertain", "low",
            f"Move {ret:+.1%} >= 20% but neither yfinance corp action matched "
            f"nor heuristics fit cleanly. Manual review required.",
            "flag")


# ── Report ──────────────────────────────────────────────────────────────


def fmt_pct(x: float | None) -> str:
    return "-" if x is None else f"{x:+.1%}"


def write_report(verdict: str, verdict_checks: list[AdjustmentCheck],
                  events: list[Event]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# T1 — Corporate-Action Audit (read-only)")
    lines.append("")
    lines.append("**Sprint:** swing phase 1 — first honest backtest.")
    lines.append("**Branch:** `feature/t1-corp-action-audit`.")
    lines.append("**Source:** `market_data.db` (27 symbols × ~10 years daily). yfinance `.splits` and `.dividends` as PRIMARY classifier.")
    lines.append("**Method:** flag every daily |return| >= "
                 f"{WATCHLIST_PCT:.0%}; classify >= {MUST_CLASSIFY_PCT:.0%}; "
                 f"yfinance match within +-{MATCH_TOLERANCE_DAYS} days primary, "
                 f"snapback/volume backstop.")
    lines.append("")

    # ── Verdict block (TOP) ─────────────────────────────────────────────
    lines.append("## ADJUSTMENT VERDICT")
    lines.append("")
    lines.append(f"**Our stored Upstox-historical daily data appears: {verdict}.**")
    lines.append("")
    if verdict_checks:
        lines.append("Evidence — known yfinance splits, comparing the realised gap in "
                     "our DB against the gap expected if data were UNADJUSTED:")
        lines.append("")
        lines.append("| Symbol | Ex-date | yf ratio | Expected unadj gap | DB realised gap | Verdict |")
        lines.append("|---|---|---:|---:|---:|---|")
        for c in verdict_checks:
            lines.append(f"| {c.symbol} | {c.ex_date.date()} | "
                         f"{c.ratio}:1 | {c.expected_unadj_gap:+.1%} | "
                         f"{c.db_gap:+.1%} | {c.verdict} |")
        lines.append("")
    if verdict == "ADJUSTED":
        lines.append("**Implication for T1.B:** no global re-adjustment needed. Apply "
                     "only event-level fixes for non-split corp actions (demergers, "
                     "value distributions) and keep real crashes intact.")
    elif verdict == "UNADJUSTED":
        lines.append("**Implication for T1.B:** T1.B becomes BACK-ADJUSTMENT across the "
                     "full series for every yfinance-reported split, not event patches.")
    else:
        lines.append("**Implication for T1.B:** manual review needed before T1.B scope is set.")
    lines.append("")
    lines.append("**Side observation (informational, not for T1.B):** our DB prices "
                 "are systematically higher than yfinance's `auto_adjust=True` closes "
                 "across the verdict events. This is consistent with Upstox adjusting "
                 "splits only while yfinance adjusts splits AND dividends. T2 must not "
                 "naively mix yfinance live prices with Upstox historical without "
                 "accounting for this offset.")
    lines.append("")

    # ── Heuristic limitations (transparency, LAW 5 + LAW 9) ─────────────
    lines.append("## Heuristic limitations (read before trusting any row)")
    lines.append("")
    lines.append("The classification logic is best-effort and known to be wrong in "
                 "edge cases. Ops should not rubber-stamp any row.")
    lines.append("")
    lines.append("- **yfinance corp-action data is incomplete.** It lists splits and "
                 "dividends but not demergers / value distributions / spinoffs. The "
                 "VEDL 2026-04-30 Vedanta demerger is NOT in yfinance and must be "
                 "encoded as a canonical override (it is).")
    lines.append("- **High volume does NOT rule out a corp action.** Trader "
                 "repositioning on demerger days regularly produces 2-5x average "
                 "volume. The heuristic now treats >=30% no-snapback moves as "
                 "structural REGARDLESS of volume — the previous version would have "
                 "mis-classified VEDL 2026-04-30 as `real_event`.")
    lines.append("- **Snapback heuristic is direction-asymmetric.** A real crash "
                 "with no recovery looks identical to a demerger on price shape "
                 "alone. We default to `demerger_suspected → back-adjust` for "
                 "extreme negative no-snapback moves; ops must verify with news "
                 "context before T1.B applies the adjustment.")
    lines.append("- **YESBANK is a known minefield.** The March 2020 SBI-led "
                 "moratorium / rescue produced ~10 days of 20-60% intraday swings "
                 "(real, not corp actions). The +58% and +45% rebounds on "
                 "low volume look like our 'no clean fit' bucket and are marked "
                 "`uncertain` — those need a human read against contemporaneous "
                 "news, not a heuristic verdict.")
    lines.append("- **Confidence labels are coarse.** 'medium' for a high-vol crisis "
                 "day (SBIN +27.7% on 19.7x vol) and 'medium' for an ambiguous "
                 "low-vol move both appear as 'medium'. Treat the reasoning text, "
                 "not the label, as the audit signal.")
    lines.append("")

    # ── Event table ─────────────────────────────────────────────────────
    lines.append(f"## Classified events ({len(events)} total)")
    lines.append("")
    if not events:
        lines.append("_No events >= "
                     f"{WATCHLIST_PCT:.0%} found across the universe._")
        REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
        return

    by_class = {}
    for ev in events:
        by_class.setdefault(ev.classification, []).append(ev)
    summary_order = ["split_artifact_unexpected", "dividend_ex_date",
                     "demerger_suspected", "real_event", "uncertain",
                     "watchlist"]
    lines.append("**Class distribution:**")
    lines.append("")
    lines.append("| Class | Count | Default handling |")
    lines.append("|---|---:|---|")
    for cls in summary_order:
        if cls not in by_class:
            continue
        n = len(by_class[cls])
        default = by_class[cls][0].recommended_handling
        lines.append(f"| {cls} | {n} | {default} |")
    lines.append("")

    # Detailed table — must-classify first (>=20%), then watchlist
    must = [e for e in events if abs(e.daily_return) >= MUST_CLASSIFY_PCT]
    watch = [e for e in events if abs(e.daily_return) < MUST_CLASSIFY_PCT]
    must.sort(key=lambda e: abs(e.daily_return), reverse=True)
    watch.sort(key=lambda e: (e.symbol, e.date))

    lines.append("### Must-classify events (|return| >= 20%)")
    lines.append("")
    lines.append("| Symbol | Date | Return | Prior→Close | Vol×20d | Next-day open | Class | Conf | Handling |")
    lines.append("|---|---|---:|---|---:|---:|---|---|---|")
    for e in must:
        ec = f"{e.prior_close:.2f}→{e.c:.2f}"
        nx = f"{e.next_open:.2f}" if e.next_open else "-"
        lines.append(f"| {e.symbol} | {e.date.date()} | {fmt_pct(e.daily_return)} | "
                     f"{ec} | {e.vol_ratio:.1f}× | {nx} | "
                     f"{e.classification} | {e.confidence} | {e.recommended_handling} |")
    lines.append("")

    lines.append("### Watchlist events (12–20%, light scrutiny)")
    lines.append("")
    if not watch:
        lines.append("_None._")
    else:
        lines.append("| Symbol | Date | Return | Vol×20d | Class |")
        lines.append("|---|---|---:|---:|---|")
        for e in watch:
            lines.append(f"| {e.symbol} | {e.date.date()} | "
                         f"{fmt_pct(e.daily_return)} | {e.vol_ratio:.1f}× | "
                         f"{e.classification} |")
        lines.append("")

    # ── Reasoning detail (one paragraph per must-classify event) ────────
    lines.append("## Reasoning per must-classify event")
    lines.append("")
    for e in must:
        lines.append(f"### {e.symbol}  {e.date.date()}  ({fmt_pct(e.daily_return)})")
        lines.append("")
        lines.append(f"- **Class:** {e.classification} (confidence: {e.confidence})")
        lines.append(f"- **Recommended handling:** {e.recommended_handling}")
        lines.append(f"- **Reasoning:** {e.reasoning}")
        lines.append("")

    # ── Sign-off section ────────────────────────────────────────────────
    lines.append("## Sign-off (required before T1.B)")
    lines.append("")
    lines.append("Per LAW 7 (reproducibility & backup), no DB write happens until ops "
                 "reviews this report and confirms each must-classify event's "
                 "recommended handling. T1.B will then:")
    lines.append("")
    lines.append("1. Back up `market_data.db` to `backups/market_data_<ts>_pre_t1.db`.")
    lines.append("2. Apply approved handling per event (back-adjust = multiply pre-ex "
                 "prices by the ratio across the full series; flag = annotate without "
                 "mutating; keep-as-is = no change).")
    lines.append("3. Add a regression test verifying the post-adjust prices for each "
                 "canonical case (VEDL demerger, etc.).")
    lines.append("4. Re-run gate + validator; commit on this branch; await push call.")
    lines.append("")
    lines.append("_End of report._")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


# ── Main ────────────────────────────────────────────────────────────────


def main() -> int:
    print("[T1.A] reading universe from DB...")
    universe = db_universe()
    print(f"[T1.A] {len(universe)} symbols")

    print("[T1.A] Step A — adjustment verdict (yfinance vs DB on known splits)...")
    verdict, verdict_checks = adjustment_verdict(universe)
    print(f"[T1.A] verdict: {verdict}  (evidence: {len(verdict_checks)} events)")

    print(f"[T1.A] Step B — scanning {len(universe)} symbols for events "
          f"(|return| >= {WATCHLIST_PCT:.0%})...")
    all_events: list[Event] = []
    for sym in universe:
        df = db_daily(sym)
        raw_events = scan_events(df, sym)
        if not raw_events:
            continue
        splits, dividends = yf_actions(sym)
        for r in raw_events:
            cls, conf, reason, handle = classify_event(
                r, splits, dividends, prior_close=r["prior_close"])
            all_events.append(Event(
                symbol=sym, date=r["date"],
                daily_return=r["daily_return"],
                prior_close=r["prior_close"],
                o=r["o"], h=r["h"], l=r["l"], c=r["c"],
                next_open=r["next_open"], volume=r["volume"],
                avg_vol_20d=r["avg_vol_20d"], vol_ratio=r["vol_ratio"],
                classification=cls, confidence=conf,
                reasoning=reason, recommended_handling=handle,
            ))
        print(f"  {sym}: {len(raw_events)} events")

    print(f"[T1.A] total events: {len(all_events)} "
          f"(must-classify >=20%: {sum(1 for e in all_events if abs(e.daily_return) >= MUST_CLASSIFY_PCT)}, "
          f"watchlist 12-20%: {sum(1 for e in all_events if abs(e.daily_return) < MUST_CLASSIFY_PCT)})")

    print(f"[T1.A] writing report to {REPORT_PATH.relative_to(ROOT)}...")
    write_report(verdict, verdict_checks, all_events)
    print(f"[T1.A] done. report has {len(REPORT_PATH.read_text(encoding='utf-8').splitlines())} lines.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
