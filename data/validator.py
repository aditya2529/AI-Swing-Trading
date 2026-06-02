import logging

import numpy as np  # noqa: F401 — kept for parity / future outlier tweaks
import pandas as pd

logger = logging.getLogger(__name__)

MAX_FORWARD_FILL_BARS = 3
OUTLIER_ZSCORE = 4.0


def validate_and_clean(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Validate an OHLCV DataFrame and return a cleaned copy.

    Steps (in order):
      1. Sort by time ascending.
      2. Drop rows where open/high/low/close are all NaN.
      3. Forward-fill missing prices up to MAX_FORWARD_FILL_BARS bars.
      4. Neutralise return outliers (|z| > OUTLIER_ZSCORE) with the prior close.
      5. Zero-fill remaining NaN volumes.
      6. Drop any rows still carrying NaN OHLC.

    Input must have a ``time`` column (the adapter contract).
    """
    if df.empty:
        logger.warning("[%s] Empty DataFrame passed to validator.", symbol)
        return df

    df = df.sort_values("time").reset_index(drop=True)

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
        logger.warning(
            "[%s] %d return outlier(s) detected (|z| > %.1f); replacing close "
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
