"""Performance metrics for the backtest harness.

PF, Sharpe, max-DD, CAGR, win-rate — computed from the trade tape and
equity curve produced by the engine-replay (backtesting/replay.py).
"""
import numpy as np
import pandas as pd


def sharpe_ratio(returns: pd.Series, risk_free: float = 0.06,
                 periods_per_year: int = 252) -> float:
    """Annualised Sharpe. ``risk_free`` is the annual rate (0.06 = 6%)."""
    returns = returns.dropna()
    if len(returns) < 2:
        return 0.0
    rf_per_bar = risk_free / periods_per_year
    excess = returns - rf_per_bar
    if excess.std() == 0:
        return 0.0
    return float((excess.mean() / excess.std()) * np.sqrt(periods_per_year))


def max_drawdown(equity_curve: pd.Series) -> float:
    """Max peak-to-trough drawdown as a positive fraction (0.15 = 15%)."""
    if len(equity_curve) == 0:
        return 0.0
    roll_max = equity_curve.cummax()
    drawdown = (equity_curve - roll_max) / (roll_max + 1e-9)
    return float(-drawdown.min())


def cagr(equity_curve: pd.Series, periods_per_year: int = 252) -> float:
    """Compound annual growth rate."""
    n = len(equity_curve)
    if n < 2 or equity_curve.iloc[0] <= 0:
        return 0.0
    total_return = equity_curve.iloc[-1] / equity_curve.iloc[0]
    years = n / periods_per_year
    return float(total_return ** (1 / years) - 1)


def win_rate(trades: pd.DataFrame) -> float:
    """Fraction of trades with positive net PnL."""
    if trades.empty:
        return 0.0
    return float((trades["pnl"] > 0).mean())


def profit_factor(trades: pd.DataFrame) -> float:
    """Gross profit / gross loss. Returns inf if there are no losers."""
    gross_profit = trades.loc[trades["pnl"] > 0, "pnl"].sum()
    gross_loss = trades.loc[trades["pnl"] < 0, "pnl"].abs().sum()
    if gross_loss == 0:
        return float("inf")
    return float(gross_profit / gross_loss)


def compute_all(trades: pd.DataFrame, equity_curve: pd.Series,
                periods_per_year: int = 252) -> dict:
    """All key metrics as a dict.

    Sharpe is computed on TRADE-level returns (correct for a low-frequency
    daily strategy; a bar-by-bar equity Sharpe is biased negative when the
    book is mostly flat).
    """
    if not trades.empty and "return" in trades.columns:
        trade_returns = trades["return"].dropna()
        trades_per_year = min(len(trade_returns), periods_per_year)
        sharpe = sharpe_ratio(trade_returns, periods_per_year=max(trades_per_year, 2))
    else:
        sharpe = 0.0

    return {
        "sharpe": sharpe,
        "max_drawdown": max_drawdown(equity_curve),
        "cagr": cagr(equity_curve, periods_per_year=periods_per_year),
        "win_rate": win_rate(trades),
        "profit_factor": profit_factor(trades),
        "n_trades": len(trades),
    }
