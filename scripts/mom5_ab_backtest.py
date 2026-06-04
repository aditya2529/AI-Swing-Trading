"""MOM-5 — A/B: MOM-2 baseline vs vol-scaled momentum (Barroso-Santa-Clara
crash-fix overlay).

PURPOSE
=======
MOM-3 surfaced a 36% held-out drawdown and MOM-4 showed the Antonacci
absolute filter at threshold=0 is a no-op on this universe. MOM-5 tests
whether the Barroso-Santa-Clara vol-scaling overlay (scale portfolio
exposure inversely to recent realized equity vol) cuts the drawdown
without killing the edge.

PRE-REGISTERED PARAMS (NOT tuned)
=================================
    vol_target_annual = 0.12     <-- the VERDICT runs against this value
    vol_window        = 63        # ~3 trading months, the BSC standard

We also run 0.10 and 0.15 as descriptive sensitivity, but THE VERDICT
IS 0.12 -- any other selection would be tuning.

ONE change vs MOM-3 / MOM-4 (LAW 4): the harness gets a vol-scaling
overlay. No strategy change, no other run-param changes.

WINDOWS / RUN PARAMS (matched to MOM-3 / MOM-4 exactly)
=======================================================
    INSPECT  : 2016-01-04 -> 2022-12-30
    HELD-OUT : 2023-01-02 -> 2026-06-03
    FULL     : 2014-06-09 -> 2026-06-03
    max_positions    = MOM_TOP_N (= 15)
    max_per_sector   = 5
    max_heat         = 0.20

WHAT THE REPORT MUST ANSWER (plainly, per ops)
==============================================
    * Did held-out maxDD drop from 36% toward the 15% gate? (Literature
      roughly halves the crash -> realistic expectation ~18-22%.)
    * Did the edge survive (disc-30% PF > 1.3)? Sharpe should improve
      per BSC; CAGR will drop somewhat (less exposure) -- report both.
    * Per-year: which years did exposure get cut (crash avoidance)?
    * Avg exposure_mult over time; % time scaled below 1.0.
    * HONEST VERDICT: deploy candidate, or DD cut at the cost of edge?

MINED-DATA CAVEAT
=================
This is the FOURTH backtest on this universe (MR-2, MOM-3, MOM-4,
MOM-5). Each one reduces the validity of the remaining tests for
forward-edge inference. Even a clean win is a CANDIDATE for paper-trade
validation -- never a deploy. Per the standing rule, MOM-5 is the LAST
historical experiment on MOM; after this we pivot to paper-trade
regardless of outcome.
"""
from __future__ import annotations

import math
import statistics
import sys
import time
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtesting.diagnostics import (
    binomial_p_value, bootstrap_pf_ci, concentration_flags, per_year_pnl,
    robustness,
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
    _discount_pf, _fmt, _fmt_pf, crash_window_dd, deepest_drawdown,
    load_momentum_universe,
)
from scripts.mom4_ab_backtest import (
    _num_delta, _pf_delta, occupancy_stats, positions_per_day,
)
from signals.momentum import MomentumStrategy

REPORT_PATH = PROJECT_ROOT / "logs" / "mom5_ab_report.md"

# ── Pre-registered parameters (NOT tuned) ──────────────────────────────
VOL_TARGET_ANNUAL_VERDICT = 0.12        # THE VERDICT runs against this
VOL_WINDOW = 63                          # ~3 trading months, BSC standard

# Descriptive sensitivity — NOT the verdict. Quoted so reviewers can
# see how the result moves on either side of the pre-registered choice.
VOL_TARGETS_SENSITIVITY = (0.10, 0.15)


# ── Exposure-mult diagnostic helpers ──────────────────────────────────


def exposure_stats(exposure_mults: list[tuple]) -> dict:
    """Stats on the per-day exposure multiplier trace produced by the
    vol-scaling harness path. Returns mean, median, fraction of days
    the scaler engaged (< 1.0), fraction of days deeply scaled
    (< 0.5), and the date of the deepest cut."""
    if not exposure_mults:
        return {"n_days": 0, "mean": float("nan"), "median": float("nan"),
                "pct_below_1": 0.0, "pct_below_half": 0.0,
                "min": float("nan"), "min_date": None}
    mults = [m for _, m in exposure_mults]
    n = len(mults)
    below_1 = sum(1 for m in mults if m < 1.0)
    below_half = sum(1 for m in mults if m < 0.5)
    min_mult = min(mults)
    min_date = next(d for d, m in exposure_mults if m == min_mult)
    return {
        "n_days": n,
        "mean": statistics.fmean(mults),
        "median": statistics.median(mults),
        "pct_below_1": below_1 / n,
        "pct_below_half": below_half / n,
        "min": min_mult,
        "min_date": min_date,
    }


def exposure_per_year(exposure_mults: list[tuple]) -> pd.DataFrame:
    """Per-calendar-year average exposure + % days scaled below 1.0."""
    if not exposure_mults:
        return pd.DataFrame()
    df = pd.DataFrame(exposure_mults, columns=["date", "mult"])
    df["year"] = pd.to_datetime(df["date"]).dt.year
    rows = []
    for yr, sub in df.groupby("year"):
        rows.append({
            "year": int(yr),
            "n_days": len(sub),
            "mean_mult": float(sub["mult"].mean()),
            "pct_below_1": float((sub["mult"] < 1.0).sum() / len(sub)),
        })
    return pd.DataFrame(rows).sort_values("year")


# ── Per-window summary ─────────────────────────────────────────────────


def summarise_window(label: str, result: dict, *, top_n: int,
                       has_vol_scaling: bool) -> dict:
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
    expo = result.get("exposure_mults", []) if has_vol_scaling else []
    expo_stats = exposure_stats(expo)
    expo_year = exposure_per_year(expo)
    return {
        "label": label, "pf": pf, "sharpe": sharpe, "mdd": mdd, "wr": wr,
        "cagr": cagr, "n_trades": n, "wins": wins, "p_value": p_val,
        "bs": bs, "robust": robust, "flags": flags,
        "trades": trades, "equity": equity,
        "deepest_dd": deepest, "crash_dds": crash_dds,
        "occupancy": occ,
        "exposure_stats": expo_stats, "exposure_per_year": expo_year,
        "n_days": result.get("n_days", 0),
        "start": result.get("start"), "end": result.get("end"),
    }


def _run_window(data: dict, *, start, end, vol_target: float | None,
                  label: str) -> dict:
    strat = MomentumStrategy()
    res = run_replay(
        data, strat,
        initial_capital=INITIAL_CAPITAL,
        max_positions=MOM_MAX_POSITIONS,
        max_per_sector=MOM_MAX_PER_SECTOR,
        max_heat=MOM_MAX_HEAT,
        vol_target_annual=vol_target,
        vol_window=VOL_WINDOW,
        start=start, end=end,
    )
    return summarise_window(label, res, top_n=MOM_TOP_N,
                              has_vol_scaling=vol_target is not None)


# ── A/B render ────────────────────────────────────────────────────────


def _gate_marks(s: dict) -> dict:
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


def _mark(v: bool) -> str:
    return "PASS" if v else "FAIL"


def ab_window_block(label: str, baseline: dict, voled: dict) -> list[str]:
    lines: list[str] = []
    lines.append(f"### {label}")
    lines.append("")
    lines.append(f"_{baseline['start'].date()} -> {baseline['end'].date()}, "
                  f"{baseline['n_days']} trading days._")
    lines.append("")
    base_pf_disc = _discount_pf(baseline["pf"], SURVIVORSHIP_DISCOUNT_CONSERVATIVE)
    vol_pf_disc = _discount_pf(voled["pf"], SURVIVORSHIP_DISCOUNT_CONSERVATIVE)
    base_gates = _gate_marks(baseline)
    vol_gates = _gate_marks(voled)
    base_pass = sum(base_gates.values())
    vol_pass = sum(vol_gates.values())

    lines.append("| Metric | Baseline (no vol) | Vol-scaled (target=0.12) | Delta |")
    lines.append("|---|---:|---:|---:|")
    lines.append(f"| ★ PF (30% disc, HEADLINE) | "
                  f"{_fmt_pf(base_pf_disc)} {_mark(base_gates['pf'])} | "
                  f"{_fmt_pf(vol_pf_disc)} {_mark(vol_gates['pf'])} | "
                  f"{_pf_delta(base_pf_disc, vol_pf_disc)} |")
    lines.append(f"| PF (raw) | {_fmt_pf(baseline['pf'])} | "
                  f"{_fmt_pf(voled['pf'])} | "
                  f"{_pf_delta(baseline['pf'], voled['pf'])} |")
    lines.append(f"| Sharpe | {_fmt(baseline['sharpe'])} "
                  f"{_mark(base_gates['sharpe'])} | "
                  f"{_fmt(voled['sharpe'])} "
                  f"{_mark(vol_gates['sharpe'])} | "
                  f"{_num_delta(baseline['sharpe'], voled['sharpe'])} |")
    base_mdd_pct = (abs(baseline["mdd"]) * 100
                    if not math.isnan(baseline["mdd"]) else float("nan"))
    vol_mdd_pct = (abs(voled["mdd"]) * 100
                   if not math.isnan(voled["mdd"]) else float("nan"))
    lines.append(f"| |max DD| | {_fmt(base_mdd_pct, 2, '%')} "
                  f"{_mark(base_gates['mdd'])} | "
                  f"{_fmt(vol_mdd_pct, 2, '%')} "
                  f"{_mark(vol_gates['mdd'])} | "
                  f"{_num_delta(base_mdd_pct, vol_mdd_pct, suffix='%')} |")
    lines.append(f"| Win rate | {_fmt(baseline['wr'])} "
                  f"{_mark(base_gates['wr'])} | "
                  f"{_fmt(voled['wr'])} "
                  f"{_mark(vol_gates['wr'])} | "
                  f"{_num_delta(baseline['wr'], voled['wr'])} |")
    lines.append(f"| CAGR | {_fmt(baseline['cagr'])} | "
                  f"{_fmt(voled['cagr'])} | "
                  f"{_num_delta(baseline['cagr'], voled['cagr'])} |")
    lines.append(f"| n_trades | {baseline['n_trades']} | "
                  f"{voled['n_trades']} | "
                  f"{voled['n_trades'] - baseline['n_trades']:+d} |")
    lines.append(f"| **Gates cleared (disc 30%)** | **{base_pass} of 4** | "
                  f"**{vol_pass} of 4** | {vol_pass - base_pass:+d} |")
    lines.append("")

    # Exposure stats (only meaningful on the voled run)
    es = voled["exposure_stats"]
    if es["n_days"] > 0:
        lines.append("**Exposure scaling -- vol-scaled run:**")
        lines.append("")
        lines.append("| Stat | Value |")
        lines.append("|---|---:|")
        lines.append(f"| Mean exposure_mult | {es['mean']:.3f} |")
        lines.append(f"| Median exposure_mult | {es['median']:.3f} |")
        lines.append(f"| % days scaled below 1.0 | "
                      f"{es['pct_below_1'] * 100:.1f}% |")
        lines.append(f"| % days scaled below 0.5 | "
                      f"{es['pct_below_half'] * 100:.1f}% |")
        if es["min_date"] is not None:
            lines.append(f"| Min exposure_mult | "
                          f"{es['min']:.3f} (on {es['min_date'].date()}) |")
        lines.append("")

    # Per-year exposure (for the voled run)
    pye = voled["exposure_per_year"]
    if not pye.empty:
        lines.append("**Per-year exposure -- vol-scaled run:**")
        lines.append("")
        lines.append("| Year | n_days | Mean exposure | % days < 1.0 |")
        lines.append("|---:|---:|---:|---:|")
        for _, r in pye.iterrows():
            lines.append(f"| {int(r['year'])} | {int(r['n_days'])} | "
                          f"{r['mean_mult']:.3f} | "
                          f"{r['pct_below_1'] * 100:.1f}% |")
        lines.append("")

    # Per-year PnL side-by-side
    bp = per_year_pnl(baseline["trades"])
    vp = per_year_pnl(voled["trades"])
    if not bp.empty or not vp.empty:
        lines.append("**Per-year PnL -- side-by-side:**")
        lines.append("")
        lines.append("| Year | Base n | Base PF | Base PnL | Vol n | Vol PF | Vol PnL |")
        lines.append("|---:|---:|---:|---:|---:|---:|---:|")
        years = sorted(set(bp["year"].tolist() if not bp.empty else [])
                        | set(vp["year"].tolist() if not vp.empty else []))
        for yr in years:
            br = bp.loc[bp["year"] == yr] if not bp.empty else pd.DataFrame()
            vr = vp.loc[vp["year"] == yr] if not vp.empty else pd.DataFrame()
            br_n = int(br["n_trades"].iloc[0]) if not br.empty else 0
            vr_n = int(vr["n_trades"].iloc[0]) if not vr.empty else 0
            br_pf = _fmt_pf(br["pf"].iloc[0]) if not br.empty else "n/a"
            vr_pf = _fmt_pf(vr["pf"].iloc[0]) if not vr.empty else "n/a"
            br_pnl = f"{br['total_pnl'].iloc[0]:+,.0f}" if not br.empty else "n/a"
            vr_pnl = f"{vr['total_pnl'].iloc[0]:+,.0f}" if not vr.empty else "n/a"
            lines.append(f"| {yr} | {br_n} | {br_pf} | {br_pnl} | "
                          f"{vr_n} | {vr_pf} | {vr_pnl} |")
        lines.append("")

    # Crash windows
    lines.append("**Momentum-crash DDs -- side-by-side:**")
    lines.append("")
    lines.append("| Window | Baseline DD | Vol-scaled DD | Delta |")
    lines.append("|---|---:|---:|---:|")
    for b, v in zip(baseline["crash_dds"], voled["crash_dds"]):
        if not (b["in_range"] or v["in_range"]):
            continue
        b_str = (f"{b['magnitude'] * 100:.2f}%" if b["in_range"]
                  else "_out of range_")
        v_str = (f"{v['magnitude'] * 100:.2f}%" if v["in_range"]
                  else "_out of range_")
        delta = ""
        if b["in_range"] and v["in_range"]:
            delta = f"{(v['magnitude'] - b['magnitude']) * 100:+.2f}pp"
        lines.append(f"| {b['label']} ({b['start'].date()} -> "
                      f"{b['end'].date()}) | {b_str} | {v_str} | {delta} |")
    lines.append("")

    bd = baseline["deepest_dd"]
    vd = voled["deepest_dd"]
    if bd["peak_date"] is not None and vd["peak_date"] is not None:
        lines.append(
            f"_Deepest DD baseline: {bd['peak_date'].date()} -> "
            f"{bd['trough_date'].date()} ({bd['magnitude'] * 100:.2f}%) | "
            f"vol-scaled: {vd['peak_date'].date()} -> "
            f"{vd['trough_date'].date()} ({vd['magnitude'] * 100:.2f}%)_")
        lines.append("")
    return lines


# ── Headline verdict ──────────────────────────────────────────────────


def headline_verdict(baseline_ho: dict, voled_ho: dict) -> list[str]:
    out: list[str] = []
    base_pf_disc = _discount_pf(baseline_ho["pf"], SURVIVORSHIP_DISCOUNT_CONSERVATIVE)
    vol_pf_disc = _discount_pf(voled_ho["pf"], SURVIVORSHIP_DISCOUNT_CONSERVATIVE)
    base_mdd = abs(baseline_ho["mdd"]) if not math.isnan(baseline_ho["mdd"]) else float("nan")
    vol_mdd = abs(voled_ho["mdd"]) if not math.isnan(voled_ho["mdd"]) else float("nan")
    base_sharpe = baseline_ho["sharpe"]
    vol_sharpe = voled_ho["sharpe"]
    base_cagr = baseline_ho["cagr"]
    vol_cagr = voled_ho["cagr"]
    base_n = baseline_ho["n_trades"]
    vol_n = voled_ho["n_trades"]

    out.append("### THE HEADLINE QUESTION (HELD-OUT, 30%-discounted)")
    out.append("")
    out.append(f"- Baseline: |max DD| = {base_mdd * 100:.2f}%  |  "
                f"PF disc = {_fmt_pf(base_pf_disc)}  |  "
                f"Sharpe = {_fmt(base_sharpe)}  |  "
                f"CAGR = {_fmt(base_cagr)}  |  n = {base_n}")
    out.append(f"- Vol-scaled (target=0.12): |max DD| = "
                f"{vol_mdd * 100:.2f}%  |  PF disc = {_fmt_pf(vol_pf_disc)}  |  "
                f"Sharpe = {_fmt(vol_sharpe)}  |  "
                f"CAGR = {_fmt(vol_cagr)}  |  n = {vol_n}")
    out.append("")

    dd_dropped = (not math.isnan(vol_mdd) and not math.isnan(base_mdd)
                  and vol_mdd < base_mdd)
    dd_under_gate = (not math.isnan(vol_mdd) and vol_mdd < GATE_MAX_DRAWDOWN)
    pf_intact = (isinstance(vol_pf_disc, float) and not math.isnan(vol_pf_disc)
                 and vol_pf_disc > GATE_PROFIT_FACTOR)
    sharpe_improved = (not math.isnan(vol_sharpe) and not math.isnan(base_sharpe)
                        and vol_sharpe > base_sharpe)
    sharpe_passes = (not math.isnan(vol_sharpe)
                     and vol_sharpe > GATE_SHARPE)
    wr_ok = (not math.isnan(voled_ho["wr"])
             and voled_ho["wr"] > GATE_WIN_RATE)
    too_few = vol_n < 30

    if dd_dropped:
        out.append(f"- Held-out |max DD| DROPPED by "
                    f"{(base_mdd - vol_mdd) * 100:.2f}pp "
                    f"({base_mdd*100:.2f}% -> {vol_mdd*100:.2f}%).")
    else:
        out.append(f"- Held-out |max DD| did NOT drop "
                    f"({base_mdd*100:.2f}% -> {vol_mdd*100:.2f}%).")
    if dd_under_gate:
        out.append(f"- Vol-scaled |max DD| **CLEARS** the "
                    f"{GATE_MAX_DRAWDOWN:.0%} gate.")
    else:
        out.append(f"- Vol-scaled |max DD| **FAILS** the "
                    f"{GATE_MAX_DRAWDOWN:.0%} gate "
                    f"(observed {vol_mdd*100:.2f}%).")
    if pf_intact:
        out.append(f"- 30%-discounted PF survives the "
                    f"{GATE_PROFIT_FACTOR} gate "
                    f"(= {_fmt_pf(vol_pf_disc)}).")
    else:
        out.append(f"- 30%-discounted PF **fails** the "
                    f"{GATE_PROFIT_FACTOR} gate "
                    f"(= {_fmt_pf(vol_pf_disc)}).")
    if sharpe_improved:
        out.append(f"- Sharpe IMPROVED from {_fmt(base_sharpe)} to "
                    f"{_fmt(vol_sharpe)} (BSC paper's primary claim "
                    f"reproduces).")
    else:
        out.append(f"- Sharpe DID NOT improve "
                    f"({_fmt(base_sharpe)} -> {_fmt(vol_sharpe)}) -- "
                    f"contrary to the BSC paper's primary claim on this "
                    f"universe.")
    if not math.isnan(base_cagr) and not math.isnan(vol_cagr):
        delta_cagr = (vol_cagr - base_cagr) * 100
        out.append(f"- CAGR moved {delta_cagr:+.2f}pp "
                    f"({_fmt(base_cagr)} -> {_fmt(vol_cagr)}) -- less "
                    f"exposure when vol bites, so CAGR is expected to "
                    f"come down somewhat.")
    out.append("")

    # Verdict paragraph
    if dd_under_gate and pf_intact and sharpe_passes and wr_ok and not too_few:
        out.append("**Verdict: TENTATIVE DEPLOY CANDIDATE.** Vol-scaled "
                    "momentum cut held-out drawdown below the gate while "
                    "the 30%-discounted PF stays above the PF gate, with "
                    "Sharpe and win-rate intact. The Barroso-Santa-Clara "
                    "primary claim (Sharpe up, DD down) reproduces on "
                    "this universe. **CANDIDATE only** -- per LAW 3 the "
                    "real validation is forward paper-trading. The "
                    "mined-data caveat stands: this is the FOURTH "
                    "backtest on this universe.")
    elif dd_dropped and pf_intact and not dd_under_gate:
        out.append("**Verdict: PARTIAL WIN -- DD materially lower but "
                    "still above gate.** The vol-scaling overlay cut the "
                    "drawdown and preserved the discounted PF, but the "
                    "remaining DD is still above the 15% gate. The "
                    "mechanism works as advertised by BSC, just not "
                    "enough on this universe at this calibration. "
                    "Further intervention proposed below -- NOT applied "
                    "here per LAW 4.")
    elif pf_intact and not dd_dropped:
        out.append("**Verdict: vol scaling did not help.** DD did not "
                    "drop materially even with the scaler engaged. "
                    "Possible diagnosis: the largest DDs are driven by "
                    "intra-month sharp moves that don't show up in the "
                    "63-day equity vol read. See the exposure stats "
                    "above for whether the scaler actually engaged in "
                    "the worst windows.")
    elif dd_under_gate and not pf_intact:
        out.append("**Verdict: cut DD by cutting edge.** The discounted "
                    "PF fell below 1.3. We traded a real edge for a "
                    "tolerable DD -- not deploy-worthy.")
    else:
        out.append("**Verdict: mixed.** See gate-pass columns above. "
                    "Not a deploy.")
    out.append("")

    out.append("_Mined-data caveat: this is the FOURTH walk-forward on "
                "this universe (MR-2, MOM-3, MOM-4, MOM-5). Each test "
                "reduces the validity of further historical inference. "
                "Momentum is also the most survivorship-sensitive "
                "strategy in this project; the 30% PF haircut may still "
                "flatter the read. Even a clean win = paper-trade "
                "candidate, NOT a deploy. Per the standing rule, this is "
                "the last historical MOM experiment; we pivot to paper "
                "next regardless._")
    return out


def proposed_followups(voled_ho: dict) -> list[str]:
    items: list[str] = []
    vol_mdd = abs(voled_ho["mdd"]) if not math.isnan(voled_ho["mdd"]) else float("nan")
    vol_pf_disc = _discount_pf(voled_ho["pf"], SURVIVORSHIP_DISCOUNT_CONSERVATIVE)
    vol_n = voled_ho["n_trades"]

    if not math.isnan(vol_mdd) and vol_mdd >= GATE_MAX_DRAWDOWN:
        items.append(
            f"**T-candidate: BSC vol scaling + portfolio DD cap (MR-4 "
            f"`dd_cap_pct`).** Vol scaling addresses smooth-vol regimes; "
            f"a DD cap catches the abrupt cliff-drops vol scaling can't "
            f"see in time. Held-out vol-scaled |max DD| = "
            f"{vol_mdd*100:.2f}% > {GATE_MAX_DRAWDOWN:.0%}.")

    if (isinstance(vol_pf_disc, float) and not math.isnan(vol_pf_disc)
            and vol_pf_disc < GATE_PROFIT_FACTOR):
        items.append(
            "**T-candidate: tighter sector / lower top_n.** If the "
            "discounted PF fell below the gate under scaling, the "
            "exposure cut may have removed too much of the concentrated "
            "edge that came from leverage-by-default sizing.")

    if vol_n < 30:
        items.append(
            f"**T-candidate: extend held-out evaluation period.** Only "
            f"{vol_n} trades on held-out under vol scaling -- below the "
            f"LAW 8 n>=30 threshold for significance.")

    if not items:
        items.append("None mechanically triggered. The standing rule "
                      "says MOM-5 is the last historical MOM experiment; "
                      "next step is paper-trading regardless of outcome.")
    return [f"- {it}" for it in items]


# ── Compose ────────────────────────────────────────────────────────────


def build_report(baseline_results: dict, voled_results: dict,
                  sensitivity: dict, *, t_elapsed: float,
                  n_symbols: int) -> str:
    lines: list[str] = []
    lines.append("# MOM-5 -- Vol-Scaled Sizing A/B "
                  "(Barroso-Santa-Clara overlay)")
    lines.append("")
    lines.append("**Branch:** `feature/mom5-vol-scaling`")
    lines.append("**Strategy:** `signals.momentum.MomentumStrategy` "
                  "(unchanged from MOM-2).")
    lines.append("**Overlay (NEW):** harness-level vol scaling -- "
                  "`run_replay(..., vol_target_annual, vol_window)`. "
                  "Default OFF preserves prior behaviour byte-for-byte.")
    lines.append(f"**Replay data:** {n_symbols} MOMENTUM_UNIVERSE symbols.")
    lines.append(f"**Initial capital:** Rs {INITIAL_CAPITAL:,.0f}")
    lines.append(f"**Wall-clock:** {t_elapsed:.1f}s.")
    lines.append("")

    lines.append("## 0. Pre-registered parameters (NOT tuned)")
    lines.append("")
    lines.append("| Param | Value | Notes |")
    lines.append("|---|---:|---|")
    lines.append(f"| vol_target_annual | **{VOL_TARGET_ANNUAL_VERDICT}** | "
                  f"VERDICT runs against this value. |")
    lines.append(f"| vol_window | {VOL_WINDOW} | ~3 trading months, BSC standard. |")
    lines.append(f"| max_positions | {MOM_MAX_POSITIONS} | "
                  f"matched to MOM-3 / MOM-4. |")
    lines.append(f"| max_per_sector | {MOM_MAX_PER_SECTOR} | "
                  f"matched. |")
    lines.append(f"| max_heat | {MOM_MAX_HEAT:.2f} | matched. |")
    lines.append("")
    lines.append("Strategy knobs: lookback=252, skip=21, top_n=15. "
                  "Sensitivity targets (0.10 and 0.15) reported as "
                  "descriptive -- they are NOT the verdict.")
    lines.append("")

    # Windows
    lines.append("## 1. Windows")
    lines.append("")
    bi = baseline_results["INSPECT"]
    bh = baseline_results["HELD-OUT"]
    bf = baseline_results["FULL"]
    lines.append("| Window | Range | Trading days |")
    lines.append("|---|---|---:|")
    lines.append(f"| INSPECT | {INSPECT_START.date()} -> "
                  f"{INSPECT_END.date()} | {bi['n_days']} |")
    lines.append(f"| **HELD-OUT (verdict)** | {HOLDOUT_START.date()} -> "
                  f"{HOLDOUT_END.date()} | {bh['n_days']} |")
    lines.append(f"| FULL | {bf['start'].date()} -> "
                  f"{bf['end'].date()} | {bf['n_days']} |")
    lines.append("")

    # HEADLINE VERDICT
    lines.append("## 2. HELD-OUT verdict (the primary read)")
    lines.append("")
    lines.extend(headline_verdict(baseline_results["HELD-OUT"],
                                     voled_results["HELD-OUT"]))
    lines.append("")
    lines.append("### Side-by-side -- held-out")
    lines.append("")
    lines.extend(ab_window_block("HELD-OUT", baseline_results["HELD-OUT"],
                                    voled_results["HELD-OUT"]))

    # INSPECT
    lines.append("## 3. INSPECT (descriptive)")
    lines.append("")
    lines.extend(ab_window_block("INSPECT", baseline_results["INSPECT"],
                                    voled_results["INSPECT"]))

    # FULL
    lines.append("## 4. FULL (descriptive)")
    lines.append("")
    lines.extend(ab_window_block("FULL", baseline_results["FULL"],
                                    voled_results["FULL"]))

    # Sensitivity
    lines.append("## 5. Sensitivity (descriptive -- NOT the verdict)")
    lines.append("")
    lines.append("These two targets are quoted purely so reviewers can "
                  "see how the held-out result moves on either side of "
                  "the pre-registered 0.12. Selecting any of them as the "
                  "verdict would be tuning -- the verdict stays at 0.12.")
    lines.append("")
    lines.append("| Target | PF raw | PF disc-30% | |max DD| | Sharpe | "
                  "CAGR | n_trades | Mean expo |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
    for tgt in sorted(set([VOL_TARGET_ANNUAL_VERDICT, *VOL_TARGETS_SENSITIVITY])):
        s = sensitivity[tgt]["HELD-OUT"]
        pf_disc = _discount_pf(s["pf"], SURVIVORSHIP_DISCOUNT_CONSERVATIVE)
        mdd_pct = (abs(s["mdd"]) * 100
                    if not math.isnan(s["mdd"]) else float("nan"))
        marker = " ★" if tgt == VOL_TARGET_ANNUAL_VERDICT else ""
        lines.append(
            f"| {tgt:.2f}{marker} | {_fmt_pf(s['pf'])} | "
            f"{_fmt_pf(pf_disc)} | {_fmt(mdd_pct, 2, '%')} | "
            f"{_fmt(s['sharpe'])} | {_fmt(s['cagr'])} | "
            f"{s['n_trades']} | "
            f"{s['exposure_stats']['mean']:.3f} |")
    lines.append("")

    # Survivorship caveat
    lines.append("## 6. Survivorship caveat")
    lines.append("")
    lines.append("From `data/universe.py`:")
    lines.append("")
    lines.append(f"> {MOMENTUM_SURVIVORSHIP_NOTE}")
    lines.append("")
    lines.append("Discount applied (per ops): **30% conservative HEADLINE**.")
    lines.append("")

    # Significance for voled held-out
    s = voled_results["HELD-OUT"]
    lines.append("## 7. Significance -- vol-scaled held-out only")
    lines.append("")
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

    # Follow-ups
    lines.append("## 8. Proposed follow-up tickets (NOT applied per LAW 4)")
    lines.append("")
    lines.extend(proposed_followups(voled_results["HELD-OUT"]))
    lines.append("")
    lines.append("_End of report._")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────


def main() -> int:
    t0 = time.time()
    print("[MOM-5] loading MOMENTUM_UNIVERSE ...")
    data = load_momentum_universe()
    print(f"[MOM-5] {len(data)} symbols loaded "
          f"(load wall {time.time()-t0:.1f}s)")
    if not data:
        return 1

    windows = (
        ("INSPECT", INSPECT_START, INSPECT_END),
        ("HELD-OUT", HOLDOUT_START, HOLDOUT_END),
        ("FULL", None, None),
    )

    baseline_results: dict[str, dict] = {}
    voled_results: dict[str, dict] = {}
    sensitivity: dict[float, dict[str, dict]] = {}

    # Baseline (no vol scaling) + verdict run (0.12) + sensitivity
    for label, start, end in windows:
        print(f"[MOM-5] {label}: baseline (no vol scaling) ...")
        t_phase = time.time()
        baseline_results[label] = _run_window(
            data, start=start, end=end, vol_target=None, label=label)
        print(f"    baseline wall {time.time()-t_phase:.1f}s  "
              f"PF raw={_fmt_pf(baseline_results[label]['pf'])}  "
              f"n_trades={baseline_results[label]['n_trades']}  "
              f"|maxDD|={abs(baseline_results[label]['mdd']) * 100:.2f}%")

        print(f"[MOM-5] {label}: vol-scaled "
              f"(target={VOL_TARGET_ANNUAL_VERDICT}) ...")
        t_phase = time.time()
        voled_results[label] = _run_window(
            data, start=start, end=end,
            vol_target=VOL_TARGET_ANNUAL_VERDICT, label=label)
        es = voled_results[label]["exposure_stats"]
        print(f"    voled wall {time.time()-t_phase:.1f}s  "
              f"PF raw={_fmt_pf(voled_results[label]['pf'])}  "
              f"n_trades={voled_results[label]['n_trades']}  "
              f"|maxDD|={abs(voled_results[label]['mdd']) * 100:.2f}%  "
              f"mean_expo={es['mean']:.3f}  "
              f"%<1.0={es['pct_below_1'] * 100:.1f}%")

    # Sensitivity — held-out only, both alternate targets + the verdict
    sensitivity[VOL_TARGET_ANNUAL_VERDICT] = {"HELD-OUT": voled_results["HELD-OUT"]}
    for tgt in VOL_TARGETS_SENSITIVITY:
        print(f"[MOM-5] sensitivity HELD-OUT: target={tgt} ...")
        t_phase = time.time()
        sensitivity[tgt] = {"HELD-OUT": _run_window(
            data, start=HOLDOUT_START, end=HOLDOUT_END,
            vol_target=tgt, label="HELD-OUT")}
        s = sensitivity[tgt]["HELD-OUT"]
        print(f"    target={tgt} wall {time.time()-t_phase:.1f}s  "
              f"PF raw={_fmt_pf(s['pf'])}  "
              f"|maxDD|={abs(s['mdd']) * 100:.2f}%  "
              f"mean_expo={s['exposure_stats']['mean']:.3f}")

    t_total = time.time() - t0
    print(f"[MOM-5] total wall {t_total:.1f}s "
          f"({t_total / 60:.1f} min). Writing report ...")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        build_report(baseline_results, voled_results, sensitivity,
                      t_elapsed=t_total, n_symbols=len(data)),
        encoding="utf-8")
    print(f"[MOM-5] report -> {REPORT_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
