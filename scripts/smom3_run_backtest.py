"""SMOM-3 — honest walk-forward A/B: small/mid-cap momentum (with low-vol
tilt) vs pure momentum baseline, both under BRUTAL small-cap costs and
a 45% survivorship discount as the HEADLINE.

PURPOSE
=======
The MOM project (MOM-3 / MOM-5) showed that momentum on the NIFTY-200-ish
MOMENTUM_UNIVERSE clears edge gates only marginally after a 30% haircut
and fails the DD gate. SMOM asks whether dropping down to the small/mid
universe AND adding a low-vol tilt can produce a deployable edge once
honest small-cap execution costs are applied.

The brutal costs are the central test:
    slippage_pct = 0.004    # 40 bps per fill — small-cap bid/ask + impact
    brokerage_pct = config default
These costs typically halve or worse the apparent PF of a high-turnover
small-cap strategy. If the edge survives them on top of the 45%
survivorship discount, that is a genuine signal.

PRE-REGISTERED PARAMETERS (NOT tuned)
=====================================
    slippage_pct          = 0.004      (brutal — per ops directive)
    brokerage_pct         = default    (from config)
    SURVIVORSHIP DISCOUNT = 0.45       (45% PF haircut — HEADLINE)
    SURVIVORSHIP_LIGHT    = 0.40       (40% — comparison only)
    Strategy knobs        = SMOM-2 module defaults
                            (top_n=15, pool_multiplier=2,
                             vol_window=63, min_traded=Rs 1 crore)
    Run-param caps        = same as MOM-3 (max_positions=15,
                                            max_per_sector=5,
                                            max_heat=0.20)

WINDOWS — matched to MOM-3 EXACTLY
==================================
    INSPECT  : 2016-01-04 -> 2022-12-30
    HELD-OUT : 2023-01-02 -> 2026-06-03
    FULL     : 2014-06-09 -> 2026-06-03

A/B = MomentumStrategy (no tilt) vs SmidMomentumStrategy (with tilt).
Same run params for both legs; SAME brutal costs for both legs. The
ONLY variable between legs is the strategy.

HONEST VERDICT (what the report MUST answer)
============================================
* Did 45%-discounted held-out PF survive > 1.3 with brutal costs?
* Did |max DD| drop below 15%?
* Sharpe + win-rate + n_trades?
* Per-year: did any window have a structural failure?
* Did the low-vol tilt help vs momentum-only?
* Did "cheap-looking small-cap momentum" die on execution?
* Edge confirmation OR paper-trade kill?

MINED-DATA TALLY
================
This is the FIFTH walk-forward on this DB (MR-2, MOM-3, MOM-4, MOM-5,
SMOM-3). Each historical test reduces validity. SMOM is on a DIFFERENT
universe (small/mid vs MOMENTUM 200), so the marginal mining cost is
smaller than another iteration on the MOM universe — but it's still a
mined-data result. Per the standing rule, even a clean win is a
PAPER-TRADE candidate, never a deploy.
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
    BROKERAGE_PCT, GATE_MAX_DRAWDOWN, GATE_PROFIT_FACTOR, GATE_SHARPE,
    GATE_WIN_RATE, INITIAL_CAPITAL, MOM_TOP_N,
)
from data.universe import (
    SMID_SURVIVORSHIP_NOTE, SMID_UNIVERSE,
)
from scripts.mom3_run_backtest import (
    HOLDOUT_END, HOLDOUT_START, INSPECT_END, INSPECT_START,
    MOM_MAX_HEAT, MOM_MAX_PER_SECTOR, MOM_MAX_POSITIONS,
    MOMENTUM_CRASH_WINDOWS, _fmt, _fmt_pf, crash_window_dd,
    deepest_drawdown,
)
from signals.momentum import MomentumStrategy
from signals.smid_momentum import SmidMomentumStrategy

REPORT_PATH = PROJECT_ROOT / "logs" / "smom3_ab_report.md"

# ── Pre-registered SMOM-3 parameters (NOT tuned) ───────────────────────
SMOM_BRUTAL_SLIPPAGE = 0.004              # 40 bps — small-cap brutal
SMOM_BROKERAGE = BROKERAGE_PCT             # config default
SURVIVORSHIP_DISCOUNT_HEADLINE = 0.45      # 45% (NOT 30%) — HEADLINE
SURVIVORSHIP_DISCOUNT_LIGHTER = 0.40       # comparison only


def _discount_pf(pf: float, discount: float) -> float:
    if pf is None or (isinstance(pf, float) and math.isnan(pf)):
        return float("nan")
    if pf == float("inf"):
        return float("inf")
    return pf * (1.0 - discount)


# ── Data loading (SMID_UNIVERSE only) ──────────────────────────────────


def load_smid_universe() -> dict[str, pd.DataFrame]:
    """Read daily bars for every SMID_UNIVERSE symbol that has rows.
    Drops weekend bars (NSE muhurat sessions etc.) per the MOM-3
    pattern so the timeline is strictly Mon-Fri."""
    from config import DB_PATH
    out: dict[str, pd.DataFrame] = {}
    con = sqlite3.connect(f"file:{Path(DB_PATH).as_posix()}?mode=ro",
                           uri=True)
    try:
        for sym in SMID_UNIVERSE:
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
            df = df[df.index.dayofweek < 5]
            out[sym] = df
    finally:
        con.close()
    return out


# ── Per-window summary ─────────────────────────────────────────────────


def summarise_window(label: str, result: dict) -> dict:
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
    return {
        "label": label, "pf": pf, "sharpe": sharpe, "mdd": mdd, "wr": wr,
        "cagr": cagr, "n_trades": n, "wins": wins, "p_value": p_val,
        "bs": bs, "robust": robust, "flags": flags,
        "trades": trades, "equity": equity,
        "deepest_dd": deepest, "crash_dds": crash_dds,
        "n_days": result.get("n_days", 0),
        "start": result.get("start"), "end": result.get("end"),
    }


def _run_window(data: dict, *, strategy_factory, start, end,
                  label: str) -> dict:
    strat = strategy_factory()
    res = run_replay(
        data, strat,
        initial_capital=INITIAL_CAPITAL,
        max_positions=MOM_MAX_POSITIONS,
        max_per_sector=MOM_MAX_PER_SECTOR,
        max_heat=MOM_MAX_HEAT,
        slippage_pct=SMOM_BRUTAL_SLIPPAGE,
        brokerage_pct=SMOM_BROKERAGE,
        start=start, end=end,
    )
    return summarise_window(label, res)


# ── A/B render ────────────────────────────────────────────────────────


def _gate_marks(s: dict) -> dict:
    pf_disc = _discount_pf(s["pf"], SURVIVORSHIP_DISCOUNT_HEADLINE)
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


def ab_window_block(window_label: str, baseline: dict, smid: dict
                     ) -> list[str]:
    lines: list[str] = []
    lines.append(f"### {window_label}")
    lines.append("")
    if baseline["start"] is not None:
        lines.append(f"_{baseline['start'].date()} -> "
                      f"{baseline['end'].date()}, "
                      f"{baseline['n_days']} trading days._")
        lines.append("")
    base_pf_disc = _discount_pf(baseline["pf"], SURVIVORSHIP_DISCOUNT_HEADLINE)
    smid_pf_disc = _discount_pf(smid["pf"], SURVIVORSHIP_DISCOUNT_HEADLINE)
    base_gates = _gate_marks(baseline)
    smid_gates = _gate_marks(smid)
    base_pass = sum(base_gates.values())
    smid_pass = sum(smid_gates.values())

    lines.append("| Metric | Momentum-only | SMID (tilt) | Delta |")
    lines.append("|---|---:|---:|---:|")
    lines.append(f"| ★ PF disc-45% (HEADLINE) | "
                  f"{_fmt_pf(base_pf_disc)} {_mark(base_gates['pf'])} | "
                  f"{_fmt_pf(smid_pf_disc)} {_mark(smid_gates['pf'])} | "
                  f"{_pf_delta(base_pf_disc, smid_pf_disc)} |")
    lines.append(f"| PF (raw) | {_fmt_pf(baseline['pf'])} | "
                  f"{_fmt_pf(smid['pf'])} | "
                  f"{_pf_delta(baseline['pf'], smid['pf'])} |")
    lines.append(f"| Sharpe | {_fmt(baseline['sharpe'])} "
                  f"{_mark(base_gates['sharpe'])} | "
                  f"{_fmt(smid['sharpe'])} "
                  f"{_mark(smid_gates['sharpe'])} | "
                  f"{_num_delta(baseline['sharpe'], smid['sharpe'])} |")
    base_mdd_pct = (abs(baseline["mdd"]) * 100
                    if not math.isnan(baseline["mdd"]) else float("nan"))
    smid_mdd_pct = (abs(smid["mdd"]) * 100
                    if not math.isnan(smid["mdd"]) else float("nan"))
    lines.append(f"| |max DD| | {_fmt(base_mdd_pct, 2, '%')} "
                  f"{_mark(base_gates['mdd'])} | "
                  f"{_fmt(smid_mdd_pct, 2, '%')} "
                  f"{_mark(smid_gates['mdd'])} | "
                  f"{_num_delta(base_mdd_pct, smid_mdd_pct, suffix='%')} |")
    lines.append(f"| Win rate | {_fmt(baseline['wr'])} "
                  f"{_mark(base_gates['wr'])} | "
                  f"{_fmt(smid['wr'])} "
                  f"{_mark(smid_gates['wr'])} | "
                  f"{_num_delta(baseline['wr'], smid['wr'])} |")
    lines.append(f"| CAGR | {_fmt(baseline['cagr'])} | "
                  f"{_fmt(smid['cagr'])} | "
                  f"{_num_delta(baseline['cagr'], smid['cagr'])} |")
    lines.append(f"| n_trades | {baseline['n_trades']} | "
                  f"{smid['n_trades']} | "
                  f"{smid['n_trades'] - baseline['n_trades']:+d} |")
    lines.append(f"| **Gates cleared (disc 45%)** | **{base_pass} of 4** | "
                  f"**{smid_pass} of 4** | {smid_pass - base_pass:+d} |")
    lines.append("")

    # Per-year
    bp = per_year_pnl(baseline["trades"])
    sp = per_year_pnl(smid["trades"])
    if not bp.empty or not sp.empty:
        lines.append("**Per-year breakdown:**")
        lines.append("")
        lines.append("| Year | Mom n | Mom PF | Mom PnL | SMID n | SMID PF | SMID PnL |")
        lines.append("|---:|---:|---:|---:|---:|---:|---:|")
        years = sorted(set(bp["year"].tolist() if not bp.empty else [])
                        | set(sp["year"].tolist() if not sp.empty else []))
        for yr in years:
            br = bp.loc[bp["year"] == yr] if not bp.empty else pd.DataFrame()
            sr = sp.loc[sp["year"] == yr] if not sp.empty else pd.DataFrame()
            br_n = int(br["n_trades"].iloc[0]) if not br.empty else 0
            sr_n = int(sr["n_trades"].iloc[0]) if not sr.empty else 0
            br_pf = _fmt_pf(br["pf"].iloc[0]) if not br.empty else "n/a"
            sr_pf = _fmt_pf(sr["pf"].iloc[0]) if not sr.empty else "n/a"
            br_pnl = f"{br['total_pnl'].iloc[0]:+,.0f}" if not br.empty else "n/a"
            sr_pnl = f"{sr['total_pnl'].iloc[0]:+,.0f}" if not sr.empty else "n/a"
            lines.append(f"| {yr} | {br_n} | {br_pf} | {br_pnl} | "
                          f"{sr_n} | {sr_pf} | {sr_pnl} |")
        lines.append("")

    # Crash windows
    lines.append("**Momentum-crash DDs:**")
    lines.append("")
    lines.append("| Window | Range | Mom DD | SMID DD |")
    lines.append("|---|---|---:|---:|")
    for b, s in zip(baseline["crash_dds"], smid["crash_dds"]):
        if not (b["in_range"] or s["in_range"]):
            continue
        rng = f"{b['start'].date()} -> {b['end'].date()}"
        b_str = (f"{b['magnitude'] * 100:.2f}%" if b["in_range"]
                  else "_out of range_")
        s_str = (f"{s['magnitude'] * 100:.2f}%" if s["in_range"]
                  else "_out of range_")
        lines.append(f"| {b['label']} | {rng} | {b_str} | {s_str} |")
    lines.append("")

    bd = baseline["deepest_dd"]
    sd = smid["deepest_dd"]
    if bd["peak_date"] is not None and sd["peak_date"] is not None:
        lines.append(
            f"_Deepest DD momentum: {bd['peak_date'].date()} -> "
            f"{bd['trough_date'].date()} ({bd['magnitude'] * 100:.2f}%) | "
            f"SMID: {sd['peak_date'].date()} -> "
            f"{sd['trough_date'].date()} ({sd['magnitude'] * 100:.2f}%)_")
        lines.append("")

    # Robustness for the SMID held-out specifically
    if window_label == "HELD-OUT":
        r = smid["robust"]
        f = smid["flags"]
        lines.append("**Robustness suite (SMID held-out):**")
        lines.append("")
        lines.append("| Question | Value |")
        lines.append("|---|---:|")
        lines.append(f"| Raw PF | {_fmt_pf(r.pf_raw)} |")
        if r.top_symbol:
            lines.append(
                f"| PF with top-contributing symbol removed "
                f"({r.top_symbol}, Rs {r.top_symbol_pnl:+,.0f}) | "
                f"{_fmt_pf(r.pf_ex_top_symbol)} |")
        else:
            lines.append("| PF with top-contributing symbol removed | "
                          "n/a (no positive symbol) |")
        if r.best_year:
            lines.append(
                f"| PF with best year removed ({r.best_year}, "
                f"Rs {r.best_year_pnl:+,.0f}) | "
                f"{_fmt_pf(r.pf_ex_best_year)} |")
        n_traded = len(per_symbol_pnl(smid["trades"]))
        lines.append(f"| # symbols net-negative | "
                      f"{r.n_negative_symbols} of {n_traded} |")
        lines.append(f"| Top-symbol share of gross PnL | "
                      f"{_fmt(f['top_symbol_share'] * 100, 1, '%')} |")
        lines.append(f"| Top-year share of gross PnL | "
                      f"{_fmt(f['top_year_share'] * 100, 1, '%')} |")
        lines.append("")
        # Significance
        lines.append("**Significance (SMID held-out):**")
        lines.append("")
        lines.append(f"- Binomial p (n={smid['n_trades']}, "
                      f"wins={smid['wins']}): **{smid['p_value']:.4f}**")
        lines.append(f"- Bootstrap PF CI 5/50/95: "
                      f"{_fmt_pf(smid['bs']['p05'])} / "
                      f"{_fmt_pf(smid['bs']['p50'])} / "
                      f"{_fmt_pf(smid['bs']['p95'])}")
        if (not math.isnan(smid["bs"]["p05"])
                and not math.isnan(smid["bs"]["p95"])):
            if smid["bs"]["p05"] < 1.0 < smid["bs"]["p95"]:
                lines.append("- 90% CI spans 1.0 — bootstrap cannot rule "
                              "out break-even.")
            elif smid["bs"]["p05"] >= 1.0:
                lines.append("- 5th-percentile PF >= 1.0 — pessimistic "
                              "tail still positive.")
            else:
                lines.append("- 95th-percentile PF < 1.0 — optimistic "
                              "tail still negative.")
        lines.append("")

    return lines


def headline_verdict(baseline_ho: dict, smid_ho: dict) -> list[str]:
    out: list[str] = []
    base_pf_disc = _discount_pf(baseline_ho["pf"], SURVIVORSHIP_DISCOUNT_HEADLINE)
    smid_pf_disc = _discount_pf(smid_ho["pf"], SURVIVORSHIP_DISCOUNT_HEADLINE)
    base_mdd = abs(baseline_ho["mdd"]) if not math.isnan(baseline_ho["mdd"]) else float("nan")
    smid_mdd = abs(smid_ho["mdd"]) if not math.isnan(smid_ho["mdd"]) else float("nan")
    base_sharpe = baseline_ho["sharpe"]
    smid_sharpe = smid_ho["sharpe"]
    base_n = baseline_ho["n_trades"]
    smid_n = smid_ho["n_trades"]

    out.append("### THE HEADLINE QUESTION (HELD-OUT, 45%-discounted, "
                "brutal costs)")
    out.append("")
    out.append(f"- Momentum-only: PF disc-45% = "
                f"{_fmt_pf(base_pf_disc)}  |  |max DD| = "
                f"{base_mdd * 100:.2f}%  |  Sharpe = "
                f"{_fmt(base_sharpe)}  |  n = {base_n}")
    out.append(f"- SMID (low-vol tilt): PF disc-45% = "
                f"**{_fmt_pf(smid_pf_disc)}**  |  |max DD| = "
                f"**{smid_mdd * 100:.2f}%**  |  Sharpe = "
                f"{_fmt(smid_sharpe)}  |  n = {smid_n}")
    out.append("")

    pf_pass = (isinstance(smid_pf_disc, float)
                and not math.isnan(smid_pf_disc)
                and smid_pf_disc > GATE_PROFIT_FACTOR)
    dd_pass = (not math.isnan(smid_mdd)
                and smid_mdd < GATE_MAX_DRAWDOWN)
    sharpe_pass = (not math.isnan(smid_sharpe)
                    and smid_sharpe > GATE_SHARPE)
    wr_pass = (not math.isnan(smid_ho["wr"])
                and smid_ho["wr"] > GATE_WIN_RATE)
    too_few = smid_n < 30

    if pf_pass and dd_pass and sharpe_pass and wr_pass and not too_few:
        out.append("**Verdict: SMID with low-vol tilt is a TENTATIVE "
                    "DEPLOY CANDIDATE.** All 4 gates clear after the "
                    "45% survivorship haircut AND brutal 40bps slippage. "
                    "Per LAW 3, validation is paper-trading. The "
                    "mined-data caveat stands (5th historical "
                    "walk-forward) and the SMID universe's survivorship "
                    "discount is set HIGHER than MOM's exactly because "
                    "the bankruptcy/delist tail is fatter at this end "
                    "of the market — 45% may STILL be optimistic.")
    elif pf_pass and not dd_pass:
        out.append("**Verdict: PARTIAL WIN — edge survives 45% haircut + "
                    "brutal costs but DD gate still fails.** The "
                    "discounted PF cleared the bar; the |max DD| did "
                    "not drop below 15%. SMID's small-cap exposure is "
                    "less violent than large-cap MOM but still above "
                    "the deploy threshold.")
    elif not pf_pass and dd_pass:
        out.append("**Verdict: small-cap momentum died on execution.** "
                    "Brutal costs + 45% discount sank the PF below the "
                    "gate, even though the DD profile improved. The "
                    "headline-positive small-cap momentum literature "
                    "doesn't survive realistic spread + impact costs "
                    "on this universe.")
    elif not pf_pass and not dd_pass:
        out.append("**Verdict: SMID with the low-vol tilt is NOT a "
                    "deploy candidate at this calibration.** Neither the "
                    "PF gate (post 45% haircut + brutal costs) nor the "
                    "DD gate clear. The strategy's apparent edge in the "
                    "raw read is largely a survivorship + cheap-fill "
                    "artifact.")
    else:
        out.append("**Verdict: mixed.** See gate-pass columns above. "
                    "Not a deploy.")
    out.append("")

    # Did the tilt help vs baseline?
    if (isinstance(base_pf_disc, float) and not math.isnan(base_pf_disc)
            and isinstance(smid_pf_disc, float)
            and not math.isnan(smid_pf_disc)):
        delta = smid_pf_disc - base_pf_disc
        if delta > 0.05:
            out.append(f"- The low-vol TILT HELPED: disc-PF improved by "
                        f"{delta:+.3f} vs momentum-only baseline on the "
                        f"same brutal-cost run.")
        elif delta < -0.05:
            out.append(f"- The low-vol TILT HURT: disc-PF dropped by "
                        f"{-delta:.3f} vs momentum-only baseline.")
        else:
            out.append(f"- The low-vol tilt was ~flat vs momentum-only "
                        f"(disc-PF delta = {delta:+.3f}).")
    if not math.isnan(base_mdd) and not math.isnan(smid_mdd):
        dd_delta = (base_mdd - smid_mdd) * 100
        if dd_delta > 1.0:
            out.append(f"- Tilt REDUCED |max DD| by {dd_delta:.2f}pp.")
        elif dd_delta < -1.0:
            out.append(f"- Tilt INCREASED |max DD| by {-dd_delta:.2f}pp.")
        else:
            out.append("- Tilt did not move |max DD| materially.")
    out.append("")

    out.append("_Mined-data caveat: this is the FIFTH walk-forward on "
                "this DB (MR-2, MOM-3, MOM-4, MOM-5, SMOM-3). SMOM is "
                "on a DIFFERENT universe than MOM, which softens the "
                "marginal mining cost — but it is STILL a mined-data "
                "read. Per the standing rule, even a clean win is a "
                "paper-trade candidate, NEVER a deploy. The 45% PF "
                "haircut may STILL be optimistic given small-cap "
                "survivorship dynamics — many of the names in "
                "SMID_UNIVERSE today did not exist 10 years ago, and "
                "many similar names from that era are entirely absent._")
    return out


# ── Followups ──────────────────────────────────────────────────────────


def proposed_followups(smid_ho: dict) -> list[str]:
    items: list[str] = []
    smid_pf_disc = _discount_pf(smid_ho["pf"], SURVIVORSHIP_DISCOUNT_HEADLINE)
    smid_mdd = abs(smid_ho["mdd"]) if not math.isnan(smid_ho["mdd"]) else float("nan")
    n = smid_ho["n_trades"]

    if not math.isnan(smid_mdd) and smid_mdd >= GATE_MAX_DRAWDOWN:
        items.append(
            f"**T-candidate: SMID + portfolio DD cap (MR-4 mechanism)** "
            f"or **SMID + vol-scaling overlay (MOM-5)**. Both are "
            f"orthogonal harness knobs. Held-out |max DD| = "
            f"{smid_mdd*100:.2f}% > {GATE_MAX_DRAWDOWN:.0%}.")

    if (isinstance(smid_pf_disc, float) and not math.isnan(smid_pf_disc)
            and smid_pf_disc < GATE_PROFIT_FACTOR):
        items.append(
            "**T-candidate: increase MIN_MEDIAN_TRADED_VALUE floor** to "
            "constrain SMID further toward genuine midcaps; the gain "
            "would be less survivorship-vulnerable but the n_trades cost "
            "may be steep. Pre-register the new floor as well; do NOT "
            "sweep it.")

    if n < 30:
        items.append(f"**T-candidate: extend held-out evaluation period.** "
                      f"Only {n} trades on held-out — below LAW 8's n>=30 "
                      f"threshold for significance.")

    if not items:
        items.append("None mechanically triggered. The standing rule "
                      "says STRATEGY #4 (SMOM) is the last historical "
                      "experiment in the strategy phase; whatever the "
                      "outcome, next step is paper-trade infrastructure.")
    return [f"- {it}" for it in items]


# ── Compose ────────────────────────────────────────────────────────────


def build_report(baseline_results: dict, smid_results: dict, *,
                  t_elapsed: float, n_symbols: int) -> str:
    lines: list[str] = []
    lines.append("# SMOM-3 -- Small/Mid-Cap Momentum + Low-Vol Tilt A/B")
    lines.append("")
    lines.append("**Branch:** `feature/smom3-smid-backtest`")
    lines.append("**Universe:** `SMID_UNIVERSE` "
                  f"({n_symbols} of {len(SMID_UNIVERSE)} symbols loaded "
                  f"from market_data.db).")
    lines.append("**A/B legs:** `MomentumStrategy` (no tilt) vs "
                  "`SmidMomentumStrategy` (low-vol tilt + liquidity "
                  "sanity).")
    lines.append(f"**Initial capital:** Rs {INITIAL_CAPITAL:,.0f}")
    lines.append(f"**Wall-clock:** {t_elapsed:.1f}s.")
    lines.append("")

    lines.append("## 0. Pre-registered parameters (NOT tuned)")
    lines.append("")
    lines.append("| Param | Value | Notes |")
    lines.append("|---|---:|---|")
    lines.append(f"| **slippage_pct** | **{SMOM_BRUTAL_SLIPPAGE}** | "
                  f"BRUTAL — 40 bps; small-cap bid/ask + impact. |")
    lines.append(f"| brokerage_pct | {SMOM_BROKERAGE} | config default. |")
    lines.append(f"| max_positions | {MOM_MAX_POSITIONS} | "
                  f"matched to MOM-3 / MOM-5. |")
    lines.append(f"| max_per_sector | {MOM_MAX_PER_SECTOR} | matched. |")
    lines.append(f"| max_heat | {MOM_MAX_HEAT:.2f} | matched. |")
    lines.append(f"| ★ **SURVIVORSHIP discount (HEADLINE)** | "
                  f"**{SURVIVORSHIP_DISCOUNT_HEADLINE:.0%}** | "
                  f"45%, NOT 30% — small-caps have a much fatter "
                  f"bankruptcy/delist tail than MOM. |")
    lines.append(f"| Survivorship discount (lighter, compare only) | "
                  f"{SURVIVORSHIP_DISCOUNT_LIGHTER:.0%} | |")
    lines.append("")
    lines.append("SMID knobs (SMOM-2 module defaults): top_n=15, "
                  "momentum_pool_multiplier=2 (pool=30), vol_window=63, "
                  "min_median_traded_value=Rs 1 crore.")
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
    if bf["start"] is not None:
        lines.append(f"| FULL | {bf['start'].date()} -> "
                      f"{bf['end'].date()} | {bf['n_days']} |")
    lines.append("")

    # HEADLINE VERDICT
    lines.append("## 2. HELD-OUT verdict (the primary read)")
    lines.append("")
    lines.extend(headline_verdict(baseline_results["HELD-OUT"],
                                     smid_results["HELD-OUT"]))
    lines.append("")
    lines.append("### Side-by-side -- held-out")
    lines.append("")
    lines.extend(ab_window_block("HELD-OUT", baseline_results["HELD-OUT"],
                                    smid_results["HELD-OUT"]))

    lines.append("## 3. INSPECT (descriptive)")
    lines.append("")
    lines.extend(ab_window_block("INSPECT", baseline_results["INSPECT"],
                                    smid_results["INSPECT"]))

    lines.append("## 4. FULL (descriptive)")
    lines.append("")
    lines.extend(ab_window_block("FULL", baseline_results["FULL"],
                                    smid_results["FULL"]))

    # Survivorship caveat
    lines.append("## 5. Survivorship caveat (LOUD)")
    lines.append("")
    lines.append("From `data/universe.py`:")
    lines.append("")
    lines.append(f"> {SMID_SURVIVORSHIP_NOTE}")
    lines.append("")
    lines.append("Discount table -- HEADLINE bold:")
    lines.append("")
    lines.append("| Window | Momentum raw | Mom disc-45% | "
                  "SMID raw | SMID disc-45% (HEADLINE) | SMID disc-40% |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for label in ("INSPECT", "HELD-OUT", "FULL"):
        bw = baseline_results[label]
        sw = smid_results[label]
        lines.append(
            f"| {label} | {_fmt_pf(bw['pf'])} | "
            f"{_fmt_pf(_discount_pf(bw['pf'], 0.45))} | "
            f"{_fmt_pf(sw['pf'])} | "
            f"**{_fmt_pf(_discount_pf(sw['pf'], 0.45))}** | "
            f"{_fmt_pf(_discount_pf(sw['pf'], 0.40))} |")
    lines.append("")

    # Followups
    lines.append("## 6. Proposed follow-up tickets (NOT applied per LAW 4)")
    lines.append("")
    lines.extend(proposed_followups(smid_results["HELD-OUT"]))
    lines.append("")
    lines.append("_End of report._")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────


def main() -> int:
    t0 = time.time()
    print(f"[SMOM-3] loading SMID_UNIVERSE "
          f"({len(SMID_UNIVERSE)} candidates) ...")
    data = load_smid_universe()
    print(f"[SMOM-3] {len(data)} symbols loaded "
          f"(load wall {time.time()-t0:.1f}s)")
    if not data:
        print("[SMOM-3] ABORT -- empty data dict")
        return 1
    print(f"[SMOM-3] BRUTAL costs: slippage={SMOM_BRUTAL_SLIPPAGE} "
          f"(40 bps), brokerage={SMOM_BROKERAGE}.")
    print(f"[SMOM-3] HEADLINE discount: "
          f"{SURVIVORSHIP_DISCOUNT_HEADLINE:.0%} (NOT 30%).")

    windows = (
        ("INSPECT", INSPECT_START, INSPECT_END),
        ("HELD-OUT", HOLDOUT_START, HOLDOUT_END),
        ("FULL", None, None),
    )

    baseline_results: dict[str, dict] = {}
    smid_results: dict[str, dict] = {}

    for label, start, end in windows:
        print(f"[SMOM-3] {label}: momentum-only baseline ...")
        t_phase = time.time()
        baseline_results[label] = _run_window(
            data,
            strategy_factory=lambda: MomentumStrategy(),
            start=start, end=end, label=label)
        bw = baseline_results[label]
        print(f"    momentum baseline wall {time.time()-t_phase:.1f}s  "
              f"PF raw={_fmt_pf(bw['pf'])}  "
              f"n_trades={bw['n_trades']}  "
              f"|maxDD|={abs(bw['mdd']) * 100:.2f}%")

        print(f"[SMOM-3] {label}: SMID (low-vol tilt) ...")
        t_phase = time.time()
        smid_results[label] = _run_window(
            data,
            strategy_factory=lambda: SmidMomentumStrategy(),
            start=start, end=end, label=label)
        sw = smid_results[label]
        print(f"    SMID wall {time.time()-t_phase:.1f}s  "
              f"PF raw={_fmt_pf(sw['pf'])}  "
              f"n_trades={sw['n_trades']}  "
              f"|maxDD|={abs(sw['mdd']) * 100:.2f}%")

    t_total = time.time() - t0
    print(f"[SMOM-3] total wall {t_total:.1f}s "
          f"({t_total / 60:.1f} min). Writing report ...")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        build_report(baseline_results, smid_results,
                      t_elapsed=t_total, n_symbols=len(data)),
        encoding="utf-8")
    print(f"[SMOM-3] report -> {REPORT_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
