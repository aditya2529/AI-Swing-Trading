"""Replay-as-live EOD runner — the CORRECT paper-trading engine.

The first runner (``eod_runner.py``) decided orders but never filled them,
so positions never formed and equity stayed flat. This runner fixes that
by reusing the PROVEN, look-ahead-safe backtest engine (``run_replay``):

  * It replays the strategy forward FROM ``GO_LIVE_DATE`` (starting flat at
    ₹5,00,000), using the full price history only for indicator warm-up.
  * The engine handles fills (decide-at-T-close -> fill-at-T+1-open),
    sizing, slippage, stops, sector caps — all already verified.
  * The result (equity curve, open positions, closed trades) IS the live
    paper portfolio. We snapshot it into the ledger each day.

Deterministic + self-healing: re-running on the same data reproduces the
exact same book, so a missed/duplicate day can never corrupt state.

Usage:  python -m live.replay_runner monthly   (-> paper_ledger.db)
        python -m live.replay_runner weekly    (-> paper_ledger_weekly.db)
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

import pandas as pd

from live import paper_ledger as ledger, telegram
from live.safety_guard import assert_paper_mode
from live.eod_runner import (
    RunnerPaths, PROJECT_ROOT, now_ist, today_ist_date_str,
    load_universe_from_market_db, bar_finality_check, backup_paper_ledger,
)

# Paper-trade clock start. Set back a few weeks so the dashboard shows a
# real equity curve + current holdings immediately (the engine is causal /
# look-ahead-safe, so this stretch is an honest deterministic simulation);
# it then continues LIVE forward from today as new bars arrive.
GO_LIVE_DATE = "2026-05-01"

# Portfolio risk caps — MATCH the SMOM-3 backtest exactly so live == tested.
CFG = dict(max_positions=15, max_per_sector=5, max_heat=0.20,
           slippage_pct=0.004)


def _persist(db, *, open_positions: dict, trades_df: pd.DataFrame,
             final_cash: float, equity_curve: pd.Series) -> None:
    """Snapshot the replay result into the ledger (overwrite — deterministic)."""
    from backtesting.replay import Position  # noqa: F401  (type only)
    with ledger.get_connection(db) as conn:
        conn.execute("DELETE FROM positions")
        conn.execute("DELETE FROM trades")
    # Positions
    for sym, p in open_positions.items():
        ledger.upsert_position(db, ledger.StoredPosition(
            symbol=sym,
            entry_date=pd.Timestamp(p.entry_date).date().isoformat(),
            entry_price=float(p.entry_price), shares=int(p.shares),
            stop=float(p.stop), risk_per_share=float(p.risk_per_share),
            cost_basis=float(p.cost_basis), bars_held=int(p.bars_held),
            highest_high=float(p.highest_high),
            highest_close=float(p.highest_close),
            last_close=float(p.last_close)))
    # Trades
    for _, t in trades_df.iterrows():
        ledger.append_trade(
            db, symbol=t["symbol"],
            entry_date=pd.Timestamp(t["entry_date"]).date().isoformat(),
            exit_date=pd.Timestamp(t["exit_date"]).date().isoformat(),
            entry_price=float(t["entry_price"]), exit_price=float(t["exit_price"]),
            shares=int(t["shares"]), pnl=float(t["pnl"]),
            return_pct=float(t["return"]), bars_held=int(t["bars_held"]),
            exit_reason=str(t["exit_reason"]))
    # Equity curve (cash/mtm cols are unused by the dashboard -> 0 for history)
    for ts, val in equity_curve.items():
        ledger.upsert_equity(db, pd.Timestamp(ts).date().isoformat(),
                             equity=float(val), cash=0.0, mtm=0.0)
    # Accurate latest split + cash singleton.
    if len(equity_curve):
        last_date = pd.Timestamp(equity_curve.index[-1]).date().isoformat()
        last_eq = float(equity_curve.iloc[-1])
        ledger.upsert_equity(db, last_date, equity=last_eq,
                             cash=float(final_cash), mtm=last_eq - float(final_cash))
    ledger.set_cash(db, float(final_cash))


def run_replay_live(*, strategy_factory, universe, paths: RunnerPaths,
                    today_override: str | None = None) -> int:
    from config import INITIAL_CAPITAL
    from backtesting.replay import run_replay

    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = paths.logs_dir / f"replay_{today_override or today_ist_date_str()}.log"
    handlers = [logging.FileHandler(log_path, mode="a", encoding="utf-8"),
                logging.StreamHandler(sys.stdout)]
    for h in handlers:
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s"))
    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(logging.INFO)
    logger = logging.getLogger("replay_runner")

    run_date = today_override or today_ist_date_str()
    hh_mm = now_ist().strftime("%H:%M")
    logger.info("=== REPLAY runner start | run_date=%s | %s IST", run_date, hh_mm)

    try:
        assert_paper_mode()
        logger.info("safety guard: PAPER_MODE=1 confirmed.")
        ledger.init_ledger(paths.paper_ledger_db, initial_capital=INITIAL_CAPITAL)

        prior = ledger.get_run(paths.paper_ledger_db, run_date)
        if prior is not None and prior.status in ("ok", "no_op"):
            logger.info("idempotency: %s already done (%s). NO-OP.", run_date, prior.status)
            return 0

        backup = backup_paper_ledger(paths)
        if backup:
            logger.info("law7 backup -> %s", backup.name)

        target_date = pd.Timestamp(run_date)
        data = load_universe_from_market_db(paths, symbols=universe)
        logger.info("loaded %d symbols.", len(data))
        ok, frac, n_with = bar_finality_check(data, target_date=target_date)
        logger.info("bar-finality: %d/%d have a %s bar (frac=%.2f)", n_with, len(data), run_date, frac)
        if not ok:
            msg = (f"BAR FINALITY FAILED: only {frac:.0%} of {len(data)} symbols "
                   f"have a {run_date} bar. Run the data feed first.")
            logger.warning(msg)
            ledger.upsert_run(paths.paper_ledger_db, ledger.RunRecord(
                run_date=run_date, run_at_utc=datetime.now(timezone.utc).isoformat(),
                status="error", n_orders=0, equity=None, cash=None, error_message=msg))
            telegram.send_error(run_date=run_date, error_type="BarFinality",
                                error_message=msg, hh_mm=hh_mm)
            return 1

        # THE ENGINE, run forward from go-live (starts flat at INITIAL_CAPITAL).
        res = run_replay(data, strategy_factory(), initial_capital=INITIAL_CAPITAL,
                         start=pd.Timestamp(GO_LIVE_DATE), end=pd.Timestamp(run_date),
                         close_at_end=False, **CFG)
        eqc = res["equity_curve"]
        open_pos = res["open_positions"]
        trades_df = res["trades"]
        final_cash = res["final_cash"]
        equity = float(eqc.iloc[-1]) if len(eqc) else float(INITIAL_CAPITAL)
        logger.info("engine: equity Rs %.0f | %d open positions | %d closed trades",
                    equity, len(open_pos), len(trades_df))

        _persist(paths.paper_ledger_db, open_positions=open_pos, trades_df=trades_df,
                 final_cash=final_cash, equity_curve=eqc)
        logger.info("ledger snapshot written.")

        ledger.upsert_run(paths.paper_ledger_db, ledger.RunRecord(
            run_date=run_date, run_at_utc=datetime.now(timezone.utc).isoformat(),
            status="ok", n_orders=len(open_pos), equity=equity, cash=final_cash,
            error_message=None))
        telegram.send_success(run_date=run_date, equity=equity,
                              n_orders=len(open_pos), hh_mm=hh_mm,
                              extra=f"{len(open_pos)} held")
        logger.info("=== REPLAY runner OK")
        return 0

    except Exception as e:  # noqa: BLE001
        import traceback
        tb = traceback.format_exc()
        logger.error("CRASH-SAFE: %s: %s\n%s", type(e).__name__, e, tb)
        try:
            telegram.send_error(run_date=run_date, error_type=type(e).__name__,
                                error_message=str(e), hh_mm=hh_mm, traceback_tail=tb)
        except Exception:  # noqa: BLE001
            pass
        return 1


def _paths_for(mode: str) -> RunnerPaths:
    if mode == "weekly":
        return RunnerPaths(paper_ledger_db=PROJECT_ROOT / "paper_ledger_weekly.db",
                           backups_dir=PROJECT_ROOT / "backups" / "weekly",
                           logs_dir=PROJECT_ROOT / "logs" / "weekly")
    return RunnerPaths()


def _factory_for(mode: str):
    from signals.smid_momentum import SmidMomentumStrategy
    freq = "weekly" if mode == "weekly" else "monthly"
    return lambda: SmidMomentumStrategy(rebalance_freq=freq)


def _universe() -> list[str]:
    from data.universe import SMID_UNIVERSE
    return list(SMID_UNIVERSE)


if __name__ == "__main__":   # pragma: no cover
    mode = sys.argv[1] if len(sys.argv) > 1 else "monthly"
    sys.exit(run_replay_live(strategy_factory=_factory_for(mode),
                             universe=_universe(), paths=_paths_for(mode)))
