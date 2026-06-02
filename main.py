"""AI Swing Trading — CLI entry point.

Subcommands (Phase 0):
    backfill   Fetch + store daily OHLCV for the universe (Step D; needs creds
               for --source upstox).
    replay     Run the causal engine-replay backtest (enabled once the
               look-ahead gate passes and a strategy exists).
    test       Run the test suite (the look-ahead gate + contract tests).

Run `python main.py <subcommand> -h` for details.
"""
from __future__ import annotations

import argparse
import logging
import sys


def _cmd_backfill(args: argparse.Namespace) -> int:
    from config import DEFAULT_SYMBOLS, DEFAULT_YEARS, REGIME_INDEX, VIX_SYMBOL
    from data.ingestion import fetch_and_store

    symbols = args.symbols or (DEFAULT_SYMBOLS + [REGIME_INDEX, VIX_SYMBOL])
    years = args.years or DEFAULT_YEARS
    failures: list[str] = []
    for sym in symbols:
        try:
            df = fetch_and_store(sym, years=years, resolution="1d",
                                 source=args.source)
            logging.info("backfill ok: %s (%d bars)", sym, len(df))
        except Exception as exc:  # noqa: BLE001 — report and continue
            logging.error("backfill FAILED: %s — %s", sym, exc)
            failures.append(sym)
    if failures:
        logging.error("%d symbol(s) failed: %s", len(failures), ", ".join(failures))
        return 1
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    logging.error(
        "replay is not enabled yet: the look-ahead gate "
        "(tests/test_lookahead_regression.py) must pass and a strategy must "
        "exist before any backtest is trustworthy. Run `python main.py test`."
    )
    return 2


def _cmd_test(args: argparse.Namespace) -> int:
    import pytest
    return pytest.main(["-q"] + (args.pytest_args or []))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="main.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    pb = sub.add_parser("backfill", help="Fetch + store daily OHLCV.")
    pb.add_argument("--symbols", nargs="*", help="Override the symbol list.")
    pb.add_argument("--years", type=int, help="Years of history (default: config).")
    pb.add_argument("--source", choices=["yfinance", "upstox"], default=None,
                    help="Force a data source (default: config.DATA_ADAPTER).")
    pb.set_defaults(func=_cmd_backfill)

    pr = sub.add_parser("replay", help="Run the engine-replay backtest.")
    pr.set_defaults(func=_cmd_replay)

    pt = sub.add_parser("test", help="Run the test suite.")
    pt.add_argument("pytest_args", nargs="*", help="Extra args passed to pytest.")
    pt.set_defaults(func=_cmd_test)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
