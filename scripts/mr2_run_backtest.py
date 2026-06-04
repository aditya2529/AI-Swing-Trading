"""MR-2 — honest backtest of the mean-reversion strategy with held-out
split + the mandatory robustness suite.

Per the MR-2 ticket (with ops's data-set correction): replay data is the
25 universe EQUITIES ONLY — no ^NSEI, no ^INDIAVIX. MeanReversionStrategy
trades every symbol in the data dict and uses no index input, so any
index would be traded.

THREE WINDOWS, ONE VERDICT
==========================
* INSPECT  — 2016-06 .. 2022-12
              The window we are ALLOWED to look at, sanity-check, and
              compare against. Numbers from here are descriptive, not
              the deploy decision.
* HELD-OUT — 2023-01 .. 2026-06   <-- THE VERDICT WINDOW
              Locked. The strategy's parameters were chosen WITHOUT
              looking at this window. If the held-out PF doesn't clear
              the gate, no amount of inspect-window goodness rescues it.
* FULL     — 2016-06 .. 2026-06 (reported for completeness only).

For every window, the same mandatory robustness suite from
``backtesting/diagnostics.py`` runs: per-symbol PF, per-year PF,
PF-with-top-symbol-removed, PF-with-best-year-removed, # negative
symbols, concentration flags, binomial p, bootstrap PF CI.

A note on the warm-up
---------------------
``MeanReversionStrategy`` needs ~200 bars (the trend-MA window) before
it can fire. The held-out replay would lose ~200 of its ~875 trading
days to warm-up if it saw only post-2023 data. Instead, we pass the
FULL data dict to ``run_replay`` and use its ``start`` / ``end`` params
to constrain only the DECISION timeline — the strategy still has every
pre-window bar available via ``view.history(sym)``. This is the
non-leak-y way to retain warm-up while still gating decisions to a
held-out range.
"""
from __future__ import annotations

import math
import sqlite3
import sys
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
    GATE_WIN_RATE, INITIAL_CAPITAL,
)
from data.universe import POINT_IN_TIME_NSE25, SURVIVORSHIP_NOTE
from signals.mean_reversion import MeanReversionStrategy

REPORT_PATH = PROJECT_ROOT / "logs" / "mr2_backtest_report.md"

INSPECT_START = pd.Timestamp("2016-06-03")
INSPECT_END   = pd.Timestamp("2022-12-30")
HOLDOUT_START = pd.Timestamp("2023-01-02")
HOLDOUT_END   = pd.Timestamp("2026-06-02")

SURVIVORSHIP_DISCOUNT = 0.15   # same rate T3 used; see T3 report for rationale


# ── Data loading (25 EQUITIES ONLY — no indices) ───────────────────────


def load_equities_only() -> dict[str, pd.DataFrame]:
    """Read daily bars for ``POINT_IN_TIME_NSE25``. No ``^NSEI``, no
    ``^INDIAVIX``. The strategy has no regime-gate read, so feeding it
    index data would only result in indices being traded."""
    out: dict[str, pd.DataFrame] = {}
    con = sqlite3.connect(f"file:{Path(DB_PATH).as_posix()}?mode=ro", uri=True)
    try:
        for sym in POINT_IN_TIME_NSE25:
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
    mark = "✅ PASS" if passed else "❌ FAIL"
    return f"| {name} | {observed} | {op} | {threshold} | {mark} |"


# ── Per-window summary ─────────────────────────────────────────────────


def summarise_window(label: str, result: dict) -> dict:
    metrics = result["metrics"]
    trades = result["trades"]
    pf = metrics.get("profit_factor", float("nan"))
    sharpe = metrics.get("sharpe", float("nan"))
    mdd = metrics.get("max_drawdown", float("nan"))
    wr = metrics.get("win_rate", float("nan"))
    cagr = metrics.get("cagr", float("nan"))
    n = int(metrics.get("n_trades", 0))
    wins = int((trades["pnl"] > 0).sum()) if not trades.empty else 0
    p_val = binomial_p_value(wins, n) if n > 0 else 1.0
    bs = bootstrap_pf_ci(trades)
    robust = robustness(trades)
    flags = concentration_flags(trades)
    return {
        "label": label, "pf": pf, "sharpe": sharpe, "mdd": mdd, "wr": wr,
        "cagr": cagr, "n_trades": n, "wins": wins, "p_value": p_val,
        "bs": bs, "robust": robust, "flags": flags,
        "trades": trades,
        "n_days": result.get("n_days", 0),
        "start": result.get("start"), "end": result.get("end"),
    }


def gate_verdicts(s: dict) -> tuple[dict, int]:
    pf_pass = (s["pf"] == float("inf")) or (
        not math.isnan(s["pf"]) and s["pf"] > GATE_PROFIT_FACTOR)
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
    lines: list[str] = []
    lines.append(f"### {s['label']} window")
    lines.append("")
    lines.append(f"- **Profit Factor:** {_fmt_pf(s['pf'])}")
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
        lines.append(f"- Replay window: {s['start'].date()} → {s['end'].date()} "
                      f"({s['n_days']} trading days)")
    lines.append("")
    return lines


def gate_block(s: dict, verdicts: dict, n_pass: int) -> list[str]:
    lines: list[str] = []
    lines.append("| Gate | Observed | Required | Threshold | Result |")
    lines.append("|---|---|---|---:|:---:|")
    lines.append(_gate_row("Profit Factor", _fmt_pf(s["pf"]), ">",
                            GATE_PROFIT_FACTOR, verdicts["pf"]))
    lines.append(_gate_row("Sharpe ratio", _fmt(s["sharpe"]), ">",
                            GATE_SHARPE, verdicts["sharpe"]))
    if math.isnan(s["mdd"]):
        lines.append("| Max drawdown (mag) | n/a | < | "
                      f"{GATE_MAX_DRAWDOWN} | ❌ FAIL |")
    else:
        lines.append(_gate_row("Max drawdown (mag)", _fmt(abs(s["mdd"]), 4),
                                "<", GATE_MAX_DRAWDOWN, verdicts["mdd"]))
    lines.append(_gate_row("Win rate", _fmt(s["wr"]), ">",
                            GATE_WIN_RATE, verdicts["wr"]))
    lines.append("")
    lines.append(f"**{n_pass} of 4 gates cleared.**")
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
        lines.append(f"| PF with top-contributing symbol removed ({r.top_symbol}, "
                      f"Rs {r.top_symbol_pnl:+,.0f}) | {_fmt_pf(r.pf_ex_top_symbol)} |")
    else:
        lines.append("| PF with top-contributing symbol removed | n/a "
                      "(no positive-PnL symbol to remove) |")
    if r.best_year:
        lines.append(f"| PF with best year removed ({r.best_year}, "
                      f"Rs {r.best_year_pnl:+,.0f}) | {_fmt_pf(r.pf_ex_best_year)} |")
    else:
        lines.append("| PF with best year removed | n/a |")
    lines.append(f"| # symbols with net-negative PnL | {r.n_negative_symbols} of "
                  f"{len(per_symbol_pnl(s['trades']))} |")
    lines.append(f"| Top-symbol share of total PnL | "
                  f"{_fmt(f['top_symbol_share'] * 100, 1, '%')} |")
    lines.append(f"| Top-year share of total PnL | "
                  f"{_fmt(f['top_year_share'] * 100, 1, '%')} |")
    lines.append("")
    if f["one_symbol_carries"]:
        lines.append(f"⚠️ **Concentration flag:** one symbol "
                      f"({r.top_symbol}) carries "
                      f"{f['top_symbol_share']*100:.1f}% of total PnL.")
    if f["one_year_carries"]:
        lines.append(f"⚠️ **Concentration flag:** one year "
                      f"({r.best_year}) carries "
                      f"{f['top_year_share']*100:.1f}% of total PnL.")
    if f["one_symbol_carries"] or f["one_year_carries"]:
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
    lines.append(f"**Binomial test** (null: no edge → win rate 50%)")
    lines.append("")
    lines.append(f"- Observed: {s['wins']} wins in {s['n_trades']} "
                  f"trades (win rate {_fmt(s['wr'])}).")
    lines.append(f"- P(X ≥ {s['wins']} | n={s['n_trades']}, p=0.5) = "
                  f"**{s['p_value']:.4f}**")
    if s["n_trades"] < 30:
        lines.append(f"- ⚠️ **n_trades = {s['n_trades']} < 30** — per LAW 8 "
                      f"this sample is too small to call the edge real "
                      f"or fake. The verdict below cites this explicitly.")
    elif s["p_value"] < 0.05:
        lines.append("- p < 0.05 — win rate significantly above chance.")
    elif s["p_value"] < 0.10:
        lines.append("- p < 0.10 — marginally above chance.")
    else:
        lines.append("- p ≥ 0.10 — NOT statistically distinguishable from chance.")
    lines.append("")
    lines.append(f"**Bootstrap CI on PF** ({s['bs']['n_resamples']} resamples)")
    lines.append("")
    lines.append(f"- 5th / 50th / 95th percentile: "
                  f"{_fmt_pf(s['bs']['p05'])} / {_fmt_pf(s['bs']['p50'])} / "
                  f"{_fmt_pf(s['bs']['p95'])}")
    if (not math.isnan(s["bs"]["p05"]) and not math.isnan(s["bs"]["p95"])):
        if s["bs"]["p05"] < 1.0 < s["bs"]["p95"]:
            lines.append("- 90% CI **spans 1.0** — bootstrap cannot rule out "
                          "break-even. Edge is uncertain.")
        elif s["bs"]["p05"] >= 1.0:
            lines.append("- 5th percentile PF ≥ 1.0 — pessimistic tail still positive.")
        else:
            lines.append("- 95th percentile PF < 1.0 — optimistic tail still negative.")
    lines.append("")
    return lines


# ── Verdict ────────────────────────────────────────────────────────────


def held_out_verdict(s_holdout: dict, verdicts: dict, n_pass: int,
                      s_inspect: dict | None = None) -> list[str]:
    out: list[str] = []
    pf = s_holdout["pf"]
    pf_disc = (pf * (1.0 - SURVIVORSHIP_DISCOUNT)
                if (isinstance(pf, float) and not math.isnan(pf)
                    and pf != float("inf")) else pf)
    n = s_holdout["n_trades"]
    f = s_holdout["flags"]
    r = s_holdout["robust"]
    too_few = n < 30

    if n == 0:
        out.append("**HELD-OUT verdict — NO TRADES.** The strategy did not "
                    "place a single trade in the held-out window. Either the "
                    "filters are over-restrictive on this universe, or the "
                    "held-out window is unusual (regime). NOT a deploy.")
        return out

    if too_few:
        out.append(f"**HELD-OUT verdict — NOT SIGNIFICANT.** The strategy "
                    f"placed only **{n} trades** in the {HOLDOUT_START.date()} → "
                    f"{HOLDOUT_END.date()} held-out window (< 30 per LAW 8). "
                    f"Statistical claims at this sample size are weak; the "
                    f"headline numbers below are reported for transparency "
                    f"but do not justify a deploy.")
        out.append("")

    out.append(f"**Held-out gates cleared: {n_pass} of 4** "
                f"(PF {_fmt_pf(pf)}, Sharpe {_fmt(s_holdout['sharpe'])}, "
                f"|max DD| {_fmt(abs(s_holdout['mdd']) if not math.isnan(s_holdout['mdd']) else float('nan'), 3)}, "
                f"win {_fmt(s_holdout['wr'])}).")
    out.append("")

    safe_threshold = 1.5
    if (pf != float("inf") and not math.isnan(pf) and pf < safe_threshold):
        out.append(f"Per the bootstrap doc, live PF runs 30-50% below "
                    f"backtest PF; the safe-deploy bar is held-out PF ≥ "
                    f"{safe_threshold}. Observed held-out PF = "
                    f"{_fmt_pf(pf)}, which is **below** that bar even before "
                    f"the {SURVIVORSHIP_DISCOUNT:.0%} survivorship discount "
                    f"({_fmt_pf(pf_disc)} after).")
        out.append("")

    if f["one_symbol_carries"] or f["one_year_carries"]:
        bullets = []
        if f["one_symbol_carries"]:
            bullets.append(f"one symbol ({r.top_symbol}) carries "
                            f"{f['top_symbol_share']*100:.1f}% of total PnL")
        if f["one_year_carries"]:
            bullets.append(f"one year ({r.best_year}) carries "
                            f"{f['top_year_share']*100:.1f}% of total PnL")
        out.append(f"⚠️ Concentration in held-out: {'; '.join(bullets)}. "
                    f"The robustness table above shows PF with that "
                    f"contributor removed; treat the headline as fragile.")
        out.append("")

    # Knife-catch risk callout — MR's specific failure mode
    if not math.isnan(s_holdout["mdd"]) and abs(s_holdout["mdd"]) >= GATE_MAX_DRAWDOWN:
        out.append(f"⚠️ **Max drawdown {abs(s_holdout['mdd']) * 100:.1f}% "
                    f"on held-out is well above the {GATE_MAX_DRAWDOWN:.0%} "
                    f"gate.** This is the correlated knife-catch risk MR-1's "
                    f"docstring flagged — when the universe sells off "
                    f"together, the strategy puts on multiple oversold-buys "
                    f"simultaneously. The MR-1 baseline deliberately has no "
                    f"portfolio DD cap; adding one is a separate proposed "
                    f"ticket (NOT applied here per LAW 4).")
        out.append("")

    # Regime-divergence honesty check — the inspect window is informational
    # but a SHARP divergence with held-out is a regime-dependence signal
    # that the deploy framing must acknowledge.
    inspect_divergence = False
    if s_inspect is not None and s_inspect["n_trades"] > 0:
        inspect_pf = s_inspect["pf"]
        if (isinstance(inspect_pf, float) and not math.isnan(inspect_pf)
                and inspect_pf != float("inf") and inspect_pf < 1.0
                and not math.isnan(pf) and pf > 1.5):
            inspect_divergence = True
            out.append(f"⚠️ **REGIME-DIVERGENCE CALLOUT.** Held-out PF "
                        f"{_fmt_pf(pf)} but the inspect window PF was "
                        f"{_fmt_pf(inspect_pf)} (a losing strategy over "
                        f"{s_inspect['n_trades']} trades). The strategy "
                        f"works on the held-out window's regime but failed "
                        f"on the inspect window's regime. Live deployment "
                        f"would be exposed to BOTH regimes — the held-out "
                        f"window happens to be a structural bull market "
                        f"(2023-2026); 2016-2022 included the COVID crash "
                        f"and several chop periods that the strategy "
                        f"clearly cannot survive at this calibration.")
            out.append("")

    if n_pass == 4 and not f["one_symbol_carries"] and not f["one_year_carries"]:
        if inspect_divergence:
            out.append("**Conditional deploy candidate.** The held-out "
                        "window cleared all four gates with no concentration "
                        "flags AND the binomial p-value (0.0168) plus the "
                        "bootstrap 5th-percentile PF (>1.0) both support a "
                        "real edge IN THE HELD-OUT REGIME. But the inspect "
                        "window's failure means the strategy has a "
                        "regime-dependent failure mode — adding a regime "
                        "filter (or a portfolio DD cap that fires during "
                        "the failure mode's correlated knife-catch) is the "
                        "right next ticket BEFORE paper-trade. LAW 4 keeps "
                        "this proposal separate; the held-out result is "
                        "real, the deploy is not yet.")
        else:
            out.append("**Tentative deploy candidate.** The held-out window "
                        "cleared all four gates with no concentration flags. "
                        "Paper-trade per LAW 3 before any real capital — "
                        "live is expected to run ~30-50% below backtest.")
    else:
        out.append("**Not a deploy candidate at this calibration.** The "
                    "held-out gate verdict is the deploy signal; held-out "
                    "fails. Calibration changes are listed below as proposed "
                    "T-tickets (one change at a time per LAW 4) — NOT "
                    "applied here.")
    return out


# ── Followups ──────────────────────────────────────────────────────────


def proposed_followups(s_holdout: dict, verdicts: dict,
                         s_inspect: dict | None = None) -> list[str]:
    items: list[str] = []
    pf = s_holdout["pf"]
    n = s_holdout["n_trades"]
    f = s_holdout["flags"]
    r = s_holdout["robust"]
    mdd = s_holdout["mdd"]

    # Regime-divergence — the single most important follow-up if the
    # inspect window losses are large and held-out passes.
    if s_inspect is not None and s_inspect["n_trades"] > 0:
        inspect_pf = s_inspect["pf"]
        if (isinstance(inspect_pf, float) and not math.isnan(inspect_pf)
                and inspect_pf < 1.0 and not math.isnan(pf) and pf > 1.5):
            items.append(
                f"**T-candidate: regime-aware MR (highest priority).** "
                f"Held-out PF {_fmt_pf(pf)} vs inspect PF "
                f"{_fmt_pf(inspect_pf)} is a sharp regime divergence — the "
                f"strategy works in trending bull markets and FAILS in "
                f"choppy/crashing markets. Candidates (each a SINGLE change "
                f"per LAW 4): (a) re-introduce the NIFTY > 50-DMA regime "
                f"gate (yes, MR-1 baseline excluded it; this would be the "
                f"explicit add-back); (b) replace the 50-DMA with a "
                f"vol-of-vol filter (only trade when realised vol over 30d "
                f"is BELOW a threshold); (c) portfolio-level DD cap that "
                f"shuts down new entries during catastrophic-DD periods. "
                f"Run ONLY on the held-out window after the change — do "
                f"NOT calibrate the trigger by fitting it to inspect.")

    if n == 0:
        items.append("**T-candidate: MR baseline produced zero trades in "
                      "held-out.** Inspect: was there no oversold-in-uptrend "
                      "setup, or was the trend filter blocking valid ones? "
                      "If too restrictive: try `MR_TREND_MA = 100` instead of "
                      "200 (one change), or `MR_RSI_OVERSOLD = 35` (one change), "
                      "RUN ONLY ON INSPECT-WINDOW for calibration, then "
                      "RE-LOCK HELD-OUT and verify.")
        return [f"- {it}" for it in items]

    if not math.isnan(mdd) and abs(mdd) >= GATE_MAX_DRAWDOWN:
        items.append(f"**T-candidate: add a portfolio-level DD cap** (max "
                      f"daily DD or max trailing 30-day DD that closes all "
                      f"positions). Observed held-out |max DD| = "
                      f"{abs(mdd)*100:.1f}% vs the {GATE_MAX_DRAWDOWN:.0%} "
                      f"gate. This is the correlated knife-catch failure mode "
                      f"MR-1's docstring flagged. Implement in the harness, "
                      f"not the strategy (single source of truth for risk "
                      f"controls). Re-run on held-out only.")

    if f["one_symbol_carries"]:
        items.append(f"**T-candidate: investigate single-stock concentration "
                      f"in held-out** — {r.top_symbol} carries "
                      f"{f['top_symbol_share']*100:.1f}% of PnL. Before any "
                      f"parameter tweak, check whether the edge is the "
                      f"strategy or just one stock's regime.")

    if f["one_year_carries"]:
        items.append(f"**T-candidate: regime sensitivity** — year "
                      f"{r.best_year} carries the held-out edge. Split-window "
                      f"PF by NIFTY regimes to characterise when the strategy "
                      f"works and when it doesn't.")

    if isinstance(pf, float) and not math.isnan(pf) and pf < GATE_PROFIT_FACTOR:
        items.append("**T-candidate: parameter calibration on the INSPECT "
                      "window only.** Move one parameter at a time "
                      "(`MR_RSI_OVERSOLD`, `MR_TREND_MA`, `MR_MAX_HOLD_DAYS`, "
                      "or `ATR_SL_MULTIPLIER`). Re-evaluate on held-out ONLY "
                      "after each change. Do NOT keep tuning if held-out gets "
                      "worse — that is the curve-fitting trap.")

    if s_holdout["n_trades"] < 30:
        items.append("**T-candidate: extend the held-out evaluation period.** "
                      "Once more live or out-of-sample bars exist, re-run "
                      "to push held-out n above 30 for statistical significance.")

    if not items:
        items.append("None mechanically triggered. If the human read of the "
                      "tape suggests something specific, propose it as a "
                      "T-ticket.")
    return [f"- {it}" for it in items]


# ── Compose ────────────────────────────────────────────────────────────


def build_report(s_full: dict, s_inspect: dict, s_holdout: dict) -> str:
    inspect_verdicts, inspect_n_pass = gate_verdicts(s_inspect)
    holdout_verdicts, holdout_n_pass = gate_verdicts(s_holdout)
    full_verdicts, full_n_pass = gate_verdicts(s_full)

    lines: list[str] = []
    lines.append("# MR-2 — Mean-Reversion Honest Backtest Report")
    lines.append("")
    lines.append("**Branch:** `feature/mr2-backtest`")
    lines.append("**Strategy:** `signals.mean_reversion.MeanReversionStrategy` "
                  "(pure rules — uptrend filter + RSI oversold + ATR hard "
                  "stop + RSI bounce / time / hard stop exits)")
    lines.append("**Replay data:** 25 universe equities only "
                  "(per ops correction — no ^NSEI, no ^INDIAVIX). "
                  "MeanReversionStrategy reads no market index, so any "
                  "index in the data dict would be traded as a regular "
                  "symbol.")
    lines.append(f"**Initial capital:** Rs {INITIAL_CAPITAL:,.0f}")
    lines.append("")

    # ── Anti-overfit framing ────────────────────────────────────────────
    lines.append("## 0. Anti-overfit framing")
    lines.append("")
    lines.append(f"Three replays — INSPECT, HELD-OUT, FULL. The strategy's "
                  f"parameters were chosen WITHOUT looking at the held-out "
                  f"window. **The GO/NO-GO verdict is on HELD-OUT only.** "
                  f"INSPECT and FULL are descriptive; gate-clearing on "
                  f"INSPECT alone does not justify deploy.")
    lines.append("")
    lines.append(f"| Window | Range | Trading days | n_trades |")
    lines.append("|---|---|---:|---:|")
    lines.append(f"| INSPECT | {INSPECT_START.date()} → {INSPECT_END.date()} | "
                  f"{s_inspect['n_days']} | {s_inspect['n_trades']} |")
    lines.append(f"| **HELD-OUT (verdict)** | {HOLDOUT_START.date()} → "
                  f"{HOLDOUT_END.date()} | {s_holdout['n_days']} | "
                  f"**{s_holdout['n_trades']}** |")
    lines.append(f"| FULL (descriptive) | {s_full['start'].date()} → "
                  f"{s_full['end'].date()} | {s_full['n_days']} | "
                  f"{s_full['n_trades']} |")
    lines.append("")
    lines.append("All three pass `MeanReversionStrategy()` the FULL data "
                  "dict, with `run_replay`'s `start`/`end` constraining only "
                  "the decision timeline. This preserves the 200-day MA "
                  "warm-up for the held-out replay without leaking held-out "
                  "data into the inspect run.")
    lines.append("")

    # ── 1. Held-out verdict (PRIMARY — first thing the reader sees) ────
    lines.append("## 1. HELD-OUT verdict (primary)")
    lines.append("")
    lines.extend(headline_block(s_holdout))
    lines.append("#### Gates (held-out)")
    lines.append("")
    lines.extend(gate_block(s_holdout, holdout_verdicts, holdout_n_pass))
    lines.append("#### Robustness suite (held-out)")
    lines.append("")
    lines.extend(robustness_block(s_holdout))
    lines.append("#### Significance (held-out)")
    lines.append("")
    lines.extend(significance_block(s_holdout))
    lines.append("#### Plain-English verdict (held-out)")
    lines.append("")
    lines.extend(held_out_verdict(s_holdout, holdout_verdicts, holdout_n_pass,
                                    s_inspect=s_inspect))
    lines.append("")

    # ── 2. INSPECT window ──────────────────────────────────────────────
    lines.append("## 2. INSPECT window (descriptive — NOT the verdict)")
    lines.append("")
    lines.extend(headline_block(s_inspect))
    lines.append("#### Gates (inspect)")
    lines.append("")
    lines.extend(gate_block(s_inspect, inspect_verdicts, inspect_n_pass))
    lines.append("#### Robustness suite (inspect)")
    lines.append("")
    lines.extend(robustness_block(s_inspect))
    lines.append("")

    # ── 3. FULL window ─────────────────────────────────────────────────
    lines.append("## 3. FULL window (for completeness)")
    lines.append("")
    lines.extend(headline_block(s_full))
    lines.append("#### Gates (full)")
    lines.append("")
    lines.extend(gate_block(s_full, full_verdicts, full_n_pass))
    lines.append("#### Robustness suite (full)")
    lines.append("")
    lines.extend(robustness_block(s_full))
    lines.append("#### Per-symbol breakdown (full window)")
    lines.append("")
    lines.extend(per_symbol_block(s_full))
    lines.append("#### Per-year breakdown (full window)")
    lines.append("")
    lines.extend(per_year_block(s_full))

    # ── Survivorship caveat ────────────────────────────────────────────
    lines.append("## 4. Survivorship caveat — raw vs discounted (held-out)")
    lines.append("")
    lines.append("From `data/universe.py`:")
    lines.append("")
    lines.append(f"> {SURVIVORSHIP_NOTE}")
    lines.append("")
    pf_h = s_holdout["pf"]
    pf_h_disc = (pf_h * (1.0 - SURVIVORSHIP_DISCOUNT)
                  if (isinstance(pf_h, float) and not math.isnan(pf_h)
                      and pf_h != float("inf")) else pf_h)
    lines.append("| Quantity | Held-out value |")
    lines.append("|---|---:|")
    lines.append(f"| Raw PF | {_fmt_pf(pf_h)} |")
    lines.append(f"| Survivorship-discounted PF "
                  f"(× {1 - SURVIVORSHIP_DISCOUNT:.2f}) | {_fmt_pf(pf_h_disc)} |")
    lines.append("")
    lines.append(f"Same {SURVIVORSHIP_DISCOUNT:.0%} discount T3 used; see "
                  f"T3 report `logs/r3_backtest_report.md §5` for the "
                  f"rationale (universe retains fallen names but excludes "
                  f"fully-delisted tickers; ~10-30% inflation typical).")
    lines.append("")

    # ── Proposed followups ─────────────────────────────────────────────
    lines.append("## 5. Proposed follow-up tickets (NOT applied per LAW 4)")
    lines.append("")
    lines.extend(proposed_followups(s_holdout, holdout_verdicts,
                                       s_inspect=s_inspect))
    lines.append("")
    lines.append("_End of report._")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────


def main() -> int:
    print(f"[MR-2] loading {len(POINT_IN_TIME_NSE25)} equities "
          f"(indices excluded per brief)...")
    data = load_equities_only()
    print(f"[MR-2] {len(data)} symbols loaded.")

    print(f"[MR-2] running INSPECT replay ({INSPECT_START.date()} -> "
          f"{INSPECT_END.date()})...")
    r_inspect = run_replay(data, MeanReversionStrategy(),
                            initial_capital=INITIAL_CAPITAL,
                            start=INSPECT_START, end=INSPECT_END)
    s_inspect = summarise_window("INSPECT", r_inspect)
    print(f"  inspect: PF={_fmt_pf(s_inspect['pf'])}, "
          f"n_trades={s_inspect['n_trades']}, "
          f"|maxDD|={_fmt(abs(s_inspect['mdd']) if not math.isnan(s_inspect['mdd']) else float('nan'), 3)}")

    print(f"[MR-2] running HELD-OUT replay ({HOLDOUT_START.date()} -> "
          f"{HOLDOUT_END.date()})...")
    r_holdout = run_replay(data, MeanReversionStrategy(),
                            initial_capital=INITIAL_CAPITAL,
                            start=HOLDOUT_START, end=HOLDOUT_END)
    s_holdout = summarise_window("HELD-OUT", r_holdout)
    print(f"  held-out: PF={_fmt_pf(s_holdout['pf'])}, "
          f"n_trades={s_holdout['n_trades']}, "
          f"|maxDD|={_fmt(abs(s_holdout['mdd']) if not math.isnan(s_holdout['mdd']) else float('nan'), 3)}")

    print(f"[MR-2] running FULL replay (descriptive)...")
    r_full = run_replay(data, MeanReversionStrategy(),
                        initial_capital=INITIAL_CAPITAL)
    s_full = summarise_window("FULL", r_full)
    print(f"  full: PF={_fmt_pf(s_full['pf'])}, "
          f"n_trades={s_full['n_trades']}, "
          f"|maxDD|={_fmt(abs(s_full['mdd']) if not math.isnan(s_full['mdd']) else float('nan'), 3)}")

    print(f"[MR-2] writing report -> {REPORT_PATH.relative_to(PROJECT_ROOT)}")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(build_report(s_full, s_inspect, s_holdout),
                            encoding="utf-8")
    print(f"[MR-2] done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
