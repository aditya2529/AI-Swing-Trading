# MOM-3 -- Cross-Sectional Momentum Walk-Forward Backtest

**Branch:** `feature/mom3-momentum-backtest`
**Strategy:** `signals.momentum.MomentumStrategy` (pure rules -- 12-1 Jegadeesh-Titman, monthly rotation, top-15, ATR catastrophe stop)
**Replay data:** 136 of 136 MOMENTUM_UNIVERSE symbols (rest had no rows in market_data.db).
**Initial capital:** Rs 500,000
**Wall-clock:** 22.4s

## 0. Run parameters (DESIGN -- per ops; NOT historical tuning)

| Param | Value | Notes |
|---|---:|---|
| max_positions | 15 | = MOM_TOP_N (strategy holds top-15) |
| max_per_sector | 5 | sector-concentration DESIGN LEVER |
| max_heat | 0.20 | 15 x 1% MAX_RISK_PCT + 5pp safety |
| risk_pct | (harness default MAX_RISK_PCT = 0.01) | unchanged |
| slippage / brokerage | (config defaults) | unchanged |

Strategy frozen knobs: lookback=252 (MOM_LOOKBACK_DAYS), skip=21 (MOM_SKIP_DAYS), top_n=15 (MOM_TOP_N). Academic 12-1 formulation -- NOT fit to data.

## 1. Anti-overfit framing

Three replays -- INSPECT, HELD-OUT, FULL. Strategy parameters were chosen WITHOUT looking at the held-out window. **The GO/NO-GO verdict is on HELD-OUT, evaluated against the 30%-DISCOUNTED PF (per ops).**

| Window | Range | Trading days | n_trades |
|---|---|---:|---:|
| INSPECT | 2016-01-04 -> 2022-12-30 | 1729 | 193 |
| **HELD-OUT (verdict)** | 2023-01-02 -> 2026-06-03 | 843 | **105** |
| FULL (descriptive) | 2014-06-09 -> 2026-06-03 | 2956 | 299 |

All three pass `MomentumStrategy()` the FULL data dict, with `run_replay`'s `start`/`end` constraining only the decision timeline. This preserves the 274-bar (~13mo) warm-up for held-out without leaking held-out data into inspect.

## 2. HELD-OUT verdict (primary)

### HELD-OUT window

- **Profit Factor (raw):** 1.986
- **★ Profit Factor (30% survivorship discount — HEADLINE):** **1.390**
- Profit Factor (25% discount, lighter): 1.490
- **Sharpe ratio:** 2.946
- **Max drawdown:** 0.3642 (= 36.42%)
- **Win rate:** 0.581 (61 of 105)
- **CAGR:** 0.186
- **n_trades:** 105
- Replay window: 2023-01-02 -> 2026-06-03 (843 trading days)

#### Gates (held-out)

| Gate | Observed | Required | Threshold | Raw | Discounted (30%) |
|---|---|---|---:|:---:|:---:|
| Profit Factor | raw 1.986 / disc 1.390 | > | 1.3 | PASS | PASS |
| Sharpe ratio | 2.946 | > | 1.0 | PASS | PASS |
| Max drawdown (mag) | 0.3642 | < | 0.15 | FAIL | FAIL |
| Win rate | 0.581 | > | 0.45 | PASS | PASS |

**Gates cleared (raw): 3 of 4** | **Gates cleared (30% discount): 3 of 4** <-- DEPLOY REFERENCE

#### Robustness suite (held-out)

| Question | Value |
|---|---:|
| Raw PF | 1.986 |
| PF with top-contributing symbol removed (PFC.NS, Rs +96,891) | 1.702 |
| PF with best year removed (2025, Rs +119,923) | 1.909 |
| # symbols with net-negative PnL | 27 of 65 |
| Top-symbol share of gross-positive PnL | 14.3% |
| Top-year share of gross-positive PnL | 17.7% |


#### Momentum-crash DD diagnostic (held-out)

**Single deepest drawdown in equity curve:**

- Peak 2024-06-03 -> Trough 2025-02-28, magnitude **36.42%**

**Drawdown over historically-identified momentum-crash windows (calendar — not curve-fit):**

| Crash window | Range | Equity DD within window |
|---|---|---:|
| 2018 vol spike | 2018-01-22 -> 2018-10-31 | _outside replay range_ |
| 2020-03 COVID | 2020-02-19 -> 2020-04-30 | _outside replay range_ |
| 2022 reversal | 2022-01-01 -> 2022-07-31 | _outside replay range_ |
| 2024 election | 2024-04-01 -> 2024-07-31 | 15.72% |

#### Significance (held-out)

**Binomial test** (null: no edge -> win rate 50%)

- Observed: 61 wins in 105 trades (win rate 0.581).
- P(X >= 61 | n=105, p=0.5) = **0.0590**
- p < 0.10 -- marginally above chance.

**Bootstrap CI on PF** (2000 resamples)

- 5th / 50th / 95th percentile: 1.124 / 1.984 / 3.542
- 5th-percentile PF >= 1.0 -- pessimistic tail still positive.

#### Per-year breakdown (held-out)

| Year | n_trades | Win rate | PF | Total PnL (Rs) |
|---:|---:|---:|---:|---:|
| 2023 | 17 | 0.882 | 2.383 | +95,052 |
| 2024 | 33 | 0.606 | 2.557 | +83,589 |
| 2025 | 30 | 0.500 | 2.166 | +119,923 |
| 2026 | 25 | 0.440 | 1.324 | +37,370 |

#### Plain-English verdict (held-out)

**Held-out gates cleared (raw): 3 of 4** (PF raw 1.986, Sharpe 2.946, |max DD| 0.364, win 0.581).

**★ Held-out gates cleared (30% survivorship discount -- DEPLOY REFERENCE): 3 of 4** (PF disc 1.390).

**Max drawdown 36.4% on held-out is above the 15% gate.** This is the momentum-crash failure mode — see the crash-window DD table above for which calendar window the worst DD landed in.

**Not a deploy candidate at this calibration.** The discounted-gate verdict is the deploy signal; it fails. Calibration changes are listed below as proposed T-tickets (one change at a time per LAW 4) -- NOT applied here.

## 3. INSPECT window (descriptive -- NOT the verdict)

### INSPECT window

- **Profit Factor (raw):** 6.120
- **★ Profit Factor (30% survivorship discount — HEADLINE):** **4.284**
- Profit Factor (25% discount, lighter): 4.590
- **Sharpe ratio:** 3.040
- **Max drawdown:** 0.4288 (= 42.88%)
- **Win rate:** 0.580 (112 of 193)
- **CAGR:** 0.408
- **n_trades:** 193
- Replay window: 2016-01-04 -> 2022-12-30 (1729 trading days)

#### Gates (inspect)

| Gate | Observed | Required | Threshold | Raw | Discounted (30%) |
|---|---|---|---:|:---:|:---:|
| Profit Factor | raw 6.120 / disc 4.284 | > | 1.3 | PASS | PASS |
| Sharpe ratio | 3.040 | > | 1.0 | PASS | PASS |
| Max drawdown (mag) | 0.4288 | < | 0.15 | FAIL | FAIL |
| Win rate | 0.580 | > | 0.45 | PASS | PASS |

**Gates cleared (raw): 3 of 4** | **Gates cleared (30% discount): 3 of 4** <-- DEPLOY REFERENCE

#### Robustness suite (inspect)

| Question | Value |
|---|---:|
| Raw PF | 6.120 |
| PF with top-contributing symbol removed (ADANIENT.NS, Rs +1,853,478) | 4.112 |
| PF with best year removed (2022, Rs +3,596,656) | 2.878 |
| # symbols with net-negative PnL | 43 of 97 |
| Top-symbol share of gross-positive PnL | 32.8% |
| Top-year share of gross-positive PnL | 63.7% |

**Concentration flag:** one year (2022) carries 63.7% of gross PnL.

#### Momentum-crash DD diagnostic (inspect)

**Single deepest drawdown in equity curve:**

- Peak 2020-01-20 -> Trough 2020-03-23, magnitude **42.88%**

**Drawdown over historically-identified momentum-crash windows (calendar — not curve-fit):**

| Crash window | Range | Equity DD within window |
|---|---|---:|
| 2018 vol spike | 2018-01-22 -> 2018-10-31 | 17.97% |
| 2020-03 COVID | 2020-02-19 -> 2020-04-30 | 42.03% |
| 2022 reversal | 2022-01-01 -> 2022-07-31 | 20.06% |
| 2024 election | 2024-04-01 -> 2024-07-31 | _outside replay range_ |

#### Per-year breakdown (inspect)

| Year | n_trades | Win rate | PF | Total PnL (Rs) |
|---:|---:|---:|---:|---:|
| 2016 | 17 | 0.471 | 5.069 | +60,776 |
| 2017 | 27 | 0.704 | 6.861 | +224,842 |
| 2018 | 29 | 0.517 | 1.800 | +78,298 |
| 2019 | 34 | 0.618 | 2.227 | +162,668 |
| 2020 | 34 | 0.471 | 0.646 | -88,590 |
| 2021 | 23 | 0.652 | 11.267 | +690,266 |
| 2022 | 29 | 0.621 | 12.174 | +3,596,656 |


## 4. FULL window (for completeness)

### FULL window

- **Profit Factor (raw):** 3.238
- **★ Profit Factor (30% survivorship discount — HEADLINE):** **2.267**
- Profit Factor (25% discount, lighter): 2.429
- **Sharpe ratio:** 3.782
- **Max drawdown:** 0.4230 (= 42.30%)
- **Win rate:** 0.555 (166 of 299)
- **CAGR:** 0.266
- **n_trades:** 299
- Replay window: 2014-06-09 -> 2026-06-03 (2956 trading days)

#### Gates (full)

| Gate | Observed | Required | Threshold | Raw | Discounted (30%) |
|---|---|---|---:|:---:|:---:|
| Profit Factor | raw 3.238 / disc 2.267 | > | 1.3 | PASS | PASS |
| Sharpe ratio | 3.782 | > | 1.0 | PASS | PASS |
| Max drawdown (mag) | 0.4230 | < | 0.15 | FAIL | FAIL |
| Win rate | 0.555 | > | 0.45 | PASS | PASS |

**Gates cleared (raw): 3 of 4** | **Gates cleared (30% discount): 3 of 4** <-- DEPLOY REFERENCE

#### Robustness suite (full)

| Question | Value |
|---|---:|
| Raw PF | 3.238 |
| PF with top-contributing symbol removed (BHEL.NS, Rs +1,235,149) | 2.845 |
| PF with best year removed (2024, Rs +2,010,794) | 2.748 |
| # symbols with net-negative PnL | 46 of 109 |
| Top-symbol share of gross-positive PnL | 12.1% |
| Top-year share of gross-positive PnL | 19.8% |


#### Momentum-crash DD diagnostic (full)

**Single deepest drawdown in equity curve:**

- Peak 2020-01-20 -> Trough 2020-03-23, magnitude **42.30%**

**Drawdown over historically-identified momentum-crash windows (calendar — not curve-fit):**

| Crash window | Range | Equity DD within window |
|---|---|---:|
| 2018 vol spike | 2018-01-22 -> 2018-10-31 | 20.55% |
| 2020-03 COVID | 2020-02-19 -> 2020-04-30 | 41.60% |
| 2022 reversal | 2022-01-01 -> 2022-07-31 | 26.12% |
| 2024 election | 2024-04-01 -> 2024-07-31 | 15.34% |

#### Per-symbol breakdown (full window, top 30 by PnL)

**Top 30 contributors:**

| Symbol | n_trades | n_wins | Win rate | PF | Total PnL (Rs) |
|---|---:|---:|---:|---:|---:|
| BHEL.NS | 2 | 1 | 0.500 | 106263.210 | +1,235,149 |
| CGPOWER.NS | 4 | 3 | 0.750 | 3018.520 | +912,791 |
| TRENT.NS | 2 | 2 | 1.000 | inf (no losers) | +652,314 |
| KPITTECH.NS | 2 | 2 | 1.000 | inf (no losers) | +620,407 |
| HAL.NS | 2 | 2 | 1.000 | inf (no losers) | +590,155 |
| DEEPAKNTR.NS | 4 | 2 | 0.500 | 33.440 | +485,730 |
| PERSISTENT.NS | 3 | 3 | 1.000 | inf (no losers) | +482,208 |
| BEL.NS | 5 | 2 | 0.400 | 4.163 | +471,644 |
| MUTHOOTFIN.NS | 6 | 6 | 1.000 | inf (no losers) | +455,133 |
| ADANIGREEN.NS | 5 | 3 | 0.600 | 2.678 | +406,272 |
| GLENMARK.NS | 3 | 2 | 0.667 | 20.008 | +393,337 |
| LUPIN.NS | 1 | 1 | 1.000 | inf (no losers) | +263,095 |
| FORTIS.NS | 2 | 1 | 0.500 | 61.138 | +232,356 |
| BHARATFORG.NS | 1 | 1 | 1.000 | inf (no losers) | +186,168 |
| DIVISLAB.NS | 5 | 5 | 1.000 | inf (no losers) | +143,355 |
| BANKBARODA.NS | 1 | 1 | 1.000 | inf (no losers) | +121,601 |
| BHARTIARTL.NS | 3 | 2 | 0.667 | 1518.318 | +99,399 |
| BAJFINANCE.NS | 7 | 4 | 0.571 | 5.556 | +92,549 |
| VBL.NS | 9 | 5 | 0.556 | 2719.471 | +92,332 |
| ITC.NS | 3 | 2 | 0.667 | 3245.998 | +84,910 |
| COFORGE.NS | 6 | 2 | 0.333 | 4.432 | +74,224 |
| ADANIENT.NS | 5 | 4 | 0.800 | 843.932 | +71,396 |
| TATACONSUM.NS | 2 | 1 | 0.500 | 2.680 | +67,603 |
| JSWENERGY.NS | 2 | 2 | 1.000 | inf (no losers) | +63,212 |
| JINDALSTEL.NS | 7 | 4 | 0.571 | 139.883 | +63,054 |
| IGL.NS | 4 | 2 | 0.500 | 13.338 | +62,744 |
| BAJAJFINSV.NS | 6 | 2 | 0.333 | 3.842 | +48,383 |
| AKZOINDIA.NS | 1 | 1 | 1.000 | inf (no losers) | +46,072 |
| IDFCFIRSTB.NS | 1 | 1 | 1.000 | inf (no losers) | +42,010 |
| PFC.NS | 3 | 1 | 0.333 | 3.659 | +41,973 |

**Bottom 10 contributors:**

| Symbol | n_trades | n_wins | Win rate | PF | Total PnL (Rs) |
|---|---:|---:|---:|---:|---:|
| GMRAIRPORT.NS | 3 | 0 | 0.000 | 0.000 | -58,983 |
| OIL.NS | 4 | 1 | 0.250 | 0.003 | -72,032 |
| HEROMOTOCO.NS | 1 | 0 | 0.000 | 0.000 | -88,355 |
| DLF.NS | 3 | 1 | 0.333 | 0.000 | -91,291 |
| TVSMOTOR.NS | 3 | 1 | 0.333 | 0.044 | -98,448 |
| MARUTI.NS | 1 | 0 | 0.000 | 0.000 | -100,211 |
| MPHASIS.NS | 3 | 1 | 0.333 | 0.051 | -132,665 |
| IRB.NS | 5 | 1 | 0.200 | 0.000 | -156,791 |
| ADANIPOWER.NS | 9 | 6 | 0.667 | 0.354 | -203,162 |
| HINDPETRO.NS | 5 | 2 | 0.400 | 0.106 | -465,678 |

#### Per-year breakdown (full window)

| Year | n_trades | Win rate | PF | Total PnL (Rs) |
|---:|---:|---:|---:|---:|
| 2015 | 1 | 1.000 | inf (no losers) | +1,499 |
| 2016 | 18 | 0.556 | 3.269 | +60,231 |
| 2017 | 24 | 0.625 | 9.186 | +260,895 |
| 2018 | 31 | 0.387 | 1.414 | +44,622 |
| 2019 | 32 | 0.625 | 1.731 | +113,710 |
| 2020 | 31 | 0.452 | 0.727 | -61,428 |
| 2021 | 26 | 0.615 | 16.407 | +787,248 |
| 2022 | 27 | 0.444 | 2.371 | +518,632 |
| 2023 | 30 | 0.700 | 4.194 | +1,591,341 |
| 2024 | 25 | 0.640 | 8.494 | +2,010,794 |
| 2025 | 27 | 0.630 | 2.784 | +1,079,304 |
| 2026 | 27 | 0.444 | 1.791 | +629,620 |


## 5. Survivorship caveat

From `data/universe.py`:

> MOMENTUM_UNIVERSE is CURRENT membership, not point-in-time. Names that were in NIFTY 200 a decade ago but have since been delisted / merged out are entirely absent. MOM-3's backtest report MUST apply an explicit survivorship discount (10-30% PF haircut typical for current-membership universes) and label results accordingly. True PIT membership rotation is a separate later upgrade.

Discount applied (per ops): **30% conservative HEADLINE**, 25% lighter shown for comparison.

| Window | Raw PF | Disc 25% | Disc 30% (HEADLINE) |
|---|---:|---:|---:|
| FULL | 3.238 | 2.429 | **2.267** |
| INSPECT | 6.120 | 4.590 | **4.284** |
| HELD-OUT | 1.986 | 1.490 | **1.390** |

## 6. Proposed follow-up tickets (NOT applied per LAW 4)

- **T-candidate: portfolio-level DD cap for MOM** (reuse the MR-4 ``dd_cap_pct`` harness param). Held-out |max DD| = 36.4% vs the 15% gate. Note the symmetric re-arm caveat MR-4 surfaced.

_End of report._