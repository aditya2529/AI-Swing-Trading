"""Fetch → validate → persist pipeline for daily OHLCV.

NSE-only (equities + macro indices like ^NSEI / ^INDIAVIX). The crypto /
US / Alpaca / Binance branches from the intraday project are dropped —
this is a daily NSE swing system. Adapter selection: explicit ``source``
override (CLI), else ``config.DATA_ADAPTER``.
"""
import logging

import pandas as pd

from config import DATA_ADAPTER, DEFAULT_YEARS
from data.database import init_db, load_ohlcv, upsert_ohlcv
from data.validator import validate_and_clean

logger = logging.getLogger(__name__)


def _get_adapter(source: str | None = None):
    """Select an adapter. ``source`` ('yfinance'|'upstox') forces the
    choice; otherwise fall back to ``config.DATA_ADAPTER``."""
    name = source or DATA_ADAPTER
    if name == "upstox":
        from data.adapters.upstox_adapter import UpstoxAdapter
        return UpstoxAdapter()
    from data.adapters.yfinance_adapter import YFinanceAdapter
    return YFinanceAdapter()


def fetch_and_store(symbol: str, years: int = DEFAULT_YEARS,
                    resolution: str = "1d",
                    source: str | None = None) -> pd.DataFrame:
    """Fetch OHLCV, validate, persist to SQLite, return the cleaned frame."""
    init_db()
    # Macro indices (^NSEI, ^INDIAVIX) aren't in the Upstox equity instrument
    # map — always source them from yfinance regardless of the requested source.
    effective_source = "yfinance" if symbol.startswith("^") else source
    adapter = _get_adapter(source=effective_source)
    adapter_name = type(adapter).__name__.replace("Adapter", "")
    logger.info("Fetching %s | %s | %d years via %s …",
                symbol, resolution, years, adapter_name)

    raw = adapter.fetch_ohlcv(symbol, years=years, resolution=resolution)
    cleaned = validate_and_clean(raw, symbol, resolution=resolution)
    upsert_ohlcv(cleaned, symbol=symbol, market="NSE", resolution=resolution)
    logger.info("Stored %d bars for %s.", len(cleaned), symbol)
    return cleaned


def get_ohlcv(symbol: str, resolution: str = "1d") -> pd.DataFrame:
    """Load OHLCV from the local DB (fetch first if empty)."""
    init_db()
    df = load_ohlcv(symbol, resolution=resolution)
    if df.empty:
        logger.info("No local data for %s — fetching now.", symbol)
        fetch_and_store(symbol, resolution=resolution)
        df = load_ohlcv(symbol, resolution=resolution)
    return df
