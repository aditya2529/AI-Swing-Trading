"""WEEKLY-SWEEP — 6 weekly-cadence strategies x 2 walk-forward windows
on MOMENTUM_UNIVERSE under brutal costs. Honest answer: which (if any)
clears the bar in BOTH inspect AND held-out?

MULTIPLE-COMPARISONS GUARD
==========================
We are testing six candidate strategies. With 6 independent tries at
a single 1.3 PF gate, the chance of at least one false-positive
"winner" by coincidence is meaningful. To control for that, a
candidate is declared a real winner ONLY IF it:
    (a) clears disc-30% PF > 1.3 in BOTH windows, AND
    (b) clears |max DD| < 15% in held-out, AND
    (c) survives robustness (top-symbol-removed PF still > 1.3, AND
        bootstrap 5th-percentile PF > 1.0).

Anything that only wins one window — even spectacularly — is not a
candidate. This is the disciplined version of "p < alpha / k" for the
walk-forward + robustness setting.

PRE-REGISTERED PARAMETERS (NOT tuned)
=====================================
    slippage_pct  = 0.004      (BRUTAL 40 bps — small-cap impact-level
                                even though we run on the larger MOM
                                universe, so the cost honesty is
                                identical across all six)
    brokerage_pct = config default
    max_positions = 15, max_per_sector = 5, max_heat = 0.20
    SURVIVORSHIP discount = 30% (HEADLINE — MOM universe)
    Each strategy uses the literature defaults documented in its
    signals/weekly/*.py module — no project-specific tuning.

WINDOWS
=======
    INSPECT  : 2016-01-04 -> 2022-12-30
    HELD-OUT : 2023-01-02 -> 2026-06-03

UNIVERSE: MOMENTUM_UNIVERSE (137 names, weekday-filtered).
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
    BROKERAGE_PCT, DB_PATH, GATE_MAX_DRAWDOWN, GATE_PROFIT_FACTOR,
    GATE_SHARPE, GATE_WIN_RATE, INITIAL_CAPITAL,
)
from data.universe import MOMENTUM_SURVIVORSHIP_NOTE, MOMENTUM_UNIVERSE
from scripts.mom3_run_backtest import (
    HOLDOUT_END, HOLDOUT_START, INSPECT_END, INSPECT_START,
    MOM_MAX_HEAT, MOM_MAX_PER_SECTOR, MOM_MAX_POSITIONS,
    _fmt, _fmt_pf,
)
from scripts.smom3_run_backtest import (
    SMOM_BRUTAL_SLIPPAGE, SMOM_BROKERAGE, _discount_pf,
)
from signals.weekly.gap_reversal import GapReversal
from signals.weekly.pullback_52w import Pullback52W
from signals.weekly.rsi2_mean_reversion import RSI2MeanReversion
from signals.weekly.sector_rotation import WeeklySectorRotation
from signals.weekly.short_momentum import ShortMomentum
from signals.weekly.weekly_donchian import WeeklyDonchian

REPORT_PATH = PROJECT_ROOT / "logs" / "weekly_sweep_report.md"

# 30% headline discount for MOM universe — same as MOM-3 / MOM-5.
SURVIVORSHIP_DISCOUNT = 0.30


STRATEGIES = [
    ("RSI2-MeanRev",    RSI2MeanReversion),
    ("ShortMomentum63", ShortMomentum),
    ("WeeklyDonchian",  WeeklyDonchian),
    ("GapReversal",     GapReversal),
    ("SectorRotation",  WeeklySectorRotation),
    ("Pullback52W",     Pullback52W),
]


# ── Data loading (MOMENTUM_UNIVERSE only, weekday-filtered) ────────────


def load_universe() -> dict[str, pd.DataFrame]:
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
            df = df[df.index.dayofweek < 5]
            out[sym] = df
    finally:
        con.close()
    return out


# ── Cost-drag helper ───────────────────────────────────────────────────


def estimate_cost_drag(trades: pd.DataFrame, *, slippage_pct: float,
                        brokerage_pct: float) -> dict:
    if trades.empty:
        return {"slippage_rs": 0.0, "brokerage_rs": 0.0,
                "total_cost_rs": 0.0, "gross_turnover_rs": 0.0}
    gross = float(((trades["entry_price"] + trades["exit_price"])
                    * trades["shares"]).sum())
    slip = gross * slippage_pct
    brok = gross * brokerage_pct
    return {"slippage_rs": slip, "brokerage_rs": brok,
            "total_cost_rs": slip + brok,
            "gross_turnover_rs": gross}


# ── Per-(strategy, window) summary ─────────────────────────────────────


def summarise(label: str, result: dict) -> dict:
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
    cost = estimate_cost_drag(trades, slippage_pct=SMOM_BRUTAL_SLIPPAGE,
                                brokerage_pct=SMOM_BROKERAGE)
    n_days = result.get("n_days", 0)
    years = n_days / 252 if n_days else 1.0
    return {
        "label": label, "pf": pf, "sharpe": sharpe, "mdd": mdd, "wr": wr,
        "cagr": cagr, "n_trades": n, "wins": wins, "p_value": p_val,
        "bs": bs, "robust": robust, "flags": flags, "cost": cost,
        "trades": trades, "equity": equity,
        "n_days": n_days, "trades_per_year": n / max(years, 1e-9),
        "start": result.get("start"), "end": result.get("end"),
    }


def _run(data, strategy, start, end, label):
    res = run_replay(
        data, strategy,
        initial_capital=INITIAL_CAPITAL,
        max_positions=MOM_MAX_POSITIONS,
        max_per_sector=MOM_MAX_PER_SECTOR,
        max_heat=MOM_MAX_HEAT,
        slippage_pct=SMOM_BRUTAL_SLIPPAGE,
        brokerage_pct=SMOM_BROKERAGE,
        start=start, end=end,
    )
    return summarise(label, res)


# ── Winner detection (multiple-comparisons guard) ──────────────────────


def is_real_winner(insp: dict, ho: dict) -> tuple[bool, list[str]]:
    """Apply the MULTIPLE-COMPARISONS GUARD. Returns (is_winner,
    reasons-it-failed)."""
    reasons: list[str] = []

    insp_pf_disc = _discount_pf(insp["pf"], SURVIVORSHIP_DISCOUNT)
    ho_pf_disc = _discount_pf(ho["pf"], SURVIVORSHIP_DISCOUNT)

    if not (isinstance(insp_pf_disc, float) and not math.isnan(insp_pf_disc)
            and insp_pf_disc > GATE_PROFIT_FACTOR):
        reasons.append(f"INSPECT disc-PF {_fmt_pf(insp_pf_disc)} "
                        f"<= {GATE_PROFIT_FACTOR}")
    if not (isinstance(ho_pf_disc, float) and not math.isnan(ho_pf_disc)
            and ho_pf_disc > GATE_PROFIT_FACTOR):
        reasons.append(f"HELD-OUT disc-PF {_fmt_pf(ho_pf_disc)} "
                        f"<= {GATE_PROFIT_FACTOR}")
    if not (not math.isnan(ho["mdd"])
            and abs(ho["mdd"]) < GATE_MAX_DRAWDOWN):
        reasons.append(f"HELD-OUT |max DD| "
                        f"{abs(ho['mdd']) * 100:.2f}% >= "
                        f"{GATE_MAX_DRAWDOWN:.0%}")
    # Robustness: top-symbol-removed > 1.3 in held-out
    pf_ex_top = ho["robust"].pf_ex_top_symbol
    if isinstance(pf_ex_top, float) and not math.isnan(pf_ex_top):
        if pf_ex_top <= GATE_PROFIT_FACTOR:
            reasons.append(
                f"HELD-OUT PF-with-top-symbol-removed "
                f"{_fmt_pf(pf_ex_top)} <= {GATE_PROFIT_FACTOR} "
                f"(edge concentrated in one name)")
    # Bootstrap 5th-percentile > 1.0 in held-out
    p05 = ho["bs"]["p05"]
    if isinstance(p05, float) and not math.isnan(p05):
        if p05 <= 1.0:
            reasons.append(
                f"HELD-OUT bootstrap 5%-tile PF {_fmt_pf(p05)} <= 1.0 "
                f"(pessimistic tail breaks even or worse)")
    # Sample size sanity
    if ho["n_trades"] < 30:
        reasons.append(
            f"HELD-OUT n_trades = {ho['n_trades']} < 30 "
            f"(LAW 8: too few trades for significance)")
    return (len(reasons) == 0, reasons)


# ── Render ────────────────────────────────────────────────────────────


def _mark(v: bool) -> str:
    return "PASS" if v else "FAIL"


def main_table(results_by_strategy: dict) -> list[str]:
    """The ONE TABLE: all 6 strategies x both windows. Ten columns:
    disc-PF, raw PF, Sharpe, |max DD|, win, n_trades, trades/yr,
    cost-drag, gates cleared (disc), winner-condition status."""
    lines: list[str] = []
    lines.append("### Master comparison table (HELD-OUT primary)")
    lines.append("")
    lines.append("| Strategy | Window | PF disc-30% | PF raw | Sharpe | |max DD| | Win | n_tr | trades/yr | Cost (Rs) | Gates |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name, _ in STRATEGIES:
        for win_label in ("INSPECT", "HELD-OUT"):
            s = results_by_strategy[name][win_label]
            pf_disc = _discount_pf(s["pf"], SURVIVORSHIP_DISCOUNT)
            mdd_pct = (abs(s["mdd"]) * 100
                        if not math.isnan(s["mdd"]) else float("nan"))
            # Gates count
            gates = (
                (isinstance(pf_disc, float) and not math.isnan(pf_disc)
                 and pf_disc > GATE_PROFIT_FACTOR),
                (not math.isnan(s["sharpe"])
                 and s["sharpe"] > GATE_SHARPE),
                (not math.isnan(s["mdd"])
                 and abs(s["mdd"]) < GATE_MAX_DRAWDOWN),
                (not math.isnan(s["wr"]) and s["wr"] > GATE_WIN_RATE),
            )
            n_pass = sum(gates)
            lines.append(
                f"| {name} | {win_label} | "
                f"{_fmt_pf(pf_disc)} | {_fmt_pf(s['pf'])} | "
                f"{_fmt(s['sharpe'])} | {_fmt(mdd_pct, 2, '%')} | "
                f"{_fmt(s['wr'])} | {s['n_trades']} | "
                f"{s['trades_per_year']:.1f} | "
                f"Rs {s['cost']['total_cost_rs']:,.0f} | "
                f"{n_pass}/4 |")
    lines.append("")
    return lines


def robustness_block(name: str, insp: dict, ho: dict) -> list[str]:
    lines: list[str] = []
    r = ho["robust"]
    f = ho["flags"]
    lines.append(f"#### {name} robustness (held-out)")
    lines.append("")
    lines.append("| Question | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Raw PF | {_fmt_pf(r.pf_raw)} |")
    if r.top_symbol:
        lines.append(
            f"| PF with top symbol removed ({r.top_symbol}, "
            f"Rs {r.top_symbol_pnl:+,.0f}) | "
            f"{_fmt_pf(r.pf_ex_top_symbol)} |")
    if r.best_year:
        lines.append(
            f"| PF with best year removed ({r.best_year}, "
            f"Rs {r.best_year_pnl:+,.0f}) | "
            f"{_fmt_pf(r.pf_ex_best_year)} |")
    n_traded = len(per_symbol_pnl(ho["trades"]))
    lines.append(f"| # symbols net-negative | "
                  f"{r.n_negative_symbols} of {n_traded} |")
    lines.append(f"| Top-symbol share gross PnL | "
                  f"{_fmt(f['top_symbol_share'] * 100, 1, '%')} |")
    lines.append(f"| Top-year share gross PnL | "
                  f"{_fmt(f['top_year_share'] * 100, 1, '%')} |")
    lines.append(f"| Binomial p (n={ho['n_trades']}, wins={ho['wins']}) | "
                  f"{ho['p_value']:.4f} |")
    lines.append(f"| Bootstrap PF 5/50/95 | "
                  f"{_fmt_pf(ho['bs']['p05'])} / "
                  f"{_fmt_pf(ho['bs']['p50'])} / "
                  f"{_fmt_pf(ho['bs']['p95'])} |")
    lines.append("")
    # Per-year for held-out
    yr = per_year_pnl(ho["trades"])
    if not yr.empty:
        lines.append(f"##### {name} per-year (held-out)")
        lines.append("")
        lines.append("| Year | n | Win % | PF | PnL (Rs) |")
        lines.append("|---:|---:|---:|---:|---:|")
        for _, row in yr.iterrows():
            lines.append(f"| {int(row['year'])} | {int(row['n_trades'])} | "
                          f"{row['win_rate']:.3f} | "
                          f"{_fmt_pf(row['pf'])} | "
                          f"{row['total_pnl']:+,.0f} |")
        lines.append("")
    return lines


def build_report(results_by_strategy: dict, winners: list[tuple[str, dict, dict]],
                  failures: dict, *, t_elapsed: float,
                  n_symbols: int) -> str:
    lines: list[str] = []
    lines.append("# WEEKLY-SWEEP — 6 weekly-cadence strategies x 2 windows")
    lines.append("")
    lines.append("**Branch:** `feature/weekly-sweep`")
    lines.append(f"**Universe:** MOMENTUM_UNIVERSE ({n_symbols} loaded), "
                  "weekday-filtered, total-return adjusted.")
    lines.append(f"**Brutal costs:** slippage = {SMOM_BRUTAL_SLIPPAGE} "
                  f"(40 bps), brokerage = {SMOM_BROKERAGE}.")
    lines.append(f"**Survivorship discount (HEADLINE):** "
                  f"{SURVIVORSHIP_DISCOUNT:.0%} (MOM universe).")
    lines.append(f"**Wall-clock:** {t_elapsed:.1f}s.")
    lines.append("")

    # Multiple-comparisons guard explanation
    lines.append("## 0. Multiple-comparisons guard")
    lines.append("")
    lines.append("Six candidates are being tested at a single 1.3 disc-PF "
                  "gate. With 6 independent tries, the chance that AT "
                  "LEAST ONE clears by coincidence is meaningfully above "
                  "what a single-strategy gate would allow. To control "
                  "for that, a candidate is declared a real winner ONLY "
                  "IF it meets ALL of:")
    lines.append("")
    lines.append(f"1. disc-{int(SURVIVORSHIP_DISCOUNT*100)}% PF > "
                  f"{GATE_PROFIT_FACTOR} in **BOTH** INSPECT and HELD-OUT.")
    lines.append(f"2. |max DD| < {GATE_MAX_DRAWDOWN:.0%} in HELD-OUT.")
    lines.append(f"3. PF with top-contributing symbol removed > "
                  f"{GATE_PROFIT_FACTOR} in HELD-OUT (edge not "
                  "concentrated in one name).")
    lines.append("4. Bootstrap 5th-percentile PF > 1.0 in HELD-OUT "
                  "(pessimistic tail not break-even).")
    lines.append("5. n_trades >= 30 in HELD-OUT (LAW 8 sample size).")
    lines.append("")
    lines.append("Anything that wins one window only — even "
                  "spectacularly — is NOT a candidate.")
    lines.append("")

    # Master table
    lines.append("## 1. Master comparison table (the answer at a glance)")
    lines.append("")
    lines.extend(main_table(results_by_strategy))

    # Pre-registered params
    lines.append("## 2. Pre-registered parameters (NOT tuned)")
    lines.append("")
    lines.append("| Param | Value | Source |")
    lines.append("|---|---:|---|")
    lines.append("| RSI2 (trend_ma, rsi_period, oversold, exit, hold) | "
                  "200, 2, 10, 60, 5 | Connors RSI2 |")
    lines.append("| ShortMomentum lookback | 63 | Carhart 97; ~1Q |")
    lines.append("| WeeklyDonchian (lookback, vol_mult, chand, hold) | "
                  "20, 1.5, 3.0, 10 | Project breakout config |")
    lines.append("| GapReversal (gap_th, trend_ma, hold) | 5%, 200, 3 | "
                  "classic gap-down reversal |")
    lines.append("| SectorRotation (lookback, top_sec, top_n) | 63, 3, 15 | "
                  "GTAA sector momentum |")
    lines.append("| Pullback52W (high, near%, rsi_os, rsi_x, hold) | "
                  "252, 5%, 40, 55, 10 | Minervini-style pullback |")
    lines.append("| slippage / brokerage | 0.004 / config | BRUTAL impact |")
    lines.append("| max_positions / max_sec / max_heat | 15 / 5 / 0.20 | "
                  "matched to SMOM-3 |")
    lines.append("")

    # Why each failure (if applicable)
    lines.append("## 3. Why each non-winner failed")
    lines.append("")
    for name, _ in STRATEGIES:
        reasons = failures.get(name, [])
        if reasons:
            lines.append(f"- **{name}** -- "
                          + "; ".join(reasons))
    lines.append("")

    # Robustness for winners (if any)
    if winners:
        lines.append("## 4. Robustness suite -- winners only")
        lines.append("")
        for name, insp, ho in winners:
            lines.extend(robustness_block(name, insp, ho))
    else:
        lines.append("## 4. Robustness suite")
        lines.append("")
        lines.append("**No candidates met all winner conditions.** The "
                      "honest answer is: at this brutal cost regime and "
                      "30% survivorship haircut, NONE of the six "
                      "weekly-cadence strategies cleared the deploy bar "
                      "on BOTH inspect AND held-out windows with the "
                      "robustness guard in place. Per-strategy reasons "
                      "for failure are in §3.")
        lines.append("")

    # Survivorship caveat
    lines.append("## 5. Survivorship caveat")
    lines.append("")
    lines.append(f"> {MOMENTUM_SURVIVORSHIP_NOTE}")
    lines.append("")
    lines.append(f"Discount applied to PF (HEADLINE): "
                  f"**{SURVIVORSHIP_DISCOUNT:.0%}** (MOM universe).")
    lines.append("")

    # Final verdict
    lines.append("## 6. Verdict")
    lines.append("")
    if winners:
        names = ", ".join(w[0] for w in winners)
        lines.append(f"**Winner(s): {names}.** Cleared the multiple-"
                      f"comparisons guard. Per the standing rule, each "
                      f"winner is a **paper-trade candidate** — not a "
                      f"deploy. Ops re-verifies the held-out numbers + "
                      f"robustness suite personally before any deploy "
                      f"decision.")
    else:
        lines.append("**No weekly-cadence winner.** At brutal 40bps "
                      "costs and the 30% MOM survivorship discount, "
                      "none of the six candidates cleared the "
                      "multiple-comparisons guard. The honest answer to "
                      "ops' question — 'is there a weekly-cadence edge "
                      "on this universe?' — is **NO at this cost regime "
                      "and at this universe scale**. The MONTHLY SMOM "
                      "candidate from SMOM-3 remains the strongest "
                      "signal in this project.")
    lines.append("")
    lines.append("_Mined-data caveat: this is the 7th walk-forward on "
                  "this DB. All six strategies use literature defaults — "
                  "no project-specific tuning — so the marginal mining "
                  "cost per strategy is small, but the comparison "
                  "itself is the source of multiple-testing inflation. "
                  "The guard above is how we control for that. Even a "
                  "clean winner is paper-trade candidate, NEVER a deploy._")
    lines.append("")
    lines.append("_End of report._")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────


def main() -> int:
    t0 = time.time()
    print(f"[WEEKLY-SWEEP] loading MOMENTUM_UNIVERSE "
          f"({len(MOMENTUM_UNIVERSE)} candidates) ...")
    data = load_universe()
    print(f"[WEEKLY-SWEEP] {len(data)} symbols loaded "
          f"(load wall {time.time()-t0:.1f}s)")
    if not data:
        return 1
    print(f"[WEEKLY-SWEEP] BRUTAL costs: "
          f"slip={SMOM_BRUTAL_SLIPPAGE}, brok={SMOM_BROKERAGE}.")
    print(f"[WEEKLY-SWEEP] HEADLINE discount: "
          f"{SURVIVORSHIP_DISCOUNT:.0%}.")

    windows = (
        ("INSPECT", INSPECT_START, INSPECT_END),
        ("HELD-OUT", HOLDOUT_START, HOLDOUT_END),
    )

    results_by_strategy: dict[str, dict[str, dict]] = {}
    for name, cls in STRATEGIES:
        results_by_strategy[name] = {}
        for win_label, start, end in windows:
            print(f"[WEEKLY-SWEEP] {name} : {win_label} ...")
            t_phase = time.time()
            results_by_strategy[name][win_label] = _run(
                data, cls(), start, end, win_label)
            s = results_by_strategy[name][win_label]
            print(f"    {win_label} wall {time.time()-t_phase:.1f}s  "
                  f"PF raw={_fmt_pf(s['pf'])}  "
                  f"n={s['n_trades']}  "
                  f"|maxDD|={abs(s['mdd']) * 100:.2f}%  "
                  f"trades/yr={s['trades_per_year']:.1f}")

    # Apply multiple-comparisons guard.
    winners: list[tuple[str, dict, dict]] = []
    failures: dict[str, list[str]] = {}
    for name, _ in STRATEGIES:
        insp = results_by_strategy[name]["INSPECT"]
        ho = results_by_strategy[name]["HELD-OUT"]
        is_win, reasons = is_real_winner(insp, ho)
        if is_win:
            winners.append((name, insp, ho))
        else:
            failures[name] = reasons

    t_total = time.time() - t0
    print(f"[WEEKLY-SWEEP] total wall {t_total:.1f}s "
          f"({t_total / 60:.1f} min). Writing report ...")
    print(f"[WEEKLY-SWEEP] WINNERS = "
          f"{[w[0] for w in winners] or '(none)'}")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        build_report(results_by_strategy, winners, failures,
                      t_elapsed=t_total, n_symbols=len(data)),
        encoding="utf-8")
    print(f"[WEEKLY-SWEEP] report -> "
          f"{REPORT_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
