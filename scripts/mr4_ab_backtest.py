"""MR-4 — A/B backtest: MR-1 baseline vs MR-1 + portfolio DD cap.

Per the MR-4 ticket: ONE change is being evaluated (the harness-level
``dd_cap_pct`` parameter on ``run_replay``). The strategy itself is
NOT touched. Regime gate stays OFF in both arms (per the brief — it's
dead). All strategy parameters frozen at MR-1 values.

PRE-REGISTERED THRESHOLD
========================
``dd_cap_pct = 0.10`` — chosen on principle (standard 10% portfolio-
drawdown circuit-breaker), NOT fit to any historical result.

DESCRIPTIVE SENSITIVITY
=======================
``dd_cap_pct = 0.15`` and ``0.20`` are run only as a descriptive
sensitivity check (does the result drift smoothly with the threshold,
or is 0.10 a knife's-edge?). The GO/NO-GO VERDICT is the
pre-registered 0.10 row.

DATA SET
========
25 universe equities + ^NSEI — IDENTICAL to MR-3 so the comparison is
apples-to-apples. ^NSEI is never traded (strategy always skips '^'
symbols).
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
    binomial_p_value, bootstrap_pf_ci, concentration_flags, per_year_pnl,
    robustness,
)
from backtesting.replay import run_replay
from config import (
    DB_PATH, GATE_MAX_DRAWDOWN, GATE_PROFIT_FACTOR, GATE_SHARPE,
    GATE_WIN_RATE, INITIAL_CAPITAL, REGIME_INDEX,
)
from data.universe import POINT_IN_TIME_NSE25, SURVIVORSHIP_NOTE
from signals.mean_reversion import MeanReversionStrategy

REPORT_PATH = PROJECT_ROOT / "logs" / "mr4_ab_report.md"

VERDICT_CAP = 0.10         # pre-registered — the GO/NO-GO verdict
SENSITIVITY_CAPS = [0.15, 0.20]  # descriptive only

# Years MR-2 / MR-3 flagged.
BAD_YEARS = [2018, 2019, 2022]
COVID_YEAR = 2020


def load_data() -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    con = sqlite3.connect(f"file:{Path(DB_PATH).as_posix()}?mode=ro", uri=True)
    try:
        for sym in POINT_IN_TIME_NSE25 + [REGIME_INDEX]:
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


# ── Format helpers ─────────────────────────────────────────────────────


def _fmt_pf(pf) -> str:
    if pf is None or (isinstance(pf, float) and math.isnan(pf)):
        return "n/a"
    if pf == float("inf"):
        return "inf"
    return f"{pf:.3f}"


def _fmt(val, decimals=3, suffix=""):
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "n/a"
    return f"{val:.{decimals}f}{suffix}"


def summarise(result: dict, label: str) -> dict:
    m = result["metrics"]
    trades = result["trades"]
    n = int(m.get("n_trades", 0))
    wins = int((trades["pnl"] > 0).sum()) if not trades.empty else 0
    return {
        "label": label, "trades": trades,
        "pf": m.get("profit_factor", float("nan")),
        "sharpe": m.get("sharpe", float("nan")),
        "mdd": m.get("max_drawdown", float("nan")),
        "wr": m.get("win_rate", float("nan")),
        "cagr": m.get("cagr", float("nan")),
        "n_trades": n, "wins": wins,
        "n_days": result.get("n_days", 0),
    }


def gates_pass(s: dict) -> tuple[dict, int]:
    pf_pass = (s["pf"] == float("inf")) or (
        not math.isnan(s["pf"]) and s["pf"] > GATE_PROFIT_FACTOR)
    sharpe_pass = (not math.isnan(s["sharpe"]) and s["sharpe"] > GATE_SHARPE)
    mdd_pass = (not math.isnan(s["mdd"]) and abs(s["mdd"]) < GATE_MAX_DRAWDOWN)
    wr_pass = (not math.isnan(s["wr"]) and s["wr"] > GATE_WIN_RATE)
    return ({"pf": pf_pass, "sharpe": sharpe_pass,
             "mdd": mdd_pass, "wr": wr_pass},
            int(pf_pass) + int(sharpe_pass) + int(mdd_pass) + int(wr_pass))


def per_year_ab(trades_a: pd.DataFrame,
                  trades_b: pd.DataFrame) -> pd.DataFrame:
    a = per_year_pnl(trades_a).set_index("year") if not trades_a.empty else pd.DataFrame()
    b = per_year_pnl(trades_b).set_index("year") if not trades_b.empty else pd.DataFrame()
    years = sorted(set(a.index) | set(b.index))
    rows = []
    for yr in years:
        ra = a.loc[yr] if yr in a.index else None
        rb = b.loc[yr] if yr in b.index else None
        rows.append({
            "year": int(yr),
            "a_n":   int(ra["n_trades"])  if ra is not None else 0,
            "a_pf":  float(ra["pf"])      if ra is not None else float("nan"),
            "a_pnl": float(ra["total_pnl"]) if ra is not None else 0.0,
            "b_n":   int(rb["n_trades"])  if rb is not None else 0,
            "b_pf":  float(rb["pf"])      if rb is not None else float("nan"),
            "b_pnl": float(rb["total_pnl"]) if rb is not None else 0.0,
        })
    return pd.DataFrame(rows).sort_values("year")


# ── Verdict logic ──────────────────────────────────────────────────────


def verdict_paragraphs(s_base: dict, s_capped: dict, ab_df: pd.DataFrame,
                        cap_pct: float) -> list[str]:
    out: list[str] = []
    out.append(f"**Headline change (cap = {cap_pct:.0%}):** baseline PF "
                f"{_fmt_pf(s_base['pf'])} -> capped PF "
                f"{_fmt_pf(s_capped['pf'])}. Baseline n={s_base['n_trades']}, "
                f"capped n={s_capped['n_trades']}. |maxDD|: "
                f"{_fmt(abs(s_base['mdd']) if not math.isnan(s_base['mdd']) else float('nan'), 3)} "
                f"-> "
                f"{_fmt(abs(s_capped['mdd']) if not math.isnan(s_capped['mdd']) else float('nan'), 3)}.")
    out.append("")

    # Q1 — did the bad years bleed less?
    bad_improvements = []
    for yr in BAD_YEARS:
        row = ab_df.loc[ab_df["year"] == yr]
        if row.empty:
            continue
        r = row.iloc[0]
        if r["a_pnl"] < 0:
            improved_pnl = r["b_pnl"] > r["a_pnl"]
            bad_improvements.append({
                "year": yr,
                "a_pnl": float(r["a_pnl"]),
                "b_pnl": float(r["b_pnl"]),
                "a_n": int(r["a_n"]),
                "b_n": int(r["b_n"]),
                "improved": improved_pnl,
            })
    n_improved = sum(1 for x in bad_improvements if x["improved"])
    out.append(f"**Q1 — Did the bad years bleed LESS?** ({BAD_YEARS})")
    out.append("")
    if not bad_improvements:
        out.append("- No bad-year data to evaluate.")
    else:
        for x in bad_improvements:
            delta_pnl = x["b_pnl"] - x["a_pnl"]
            verdict = "bled less" if x["improved"] else "did NOT improve"
            out.append(f"- **{x['year']}**: baseline Rs "
                        f"{x['a_pnl']:+,.0f} on n={x['a_n']} -> capped Rs "
                        f"{x['b_pnl']:+,.0f} on n={x['b_n']} "
                        f"(Δ Rs {delta_pnl:+,.0f}) — **{verdict}**.")
    out.append("")
    out.append(f"**Summary:** {n_improved} of {len(bad_improvements)} "
                f"flagged bad years bled less.")
    out.append("")

    # Q2 — did the good years stay essentially intact?
    out.append("**Q2 — Did the good years stay essentially intact?**")
    out.append("")
    good_years = ab_df[ab_df["a_pnl"] > 0]
    if good_years.empty:
        out.append("- No baseline-positive years to evaluate.")
    else:
        rows = []
        for _, r in good_years.iterrows():
            delta = float(r["b_pnl"] - r["a_pnl"])
            pct_retained = (float(r["b_pnl"]) / float(r["a_pnl"])
                             if r["a_pnl"] else 0.0)
            rows.append({
                "year": int(r["year"]),
                "a_pnl": float(r["a_pnl"]),
                "b_pnl": float(r["b_pnl"]),
                "delta": delta,
                "retained": pct_retained,
            })
        for x in rows:
            verdict = ("intact" if x["retained"] >= 0.95
                        else ("mostly intact" if x["retained"] >= 0.80
                              else "hit"))
            out.append(f"- **{x['year']}**: baseline Rs "
                        f"{x['a_pnl']:+,.0f} -> capped Rs "
                        f"{x['b_pnl']:+,.0f} "
                        f"(retained {x['retained']:.1%}) — **{verdict}**.")
        n_intact = sum(1 for x in rows if x["retained"] >= 0.95)
        out.append("")
        out.append(f"**Summary:** {n_intact} of {len(rows)} good years "
                    f"essentially intact (≥95% retained).")
    out.append("")

    # Q3 — net effect
    out.append("**Q3 — Net effect: better, worse, or a wash?**")
    out.append("")
    base_total = float(s_base["trades"]["pnl"].sum()) if not s_base["trades"].empty else 0.0
    capped_total = float(s_capped["trades"]["pnl"].sum()) if not s_capped["trades"].empty else 0.0
    delta_total = capped_total - base_total
    pf_better = (not math.isnan(s_capped["pf"]) and not math.isnan(s_base["pf"])
                  and s_capped["pf"] > s_base["pf"])
    dd_better = (not math.isnan(s_capped["mdd"]) and not math.isnan(s_base["mdd"])
                  and abs(s_capped["mdd"]) < abs(s_base["mdd"]))
    out.append(f"- Total PnL: baseline Rs {base_total:+,.0f} -> capped "
                f"Rs {capped_total:+,.0f} (Δ Rs {delta_total:+,.0f}).")
    out.append(f"- PF: {_fmt_pf(s_base['pf'])} -> {_fmt_pf(s_capped['pf'])} "
                f"({'better' if pf_better else 'not better'}).")
    out.append(f"- |maxDD|: "
                f"{_fmt(abs(s_base['mdd']) if not math.isnan(s_base['mdd']) else float('nan'), 3)} -> "
                f"{_fmt(abs(s_capped['mdd']) if not math.isnan(s_capped['mdd']) else float('nan'), 3)} "
                f"({'better' if dd_better else 'not better'}).")
    out.append(f"- n_trades: {s_base['n_trades']} -> {s_capped['n_trades']} "
                f"({'fewer' if s_capped['n_trades'] < s_base['n_trades'] else 'same/more'}).")
    out.append("")
    # Synthesise
    capped_gates = gates_pass(s_capped)
    capped_n_pass = capped_gates[1]
    pf = s_capped["pf"]
    pf_clearly_above = (isinstance(pf, float) and not math.isnan(pf)
                         and pf != float("inf") and pf > GATE_PROFIT_FACTOR)
    if capped_n_pass == 4 and pf_clearly_above and dd_better:
        out.append(f"**Net verdict: the cap turned this into a real "
                    f"candidate.** All four gates clear, PF clearly above "
                    f"1.3, |maxDD| lower. Subject to the mined-data caveat "
                    f"below — still NOT a deploy, still a paper-trade "
                    f"candidate.")
    elif pf_better and dd_better:
        out.append(f"**Net verdict: cap improved both PF and |maxDD|** but "
                    f"the headline gates are not all clear "
                    f"({capped_n_pass} of 4). The cap is a keeper for the "
                    f"harness; the underlying MR edge is still uncertain.")
    elif not pf_better and dd_better:
        out.append(f"**Net verdict: cap trimmed |maxDD| but did NOT improve "
                    f"PF.** The cap is doing its risk-control job but does "
                    f"not by itself create edge. Keep the cap in the harness "
                    f"as good risk infra; don't claim the MR edge is real "
                    f"on this evidence.")
    elif pf_better and not dd_better:
        out.append(f"**Net verdict: PF improved but |maxDD| did not.** "
                    f"Unusual — the cap should reduce DD by construction. "
                    f"Worth inspecting the trade tape before trusting this "
                    f"as a result.")
    else:
        out.append(f"**Net verdict: cap did not help.** The DD cap is a "
                    f"keeper for the harness anyway (good risk infra for "
                    f"any future strategy), but it did not improve MR's "
                    f"headline.")
    return out


# ── Markdown helpers ───────────────────────────────────────────────────


def _gate_row(name, observed, op, threshold, passed):
    mark = "PASS" if passed else "FAIL"
    return f"| {name} | {observed} | {op} | {threshold} | {mark} |"


def headline_block(s: dict) -> list[str]:
    out: list[str] = []
    out.append(f"- **Profit Factor:** {_fmt_pf(s['pf'])}")
    out.append(f"- **Sharpe ratio:** {_fmt(s['sharpe'])}")
    if not math.isnan(s["mdd"]):
        out.append(f"- **Max drawdown:** {_fmt(abs(s['mdd']), 4)} "
                    f"(= {_fmt(abs(s['mdd']) * 100, 2)}%)")
    else:
        out.append("- Max drawdown: n/a")
    out.append(f"- **Win rate:** {_fmt(s['wr'])} "
                f"({s['wins']} of {s['n_trades']})")
    out.append(f"- **CAGR:** {_fmt(s['cagr'])}")
    out.append(f"- **n_trades:** {s['n_trades']}")
    return out


def gate_block(s: dict) -> list[str]:
    verdicts, n_pass = gates_pass(s)
    out: list[str] = []
    out.append("| Gate | Observed | Required | Threshold | Result |")
    out.append("|---|---|---|---:|:---:|")
    out.append(_gate_row("Profit Factor", _fmt_pf(s["pf"]), ">",
                          GATE_PROFIT_FACTOR, verdicts["pf"]))
    out.append(_gate_row("Sharpe ratio", _fmt(s["sharpe"]), ">",
                          GATE_SHARPE, verdicts["sharpe"]))
    if math.isnan(s["mdd"]):
        out.append("| Max drawdown (mag) | n/a | < | "
                    f"{GATE_MAX_DRAWDOWN} | FAIL |")
    else:
        out.append(_gate_row("Max drawdown (mag)", _fmt(abs(s["mdd"]), 4),
                              "<", GATE_MAX_DRAWDOWN, verdicts["mdd"]))
    out.append(_gate_row("Win rate", _fmt(s["wr"]), ">",
                          GATE_WIN_RATE, verdicts["wr"]))
    out.append("")
    out.append(f"**{n_pass} of 4 gates cleared.**")
    return out


def robustness_block(s: dict) -> list[str]:
    r = robustness(s["trades"])
    f = concentration_flags(s["trades"])
    out: list[str] = []
    out.append("| Question | Value |")
    out.append("|---|---:|")
    out.append(f"| Raw PF | {_fmt_pf(r.pf_raw)} |")
    if r.top_symbol:
        out.append(f"| PF with top-contributing symbol removed "
                    f"({r.top_symbol}, Rs {r.top_symbol_pnl:+,.0f}) | "
                    f"{_fmt_pf(r.pf_ex_top_symbol)} |")
    else:
        out.append("| PF with top-contributing symbol removed | n/a |")
    if r.best_year:
        out.append(f"| PF with best year removed ({r.best_year}, Rs "
                    f"{r.best_year_pnl:+,.0f}) | "
                    f"{_fmt_pf(r.pf_ex_best_year)} |")
    else:
        out.append("| PF with best year removed | n/a |")
    out.append(f"| # symbols with net-negative PnL | "
                f"{r.n_negative_symbols} |")
    out.append(f"| Top-symbol share of gross-positive PnL | "
                f"{_fmt(f['top_symbol_share'] * 100, 1, '%')} |")
    out.append(f"| Top-year share of gross-positive PnL | "
                f"{_fmt(f['top_year_share'] * 100, 1, '%')} |")
    out.append("")
    if f["one_symbol_carries"]:
        out.append(f"⚠️ **Concentration flag:** one symbol "
                    f"({r.top_symbol}) carries "
                    f"{f['top_symbol_share']*100:.1f}% of gross positive PnL.")
    if f["one_year_carries"]:
        out.append(f"⚠️ **Concentration flag:** one year "
                    f"({r.best_year}) carries "
                    f"{f['top_year_share']*100:.1f}% of gross positive PnL.")
    if f["one_symbol_carries"] or f["one_year_carries"]:
        out.append("")
    return out


def significance_block(s: dict) -> list[str]:
    out: list[str] = []
    wins = s["wins"]; n = s["n_trades"]
    p_val = binomial_p_value(wins, n) if n > 0 else 1.0
    bs = bootstrap_pf_ci(s["trades"])
    out.append(f"- Observed: {wins} wins in {n} trades "
                f"(win rate {_fmt(s['wr'])}).")
    out.append(f"- Binomial P(X ≥ {wins} | n={n}, p=0.5) = "
                f"**{p_val:.4f}**.")
    if n < 30:
        out.append(f"- ⚠️ n_trades = {n} < 30 — claims at this sample "
                    f"size are weak per LAW 8.")
    elif p_val < 0.05:
        out.append("- p < 0.05 — win rate significantly above chance.")
    elif p_val < 0.10:
        out.append("- p < 0.10 — marginally above chance.")
    else:
        out.append("- p ≥ 0.10 — NOT statistically distinguishable from chance.")
    out.append(f"- Bootstrap PF 5/50/95%: {_fmt_pf(bs['p05'])} / "
                f"{_fmt_pf(bs['p50'])} / {_fmt_pf(bs['p95'])}.")
    if (not math.isnan(bs["p05"]) and not math.isnan(bs["p95"])):
        if bs["p05"] < 1.0 < bs["p95"]:
            out.append("- 90% CI spans 1.0 — bootstrap cannot rule out "
                        "break-even.")
        elif bs["p05"] >= 1.0:
            out.append("- 5th percentile PF ≥ 1.0 — pessimistic tail "
                        "still positive.")
        else:
            out.append("- 95th percentile PF < 1.0 — optimistic tail "
                        "still negative.")
    return out


# ── Compose ────────────────────────────────────────────────────────────


def build_report(s_base: dict,
                   s_verdict: dict, ab_verdict: pd.DataFrame,
                   sensitivity_results: dict) -> str:
    lines: list[str] = []
    lines.append("# MR-4 — Portfolio DD-Cap A/B Report")
    lines.append("")
    lines.append("**Branch:** `feature/mr4-dd-cap`")
    lines.append(f"**Single change vs MR-1 baseline:** `dd_cap_pct=0.10` "
                  f"on `run_replay`. Strategy code untouched. Regime gate "
                  f"OFF in both arms (it's dead — see MR-3 report).")
    lines.append(f"**Pre-registered threshold:** "
                  f"`dd_cap_pct = {VERDICT_CAP:.2f}` (chosen on principle "
                  f"as a standard 10% portfolio-drawdown circuit-breaker, "
                  f"NOT fit to results). 0.15 and 0.20 reported as "
                  f"descriptive sensitivity ONLY.")
    lines.append(f"**Replay data:** 25 universe equities + ^NSEI "
                  f"(identical to MR-3 for clean comparison; ^NSEI "
                  f"never traded by the strategy's '^'-skip).")
    lines.append(f"**Initial capital:** Rs {INITIAL_CAPITAL:,.0f}")
    lines.append("")

    # 1. Per-year baseline vs verdict-cap
    lines.append(f"## 1. Per-year A/B (baseline vs DD-cap@{VERDICT_CAP:.0%})")
    lines.append("")
    lines.append("Per-year is the honest walk-forward because the rules "
                  "are parameter-free.")
    lines.append("")
    if ab_verdict.empty:
        lines.append("_No trades._")
    else:
        lines.append("| Year | Base n | Base PF | Base PnL (Rs) | "
                      "Cap n | Cap PF | Cap PnL (Rs) | Δ n | Δ PnL (Rs) |")
        lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for _, r in ab_verdict.iterrows():
            delta_n = int(r["b_n"] - r["a_n"])
            delta_pnl = float(r["b_pnl"] - r["a_pnl"])
            lines.append(
                f"| {int(r['year'])} | {int(r['a_n'])} | "
                f"{_fmt_pf(r['a_pf'])} | {r['a_pnl']:+,.0f} | "
                f"{int(r['b_n'])} | {_fmt_pf(r['b_pf'])} | "
                f"{r['b_pnl']:+,.0f} | {delta_n:+d} | "
                f"{delta_pnl:+,.0f} |")
    lines.append("")

    # 2. The three brief questions
    lines.append("## 2. The three key questions (plain-English)")
    lines.append("")
    lines.extend(verdict_paragraphs(s_base, s_verdict, ab_verdict,
                                       VERDICT_CAP))
    lines.append("")

    # 3. Capped variant headline + gates
    lines.append(f"## 3. Capped variant — full-cycle headline ({VERDICT_CAP:.0%})")
    lines.append("")
    lines.extend(headline_block(s_verdict))
    lines.append("")
    lines.extend(gate_block(s_verdict))
    lines.append("")

    # 4. Robustness on capped
    lines.append(f"## 4. Robustness suite (capped@{VERDICT_CAP:.0%})")
    lines.append("")
    lines.extend(robustness_block(s_verdict))

    # 5. Significance
    lines.append(f"## 5. Significance (capped@{VERDICT_CAP:.0%})")
    lines.append("")
    lines.extend(significance_block(s_verdict))
    lines.append("")

    # 6. Sensitivity (descriptive only)
    lines.append("## 6. Sensitivity (descriptive ONLY — 0.15 / 0.20)")
    lines.append("")
    lines.append("These rows are NOT the verdict. They exist so a reader "
                  "can tell whether the 0.10 result is on a smooth curve "
                  "or sitting on a knife's edge.")
    lines.append("")
    lines.append("| dd_cap_pct | PF | Sharpe | \\|maxDD\\| | Win rate | n_trades |")
    lines.append("|---:|---:|---:|---:|---:|---:|")
    lines.append(f"| 0.00 (baseline) | {_fmt_pf(s_base['pf'])} | "
                  f"{_fmt(s_base['sharpe'])} | "
                  f"{_fmt(abs(s_base['mdd']) if not math.isnan(s_base['mdd']) else float('nan'), 4)} | "
                  f"{_fmt(s_base['wr'])} | {s_base['n_trades']} |")
    lines.append(f"| **{VERDICT_CAP:.2f} (verdict)** | "
                  f"**{_fmt_pf(s_verdict['pf'])}** | "
                  f"**{_fmt(s_verdict['sharpe'])}** | "
                  f"**{_fmt(abs(s_verdict['mdd']) if not math.isnan(s_verdict['mdd']) else float('nan'), 4)}** | "
                  f"**{_fmt(s_verdict['wr'])}** | "
                  f"**{s_verdict['n_trades']}** |")
    for cap in SENSITIVITY_CAPS:
        s = sensitivity_results[cap]
        lines.append(f"| {cap:.2f} | {_fmt_pf(s['pf'])} | "
                      f"{_fmt(s['sharpe'])} | "
                      f"{_fmt(abs(s['mdd']) if not math.isnan(s['mdd']) else float('nan'), 4)} | "
                      f"{_fmt(s['wr'])} | {s['n_trades']} |")
    lines.append("")

    # 7. Survivorship
    pf_v = s_verdict["pf"]
    discount = 0.15
    pf_disc = (pf_v * (1.0 - discount) if (isinstance(pf_v, float)
                and not math.isnan(pf_v) and pf_v != float("inf")) else pf_v)
    lines.append("## 7. Survivorship caveat — capped raw vs discounted")
    lines.append("")
    lines.append(f"> {SURVIVORSHIP_NOTE}")
    lines.append("")
    lines.append("| Quantity | Capped@10% value |")
    lines.append("|---|---:|")
    lines.append(f"| Raw PF | {_fmt_pf(pf_v)} |")
    lines.append(f"| Survivorship-discounted PF (× {1-discount:.2f}) | "
                  f"{_fmt_pf(pf_disc)} |")
    lines.append("")
    lines.append(f"Same {discount:.0%} discount T3/MR-2/MR-3 used. Live PF "
                  f"runs 30-50% below backtest on top of this.")
    lines.append("")

    # 8. Mined-data caveat
    lines.append("## 8. Mined-data caveat (MANDATORY)")
    lines.append("")
    lines.append("The historical data has now been mined **four times** "
                  "(T3 breakout, MR-2 baseline + held-out split, MR-3 "
                  "regime gate, this MR-4 DD cap). The MR-2 held-out "
                  "window (2023-2026) is BURNED. Even a clean MR-4 result "
                  "is a CANDIDATE for forward paper-trading, not a "
                  "deploy. The one honest test left is live paper-trading "
                  "on bars that don't yet exist in `market_data.db`.")
    lines.append("")
    lines.append("Per LAW 3: minimum 30 trades OR 4 weeks live paper "
                  "before any real capital. Per the bootstrap doc: live "
                  "safe-deploy bar is PF ≥ 1.5.")
    lines.append("")
    lines.append("Note: the DD cap is a KEEPER for the harness regardless "
                  "of MR's verdict — it's general-purpose risk infra that "
                  "every future strategy benefits from.")
    lines.append("")
    lines.append("_End of report._")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────


def main() -> int:
    print(f"[MR-4] loading 25 equities + {REGIME_INDEX}...")
    data = load_data()
    print(f"[MR-4] {len(data)} symbols loaded.")

    print(f"[MR-4] running BASELINE replay (no cap)...")
    r_base = run_replay(data, MeanReversionStrategy(use_regime_gate=False),
                          initial_capital=INITIAL_CAPITAL)
    s_base = summarise(r_base, "BASELINE")
    print(f"  baseline: PF={_fmt_pf(s_base['pf'])}, "
          f"n={s_base['n_trades']}, "
          f"|maxDD|={_fmt(abs(s_base['mdd']) if not math.isnan(s_base['mdd']) else float('nan'), 3)}")

    print(f"[MR-4] running VERDICT replay (dd_cap_pct={VERDICT_CAP:.2f}) ...")
    r_verdict = run_replay(data, MeanReversionStrategy(use_regime_gate=False),
                             initial_capital=INITIAL_CAPITAL,
                             dd_cap_pct=VERDICT_CAP)
    s_verdict = summarise(r_verdict, f"CAP@{VERDICT_CAP:.2f}")
    print(f"  capped@{VERDICT_CAP:.2f}: PF={_fmt_pf(s_verdict['pf'])}, "
          f"n={s_verdict['n_trades']}, "
          f"|maxDD|={_fmt(abs(s_verdict['mdd']) if not math.isnan(s_verdict['mdd']) else float('nan'), 3)}")

    sensitivity_results: dict[float, dict] = {}
    for cap in SENSITIVITY_CAPS:
        print(f"[MR-4] running SENSITIVITY replay (dd_cap_pct={cap:.2f}) ...")
        r = run_replay(data, MeanReversionStrategy(use_regime_gate=False),
                        initial_capital=INITIAL_CAPITAL,
                        dd_cap_pct=cap)
        s = summarise(r, f"CAP@{cap:.2f}")
        sensitivity_results[cap] = s
        print(f"  capped@{cap:.2f}: PF={_fmt_pf(s['pf'])}, "
              f"n={s['n_trades']}, "
              f"|maxDD|={_fmt(abs(s['mdd']) if not math.isnan(s['mdd']) else float('nan'), 3)}")

    ab_verdict = per_year_ab(s_base["trades"], s_verdict["trades"])

    print(f"[MR-4] writing report -> {REPORT_PATH.relative_to(PROJECT_ROOT)}")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        build_report(s_base, s_verdict, ab_verdict, sensitivity_results),
        encoding="utf-8")
    print(f"[MR-4] done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
