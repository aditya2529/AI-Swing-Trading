"""yfinance OHLCV adapter — free, no credentials. Daily bars are the
swing default; this is also our cross-source reconciliation feed against
Upstox (bootstrap trap #6: backtest data ≠ live data)."""
import logging
import time
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

from data.adapters.base import DataAdapter

logger = logging.getLogger(__name__)


_RESOLUTION_MAP = {
    "1d": "1d",
    "1h": "1h",
    "5m": "5m",
}

# yfinance limits on intraday history (daily is effectively unlimited).
_MAX_DAYS = {
    "1h": 730,
    "5m": 60,
    "1d": 99999,
}

# Transient yfinance failures (network blip, internal AttributeError in their
# date parser, etc.) are common on long batch backfills. We retry the SAME
# request a few times with backoff. A genuinely-dead ticker still raises after
# the retries are exhausted, so a dead-symbol caller (e.g. MOM-1) still
# correctly logs it as failed and moves on.
_RETRY_ATTEMPTS = 3       # 1 original + 2 retries
_RETRY_BACKOFF_SECS = 0.5


def _history_with_retries(ticker, **kwargs) -> pd.DataFrame:
    """Wrap ``ticker.history(**kwargs)`` with retries. Surfaces the LAST
    exception to the caller if all attempts fail."""
    last_exc: Exception | None = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            raw = ticker.history(**kwargs)
            # yfinance does NOT raise on a delisted symbol — it returns an
            # empty frame. We treat empty-on-attempt-N the same as a raise
            # so we get one more shot before giving up.
            if raw is None or raw.empty:
                if attempt < _RETRY_ATTEMPTS:
                    logger.info("yfinance returned empty frame (attempt %d/%d) "
                                "— retrying.", attempt, _RETRY_ATTEMPTS)
                    time.sleep(_RETRY_BACKOFF_SECS * attempt)
                    continue
            return raw
        except Exception as e:   # noqa: BLE001 — yfinance throws many types
            last_exc = e
            if attempt < _RETRY_ATTEMPTS:
                logger.info("yfinance raised %s (attempt %d/%d) — retrying.",
                            type(e).__name__, attempt, _RETRY_ATTEMPTS)
                time.sleep(_RETRY_BACKOFF_SECS * attempt)
            else:
                raise
    # Empty after all retries — return whatever the last call returned
    # (empty DataFrame); the caller's empty-check raises a clean ValueError.
    return raw


class YFinanceAdapter(DataAdapter):

    def fetch_ohlcv(self, symbol: str, years: int = 3,
                    resolution: str = "1d") -> pd.DataFrame:
        interval = _RESOLUTION_MAP.get(resolution, "1d")
        ticker = yf.Ticker(symbol)

        if interval == "5m":
            # 5-min: use period= (not start/end) to dodge yfinance's
            # 60-day boundary errors.
            raw = _history_with_retries(ticker, period="58d", interval=interval)
        else:
            end = datetime.utcnow()
            max_days = _MAX_DAYS.get(interval, 99999)
            delta_days = min(years * 365, max_days)
            start = end - timedelta(days=delta_days)
            raw = _history_with_retries(
                ticker,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval=interval,
            )

        if raw.empty:
            raise ValueError(f"yfinance returned no data for {symbol!r} ({interval})")

        raw = raw.reset_index()
        # yfinance names the time column 'Datetime' for intraday, 'Date' for daily.
        time_col = "Datetime" if "Datetime" in raw.columns else "Date"
        df = raw[[time_col, "Open", "High", "Low", "Close", "Volume"]].copy()
        df.columns = ["time", "open", "high", "low", "close", "volume"]
        df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)
        df = df.dropna(subset=["open", "close"])
        df = df.reset_index(drop=True)
        return df

    def is_available(self) -> bool:
        try:
            t = yf.Ticker("RELIANCE.NS")
            info = t.fast_info
            return info.last_price is not None
        except Exception:
            return False
