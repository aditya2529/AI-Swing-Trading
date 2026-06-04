"""MOM-3 — walk-forward backtest of MomentumStrategy with the
mandatory robustness suite, survivorship-DISCOUNTED PF as the headline,
and a momentum-crash drawdown diagnostic.

OPS-DIRECTED RUN PARAMETERS (NOT historical tuning — matched to
the strategy's design)
==========================================================================
* ``max_positions    = MOM_TOP_N (= 15)``      — strategy holds top-15
* ``max_per_sector   = 5``                     — momentum often clusters
                                                  by sector; the per-sector
                                                  cap is the DESIGN LEVER
                                                  that controls cluster
                                                  exposure. Loose enough
                                                  to allow a real momentum
                                                  read, tight enough that
                                                  any single sector cannot
                                                  drive the entire portfolio.
* ``max_heat         = 0.20``                  — 15 positions × 1% per-trade
                                                  risk (default ``MAX_RISK_PCT``)
                                                  = 15% sum-of-open-risk; cap
                                                  at 20% gives a 5pp safety
                                                  margin without binding under
                                                  normal conditions.
* All other harness defaults unchanged (``risk_pct``, slippage, brokerage,
  initial capital).
* ``record_decisions = False`` — we do not need per-day decision logs for
  the verdict; the look-ahead regression suite (tests/) already proved
  the strategy is causal.

THREE WINDOWS, ONE VERDICT
==========================
* INSPECT  : 2016-01-04 → 2022-12-30     (descriptive — NOT the verdict)
* HELD-OUT : 2023-01-02 → 2026-06-03     ★ the verdict window ★
* FULL     : 2016-01-04 → 2026-06-03     (for completeness)

For every window: profit factor, Sharpe, max DD, win rate, n_trades,
CAGR. Plus the full robustness suite from ``backtesting/diagnostics.py``:
per-symbol PF, per-year PF, PF-with-top-symbol-removed,
PF-with-best-year-removed, # negative symbols, concentration flags,
binomial p, bootstrap PF CI.

SURVIVORSHIP-DISCOUNTED PF IS THE HEADLINE
==========================================
``MOMENTUM_UNIVERSE`` is CURRENT NSE-200-ish membership. Names that
were liquid 10 years ago but have since delisted / merged out are
entirely absent — and that absence systematically inflates the raw
backtested PF (selection bias toward survivors, which tend to be the
historical winners). We report TWO bounds:

  * ``PF × 0.70``  — 30% haircut (CONSERVATIVE — the headline)
  * ``PF × 0.75``  — 25% haircut (lighter)

The gate evaluation prints results against BOTH raw and 30%-discounted
PF, but the conservative discounted number is what the deploy
decision should reference.

MOMENTUM-CRASH DD CHECK
=======================
Momentum has a well-documented failure mode: sharp trend reversals
("momentum crashes") that crater the top-N portfolio while the laggards
suddenly outperform. We report:

  * The single deepest drawdown in the equity curve (peak, trough, magnitude).
  * DD magnitudes specifically over historically-identified crash windows:
    2018 vol spike, 2020-03 COVID, 2022 reversal, 2024 election period.

These are CALENDAR windows identified ex-ante (not parameter-tuned to
match the equity curve), so they are diagnostic, not curve-fit.

WARM-UP NOTE
============
MomentumStrategy needs ``MOM_LOOKBACK_DAYS + MOM_SKIP_DAYS + 1`` = 274
bars of causal history before it can score a symbol. Every replay
receives the FULL data dict; ``run_replay``'s ``start``/``end`` constrains
ONLY the decision timeline. Pre-window bars stay visible via
``view.history(sym)``. The strategy's own eligibility check defensively
skips any symbol that hasn't accumulated 274 bars yet (newer listings
— LICI, SBICARD, VBL, SBILIFE — are excluded from ranking until they
have history; this is the MOM-2 design and is correct).
"""
from __future__ import annotations

import math
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtesting.diagnostics import (
    binomial_p_value, bootstrap_pf_ci, concentration_flags, per_symbol_pnl,
    per_year_pnl, profit_factor, robustness,
)
from backtesting.replay import run_replay
from config import (
    DB_PATH, GATE_MAX_DRAWDOWN, GATE_PROFIT_FACTOR, GATE_SHARPE,
    GATE_WIN_RATE, INITIAL_CAPITAL, MOM_TOP_N,
)
from data.universe import MOMENTUM_UNIVERSE, MOMENTUM_SURVIVORSHIP_NOTE
from signals.momentum import MomentumStrategy

REPORT_PATH = PROJECT_ROOT / "logs" / "mom3_backtest_report.md"

# ── Walk-forward windows (locked BEFORE looking at any number) ─────────
INSPECT_START = pd.Timestamp("2016-01-04")
INSPECT_END   = pd.Timestamp("2022-12-30")
HOLDOUT_START = pd.Timestamp("2023-01-02")
HOLDOUT_END   = pd.Timestamp("2026-06-03")

# ── Momentum run parameters (DESIGN — per ops) ─────────────────────────
MOM_MAX_POSITIONS = MOM_TOP_N        # 15 — matches the strategy's top-N
MOM_MAX_PER_SECTOR = 5               # sector concentration lever
MOM_MAX_HEAT = 0.20                  # 15 × 1% + 5pp safety; doesn't bind normally

# ── Survivorship discount (conservative HEADLINE, light shown for compare) ─
SURVIVORSHIP_DISCOUNT_CONSERVATIVE = 0.30   # PF × 0.70 (HEADLINE)
SURVIVORSHIP_DISCOUNT_LIGHT = 0.25          # PF × 0.75 (lighter bound)

# ── Momentum-crash calendar windows (identified ex-ante) ───────────────
MOMENTUM_CRASH_WINDOWS: list[tuple[str, str, str]] = [
    ("2018 vol spike",    "2018-01-22", "2018-10-31"),
    ("2020-03 COVID",     "2020-02-19", "2020-04-30"),
    ("2022 reversal",     "2022-01-01", "2022-07-31"),
    ("2024 election",     "2024-04-01", "2024-07-31"),
]


# ── Data loading (MOMENTUM_UNIVERSE only — no macro indices) ───────────


def load_momentum_universe() -> dict[str, pd.DataFrame]:
    """Read daily bars for every symbol in ``MOMENTUM_UNIVERSE``. We
    don't pass ``^NSEI``/``^INDIAVIX`` — MomentumStrategy reads no macro
    context (the rotation IS the regime read), and the strategy
    defensively skips ``^``-prefixed symbols regardless.

    ASYMMETRIC-CALENDAR FIX (MOM-3 specific)
    ----------------------------------------
    NSE runs a handful of Sat/Sun special sessions per year (Diwali
    Muhurat trading, etc.). The DB contains ~556 such weekend bars
    over 12 years, but only the older/most-liquid names (RELIANCE,
    INFY, …) participated — most of the newer MOM-1 backfill symbols
    lack them entirely. The replay timeline is the UNION of all
    symbol indexes, so every muhurat session enters the timeline; on
    those dates most positions return ``_price(close) = None`` and
    the harness's MTM excludes them, producing a spurious one-day
    drop of the equity curve back toward ``cash`` only.

    For the MOM-3 verdict we DROP non-weekday bars at load time. This
    is contained to this script (no harness change, no strategy
    change) and makes the timeline strictly Monday-Friday — exactly
    matching the calendar coverage that ALL universe symbols share.
    The muhurat sessions carry trivial information (no monthly
    rebalance lands on them given they cluster around late October),
    so dropping them has effectively zero impact on the strategy's
    economics; what it removes is the equity-curve artifact only.

    A separate follow-up T-ticket should address this at the harness
    or DB level so all strategies benefit — see the MOM-3 report's
    follow-ups section.
    """
    out: dict[str, pd.DataFrame] = {}
    con = sqlite3.connect(f"file:{Path(DB_PATH).as_posix()}?mode=ro", uri=True)
    try:
        for sym in MOMENTUM_UNIVERSE:
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
            # Drop weekend bars (muhurat sessions, etc.) — see docstring.
            df = df[df.index.dayofweek < 5]
            out[sym] = df
    finally:
        con.close()
    return out


# ── Render helpers ─────────────────────────────────────────────────────


def _fmt_pf(pf) -> str:
    if pf is None or (isinstance(pf, float) and math.isnan(pf)):
        return "n/a"
    if pf == float("inf"):
        return "inf (no losers)"
    return f"{pf:.3f}"


def _fmt(val, decimals=3, suffix=""):
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "n/a"
    return f"{val:.{decimals}f}{suffix}"


def _gate_row(name, observed, op, threshold, passed):
    mark = "PASS" if passed else "FAIL"
    return f"| {name} | {observed} | {op} | {threshold} | {mark} |"


def _discount_pf(pf: float, discount: float) -> float:
    if pf is None or (isinstance(pf, float) and math.isnan(pf)):
        return float("nan")
    if pf == float("inf"):
        return float("inf")
    return pf * (1.0 - discount)


# ── Momentum-crash DD diagnostic ───────────────────────────────────────


def deepest_drawdown(equity: pd.Series) -> dict:
    """Single deepest drawdown in the equity curve. Returns peak/trough
    dates and the magnitude (positive number, e.g. 0.18 = -18%)."""
    if equity.empty:
        return {"peak_date": None, "trough_date": None, "magnitude": float("nan")}
    peak = equity.cummax()
    dd = (equity - peak) / peak
    trough_date = dd.idxmin()
    peak_date = equity.loc[:trough_date].idxmax()
    return {
        "peak_date": peak_date,
        "trough_date": trough_date,
        "magnitude": float(abs(dd.min())),
    }


def crash_window_dd(equity: pd.Series, windows: list[tuple[str, str, str]]
                     ) -> list[dict]:
    """DD within each pre-identified crash calendar window. Returns one
    row per window with the magnitude (0.0 if equity series doesn't
    cover the window at all)."""
    out: list[dict] = []
    if equity.empty:
        return out
    for label, s, e in windows:
        start = pd.Timestamp(s)
        end = pd.Timestamp(e)
        sub = equity.loc[(equity.index >= start) & (equity.index <= end)]
        if sub.empty:
            out.append({"label": label, "start": start, "end": end,
                        "magnitude": 0.0, "in_range": False})
            continue
        # DD within the sub-window: peak-to-trough within the window.
        peak = sub.cummax()
        dd = (sub - peak) / peak
        out.append({"label": label, "start": start, "end": end,
                    "magnitude": float(abs(dd.min())), "in_range": True})
    return out


# ── Per-window summary ─────────────────────────────────────────────────


def summarise_window(label: str, result: dict, *,
                       run_bootstrap: bool = True) -> dict:
    metrics = result["metrics"]
    trades = result["trades"]
    equity = result["equity_curve"]
    pf = metrics.get("profit_factor", float("nan"))
    sharpe = metrics.get("sharpe", float("nan"))
    mdd = metrics.get("max_drawdown", float("nan"))
    wr = metrics.get("win_rate", float("nan"))
    cagr = metrics.get("cagr", float("nan"))
    n = int(metrics.get("n_trades", 0))
    wins = int((trades["pnl"] > 0).sum()) if not trades.empty else 0
    p_val = binomial_p_value(wins, n) if n > 0 else 1.0
    bs = (bootstrap_pf_ci(trades) if run_bootstrap
          else {"p05": float("nan"), "p50": float("nan"),
                "p95": float("nan"), "n_resamples": 0})
    robust = robustness(trades)
    flags = concentration_flags(trades)
    deepest = deepest_drawdown(equity)
    crash_dds = crash_window_dd(equity, MOMENTUM_CRASH_WINDOWS)
    return {
        "label": label, "pf": pf, "sharpe": sharpe, "mdd": mdd, "wr": wr,
        "cagr": cagr, "n_trades": n, "wins": wins, "p_value": p_val,
        "bs": bs, "robust": robust, "flags": flags,
        "trades": trades, "equity": equity,
        "deepest_dd": deepest, "crash_dds": crash_dds,
        "n_days": result.get("n_days", 0),
        "start": result.get("start"), "end": result.get("end"),
    }


def gate_verdicts(s: dict, pf_to_use: float | None = None) -> tuple[dict, int]:
    """Gate evaluation. ``pf_to_use`` overrides the gate PF (used to
    show the discounted-PF gate verdict alongside the raw)."""
    pf = pf_to_use if pf_to_use is not None else s["pf"]
    pf_pass = (pf == float("inf")) or (
        isinstance(pf, float) and not math.isnan(pf) and pf > GATE_PROFIT_FACTOR)
    sharpe_pass = (not math.isnan(s["sharpe"])
                   and s["sharpe"] > GATE_SHARPE)
    mdd_pass = (not math.isnan(s["mdd"])
                and abs(s["mdd"]) < GATE_MAX_DRAWDOWN)
    wr_pass = (not math.isnan(s["wr"]) and s["wr"] > GATE_WIN_RATE)
    return ({"pf": pf_pass, "sharpe": sharpe_pass,
             "mdd": mdd_pass, "wr": wr_pass},
            int(pf_pass) + int(sharpe_pass) + int(mdd_pass) + int(wr_pass))


# ── Markdown blocks ────────────────────────────────────────────────────


def headline_block(s: dict) -> list[str]:
    pf_raw = s["pf"]
    pf_disc = _discount_pf(pf_raw, SURVIVORSHIP_DISCOUNT_CONSERVATIVE)
    pf_light = _discount_pf(pf_raw, SURVIVORSHIP_DISCOUNT_LIGHT)
    lines: list[str] = []
    lines.append(f"### {s['label']} window")
    lines.append("")
    lines.append(f"- **Profit Factor (raw):** {_fmt_pf(pf_raw)}")
    lines.append(f"- **★ Profit Factor (30% survivorship discount — HEADLINE):** "
                  f"**{_fmt_pf(pf_disc)}**")
    lines.append(f"- Profit Factor (25% discount, lighter): {_fmt_pf(pf_light)}")
    lines.append(f"- **Sharpe ratio:** {_fmt(s['sharpe'])}")
    if not math.isnan(s["mdd"]):
        lines.append(f"- **Max drawdown:** {_fmt(abs(s['mdd']), 4)} "
                      f"(= {_fmt(abs(s['mdd']) * 100, 2)}%)")
    else:
        lines.append("- **Max drawdown:** n/a")
    lines.append(f"- **Win rate:** {_fmt(s['wr'])} "
                  f"({s['wins']} of {s['n_trades']})")
    lines.append(f"- **CAGR:** {_fmt(s['cagr'])}")
    lines.append(f"- **n_trades:** {s['n_trades']}")
    if s["start"] is not None and s["end"] is not None:
        lines.append(f"- Replay window: {s['start'].date()} -> {s['end'].date()} "
                      f"({s['n_days']} trading days)")
    lines.append("")
    return lines


def gate_block(s: dict) -> list[str]:
    """Show gates evaluated against BOTH raw and 30%-discounted PF.
    The discounted-PF row is the deploy reference per ops."""
    lines: list[str] = []
    raw_verdicts, raw_n = gate_verdicts(s)
    pf_disc = _discount_pf(s["pf"], SURVIVORSHIP_DISCOUNT_CONSERVATIVE)
    disc_verdicts, disc_n = gate_verdicts(s, pf_to_use=pf_disc)
    lines.append("| Gate | Observed | Required | Threshold | Raw | Discounted (30%) |")
    lines.append("|---|---|---|---:|:---:|:---:|")
    pf_raw_str = _fmt_pf(s["pf"])
    pf_disc_str = _fmt_pf(pf_disc)
    lines.append(
        f"| Profit Factor | raw {pf_raw_str} / disc {pf_disc_str} | > | "
        f"{GATE_PROFIT_FACTOR} | "
        f"{'PASS' if raw_verdicts['pf'] else 'FAIL'} | "
        f"{'PASS' if disc_verdicts['pf'] else 'FAIL'} |")
    sh = _fmt(s["sharpe"])
    lines.append(
        f"| Sharpe ratio | {sh} | > | {GATE_SHARPE} | "
        f"{'PASS' if raw_verdicts['sharpe'] else 'FAIL'} | "
        f"{'PASS' if disc_verdicts['sharpe'] else 'FAIL'} |")
    if math.isnan(s["mdd"]):
        mdd_str = "n/a"
    else:
        mdd_str = _fmt(abs(s["mdd"]), 4)
    lines.append(
        f"| Max drawdown (mag) | {mdd_str} | < | {GATE_MAX_DRAWDOWN} | "
        f"{'PASS' if raw_verdicts['mdd'] else 'FAIL'} | "
        f"{'PASS' if disc_verdicts['mdd'] else 'FAIL'} |")
    wr_str = _fmt(s["wr"])
    lines.append(
        f"| Win rate | {wr_str} | > | {GATE_WIN_RATE} | "
        f"{'PASS' if raw_verdicts['wr'] else 'FAIL'} | "
        f"{'PASS' if disc_verdicts['wr'] else 'FAIL'} |")
    lines.append("")
    lines.append(f"**Gates cleared (raw): {raw_n} of 4** | "
                  f"**Gates cleared (30% discount): {disc_n} of 4** "
                  f"<-- DEPLOY REFERENCE")
    lines.append("")
    return lines


def robustness_block(s: dict) -> list[str]:
    r = s["robust"]
    f = s["flags"]
    lines: list[str] = []
    lines.append("| Question | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Raw PF | {_fmt_pf(r.pf_raw)} |")
    if r.top_symbol:
        lines.append(
            f"| PF with top-contributing symbol removed ({r.top_symbol}, "
            f"Rs {r.top_symbol_pnl:+,.0f}) | {_fmt_pf(r.pf_ex_top_symbol)} |")
    else:
        lines.append("| PF with top-contributing symbol removed | n/a "
                      "(no positive-PnL symbol to remove) |")
    if r.best_year:
        lines.append(
            f"| PF with best year removed ({r.best_year}, "
            f"Rs {r.best_year_pnl:+,.0f}) | {_fmt_pf(r.pf_ex_best_year)} |")
    else:
        lines.append("| PF with best year removed | n/a |")
    n_traded_sym = len(per_symbol_pnl(s["trades"]))
    lines.append(f"| # symbols with net-negative PnL | "
                  f"{r.n_negative_symbols} of {n_traded_sym} |")
    lines.append(f"| Top-symbol share of gross-positive PnL | "
                  f"{_fmt(f['top_symbol_share'] * 100, 1, '%')} |")
    lines.append(f"| Top-year share of gross-positive PnL | "
                  f"{_fmt(f['top_year_share'] * 100, 1, '%')} |")
    lines.append("")
    if f["one_symbol_carries"]:
        lines.append(f"**Concentration flag:** one symbol ({r.top_symbol}) "
                      f"carries {f['top_symbol_share']*100:.1f}% of gross PnL.")
    if f["one_year_carries"]:
        lines.append(f"**Concentration flag:** one year ({r.best_year}) "
                      f"carries {f['top_year_share']*100:.1f}% of gross PnL.")
    lines.append("")
    return lines


def momentum_crash_block(s: dict) -> list[str]:
    """The momentum-specific DD diagnostic: single deepest DD + DD over
    each pre-identified crash calendar window."""
    lines: list[str] = []
    deepest = s["deepest_dd"]
    if deepest["peak_date"] is not None:
        lines.append("**Single deepest drawdown in equity curve:**")
        lines.append("")
        lines.append(
            f"- Peak {deepest['peak_date'].date()} -> "
            f"Trough {deepest['trough_date'].date()}, magnitude "
            f"**{deepest['magnitude'] * 100:.2f}%**")
        lines.append("")
    lines.append("**Drawdown over historically-identified momentum-crash "
                  "windows (calendar — not curve-fit):**")
    lines.append("")
    lines.append("| Crash window | Range | Equity DD within window |")
    lines.append("|---|---|---:|")
    for row in s["crash_dds"]:
        rng = f"{row['start'].date()} -> {row['end'].date()}"
        if not row["in_range"]:
            mag = "_outside replay range_"
        else:
            mag = f"{row['magnitude'] * 100:.2f}%"
        lines.append(f"| {row['label']} | {rng} | {mag} |")
    lines.append("")
    return lines


def per_symbol_block(s: dict) -> list[str]:
    sym_df = per_symbol_pnl(s["trades"])
    if sym_df.empty:
        return ["_No trades to break down._", ""]
    lines: list[str] = []
    lines.append("| Symbol | n_trades | n_wins | Win rate | PF | Total PnL (Rs) |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for _, r in sym_df.iterrows():
        lines.append(f"| {r['symbol']} | {int(r['n_trades'])} | "
                      f"{int(r['n_wins'])} | {r['win_rate']:.3f} | "
                      f"{_fmt_pf(r['pf'])} | {r['total_pnl']:+,.0f} |")
    lines.append("")
    return lines


def per_year_block(s: dict) -> list[str]:
    yr_df = per_year_pnl(s["trades"])
    if yr_df.empty:
        return ["_No trades to break down._", ""]
    lines: list[str] = []
    lines.append("| Year | n_trades | Win rate | PF | Total PnL (Rs) |")
    lines.append("|---:|---:|---:|---:|---:|")
    for _, r in yr_df.iterrows():
        lines.append(f"| {int(r['year'])} | {int(r['n_trades'])} | "
                      f"{r['win_rate']:.3f} | {_fmt_pf(r['pf'])} | "
                      f"{r['total_pnl']:+,.0f} |")
    lines.append("")
    return lines


def significance_block(s: dict) -> list[str]:
    lines: list[str] = []
    lines.append(f"**Binomial test** (null: no edge -> win rate 50%)")
    lines.append("")
    lines.append(f"- Observed: {s['wins']} wins in {s['n_trades']} trades "
                  f"(win rate {_fmt(s['wr'])}).")
    lines.append(f"- P(X >= {s['wins']} | n={s['n_trades']}, p=0.5) = "
                  f"**{s['p_value']:.4f}**")
    if s["n_trades"] < 30:
        lines.append(f"- **n_trades = {s['n_trades']} < 30** -- per LAW 8 "
                      f"this sample is too small to call the edge real or "
                      f"fake.")
    elif s["p_value"] < 0.05:
        lines.append("- p < 0.05 -- win rate significantly above chance.")
    elif s["p_value"] < 0.10:
        lines.append("- p < 0.10 -- marginally above chance.")
    else:
        lines.append("- p >= 0.10 -- NOT statistically distinguishable "
                      "from chance.")
    lines.append("")
    lines.append(f"**Bootstrap CI on PF** "
                  f"({s['bs']['n_resamples']} resamples)")
    lines.append("")
    lines.append(f"- 5th / 50th / 95th percentile: "
                  f"{_fmt_pf(s['bs']['p05'])} / "
                  f"{_fmt_pf(s['bs']['p50'])} / "
                  f"{_fmt_pf(s['bs']['p95'])}")
    if (not math.isnan(s["bs"]["p05"]) and
            not math.isnan(s["bs"]["p95"])):
        if s["bs"]["p05"] < 1.0 < s["bs"]["p95"]:
            lines.append("- 90% CI **spans 1.0** -- bootstrap cannot rule "
                          "out break-even. Edge is uncertain.")
        elif s["bs"]["p05"] >= 1.0:
            lines.append("- 5th-percentile PF >= 1.0 -- pessimistic tail "
                          "still positive.")
        else:
            lines.append("- 95th-percentile PF < 1.0 -- optimistic tail "
                          "still negative.")
    lines.append("")
    return lines


# ── Verdict ────────────────────────────────────────────────────────────


def held_out_verdict(s_holdout: dict, s_inspect: dict | None) -> list[str]:
    out: list[str] = []
    pf_raw = s_holdout["pf"]
    pf_disc = _discount_pf(pf_raw, SURVIVORSHIP_DISCOUNT_CONSERVATIVE)
    n = s_holdout["n_trades"]
    f = s_holdout["flags"]
    r = s_holdout["robust"]
    too_few = n < 30

    raw_verdicts, raw_n = gate_verdicts(s_holdout)
    disc_verdicts, disc_n = gate_verdicts(s_holdout, pf_to_use=pf_disc)

    if n == 0:
        out.append("**HELD-OUT verdict -- NO TRADES.** The strategy did not "
                    "place a single trade in the held-out window. Either "
                    "the eligibility filters bound too tight, or no name "
                    "ever cleared the top-15 ranking. NOT a deploy.")
        return out

    if too_few:
        out.append(f"**HELD-OUT verdict -- NOT SIGNIFICANT.** The strategy "
                    f"placed only **{n} trades** in the "
                    f"{HOLDOUT_START.date()} -> {HOLDOUT_END.date()} "
                    f"held-out window (< 30 per LAW 8). Statistical claims "
                    f"at this sample size are weak.")
        out.append("")

    out.append(f"**Held-out gates cleared (raw): {raw_n} of 4** "
                f"(PF raw {_fmt_pf(pf_raw)}, Sharpe "
                f"{_fmt(s_holdout['sharpe'])}, |max DD| "
                f"{_fmt(abs(s_holdout['mdd']) if not math.isnan(s_holdout['mdd']) else float('nan'), 3)}, "
                f"win {_fmt(s_holdout['wr'])}).")
    out.append("")
    out.append(f"**★ Held-out gates cleared (30% survivorship discount -- "
                f"DEPLOY REFERENCE): {disc_n} of 4** "
                f"(PF disc {_fmt_pf(pf_disc)}).")
    out.append("")

    if f["one_symbol_carries"] or f["one_year_carries"]:
        bullets = []
        if f["one_symbol_carries"]:
            bullets.append(f"one symbol ({r.top_symbol}) carries "
                            f"{f['top_symbol_share']*100:.1f}% of gross PnL")
        if f["one_year_carries"]:
            bullets.append(f"one year ({r.best_year}) carries "
                            f"{f['top_year_share']*100:.1f}% of gross PnL")
        out.append(f"**Concentration in held-out:** {'; '.join(bullets)}. "
                    f"The robustness table shows PF with that contributor "
                    f"removed; treat the headline as fragile.")
        out.append("")

    if (not math.isnan(s_holdout["mdd"])
            and abs(s_holdout["mdd"]) >= GATE_MAX_DRAWDOWN):
        out.append(f"**Max drawdown {abs(s_holdout['mdd']) * 100:.1f}% on "
                    f"held-out is above the {GATE_MAX_DRAWDOWN:.0%} gate.** "
                    f"This is the momentum-crash failure mode — see the "
                    f"crash-window DD table above for which calendar window "
                    f"the worst DD landed in.")
        out.append("")

    # Regime divergence between INSPECT and HELD-OUT.
    if s_inspect is not None and s_inspect["n_trades"] > 0:
        inspect_pf = s_inspect["pf"]
        if (isinstance(inspect_pf, float) and not math.isnan(inspect_pf)
                and inspect_pf != float("inf") and inspect_pf < 1.0
                and not math.isnan(pf_raw) and pf_raw > 1.5):
            out.append(f"**REGIME-DIVERGENCE CALLOUT.** Held-out raw PF "
                        f"{_fmt_pf(pf_raw)} but the inspect window PF was "
                        f"{_fmt_pf(inspect_pf)} (a losing strategy over "
                        f"{s_inspect['n_trades']} trades). The strategy "
                        f"works on the held-out window's regime but failed "
                        f"on the inspect window's regime. Live deployment "
                        f"would face BOTH regimes.")
            out.append("")

    if disc_n == 4 and not f["one_symbol_carries"] and not f["one_year_carries"]:
        out.append("**Tentative deploy candidate.** The held-out window "
                    "cleared all four gates AFTER the 30% survivorship "
                    "discount, with no concentration flags. Paper-trade "
                    "per LAW 3 before any real capital -- live PF is "
                    "expected to run ~30-50% below backtest.")
    elif raw_n == 4 and disc_n < 4:
        out.append("**Not a deploy candidate.** Raw PF clears the gate but "
                    "the 30% survivorship discount sinks it. The deploy "
                    "reference is the discounted number per the MOM brief.")
    else:
        out.append("**Not a deploy candidate at this calibration.** The "
                    "discounted-gate verdict is the deploy signal; it "
                    "fails. Calibration changes are listed below as "
                    "proposed T-tickets (one change at a time per LAW 4) "
                    "-- NOT applied here.")
    return out


# ── Followups ──────────────────────────────────────────────────────────


def proposed_followups(s_holdout: dict, s_inspect: dict | None) -> list[str]:
    items: list[str] = []
    n = s_holdout["n_trades"]
    f = s_holdout["flags"]
    r = s_holdout["robust"]
    mdd = s_holdout["mdd"]
    pf = s_holdout["pf"]

    if n == 0:
        items.append("**T-candidate: MOM held-out produced zero trades.** "
                      "Investigate whether the eligibility filter (>= 274 "
                      "bars) is excluding too many names, or whether the "
                      "top-15 ranking is too narrow on this universe.")
        return [f"- {it}" for it in items]

    if not math.isnan(mdd) and abs(mdd) >= GATE_MAX_DRAWDOWN:
        items.append(f"**T-candidate: portfolio-level DD cap for MOM** "
                      f"(reuse the MR-4 ``dd_cap_pct`` harness param). "
                      f"Held-out |max DD| = {abs(mdd)*100:.1f}% vs the "
                      f"{GATE_MAX_DRAWDOWN:.0%} gate. Note the symmetric "
                      f"re-arm caveat MR-4 surfaced.")

    if f["one_symbol_carries"]:
        items.append(f"**T-candidate: single-symbol concentration** -- "
                      f"{r.top_symbol} carries "
                      f"{f['top_symbol_share']*100:.1f}% of gross PnL on "
                      f"held-out. Check whether tightening "
                      f"``MAX_PER_SECTOR`` would dampen this concentration.")

    if f["one_year_carries"]:
        items.append(f"**T-candidate: year concentration** -- year "
                      f"{r.best_year} carries the held-out edge. Look at "
                      f"per-year PF for regime characterisation; consider a "
                      f"trend-strength filter (only rebalance when median "
                      f"top-15 12-1 score exceeds a threshold).")

    pf_disc = _discount_pf(pf, SURVIVORSHIP_DISCOUNT_CONSERVATIVE)
    if (isinstance(pf_disc, float) and not math.isnan(pf_disc)
            and pf_disc < GATE_PROFIT_FACTOR):
        items.append("**T-candidate: trend-strength filter** -- only "
                      "rebalance into names whose 12-1 score exceeds a "
                      "MINIMUM (e.g. > 0.10). Filters out top-N selections "
                      "that are merely 'least bad' in a bear market. Calibrate "
                      "the threshold on INSPECT ONLY, then re-evaluate on "
                      "HELD-OUT.")

    if s_holdout["n_trades"] < 30:
        items.append("**T-candidate: extend held-out evaluation period.** "
                      "Once more bars accumulate, re-run to push held-out "
                      "n above 30 for statistical significance.")

    if not items:
        items.append("None mechanically triggered. If the human read of the "
                      "tape suggests something specific, propose it as a "
                      "T-ticket.")
    return [f"- {it}" for it in items]


# ── Compose ────────────────────────────────────────────────────────────


def build_report(s_full: dict, s_inspect: dict, s_holdout: dict,
                  *, run_params: dict, t_elapsed: float,
                  n_symbols_loaded: int) -> str:
    lines: list[str] = []
    lines.append("# MOM-3 -- Cross-Sectional Momentum Walk-Forward Backtest")
    lines.append("")
    lines.append("**Branch:** `feature/mom3-momentum-backtest`")
    lines.append("**Strategy:** `signals.momentum.MomentumStrategy` "
                  "(pure rules -- 12-1 Jegadeesh-Titman, monthly rotation, "
                  "top-15, ATR catastrophe stop)")
    lines.append("**Replay data:** {} of {} MOMENTUM_UNIVERSE symbols "
                  "(rest had no rows in market_data.db).".format(
                    n_symbols_loaded, len(MOMENTUM_UNIVERSE)))
    lines.append(f"**Initial capital:** Rs {INITIAL_CAPITAL:,.0f}")
    lines.append(f"**Wall-clock:** {t_elapsed:.1f}s")
    lines.append("")

    # Run params block
    lines.append("## 0. Run parameters (DESIGN -- per ops; NOT historical tuning)")
    lines.append("")
    lines.append("| Param | Value | Notes |")
    lines.append("|---|---:|---|")
    lines.append(f"| max_positions | {run_params['max_positions']} | "
                  f"= MOM_TOP_N (strategy holds top-15) |")
    lines.append(f"| max_per_sector | {run_params['max_per_sector']} | "
                  f"sector-concentration DESIGN LEVER |")
    lines.append(f"| max_heat | {run_params['max_heat']:.2f} | "
                  f"15 x 1% MAX_RISK_PCT + 5pp safety |")
    lines.append("| risk_pct | (harness default MAX_RISK_PCT = 0.01) | "
                  "unchanged |")
    lines.append("| slippage / brokerage | (config defaults) | unchanged |")
    lines.append("")
    lines.append(f"Strategy frozen knobs: lookback={252} (MOM_LOOKBACK_DAYS), "
                  f"skip={21} (MOM_SKIP_DAYS), top_n={MOM_TOP_N} (MOM_TOP_N). "
                  f"Academic 12-1 formulation -- NOT fit to data.")
    lines.append("")

    # Anti-overfit framing
    lines.append("## 1. Anti-overfit framing")
    lines.append("")
    lines.append("Three replays -- INSPECT, HELD-OUT, FULL. Strategy "
                  "parameters were chosen WITHOUT looking at the held-out "
                  "window. **The GO/NO-GO verdict is on HELD-OUT, evaluated "
                  "against the 30%-DISCOUNTED PF (per ops).**")
    lines.append("")
    lines.append("| Window | Range | Trading days | n_trades |")
    lines.append("|---|---|---:|---:|")
    lines.append(f"| INSPECT | {INSPECT_START.date()} -> "
                  f"{INSPECT_END.date()} | {s_inspect['n_days']} | "
                  f"{s_inspect['n_trades']} |")
    lines.append(f"| **HELD-OUT (verdict)** | {HOLDOUT_START.date()} -> "
                  f"{HOLDOUT_END.date()} | {s_holdout['n_days']} | "
                  f"**{s_holdout['n_trades']}** |")
    lines.append(f"| FULL (descriptive) | "
                  f"{s_full['start'].date() if s_full['start'] is not None else '?'} -> "
                  f"{s_full['end'].date() if s_full['end'] is not None else '?'} | "
                  f"{s_full['n_days']} | {s_full['n_trades']} |")
    lines.append("")
    lines.append("All three pass `MomentumStrategy()` the FULL data dict, "
                  "with `run_replay`'s `start`/`end` constraining only the "
                  "decision timeline. This preserves the 274-bar (~13mo) "
                  "warm-up for held-out without leaking held-out data into "
                  "inspect.")
    lines.append("")

    # Held-out verdict (PRIMARY)
    lines.append("## 2. HELD-OUT verdict (primary)")
    lines.append("")
    lines.extend(headline_block(s_holdout))
    lines.append("#### Gates (held-out)")
    lines.append("")
    lines.extend(gate_block(s_holdout))
    lines.append("#### Robustness suite (held-out)")
    lines.append("")
    lines.extend(robustness_block(s_holdout))
    lines.append("#### Momentum-crash DD diagnostic (held-out)")
    lines.append("")
    lines.extend(momentum_crash_block(s_holdout))
    lines.append("#### Significance (held-out)")
    lines.append("")
    lines.extend(significance_block(s_holdout))
    lines.append("#### Per-year breakdown (held-out)")
    lines.append("")
    lines.extend(per_year_block(s_holdout))
    lines.append("#### Plain-English verdict (held-out)")
    lines.append("")
    lines.extend(held_out_verdict(s_holdout, s_inspect))
    lines.append("")

    # INSPECT
    lines.append("## 3. INSPECT window (descriptive -- NOT the verdict)")
    lines.append("")
    lines.extend(headline_block(s_inspect))
    lines.append("#### Gates (inspect)")
    lines.append("")
    lines.extend(gate_block(s_inspect))
    lines.append("#### Robustness suite (inspect)")
    lines.append("")
    lines.extend(robustness_block(s_inspect))
    lines.append("#### Momentum-crash DD diagnostic (inspect)")
    lines.append("")
    lines.extend(momentum_crash_block(s_inspect))
    lines.append("#### Per-year breakdown (inspect)")
    lines.append("")
    lines.extend(per_year_block(s_inspect))
    lines.append("")

    # FULL
    lines.append("## 4. FULL window (for completeness)")
    lines.append("")
    lines.extend(headline_block(s_full))
    lines.append("#### Gates (full)")
    lines.append("")
    lines.extend(gate_block(s_full))
    lines.append("#### Robustness suite (full)")
    lines.append("")
    lines.extend(robustness_block(s_full))
    lines.append("#### Momentum-crash DD diagnostic (full)")
    lines.append("")
    lines.extend(momentum_crash_block(s_full))
    lines.append("#### Per-symbol breakdown (full window, top 30 by PnL)")
    lines.append("")
    sym_df = per_symbol_pnl(s_full["trades"])
    if not sym_df.empty:
        top = sym_df.head(30)
        bot = sym_df.tail(10)
        lines.append("**Top 30 contributors:**")
        lines.append("")
        s_top = {"trades": s_full["trades"].loc[
            s_full["trades"]["symbol"].isin(top["symbol"])]}
        lines.extend(per_symbol_block(s_top))
        lines.append("**Bottom 10 contributors:**")
        lines.append("")
        s_bot = {"trades": s_full["trades"].loc[
            s_full["trades"]["symbol"].isin(bot["symbol"])]}
        lines.extend(per_symbol_block(s_bot))
    else:
        lines.append("_No trades to break down._")
        lines.append("")
    lines.append("#### Per-year breakdown (full window)")
    lines.append("")
    lines.extend(per_year_block(s_full))
    lines.append("")

    # Survivorship caveat
    lines.append("## 5. Survivorship caveat")
    lines.append("")
    lines.append("From `data/universe.py`:")
    lines.append("")
    lines.append(f"> {MOMENTUM_SURVIVORSHIP_NOTE}")
    lines.append("")
    lines.append("Discount applied (per ops): **30% conservative HEADLINE**, "
                  "25% lighter shown for comparison.")
    lines.append("")
    lines.append("| Window | Raw PF | Disc 25% | Disc 30% (HEADLINE) |")
    lines.append("|---|---:|---:|---:|")
    for s in (s_full, s_inspect, s_holdout):
        lines.append(f"| {s['label']} | {_fmt_pf(s['pf'])} | "
                      f"{_fmt_pf(_discount_pf(s['pf'], 0.25))} | "
                      f"**{_fmt_pf(_discount_pf(s['pf'], 0.30))}** |")
    lines.append("")

    # Proposed followups
    lines.append("## 6. Proposed follow-up tickets (NOT applied per LAW 4)")
    lines.append("")
    lines.extend(proposed_followups(s_holdout, s_inspect))
    lines.append("")
    lines.append("_End of report._")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────


def main() -> int:
    t0 = time.time()
    print(f"[MOM-3] loading {len(MOMENTUM_UNIVERSE)} MOMENTUM_UNIVERSE "
          f"symbols from {DB_PATH.name} ...")
    data = load_momentum_universe()
    print(f"[MOM-3] {len(data)} symbols loaded "
          f"(load wall {time.time()-t0:.1f}s)")
    if not data:
        print("[MOM-3] ABORT -- empty data dict")
        return 1

    run_params = {
        "max_positions": MOM_MAX_POSITIONS,
        "max_per_sector": MOM_MAX_PER_SECTOR,
        "max_heat": MOM_MAX_HEAT,
    }

    print(f"[MOM-3] run params: {run_params}")
    print(f"[MOM-3] gates: PF>{GATE_PROFIT_FACTOR}, Sharpe>{GATE_SHARPE}, "
          f"|maxDD|<{GATE_MAX_DRAWDOWN}, win>{GATE_WIN_RATE}")

    # ── INSPECT ────────────────────────────────────────────────────────
    print(f"[MOM-3] INSPECT replay "
          f"({INSPECT_START.date()} -> {INSPECT_END.date()}) ...")
    t_phase = time.time()
    r_inspect = run_replay(
        data, MomentumStrategy(),
        initial_capital=INITIAL_CAPITAL,
        max_positions=MOM_MAX_POSITIONS,
        max_per_sector=MOM_MAX_PER_SECTOR,
        max_heat=MOM_MAX_HEAT,
        start=INSPECT_START, end=INSPECT_END,
    )
    print(f"  inspect replay wall {time.time()-t_phase:.1f}s")
    s_inspect = summarise_window("INSPECT", r_inspect)
    print(f"  inspect PF raw={_fmt_pf(s_inspect['pf'])}  "
          f"n_trades={s_inspect['n_trades']}  "
          f"|maxDD|={_fmt(abs(s_inspect['mdd']) if not math.isnan(s_inspect['mdd']) else float('nan'), 3)}")

    # ── HELD-OUT ───────────────────────────────────────────────────────
    print(f"[MOM-3] HELD-OUT replay "
          f"({HOLDOUT_START.date()} -> {HOLDOUT_END.date()}) ...")
    t_phase = time.time()
    r_holdout = run_replay(
        data, MomentumStrategy(),
        initial_capital=INITIAL_CAPITAL,
        max_positions=MOM_MAX_POSITIONS,
        max_per_sector=MOM_MAX_PER_SECTOR,
        max_heat=MOM_MAX_HEAT,
        start=HOLDOUT_START, end=HOLDOUT_END,
    )
    print(f"  held-out replay wall {time.time()-t_phase:.1f}s")
    s_holdout = summarise_window("HELD-OUT", r_holdout)
    print(f"  held-out PF raw={_fmt_pf(s_holdout['pf'])}  "
          f"n_trades={s_holdout['n_trades']}  "
          f"|maxDD|={_fmt(abs(s_holdout['mdd']) if not math.isnan(s_holdout['mdd']) else float('nan'), 3)}")

    # ── FULL ───────────────────────────────────────────────────────────
    print(f"[MOM-3] FULL replay (descriptive) ...")
    t_phase = time.time()
    r_full = run_replay(
        data, MomentumStrategy(),
        initial_capital=INITIAL_CAPITAL,
        max_positions=MOM_MAX_POSITIONS,
        max_per_sector=MOM_MAX_PER_SECTOR,
        max_heat=MOM_MAX_HEAT,
    )
    print(f"  full replay wall {time.time()-t_phase:.1f}s")
    s_full = summarise_window("FULL", r_full)
    print(f"  full PF raw={_fmt_pf(s_full['pf'])}  "
          f"n_trades={s_full['n_trades']}  "
          f"|maxDD|={_fmt(abs(s_full['mdd']) if not math.isnan(s_full['mdd']) else float('nan'), 3)}")

    t_total = time.time() - t0
    print(f"[MOM-3] total wall {t_total:.1f}s "
          f"({t_total / 60:.1f} min). Writing report ...")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        build_report(s_full, s_inspect, s_holdout,
                      run_params=run_params, t_elapsed=t_total,
                      n_symbols_loaded=len(data)),
        encoding="utf-8")
    print(f"[MOM-3] report -> {REPORT_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
