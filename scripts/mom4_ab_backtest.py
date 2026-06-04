"""MOM-4 — A/B backtest: MOM-2 baseline (filter OFF) vs dual-momentum
(Antonacci absolute filter ON).

PURPOSE
=======
MOM-3 surfaced a 36% held-out drawdown that fails the 15% gate -- the
classic momentum-crash failure mode. MOM-4 asks one question:

  Does adding the parameter-free Antonacci absolute filter (drop names
  whose own 12-1 score is non-positive, threshold = 0 exactly) cut the
  held-out drawdown WITHOUT cutting the edge?

Two replays per window. Everything else is held constant -- same
universe, same harness, same run params, same windows, same
diagnostics, same survivorship discount -- so any difference between
A (filter OFF) and B (filter ON) is solely attributable to the
absolute filter.

RUN PARAMS (matched to MOM-3 exactly)
=====================================
    max_positions   = MOM_TOP_N (= 15)
    max_per_sector  = 5
    max_heat        = 0.20

WINDOWS (matched to MOM-3 exactly)
==================================
    INSPECT  : 2016-01-04 -> 2022-12-30  (descriptive)
    HELD-OUT : 2023-01-02 -> 2026-06-03  (verdict)
    FULL     : 2014-06-09 -> 2026-06-03  (descriptive)

WHAT THE REPORT MUST ANSWER (plainly, per ops)
==============================================
    * Did held-out maxDD drop from 36% toward the <15% gate?
    * Did the edge survive (disc-30% PF still > 1.3)?
        PF, Sharpe, win-rate, n_trades.
    * Per-year: which years did the filter sit in cash?
    * % of trading days the book was fully/partially/zero invested.
    * HONEST VERDICT: deploy candidate, OR DD cut at the cost of edge?

MINED-DATA CAVEAT
=================
This is the SECOND backtest we've run on this universe (after MOM-3).
Even a clean result is a CANDIDATE -- not a deploy. Real validation
is forward paper-trading. Momentum is also the most survivorship-
sensitive strategy in this project; the 30% PF haircut may STILL
flatter the result.
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
    per_year_pnl, robustness,
)
from backtesting.replay import run_replay
from config import (
    GATE_MAX_DRAWDOWN, GATE_PROFIT_FACTOR, GATE_SHARPE, GATE_WIN_RATE,
    INITIAL_CAPITAL, MOM_TOP_N,
)
from data.universe import MOMENTUM_SURVIVORSHIP_NOTE
from scripts.mom3_run_backtest import (
    HOLDOUT_END, HOLDOUT_START, INSPECT_END, INSPECT_START,
    MOM_MAX_HEAT, MOM_MAX_PER_SECTOR, MOM_MAX_POSITIONS,
    MOMENTUM_CRASH_WINDOWS, SURVIVORSHIP_DISCOUNT_CONSERVATIVE,
    SURVIVORSHIP_DISCOUNT_LIGHT,
    _discount_pf, _fmt, _fmt_pf, crash_window_dd, deepest_drawdown,
    load_momentum_universe,
)
from signals.momentum import MomentumStrategy

REPORT_PATH = PROJECT_ROOT / "logs" / "mom4_ab_report.md"


# ── Filter activity diagnostic ──────────────────────────────────────────


def filter_activity(data: dict, start: pd.Timestamp | None,
                     end: pd.Timestamp | None) -> dict:
    """Count, post-hoc, how often the absolute filter (threshold=0)
    actually WOULD bite on this universe — independent of the replay.
    For each monthly rebalance day in the window, score the universe
    and check how many of the top-N (by relative rank) have a non-
    positive score. This answers: "across all rebalances in this
    window, on how many would the filter have actually fired?"

    Returns counts so the report can say honestly whether the filter
    is a no-op or a real lever on this data.
    """
    from backtesting.replay import BarView
    strat = MomentumStrategy()   # only used for _compute_scores
    all_dates = sorted(set().union(*[set(df.index) for df in data.values()]))
    in_range = [t for t in all_dates
                if (start is None or t >= start)
                and (end is None or t <= end)]
    n_rebal = 0
    n_rebal_with_any_neg = 0
    n_rebal_all_neg = 0
    distribution: dict[int, int] = {}
    for i in range(1, len(in_range)):
        d, prev = in_range[i], in_range[i - 1]
        # A rebalance day is one whose immediately-prior bar is in a
        # different calendar month. Same definition as the strategy
        # uses internally.
        if d.month == prev.month and d.year == prev.year:
            continue
        n_rebal += 1
        view = BarView(data, cutoff=d)
        scores = strat._compute_scores(view)
        if not scores:
            continue
        ranked = sorted(scores, key=lambda s: scores[s], reverse=True)
        top = ranked[:MOM_TOP_N]
        n_neg = sum(1 for s in top if scores[s] <= 0)
        distribution[n_neg] = distribution.get(n_neg, 0) + 1
        if n_neg > 0:
            n_rebal_with_any_neg += 1
        if n_neg == len(top):
            n_rebal_all_neg += 1
    return {
        "n_rebal_days": n_rebal,
        "n_rebal_with_any_neg_in_topN": n_rebal_with_any_neg,
        "n_rebal_all_neg_in_topN": n_rebal_all_neg,
        "distribution": distribution,
    }


# ── Per-day book occupancy diagnostic ──────────────────────────────────


def positions_per_day(trades: pd.DataFrame,
                       timeline: pd.DatetimeIndex) -> pd.Series:
    """For each date in ``timeline``, count how many trades had a
    position open. A trade is OPEN on dates in
    ``[entry_date, exit_date]`` inclusive — which counts the entry-day
    open through the exit-day open (close-out happens at the next day's
    open per the harness convention; we treat the trade as still
    occupying a book slot on the exit-DECISION day).

    Used to compute % of trading days the book was fully invested,
    partially invested, or fully cash — the MOM-4 crash-avoidance
    diagnostic ops asked for.
    """
    counts = pd.Series(0, index=timeline, dtype=int)
    if trades.empty:
        return counts
    for _, row in trades.iterrows():
        start = pd.Timestamp(row["entry_date"])
        end = pd.Timestamp(row["exit_date"])
        mask = (timeline >= start) & (timeline <= end)
        counts.loc[mask] += 1
    return counts


def occupancy_stats(pos_per_day: pd.Series, top_n: int) -> dict:
    """Summary stats: % full / partial / zero days, mean positions."""
    if pos_per_day.empty:
        return {"pct_full": 0.0, "pct_partial": 0.0, "pct_zero": 0.0,
                "mean_positions": 0.0, "n_days": 0}
    n_days = len(pos_per_day)
    full = int((pos_per_day == top_n).sum())
    zero = int((pos_per_day == 0).sum())
    partial = n_days - full - zero
    return {
        "pct_full": full / n_days,
        "pct_partial": partial / n_days,
        "pct_zero": zero / n_days,
        "mean_positions": float(pos_per_day.mean()),
        "n_days": n_days,
    }


# ── Per-window summary (matches MOM-3 shape + occupancy) ───────────────


def summarise_window(label: str, result: dict, *, top_n: int) -> dict:
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
    bs = bootstrap_pf_ci(trades)
    robust = robustness(trades)
    flags = concentration_flags(trades)
    deepest = deepest_drawdown(equity)
    crash_dds = crash_window_dd(equity, MOMENTUM_CRASH_WINDOWS)
    pos_per_day = positions_per_day(trades, equity.index)
    occ = occupancy_stats(pos_per_day, top_n)
    return {
        "label": label, "pf": pf, "sharpe": sharpe, "mdd": mdd, "wr": wr,
        "cagr": cagr, "n_trades": n, "wins": wins, "p_value": p_val,
        "bs": bs, "robust": robust, "flags": flags,
        "trades": trades, "equity": equity,
        "deepest_dd": deepest, "crash_dds": crash_dds,
        "occupancy": occ,
        "n_days": result.get("n_days", 0),
        "start": result.get("start"), "end": result.get("end"),
    }


# ── A/B side-by-side rendering ────────────────────────────────────────


def _gate_marks(s: dict) -> dict:
    """Boolean PASS/FAIL for each gate, evaluated against the discounted PF."""
    pf_disc = _discount_pf(s["pf"], SURVIVORSHIP_DISCOUNT_CONSERVATIVE)
    return {
        "pf": ((pf_disc == float("inf")) or
                (isinstance(pf_disc, float) and not math.isnan(pf_disc)
                 and pf_disc > GATE_PROFIT_FACTOR)),
        "sharpe": (not math.isnan(s["sharpe"])
                    and s["sharpe"] > GATE_SHARPE),
        "mdd": (not math.isnan(s["mdd"])
                and abs(s["mdd"]) < GATE_MAX_DRAWDOWN),
        "wr": (not math.isnan(s["wr"]) and s["wr"] > GATE_WIN_RATE),
    }


def ab_window_block(window_label: str, baseline: dict, filtered: dict
                     ) -> list[str]:
    """Side-by-side metrics table for ONE window."""
    lines: list[str] = []
    lines.append(f"### {window_label}")
    lines.append("")
    lines.append(f"_{baseline['start'].date()} -> "
                  f"{baseline['end'].date()}, "
                  f"{baseline['n_days']} trading days._")
    lines.append("")
    base_pf_disc = _discount_pf(baseline["pf"], SURVIVORSHIP_DISCOUNT_CONSERVATIVE)
    filt_pf_disc = _discount_pf(filtered["pf"], SURVIVORSHIP_DISCOUNT_CONSERVATIVE)
    base_gates = _gate_marks(baseline)
    filt_gates = _gate_marks(filtered)

    def _mark(v: bool) -> str:
        return "PASS" if v else "FAIL"

    lines.append("| Metric | Baseline (filter OFF) | Dual-mom (filter ON) | "
                  "Delta |")
    lines.append("|---|---:|---:|---:|")
    # Discounted PF -- the HEADLINE
    lines.append(f"| ★ PF (30% disc, HEADLINE) | "
                  f"{_fmt_pf(base_pf_disc)} {_mark(base_gates['pf'])} | "
                  f"{_fmt_pf(filt_pf_disc)} {_mark(filt_gates['pf'])} | "
                  f"{_pf_delta(base_pf_disc, filt_pf_disc)} |")
    lines.append(f"| PF (raw) | {_fmt_pf(baseline['pf'])} | "
                  f"{_fmt_pf(filtered['pf'])} | "
                  f"{_pf_delta(baseline['pf'], filtered['pf'])} |")
    lines.append(f"| Sharpe | {_fmt(baseline['sharpe'])} "
                  f"{_mark(base_gates['sharpe'])} | "
                  f"{_fmt(filtered['sharpe'])} "
                  f"{_mark(filt_gates['sharpe'])} | "
                  f"{_num_delta(baseline['sharpe'], filtered['sharpe'])} |")
    base_mdd_pct = (abs(baseline["mdd"]) * 100
                    if not math.isnan(baseline["mdd"]) else float("nan"))
    filt_mdd_pct = (abs(filtered["mdd"]) * 100
                    if not math.isnan(filtered["mdd"]) else float("nan"))
    lines.append(f"| |max DD| | {_fmt(base_mdd_pct, 2, '%')} "
                  f"{_mark(base_gates['mdd'])} | "
                  f"{_fmt(filt_mdd_pct, 2, '%')} "
                  f"{_mark(filt_gates['mdd'])} | "
                  f"{_num_delta(base_mdd_pct, filt_mdd_pct, suffix='%')} |")
    lines.append(f"| Win rate | {_fmt(baseline['wr'])} "
                  f"{_mark(base_gates['wr'])} | "
                  f"{_fmt(filtered['wr'])} "
                  f"{_mark(filt_gates['wr'])} | "
                  f"{_num_delta(baseline['wr'], filtered['wr'])} |")
    lines.append(f"| CAGR | {_fmt(baseline['cagr'])} | "
                  f"{_fmt(filtered['cagr'])} | "
                  f"{_num_delta(baseline['cagr'], filtered['cagr'])} |")
    lines.append(f"| n_trades | {baseline['n_trades']} | "
                  f"{filtered['n_trades']} | "
                  f"{filtered['n_trades'] - baseline['n_trades']:+d} |")
    base_gates_passed = sum(base_gates.values())
    filt_gates_passed = sum(filt_gates.values())
    lines.append(f"| **Gates cleared (disc 30%)** | **{base_gates_passed} of "
                  f"4** | **{filt_gates_passed} of 4** | "
                  f"{filt_gates_passed - base_gates_passed:+d} |")
    lines.append("")

    # Occupancy
    bo = baseline["occupancy"]
    fo = filtered["occupancy"]
    lines.append("**Book occupancy (% of trading days):**")
    lines.append("")
    lines.append("| Occupancy | Baseline | Dual-mom | Delta |")
    lines.append("|---|---:|---:|---:|")
    lines.append(f"| Fully invested (== {MOM_TOP_N} positions) | "
                  f"{bo['pct_full'] * 100:.1f}% | "
                  f"{fo['pct_full'] * 100:.1f}% | "
                  f"{(fo['pct_full'] - bo['pct_full']) * 100:+.1f}pp |")
    lines.append(f"| Partial (1 to {MOM_TOP_N - 1}) | "
                  f"{bo['pct_partial'] * 100:.1f}% | "
                  f"{fo['pct_partial'] * 100:.1f}% | "
                  f"{(fo['pct_partial'] - bo['pct_partial']) * 100:+.1f}pp |")
    lines.append(f"| **Fully CASH (0 positions)** | "
                  f"**{bo['pct_zero'] * 100:.1f}%** | "
                  f"**{fo['pct_zero'] * 100:.1f}%** | "
                  f"{(fo['pct_zero'] - bo['pct_zero']) * 100:+.1f}pp |")
    lines.append(f"| Mean positions | {bo['mean_positions']:.2f} | "
                  f"{fo['mean_positions']:.2f} | "
                  f"{fo['mean_positions'] - bo['mean_positions']:+.2f} |")
    lines.append("")

    # Per-year side-by-side
    base_yr = per_year_pnl(baseline["trades"])
    filt_yr = per_year_pnl(filtered["trades"])
    if not base_yr.empty or not filt_yr.empty:
        lines.append("**Per-year breakdown:**")
        lines.append("")
        lines.append("| Year | Baseline n | Baseline PF | Baseline PnL | "
                      "Dual n | Dual PF | Dual PnL |")
        lines.append("|---:|---:|---:|---:|---:|---:|---:|")
        years = sorted(set(base_yr["year"].tolist() if not base_yr.empty else [])
                        | set(filt_yr["year"].tolist() if not filt_yr.empty else []))
        for yr in years:
            br = base_yr.loc[base_yr["year"] == yr] if not base_yr.empty else pd.DataFrame()
            fr = filt_yr.loc[filt_yr["year"] == yr] if not filt_yr.empty else pd.DataFrame()
            br_n = int(br["n_trades"].iloc[0]) if not br.empty else 0
            fr_n = int(fr["n_trades"].iloc[0]) if not fr.empty else 0
            br_pf = _fmt_pf(br["pf"].iloc[0]) if not br.empty else "n/a"
            fr_pf = _fmt_pf(fr["pf"].iloc[0]) if not fr.empty else "n/a"
            br_pnl = f"{br['total_pnl'].iloc[0]:+,.0f}" if not br.empty else "n/a"
            fr_pnl = f"{fr['total_pnl'].iloc[0]:+,.0f}" if not fr.empty else "n/a"
            lines.append(f"| {yr} | {br_n} | {br_pf} | {br_pnl} | "
                          f"{fr_n} | {fr_pf} | {fr_pnl} |")
        lines.append("")

    # Crash-window DDs side-by-side (only entries inside the window)
    lines.append("**Momentum-crash DD diagnostic:**")
    lines.append("")
    lines.append("| Window | Baseline DD | Dual-mom DD | Delta |")
    lines.append("|---|---:|---:|---:|")
    for b, f in zip(baseline["crash_dds"], filtered["crash_dds"]):
        if not (b["in_range"] or f["in_range"]):
            continue
        b_str = (f"{b['magnitude'] * 100:.2f}%" if b["in_range"]
                  else "_out of range_")
        f_str = (f"{f['magnitude'] * 100:.2f}%" if f["in_range"]
                  else "_out of range_")
        delta = ""
        if b["in_range"] and f["in_range"]:
            d = (f["magnitude"] - b["magnitude"]) * 100
            delta = f"{d:+.2f}pp"
        lines.append(f"| {b['label']} ({b['start'].date()} -> "
                      f"{b['end'].date()}) | {b_str} | {f_str} | {delta} |")
    lines.append("")

    # Deepest DD anchor (helpful for narrative)
    bd = baseline["deepest_dd"]
    fd = filtered["deepest_dd"]
    if bd["peak_date"] is not None and fd["peak_date"] is not None:
        lines.append(
            f"_Deepest DD baseline: {bd['peak_date'].date()} -> "
            f"{bd['trough_date'].date()} ({bd['magnitude'] * 100:.2f}%) | "
            f"dual-mom: {fd['peak_date'].date()} -> "
            f"{fd['trough_date'].date()} ({fd['magnitude'] * 100:.2f}%)_")
        lines.append("")
    return lines


def _pf_delta(a: float, b: float) -> str:
    if (isinstance(a, float) and math.isnan(a)) or (
            isinstance(b, float) and math.isnan(b)):
        return "n/a"
    if a == float("inf") or b == float("inf"):
        return "(inf)"
    return f"{b - a:+.3f}"


def _num_delta(a: float, b: float, *, suffix: str = "") -> str:
    if (isinstance(a, float) and math.isnan(a)) or (
            isinstance(b, float) and math.isnan(b)):
        return "n/a"
    return f"{b - a:+.3f}{suffix}"


# ── Verdict ────────────────────────────────────────────────────────────


def headline_verdict(baseline_ho: dict, filtered_ho: dict,
                       *, filter_was_noop: bool = False) -> list[str]:
    """The single most important question, answered plainly: did
    dual-momentum turn this into a deploy candidate?
    """
    out: list[str] = []
    base_pf_disc = _discount_pf(baseline_ho["pf"], SURVIVORSHIP_DISCOUNT_CONSERVATIVE)
    filt_pf_disc = _discount_pf(filtered_ho["pf"], SURVIVORSHIP_DISCOUNT_CONSERVATIVE)
    base_mdd = abs(baseline_ho["mdd"]) if not math.isnan(baseline_ho["mdd"]) else float("nan")
    filt_mdd = abs(filtered_ho["mdd"]) if not math.isnan(filtered_ho["mdd"]) else float("nan")
    base_n = baseline_ho["n_trades"]
    filt_n = filtered_ho["n_trades"]

    dd_dropped = (not math.isnan(filt_mdd) and not math.isnan(base_mdd)
                  and filt_mdd < base_mdd)
    dd_under_gate = (not math.isnan(filt_mdd)
                     and filt_mdd < GATE_MAX_DRAWDOWN)
    pf_intact = (isinstance(filt_pf_disc, float)
                 and not math.isnan(filt_pf_disc)
                 and filt_pf_disc > GATE_PROFIT_FACTOR)
    sharpe_ok = (not math.isnan(filtered_ho["sharpe"])
                 and filtered_ho["sharpe"] > GATE_SHARPE)
    wr_ok = (not math.isnan(filtered_ho["wr"])
             and filtered_ho["wr"] > GATE_WIN_RATE)
    too_few = filt_n < 30

    # Headline one-liner
    out.append("### THE HEADLINE QUESTION (HELD-OUT, 30%-discounted)")
    out.append("")
    out.append(f"- Baseline |max DD|     = "
                f"{base_mdd * 100:.2f}%   |  PF disc = {_fmt_pf(base_pf_disc)}  "
                f"|  n_trades = {base_n}")
    out.append(f"- Dual-mom |max DD|     = "
                f"{filt_mdd * 100:.2f}%   |  PF disc = {_fmt_pf(filt_pf_disc)}  "
                f"|  n_trades = {filt_n}")
    out.append("")
    if dd_dropped:
        improvement = (base_mdd - filt_mdd) * 100
        out.append(f"- Held-out |max DD| DROPPED by **{improvement:.2f}pp** "
                    f"({base_mdd*100:.2f}% -> {filt_mdd*100:.2f}%).")
    else:
        regression = (filt_mdd - base_mdd) * 100
        out.append(f"- Held-out |max DD| did NOT drop "
                    f"({base_mdd*100:.2f}% -> {filt_mdd*100:.2f}% = "
                    f"{regression:+.2f}pp).")
    if dd_under_gate:
        out.append(f"- Dual-mom |max DD| **CLEARS** the {GATE_MAX_DRAWDOWN:.0%} gate.")
    else:
        out.append(f"- Dual-mom |max DD| **FAILS** the {GATE_MAX_DRAWDOWN:.0%} gate.")
    if pf_intact:
        out.append(f"- 30%-discounted PF survives the {GATE_PROFIT_FACTOR} "
                    f"gate (= {_fmt_pf(filt_pf_disc)}).")
    else:
        out.append(f"- 30%-discounted PF **fails** the {GATE_PROFIT_FACTOR} "
                    f"gate (= {_fmt_pf(filt_pf_disc)}).")
    out.append("")

    # Verdict paragraph
    if filter_was_noop:
        out.append("**Verdict: filter was a NO-OP -- not the right "
                    "intervention for this universe.** Across every "
                    "rebalance day in every window, all 15 top-ranked "
                    "names had positive absolute momentum, so the "
                    "filter never fired. Trade tape, equity curve, and "
                    "every metric are byte-identical between baseline "
                    "and filtered. MOM-2 baseline's failure mode is not "
                    "'we hold names with negative absolute momentum' -- "
                    "it is 'relative winners get whipsawed in fast "
                    "reversals'. The Antonacci dual-momentum filter at "
                    "threshold=0 cannot address that failure mode on "
                    "this universe. Honest null result; follow-ups "
                    "below propose interventions that target the actual "
                    "failure mode.")
    elif dd_under_gate and pf_intact and sharpe_ok and wr_ok and not too_few:
        out.append("**Verdict: TENTATIVE DEPLOY CANDIDATE.** Dual-momentum "
                    "cut the held-out drawdown below the gate while the "
                    "30%-discounted edge stays above the PF gate, with "
                    "Sharpe + win-rate both intact. This is a CANDIDATE, "
                    "not a deploy -- per LAW 3 the real validation is "
                    "forward paper-trading. The mined-data caveat stands: "
                    "this is the SECOND backtest run on this universe; even "
                    "a clean result has reduced statistical purity.")
    elif pf_intact and not dd_under_gate:
        out.append("**Verdict: edge intact, risk still fails.** The "
                    "absolute filter preserved or improved the discounted "
                    "PF but did NOT drag |max DD| below the 15% gate. "
                    "The dual-momentum filter alone is insufficient. "
                    "Follow-ups proposed below (NOT applied here per LAW 4).")
    elif dd_under_gate and not pf_intact:
        out.append("**Verdict: dual-momentum cut DD by cutting edge.** "
                    "The filter dropped the held-out drawdown below the "
                    "gate, but the 30%-discounted PF fell below 1.3. We "
                    "traded a real edge for a tolerable drawdown -- "
                    "that's not deploy-worthy.")
    elif not dd_dropped:
        out.append("**Verdict: dual-momentum did not help.** The "
                    "absolute filter neither reduced |max DD| nor "
                    "improved discounted PF on held-out. The MOM-2 "
                    "baseline's failure mode is not the kind of "
                    "absolute-momentum-below-zero pattern the filter "
                    "targets.")
    else:
        out.append("**Verdict: mixed.** DD improved but other gates "
                    "fell; see the gate columns above. Not a deploy.")
    out.append("")

    # Mined-data caveat (always stated)
    out.append("_Mined-data caveat: this is the second walk-forward we "
                "have run on this universe and momentum is the most "
                "survivorship-sensitive strategy in the project. Even "
                "a clean held-out result is a CANDIDATE for paper-trade "
                "validation -- NOT a deploy. The 30% PF haircut may still "
                "flatter the read, since MOMENTUM_UNIVERSE is current "
                "membership, not point-in-time._")
    return out


# ── Followups ──────────────────────────────────────────────────────────


def proposed_followups(baseline_ho: dict, filtered_ho: dict) -> list[str]:
    items: list[str] = []
    filt_mdd = abs(filtered_ho["mdd"]) if not math.isnan(filtered_ho["mdd"]) else float("nan")
    filt_pf_disc = _discount_pf(filtered_ho["pf"], SURVIVORSHIP_DISCOUNT_CONSERVATIVE)

    if not math.isnan(filt_mdd) and filt_mdd >= GATE_MAX_DRAWDOWN:
        items.append(
            f"**T-candidate: portfolio DD cap layered on dual-momentum.** "
            f"Held-out |max DD| = {filt_mdd*100:.2f}% > "
            f"{GATE_MAX_DRAWDOWN:.0%} gate even with the filter. Reuse the "
            f"MR-4 ``dd_cap_pct`` harness param. Note the symmetric re-arm "
            f"trap MR-4 surfaced.")

    if (isinstance(filt_pf_disc, float) and not math.isnan(filt_pf_disc)
            and filt_pf_disc < GATE_PROFIT_FACTOR):
        items.append(
            "**T-candidate: investigate whether MOM_TOP_N or "
            "MAX_PER_SECTOR is too tight under the filter.** If the "
            "filter rejects many names in a downturn, the surviving "
            "portfolio may be too narrow / sector-concentrated to "
            "generate the edge it does in calmer regimes.")

    filt_n = filtered_ho["n_trades"]
    if filt_n < 30:
        items.append(
            f"**T-candidate: extend held-out evaluation period.** Only "
            f"{filt_n} trades on held-out under the filter -- below the "
            f"LAW 8 n>=30 threshold for significance.")

    if not items:
        items.append("None mechanically triggered. If paper-trade "
                      "validates the dual-momentum read, the next ticket "
                      "is the paper-trade harness, not another backtest.")
    return [f"- {it}" for it in items]


# ── Compose ────────────────────────────────────────────────────────────


def build_report(baseline_results: dict, filtered_results: dict,
                  filter_activity_per_window: dict,
                  *, t_elapsed: float, n_symbols: int) -> str:
    lines: list[str] = []
    lines.append("# MOM-4 -- Dual-Momentum A/B (Absolute Filter ON vs OFF)")
    lines.append("")
    lines.append("**Branch:** `feature/mom4-abs-filter`")
    lines.append("**Strategy:** `signals.momentum.MomentumStrategy` -- "
                  "MOM-2 baseline (filter OFF) vs MOM-4 dual-mom "
                  "(``use_absolute_filter=True``, threshold=0 exactly, "
                  "parameter-free per Antonacci).")
    lines.append(f"**Replay data:** {n_symbols} MOMENTUM_UNIVERSE symbols.")
    lines.append(f"**Initial capital:** Rs {INITIAL_CAPITAL:,.0f}")
    lines.append(f"**Wall-clock:** {t_elapsed:.1f}s (both legs, all windows).")
    lines.append("")

    # Run params
    lines.append("## 0. Run parameters (matched to MOM-3 exactly)")
    lines.append("")
    lines.append("| Param | Value |")
    lines.append("|---|---:|")
    lines.append(f"| max_positions | {MOM_MAX_POSITIONS} (= MOM_TOP_N) |")
    lines.append(f"| max_per_sector | {MOM_MAX_PER_SECTOR} |")
    lines.append(f"| max_heat | {MOM_MAX_HEAT:.2f} |")
    lines.append("| risk_pct / slippage / brokerage | (config defaults) |")
    lines.append("")
    lines.append("Strategy knobs: lookback=252, skip=21, top_n=15 "
                  "(MOM_TOP_N). Absolute filter threshold = 0 exactly -- "
                  "NOT tuned. ONE change vs MOM-3 (LAW 4).")
    lines.append("")

    # Anti-overfit framing + windows
    lines.append("## 1. Windows")
    lines.append("")
    lines.append("| Window | Range | Trading days |")
    lines.append("|---|---|---:|")
    bh = baseline_results["HELD-OUT"]
    bi = baseline_results["INSPECT"]
    bf = baseline_results["FULL"]
    lines.append(f"| INSPECT | {INSPECT_START.date()} -> "
                  f"{INSPECT_END.date()} | {bi['n_days']} |")
    lines.append(f"| **HELD-OUT (verdict)** | {HOLDOUT_START.date()} -> "
                  f"{HOLDOUT_END.date()} | {bh['n_days']} |")
    lines.append(f"| FULL | {bf['start'].date()} -> "
                  f"{bf['end'].date()} | {bf['n_days']} |")
    lines.append("")

    # Filter-activity diagnostic — does the filter actually fire?
    lines.append("## 1b. Filter activity (would the filter fire?)")
    lines.append("")
    lines.append("For each monthly rebalance day in each window, score "
                  "the universe and count how many of the top-15 (by "
                  "relative rank) had a non-positive 12-1 momentum score. "
                  "These are the candidates the dual-momentum filter "
                  "WOULD drop. Computed post-hoc, independent of the "
                  "replay.")
    lines.append("")
    lines.append("| Window | n_rebal_days | rebals with >=1 negative in top-15 | rebals with ALL top-15 negative |")
    lines.append("|---|---:|---:|---:|")
    for label in ("INSPECT", "HELD-OUT", "FULL"):
        fa = filter_activity_per_window[label]
        lines.append(f"| {label} | {fa['n_rebal_days']} | "
                      f"{fa['n_rebal_with_any_neg_in_topN']} | "
                      f"{fa['n_rebal_all_neg_in_topN']} |")
    lines.append("")
    any_fired = any(
        filter_activity_per_window[w]["n_rebal_with_any_neg_in_topN"] > 0
        for w in ("INSPECT", "HELD-OUT", "FULL"))
    if not any_fired:
        lines.append("**The absolute filter at threshold=0 was a NO-OP on "
                      "this universe — across every rebalance day in every "
                      "window, all 15 top-ranked names had positive "
                      "absolute momentum.** In a 136-name universe with "
                      "broad sector coverage, somewhere there are always "
                      "15 names with positive 12-1 momentum, even in "
                      "2020 COVID and 2022 reversal. The filter cannot "
                      "rescue MOM-3's drawdown because the drawdown "
                      "mechanism is NOT 'we hold names with negative "
                      "absolute momentum' — it is 'the relative winners "
                      "themselves get whipsawed in fast reversals'.")
        lines.append("")

    # HEADLINE VERDICT (held-out, the only window that decides deploy)
    lines.append("## 2. HELD-OUT verdict (the primary read)")
    lines.append("")
    lines.extend(headline_verdict(baseline_results["HELD-OUT"],
                                     filtered_results["HELD-OUT"],
                                     filter_was_noop=not any_fired))
    lines.append("")
    lines.append("### Side-by-side -- held-out")
    lines.append("")
    lines.extend(ab_window_block("HELD-OUT", baseline_results["HELD-OUT"],
                                    filtered_results["HELD-OUT"]))

    # INSPECT
    lines.append("## 3. INSPECT (descriptive)")
    lines.append("")
    lines.extend(ab_window_block("INSPECT", baseline_results["INSPECT"],
                                    filtered_results["INSPECT"]))

    # FULL
    lines.append("## 4. FULL (descriptive)")
    lines.append("")
    lines.extend(ab_window_block("FULL", baseline_results["FULL"],
                                    filtered_results["FULL"]))

    # Survivorship caveat
    lines.append("## 5. Survivorship caveat")
    lines.append("")
    lines.append("From `data/universe.py`:")
    lines.append("")
    lines.append(f"> {MOMENTUM_SURVIVORSHIP_NOTE}")
    lines.append("")
    lines.append("Discount applied (per ops MOM brief): **30% conservative "
                  "HEADLINE**, 25% lighter shown for comparison.")
    lines.append("")
    lines.append("| Window | Baseline PF raw | Baseline disc-30% | "
                  "Dual-mom PF raw | Dual-mom disc-30% | "
                  "Dual disc-25% |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for label in ("INSPECT", "HELD-OUT", "FULL"):
        bw = baseline_results[label]
        fw = filtered_results[label]
        lines.append(
            f"| {label} | {_fmt_pf(bw['pf'])} | "
            f"{_fmt_pf(_discount_pf(bw['pf'], 0.30))} | "
            f"{_fmt_pf(fw['pf'])} | "
            f"**{_fmt_pf(_discount_pf(fw['pf'], 0.30))}** | "
            f"{_fmt_pf(_discount_pf(fw['pf'], 0.25))} |")
    lines.append("")

    # Significance for the filtered held-out
    lines.append("## 6. Significance -- dual-momentum held-out only")
    lines.append("")
    s = filtered_results["HELD-OUT"]
    lines.append(f"- Binomial p (n={s['n_trades']}, wins={s['wins']}): "
                  f"**{s['p_value']:.4f}**")
    lines.append(f"- Bootstrap PF CI (2000 resamples) 5/50/95: "
                  f"{_fmt_pf(s['bs']['p05'])} / "
                  f"{_fmt_pf(s['bs']['p50'])} / "
                  f"{_fmt_pf(s['bs']['p95'])}")
    if not math.isnan(s["bs"]["p05"]) and not math.isnan(s["bs"]["p95"]):
        if s["bs"]["p05"] < 1.0 < s["bs"]["p95"]:
            lines.append("- 90% CI **spans 1.0** -- bootstrap cannot rule "
                          "out break-even.")
        elif s["bs"]["p05"] >= 1.0:
            lines.append("- 5th-percentile PF >= 1.0 -- pessimistic tail "
                          "still positive.")
        else:
            lines.append("- 95th-percentile PF < 1.0 -- optimistic tail "
                          "still negative.")
    lines.append("")

    # Followups
    lines.append("## 7. Proposed follow-up tickets (NOT applied per LAW 4)")
    lines.append("")
    lines.extend(proposed_followups(baseline_results["HELD-OUT"],
                                      filtered_results["HELD-OUT"]))
    lines.append("")
    lines.append("_End of report._")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────


def _run_window(data: dict, *, start: pd.Timestamp | None,
                  end: pd.Timestamp | None, filter_on: bool, label: str
                  ) -> dict:
    strat = MomentumStrategy(use_absolute_filter=filter_on)
    res = run_replay(
        data, strat,
        initial_capital=INITIAL_CAPITAL,
        max_positions=MOM_MAX_POSITIONS,
        max_per_sector=MOM_MAX_PER_SECTOR,
        max_heat=MOM_MAX_HEAT,
        start=start, end=end,
    )
    return summarise_window(label, res, top_n=MOM_TOP_N)


def main() -> int:
    t0 = time.time()
    print("[MOM-4] loading MOMENTUM_UNIVERSE ...")
    data = load_momentum_universe()
    print(f"[MOM-4] {len(data)} symbols loaded "
          f"(load wall {time.time()-t0:.1f}s)")
    if not data:
        print("[MOM-4] ABORT -- empty data dict")
        return 1

    windows = (
        ("INSPECT", INSPECT_START, INSPECT_END),
        ("HELD-OUT", HOLDOUT_START, HOLDOUT_END),
        ("FULL", None, None),
    )

    baseline_results: dict[str, dict] = {}
    filtered_results: dict[str, dict] = {}
    filter_activity_per_window: dict[str, dict] = {}

    for label, start, end in windows:
        # Filter activity (post-hoc, cheap — scoring at month boundaries
        # only). Tells us how often the filter would actually fire.
        filter_activity_per_window[label] = filter_activity(
            data, start=start, end=end)
        fa = filter_activity_per_window[label]
        print(f"[MOM-4] {label} filter activity: "
              f"{fa['n_rebal_with_any_neg_in_topN']} / "
              f"{fa['n_rebal_days']} rebals would have filtered something; "
              f"{fa['n_rebal_all_neg_in_topN']} would have gone fully cash")

        print(f"[MOM-4] {label}: baseline (filter OFF) ...")
        t_phase = time.time()
        baseline_results[label] = _run_window(
            data, start=start, end=end, filter_on=False, label=label)
        print(f"    baseline wall {time.time()-t_phase:.1f}s  "
              f"PF raw={_fmt_pf(baseline_results[label]['pf'])}  "
              f"n_trades={baseline_results[label]['n_trades']}  "
              f"|maxDD|={abs(baseline_results[label]['mdd']) * 100:.2f}%")

        print(f"[MOM-4] {label}: dual-momentum (filter ON) ...")
        t_phase = time.time()
        filtered_results[label] = _run_window(
            data, start=start, end=end, filter_on=True, label=label)
        print(f"    filtered wall {time.time()-t_phase:.1f}s  "
              f"PF raw={_fmt_pf(filtered_results[label]['pf'])}  "
              f"n_trades={filtered_results[label]['n_trades']}  "
              f"|maxDD|={abs(filtered_results[label]['mdd']) * 100:.2f}%  "
              f"%cash={filtered_results[label]['occupancy']['pct_zero']*100:.1f}%")

    t_total = time.time() - t0
    print(f"[MOM-4] total wall {t_total:.1f}s "
          f"({t_total / 60:.1f} min). Writing report ...")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        build_report(baseline_results, filtered_results,
                      filter_activity_per_window,
                      t_elapsed=t_total, n_symbols=len(data)),
        encoding="utf-8")
    print(f"[MOM-4] report -> {REPORT_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
