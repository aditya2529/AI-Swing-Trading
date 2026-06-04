# MR-2 — Mean-Reversion Honest Backtest Report

**Branch:** `feature/mr2-backtest`
**Strategy:** `signals.mean_reversion.MeanReversionStrategy` (pure rules — uptrend filter + RSI oversold + ATR hard stop + RSI bounce / time / hard stop exits)
**Replay data:** 25 universe equities only (per ops correction — no ^NSEI, no ^INDIAVIX). MeanReversionStrategy reads no market index, so any index in the data dict would be traded as a regular symbol.
**Initial capital:** Rs 500,000

## 0. Anti-overfit framing

Three replays — INSPECT, HELD-OUT, FULL. The strategy's parameters were chosen WITHOUT looking at the held-out window. **The GO/NO-GO verdict is on HELD-OUT only.** INSPECT and FULL are descriptive; gate-clearing on INSPECT alone does not justify deploy.

| Window | Range | Trading days | n_trades |
|---|---|---:|---:|
| INSPECT | 2016-06-03 → 2022-12-30 | 1631 | 44 |
| **HELD-OUT (verdict)** | 2023-01-02 → 2026-06-02 | 845 | **38** |
| FULL (descriptive) | 2016-06-03 → 2026-06-02 | 2476 | 82 |

All three pass `MeanReversionStrategy()` the FULL data dict, with `run_replay`'s `start`/`end` constraining only the decision timeline. This preserves the 200-day MA warm-up for the held-out replay without leaking held-out data into the inspect run.

## 1. HELD-OUT verdict (primary)

### HELD-OUT window

- **Profit Factor:** 2.375
- **Sharpe ratio:** 2.470
- **Max drawdown:** 0.0397 (= 3.97%)
- **Win rate:** 0.684 (26 of 38)
- **CAGR:** 0.032
- **n_trades:** 38
- Replay window: 2023-01-02 → 2026-06-02 (845 trading days)

#### Gates (held-out)

| Gate | Observed | Required | Threshold | Result |
|---|---|---|---:|:---:|
| Profit Factor | 2.375 | > | 1.3 | ✅ PASS |
| Sharpe ratio | 2.470 | > | 1.0 | ✅ PASS |
| Max drawdown (mag) | 0.0397 | < | 0.15 | ✅ PASS |
| Win rate | 0.684 | > | 0.45 | ✅ PASS |

**4 of 4 gates cleared.**

#### Robustness suite (held-out)

| Question | Value |
|---|---:|
| Raw PF | 2.375 |
| PF with top-contributing symbol removed (TATASTEEL.NS, Rs +20,840) | 1.928 |
| PF with best year removed (2023, Rs +20,109) | 2.086 |
| # symbols with net-negative PnL | 7 of 20 |
| Top-symbol share of total PnL | 21.8% |
| Top-year share of total PnL | 21.1% |

#### Significance (held-out)

**Binomial test** (null: no edge → win rate 50%)

- Observed: 26 wins in 38 trades (win rate 0.684).
- P(X ≥ 26 | n=38, p=0.5) = **0.0168**
- p < 0.05 — win rate significantly above chance.

**Bootstrap CI on PF** (2000 resamples)

- 5th / 50th / 95th percentile: 1.099 / 2.478 / 6.595
- 5th percentile PF ≥ 1.0 — pessimistic tail still positive.

#### Plain-English verdict (held-out)

**Held-out gates cleared: 4 of 4** (PF 2.375, Sharpe 2.470, |max DD| 0.040, win 0.684).

⚠️ **REGIME-DIVERGENCE CALLOUT.** Held-out PF 2.375 but the inspect window PF was 0.654 (a losing strategy over 44 trades). The strategy works on the held-out window's regime but failed on the inspect window's regime. Live deployment would be exposed to BOTH regimes — the held-out window happens to be a structural bull market (2023-2026); 2016-2022 included the COVID crash and several chop periods that the strategy clearly cannot survive at this calibration.

**Conditional deploy candidate.** The held-out window cleared all four gates with no concentration flags AND the binomial p-value (0.0168) plus the bootstrap 5th-percentile PF (>1.0) both support a real edge IN THE HELD-OUT REGIME. But the inspect window's failure means the strategy has a regime-dependent failure mode — adding a regime filter (or a portfolio DD cap that fires during the failure mode's correlated knife-catch) is the right next ticket BEFORE paper-trade. LAW 4 keeps this proposal separate; the held-out result is real, the deploy is not yet.

## 2. INSPECT window (descriptive — NOT the verdict)

### INSPECT window

- **Profit Factor:** 0.654
- **Sharpe ratio:** -1.362
- **Max drawdown:** 0.1753 (= 17.53%)
- **Win rate:** 0.500 (22 of 44)
- **CAGR:** -0.012
- **n_trades:** 44
- Replay window: 2016-06-03 → 2022-12-30 (1631 trading days)

#### Gates (inspect)

| Gate | Observed | Required | Threshold | Result |
|---|---|---|---:|:---:|
| Profit Factor | 0.654 | > | 1.3 | ❌ FAIL |
| Sharpe ratio | -1.362 | > | 1.0 | ❌ FAIL |
| Max drawdown (mag) | 0.1753 | < | 0.15 | ❌ FAIL |
| Win rate | 0.500 | > | 0.45 | ✅ PASS |

**1 of 4 gates cleared.**

#### Robustness suite (inspect)

| Question | Value |
|---|---:|
| Raw PF | 0.654 |
| PF with top-contributing symbol removed (TCS.NS, Rs +8,524) | 0.575 |
| PF with best year removed (2017, Rs +12,713) | 0.451 |
| # symbols with net-negative PnL | 14 of 21 |
| Top-symbol share of total PnL | 12.2% |
| Top-year share of total PnL | 18.2% |


## 3. FULL window (for completeness)

### FULL window

- **Profit Factor:** 1.104
- **Sharpe ratio:** 0.107
- **Max drawdown:** 0.1753 (= 17.53%)
- **Win rate:** 0.585 (48 of 82)
- **CAGR:** 0.003
- **n_trades:** 82
- Replay window: 2016-06-03 → 2026-06-02 (2476 trading days)

#### Gates (full)

| Gate | Observed | Required | Threshold | Result |
|---|---|---|---:|:---:|
| Profit Factor | 1.104 | > | 1.3 | ❌ FAIL |
| Sharpe ratio | 0.107 | > | 1.0 | ❌ FAIL |
| Max drawdown (mag) | 0.1753 | < | 0.15 | ❌ FAIL |
| Win rate | 0.585 | > | 0.45 | ✅ PASS |

**1 of 4 gates cleared.**

#### Robustness suite (full)

| Question | Value |
|---|---:|
| Raw PF | 1.104 |
| PF with top-contributing symbol removed (TATASTEEL.NS, Rs +26,723) | 0.914 |
| PF with best year removed (2023, Rs +18,600) | 0.973 |
| # symbols with net-negative PnL | 13 of 25 |
| Top-symbol share of total PnL | 16.9% |
| Top-year share of total PnL | 11.7% |

#### Per-symbol breakdown (full window)

| Symbol | n_trades | n_wins | Win rate | PF | Total PnL (Rs) |
|---|---:|---:|---:|---:|---:|
| TATASTEEL.NS | 6 | 4 | 0.667 | 4.783 | +26,723 |
| CIPLA.NS | 3 | 3 | 1.000 | inf (no losers) | +11,687 |
| LT.NS | 2 | 2 | 1.000 | inf (no losers) | +7,820 |
| GAIL.NS | 2 | 2 | 1.000 | inf (no losers) | +7,420 |
| INFY.NS | 3 | 3 | 1.000 | inf (no losers) | +7,267 |
| TCS.NS | 3 | 2 | 0.667 | 3.365 | +5,991 |
| RELIANCE.NS | 5 | 3 | 0.600 | 1.711 | +4,346 |
| MARUTI.NS | 5 | 3 | 0.600 | 1.713 | +3,940 |
| SBIN.NS | 4 | 3 | 0.750 | 1.681 | +3,232 |
| ONGC.NS | 1 | 1 | 1.000 | inf (no losers) | +1,898 |
| M&M.NS | 4 | 2 | 0.500 | 1.166 | +1,543 |
| SUNPHARMA.NS | 1 | 1 | 1.000 | inf (no losers) | +264 |
| BHARTIARTL.NS | 5 | 3 | 0.600 | 0.999 | -19 |
| ITC.NS | 2 | 1 | 0.500 | 0.529 | -166 |
| TATAMOTORS.NS | 2 | 1 | 0.500 | 0.786 | -429 |
| ICICIBANK.NS | 2 | 1 | 0.500 | 0.466 | -1,656 |
| AXISBANK.NS | 4 | 2 | 0.500 | 0.659 | -2,569 |
| YESBANK.NS | 3 | 1 | 0.333 | 0.434 | -2,672 |
| BPCL.NS | 3 | 1 | 0.333 | 0.341 | -4,357 |
| LUPIN.NS | 2 | 1 | 0.500 | 0.229 | -6,294 |
| HINDALCO.NS | 2 | 1 | 0.500 | 0.164 | -6,341 |
| VEDL.NS | 5 | 2 | 0.400 | 0.327 | -7,315 |
| WIPRO.NS | 4 | 2 | 0.500 | 0.406 | -8,362 |
| HDFCBANK.NS | 4 | 1 | 0.250 | 0.114 | -10,616 |
| HINDUNILVR.NS | 5 | 2 | 0.400 | 0.118 | -16,393 |

#### Per-year breakdown (full window)

| Year | n_trades | Win rate | PF | Total PnL (Rs) |
|---:|---:|---:|---:|---:|
| 2017 | 13 | 0.615 | 1.773 | +12,713 |
| 2018 | 5 | 0.400 | 0.219 | -14,725 |
| 2019 | 5 | 0.200 | 0.066 | -20,621 |
| 2020 | 7 | 0.714 | 1.456 | +5,575 |
| 2021 | 9 | 0.556 | 0.960 | -608 |
| 2022 | 5 | 0.200 | 0.124 | -19,232 |
| 2023 | 8 | 0.750 | 3.568 | +18,600 |
| 2024 | 18 | 0.611 | 1.405 | +9,381 |
| 2025 | 6 | 0.667 | 8.710 | +12,378 |
| 2026 | 6 | 0.833 | 3.352 | +11,484 |

## 4. Survivorship caveat — raw vs discounted (held-out)

From `data/universe.py`:

> Fixed-membership universe with fallen-from-index names retained. NOT a true point-in-time rotation and excludes fully-delisted tickers (unfetchable). Phase 3 must report PF raw AND survivorship-discounted, and label results accordingly.

| Quantity | Held-out value |
|---|---:|
| Raw PF | 2.375 |
| Survivorship-discounted PF (× 0.85) | 2.019 |

Same 15% discount T3 used; see T3 report `logs/r3_backtest_report.md §5` for the rationale (universe retains fallen names but excludes fully-delisted tickers; ~10-30% inflation typical).

## 5. Proposed follow-up tickets (NOT applied per LAW 4)

- **T-candidate: regime-aware MR (highest priority).** Held-out PF 2.375 vs inspect PF 0.654 is a sharp regime divergence — the strategy works in trending bull markets and FAILS in choppy/crashing markets. Candidates (each a SINGLE change per LAW 4): (a) re-introduce the NIFTY > 50-DMA regime gate (yes, MR-1 baseline excluded it; this would be the explicit add-back); (b) replace the 50-DMA with a vol-of-vol filter (only trade when realised vol over 30d is BELOW a threshold); (c) portfolio-level DD cap that shuts down new entries during catastrophic-DD periods. Run ONLY on the held-out window after the change — do NOT calibrate the trigger by fitting it to inspect.

_End of report._