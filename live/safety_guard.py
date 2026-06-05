"""PAPER-ONLY safety guard.

Hard assertion that no live-trading code path exists in this project.
We are deliberately running the strategy in paper mode on Oracle:
positions / trades / equity are all recorded in a LOCAL SQLite ledger,
no broker API is reached, and no Order objects ever leave the
process.

The guard runs at process start. If any of the following are TRUE
the process exits non-zero IMMEDIATELY (before any state-changing
work):
    * an ``UPSTOX_ACCESS_TOKEN`` / equivalent broker credential
      is set in the environment (we never want it accidentally
      picked up);
    * a ``PAPER_MODE`` env var is set to anything other than ``"1"``
      (must be explicitly opted in to paper mode every run);
    * any importable broker-order module (``upstox_orders``,
      ``broker_client``, etc.) exists on the path.

Designed to fail closed. Better to refuse to start than to
accidentally place a live order.
"""
from __future__ import annotations

import importlib.util
import os
import sys

# Module names that, if importable, would indicate a broker order path
# is reachable. We do NOT have any of these in this project — the
# guard exists so a future accidental addition fails the daily cron
# until it is explicitly removed or the guard is consciously updated.
_FORBIDDEN_MODULES = (
    "upstox_orders",
    "upstox.orders",
    "broker_client",
    "live_broker",
    "zerodha_kite_orders",
    "place_order",
)

# Environment variables that, if set, suggest broker credentials are
# in scope. The guard refuses to run with any of these populated.
_FORBIDDEN_ENV_VARS = (
    "UPSTOX_ACCESS_TOKEN",
    "UPSTOX_LIVE_TOKEN",
    "ZERODHA_API_KEY",
    "ZERODHA_API_SECRET",
    "BROKER_LIVE_KEY",
)


class SafetyGuardError(RuntimeError):
    """Raised when the paper-only guard refuses to allow the process
    to continue. Catch ONLY in the top-level runner, log + alert,
    exit non-zero."""


def assert_paper_mode() -> None:
    """Hard-fail check. Run at process start before any DB / network
    work. Raises SafetyGuardError on any violation; the runner's
    crash-safe wrapper converts that into a Telegram alert + non-zero
    exit."""
    reasons: list[str] = []

    # PAPER_MODE must be explicitly opted in to "1" (string).
    paper_mode = os.getenv("PAPER_MODE", "")
    if paper_mode != "1":
        reasons.append(
            f"PAPER_MODE env var must be exactly '1' to run "
            f"(got {paper_mode!r}). Refusing to start to prevent "
            f"accidental live trading.")

    for env_name in _FORBIDDEN_ENV_VARS:
        if os.environ.get(env_name):
            reasons.append(
                f"Forbidden env var {env_name} is set — broker "
                f"credentials must NEVER be in scope during a paper "
                f"run. Refusing to start.")

    for mod_name in _FORBIDDEN_MODULES:
        # ``find_spec`` on a submodule whose parent package is missing
        # raises ModuleNotFoundError (rather than returning None) —
        # that's the SAFE outcome here (the module isn't reachable),
        # so we swallow the exception. Any OTHER exception bubbles up.
        try:
            spec = importlib.util.find_spec(mod_name)
        except (ModuleNotFoundError, ValueError):
            spec = None
        if spec is not None:
            reasons.append(
                f"Forbidden module {mod_name!r} is importable — "
                f"a broker-order code path is reachable from this "
                f"process. Refusing to start.")

    if reasons:
        raise SafetyGuardError(
            "PAPER-ONLY guard failed. Reasons:\n  - "
            + "\n  - ".join(reasons))


def safety_status_summary() -> dict:
    """Read-only snapshot for logs / health output. Does NOT raise;
    use ``assert_paper_mode()`` for the enforcement check."""
    return {
        "paper_mode": os.getenv("PAPER_MODE", ""),
        "forbidden_env_set": [v for v in _FORBIDDEN_ENV_VARS
                                if os.environ.get(v)],
        "forbidden_modules_importable": [
            m for m in _FORBIDDEN_MODULES
            if importlib.util.find_spec(m) is not None
        ],
        "python_executable": sys.executable,
    }
