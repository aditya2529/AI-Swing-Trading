"""WEEKLY paper-trading runner — the live forward TEST of weekly trading.

Runs the SAME strategy as the monthly bot (``SmidMomentumStrategy``) but
with ``rebalance_freq="weekly"`` so the book rotates every ISO week. This
is a clean head-to-head: monthly vs weekly, identical universe + data +
risk controls, the ONLY difference is cadence.

ISOLATION (so this can NEVER affect the monthly bot)
====================================================
This module ONLY imports and calls the EXISTING ``run_eod`` — it changes
nothing in the monthly path. It overrides three paths so the weekly bot
has its own everything:

    * ledger  : paper_ledger_weekly.db   (separate from paper_ledger.db)
    * backups : backups/weekly/
    * logs    : logs/weekly/

``market_data.db`` is SHARED but opened READ-ONLY by the runner, so the
weekly bot cannot corrupt it. The safety guard (PAPER_MODE), crash-safe
wrapper, LAW-7 backup, bar-finality check and idempotency lock are all
inherited unchanged from ``run_eod``.

Cron: 15:50 IST Mon-Fri (5 min after the monthly run, so the two Telegram
pings arrive separately).
"""
from __future__ import annotations

import sys

from live.eod_runner import run_eod, RunnerPaths, PROJECT_ROOT


# ── Weekly strategy: same as monthly, weekly cadence ──────────────────
def _weekly_strategy_factory():
    from signals.smid_momentum import SmidMomentumStrategy
    return SmidMomentumStrategy(rebalance_freq="weekly")


def _weekly_universe() -> list[str]:
    # SAME universe as monthly — apples-to-apples comparison.
    from data.universe import SMID_UNIVERSE
    return list(SMID_UNIVERSE)


# ── Fully isolated paths (own ledger / logs / backups) ────────────────
WEEKLY_PATHS = RunnerPaths(
    paper_ledger_db=PROJECT_ROOT / "paper_ledger_weekly.db",
    backups_dir=PROJECT_ROOT / "backups" / "weekly",
    logs_dir=PROJECT_ROOT / "logs" / "weekly",
)


if __name__ == "__main__":   # pragma: no cover
    sys.exit(run_eod(
        strategy_factory=_weekly_strategy_factory,
        universe=_weekly_universe(),
        paths=WEEKLY_PATHS,
    ))
