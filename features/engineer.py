"""Feature engineering: raw daily OHLCV → indicator columns.

LOOK-AHEAD AUDIT (daily bars)
-----------------------------
Every feature at row T is computed from bars up to and INCLUDING T, using
no value beyond row T:
    - RSI / MACD / ATR / ADX: exponential (ewm) or .shift(1)-based — past+current.
    - Bollinger / volatility: rolling(min_periods=period) — past+current window.
    - returns: pct_change(n) = close[T]/close[T-n] − 1 — past+current.
    - VWAP / OBV: cumulative — past+current.
    - macro (NIFTY/VIX): reindex+ffill aligned to row T — past+current.

This is causal at the swing decision moment because we DECIDE at day-T
close (when bar[T] has fully resolved) and ENTER at day-T+1 open. A
feature at row T may legitimately use close[T]; what must never happen is
the strategy seeing row ≥ T+1 — and THAT is enforced by the replay
harness's slice (backtesting/replay.py), not here. So no extra .shift()
is applied to the feature values themselves.

Intraday-only features (opening range, mins-to-close, daily-reset VWAP)
from the predecessor project are removed — this is a daily system.
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd


# ── Low-level helpers ───────────────────────────────────────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def _true_range(df: pd.DataFrame) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift(1)).abs()
    lc = (df["low"] - df["close"].shift(1)).abs()
    return pd.concat([hl, hc, lc], axis=1).max(axis=1)


# ── Individual indicators ───────────────────────────────────────────────

def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    return 100 - (100 / (1 + rs))


def compute_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    fast_ema = _ema(close, fast)
    slow_ema = _ema(close, slow)
    macd_line = fast_ema - slow_ema
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_bollinger(close: pd.Series, period: int = 20, std_dev: float = 2.0):
    mid = _sma(close, period)
    std = close.rolling(window=period, min_periods=period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return upper, lower, mid


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = _true_range(df)
    return tr.ewm(span=period, adjust=False).mean()


def compute_vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    tpv = typical * df["volume"]
    cumvol = df["volume"].cumsum()
    cumtpv = tpv.cumsum()
    return cumtpv / (cumvol + 1e-9)


def compute_obv(df: pd.DataFrame) -> pd.Series:
    direction = df["close"].diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    signed_vol = direction * df["volume"]
    return signed_vol.cumsum()


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = _true_range(df)
    up_move = df["high"].diff()
    down_move = -df["low"].diff()

    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    atr = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / (atr + 1e-9))
    minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / (atr + 1e-9))

    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9))
    return dx.ewm(span=period, adjust=False).mean()


def compute_rolling_returns(close: pd.Series, periods=(5, 10, 20)) -> dict:
    return {f"return_{p}": close.pct_change(p) for p in periods}


def compute_rolling_volatility(close: pd.Series, periods=(10, 20)) -> dict:
    log_ret = np.log(close / close.shift(1))
    return {f"volatility_{p}": log_ret.rolling(window=p, min_periods=p).std()
            for p in periods}


def compute_time_features(index: pd.DatetimeIndex) -> dict:
    # For daily bars hour/minute are constant; day_of_week carries a mild
    # calendar signal. Kept for parity — strategy v1 does not rely on these.
    day_of_week = index.dayofweek  # 0=Mon … 4=Fri
    return {"day_of_week": day_of_week.to_numpy()}


# ── Macro context (NIFTY 50 + India VIX) ────────────────────────────────

def _load_macro_context(db_path=None) -> tuple:
    """Load NIFTY 50 returns/MA and India VIX from the local DB.

    Returns ``(nifty_ret, nifty_ma20, vix, vix_zscore)`` Series, or a
    4-tuple of ``None`` if the read fails (feature pipeline never breaks).
    Single-threaded daily use — no TTL cache needed (unlike the intraday
    8-worker engine that required one).
    """
    try:
        from config import DB_PATH, REGIME_INDEX, VIX_SYMBOL
        conn = sqlite3.connect(str(db_path or DB_PATH), check_same_thread=False)
        try:
            sql = ("SELECT time, close FROM ohlcv WHERE symbol = ? AND "
                   "resolution = '1d' ORDER BY time ASC")
            nifty_df = pd.read_sql_query(sql, conn, params=[REGIME_INDEX],
                                         parse_dates=["time"])
            vix_df = pd.read_sql_query(sql, conn, params=[VIX_SYMBOL],
                                       parse_dates=["time"])
        finally:
            conn.close()
        if nifty_df.empty or vix_df.empty:
            return (None, None, None, None)
        nifty = nifty_df.set_index("time")["close"]
        vix = vix_df.set_index("time")["close"]
        # Dedup defensively (ingest races can produce duplicate timestamps;
        # a duplicate label breaks the downstream reindex).
        if nifty.index.has_duplicates:
            nifty = nifty[~nifty.index.duplicated(keep="last")]
        if vix.index.has_duplicates:
            vix = vix[~vix.index.duplicated(keep="last")]
        nifty_ret = nifty.pct_change().rename("nifty_return")
        nifty_ma20 = (nifty / nifty.rolling(20).mean() - 1).rename("nifty_vs_ma20")
        vix = vix.rename("india_vix")
        vix_zscore = ((vix - vix.rolling(60).mean()) /
                      (vix.rolling(60).std() + 1e-9)).rename("vix_zscore")
        return nifty_ret, nifty_ma20, vix, vix_zscore
    except Exception:
        return (None, None, None, None)


# ── Main entry point ────────────────────────────────────────────────────

def engineer_features(df: pd.DataFrame, add_macro: bool = True) -> pd.DataFrame:
    """Compute indicator features on a daily OHLCV DataFrame.

    Args:
        df: DataFrame with a DatetimeIndex (or a ``time`` column) and
            columns [open, high, low, close, volume].
        add_macro: join NIFTY 50 + India VIX context (reads the DB). Pass
            False for self-contained / offline use (e.g. the gate tests).

    Returns:
        ``df`` with feature columns appended; warm-up rows (NaN features)
        are dropped.
    """
    out = df.copy()

    if "time" in out.columns and not isinstance(out.index, pd.DatetimeIndex):
        out = out.set_index("time")
    out.index = pd.to_datetime(out.index)

    if out.index.has_duplicates:
        out = out[~out.index.duplicated(keep="last")]

    out["rsi"] = compute_rsi(out["close"])
    out["macd"], out["macd_signal"], out["macd_hist"] = compute_macd(out["close"])
    out["bb_upper"], out["bb_lower"], out["bb_mid"] = compute_bollinger(out["close"])
    out["atr"] = compute_atr(out)
    out["vwap"] = compute_vwap(out)
    out["obv"] = compute_obv(out) / 1e6   # normalise to millions of shares
    out["adx"] = compute_adx(out)

    for name, series in compute_rolling_returns(out["close"]).items():
        out[name] = series
    for name, series in compute_rolling_volatility(out["close"]).items():
        out[name] = series

    idx = out.index if isinstance(out.index, pd.DatetimeIndex) else pd.to_datetime(out.index)
    for name, values in compute_time_features(idx).items():
        out[name] = values

    macro_cols: list[str] = []
    if add_macro:
        nifty_ret, nifty_ma20, vix, vix_zscore = _load_macro_context()
        if nifty_ret is not None:
            for series in [nifty_ret, nifty_ma20, vix, vix_zscore]:
                out[series.name] = series.reindex(out.index, method="ffill")
            macro_cols = ["nifty_return", "nifty_vs_ma20", "india_vix", "vix_zscore"]

    feature_cols = [
        "rsi", "macd", "macd_signal", "macd_hist",
        "bb_upper", "bb_lower", "bb_mid",
        "atr", "vwap", "obv", "adx",
        "return_5", "return_10", "return_20",
        "volatility_10", "volatility_20",
        "day_of_week",
    ] + macro_cols
    out = out.dropna(subset=feature_cols)
    return out
