# T3 — First Honest Backtest Report

**Branch:** `feature/t3-first-backtest`
**Strategy:** `signals.breakout.BreakoutStrategy` (pure rules — Donchian upper + volume + regime + R:R measured-move screen + Chandelier + time stop)
**Window:** 2016-06-03 → 2026-06-02 (2476 trading days)
**Universe:** 25 equities + ^NSEI (macro). ^INDIAVIX excluded as the strategy would otherwise try to trade it.
**Initial capital:** Rs 500,000

## 1. Headline metrics

- **Profit Factor:** 1.118
- **Sharpe ratio:** 0.766
- **Max drawdown:** 0.5112 (= 51.12%)
- **Win rate:** 0.493  (145 of 294 closed trades)
- **CAGR:** 0.014
- **n_trades:** 294
- **Final equity:** Rs 574,593  (total return +14.92%)

## 2. Verdict vs the success gates

| Gate | Observed | Required | Threshold | Result |
|---|---|---|---:|:---:|
| Profit Factor | 1.118 | > | 1.3 | ❌ FAIL |
| Sharpe ratio | 0.766 | > | 1.0 | ❌ FAIL |
| Max drawdown (mag) | 0.5112 | < | 0.15 | ❌ FAIL |
| Win rate | 0.493 | > | 0.45 | ✅ PASS |

**Summary:** 1 of 4 gates cleared.

## 3. Filter funnel (entry pipeline)

| Stage | Symbol-days surviving | % of raw breakouts | Δ from prior stage |
|---|---:|---:|---:|
| 1. Raw Donchian breakouts (close > prior-20-day high, excl. T) | 4199 | 100.0% | — |
| 2. Survives volume confirmation (> 1.5× 20-day avg) | 1825 | 43.5% | -2374 |
| 3. Survives regime gate (^NSEI > 50-day MA) | 1533 | 36.5% | -292 |
| 4. Survives measured-move R:R screen (≥ 2.0) | 473 | 11.3% | -1060 |
| 5. Actually became trades (passed portfolio caps + slot availability) | 294 | 7.0% | -179 |

**Biggest single filter:** **volume** (-2374 symbol-days, 56.5% of raw breakouts).

## 4. Trade-tape sanity (LAWS 5 + 8)

### 4a. Per-symbol breakdown (sorted by total PnL)

| Symbol | n_trades | n_wins | Win rate | PF | Total PnL (Rs) |
|---|---:|---:|---:|---:|---:|
| ONGC.NS | 12 | 8 | 0.667 | 5.998 | +64,344 |
| SBIN.NS | 14 | 9 | 0.643 | 4.210 | +26,655 |
| TCS.NS | 12 | 7 | 0.583 | 2.112 | +26,536 |
| WIPRO.NS | 14 | 9 | 0.643 | 1.991 | +19,187 |
| TATASTEEL.NS | 15 | 8 | 0.533 | 1.733 | +19,013 |
| LUPIN.NS | 13 | 9 | 0.692 | 1.855 | +17,956 |
| AXISBANK.NS | 6 | 3 | 0.500 | 3.524 | +13,669 |
| HINDALCO.NS | 15 | 7 | 0.467 | 1.285 | +7,598 |
| TATAMOTORS.NS | 13 | 6 | 0.462 | 1.093 | +2,689 |
| GAIL.NS | 7 | 4 | 0.571 | 1.065 | +1,344 |
| RELIANCE.NS | 19 | 11 | 0.579 | 1.025 | +1,015 |
| INFY.NS | 7 | 3 | 0.429 | 1.027 | +609 |
| M&M.NS | 12 | 6 | 0.500 | 0.991 | -192 |
| HINDUNILVR.NS | 10 | 4 | 0.400 | 0.896 | -2,074 |
| ICICIBANK.NS | 12 | 8 | 0.667 | 0.846 | -3,259 |
| YESBANK.NS | 9 | 4 | 0.444 | 0.845 | -3,566 |
| MARUTI.NS | 11 | 5 | 0.455 | 0.851 | -3,805 |
| VEDL.NS | 13 | 5 | 0.385 | 0.874 | -6,806 |
| HDFCBANK.NS | 12 | 3 | 0.250 | 0.675 | -8,361 |
| BHARTIARTL.NS | 13 | 3 | 0.231 | 0.666 | -8,379 |
| LT.NS | 15 | 8 | 0.533 | 0.649 | -10,653 |
| SUNPHARMA.NS | 11 | 4 | 0.364 | 0.564 | -10,935 |
| BPCL.NS | 10 | 5 | 0.500 | 0.658 | -13,236 |
| CIPLA.NS | 5 | 1 | 0.200 | 0.020 | -14,282 |
| ITC.NS | 14 | 5 | 0.357 | 0.203 | -40,474 |

⚠️ **CONCENTRATION FLAG:** the top symbol (ONGC.NS, Rs +64,344) carries more than half the total PnL — the edge may be a single-stock fluke rather than a generalisable signal.

### 4b. Per-year breakdown

| Year | n_trades | Win rate | PF | Total PnL (Rs) |
|---:|---:|---:|---:|---:|
| 2016 | 3 | 0.667 | 2.151 | +1,342 |
| 2017 | 33 | 0.606 | 2.046 | +35,766 |
| 2018 | 36 | 0.389 | 0.748 | -16,799 |
| 2019 | 19 | 0.421 | 0.749 | -9,106 |
| 2020 | 32 | 0.531 | 1.950 | +52,853 |
| 2021 | 37 | 0.405 | 1.042 | +5,034 |
| 2022 | 28 | 0.429 | 0.821 | -11,958 |
| 2023 | 37 | 0.649 | 1.416 | +26,482 |
| 2024 | 39 | 0.513 | 1.417 | +38,770 |
| 2025 | 25 | 0.440 | 0.474 | -45,733 |
| 2026 | 5 | 0.400 | 0.765 | -2,058 |

⚠️ **CONCENTRATION FLAG:** year 2020 carries more than half the total PnL (Rs +52,853) — the edge may be a single-regime fluke.

### 4c. Significance — could this be luck?

**Binomial test** (null hypothesis: no edge → win rate 50%)

- Observed: 145 wins in 294 trades (win rate 0.493).
- P(X ≥ 145 | n=294, p=0.5) = **0.6147**
- p ≥ 0.10 — NOT statistically distinguishable from chance at this sample size.

**Bootstrap CI on Profit Factor** (2000 resamples with replacement)

- 5th percentile PF: 0.862
- 50th percentile (median) PF: 1.110
- 95th percentile PF: 1.442
- The 90% CI **spans 1.0** — bootstrap cannot rule out a true PF of 1.0 (break-even). Edge is uncertain.

## 5. Survivorship caveat — raw vs discounted

From `data/universe.py`:

> Fixed-membership universe with fallen-from-index names retained. NOT a true point-in-time rotation and excludes fully-delisted tickers (unfetchable). Phase 3 must report PF raw AND survivorship-discounted, and label results accordingly.

| Quantity | Value |
|---|---:|
| Raw PF | 1.118 |
| Survivorship-discounted PF (× 0.85) | 0.950 |

Discount rate **15%** is a conservative middle ground — the universe does retain the four prominent fallen-from-NIFTY names (YESBANK / VEDL / LUPIN / GAIL) so the worst form of survivorship bias (today's-winners-only) is partially corrected, but fully-delisted tickers (e.g. Bharti Infratel, merged 2020) have no fetchable OHLC and are absent. Treat the discounted PF as a more honest live expectation than the raw number.

**Reminder from the bootstrap doc:** live PF typically lands 30-50% below backtest PF. If the discounted backtest PF is already near the 1.3 gate, live could easily fall below break-even.

## 6. Plain-English verdict

Only **1 of 4** success gates pass on raw numbers. The strategy is NOT working at the current calibration on this universe.

The binomial test cannot rule out chance at this sample size (p = 0.615). Even if the gate verdicts look favourable, we do not have enough evidence yet to call the edge real.

⚠️ Concentration: one symbol carries >50% of total PnL; one year carries >50% of total PnL. Treat the headline as fragile until more breadth accumulates.

Per the bootstrap doc, live PF typically lands 30–50% below backtest PF. With raw PF 1.118, the realistic live expectation is roughly 0.615 – 0.839. This is the number to plan around, not the headline.

## 7. Proposed follow-up tickets (NOT applied per LAW 4)

- **T4 candidate: shrink the max-drawdown.** Observed |max DD| ≈ 51% vs the 15% gate — the dominant failure mode. Candidates (EACH a single change per LAW 4): (a) tighten `ATR_SL_MULTIPLIER` from 2.0 to 1.5, (b) tighten `CHANDELIER_ATR_MULT` from the current value to 2.0, (c) add a portfolio-level kill switch that closes all positions when daily DD exceeds e.g. 5%, (d) reduce `MAX_RISK_PCT` per trade. Pick ONE.
- **T4 candidate: investigate single-stock concentration.** One symbol carries >50% of total PnL. Before any calibration, run the same strategy on a per-symbol PF distribution and ask: is the edge a generalisable Donchian-breakout signal, or is it one stock's regime? If the latter, no parameter tweak will help.
- **T4 candidate: regime-shift sensitivity.** One year carries >50% of total PnL — strategy may be a 'works in trending markets, fails otherwise' filter. Candidates: split-window PF (bull / bear / sideways) using NIFTY 50 regimes, or add a vol-of-vol filter to identify the 'good' regimes.
- **T4 candidate: re-examine the volume filter.** It removes 57% of raw breakouts — is the strategy starved at this multiplier? Run `VOLUME_MULT` ∈ {1.2, 1.5, 1.8, 2.0} on the same harness and report PF + n_trades + max DD for each.
- **T4 candidate: re-examine the R:R screen.** It removes 25% of regime-survivors — consistent with T2's note that ATR-warm-up on the breakout day inflates the stop and shrinks reward-to-risk on tight channels. Candidates: use a longer ATR period (e.g. 21) to dampen the breakout-day TR spike, OR use a swing-low-based stop instead of ATR.

## 8. Data summary

| Symbol | n_bars | First date | Last date |
|---|---:|---|---|
| TCS.NS | 2473 | 2016-06-03 | 2026-06-02 |
| INFY.NS | 2473 | 2016-06-03 | 2026-06-02 |
| WIPRO.NS | 2473 | 2016-06-03 | 2026-06-02 |
| HDFCBANK.NS | 2473 | 2016-06-03 | 2026-06-02 |
| ICICIBANK.NS | 2473 | 2016-06-03 | 2026-06-02 |
| SBIN.NS | 2473 | 2016-06-03 | 2026-06-02 |
| AXISBANK.NS | 2473 | 2016-06-03 | 2026-06-02 |
| YESBANK.NS | 2473 | 2016-06-03 | 2026-06-02 |
| RELIANCE.NS | 2476 | 2016-06-03 | 2026-06-02 |
| ONGC.NS | 2473 | 2016-06-03 | 2026-06-02 |
| GAIL.NS | 2473 | 2016-06-03 | 2026-06-02 |
| BPCL.NS | 2473 | 2016-06-03 | 2026-06-02 |
| MARUTI.NS | 2473 | 2016-06-03 | 2026-06-02 |
| M&M.NS | 2473 | 2016-06-03 | 2026-06-02 |
| TATAMOTORS.NS | 2473 | 2016-06-03 | 2026-06-02 |
| SUNPHARMA.NS | 2473 | 2016-06-03 | 2026-06-02 |
| CIPLA.NS | 2473 | 2016-06-03 | 2026-06-02 |
| LUPIN.NS | 2473 | 2016-06-03 | 2026-06-02 |
| HINDUNILVR.NS | 2473 | 2016-06-03 | 2026-06-02 |
| ITC.NS | 2473 | 2016-06-03 | 2026-06-02 |
| TATASTEEL.NS | 2473 | 2016-06-03 | 2026-06-02 |
| HINDALCO.NS | 2473 | 2016-06-03 | 2026-06-02 |
| VEDL.NS | 2473 | 2016-06-03 | 2026-06-02 |
| BHARTIARTL.NS | 2473 | 2016-06-03 | 2026-06-02 |
| LT.NS | 2473 | 2016-06-03 | 2026-06-02 |
| ^NSEI | 2461 | 2016-06-06 | 2026-06-02 |

_End of report._