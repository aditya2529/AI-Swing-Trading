# MOM-3 -- Cross-Sectional Momentum Walk-Forward Backtest

**Branch:** `feature/mom3-momentum-backtest`
**Strategy:** `signals.momentum.MomentumStrategy` (pure rules -- 12-1 Jegadeesh-Titman, monthly rotation, top-15, ATR catastrophe stop)
**Replay data:** 136 of 136 MOMENTUM_UNIVERSE symbols (rest had no rows in market_data.db).
**Initial capital:** Rs 500,000
**Wall-clock:** 23.1s

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
| INSPECT | 2016-01-04 -> 2022-12-30 | 1729 | 210 |
| **HELD-OUT (verdict)** | 2023-01-02 -> 2026-06-03 | 843 | **106** |
| FULL (descriptive) | 2014-06-09 -> 2026-06-03 | 2956 | 304 |

All three pass `MomentumStrategy()` the FULL data dict, with `run_replay`'s `start`/`end` constraining only the decision timeline. This preserves the 274-bar (~13mo) warm-up for held-out without leaking held-out data into inspect.

## 2. HELD-OUT verdict (primary)

### HELD-OUT window

- **Profit Factor (raw):** 1.753
- **★ Profit Factor (30% survivorship discount — HEADLINE):** **1.227**
- Profit Factor (25% discount, lighter): 1.314
- **Sharpe ratio:** 3.002
- **Max drawdown:** 0.3417 (= 34.17%)
- **Win rate:** 0.613 (65 of 106)
- **CAGR:** 0.147
- **n_trades:** 106
- Replay window: 2023-01-02 -> 2026-06-03 (843 trading days)

#### Gates (held-out)

| Gate | Observed | Required | Threshold | Raw | Discounted (30%) |
|---|---|---|---:|:---:|:---:|
| Profit Factor | raw 1.753 / disc 1.227 | > | 1.3 | PASS | FAIL |
| Sharpe ratio | 3.002 | > | 1.0 | PASS | PASS |
| Max drawdown (mag) | 0.3417 | < | 0.15 | FAIL | FAIL |
| Win rate | 0.613 | > | 0.45 | PASS | PASS |

**Gates cleared (raw): 3 of 4** | **Gates cleared (30% discount): 2 of 4** <-- DEPLOY REFERENCE

#### Robustness suite (held-out)

| Question | Value |
|---|---:|
| Raw PF | 1.753 |
| PF with top-contributing symbol removed (PFC.NS, Rs +97,318) | 1.501 |
| PF with best year removed (2025, Rs +209,894) | 1.259 |
| # symbols with net-negative PnL | 24 of 65 |
| Top-symbol share of gross-positive PnL | 14.4% |
| Top-year share of gross-positive PnL | 31.0% |


#### Momentum-crash DD diagnostic (held-out)

**Single deepest drawdown in equity curve:**

- Peak 2024-06-03 -> Trough 2025-02-28, magnitude **34.17%**

**Drawdown over historically-identified momentum-crash windows (calendar — not curve-fit):**

| Crash window | Range | Equity DD within window |
|---|---|---:|
| 2018 vol spike | 2018-01-22 -> 2018-10-31 | _outside replay range_ |
| 2020-03 COVID | 2020-02-19 -> 2020-04-30 | _outside replay range_ |
| 2022 reversal | 2022-01-01 -> 2022-07-31 | _outside replay range_ |
| 2024 election | 2024-04-01 -> 2024-07-31 | 16.79% |

#### Significance (held-out)

**Binomial test** (null: no edge -> win rate 50%)

- Observed: 65 wins in 106 trades (win rate 0.613).
- P(X >= 65 | n=106, p=0.5) = **0.0125**
- p < 0.05 -- win rate significantly above chance.

**Bootstrap CI on PF** (2000 resamples)

- 5th / 50th / 95th percentile: 0.966 / 1.802 / 3.503
- 90% CI **spans 1.0** -- bootstrap cannot rule out break-even. Edge is uncertain.

#### Per-year breakdown (held-out)

| Year | n_trades | Win rate | PF | Total PnL (Rs) |
|---:|---:|---:|---:|---:|
| 2023 | 17 | 0.765 | 1.961 | +68,311 |
| 2024 | 32 | 0.656 | 2.591 | +81,603 |
| 2025 | 28 | 0.571 | 3.799 | +209,894 |
| 2026 | 29 | 0.517 | 0.633 | -69,242 |

#### Plain-English verdict (held-out)

**Held-out gates cleared (raw): 3 of 4** (PF raw 1.753, Sharpe 3.002, |max DD| 0.342, win 0.613).

**★ Held-out gates cleared (30% survivorship discount -- DEPLOY REFERENCE): 2 of 4** (PF disc 1.227).

**Max drawdown 34.2% on held-out is above the 15% gate.** This is the momentum-crash failure mode — see the crash-window DD table above for which calendar window the worst DD landed in.

**Not a deploy candidate at this calibration.** The discounted-gate verdict is the deploy signal; it fails. Calibration changes are listed below as proposed T-tickets (one change at a time per LAW 4) -- NOT applied here.

## 3. INSPECT window (descriptive -- NOT the verdict)

### INSPECT window

- **Profit Factor (raw):** 4.430
- **★ Profit Factor (30% survivorship discount — HEADLINE):** **3.101**
- Profit Factor (25% discount, lighter): 3.323
- **Sharpe ratio:** 2.956
- **Max drawdown:** 0.4869 (= 48.69%)
- **Win rate:** 0.567 (119 of 210)
- **CAGR:** 0.328
- **n_trades:** 210
- Replay window: 2016-01-04 -> 2022-12-30 (1729 trading days)

#### Gates (inspect)

| Gate | Observed | Required | Threshold | Raw | Discounted (30%) |
|---|---|---|---:|:---:|:---:|
| Profit Factor | raw 4.430 / disc 3.101 | > | 1.3 | PASS | PASS |
| Sharpe ratio | 2.956 | > | 1.0 | PASS | PASS |
| Max drawdown (mag) | 0.4869 | < | 0.15 | FAIL | FAIL |
| Win rate | 0.567 | > | 0.45 | PASS | PASS |

**Gates cleared (raw): 3 of 4** | **Gates cleared (30% discount): 3 of 4** <-- DEPLOY REFERENCE

#### Robustness suite (inspect)

| Question | Value |
|---|---:|
| Raw PF | 4.430 |
| PF with top-contributing symbol removed (ADANIENT.NS, Rs +1,019,348) | 3.263 |
| PF with best year removed (2022, Rs +2,429,445) | 1.907 |
| # symbols with net-negative PnL | 44 of 97 |
| Top-symbol share of gross-positive PnL | 26.3% |
| Top-year share of gross-positive PnL | 62.8% |

**Concentration flag:** one year (2022) carries 62.8% of gross PnL.

#### Momentum-crash DD diagnostic (inspect)

**Single deepest drawdown in equity curve:**

- Peak 2018-01-23 -> Trough 2020-03-23, magnitude **48.69%**

**Drawdown over historically-identified momentum-crash windows (calendar — not curve-fit):**

| Crash window | Range | Equity DD within window |
|---|---|---:|
| 2018 vol spike | 2018-01-22 -> 2018-10-31 | 21.62% |
| 2020-03 COVID | 2020-02-19 -> 2020-04-30 | 46.41% |
| 2022 reversal | 2022-01-01 -> 2022-07-31 | 23.37% |
| 2024 election | 2024-04-01 -> 2024-07-31 | _outside replay range_ |

#### Per-year breakdown (inspect)

| Year | n_trades | Win rate | PF | Total PnL (Rs) |
|---:|---:|---:|---:|---:|
| 2016 | 20 | 0.450 | 3.513 | +69,059 |
| 2017 | 31 | 0.742 | 11.848 | +301,220 |
| 2018 | 30 | 0.467 | 1.367 | +31,211 |
| 2019 | 39 | 0.590 | 1.046 | +7,893 |
| 2020 | 32 | 0.469 | 0.497 | -135,474 |
| 2021 | 28 | 0.679 | 7.462 | +292,912 |
| 2022 | 30 | 0.533 | 10.788 | +2,429,445 |


## 4. FULL window (for completeness)

### FULL window

- **Profit Factor (raw):** 2.599
- **★ Profit Factor (30% survivorship discount — HEADLINE):** **1.820**
- Profit Factor (25% discount, lighter): 1.950
- **Sharpe ratio:** 3.876
- **Max drawdown:** 0.4258 (= 42.58%)
- **Win rate:** 0.595 (181 of 304)
- **CAGR:** 0.248
- **n_trades:** 304
- Replay window: 2014-06-09 -> 2026-06-03 (2956 trading days)

#### Gates (full)

| Gate | Observed | Required | Threshold | Raw | Discounted (30%) |
|---|---|---|---:|:---:|:---:|
| Profit Factor | raw 2.599 / disc 1.820 | > | 1.3 | PASS | PASS |
| Sharpe ratio | 3.876 | > | 1.0 | PASS | PASS |
| Max drawdown (mag) | 0.4258 | < | 0.15 | FAIL | FAIL |
| Win rate | 0.595 | > | 0.45 | PASS | PASS |

**Gates cleared (raw): 3 of 4** | **Gates cleared (30% discount): 3 of 4** <-- DEPLOY REFERENCE

#### Robustness suite (full)

| Question | Value |
|---|---:|
| Raw PF | 2.599 |
| PF with top-contributing symbol removed (BHEL.NS, Rs +1,292,512) | 2.268 |
| PF with best year removed (2023, Rs +2,210,098) | 2.065 |
| # symbols with net-negative PnL | 43 of 109 |
| Top-symbol share of gross-positive PnL | 12.8% |
| Top-year share of gross-positive PnL | 21.8% |


#### Momentum-crash DD diagnostic (full)

**Single deepest drawdown in equity curve:**

- Peak 2020-01-24 -> Trough 2020-03-23, magnitude **42.58%**

**Drawdown over historically-identified momentum-crash windows (calendar — not curve-fit):**

| Crash window | Range | Equity DD within window |
|---|---|---:|
| 2018 vol spike | 2018-01-22 -> 2018-10-31 | 25.08% |
| 2020-03 COVID | 2020-02-19 -> 2020-04-30 | 42.30% |
| 2022 reversal | 2022-01-01 -> 2022-07-31 | 22.03% |
| 2024 election | 2024-04-01 -> 2024-07-31 | 18.51% |

#### Per-symbol breakdown (full window, top 30 by PnL)

**Top 30 contributors:**

| Symbol | n_trades | n_wins | Win rate | PF | Total PnL (Rs) |
|---|---:|---:|---:|---:|---:|
| BHEL.NS | 2 | 1 | 0.500 | 36424.406 | +1,292,512 |
| RECLTD.NS | 3 | 2 | 0.667 | 40314.299 | +916,336 |
| CGPOWER.NS | 4 | 3 | 0.750 | 43.657 | +845,185 |
| HAL.NS | 2 | 2 | 1.000 | inf (no losers) | +654,553 |
| ADANIENT.NS | 5 | 5 | 1.000 | inf (no losers) | +623,495 |
| DEEPAKNTR.NS | 5 | 4 | 0.800 | 494.333 | +491,640 |
| MUTHOOTFIN.NS | 6 | 6 | 1.000 | inf (no losers) | +463,359 |
| ADANIGREEN.NS | 4 | 2 | 0.500 | 4.959 | +448,333 |
| GLENMARK.NS | 3 | 2 | 0.667 | 24.051 | +403,627 |
| PERSISTENT.NS | 3 | 2 | 0.667 | 191.751 | +375,344 |
| VBL.NS | 7 | 5 | 0.714 | 1379.967 | +281,229 |
| KPITTECH.NS | 2 | 2 | 1.000 | inf (no losers) | +273,343 |
| TRENT.NS | 2 | 2 | 1.000 | inf (no losers) | +216,867 |
| COFORGE.NS | 5 | 2 | 0.400 | 34.607 | +211,727 |
| BHARTIARTL.NS | 3 | 2 | 0.667 | 190.579 | +206,341 |
| BHARATFORG.NS | 1 | 1 | 1.000 | inf (no losers) | +185,012 |
| FORTIS.NS | 2 | 1 | 0.500 | 46.259 | +174,866 |
| DIVISLAB.NS | 4 | 4 | 1.000 | inf (no losers) | +119,470 |
| BAJFINANCE.NS | 7 | 5 | 0.714 | 3.674 | +90,766 |
| MFSL.NS | 2 | 2 | 1.000 | inf (no losers) | +85,353 |
| LUPIN.NS | 3 | 1 | 0.333 | 2.840 | +82,059 |
| ABB.NS | 2 | 1 | 0.500 | 5.747 | +80,629 |
| JSWENERGY.NS | 2 | 2 | 1.000 | inf (no losers) | +70,168 |
| JINDALSTEL.NS | 6 | 4 | 0.667 | 27.633 | +64,045 |
| ASHOKLEY.NS | 4 | 3 | 0.750 | 499.637 | +60,697 |
| AKZOINDIA.NS | 3 | 2 | 0.667 | 12.990 | +44,246 |
| PFC.NS | 2 | 1 | 0.500 | 2.614 | +42,336 |
| PETRONET.NS | 1 | 1 | 1.000 | inf (no losers) | +34,151 |
| BAJAJFINSV.NS | 5 | 2 | 0.400 | 3.732 | +33,684 |
| RELIANCE.NS | 5 | 4 | 0.800 | 417.599 | +32,975 |

**Bottom 10 contributors:**

| Symbol | n_trades | n_wins | Win rate | PF | Total PnL (Rs) |
|---|---:|---:|---:|---:|---:|
| HEROMOTOCO.NS | 1 | 0 | 0.000 | 0.000 | -75,011 |
| DLF.NS | 3 | 1 | 0.333 | 0.000 | -79,600 |
| ADANIPOWER.NS | 10 | 7 | 0.700 | 0.583 | -91,513 |
| UPL.NS | 3 | 2 | 0.667 | 0.067 | -101,208 |
| BEL.NS | 5 | 3 | 0.600 | 0.317 | -101,749 |
| IRB.NS | 5 | 1 | 0.200 | 0.000 | -178,578 |
| MARUTI.NS | 1 | 0 | 0.000 | 0.000 | -191,448 |
| SIEMENS.NS | 3 | 1 | 0.333 | 0.007 | -365,614 |
| HINDPETRO.NS | 4 | 2 | 0.500 | 0.115 | -485,134 |
| VEDL.NS | 6 | 3 | 0.500 | 0.063 | -880,421 |

#### Per-year breakdown (full window)

| Year | n_trades | Win rate | PF | Total PnL (Rs) |
|---:|---:|---:|---:|---:|
| 2015 | 1 | 1.000 | inf (no losers) | +1,499 |
| 2016 | 20 | 0.550 | 1.877 | +30,866 |
| 2017 | 29 | 0.793 | 31.595 | +306,072 |
| 2018 | 31 | 0.581 | 1.062 | +8,727 |
| 2019 | 32 | 0.562 | 1.322 | +41,299 |
| 2020 | 33 | 0.485 | 0.495 | -114,804 |
| 2021 | 27 | 0.630 | 12.915 | +642,553 |
| 2022 | 20 | 0.450 | 2.804 | +480,096 |
| 2023 | 26 | 0.808 | 19.372 | +2,210,098 |
| 2024 | 27 | 0.593 | 7.683 | +1,925,159 |
| 2025 | 30 | 0.600 | 2.581 | +1,506,629 |
| 2026 | 28 | 0.464 | 0.519 | -804,918 |


## 5. Survivorship caveat

From `data/universe.py`:

> MOMENTUM_UNIVERSE is CURRENT membership, not point-in-time. Names that were in NIFTY 200 a decade ago but have since been delisted / merged out are entirely absent. MOM-3's backtest report MUST apply an explicit survivorship discount (10-30% PF haircut typical for current-membership universes) and label results accordingly. True PIT membership rotation is a separate later upgrade.

Discount applied (per ops): **30% conservative HEADLINE**, 25% lighter shown for comparison.

| Window | Raw PF | Disc 25% | Disc 30% (HEADLINE) |
|---|---:|---:|---:|
| FULL | 2.599 | 1.950 | **1.820** |
| INSPECT | 4.430 | 3.323 | **3.101** |
| HELD-OUT | 1.753 | 1.314 | **1.227** |

## 6. Proposed follow-up tickets (NOT applied per LAW 4)

- **T-candidate: portfolio-level DD cap for MOM** (reuse the MR-4 ``dd_cap_pct`` harness param). Held-out |max DD| = 34.2% vs the 15% gate. Note the symmetric re-arm caveat MR-4 surfaced.
- **T-candidate: trend-strength filter** -- only rebalance into names whose 12-1 score exceeds a MINIMUM (e.g. > 0.10). Filters out top-N selections that are merely 'least bad' in a bear market. Calibrate the threshold on INSPECT ONLY, then re-evaluate on HELD-OUT.

_End of report._