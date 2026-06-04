"""Causal daily engine-replay backtest harness — the honest harness.

This is the swing-project port of the intraday project's crown-jewel
engine-replay, rebuilt for DAILY bars and a clean strategy-callback
interface (the intraday version monkeypatched a specific live engine; we
don't have one yet, and the look-ahead defence shouldn't depend on one).

THE LOOK-AHEAD DEFENCE (verified for daily bars — see README + the kickoff)
--------------------------------------------------------------------------
Daily bars are date-labelled full-session records: ``bar[T]`` is the
complete OHLC for trading day T, and ``close[T] != open[T+1]`` (verified
against the DB; real overnight gaps). So:

    decide at day-T CLOSE  →  fill at day-T+1 OPEN.

At the decision moment (after close on day T) ``bar[T]`` has fully
resolved, so the strategy may use bars up to and INCLUDING T. The single
boundary that must never be crossed is: the strategy must not see any bar
with index > T. That is enforced HERE, by ``BarView`` (decisions only ever
receive a slice ``df.index <= cutoff``) and by the execution model (every
fill is the NEXT day's open, computed by the harness — never handed to the
strategy). This is why we do NOT blindly copy the intraday ``index <
clock``: for daily bars the decision legitimately includes day T; the
strictly-earlier boundary lands on the FILL day (T+1), not the decision.

Execution model (deliberately conservative / honest):
    * Entries decided at T close fill at T+1 open × (1 + slippage).
    * Exits decided at T close fill at T+1 open × (1 − slippage).
    * Brokerage applied on both legs.
    * No intrabar stop fills — a gap through a stop fills at the next
      open (worse, never better). This understates some exits but never
      overstates edge. (An intrabar-fill execution refinement can be
      added later and re-validated against this baseline.)

The harness writes NOTHING to any database — it operates on in-memory
per-symbol DataFrames, so a replay can never corrupt live state.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

import pandas as pd

from backtesting.metrics import compute_all
from config import (
    BROKERAGE_PCT, INITIAL_CAPITAL, MAX_PER_SECTOR, MAX_PORTFOLIO_HEAT,
    MAX_POSITIONS, MAX_RISK_PCT, PERIODS_PER_YEAR, SECTORS, SLIPPAGE_PCT,
)


# ── Vol-scaling helper (MOM-5) ─────────────────────────────────────────


def _compute_exposure_mult(equity_history: list, vol_target_annual: float,
                             vol_window: int) -> float:
    """Vol-scaled exposure multiplier (Barroso-Santa-Clara momentum-crash
    fix). Compares the trailing realized annualized volatility of the
    daily equity-return series against a fixed target.

    Returns a multiplier in ``[0.0, 1.0]``:
        exposure_mult = clip(vol_target_annual / sigma_annual, 0.0, 1.0)

    The CAP at 1.0 prevents leverage (this is a long-only cash system).
    Fallback to 1.0 (no scaling) when:
        * fewer than ``vol_window + 1`` equity points are available
          (warm-up — exactly enough returns to compute std without bias);
        * trailing realized vol is non-positive (degenerate flat series).

    The input ``equity_history`` is a sequence of close-MTM equity values
    in CHRONOLOGICAL order — the harness's own ``equity_rows`` list,
    which is appended exactly once per timeline tick AFTER that tick's
    close-MTM step. Calling this BEFORE today's MTM is appended is what
    makes the read causal (today's close hasn't happened yet at the
    entry-fill moment).
    """
    if len(equity_history) < vol_window + 1:
        return 1.0
    recent = equity_history[-(vol_window + 1):]
    rets = [recent[i + 1] / recent[i] - 1.0
            for i in range(len(recent) - 1)
            if recent[i] > 0]
    if len(rets) < 2:
        return 1.0
    sigma_daily = statistics.stdev(rets)
    sigma_annual = sigma_daily * math.sqrt(PERIODS_PER_YEAR)
    if sigma_annual <= 0:
        return 1.0
    return max(0.0, min(vol_target_annual / sigma_annual, 1.0))


# ── Orders the strategy can return ──────────────────────────────────────

@dataclass(frozen=True)
class EnterOrder:
    """Open a long in ``symbol`` with an initial hard stop at ``stop``
    (an absolute price below the expected entry). The harness sizes the
    position by fixed-fractional risk and fills at the next day's open."""
    symbol: str
    stop: float
    reason: str = ""


@dataclass(frozen=True)
class ExitOrder:
    """Close the open position in ``symbol`` at the next day's open."""
    symbol: str
    reason: str = ""


def _order_key(order) -> tuple:
    """Deterministic, comparable serialisation of an order (for tests/logs)."""
    if isinstance(order, EnterOrder):
        return ("enter", order.symbol, round(float(order.stop), 4))
    if isinstance(order, ExitOrder):
        return ("exit", order.symbol, "")
    raise TypeError(f"unknown order type: {type(order)!r}")


# ── Position bookkeeping (neutral stats the strategy may read) ───────────

@dataclass
class Position:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    shares: int
    stop: float
    risk_per_share: float
    cost_basis: float            # incl. entry brokerage
    bars_held: int = 0
    highest_high: float = 0.0    # since entry (for a strategy's trailing stop)
    highest_close: float = 0.0
    # Last close actually observed for this position. Used by the MTM
    # step as a stale-carry when the symbol has no bar on a given
    # timeline date (the replay timeline is the UNION of all symbol
    # indexes, so a missing bar for ONE position on a date does not
    # drop that date from the timeline — without stale-carry, MTM
    # would silently exclude the position, producing a spurious
    # equity-curve dip toward cash). Initialised to entry_price at
    # fill so day-0 MTM is well-defined even before any close-bar
    # update has run.
    last_close: float = 0.0


@dataclass
class Book:
    """Read-only-ish snapshot handed to the strategy each decision day."""
    cash: float
    equity: float
    positions: dict  # symbol -> Position

    def has_position(self, symbol: str) -> bool:
        return symbol in self.positions

    def open_symbols(self) -> list:
        return list(self.positions)


# ── The causal window ───────────────────────────────────────────────────

class BarView:
    """A causal view over per-symbol daily bars as of decision day
    ``cutoff``. The ONLY data access a strategy gets — and it can never
    reach a bar with index > cutoff."""

    def __init__(self, data: dict, cutoff: pd.Timestamp):
        self._data = data
        self.cutoff = cutoff

    def symbols(self) -> list:
        return list(self._data)

    def has_bar(self, symbol: str) -> bool:
        """True iff ``symbol`` has a bar stamped exactly on the cutoff day."""
        df = self._data.get(symbol)
        return df is not None and self.cutoff in df.index

    def history(self, symbol: str) -> pd.DataFrame:
        """All bars for ``symbol`` up to and INCLUDING the cutoff day.

        Returns an independent copy, so a strategy can never mutate the
        harness's source frames mid-replay (defensive isolation)."""
        df = self._data.get(symbol)
        if df is None:
            return pd.DataFrame()
        return df.loc[df.index <= self.cutoff].copy()

    def latest(self, symbol: str):
        """The most recent causal row (day T), or None."""
        h = self.history(symbol)
        return None if h.empty else h.iloc[-1]


# ── The replay loop ─────────────────────────────────────────────────────

def run_replay(data: dict, strategy, *, initial_capital: float = INITIAL_CAPITAL,
               start=None, end=None, risk_pct: float = MAX_RISK_PCT,
               max_positions: int = MAX_POSITIONS,
               max_per_sector: int = MAX_PER_SECTOR,
               max_heat: float = MAX_PORTFOLIO_HEAT,
               dd_cap_pct: float | None = None,
               vol_target_annual: float | None = None,
               vol_window: int = 63,
               close_at_end: bool = True,
               record_decisions: bool = False) -> dict:
    """Replay ``strategy`` over per-symbol daily ``data``.

    Args:
        data: ``{symbol: DataFrame}`` with a DatetimeIndex and columns
            [open, high, low, close, volume]. Pass FULL history (including
            indicator warm-up) — the strategy receives a causal slice.
        strategy: object with ``decide(view: BarView, book: Book) ->
            list[EnterOrder | ExitOrder]``. Called once per day at the
            close; returned orders fill at the NEXT day's open.
        start, end: optional inclusive bounds on the decision timeline.
        risk_pct, max_positions, max_per_sector, max_heat: portfolio risk
            controls (LAW 6).
        dd_cap_pct: portfolio-level drawdown circuit-breaker. ``None``
            (default) disables the cap and reproduces prior behaviour
            byte-for-byte (regression-safe). Otherwise, on each day-T
            open AFTER any exit fills, equity = cash + MTM-at-open is
            compared against ``peak_equity × (1 - dd_cap_pct)``. If the
            threshold is breached, that day's pending NEW ENTRIES are
            dropped (the strategy's order list for those is discarded;
            no shares purchased, no cash spent). Exits ALWAYS process.
            The cap re-arms symmetrically — once equity-at-open rises
            back above the threshold, entries resume. ``peak_equity``
            is the running max of the close-MTM equity series the
            harness already computes, so the comparison uses only
            information knowable at day-T open (causal — no look-ahead).
        vol_target_annual: Barroso-Santa-Clara vol-scaled exposure
            (MOM-5). ``None`` (default) disables vol scaling and
            reproduces prior behaviour byte-for-byte (regression-safe).
            Otherwise, at every day-T entry-fill moment, the trailing
            realized annualized volatility of the close-MTM equity
            series over ``vol_window`` days is compared against the
            target; ``exposure_mult = clip(target / realized, 0.0, 1.0)``
            scales the shares of each new entry that day (no leverage —
            the cap at 1.0 means we never SCALE UP in calm periods,
            only down in turbulent ones). Causal: ``equity_rows`` is
            appended AFTER each day's close MTM step, so the vol read
            at the day-T open uses only equity through day T-1 close.
            Exits and the DD cap are unaffected. When fewer than
            ``vol_window + 1`` equity points exist or realized vol is
            zero, ``exposure_mult = 1.0`` (no scaling).
        vol_window: rolling window (days) for the realized-vol read.
            Default 63 (~3 trading months — the
            Barroso-Santa-Clara standard). Ignored when
            ``vol_target_annual is None``.
        close_at_end: force-close open positions at the final bar's close
            so end-of-data positions become realised trades.
        record_decisions: also return the per-day decision log (used by the
            look-ahead regression tests).

    Returns dict: metrics, trades (DataFrame), equity_curve (Series),
    per-day decisions (if requested), plus run stats.
    """
    if not data:
        raise ValueError("run_replay: empty data")

    # Unified, sorted decision timeline across all symbols.
    all_dates = sorted(set().union(*[set(df.index) for df in data.values()]))
    timeline = [t for t in all_dates
                if (start is None or t >= start) and (end is None or t <= end)]
    # Map date -> next date (for next-open fills).
    next_of = {t: all_dates[i + 1] for i, t in enumerate(all_dates[:-1])}

    cash = float(initial_capital)
    positions: dict = {}
    trades: list = []
    equity_rows: list = []
    decisions_log: list = []
    pending: list = []   # orders decided yesterday, to fill at today's open
    # Per-day exposure multiplier (vol-scaling diagnostic). Populated
    # only when ``vol_target_annual is not None``; otherwise stays empty
    # so the default-off code path is byte-equivalent to prior behaviour.
    exposure_mults_log: list = []
    # Running peak of the close-MTM equity series. Used only by the
    # ``dd_cap_pct`` halt check; updated after each day's close MTM step.
    # Seeded at ``initial_capital`` so the very first day has a baseline
    # peak to compare against (cap can never trigger on day 1 because
    # equity-at-open == initial_capital == peak).
    peak_equity = float(initial_capital)

    def _price(sym, t, col):
        df = data.get(sym)
        if df is None or t not in df.index:
            return None
        return float(df.at[t, col])

    def _sector(sym):
        return SECTORS.get(sym, sym)

    def _open_risk():
        return sum(p.shares * p.risk_per_share for p in positions.values())

    for t in timeline:
        # 1) Fill yesterday's orders at TODAY's open. Exits first (free cash),
        #    then entries. Orders whose symbol has no bar today are dropped.
        exits = [o for o in pending if isinstance(o, ExitOrder)]
        enters = [o for o in pending if isinstance(o, EnterOrder)]

        for o in exits:
            pos = positions.get(o.symbol)
            op = _price(o.symbol, t, "open")
            if pos is None or op is None:
                continue
            xfill = op * (1.0 - SLIPPAGE_PCT)
            proceeds = pos.shares * xfill
            proceeds_net = proceeds * (1.0 - BROKERAGE_PCT)
            cash += proceeds_net
            pnl = proceeds_net - pos.cost_basis
            trades.append({
                "symbol": o.symbol,
                "entry_date": pos.entry_date, "exit_date": t,
                "entry_price": pos.entry_price, "exit_price": xfill,
                "shares": pos.shares, "pnl": pnl,
                "return": pnl / pos.cost_basis if pos.cost_basis else 0.0,
                "bars_held": pos.bars_held, "exit_reason": o.reason,
            })
            del positions[o.symbol]

        # ── DD-cap halt check (entries only; exits are unaffected) ──
        # Causal: cash reflects today's exit proceeds; MTM uses today's
        # open prices. Both are known at the day-T entry-fill moment.
        # ``peak_equity`` is the running max through yesterday's close
        # (today's close hasn't happened yet). When breached, drop today's
        # entries — the symmetric re-arm is implicit: tomorrow we check
        # again, and entries resume the moment equity-at-open exceeds the
        # threshold.
        if dd_cap_pct is not None and enters:
            mtm_at_open = sum(
                p.shares * (_price(s, t, "open") or p.entry_price)
                for s, p in positions.items())
            equity_at_open = cash + mtm_at_open
            threshold = peak_equity * (1.0 - dd_cap_pct)
            if equity_at_open <= threshold:
                enters = []   # halted — new exposure blocked today

        # ── Vol-scaling exposure multiplier (entries only) ─────────
        # Causal: ``equity_rows`` holds close-MTM equity through DAY
        # T-1 (today's close hasn't happened yet). Computed every day
        # the vol scaler is on, regardless of whether ``enters`` is
        # non-empty — gives a complete per-day exposure trace for the
        # MOM-5 diagnostic. Applied to today's entries only.
        exposure_mult = 1.0
        if vol_target_annual is not None:
            equity_series = [e for _, e in equity_rows]
            exposure_mult = _compute_exposure_mult(
                equity_series, vol_target_annual, vol_window)
            exposure_mults_log.append((t, exposure_mult))

        for o in enters:
            if o.symbol in positions:
                continue
            op = _price(o.symbol, t, "open")
            if op is None:
                continue
            fill = op * (1.0 + SLIPPAGE_PCT)
            risk_per_share = fill - o.stop
            if risk_per_share <= 0:
                continue  # invalid stop (not below entry) — cannot size
            # Equity at the moment of fill (cash + MTM at today's open).
            mtm = sum(p.shares * (_price(s, t, "open") or p.entry_price)
                      for s, p in positions.items())
            equity_now = cash + mtm
            # Risk controls (LAW 6).
            if len(positions) >= max_positions:
                continue
            if sum(1 for p in positions.values()
                   if _sector(p.symbol) == _sector(o.symbol)) >= max_per_sector:
                continue
            shares = int((risk_pct * equity_now) // risk_per_share)
            if shares <= 0:
                continue
            # Vol-scaled exposure (MOM-5): trim shares BEFORE heat / cash
            # checks so those caps apply to the post-scaling size.
            # exposure_mult is 1.0 when vol_target_annual is None (no-op).
            if vol_target_annual is not None and exposure_mult < 1.0:
                shares = int(shares * exposure_mult)
                if shares <= 0:
                    continue
            if (_open_risk() + shares * risk_per_share) > max_heat * equity_now:
                continue
            cost = shares * fill
            cost_total = cost * (1.0 + BROKERAGE_PCT)
            if cost_total > cash:
                # Scale down to available cash rather than skip outright.
                shares = int(cash // (fill * (1.0 + BROKERAGE_PCT)))
                if shares <= 0:
                    continue
                cost_total = shares * fill * (1.0 + BROKERAGE_PCT)
            cash -= cost_total
            positions[o.symbol] = Position(
                symbol=o.symbol, entry_date=t, entry_price=fill, shares=shares,
                stop=o.stop, risk_per_share=risk_per_share, cost_basis=cost_total,
                bars_held=0, highest_high=_price(o.symbol, t, "high") or fill,
                highest_close=_price(o.symbol, t, "close") or fill,
                last_close=_price(o.symbol, t, "close") or fill,
            )

        # 2) Update per-position running stats at today's close, mark equity.
        #
        # STALE-CARRY MTM: when ``_price(close)`` is None for a position on
        # the current date (the symbol has no bar today — e.g. asymmetric
        # NSE coverage like muhurat sessions, partial-coverage holidays, or
        # the last DB date when one symbol's data ends earlier than
        # another's), we mark the position at its LAST KNOWN CLOSE
        # (``p.last_close``). Without this carry, the position contributes
        # 0 to MTM on missing-bar days and the equity curve dips toward
        # cash — a spurious one-day artifact that is NOT a real economic
        # drawdown. ``p.bars_held`` and ``p.highest_close`` still update
        # only on REAL bars (no fictitious holding time / no fake highs);
        # only the displayed MTM is stale-carried.
        mtm = 0.0
        for p in positions.values():
            c = _price(p.symbol, t, "close")
            hi = _price(p.symbol, t, "high")
            if c is not None:
                p.bars_held += 1
                p.highest_close = max(p.highest_close, c)
                p.last_close = c
                mtm += p.shares * c
            else:
                mtm += p.shares * p.last_close
            if hi is not None:
                p.highest_high = max(p.highest_high, hi)
        equity = cash + mtm
        equity_rows.append((t, equity))
        # Update the running peak for tomorrow's DD-cap check. Always
        # tracked regardless of ``dd_cap_pct`` — cheap, and keeps the
        # default-off path byte-equivalent (the peak just isn't consulted).
        if equity > peak_equity:
            peak_equity = equity

        # 3) Decision at today's close on a strictly-causal view.
        view = BarView(data, cutoff=t)
        book = Book(cash=cash, equity=equity, positions=positions)
        orders = list(strategy.decide(view, book) or [])
        if record_decisions:
            decisions_log.append((t, sorted(_order_key(o) for o in orders)))

        # 4) Queue today's orders to fill at the NEXT day's open. If there is
        #    no next day, they expire unfilled (honest: no fill price exists).
        pending = orders if t in next_of else []

    # Force-close any open positions at the final available close.
    if close_at_end and positions and timeline:
        t_last = timeline[-1]
        for sym, pos in list(positions.items()):
            c = _price(sym, t_last, "close")
            if c is None:
                continue
            proceeds_net = pos.shares * c * (1.0 - BROKERAGE_PCT)
            cash += proceeds_net
            pnl = proceeds_net - pos.cost_basis
            trades.append({
                "symbol": sym, "entry_date": pos.entry_date, "exit_date": t_last,
                "entry_price": pos.entry_price, "exit_price": c,
                "shares": pos.shares, "pnl": pnl,
                "return": pnl / pos.cost_basis if pos.cost_basis else 0.0,
                "bars_held": pos.bars_held, "exit_reason": "end_of_data",
            })
            del positions[sym]

    trades_df = pd.DataFrame(trades, columns=[
        "symbol", "entry_date", "exit_date", "entry_price", "exit_price",
        "shares", "pnl", "return", "bars_held", "exit_reason",
    ])
    equity_curve = (pd.Series(dict(equity_rows)).sort_index()
                    if equity_rows else pd.Series(dtype=float))
    metrics = compute_all(trades_df, equity_curve, periods_per_year=PERIODS_PER_YEAR)

    result = {
        "metrics": metrics,
        "trades": trades_df,
        "equity_curve": equity_curve,
        "final_cash": cash,
        "n_days": len(timeline),
        "start": timeline[0] if timeline else None,
        "end": timeline[-1] if timeline else None,
    }
    if record_decisions:
        result["decisions"] = decisions_log
    if vol_target_annual is not None:
        # Per-day exposure multiplier trace — only present when the vol
        # scaler is on, so the default-off return shape is unchanged.
        result["exposure_mults"] = exposure_mults_log
    return result
