# AI Swing Trading

A long-only **breakout swing** system on **daily NSE bars** — rules-based first,
ML only later if it provably beats the simple version. Built from the hard-won
lessons of the predecessor intraday project, whose backtest (PF 8.1) lied to a
live PF of 0.76 because of a look-ahead-bias bug.

> **Mantra:** Honest numbers, one change at a time, paper before real, verify everything.

See [`SWING_PROJECT_BOOTSTRAP.md`](SWING_PROJECT_BOOTSTRAP.md) for the full
context and the 8 non-negotiable Engineering Laws, and
[`SWING_STRATEGY_OPTIONS.md`](SWING_STRATEGY_OPTIONS.md) for the strategy menu.

## The look-ahead rule (the one that matters)

A decision at action-moment **A** may use only data that has **fully resolved
before A**.

- Daily bars here are **date-labelled full-session records** (`bar[T]` =
  complete OHLC for trading day T; verified `close[T] ≠ open[T+1]`, real
  overnight gaps).
- We **decide at day-T close** (when `bar[T]` is final) and **enter at day-T+1
  open**. So the decision may legitimately use bars **through day T**, and the
  only T+1 value touched is `open[T+1]` (the fill).
- The intraday project's `index < clock` was right *there* (5-min bars were
  open-stamped, so the current bar's close was in the future). Copying it blindly
  for daily bars would wrongly drop the breakout day — so the slice is **derived
  from the convention, not copied**, and locked by a regression test.

The gate: `tests/test_lookahead_regression.py` must pass before any strategy
logic is written.

## Status

**Phase 0 — foundation & honest harness.** No strategy/model logic until the
look-ahead gate passes.

## Layout

```
data/         OHLCV DB, validator, ingestion, adapters (yfinance, upstox), universe
features/     engineer.py — indicators (look-ahead-audited, daily-safe)
signals/      risk.py — sizing, ATR stop, Chandelier trail, portfolio heat
backtesting/  metrics.py (PF/Sharpe/DD), replay.py — causal daily engine-replay
tests/        test_lookahead_regression.py (the gate), test_replay_contract.py
config.py     all parameters; main.py — CLI entry point
```

## Develop

```powershell
py -3.11 -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest -q                 # gate + contract tests
```

Paper money only. Real money is months away by design.
