"""Reusable robustness + significance diagnostics for the replay harness.

WHY THIS MODULE EXISTS
======================
The T3 (breakout) backtest looked OK on its headline number (PF 1.118)
until the trade-tape sanity surfaced that ONE stock (ONGC) carried
+Rs 64K of the +Rs 75K total PnL, and ONE year (2020) carried +Rs 53K
of it. With those single contributors removed the strategy was below
break-even. The T3 report computed all of this inline in
``scripts/r3_run_backtest.py``; the MR-2 ticket directs that the same
diagnostics be factored into this module so EVERY future strategy's
backtest report runs them by default rather than depending on a
report author remembering to copy-paste.

Each function takes a ``pd.DataFrame`` matching the trade-tape produced
by ``backtesting.replay.run_replay`` — columns include at minimum
``symbol``, ``entry_date``, ``exit_date``, ``pnl``. Functions are pure
(no IO, no random state except where explicit) and return either a
small named ``dict`` or a ``DataFrame``.

CONTRACT (LAW 5 — verify, not assume)
=====================================
The "profit factor" everywhere here is ``sum(pnl > 0) / |sum(pnl < 0)|``,
matching the backtest's ``backtesting.metrics`` definition. Trades with
``pnl == 0`` are ignored. An infinite PF (no losers) is reported as
``float('inf')``; this matches the rest of the codebase rather than
silently capping it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


# ── Core PF helper ──────────────────────────────────────────────────────


def profit_factor(pnl: pd.Series | np.ndarray) -> float:
    """``sum(wins) / |sum(losses)|``. Returns ``inf`` if no losers,
    ``nan`` if no trades at all (so the caller can decide how to render).
    """
    arr = np.asarray(pnl, dtype=float)
    if arr.size == 0:
        return float("nan")
    wins = arr[arr > 0].sum()
    losses = arr[arr < 0].sum()
    if losses == 0:
        return float("inf") if wins > 0 else float("nan")
    return float(wins / abs(losses))


# ── Per-bucket breakdowns ───────────────────────────────────────────────


def per_symbol_pnl(trades: pd.DataFrame) -> pd.DataFrame:
    """One row per symbol with n_trades, n_wins, win_rate, pf, total_pnl,
    sorted by ``total_pnl`` descending. Empty input -> empty DataFrame
    (no header construction needed: caller branches on empty)."""
    if trades.empty:
        return pd.DataFrame()
    rows = []
    for sym, sub in trades.groupby("symbol"):
        n = len(sub)
        wins = sub[sub["pnl"] > 0]
        rows.append({
            "symbol": sym,
            "n_trades": int(n),
            "n_wins": int(len(wins)),
            "win_rate": (len(wins) / n) if n else 0.0,
            "pf": profit_factor(sub["pnl"]),
            "total_pnl": float(sub["pnl"].sum()),
        })
    return pd.DataFrame(rows).sort_values("total_pnl", ascending=False)


def per_year_pnl(trades: pd.DataFrame) -> pd.DataFrame:
    """Per-calendar-year (using ``exit_date``) version of the above."""
    if trades.empty:
        return pd.DataFrame()
    df = trades.copy()
    df["year"] = pd.to_datetime(df["exit_date"]).dt.year.astype(int)
    rows = []
    for yr, sub in df.groupby("year"):
        n = len(sub)
        wins = sub[sub["pnl"] > 0]
        rows.append({
            "year": int(yr),
            "n_trades": int(n),
            "n_wins": int(len(wins)),
            "win_rate": (len(wins) / n) if n else 0.0,
            "pf": profit_factor(sub["pnl"]),
            "total_pnl": float(sub["pnl"].sum()),
        })
    return pd.DataFrame(rows).sort_values("year")


# ── Robustness ─────────────────────────────────────────────────────────


@dataclass
class RobustnessResult:
    """Bundle the four robustness numbers MR-2 mandates so a report
    can render them as a single block."""
    pf_raw: float
    pf_ex_top_symbol: float        # PF with the largest-PnL symbol removed
    pf_ex_best_year: float         # PF with the largest-PnL year removed
    n_negative_symbols: int        # how many symbols ended net negative
    top_symbol: str | None         # name of the symbol that was removed
    top_symbol_pnl: float | None   # its total PnL (signed)
    best_year: int | None          # year that was removed
    best_year_pnl: float | None    # its total PnL (signed)


def robustness(trades: pd.DataFrame) -> RobustnessResult:
    """The four-question robustness check the breakout strategy failed
    silently in T3 — packaged for any future strategy's backtest report.

    The "top symbol" and "best year" are chosen by HIGHEST POSITIVE total
    PnL (the contributor most likely to flatter the headline). If that
    contributor's total PnL is non-positive, removing it would make
    things worse and the test is conceptually vacuous — we report it as
    such (``pf_ex_top_symbol == pf_raw`` and ``top_symbol = None``).
    """
    pf_raw = profit_factor(trades["pnl"]) if not trades.empty else float("nan")
    sym_df = per_symbol_pnl(trades)
    yr_df = per_year_pnl(trades)

    top_sym = top_sym_pnl = None
    pf_ex_top_sym = pf_raw
    if not sym_df.empty:
        cand = sym_df.iloc[0]
        if cand["total_pnl"] > 0:
            top_sym = str(cand["symbol"])
            top_sym_pnl = float(cand["total_pnl"])
            pf_ex_top_sym = profit_factor(
                trades.loc[trades["symbol"] != top_sym, "pnl"])

    best_year = best_year_pnl = None
    pf_ex_best_year = pf_raw
    if not yr_df.empty:
        top_year_row = yr_df.sort_values("total_pnl", ascending=False).iloc[0]
        if top_year_row["total_pnl"] > 0:
            best_year = int(top_year_row["year"])
            best_year_pnl = float(top_year_row["total_pnl"])
            mask = pd.to_datetime(trades["exit_date"]).dt.year != best_year
            pf_ex_best_year = profit_factor(trades.loc[mask, "pnl"])

    n_neg = int((sym_df["total_pnl"] < 0).sum()) if not sym_df.empty else 0
    return RobustnessResult(
        pf_raw=pf_raw, pf_ex_top_symbol=pf_ex_top_sym,
        pf_ex_best_year=pf_ex_best_year, n_negative_symbols=n_neg,
        top_symbol=top_sym, top_symbol_pnl=top_sym_pnl,
        best_year=best_year, best_year_pnl=best_year_pnl)


# ── Concentration flags ─────────────────────────────────────────────────


def concentration_flags(trades: pd.DataFrame,
                          fraction_threshold: float = 0.5) -> dict:
    """Returns booleans + supporting numbers for the two concentration
    questions T3's report explicitly checked. A flag is True when ONE
    bucket carries more than ``fraction_threshold`` of total POSITIVE
    PnL — that's the headline-edge-is-fragile signal.

    The denominator is ``sum(pnl > 0)`` (gross positive PnL) rather than
    net total PnL. Net total can be small, zero, or negative, which made
    naive division either 0% (when total ≤ 0) or >100% (when one
    contributor exceeded a small net total). Gross-positive denominator
    gives a well-defined 0-100% share regardless of strategy outcome.
    """
    if trades.empty:
        return {"one_symbol_carries": False, "one_year_carries": False,
                "total_pnl": 0.0, "gross_positive_pnl": 0.0,
                "top_symbol_share": 0.0, "top_year_share": 0.0}
    sym_df = per_symbol_pnl(trades)
    yr_df = per_year_pnl(trades)
    total = float(trades["pnl"].sum())
    gross_pos = float(trades.loc[trades["pnl"] > 0, "pnl"].sum())
    one_sym = one_yr = False
    sym_share = yr_share = 0.0
    if gross_pos > 0:
        if not sym_df.empty:
            # Top symbol by POSITIVE PnL (the contributor we'd remove
            # in the robustness check). If no symbol is net positive,
            # the strategy has no edge to attribute and the share is 0.
            pos_syms = sym_df.loc[sym_df["total_pnl"] > 0]
            if not pos_syms.empty:
                sym_share = float(pos_syms["total_pnl"].iloc[0] / gross_pos)
                one_sym = sym_share > fraction_threshold
        if not yr_df.empty:
            pos_yrs = yr_df.loc[yr_df["total_pnl"] > 0]
            if not pos_yrs.empty:
                yr_top = pos_yrs.sort_values("total_pnl", ascending=False).iloc[0]
                yr_share = float(yr_top["total_pnl"] / gross_pos)
                one_yr = yr_share > fraction_threshold
    return {
        "one_symbol_carries": one_sym,
        "one_year_carries": one_yr,
        "total_pnl": total,
        "gross_positive_pnl": gross_pos,
        "top_symbol_share": sym_share,
        "top_year_share": yr_share,
    }


# ── Significance ────────────────────────────────────────────────────────


def binomial_p_value(wins: int, n: int, null_p: float = 0.5) -> float:
    """One-sided ``P(X >= wins | n, p = null_p)``. Null = no edge,
    coin-flip win rate. Small p ⇒ observed result unlikely under chance.
    """
    if n == 0:
        return 1.0
    return sum(math.comb(n, k) * (null_p ** k) * ((1 - null_p) ** (n - k))
                for k in range(wins, n + 1))


def bootstrap_pf_ci(trades: pd.DataFrame, *, n_resamples: int = 2000,
                     seed: int = 13) -> dict:
    """5/50/95-percentile PFs from resampling trades with replacement."""
    if trades.empty:
        return {"p05": float("nan"), "p50": float("nan"),
                "p95": float("nan"), "n_resamples": 0}
    pnl = trades["pnl"].to_numpy()
    rng = np.random.default_rng(seed)
    pfs = np.empty(n_resamples)
    for i in range(n_resamples):
        sample = rng.choice(pnl, size=len(pnl), replace=True)
        pfs[i] = profit_factor(sample)
    finite = pfs[np.isfinite(pfs)]
    if finite.size == 0:
        return {"p05": float("nan"), "p50": float("nan"),
                "p95": float("nan"), "n_resamples": n_resamples}
    return {
        "p05": float(np.percentile(finite, 5)),
        "p50": float(np.percentile(finite, 50)),
        "p95": float(np.percentile(finite, 95)),
        "n_resamples": int(n_resamples),
    }
