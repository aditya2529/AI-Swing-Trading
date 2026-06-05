"""Telegram alerter.

Reads ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID`` from
``config.py`` (which loads from ``.env``). If either is unset, send
calls are NO-OPS — they log a warning but do not raise. This lets
the EOD runner be developed locally without bot credentials.

Network failures are SWALLOWED (logged at warning level) so a
Telegram outage cannot crash the daily cron. The point of the
ping is to let ops know things are OK; a missed ping is a
SEPARATE concern (the health-check cron catches that).
"""
from __future__ import annotations

import logging
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)


def _get_credentials() -> tuple[str | None, str | None]:
    """Lazy read so tests can patch ``config`` without import-order
    headaches."""
    try:
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    except Exception:
        return None, None
    return TELEGRAM_BOT_TOKEN or None, TELEGRAM_CHAT_ID or None


def send(message: str, *, timeout: float = 10.0) -> bool:
    """Send ``message`` to the configured chat. Returns True on
    success, False on any failure (missing creds, network, HTTP
    non-200). Never raises."""
    token, chat_id = _get_credentials()
    if not token or not chat_id:
        logger.warning(
            "[telegram] credentials not set (TELEGRAM_BOT_TOKEN / "
            "TELEGRAM_CHAT_ID missing) — message dropped: %s",
            message[:140])
        return False

    # urllib over requests to keep the dependency surface minimal —
    # this module ships on the Oracle VM and the fewer pinned
    # libraries it pulls in the better.
    try:
        from urllib import request
        url = (f"https://api.telegram.org/bot{token}/sendMessage"
                f"?chat_id={quote_plus(str(chat_id))}"
                f"&text={quote_plus(message)}"
                f"&parse_mode=HTML")
        with request.urlopen(url, timeout=timeout) as resp:
            if 200 <= resp.status < 300:
                logger.info("[telegram] ping ok")
                return True
            logger.warning("[telegram] non-2xx status %s", resp.status)
            return False
    except Exception as e:
        logger.warning("[telegram] send failed: %s", e)
        return False


def send_success(*, run_date: str, equity: float, n_orders: int,
                  hh_mm: str, extra: str = "") -> bool:
    """Standard success ping format. The health-check cron parses
    nothing; ops parses by eye. Keep it short and grep-friendly."""
    msg = (f"OK {run_date} {hh_mm} IST | "
            f"equity Rs {equity:,.0f} | orders {n_orders}")
    if extra:
        msg += f" | {extra}"
    return send(msg)


def send_error(*, run_date: str, error_type: str, error_message: str,
                hh_mm: str, traceback_tail: str = "") -> bool:
    """Standard error ping. Includes the FIRST 500 chars of the
    error tail — full traceback is in the log file the cron writes."""
    msg = (f"ERROR {run_date} {hh_mm} IST | {error_type} | "
            f"{error_message[:200]}")
    if traceback_tail:
        # Trim hard so a giant traceback can't blow the 4096-char
        # Telegram message limit.
        msg += "\n\n" + traceback_tail[-500:]
    return send(msg)


def send_health_alert(*, last_ok_date: str | None,
                       today_date: str) -> bool:
    msg = (f"HEALTH ALERT {today_date} IST | "
            f"no success run since {last_ok_date or 'never'}. "
            f"Investigate the daily cron.")
    return send(msg)
