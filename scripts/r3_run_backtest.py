"""T3 — first honest backtest: replay BreakoutStrategy on the full window.

Per the T3 ticket: load daily bars for the 25 universe equities + ^NSEI
(NO ^INDIAVIX — the strategy would try to trade it), run the REAL
``backtesting.replay.run_replay`` over the full 2016-2026 window with
``signals.breakout.BreakoutStrategy``, and produce a 5-min-readable
report at ``logs/r3_backtest_report.md``.

CONSTRAINTS
===========
This script READS and REPORTS. It does not modify any strategy, harness,
or validator code (LAW 4: one change at a time — calibration changes,
if any are needed, are a proposed T4 follow-up listed in the report).

CONTENT OF THE REPORT (per the brief)
=====================================
1. Headline metrics (PF, Sharpe, max DD, win rate, CAGR, n_trades).
2. Gate verdicts (PF > 1.3, Sharpe > 1.0, max DD < 15%, win rate > 45%).
3. FILTER FUNNEL — independently computed: how many raw Donchian breaks
   occurred, then volume-survivors, then regime-survivors, then RR-
   survivors, then actual trades. This is the over-filtering diagnostic
   T2 flagged.
4. Trade-tape sanity — per-symbol PF, per-year PF, binomial p-value,
   bootstrap CI on PF, and an explicit n_trades >= 30 flag (LAW 8).
5. Survivorship caveat — raw PF AND survivorship-discounted PF, with
   the discount rate stated and justified.
6. Plain-English verdict.

The funnel is computed in a SEPARATE pass over the same data the
strategy sees, replicating the four entry checks exactly. This avoids
modifying the strategy to expose counters. Both passes import the same
config constants and the same ``compute_atr`` so the funnel can't drift
from the strategy's actual gates.
"""
from __future__ import annotations

import math
import sqlite3
import sys
from pathlib import Path
from typing import TypedDict

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backtesting.replay import run_replay
from config import (
    ATR_PERIOD, ATR_SL_MULTIPLIER, BREAKOUT_LOOKBACK, DB_PATH,
    GATE_MAX_DRAWDOWN, GATE_PROFIT_FACTOR, GATE_SHARPE, GATE_WIN_RATE,
    INITIAL_CAPITAL, MIN_RR, REGIME_INDEX, REGIME_MA, VOLUME_AVG_WINDOW,
    VOLUME_MULT,
)
from data.universe import POINT_IN_TIME_NSE25, SURVIVORSHIP_NOTE
from features.engineer import compute_atr
from signals.breakout import BreakoutStrategy

REPORT_PATH = PROJECT_ROOT / "logs" / "r3_backtest_report.md"

# Survivorship discount — conservative middle ground because:
#   * universe DOES retain prominent fallen-from-NIFTY names
#     (YESBANK/VEDL/LUPIN/GAIL) so the worst survivorship pattern
#     (today's winners only) is partially corrected,
#   * BUT it excludes fully-delisted tickers (e.g. Bharti Infratel,
#     merged 2020) whose returns are entirely missing,
#   * AND it is fixed-membership, not a true point-in-time rotation.
# Empirical literature on Indian equity backtest haircuts is sparse;
# US-equity guidance from Carhart/Elton-Gruber-style studies of dead-
# fund bias puts the inflation of long-only equity strategy PFs at
# ~10-30%. Reporting 15% as the "central" discount with raw also stated.
SURVIVORSHIP_DISCOUNT = 0.15

BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 13


# ── Data loading ────────────────────────────────────────────────────────


class LoadResult(TypedDict):
    data: dict           # {symbol: DataFrame}
    summary: list        # rows: (symbol, n_bars, first_date, last_date)


def load_data(symbols: list[str], regime_index: str) -> LoadResult:
    """Read daily bars from the DB for the strategy universe + the regime
    index. Returns the canonical {symbol: DataFrame} dict the harness
    expects, plus a small summary table for the report.
    """
    syms = list(symbols) + [regime_index]
    out: dict[str, pd.DataFrame] = {}
    summary: list[tuple] = []
    con = sqlite3.connect(f"file:{Path(DB_PATH).as_posix()}?mode=ro", uri=True)
    try:
        for sym in syms:
            df = pd.read_sql_query(
                "SELECT time, open, high, low, close, volume "
                "FROM ohlcv WHERE symbol = ? AND resolution = '1d' "
                "ORDER BY time ASC",
                con, params=[sym], parse_dates=["time"])
            if df.empty:
                summary.append((sym, 0, None, None))
                continue
            df["time"] = pd.to_datetime(df["time"]).dt.normalize()
            df = df.set_index("time")
            # Dedup defensively (matches the validator's day-bar handling).
            if df.index.has_duplicates:
                df = df[~df.index.duplicated(keep="last")]
            out[sym] = df
            summary.append((sym, len(df),
                            df.index.min().date(), df.index.max().date()))
    finally:
        con.close()
    return {"data": out, "summary": summary}


# ── Filter funnel (independent recompute, vectorized) ──────────────────


def compute_filter_funnel(data: dict[str, pd.DataFrame],
                           regime_index: str) -> dict:
    """Count how many bar-events survive each of the strategy's four
    entry filters across the FULL window, summed over all symbols.

    This is an INDEPENDENT recompute — it does not call into the
    strategy. It uses the same ``config`` constants the strategy uses
    and the same ``compute_atr`` for ATR. If a future strategy change
    diverges from these counts, the report's funnel and the actual
    trade count will diverge, surfacing the drift.
    """
    nsei = data.get(regime_index)
    if nsei is None or nsei.empty:
        return {"error": f"regime index {regime_index!r} not in data"}
    nsei_ma = nsei["close"].rolling(REGIME_MA, min_periods=REGIME_MA).mean()
    regime_on = (nsei["close"] > nsei_ma).reindex(nsei.index)

    n_raw = n_vol = n_regime = n_rr = 0
    per_symbol = []
    for sym, df in data.items():
        if sym == regime_index:
            continue
        n = len(df)
        if n < max(BREAKOUT_LOOKBACK, VOLUME_AVG_WINDOW, ATR_PERIOD) + 2:
            per_symbol.append((sym, 0, 0, 0, 0))
            continue

        # Prior N-day high, EXCLUDING T (Donchian upper, matches strategy)
        prior_high = (df["high"].rolling(BREAKOUT_LOOKBACK, min_periods=BREAKOUT_LOOKBACK)
                                  .max().shift(1))
        prior_low = (df["low"].rolling(BREAKOUT_LOOKBACK, min_periods=BREAKOUT_LOOKBACK)
                                .min().shift(1))
        vol_avg = (df["volume"].rolling(VOLUME_AVG_WINDOW, min_periods=VOLUME_AVG_WINDOW)
                                   .mean().shift(1))
        atr = compute_atr(df, period=ATR_PERIOD)

        breakout = df["close"] > prior_high
        vol_ok = df["volume"] > VOLUME_MULT * vol_avg
        # Align regime to this symbol's dates by reindex/ffill (matches
        # the strategy's same-causal-view read; this fills calendar gaps
        # where the symbol traded but the index didn't). Cast to float
        # before reindex so fillna can produce a strict bool without
        # pandas' downcasting deprecation noise.
        regime_aligned = regime_on.astype(float).reindex(df.index, method="ffill")
        regime_ok = (regime_aligned.fillna(0.0) > 0.5)

        # RR screen — replicate strategy: stop=entry-2*ATR; target=2*BL-RL.
        entry = df["close"]
        stop = entry - ATR_SL_MULTIPLIER * atr
        target = 2.0 * prior_high - prior_low
        # Valid screen needs: stop < entry, recent_low < prior_high,
        # target > entry. Compute RR only when valid.
        valid = (
            (stop < entry) & (prior_low < prior_high)
            & (target > entry) & atr.notna() & (atr > 0)
        )
        rr = pd.Series(np.nan, index=df.index)
        denom = entry - stop
        rr.loc[valid] = ((target - entry) / denom).loc[valid]
        rr_ok = rr >= MIN_RR

        # Funnel counts — each subsequent count is conditional on the prior
        # AND the current filter being TRUE. NaN -> False under boolean ops.
        n_b = int(breakout.fillna(False).sum())
        n_b_vol = int((breakout & vol_ok).fillna(False).sum())
        n_b_vol_reg = int((breakout & vol_ok & regime_ok).fillna(False).sum())
        n_b_vol_reg_rr = int((breakout & vol_ok & regime_ok & rr_ok).fillna(False).sum())
        n_raw += n_b
        n_vol += n_b_vol
        n_regime += n_b_vol_reg
        n_rr += n_b_vol_reg_rr
        per_symbol.append((sym, n_b, n_b_vol, n_b_vol_reg, n_b_vol_reg_rr))

    return {
        "n_raw_breakouts": n_raw,
        "n_after_volume": n_vol,
        "n_after_regime": n_regime,
        "n_after_rr": n_rr,
        "per_symbol": per_symbol,
    }


# ── Trade-tape statistics ───────────────────────────────────────────────


def per_symbol_pf(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    g = trades.groupby("symbol")
    rows = []
    for sym, sub in g:
        wins = sub[sub["pnl"] > 0]
        losses = sub[sub["pnl"] < 0]
        gross_w = float(wins["pnl"].sum())
        gross_l = float(losses["pnl"].sum())
        pf = (gross_w / abs(gross_l)) if gross_l < 0 else float("inf")
        rows.append({
            "symbol": sym,
            "n_trades": len(sub),
            "n_wins": len(wins),
            "win_rate": len(wins) / len(sub) if len(sub) else 0.0,
            "pf": pf,
            "total_pnl": float(sub["pnl"].sum()),
        })
    out = pd.DataFrame(rows)
    return out.sort_values("total_pnl", ascending=False)


def per_year_pf(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    df = trades.copy()
    df["year"] = pd.to_datetime(df["exit_date"]).dt.year.astype(int)
    rows = []
    for yr, sub in df.groupby("year"):
        wins = sub[sub["pnl"] > 0]
        losses = sub[sub["pnl"] < 0]
        gross_w = float(wins["pnl"].sum())
        gross_l = float(losses["pnl"].sum())
        pf = (gross_w / abs(gross_l)) if gross_l < 0 else float("inf")
        rows.append({
            "year": int(yr),
            "n_trades": int(len(sub)),
            "n_wins": int(len(wins)),
            "win_rate": len(wins) / len(sub) if len(sub) else 0.0,
            "pf": pf,
            "total_pnl": float(sub["pnl"].sum()),
        })
    return pd.DataFrame(rows).sort_values("year")


def binomial_p_value(wins: int, n: int, null_p: float = 0.5) -> float:
    """One-sided P(X >= wins | n, p=null_p). The null hypothesis is
    'no edge' (win rate = 50%). A small p means the observed wins are
    unlikely under chance."""
    if n == 0:
        return 1.0
    p = sum(math.comb(n, k) * (null_p ** k) * ((1 - null_p) ** (n - k))
            for k in range(wins, n + 1))
    return p


def bootstrap_pf_ci(trades: pd.DataFrame, *, n_resamples: int = BOOTSTRAP_RESAMPLES,
                     seed: int = BOOTSTRAP_SEED) -> dict:
    """Bootstrap a confidence interval on the profit factor by resampling
    trades WITH replacement. Returns 5th, 50th, 95th percentile PFs."""
    if trades.empty:
        return {"p05": float("nan"), "p50": float("nan"),
                "p95": float("nan"), "n_resamples": 0}
    pnl = trades["pnl"].to_numpy()
    rng = np.random.default_rng(seed)
    pfs = np.empty(n_resamples)
    for i in range(n_resamples):
        sample = rng.choice(pnl, size=len(pnl), replace=True)
        wins = sample[sample > 0].sum()
        losses = sample[sample < 0].sum()
        pfs[i] = (wins / abs(losses)) if losses < 0 else (
            float("inf") if wins > 0 else float("nan"))
    finite = pfs[np.isfinite(pfs)]
    if len(finite) == 0:
        return {"p05": float("nan"), "p50": float("nan"),
                "p95": float("nan"), "n_resamples": n_resamples}
    return {
        "p05": float(np.percentile(finite, 5)),
        "p50": float(np.percentile(finite, 50)),
        "p95": float(np.percentile(finite, 95)),
        "n_resamples": n_resamples,
    }


# ── Report ──────────────────────────────────────────────────────────────


def _fmt_pf(pf) -> str:
    if pf is None or (isinstance(pf, float) and pf != pf):
        return "n/a"
    if pf == float("inf"):
        return "inf (no losers in sample)"
    return f"{pf:.3f}"


def _fmt(val, decimals=3, suffix=""):
    if val is None or (isinstance(val, float) and val != val):
        return "n/a"
    return f"{val:.{decimals}f}{suffix}"


def _gate_row(name: str, observed, op_str: str, threshold,
               passed: bool) -> str:
    mark = "✅ PASS" if passed else "❌ FAIL"
    return f"| {name} | {observed} | {op_str} | {threshold} | {mark} |"


def build_report(*, replay_result: dict, funnel: dict, load_summary: list,
                  symbols: list, regime_index: str) -> str:
    metrics = replay_result["metrics"]
    trades = replay_result["trades"]
    eq = replay_result["equity_curve"]
    pf = metrics.get("profit_factor", float("nan"))
    sharpe = metrics.get("sharpe", float("nan"))
    mdd = metrics.get("max_drawdown", float("nan"))
    wr = metrics.get("win_rate", float("nan"))
    cagr = metrics.get("cagr", float("nan"))
    n = metrics.get("n_trades", 0)

    # Gate verdicts
    pf_pass = (pf == float("inf")) or (pf > GATE_PROFIT_FACTOR)
    sharpe_pass = sharpe > GATE_SHARPE
    # max DD as a fraction; bootstrap reports magnitude (e.g. 0.12 = 12%).
    # We pass when the magnitude is BELOW the gate (smaller drawdown is better).
    mdd_pass = abs(mdd) < GATE_MAX_DRAWDOWN
    wr_pass = wr > GATE_WIN_RATE

    # Per-symbol + per-year breakdowns
    sym_df = per_symbol_pf(trades)
    yr_df = per_year_pf(trades)

    # Significance — binomial null = 50% win rate (no edge).
    wins = int((trades["pnl"] > 0).sum()) if not trades.empty else 0
    p_value = binomial_p_value(wins, n) if n > 0 else 1.0

    # Bootstrap PF
    bs = bootstrap_pf_ci(trades)

    # Survivorship-discounted PF
    if pf == float("inf") or (isinstance(pf, float) and pf != pf):
        pf_disc = pf
    else:
        pf_disc = pf * (1.0 - SURVIVORSHIP_DISCOUNT)

    # Concentration flags (LAW 5 + 8)
    one_stock_carries = False
    if not sym_df.empty and trades["pnl"].sum() > 0:
        top = sym_df.iloc[0]
        if top["total_pnl"] > 0.5 * trades["pnl"].sum():
            one_stock_carries = True

    one_year_carries = False
    if not yr_df.empty and trades["pnl"].sum() > 0:
        top_yr = yr_df.sort_values("total_pnl", ascending=False).iloc[0]
        if top_yr["total_pnl"] > 0.5 * trades["pnl"].sum():
            one_year_carries = True

    too_few = n < 30

    # ── Compose the report ─────────────────────────────────────────────
    lines: list[str] = []
    lines.append("# T3 — First Honest Backtest Report")
    lines.append("")
    lines.append(f"**Branch:** `feature/t3-first-backtest`")
    lines.append(f"**Strategy:** `signals.breakout.BreakoutStrategy` "
                  f"(pure rules — Donchian upper + volume + regime + R:R "
                  f"measured-move screen + Chandelier + time stop)")
    lines.append(f"**Window:** {replay_result['start'].date()} → "
                  f"{replay_result['end'].date()} "
                  f"({replay_result['n_days']} trading days)")
    lines.append(f"**Universe:** {len(symbols)} equities + {regime_index} "
                  f"(macro). ^INDIAVIX excluded as the strategy would "
                  f"otherwise try to trade it.")
    lines.append(f"**Initial capital:** Rs {INITIAL_CAPITAL:,.0f}")
    lines.append("")

    # 1. Headline metrics
    lines.append("## 1. Headline metrics")
    lines.append("")
    lines.append(f"- **Profit Factor:** {_fmt_pf(pf)}")
    lines.append(f"- **Sharpe ratio:** {_fmt(sharpe)}")
    lines.append(f"- **Max drawdown:** {_fmt(abs(mdd), 4, ' (= '+_fmt(abs(mdd)*100, 2, '%)'))}" if pd.notna(mdd) else "- Max drawdown: n/a")
    lines.append(f"- **Win rate:** {_fmt(wr)}  ({wins} of {n} closed trades)")
    lines.append(f"- **CAGR:** {_fmt(cagr)}")
    lines.append(f"- **n_trades:** {n}")
    if not eq.empty:
        final_eq = float(eq.iloc[-1])
        total_ret = final_eq / INITIAL_CAPITAL - 1.0
        lines.append(f"- **Final equity:** Rs {final_eq:,.0f}  "
                      f"(total return {total_ret:+.2%})")
    lines.append("")

    # 2. Gate verdicts
    lines.append("## 2. Verdict vs the success gates")
    lines.append("")
    lines.append("| Gate | Observed | Required | Threshold | Result |")
    lines.append("|---|---|---|---:|:---:|")
    lines.append(_gate_row("Profit Factor", _fmt_pf(pf), ">", GATE_PROFIT_FACTOR, pf_pass))
    lines.append(_gate_row("Sharpe ratio", _fmt(sharpe), ">", GATE_SHARPE, sharpe_pass))
    lines.append(_gate_row("Max drawdown (mag)", _fmt(abs(mdd), 4), "<", GATE_MAX_DRAWDOWN, mdd_pass))
    lines.append(_gate_row("Win rate", _fmt(wr), ">", GATE_WIN_RATE, wr_pass))
    lines.append("")
    n_pass = sum([pf_pass, sharpe_pass, mdd_pass, wr_pass])
    lines.append(f"**Summary:** {n_pass} of 4 gates cleared.")
    lines.append("")

    # 3. Filter funnel
    lines.append("## 3. Filter funnel (entry pipeline)")
    lines.append("")
    if "error" in funnel:
        lines.append(f"_(could not compute funnel: {funnel['error']})_")
    else:
        n_raw = funnel["n_raw_breakouts"]
        n_v = funnel["n_after_volume"]
        n_r = funnel["n_after_regime"]
        n_rr = funnel["n_after_rr"]
        lines.append("| Stage | Symbol-days surviving | % of raw breakouts | Δ from prior stage |")
        lines.append("|---|---:|---:|---:|")
        def pct(x): return f"{x / n_raw * 100:.1f}%" if n_raw else "n/a"
        lines.append(f"| 1. Raw Donchian breakouts (close > prior-{BREAKOUT_LOOKBACK}-day high, excl. T) | {n_raw} | 100.0% | — |")
        lines.append(f"| 2. Survives volume confirmation (> {VOLUME_MULT}× 20-day avg) | {n_v} | {pct(n_v)} | -{n_raw - n_v} |")
        lines.append(f"| 3. Survives regime gate (^NSEI > {REGIME_MA}-day MA) | {n_r} | {pct(n_r)} | -{n_v - n_r} |")
        lines.append(f"| 4. Survives measured-move R:R screen (≥ {MIN_RR}) | {n_rr} | {pct(n_rr)} | -{n_r - n_rr} |")
        lines.append(f"| 5. Actually became trades (passed portfolio caps + slot availability) | {n} | {pct(n)} | -{n_rr - n} |")
        lines.append("")
        # Honest interpretation: which filter killed the most?
        kill_v = n_raw - n_v
        kill_r = n_v - n_r
        kill_rr = n_r - n_rr
        kill_port = n_rr - n
        kills = [(kill_v, "volume"), (kill_r, "regime"), (kill_rr, "R:R screen"),
                 (kill_port, "portfolio caps / slot availability")]
        kills.sort(reverse=True)
        top_killer = kills[0]
        lines.append(f"**Biggest single filter:** **{top_killer[1]}** "
                      f"(-{top_killer[0]} symbol-days, "
                      f"{top_killer[0] / n_raw * 100:.1f}% of raw breakouts).")
        lines.append("")

    # 4. Trade-tape sanity
    lines.append("## 4. Trade-tape sanity (LAWS 5 + 8)")
    lines.append("")

    # Per-symbol
    lines.append("### 4a. Per-symbol breakdown (sorted by total PnL)")
    lines.append("")
    if sym_df.empty:
        lines.append("_No trades to break down._")
    else:
        lines.append("| Symbol | n_trades | n_wins | Win rate | PF | Total PnL (Rs) |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for _, r in sym_df.iterrows():
            lines.append(f"| {r['symbol']} | {r['n_trades']} | {r['n_wins']} | "
                          f"{r['win_rate']:.3f} | {_fmt_pf(r['pf'])} | "
                          f"{r['total_pnl']:+,.0f} |")
        lines.append("")
        if one_stock_carries:
            top = sym_df.iloc[0]
            lines.append(f"⚠️ **CONCENTRATION FLAG:** the top symbol "
                          f"({top['symbol']}, Rs {top['total_pnl']:+,.0f}) "
                          f"carries more than half the total PnL — the "
                          f"edge may be a single-stock fluke rather than "
                          f"a generalisable signal.")
            lines.append("")

    # Per-year
    lines.append("### 4b. Per-year breakdown")
    lines.append("")
    if yr_df.empty:
        lines.append("_No trades to break down._")
    else:
        lines.append("| Year | n_trades | Win rate | PF | Total PnL (Rs) |")
        lines.append("|---:|---:|---:|---:|---:|")
        for _, r in yr_df.iterrows():
            lines.append(f"| {int(r['year'])} | {int(r['n_trades'])} | "
                          f"{r['win_rate']:.3f} | {_fmt_pf(r['pf'])} | "
                          f"{r['total_pnl']:+,.0f} |")
        lines.append("")
        if one_year_carries:
            top_yr = yr_df.sort_values("total_pnl", ascending=False).iloc[0]
            lines.append(f"⚠️ **CONCENTRATION FLAG:** year {int(top_yr['year'])} "
                          f"carries more than half the total PnL "
                          f"(Rs {top_yr['total_pnl']:+,.0f}) — the edge "
                          f"may be a single-regime fluke.")
            lines.append("")

    # 4c. Significance
    lines.append("### 4c. Significance — could this be luck?")
    lines.append("")
    if too_few:
        lines.append(f"⚠️ **n_trades = {n} < 30** — per LAW 8, the sample "
                      f"is too small to draw confident conclusions. Numbers "
                      f"below are reported for transparency but should not "
                      f"be over-weighted.")
        lines.append("")
    lines.append("**Binomial test** (null hypothesis: no edge → win rate 50%)")
    lines.append("")
    lines.append(f"- Observed: {wins} wins in {n} trades "
                  f"(win rate {wr:.3f}).")
    lines.append(f"- P(X ≥ {wins} | n={n}, p=0.5) = **{p_value:.4f}**")
    if p_value < 0.05:
        lines.append(f"- p < 0.05 — win rate is significantly above chance.")
    elif p_value < 0.10:
        lines.append(f"- p < 0.10 — marginally above chance, not statistically conclusive.")
    else:
        lines.append(f"- p ≥ 0.10 — NOT statistically distinguishable from chance at this sample size.")
    lines.append("")
    lines.append("**Bootstrap CI on Profit Factor** "
                  f"({bs['n_resamples']} resamples with replacement)")
    lines.append("")
    lines.append(f"- 5th percentile PF: {_fmt_pf(bs['p05'])}")
    lines.append(f"- 50th percentile (median) PF: {_fmt_pf(bs['p50'])}")
    lines.append(f"- 95th percentile PF: {_fmt_pf(bs['p95'])}")
    if not (isinstance(bs["p05"], float) and bs["p05"] != bs["p05"]):
        if bs["p05"] < 1.0 < bs["p95"]:
            lines.append("- The 90% CI **spans 1.0** — bootstrap cannot rule out "
                          "a true PF of 1.0 (break-even). Edge is uncertain.")
        elif bs["p05"] >= 1.0:
            lines.append("- The 5th percentile PF is above 1.0 — bootstrap "
                          "suggests the edge is positive even on the pessimistic tail.")
        else:
            lines.append("- The 95th percentile PF is below 1.0 — bootstrap "
                          "suggests the edge is negative even on the optimistic tail.")
    lines.append("")

    # 5. Survivorship caveat
    lines.append("## 5. Survivorship caveat — raw vs discounted")
    lines.append("")
    lines.append("From `data/universe.py`:")
    lines.append("")
    lines.append(f"> {SURVIVORSHIP_NOTE}")
    lines.append("")
    lines.append("| Quantity | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Raw PF | {_fmt_pf(pf)} |")
    lines.append(f"| Survivorship-discounted PF (× {1 - SURVIVORSHIP_DISCOUNT:.2f}) | {_fmt_pf(pf_disc)} |")
    lines.append("")
    lines.append(f"Discount rate **{SURVIVORSHIP_DISCOUNT:.0%}** is a "
                  "conservative middle ground — the universe does retain "
                  "the four prominent fallen-from-NIFTY names "
                  "(YESBANK / VEDL / LUPIN / GAIL) so the worst form of "
                  "survivorship bias (today's-winners-only) is partially "
                  "corrected, but fully-delisted tickers (e.g. Bharti "
                  "Infratel, merged 2020) have no fetchable OHLC and are "
                  "absent. Treat the discounted PF as a more honest live "
                  "expectation than the raw number.")
    lines.append("")
    lines.append("**Reminder from the bootstrap doc:** live PF typically "
                  "lands 30-50% below backtest PF. If the discounted "
                  "backtest PF is already near the 1.3 gate, live could "
                  "easily fall below break-even.")
    lines.append("")

    # 6. Plain-English verdict
    lines.append("## 6. Plain-English verdict")
    lines.append("")
    verdict_lines = _build_verdict(
        n_pass=n_pass, n=n, wr=wr, pf=pf, pf_disc=pf_disc, sharpe=sharpe,
        mdd=mdd, too_few=too_few, p_value=p_value,
        one_stock_carries=one_stock_carries, one_year_carries=one_year_carries,
        bs=bs, funnel=funnel)
    lines.extend(verdict_lines)
    lines.append("")

    # 7. Proposed follow-ups (NOT applied)
    lines.append("## 7. Proposed follow-up tickets (NOT applied per LAW 4)")
    lines.append("")
    lines.append(_proposed_followups(
        too_few=too_few, n_pass=n_pass, n=n, mdd=mdd,
        one_stock_carries=one_stock_carries,
        one_year_carries=one_year_carries, funnel=funnel))
    lines.append("")

    # 8. Data summary (small reference table)
    lines.append("## 8. Data summary")
    lines.append("")
    lines.append("| Symbol | n_bars | First date | Last date |")
    lines.append("|---|---:|---|---|")
    for sym, n_bars, fd, ld in load_summary:
        lines.append(f"| {sym} | {n_bars} | {fd if fd else 'n/a'} | "
                      f"{ld if ld else 'n/a'} |")
    lines.append("")
    lines.append("_End of report._")
    return "\n".join(lines)


def _build_verdict(*, n_pass, n, wr, pf, pf_disc, sharpe, mdd, too_few,
                    p_value, one_stock_carries, one_year_carries, bs,
                    funnel) -> list[str]:
    out: list[str] = []
    if n == 0:
        out.append("The strategy did **not place a single trade** over the "
                    f"full {2026 - 2016}-year window. The entry filters are "
                    f"clearly over-restrictive on this universe with these "
                    f"thresholds. Calibration is needed before any verdict "
                    f"on edge is meaningful — see proposed follow-ups.")
        return out

    if too_few:
        out.append(f"The strategy placed **{n} trades** in a "
                    f"~{2026 - 2016}-year window — far below the LAW 8 "
                    f"threshold of 30. Statistical claims at this sample "
                    f"size are weak; the headline numbers may swing wildly "
                    f"with one or two more trades.")
        out.append("")

    if n_pass == 4:
        out.append(f"All four success gates pass on raw numbers "
                    f"(PF {_fmt_pf(pf)}, Sharpe {_fmt(sharpe)}, "
                    f"max DD {_fmt(abs(mdd) if pd.notna(mdd) else float('nan'), 3)}, "
                    f"win rate {_fmt(wr)}). After the {SURVIVORSHIP_DISCOUNT:.0%} "
                    f"survivorship discount the PF lands at {_fmt_pf(pf_disc)}.")
    elif n_pass >= 2:
        out.append(f"Partial result: **{n_pass} of 4** success gates pass "
                    f"on raw numbers. The strategy is operating but the "
                    f"edge is not clean.")
    else:
        out.append(f"Only **{n_pass} of 4** success gates pass on raw "
                    f"numbers. The strategy is NOT working at the current "
                    f"calibration on this universe.")

    out.append("")

    if p_value >= 0.10 or too_few:
        out.append(f"The binomial test cannot rule out chance at this "
                    f"sample size (p = {p_value:.3f}). Even if the gate "
                    f"verdicts look favourable, we do not have enough "
                    f"evidence yet to call the edge real.")
    elif p_value < 0.05:
        out.append(f"The win rate is significantly above 50% "
                    f"(p = {p_value:.4f}); the bootstrap CI on PF is "
                    f"[{_fmt_pf(bs['p05'])}, {_fmt_pf(bs['p95'])}] (90%).")
    else:
        out.append(f"The win rate is marginally above chance "
                    f"(p = {p_value:.3f}); evidence for edge is weak but "
                    f"present.")

    out.append("")

    flags = []
    if one_stock_carries:
        flags.append("one symbol carries >50% of total PnL")
    if one_year_carries:
        flags.append("one year carries >50% of total PnL")
    if flags:
        out.append(f"⚠️ Concentration: {'; '.join(flags)}. Treat the "
                    f"headline as fragile until more breadth accumulates.")
        out.append("")

    out.append(f"Per the bootstrap doc, live PF typically lands 30–50% "
                f"below backtest PF. With raw PF "
                f"{_fmt_pf(pf)}, the realistic live expectation is "
                f"roughly {_fmt_pf(pf * 0.55 if isinstance(pf, float) and pf != float('inf') else None)} – "
                f"{_fmt_pf(pf * 0.75 if isinstance(pf, float) and pf != float('inf') else None)}. "
                f"This is the number to plan around, not the headline.")
    return out


def _proposed_followups(*, too_few: bool, n_pass: int, n: int, mdd: float,
                          one_stock_carries: bool, one_year_carries: bool,
                          funnel: dict) -> str:
    """T4 proposals — explicitly NOT applied in T3 (LAW 4). Each item
    is a SINGLE-CHANGE candidate so a future ticket can isolate cause."""
    items: list[str] = []

    if n == 0:
        items.append("**T4 candidate: thresholds are too restrictive — "
                      "zero trades fired across the full window.** Likely "
                      "fixes (each a separate ticket): loosen `MIN_RR` to "
                      "1.5, lower `VOLUME_MULT` to 1.2, or shorten "
                      "`BREAKOUT_LOOKBACK` to 10. Run one at a time and "
                      "report the funnel + n_trades each time.")
        return "\n".join(f"- {it}" for it in items)

    if abs(mdd) >= GATE_MAX_DRAWDOWN:
        items.append(f"**T4 candidate: shrink the max-drawdown.** Observed "
                      f"|max DD| ≈ {abs(mdd):.0%} vs the {GATE_MAX_DRAWDOWN:.0%} "
                      f"gate — the dominant failure mode. Candidates "
                      f"(EACH a single change per LAW 4): "
                      f"(a) tighten `ATR_SL_MULTIPLIER` from "
                      f"{ATR_SL_MULTIPLIER} to 1.5, "
                      f"(b) tighten `CHANDELIER_ATR_MULT` from the current "
                      f"value to 2.0, (c) add a portfolio-level kill switch "
                      f"that closes all positions when daily DD exceeds e.g. "
                      f"5%, (d) reduce `MAX_RISK_PCT` per trade. Pick ONE.")

    if one_stock_carries:
        items.append("**T4 candidate: investigate single-stock concentration.** "
                      "One symbol carries >50% of total PnL. Before any "
                      "calibration, run the same strategy on a per-symbol "
                      "PF distribution and ask: is the edge a generalisable "
                      "Donchian-breakout signal, or is it one stock's regime? "
                      "If the latter, no parameter tweak will help.")

    if one_year_carries:
        items.append("**T4 candidate: regime-shift sensitivity.** One year "
                      "carries >50% of total PnL — strategy may be a 'works "
                      "in trending markets, fails otherwise' filter. "
                      "Candidates: split-window PF (bull / bear / sideways) "
                      "using NIFTY 50 regimes, or add a vol-of-vol filter "
                      "to identify the 'good' regimes.")

    if isinstance(funnel, dict) and "n_raw_breakouts" in funnel:
        n_raw = funnel["n_raw_breakouts"]
        n_after_vol = funnel["n_after_volume"]
        n_after_rr = funnel["n_after_rr"]
        if n_raw > 0:
            vol_kill_pct = (n_raw - n_after_vol) / n_raw
            rr_kill_pct = (funnel["n_after_regime"] - n_after_rr) / n_raw
            if vol_kill_pct > 0.5:
                items.append(f"**T4 candidate: re-examine the volume filter.** "
                              f"It removes {vol_kill_pct:.0%} of raw breakouts "
                              f"— is the strategy starved at this multiplier? "
                              f"Run `VOLUME_MULT` ∈ {{1.2, 1.5, 1.8, 2.0}} on "
                              f"the same harness and report PF + n_trades + "
                              f"max DD for each.")
            if rr_kill_pct > 0.20:
                items.append(f"**T4 candidate: re-examine the R:R screen.** "
                              f"It removes {rr_kill_pct:.0%} of regime-survivors "
                              f"— consistent with T2's note that ATR-warm-up "
                              f"on the breakout day inflates the stop and "
                              f"shrinks reward-to-risk on tight channels. "
                              f"Candidates: use a longer ATR period (e.g. 21) "
                              f"to dampen the breakout-day TR spike, OR use a "
                              f"swing-low-based stop instead of ATR.")

    if too_few:
        items.append("**T4 candidate: parameter robustness sweep.** Before "
                      "promoting any single calibration, run a small "
                      "lookback/threshold grid on the same harness and "
                      "report PF distribution across cells. Single-cell "
                      "results at low n are not robust.")

    if n_pass == 4 and not too_few and not one_stock_carries and not one_year_carries:
        items.append("None — gates clear and no concentration flags. If the "
                      "live paper-trade phase later diverges, revisit then "
                      "with the live evidence in hand.")

    if not items:
        items.append("None mechanically triggered. If the human read of the "
                      "tape suggests calibration is needed, propose it "
                      "explicitly as a T4 ticket.")
    return "\n".join(f"- {it}" for it in items)


# ── Main ────────────────────────────────────────────────────────────────


def main() -> int:
    print(f"[T3] loading data for {len(POINT_IN_TIME_NSE25)} equities "
          f"+ {REGIME_INDEX} (^INDIAVIX excluded)...")
    load = load_data(POINT_IN_TIME_NSE25, REGIME_INDEX)
    data = load["data"]
    print(f"[T3] {len(data)} symbols loaded. Running replay...")

    strategy = BreakoutStrategy()
    result = run_replay(data, strategy, initial_capital=INITIAL_CAPITAL)
    metrics = result["metrics"]
    print(f"[T3] replay done. n_trades={metrics.get('n_trades', 0)}, "
          f"PF={metrics.get('profit_factor', 'n/a')}, "
          f"Sharpe={metrics.get('sharpe', 'n/a'):.3f}")

    print(f"[T3] computing filter funnel (independent recompute)...")
    funnel = compute_filter_funnel(data, REGIME_INDEX)
    if "error" not in funnel:
        print(f"[T3] funnel — raw breakouts: {funnel['n_raw_breakouts']}, "
              f"after vol: {funnel['n_after_volume']}, "
              f"after regime: {funnel['n_after_regime']}, "
              f"after RR: {funnel['n_after_rr']}, "
              f"actual trades: {metrics.get('n_trades', 0)}")

    print(f"[T3] writing report -> {REPORT_PATH.relative_to(PROJECT_ROOT)}")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    report = build_report(
        replay_result=result, funnel=funnel,
        load_summary=load["summary"], symbols=POINT_IN_TIME_NSE25,
        regime_index=REGIME_INDEX)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"[T3] done. {len(report.splitlines())} lines.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
