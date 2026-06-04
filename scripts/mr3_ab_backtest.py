"""MR-3 — A/B backtest of MeanReversionStrategy: baseline vs regime-gated.

Per the MR-3 ticket: ONE change is being evaluated (the
``use_regime_gate=True`` toggle, which blocks new entries unless
^NSEI close > its REGIME_MA-day MA). All other parameters are frozen
at the MR-1 baseline — no per-year fitting, no calibration.

DATA SET
========
25 universe equities + ^NSEI. The gated variant needs ^NSEI in the
data dict for the regime read; the ^-symbol skip in
``signals.mean_reversion.MeanReversionStrategy`` ensures the
strategy NEVER trades it (regardless of toggle setting).

OUTPUTS
=======
Single report at ``logs/mr3_ab_report.md`` containing:
  * Per-year PF / n_trades / win-rate / total-PnL SIDE BY SIDE for
    baseline and gated, with Δ columns.
  * The three key questions answered plainly:
      - Did 2018 / 2019 / 2022 improve?
      - Did 2020 get hurt? (COVID-bounce wins happened during a
        NIFTY < 50-DMA period, so the gate may suppress them.)
      - Net full-cycle effect: better, worse, or a wash?
  * Full-cycle gates verdict for the gated variant (PF > 1.3,
    Sharpe > 1.0, |maxDD| < 15%, win > 45%).
  * Robustness suite (backtesting/diagnostics) on the gated result.
  * The MANDATORY mined-data caveat — historical data has now been
    looked at across breakout + MR + this regime gate; even a strong
    MR-3 result is a CANDIDATE for forward paper-trade, not a deploy.

The script touches no strategy or harness code.
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
    profit_factor, robustness,
)
from backtesting.replay import run_replay
from config import (
    DB_PATH, GATE_MAX_DRAWDOWN, GATE_PROFIT_FACTOR, GATE_SHARPE,
    GATE_WIN_RATE, INITIAL_CAPITAL, REGIME_INDEX,
)
from data.universe import POINT_IN_TIME_NSE25, SURVIVORSHIP_NOTE
from signals.mean_reversion import MeanReversionStrategy

REPORT_PATH = PROJECT_ROOT / "logs" / "mr3_ab_report.md"

# Years the baseline MR-2 report flagged as the failure mode (per the
# MR-3 brief). The A/B section explicitly checks whether the gate
# fixes these years without breaking the COVID-bounce year (2020).
BAD_YEARS_BASELINE = [2018, 2019, 2022]
COVID_YEAR = 2020


# ── Data loading (25 equities + ^NSEI) ─────────────────────────────────


def load_equities_plus_nsei() -> dict[str, pd.DataFrame]:
    """Read daily bars for ``POINT_IN_TIME_NSE25`` PLUS ``REGIME_INDEX``
    (``^NSEI``). The gated variant requires the regime input; the
    ^-symbol skip in the strategy keeps it from being traded."""
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


# ── Render helpers ─────────────────────────────────────────────────────


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


def _delta(a: float, b: float, decimals=3) -> str:
    """Format ``b - a`` with sign. Treat NaN / inf gracefully."""
    if (isinstance(a, float) and (math.isnan(a) or math.isinf(a))) or \
       (isinstance(b, float) and (math.isnan(b) or math.isinf(b))):
        return "—"
    d = b - a
    sign = "+" if d > 0 else ("" if d == 0 else "")
    return f"{sign}{d:.{decimals}f}"


# ── Per-year side-by-side ──────────────────────────────────────────────


def per_year_ab(trades_base: pd.DataFrame,
                  trades_gated: pd.DataFrame) -> pd.DataFrame:
    """Return one row per year with baseline + gated columns plus deltas."""
    base = per_year_pnl(trades_base).set_index("year") if not trades_base.empty else pd.DataFrame()
    gated = per_year_pnl(trades_gated).set_index("year") if not trades_gated.empty else pd.DataFrame()
    years = sorted(set(base.index) | set(gated.index))
    rows = []
    for yr in years:
        b = base.loc[yr] if yr in base.index else None
        g = gated.loc[yr] if yr in gated.index else None
        rows.append({
            "year": int(yr),
            "base_n":   int(b["n_trades"])   if b is not None else 0,
            "base_pf":  float(b["pf"])       if b is not None else float("nan"),
            "base_wr":  float(b["win_rate"]) if b is not None else float("nan"),
            "base_pnl": float(b["total_pnl"]) if b is not None else 0.0,
            "gated_n":   int(g["n_trades"])   if g is not None else 0,
            "gated_pf":  float(g["pf"])       if g is not None else float("nan"),
            "gated_wr":  float(g["win_rate"]) if g is not None else float("nan"),
            "gated_pnl": float(g["total_pnl"]) if g is not None else 0.0,
        })
    return pd.DataFrame(rows).sort_values("year")


# ── Headline summary ──────────────────────────────────────────────────


def summarise(result: dict, label: str) -> dict:
    metrics = result["metrics"]
    trades = result["trades"]
    pf = metrics.get("profit_factor", float("nan"))
    sharpe = metrics.get("sharpe", float("nan"))
    mdd = metrics.get("max_drawdown", float("nan"))
    wr = metrics.get("win_rate", float("nan"))
    n = int(metrics.get("n_trades", 0))
    wins = int((trades["pnl"] > 0).sum()) if not trades.empty else 0
    return {
        "label": label, "trades": trades,
        "pf": pf, "sharpe": sharpe, "mdd": mdd, "wr": wr,
        "n_trades": n, "wins": wins,
        "cagr": metrics.get("cagr", float("nan")),
        "equity_curve": result["equity_curve"],
        "n_days": result.get("n_days", 0),
        "start": result.get("start"), "end": result.get("end"),
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


def _gate_row(name, observed, op, threshold, passed):
    mark = "PASS" if passed else "FAIL"
    return f"| {name} | {observed} | {op} | {threshold} | {mark} |"


# ── Verdict logic ──────────────────────────────────────────────────────


def verdict_paragraphs(s_base: dict, s_gated: dict,
                         ab_df: pd.DataFrame) -> list[str]:
    """The plain-English honest verdict — was the gate worth it?"""
    out: list[str] = []
    out.append(f"**Headline change:** baseline full-cycle PF "
                f"{_fmt_pf(s_base['pf'])}, gated PF "
                f"{_fmt_pf(s_gated['pf'])}. Baseline n={s_base['n_trades']}, "
                f"gated n={s_gated['n_trades']}. Baseline |maxDD| "
                f"{_fmt(abs(s_base['mdd']) if not math.isnan(s_base['mdd']) else float('nan'), 3)}, "
                f"gated |maxDD| "
                f"{_fmt(abs(s_gated['mdd']) if not math.isnan(s_gated['mdd']) else float('nan'), 3)}.")
    out.append("")

    # Per-year improvement check
    bad_year_improvements = []
    for yr in BAD_YEARS_BASELINE:
        row = ab_df.loc[ab_df["year"] == yr]
        if row.empty:
            continue
        r = row.iloc[0]
        # An "improvement" = base_pnl < 0 AND (gated_pnl > base_pnl OR gated_n < base_n)
        if r["base_pnl"] < 0:
            improved_pnl = r["gated_pnl"] > r["base_pnl"]
            fewer_trades = r["gated_n"] < r["base_n"]
            bad_year_improvements.append({
                "year": yr,
                "base_pnl": float(r["base_pnl"]),
                "gated_pnl": float(r["gated_pnl"]),
                "base_n": int(r["base_n"]),
                "gated_n": int(r["gated_n"]),
                "improved": improved_pnl or fewer_trades,
            })

    n_improved = sum(1 for x in bad_year_improvements if x["improved"])
    out.append(f"**Q1 — Did the bad years improve?** ({BAD_YEARS_BASELINE})")
    out.append("")
    if not bad_year_improvements:
        out.append("- No bad-year data to evaluate.")
    else:
        for x in bad_year_improvements:
            pnl_delta = x["gated_pnl"] - x["base_pnl"]
            n_delta = x["gated_n"] - x["base_n"]
            verdict = ("improved" if x["improved"] else "did NOT improve")
            out.append(f"- **{x['year']}**: baseline PnL Rs "
                        f"{x['base_pnl']:+,.0f} on n={x['base_n']} → "
                        f"gated Rs {x['gated_pnl']:+,.0f} on n={x['gated_n']} "
                        f"(ΔPnL Rs {pnl_delta:+,.0f}, Δn {n_delta:+d}) — "
                        f"**{verdict}**.")
    out.append("")
    out.append(f"**Summary:** {n_improved} of {len(bad_year_improvements)} "
                f"flagged bad years improved under the gate.")
    out.append("")

    # COVID 2020 check
    covid_row = ab_df.loc[ab_df["year"] == COVID_YEAR]
    out.append(f"**Q2 — Did 2020 (the COVID-bounce year) get hurt?**")
    out.append("")
    if covid_row.empty:
        out.append("- No 2020 data to evaluate.")
    else:
        r = covid_row.iloc[0]
        pnl_delta = r["gated_pnl"] - r["base_pnl"]
        n_delta = int(r["gated_n"] - r["base_n"])
        hurt = (r["gated_pnl"] < r["base_pnl"]
                and r["base_pnl"] > 0)
        if r["base_pnl"] <= 0:
            out.append(f"- 2020 was NOT a winner for the baseline "
                        f"(PnL Rs {r['base_pnl']:+,.0f}); gated is Rs "
                        f"{r['gated_pnl']:+,.0f} (Δ Rs {pnl_delta:+,.0f}). "
                        f"The hypothesised tradeoff doesn't apply because "
                        f"the baseline didn't have COVID-bounce wins to "
                        f"lose in the first place.")
        elif hurt:
            out.append(f"- **Yes** — 2020 baseline made Rs "
                        f"{r['base_pnl']:+,.0f} on n={int(r['base_n'])}, "
                        f"gated made Rs {r['gated_pnl']:+,.0f} on n="
                        f"{int(r['gated_n'])} (ΔPnL Rs {pnl_delta:+,.0f}, "
                        f"Δn {n_delta:+d}). The gate blocked the COVID-"
                        f"bounce wins, as the brief warned.")
        else:
            out.append(f"- **No** — 2020 baseline Rs "
                        f"{r['base_pnl']:+,.0f}, gated Rs "
                        f"{r['gated_pnl']:+,.0f} (Δ Rs {pnl_delta:+,.0f}). "
                        f"The gate did not significantly cost the "
                        f"COVID-bounce year.")
    out.append("")

    # Q3 — net effect
    out.append("**Q3 — Net effect: better, worse, or a wash?**")
    out.append("")
    base_total = float(s_base["trades"]["pnl"].sum()) if not s_base["trades"].empty else 0.0
    gated_total = float(s_gated["trades"]["pnl"].sum()) if not s_gated["trades"].empty else 0.0
    pnl_delta_total = gated_total - base_total
    pf_better = (not math.isnan(s_gated["pf"]) and not math.isnan(s_base["pf"])
                  and s_gated["pf"] > s_base["pf"])
    dd_better = (not math.isnan(s_gated["mdd"]) and not math.isnan(s_base["mdd"])
                  and abs(s_gated["mdd"]) < abs(s_base["mdd"]))
    out.append(f"- Total PnL: baseline Rs {base_total:+,.0f}, gated "
                f"Rs {gated_total:+,.0f} (Δ Rs {pnl_delta_total:+,.0f}).")
    out.append(f"- PF: {_fmt_pf(s_base['pf'])} → {_fmt_pf(s_gated['pf'])} "
                f"({'better' if pf_better else 'not better'}).")
    out.append(f"- |maxDD|: "
                f"{_fmt(abs(s_base['mdd']) if not math.isnan(s_base['mdd']) else float('nan'), 3)} → "
                f"{_fmt(abs(s_gated['mdd']) if not math.isnan(s_gated['mdd']) else float('nan'), 3)} "
                f"({'better' if dd_better else 'not better'}).")
    out.append(f"- n_trades: {s_base['n_trades']} → "
                f"{s_gated['n_trades']} "
                f"({'fewer' if s_gated['n_trades'] < s_base['n_trades'] else 'more or same'}).")
    out.append("")

    # Synthesise
    if pf_better and dd_better:
        out.append("**Net verdict: the gate improved both PF AND max-DD on the "
                    "full cycle.** Worth carrying as a regime-aware baseline "
                    "BUT still subject to the mined-data caveat below.")
    elif pf_better and not dd_better:
        out.append("**Net verdict: PF improved but max-DD did not.** Partial "
                    "win — the gate suppresses LOSING trades on average but "
                    "doesn't reduce the worst drawdown. Open question: is "
                    "there a single bad sequence the gate doesn't catch?")
    elif not pf_better and dd_better:
        out.append("**Net verdict: max-DD improved but PF did not.** The gate "
                    "trades good-year gains for bad-year safety. Whether "
                    "that's a net win depends on the user's risk preference; "
                    "this report does NOT call it a clear improvement.")
    else:
        out.append("**Net verdict: the gate did not improve the headline.** "
                    "It blocks trades but the trade-off (lost wins ≥ avoided "
                    "losses) is not favourable. Do NOT carry this variant.")
    return out


# ── Compose ────────────────────────────────────────────────────────────


def build_report(s_base: dict, s_gated: dict, ab_df: pd.DataFrame) -> str:
    base_verdicts, base_n_pass = gates_pass(s_base)
    gated_verdicts, gated_n_pass = gates_pass(s_gated)
    gated_robust = robustness(s_gated["trades"])
    gated_flags = concentration_flags(s_gated["trades"])
    wins_g = s_gated["wins"]; n_g = s_gated["n_trades"]
    p_val_g = binomial_p_value(wins_g, n_g) if n_g > 0 else 1.0
    bs_g = bootstrap_pf_ci(s_gated["trades"])

    lines: list[str] = []
    lines.append("# MR-3 — Regime-Gated Mean-Reversion A/B Report")
    lines.append("")
    lines.append("**Branch:** `feature/mr3-regime-gate`")
    lines.append("**Single change vs MR-1 baseline:** `use_regime_gate=True` — "
                  "block new entries unless ^NSEI close > its 50-DMA. All "
                  "other parameters frozen at MR-1 values. No per-year "
                  "tuning, no calibration.")
    lines.append("**Replay data:** 25 universe equities + ^NSEI. "
                  "MeanReversionStrategy skips '^'-prefixed symbols from "
                  "trading regardless of the toggle, so ^NSEI is read for "
                  "the regime check but never traded.")
    lines.append(f"**Initial capital:** Rs {INITIAL_CAPITAL:,.0f}")
    lines.append("")

    # ── 1. Per-year side-by-side ───────────────────────────────────────
    lines.append("## 1. Per-year side-by-side (the decisive view)")
    lines.append("")
    lines.append("Each year is its own honest walk-forward because the "
                  "rules are parameter-free — no parameter was fit on any "
                  "year to make another year look better.")
    lines.append("")
    if ab_df.empty:
        lines.append("_No trades to break down._")
    else:
        lines.append("| Year | Base n | Base PF | Base PnL (Rs) | Gated n | Gated PF | Gated PnL (Rs) | Δ n | Δ PnL (Rs) |")
        lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for _, r in ab_df.iterrows():
            yr = int(r["year"])
            delta_n = int(r["gated_n"] - r["base_n"])
            delta_pnl = float(r["gated_pnl"] - r["base_pnl"])
            lines.append(
                f"| {yr} | {int(r['base_n'])} | {_fmt_pf(r['base_pf'])} | "
                f"{r['base_pnl']:+,.0f} | {int(r['gated_n'])} | "
                f"{_fmt_pf(r['gated_pf'])} | {r['gated_pnl']:+,.0f} | "
                f"{delta_n:+d} | {delta_pnl:+,.0f} |")
    lines.append("")

    # ── 2. The three key questions ─────────────────────────────────────
    lines.append("## 2. The three key questions (plain-English)")
    lines.append("")
    lines.extend(verdict_paragraphs(s_base, s_gated, ab_df))
    lines.append("")

    # ── 3. Gated full-cycle gates verdict ──────────────────────────────
    lines.append("## 3. Gated variant — full-cycle gate verdict")
    lines.append("")
    lines.append(f"- **Profit Factor:** {_fmt_pf(s_gated['pf'])}")
    lines.append(f"- **Sharpe ratio:** {_fmt(s_gated['sharpe'])}")
    if not math.isnan(s_gated["mdd"]):
        lines.append(f"- **Max drawdown:** {_fmt(abs(s_gated['mdd']), 4)} "
                      f"(= {_fmt(abs(s_gated['mdd']) * 100, 2)}%)")
    else:
        lines.append("- Max drawdown: n/a")
    lines.append(f"- **Win rate:** {_fmt(s_gated['wr'])} "
                  f"({wins_g} of {n_g})")
    lines.append(f"- **CAGR:** {_fmt(s_gated['cagr'])}")
    lines.append(f"- **n_trades:** {n_g}")
    lines.append("")
    lines.append("| Gate | Observed | Required | Threshold | Result |")
    lines.append("|---|---|---|---:|:---:|")
    lines.append(_gate_row("Profit Factor", _fmt_pf(s_gated["pf"]), ">",
                            GATE_PROFIT_FACTOR, gated_verdicts["pf"]))
    lines.append(_gate_row("Sharpe ratio", _fmt(s_gated["sharpe"]), ">",
                            GATE_SHARPE, gated_verdicts["sharpe"]))
    if math.isnan(s_gated["mdd"]):
        lines.append("| Max drawdown (mag) | n/a | < | "
                      f"{GATE_MAX_DRAWDOWN} | FAIL |")
    else:
        lines.append(_gate_row("Max drawdown (mag)",
                                _fmt(abs(s_gated["mdd"]), 4),
                                "<", GATE_MAX_DRAWDOWN, gated_verdicts["mdd"]))
    lines.append(_gate_row("Win rate", _fmt(s_gated["wr"]), ">",
                            GATE_WIN_RATE, gated_verdicts["wr"]))
    lines.append("")
    lines.append(f"**{gated_n_pass} of 4 gates cleared** on the gated "
                  f"full-cycle replay.")
    lines.append("")

    # ── 4. Robustness on gated ─────────────────────────────────────────
    lines.append("## 4. Robustness suite (gated variant)")
    lines.append("")
    lines.append("| Question | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Raw PF | {_fmt_pf(gated_robust.pf_raw)} |")
    if gated_robust.top_symbol:
        lines.append(f"| PF with top-contributing symbol removed "
                      f"({gated_robust.top_symbol}, Rs "
                      f"{gated_robust.top_symbol_pnl:+,.0f}) | "
                      f"{_fmt_pf(gated_robust.pf_ex_top_symbol)} |")
    else:
        lines.append("| PF with top-contributing symbol removed | n/a "
                      "(no positive-PnL symbol) |")
    if gated_robust.best_year:
        lines.append(f"| PF with best year removed "
                      f"({gated_robust.best_year}, Rs "
                      f"{gated_robust.best_year_pnl:+,.0f}) | "
                      f"{_fmt_pf(gated_robust.pf_ex_best_year)} |")
    else:
        lines.append("| PF with best year removed | n/a |")
    lines.append(f"| # symbols with net-negative PnL | "
                  f"{gated_robust.n_negative_symbols} |")
    lines.append(f"| Top-symbol share of gross-positive PnL | "
                  f"{_fmt(gated_flags['top_symbol_share'] * 100, 1, '%')} |")
    lines.append(f"| Top-year share of gross-positive PnL | "
                  f"{_fmt(gated_flags['top_year_share'] * 100, 1, '%')} |")
    lines.append("")
    if gated_flags["one_symbol_carries"]:
        lines.append(f"⚠️ **Concentration flag:** one symbol "
                      f"({gated_robust.top_symbol}) carries "
                      f"{gated_flags['top_symbol_share']*100:.1f}% of gross "
                      f"positive PnL — the gated edge depends heavily on "
                      f"this contributor.")
    if gated_flags["one_year_carries"]:
        lines.append(f"⚠️ **Concentration flag:** one year "
                      f"({gated_robust.best_year}) carries "
                      f"{gated_flags['top_year_share']*100:.1f}% of gross "
                      f"positive PnL.")
    if gated_flags["one_symbol_carries"] or gated_flags["one_year_carries"]:
        lines.append("")

    # ── 5. Significance ────────────────────────────────────────────────
    lines.append("## 5. Significance (gated variant)")
    lines.append("")
    lines.append(f"**Binomial test** (null: no edge → win rate 50%)")
    lines.append("")
    lines.append(f"- Observed: {wins_g} wins in {n_g} trades.")
    lines.append(f"- P(X ≥ {wins_g} | n={n_g}, p=0.5) = "
                  f"**{p_val_g:.4f}**")
    if n_g < 30:
        lines.append(f"- ⚠️ **n_trades = {n_g} < 30** — per LAW 8, the "
                      f"gated sample is too small to claim a real edge.")
    elif p_val_g < 0.05:
        lines.append("- p < 0.05 — win rate significantly above chance.")
    elif p_val_g < 0.10:
        lines.append("- p < 0.10 — marginally above chance.")
    else:
        lines.append("- p ≥ 0.10 — NOT statistically distinguishable from "
                      "chance.")
    lines.append("")
    lines.append(f"**Bootstrap CI on PF** ({bs_g['n_resamples']} resamples)")
    lines.append("")
    lines.append(f"- 5th / 50th / 95th percentile: "
                  f"{_fmt_pf(bs_g['p05'])} / {_fmt_pf(bs_g['p50'])} / "
                  f"{_fmt_pf(bs_g['p95'])}")
    if (not math.isnan(bs_g["p05"]) and not math.isnan(bs_g["p95"])):
        if bs_g["p05"] < 1.0 < bs_g["p95"]:
            lines.append("- 90% CI **spans 1.0** — bootstrap cannot rule "
                          "out break-even.")
        elif bs_g["p05"] >= 1.0:
            lines.append("- 5th percentile PF ≥ 1.0 — pessimistic tail "
                          "still positive.")
        else:
            lines.append("- 95th percentile PF < 1.0 — optimistic tail "
                          "still negative.")
    lines.append("")

    # ── 6. Survivorship ────────────────────────────────────────────────
    lines.append("## 6. Survivorship caveat — gated raw vs discounted")
    lines.append("")
    lines.append("From `data/universe.py`:")
    lines.append("")
    lines.append(f"> {SURVIVORSHIP_NOTE}")
    lines.append("")
    pf_g = s_gated["pf"]
    discount = 0.15
    pf_g_disc = (pf_g * (1.0 - discount) if (isinstance(pf_g, float)
                  and not math.isnan(pf_g) and pf_g != float("inf")) else pf_g)
    lines.append("| Quantity | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Raw gated PF | {_fmt_pf(pf_g)} |")
    lines.append(f"| Survivorship-discounted gated PF "
                  f"(× {1 - discount:.2f}) | {_fmt_pf(pf_g_disc)} |")
    lines.append("")
    lines.append(f"Same {discount:.0%} discount T3/MR-2 used; rationale "
                  f"there. Live PF typically runs 30-50% below backtest "
                  f"on top of this, so the practical live expectation is "
                  f"roughly the discounted number × 0.55-0.75.")
    lines.append("")

    # ── 7. MINED-DATA CAVEAT ───────────────────────────────────────────
    lines.append("## 7. Mined-data caveat (MANDATORY)")
    lines.append("")
    lines.append("This historical data has now been looked at across "
                  "**three lenses**: T3 (breakout), MR-2 (mean-reversion "
                  "baseline + held-out split), and now MR-3 (the "
                  "regime gate). Each look mines the same underlying "
                  "price history. Even if THIS report's headline looks "
                  "good, that's not a deploy signal — it's a CANDIDATE "
                  "to be paper-traded forward on fresh data.")
    lines.append("")
    lines.append("Concretely, the MR-2 held-out window (2023-2026) is "
                  "now BURNED — it's been used to test the MR-1 baseline "
                  "and (because MR-3 reads the full cycle) the gated "
                  "variant too. The only honest forward test from this "
                  "point is **live paper-trading on bars that don't yet "
                  "exist in `market_data.db`**.")
    lines.append("")
    lines.append("Per LAW 3: minimum **30 trades OR 4 weeks**, whichever "
                  "is longer, before any real capital. Per the bootstrap "
                  "doc: aim for held-out PF ≥ 1.5 as the safe-deploy bar.")
    lines.append("")

    lines.append("_End of report._")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────


def main() -> int:
    print(f"[MR-3] loading {len(POINT_IN_TIME_NSE25)} equities + "
          f"{REGIME_INDEX}...")
    data = load_equities_plus_nsei()
    print(f"[MR-3] {len(data)} symbols loaded.")

    print(f"[MR-3] running BASELINE replay (use_regime_gate=False)...")
    r_base = run_replay(data, MeanReversionStrategy(use_regime_gate=False),
                          initial_capital=INITIAL_CAPITAL)
    s_base = summarise(r_base, "BASELINE")
    print(f"  baseline: PF={_fmt_pf(s_base['pf'])}, "
          f"n_trades={s_base['n_trades']}, "
          f"|maxDD|={_fmt(abs(s_base['mdd']) if not math.isnan(s_base['mdd']) else float('nan'), 3)}")

    print(f"[MR-3] running GATED replay (use_regime_gate=True)...")
    r_gated = run_replay(data, MeanReversionStrategy(use_regime_gate=True),
                           initial_capital=INITIAL_CAPITAL)
    s_gated = summarise(r_gated, "GATED")
    print(f"  gated: PF={_fmt_pf(s_gated['pf'])}, "
          f"n_trades={s_gated['n_trades']}, "
          f"|maxDD|={_fmt(abs(s_gated['mdd']) if not math.isnan(s_gated['mdd']) else float('nan'), 3)}")

    print(f"[MR-3] building A/B per-year table...")
    ab_df = per_year_ab(s_base["trades"], s_gated["trades"])

    print(f"[MR-3] writing report -> {REPORT_PATH.relative_to(PROJECT_ROOT)}")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(build_report(s_base, s_gated, ab_df),
                             encoding="utf-8")
    print(f"[MR-3] done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
