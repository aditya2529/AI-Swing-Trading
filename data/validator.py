import logging

import numpy as np  # noqa: F401 — kept for parity / future outlier tweaks
import pandas as pd

logger = logging.getLogger(__name__)

MAX_FORWARD_FILL_BARS = 3
OUTLIER_ZSCORE = 4.0


def validate_and_clean(df: pd.DataFrame, symbol: str,
                       resolution: str = "1d") -> pd.DataFrame:
    """Validate an OHLCV DataFrame and return a cleaned copy.

    Steps (in order):
      1. Sort by time ascending.
      2. For daily bars: normalise timestamps to the date (strip stray
         intraday time on boundary candles) and dedup on time.
      3. Drop rows where open/high/low/close are all NaN.
      4. Forward-fill missing prices up to MAX_FORWARD_FILL_BARS bars.
      5. Outliers (|return z| > OUTLIER_ZSCORE):
           - DAILY: LOG only — do NOT mutate. A >4σ daily move is usually a
             REAL event (crash, split-adjustment, gap). Smoothing it would
             erase genuine history (e.g. the deliberately-included fallen
             names whose crashes are the whole point). The daily feeds
             (Upstox/yfinance) are already adjusted and agree to ~0%, so
             there are no bad ticks to smooth here.
           - INTRADAY: replace the close with the prior bar (bad-tick guard,
             carried over from the 5-min project).
      6. Zero-fill remaining NaN volumes.
      7. Drop any rows still carrying NaN OHLC.

    Input must have a ``time`` column (the adapter contract).
    """
    if df.empty:
        logger.warning("[%s] Empty DataFrame passed to validator.", symbol)
        return df

    df = df.sort_values("time").reset_index(drop=True)

    if resolution == "1d":
        df["time"] = pd.to_datetime(df["time"]).dt.normalize()
        df = df.drop_duplicates(subset="time", keep="last").reset_index(drop=True)

    initial_len = len(df)
    df = df.dropna(subset=["open", "high", "low", "close"], how="all")
    dropped = initial_len - len(df)
    if dropped:
        logger.info("[%s] Dropped %d all-NaN rows.", symbol, dropped)

    for col in ["open", "high", "low", "close"]:
        mask = df[col].isna()
        if mask.any():
            df[col] = df[col].ffill(limit=MAX_FORWARD_FILL_BARS)
            remaining = df[col].isna().sum()
            if remaining:
                logger.warning(
                    "[%s] %d %s bars could not be forward-filled (gap too large).",
                    symbol, remaining, col,
                )

    returns = df["close"].pct_change()
    z = (returns - returns.mean()) / (returns.std() + 1e-9)
    outliers = z.abs() > OUTLIER_ZSCORE
    if outliers.any():
        n = int(outliers.sum())
        if resolution == "1d":
            logger.warning(
                "[%s] %d daily >|%.0fσ| move(s) KEPT as real events (not "
                "smoothed).", symbol, n, OUTLIER_ZSCORE,
            )
        else:
            logger.warning(
                "[%s] %d intraday outlier(s) (|z| > %.1f); replacing close "
                "with prior bar.", symbol, n, OUTLIER_ZSCORE,
            )
            df.loc[outliers, "close"] = df["close"].shift(1)[outliers]

    df["volume"] = df["volume"].fillna(0).astype(int)

    before_final = len(df)
    df = df.dropna(subset=["open", "high", "low", "close"])
    if len(df) < before_final:
        logger.info("[%s] Dropped %d rows with remaining NaN after cleaning.",
                    symbol, before_final - len(df))

    return df.reset_index(drop=True)
