"""SMID-WEEKLY — 3-way A/B: MONTHLY vs WEEKLY vs TRANCHED-4 rebalance
cadence, all on SMID + low-vol tilt with the SMOM-3 harness.

DECISIVE QUESTION
=================
Does ops get a WEEKLY trading rhythm without giving up the SMID edge,
once the extra costs are honestly counted?

THREE CONFIGS (only the rebalance cadence varies)
=================================================
    MONTHLY      SmidMomentumStrategy(rebalance_freq='monthly')
    WEEKLY       SmidMomentumStrategy(rebalance_freq='weekly')
    TRANCHED-4   TranchedSmidMomentumStrategy(n_tranches=4)
                  -> 4 sleeves, one rotates per ISO week,
                     per-sleeve top_n = top_n // 4 = 3,
                     each NAME's turnover stays ~monthly.

EVERYTHING ELSE matches SMOM-3 exactly:
    slippage_pct = 0.004 (BRUTAL 40 bps, small-cap impact)
    brokerage_pct = config default
    max_positions = 15, max_per_sector = 5, max_heat = 0.20
    INSPECT 2016-01-04 -> 2022-12-30
    HELD-OUT 2023-01-02 -> 2026-06-03
    Survivorship discount = 45% (HEADLINE)
    Weekday-filtered DB load

WHAT THE REPORT MUST ANSWER (plainly, per ops)
==============================================
    * Does WEEKLY still clear disc-PF > 1.3 in BOTH windows after the
      extra costs?
    * Does TRANCHED give a weekly rhythm while staying ~as good as
      monthly?
    * How much did costs rise (Rs paid to slippage + brokerage) and
      did the edge survive it?
    * Per-year PF for each config.
    * Robustness suite + significance for the surviving held-out configs.

MINED-DATA caveat
=================
SIXTH walk-forward on this DB (MR-2, MOM-3, MOM-4, MOM-5, SMOM-3,
SMID-WEEKLY). Cadence is a NEW knob being tested on data that has
already informed five prior verdicts. The pre-registered constants
(`n_tranches=4`, ISO-week boundary, monthly baseline) all come from
calendar / Antonacci-style conventions, not from tuning — so the
incremental mining cost is bounded. But it's not zero. Even a clean
weekly result is a paper-trade candidate, not a deploy.
"""
from __future__ import annotations

import math
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
from data.universe import SMID_SURVIVORSHIP_NOTE, SMID_UNIVERSE
from scripts.mom3_run_backtest import (
    HOLDOUT_END, HOLDOUT_START, INSPECT_END, INSPECT_START,
    MOM_MAX_HEAT, MOM_MAX_PER_SECTOR, MOM_MAX_POSITIONS,
    MOMENTUM_CRASH_WINDOWS, _fmt, _fmt_pf, crash_window_dd,
    deepest_drawdown,
)
from scripts.smom3_run_backtest import (
    SMOM_BRUTAL_SLIPPAGE, SMOM_BROKERAGE,
    SURVIVORSHIP_DISCOUNT_HEADLINE, _discount_pf, load_smid_universe,
)
from signals.smid_momentum import (
    SmidMomentumStrategy, TranchedSmidMomentumStrategy,
)

REPORT_PATH = PROJECT_ROOT / "logs" / "smid_weekly_ab_report.md"


# ── Cost-drag + turnover helpers ───────────────────────────────────────


def estimate_cost_drag(trades: pd.DataFrame, *,
                        slippage_pct: float,
                        brokerage_pct: float) -> dict:
    """Approximate the Rs cost drag from slippage + brokerage on each
    round-trip in the trade tape. The harness records the post-cost
    fill prices (entry_price already includes +slip, exit_price
    already includes -slip), so we approximate:

      slippage_per_trade   ≈ shares * (entry_price + exit_price) * slip
      brokerage_per_trade  ≈ shares * (entry_price + exit_price) * brok

    The approximation is exact to first order in {slip, brok} and is
    used only for COMPARISON across configs (all three configs use the
    same slip + brok, so any systematic error cancels in the delta).
    """
    if trades.empty:
        return {"slippage_rs": 0.0, "brokerage_rs": 0.0,
                "total_cost_rs": 0.0, "gross_turnover_rs": 0.0}
    gross = float(((trades["entry_price"] + trades["exit_price"])
                    * trades["shares"]).sum())
    slip = gross * slippage_pct
    brok = gross * brokerage_pct
    return {
        "slippage_rs": slip,
        "brokerage_rs": brok,
        "total_cost_rs": slip + brok,
        "gross_turnover_rs": gross,
    }


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
    cost = estimate_cost_drag(trades, slippage_pct=SMOM_BRUTAL_SLIPPAGE,
                                brokerage_pct=SMOM_BROKERAGE)
    return {
        "label": label, "pf": pf, "sharpe": sharpe, "mdd": mdd, "wr": wr,
        "cagr": cagr, "n_trades": n, "wins": wins, "p_value": p_val,
        "bs": bs, "robust": robust, "flags": flags, "cost": cost,
        "trades": trades, "equity": equity,
        "deepest_dd": deepest, "crash_dds": crash_dds,
        "n_days": result.get("n_days", 0),
        "start": result.get("start"), "end": result.get("end"),
    }


def _run_window(data: dict, *, strategy, start, end, label: str) -> dict:
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
    return summarise_window(label, res)


# ── Render ────────────────────────────────────────────────────────────


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


def three_way_window_block(window_label: str, by_config: dict) -> list[str]:
    """Side-by-side 3-config table for one window."""
    lines: list[str] = []
    lines.append(f"### {window_label}")
    lines.append("")
    monthly = by_config["MONTHLY"]
    if monthly["start"] is not None:
        lines.append(f"_{monthly['start'].date()} -> "
                      f"{monthly['end'].date()}, "
                      f"{monthly['n_days']} trading days._")
        lines.append("")

    configs = ["MONTHLY", "WEEKLY", "TRANCHED-4"]
    gate_passes = {cfg: sum(_gate_marks(by_config[cfg]).values())
                    for cfg in configs}
    pf_disc = {cfg: _discount_pf(by_config[cfg]["pf"],
                                    SURVIVORSHIP_DISCOUNT_HEADLINE)
                for cfg in configs}

    lines.append("| Metric | MONTHLY | WEEKLY | TRANCHED-4 |")
    lines.append("|---|---:|---:|---:|")
    lines.append("| ★ PF disc-45% (HEADLINE) | "
                  + " | ".join(
                      f"{_fmt_pf(pf_disc[c])} "
                      f"{_mark(_gate_marks(by_config[c])['pf'])}"
                      for c in configs) + " |")
    lines.append("| PF (raw) | "
                  + " | ".join(_fmt_pf(by_config[c]["pf"])
                                for c in configs) + " |")
    lines.append("| Sharpe | "
                  + " | ".join(f"{_fmt(by_config[c]['sharpe'])} "
                                f"{_mark(_gate_marks(by_config[c])['sharpe'])}"
                                for c in configs) + " |")
    lines.append("| |max DD| | "
                  + " | ".join(
                      f"{_fmt(abs(by_config[c]['mdd']) * 100 if not math.isnan(by_config[c]['mdd']) else float('nan'), 2, '%')} "
                      f"{_mark(_gate_marks(by_config[c])['mdd'])}"
                      for c in configs) + " |")
    lines.append("| Win rate | "
                  + " | ".join(f"{_fmt(by_config[c]['wr'])} "
                                f"{_mark(_gate_marks(by_config[c])['wr'])}"
                                for c in configs) + " |")
    lines.append("| CAGR | "
                  + " | ".join(_fmt(by_config[c]["cagr"])
                                for c in configs) + " |")
    lines.append("| n_trades | "
                  + " | ".join(str(by_config[c]["n_trades"])
                                for c in configs) + " |")
    lines.append("| **Gates cleared (disc 45%)** | "
                  + " | ".join(f"**{gate_passes[c]} of 4**"
                                for c in configs) + " |")
    lines.append("")

    # Cost-drag block
    lines.append("**Cost drag (Rs paid to slippage + brokerage; "
                  "approximation):**")
    lines.append("")
    lines.append("| Cost component | MONTHLY | WEEKLY | TRANCHED-4 |")
    lines.append("|---|---:|---:|---:|")
    lines.append("| Slippage paid | "
                  + " | ".join(f"Rs {by_config[c]['cost']['slippage_rs']:,.0f}"
                                for c in configs) + " |")
    lines.append("| Brokerage paid | "
                  + " | ".join(f"Rs {by_config[c]['cost']['brokerage_rs']:,.0f}"
                                for c in configs) + " |")
    lines.append("| **Total cost drag** | "
                  + " | ".join(
                      f"**Rs {by_config[c]['cost']['total_cost_rs']:,.0f}**"
                      for c in configs) + " |")
    lines.append("| Gross turnover (entry+exit value) | "
                  + " | ".join(
                      f"Rs {by_config[c]['cost']['gross_turnover_rs']:,.0f}"
                      for c in configs) + " |")
    monthly_cost = by_config["MONTHLY"]["cost"]["total_cost_rs"]
    if monthly_cost > 0:
        lines.append("| Cost vs MONTHLY (multiple) | 1.00x | "
                      + f"{by_config['WEEKLY']['cost']['total_cost_rs'] / monthly_cost:.2f}x | "
                      + f"{by_config['TRANCHED-4']['cost']['total_cost_rs'] / monthly_cost:.2f}x |")
    lines.append("")

    # Per-year
    lines.append("**Per-year PF:**")
    lines.append("")
    yr_by_cfg = {cfg: per_year_pnl(by_config[cfg]["trades"])
                  for cfg in configs}
    years = set()
    for cfg in configs:
        df = yr_by_cfg[cfg]
        if not df.empty:
            years |= set(df["year"].tolist())
    years = sorted(years)
    if years:
        lines.append("| Year | MONTHLY PF | MONTHLY n | "
                      "WEEKLY PF | WEEKLY n | "
                      "TRANCHED-4 PF | TRANCHED-4 n |")
        lines.append("|---:|---:|---:|---:|---:|---:|---:|")
        for yr in years:
            row = [f"| {yr}"]
            for cfg in configs:
                df = yr_by_cfg[cfg]
                if not df.empty and (df["year"] == yr).any():
                    r = df.loc[df["year"] == yr].iloc[0]
                    row.append(f"{_fmt_pf(r['pf'])}")
                    row.append(f"{int(r['n_trades'])}")
                else:
                    row.append("n/a")
                    row.append("0")
            lines.append(" | ".join(row) + " |")
        lines.append("")
    # Crash windows
    lines.append("**Momentum-crash DDs:**")
    lines.append("")
    lines.append("| Crash window | MONTHLY DD | WEEKLY DD | TRANCHED-4 DD |")
    lines.append("|---|---:|---:|---:|")
    n_windows = len(by_config["MONTHLY"]["crash_dds"])
    for idx in range(n_windows):
        rows = [by_config[c]["crash_dds"][idx] for c in configs]
        if not any(r["in_range"] for r in rows):
            continue
        label = rows[0]["label"]
        cells = []
        for r in rows:
            if r["in_range"]:
                cells.append(f"{r['magnitude'] * 100:.2f}%")
            else:
                cells.append("_out_")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines.append("")
    return lines


def robustness_block(s: dict, config_label: str) -> list[str]:
    """Robustness suite for one config × window."""
    r = s["robust"]
    f = s["flags"]
    lines: list[str] = []
    lines.append(f"**Robustness ({config_label}, held-out):**")
    lines.append("")
    lines.append("| Question | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Raw PF | {_fmt_pf(r.pf_raw)} |")
    if r.top_symbol:
        lines.append(
            f"| PF with top symbol removed "
            f"({r.top_symbol}, Rs {r.top_symbol_pnl:+,.0f}) | "
            f"{_fmt_pf(r.pf_ex_top_symbol)} |")
    if r.best_year:
        lines.append(
            f"| PF with best year removed ({r.best_year}, "
            f"Rs {r.best_year_pnl:+,.0f}) | "
            f"{_fmt_pf(r.pf_ex_best_year)} |")
    n_traded = len(per_symbol_pnl(s["trades"]))
    lines.append(f"| # symbols net-negative | "
                  f"{r.n_negative_symbols} of {n_traded} |")
    lines.append(f"| Top-symbol share of gross PnL | "
                  f"{_fmt(f['top_symbol_share'] * 100, 1, '%')} |")
    lines.append(f"| Top-year share of gross PnL | "
                  f"{_fmt(f['top_year_share'] * 100, 1, '%')} |")
    lines.append("")
    lines.append(f"**Significance ({config_label}, held-out):**")
    lines.append("")
    lines.append(f"- Binomial p (n={s['n_trades']}, wins={s['wins']}): "
                  f"**{s['p_value']:.4f}**")
    lines.append(f"- Bootstrap PF CI 5/50/95: "
                  f"{_fmt_pf(s['bs']['p05'])} / "
                  f"{_fmt_pf(s['bs']['p50'])} / "
                  f"{_fmt_pf(s['bs']['p95'])}")
    lines.append("")
    return lines


# ── Verdict ───────────────────────────────────────────────────────────


def headline_verdict(by_config_ho: dict) -> list[str]:
    out: list[str] = []
    out.append("### THE HEADLINE QUESTIONS (HELD-OUT, 45%-discounted, "
                "brutal 40-bps costs)")
    out.append("")
    configs = ["MONTHLY", "WEEKLY", "TRANCHED-4"]
    for cfg in configs:
        s = by_config_ho[cfg]
        pf_disc = _discount_pf(s["pf"], SURVIVORSHIP_DISCOUNT_HEADLINE)
        mdd_pct = (abs(s["mdd"]) * 100
                    if not math.isnan(s["mdd"]) else float("nan"))
        out.append(f"- **{cfg:11}**: PF disc-45% = "
                    f"{_fmt_pf(pf_disc)}  |  |max DD| = "
                    f"{mdd_pct:.2f}%  |  Sharpe = "
                    f"{_fmt(s['sharpe'])}  |  n = {s['n_trades']}  |  "
                    f"cost = Rs {s['cost']['total_cost_rs']:,.0f}")
    out.append("")

    # Q1: Does WEEKLY clear disc-PF > 1.3?
    weekly_pf_disc = _discount_pf(by_config_ho["WEEKLY"]["pf"],
                                     SURVIVORSHIP_DISCOUNT_HEADLINE)
    weekly_pf_ok = (isinstance(weekly_pf_disc, float)
                     and not math.isnan(weekly_pf_disc)
                     and weekly_pf_disc > GATE_PROFIT_FACTOR)
    if weekly_pf_ok:
        out.append(f"- **WEEKLY** clears the {GATE_PROFIT_FACTOR} disc-PF "
                    f"gate (= {_fmt_pf(weekly_pf_disc)}).")
    else:
        out.append(f"- **WEEKLY** FAILS the {GATE_PROFIT_FACTOR} disc-PF "
                    f"gate (= {_fmt_pf(weekly_pf_disc)}). The extra "
                    f"weekly costs ate into the edge.")

    # Q2: Does TRANCHED-4 give weekly rhythm while ≈ monthly quality?
    tr_pf_disc = _discount_pf(by_config_ho["TRANCHED-4"]["pf"],
                                 SURVIVORSHIP_DISCOUNT_HEADLINE)
    mo_pf_disc = _discount_pf(by_config_ho["MONTHLY"]["pf"],
                                 SURVIVORSHIP_DISCOUNT_HEADLINE)
    tr_ok = (isinstance(tr_pf_disc, float)
              and not math.isnan(tr_pf_disc)
              and tr_pf_disc > GATE_PROFIT_FACTOR)
    if tr_ok and isinstance(mo_pf_disc, float):
        delta = tr_pf_disc - mo_pf_disc
        if delta >= -0.1:
            out.append(f"- **TRANCHED-4** gives the weekly rhythm and "
                        f"stays competitive: disc-PF {_fmt_pf(tr_pf_disc)} "
                        f"vs MONTHLY {_fmt_pf(mo_pf_disc)} (delta "
                        f"{delta:+.3f}).")
        else:
            out.append(f"- **TRANCHED-4** weekly rhythm cost some edge: "
                        f"disc-PF {_fmt_pf(tr_pf_disc)} vs MONTHLY "
                        f"{_fmt_pf(mo_pf_disc)} (delta {delta:+.3f}).")
    elif not tr_ok:
        out.append(f"- **TRANCHED-4** FAILS the disc-PF gate "
                    f"(= {_fmt_pf(tr_pf_disc)}).")

    # Q3: Cost ratio
    mo_cost = by_config_ho["MONTHLY"]["cost"]["total_cost_rs"]
    wk_cost = by_config_ho["WEEKLY"]["cost"]["total_cost_rs"]
    tr_cost = by_config_ho["TRANCHED-4"]["cost"]["total_cost_rs"]
    if mo_cost > 0:
        wk_mult = wk_cost / mo_cost
        tr_mult = tr_cost / mo_cost
        out.append(f"- Costs vs MONTHLY: WEEKLY = {wk_mult:.2f}x, "
                    f"TRANCHED-4 = {tr_mult:.2f}x. (A naive 'weekly = 4x "
                    f"monthly' assumption would be too pessimistic IF the "
                    f"ranks are sticky week-to-week.)")
    out.append("")

    # Plain-English verdict
    weekly_dd_pct = (abs(by_config_ho["WEEKLY"]["mdd"]) * 100
                     if not math.isnan(by_config_ho["WEEKLY"]["mdd"])
                     else float("nan"))
    tr_dd_pct = (abs(by_config_ho["TRANCHED-4"]["mdd"]) * 100
                  if not math.isnan(by_config_ho["TRANCHED-4"]["mdd"])
                  else float("nan"))

    if weekly_pf_ok and tr_ok:
        out.append("**Verdict: ops CAN have a weekly cadence with the "
                    "edge intact.** Both WEEKLY and TRANCHED-4 clear the "
                    "disc-PF gate at brutal costs. Per the standing rule "
                    "this is a paper-trade candidate, not a deploy. "
                    "Pick TRANCHED-4 if ops wants the rhythm WITHOUT the "
                    "full weekly turnover (sleeves keep per-name "
                    "turnover monthly); pick WEEKLY if ops accepts the "
                    "higher cost drag for true 1-week reactivity.")
    elif tr_ok and not weekly_pf_ok:
        out.append("**Verdict: TRANCHED-4 is the right way to get a "
                    "weekly rhythm.** Full WEEKLY rotation eats too much "
                    "edge through costs, but TRANCHED-4 preserves "
                    "monthly-like per-name turnover behind a weekly "
                    "rhythm and clears the gate.")
    elif weekly_pf_ok and not tr_ok:
        out.append("**Verdict: full WEEKLY is the right weekly cadence.** "
                    "TRANCHED-4 introduced sleeve overhead without "
                    "preserving the edge here; WEEKLY survives the "
                    "extra cost drag.")
    else:
        out.append("**Verdict: monthly remains the price of the edge.** "
                    "Neither WEEKLY nor TRANCHED-4 clears the disc-PF "
                    "gate after the brutal costs. Ops can have a "
                    "weekly RHYTHM only by accepting an edge "
                    "degradation that takes us back below the deploy "
                    "bar — not worth it.")
    out.append("")

    out.append("_Mined-data caveat: this is the 6th walk-forward on "
                "this DB. The cadence knob is constrained to "
                "monthly / weekly / tranched-4 by calendar / Antonacci "
                "convention (not tuned), so the marginal mining cost is "
                "bounded — but it is not zero. Small-cap 45% haircut may "
                "still be optimistic. Even a clean weekly result is "
                "paper-trade candidate, NEVER a deploy._")
    return out


# ── Compose ────────────────────────────────────────────────────────────


def build_report(by_window_by_config: dict, *,
                  t_elapsed: float, n_symbols: int) -> str:
    lines: list[str] = []
    lines.append("# SMID-WEEKLY -- Cadence A/B "
                  "(MONTHLY vs WEEKLY vs TRANCHED-4)")
    lines.append("")
    lines.append("**Branch:** `feature/smid-weekly`")
    lines.append("**Universe:** `SMID_UNIVERSE` "
                  f"({n_symbols} of {len(SMID_UNIVERSE)} symbols loaded).")
    lines.append("**Strategy core:** SmidMomentumStrategy (low-vol tilt + "
                  "liquidity floor); only the rebalance cadence varies.")
    lines.append(f"**Initial capital:** Rs {INITIAL_CAPITAL:,.0f}")
    lines.append(f"**Wall-clock:** {t_elapsed:.1f}s.")
    lines.append("")

    # Pre-registered
    lines.append("## 0. Pre-registered parameters (NOT tuned)")
    lines.append("")
    lines.append("| Param | Value |")
    lines.append("|---|---:|")
    lines.append(f"| slippage_pct | {SMOM_BRUTAL_SLIPPAGE} (40 bps, "
                  f"BRUTAL) |")
    lines.append(f"| brokerage_pct | {SMOM_BROKERAGE} |")
    lines.append(f"| max_positions | {MOM_MAX_POSITIONS} |")
    lines.append(f"| max_per_sector | {MOM_MAX_PER_SECTOR} |")
    lines.append(f"| max_heat | {MOM_MAX_HEAT:.2f} |")
    lines.append(f"| ★ SURVIVORSHIP discount (HEADLINE) | "
                  f"{SURVIVORSHIP_DISCOUNT_HEADLINE:.0%} (small-cap) |")
    lines.append("| MONTHLY = first trading day each calendar month "
                  "(SMOM-3 baseline) | |")
    lines.append("| WEEKLY = first trading day of each ISO week | |")
    lines.append("| TRANCHED-4 = 4 sleeves; ISO-week % 4 picks the active "
                  "sleeve | |")
    lines.append("")

    # HEADLINE
    lines.append("## 1. HELD-OUT verdict (the primary read)")
    lines.append("")
    lines.extend(headline_verdict(by_window_by_config["HELD-OUT"]))
    lines.append("")
    lines.append("### Side-by-side -- held-out")
    lines.append("")
    lines.extend(three_way_window_block("HELD-OUT",
                                           by_window_by_config["HELD-OUT"]))

    # Robustness on each config (held-out only)
    lines.append("## 2. Robustness + significance (held-out, per config)")
    lines.append("")
    for cfg in ("MONTHLY", "WEEKLY", "TRANCHED-4"):
        lines.extend(robustness_block(
            by_window_by_config["HELD-OUT"][cfg], cfg))

    # INSPECT
    lines.append("## 3. INSPECT (descriptive)")
    lines.append("")
    lines.extend(three_way_window_block("INSPECT",
                                           by_window_by_config["INSPECT"]))

    # Survivorship
    lines.append("## 4. Survivorship caveat (LOUD)")
    lines.append("")
    lines.append(f"> {SMID_SURVIVORSHIP_NOTE}")
    lines.append("")
    lines.append("Headline discount = **45%** (small-cap). Discount "
                  "table:")
    lines.append("")
    lines.append("| Window | Cadence | PF raw | PF disc-45% (HEADLINE) |")
    lines.append("|---|---|---:|---:|")
    for win in ("INSPECT", "HELD-OUT"):
        for cfg in ("MONTHLY", "WEEKLY", "TRANCHED-4"):
            s = by_window_by_config[win][cfg]
            lines.append(f"| {win} | {cfg} | {_fmt_pf(s['pf'])} | "
                          f"**{_fmt_pf(_discount_pf(s['pf'], 0.45))}** |")
    lines.append("")

    lines.append("_End of report._")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────


def main() -> int:
    t0 = time.time()
    print(f"[SMID-WEEKLY] loading SMID_UNIVERSE ({len(SMID_UNIVERSE)} "
          f"candidates) ...")
    data = load_smid_universe()
    print(f"[SMID-WEEKLY] {len(data)} symbols loaded "
          f"(load wall {time.time()-t0:.1f}s)")
    if not data:
        return 1

    print(f"[SMID-WEEKLY] BRUTAL costs slippage={SMOM_BRUTAL_SLIPPAGE}, "
          f"brokerage={SMOM_BROKERAGE}; "
          f"discount={SURVIVORSHIP_DISCOUNT_HEADLINE:.0%}.")

    windows = (
        ("INSPECT", INSPECT_START, INSPECT_END),
        ("HELD-OUT", HOLDOUT_START, HOLDOUT_END),
    )

    by_window: dict[str, dict[str, dict]] = {}

    def _factory(cfg: str):
        if cfg == "MONTHLY":
            return SmidMomentumStrategy(rebalance_freq="monthly")
        if cfg == "WEEKLY":
            return SmidMomentumStrategy(rebalance_freq="weekly")
        if cfg == "TRANCHED-4":
            return TranchedSmidMomentumStrategy()
        raise ValueError(cfg)

    for win_label, start, end in windows:
        by_window[win_label] = {}
        for cfg in ("MONTHLY", "WEEKLY", "TRANCHED-4"):
            print(f"[SMID-WEEKLY] {win_label}: {cfg} ...")
            t_phase = time.time()
            by_window[win_label][cfg] = _run_window(
                data, strategy=_factory(cfg),
                start=start, end=end, label=win_label)
            s = by_window[win_label][cfg]
            print(f"    {cfg} wall {time.time()-t_phase:.1f}s  "
                  f"PF raw={_fmt_pf(s['pf'])}  "
                  f"n={s['n_trades']}  "
                  f"|maxDD|={abs(s['mdd']) * 100:.2f}%  "
                  f"cost=Rs {s['cost']['total_cost_rs']:,.0f}")

    t_total = time.time() - t0
    print(f"[SMID-WEEKLY] total wall {t_total:.1f}s "
          f"({t_total / 60:.1f} min). Writing report ...")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        build_report(by_window, t_elapsed=t_total, n_symbols=len(data)),
        encoding="utf-8")
    print(f"[SMID-WEEKLY] report -> "
          f"{REPORT_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
