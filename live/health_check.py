"""Health-check cron — alerts if no daily run succeeded by a cutoff.

Run by a SEPARATE cron (~16:30 IST Mon-Fri) so that even a full crash
of the EOD runner cannot suppress this alert. Reads the paper-ledger
``runs`` table; if no ``ok`` / ``no_op`` row exists for today AND
today is a market day, send a Telegram health alert.

Exits 0 always (the alert IS the output). Returns 1 only on truly
catastrophic failure (the alert itself failing).
"""
from __future__ import annotations

import logging
import sys
from datetime import date as date_cls
from pathlib import Path

from live import paper_ledger as ledger
from live import telegram
from live.eod_runner import (
    RunnerPaths, now_ist, today_ist_date_str,
)


def is_market_day(d: date_cls) -> bool:
    """Conservative: Mon-Fri = market day. NSE holiday calendar is
    not maintained here; the daily runner's ``bar_finality_check``
    catches actual NSE closures by detecting missing bars.
    """
    return d.weekday() < 5


def main(paths: RunnerPaths | None = None) -> int:
    paths = paths or RunnerPaths()
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = paths.logs_dir / "health_check.log"
    log_handlers = [
        logging.FileHandler(log_path, mode="a", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    for h in log_handlers:
        h.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s | %(message)s"))
    root_logger = logging.getLogger()
    root_logger.handlers = log_handlers
    root_logger.setLevel(logging.INFO)
    logger = logging.getLogger("health_check")

    today = now_ist().date()
    if not is_market_day(today):
        logger.info("today %s is weekend — health check silent.",
                      today.isoformat())
        return 0

    if not paths.paper_ledger_db.exists():
        logger.warning("paper-ledger DB does NOT exist at %s — "
                          "alerting.", paths.paper_ledger_db)
        try:
            telegram.send_health_alert(last_ok_date=None,
                                          today_date=today.isoformat())
        except Exception as e:
            logger.error("could not send health alert: %s", e)
            return 1
        return 0

    today_str = today.isoformat()
    today_run = ledger.get_run(paths.paper_ledger_db, today_str)
    if today_run is not None and today_run.status in ("ok", "no_op"):
        logger.info("today %s ran ok (status=%s, n_orders=%d).",
                      today_str, today_run.status, today_run.n_orders)
        return 0

    last_ok = ledger.last_successful_run_date(paths.paper_ledger_db)
    logger.warning("health alert: no ok/no_op run for today %s "
                      "(last_ok=%s)", today_str, last_ok)
    try:
        telegram.send_health_alert(last_ok_date=last_ok,
                                      today_date=today_str)
    except Exception as e:
        logger.error("could not send health alert: %s", e)
        return 1
    return 0


if __name__ == "__main__":   # pragma: no cover
    sys.exit(main())
