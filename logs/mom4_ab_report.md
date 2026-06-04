# MOM-4 -- Dual-Momentum A/B (Absolute Filter ON vs OFF)

**Branch:** `feature/mom4-abs-filter`
**Strategy:** `signals.momentum.MomentumStrategy` -- MOM-2 baseline (filter OFF) vs MOM-4 dual-mom (``use_absolute_filter=True``, threshold=0 exactly, parameter-free per Antonacci).
**Replay data:** 136 MOMENTUM_UNIVERSE symbols.
**Initial capital:** Rs 500,000
**Wall-clock:** 47.7s (both legs, all windows).

## 0. Run parameters (matched to MOM-3 exactly)

| Param | Value |
|---|---:|
| max_positions | 15 (= MOM_TOP_N) |
| max_per_sector | 5 |
| max_heat | 0.20 |
| risk_pct / slippage / brokerage | (config defaults) |

Strategy knobs: lookback=252, skip=21, top_n=15 (MOM_TOP_N). Absolute filter threshold = 0 exactly -- NOT tuned. ONE change vs MOM-3 (LAW 4).

## 1. Windows

| Window | Range | Trading days |
|---|---|---:|
| INSPECT | 2016-01-04 -> 2022-12-30 | 1729 |
| **HELD-OUT (verdict)** | 2023-01-02 -> 2026-06-03 | 843 |
| FULL | 2014-06-09 -> 2026-06-03 | 2956 |

## 1b. Filter activity (would the filter fire?)

For each monthly rebalance day in each window, score the universe and count how many of the top-15 (by relative rank) had a non-positive 12-1 momentum score. These are the candidates the dual-momentum filter WOULD drop. Computed post-hoc, independent of the replay.

| Window | n_rebal_days | rebals with >=1 negative in top-15 | rebals with ALL top-15 negative |
|---|---:|---:|---:|
| INSPECT | 83 | 0 | 0 |
| HELD-OUT | 41 | 0 | 0 |
| FULL | 144 | 0 | 0 |

**The absolute filter at threshold=0 was a NO-OP on this universe — across every rebalance day in every window, all 15 top-ranked names had positive absolute momentum.** In a 136-name universe with broad sector coverage, somewhere there are always 15 names with positive 12-1 momentum, even in 2020 COVID and 2022 reversal. The filter cannot rescue MOM-3's drawdown because the drawdown mechanism is NOT 'we hold names with negative absolute momentum' — it is 'the relative winners themselves get whipsawed in fast reversals'.

## 2. HELD-OUT verdict (the primary read)

### THE HEADLINE QUESTION (HELD-OUT, 30%-discounted)

- Baseline |max DD|     = 36.42%   |  PF disc = 1.390  |  n_trades = 105
- Dual-mom |max DD|     = 36.42%   |  PF disc = 1.390  |  n_trades = 105

- Held-out |max DD| did NOT drop (36.42% -> 36.42% = +0.00pp).
- Dual-mom |max DD| **FAILS** the 15% gate.
- 30%-discounted PF survives the 1.3 gate (= 1.390).

**Verdict: filter was a NO-OP -- not the right intervention for this universe.** Across every rebalance day in every window, all 15 top-ranked names had positive absolute momentum, so the filter never fired. Trade tape, equity curve, and every metric are byte-identical between baseline and filtered. MOM-2 baseline's failure mode is not 'we hold names with negative absolute momentum' -- it is 'relative winners get whipsawed in fast reversals'. The Antonacci dual-momentum filter at threshold=0 cannot address that failure mode on this universe. Honest null result; follow-ups below propose interventions that target the actual failure mode.

_Mined-data caveat: this is the second walk-forward we have run on this universe and momentum is the most survivorship-sensitive strategy in the project. Even a clean held-out result is a CANDIDATE for paper-trade validation -- NOT a deploy. The 30% PF haircut may still flatter the read, since MOMENTUM_UNIVERSE is current membership, not point-in-time._

### Side-by-side -- held-out

### HELD-OUT

_2023-01-02 -> 2026-06-03, 843 trading days._

| Metric | Baseline (filter OFF) | Dual-mom (filter ON) | Delta |
|---|---:|---:|---:|
| ★ PF (30% disc, HEADLINE) | 1.390 PASS | 1.390 PASS | +0.000 |
| PF (raw) | 1.986 | 1.986 | +0.000 |
| Sharpe | 2.946 PASS | 2.946 PASS | +0.000 |
| |max DD| | 36.42% FAIL | 36.42% FAIL | +0.000% |
| Win rate | 0.581 PASS | 0.581 PASS | +0.000 |
| CAGR | 0.186 | 0.186 | +0.000 |
| n_trades | 105 | 105 | +0 |
| **Gates cleared (disc 30%)** | **3 of 4** | **3 of 4** | +0 |

**Book occupancy (% of trading days):**

| Occupancy | Baseline | Dual-mom | Delta |
|---|---:|---:|---:|
| Fully invested (== 15 positions) | 12.1% | 12.1% | +0.0pp |
| Partial (1 to 14) | 87.8% | 87.8% | +0.0pp |
| **Fully CASH (0 positions)** | **0.1%** | **0.1%** | +0.0pp |
| Mean positions | 11.36 | 11.36 | +0.00 |

**Per-year breakdown:**

| Year | Baseline n | Baseline PF | Baseline PnL | Dual n | Dual PF | Dual PnL |
|---:|---:|---:|---:|---:|---:|---:|
| 2023 | 17 | 2.383 | +95,052 | 17 | 2.383 | +95,052 |
| 2024 | 33 | 2.557 | +83,589 | 33 | 2.557 | +83,589 |
| 2025 | 30 | 2.166 | +119,923 | 30 | 2.166 | +119,923 |
| 2026 | 25 | 1.324 | +37,370 | 25 | 1.324 | +37,370 |

**Momentum-crash DD diagnostic:**

| Window | Baseline DD | Dual-mom DD | Delta |
|---|---:|---:|---:|
| 2024 election (2024-04-01 -> 2024-07-31) | 15.72% | 15.72% | +0.00pp |

_Deepest DD baseline: 2024-06-03 -> 2025-02-28 (36.42%) | dual-mom: 2024-06-03 -> 2025-02-28 (36.42%)_

## 3. INSPECT (descriptive)

### INSPECT

_2016-01-04 -> 2022-12-30, 1729 trading days._

| Metric | Baseline (filter OFF) | Dual-mom (filter ON) | Delta |
|---|---:|---:|---:|
| ★ PF (30% disc, HEADLINE) | 4.284 PASS | 4.284 PASS | +0.000 |
| PF (raw) | 6.120 | 6.120 | +0.000 |
| Sharpe | 3.040 PASS | 3.040 PASS | +0.000 |
| |max DD| | 42.88% FAIL | 42.88% FAIL | +0.000% |
| Win rate | 0.580 PASS | 0.580 PASS | +0.000 |
| CAGR | 0.408 | 0.408 | +0.000 |
| n_trades | 193 | 193 | +0 |
| **Gates cleared (disc 30%)** | **3 of 4** | **3 of 4** | +0 |

**Book occupancy (% of trading days):**

| Occupancy | Baseline | Dual-mom | Delta |
|---|---:|---:|---:|
| Fully invested (== 15 positions) | 3.2% | 3.2% | +0.0pp |
| Partial (1 to 14) | 95.7% | 95.7% | +0.0pp |
| **Fully CASH (0 positions)** | **1.2%** | **1.2%** | +0.0pp |
| Mean positions | 11.16 | 11.16 | +0.00 |

**Per-year breakdown:**

| Year | Baseline n | Baseline PF | Baseline PnL | Dual n | Dual PF | Dual PnL |
|---:|---:|---:|---:|---:|---:|---:|
| 2016 | 17 | 5.069 | +60,776 | 17 | 5.069 | +60,776 |
| 2017 | 27 | 6.861 | +224,842 | 27 | 6.861 | +224,842 |
| 2018 | 29 | 1.800 | +78,298 | 29 | 1.800 | +78,298 |
| 2019 | 34 | 2.227 | +162,668 | 34 | 2.227 | +162,668 |
| 2020 | 34 | 0.646 | -88,590 | 34 | 0.646 | -88,590 |
| 2021 | 23 | 11.267 | +690,266 | 23 | 11.267 | +690,266 |
| 2022 | 29 | 12.174 | +3,596,656 | 29 | 12.174 | +3,596,656 |

**Momentum-crash DD diagnostic:**

| Window | Baseline DD | Dual-mom DD | Delta |
|---|---:|---:|---:|
| 2018 vol spike (2018-01-22 -> 2018-10-31) | 17.97% | 17.97% | +0.00pp |
| 2020-03 COVID (2020-02-19 -> 2020-04-30) | 42.03% | 42.03% | +0.00pp |
| 2022 reversal (2022-01-01 -> 2022-07-31) | 20.06% | 20.06% | +0.00pp |

_Deepest DD baseline: 2020-01-20 -> 2020-03-23 (42.88%) | dual-mom: 2020-01-20 -> 2020-03-23 (42.88%)_

## 4. FULL (descriptive)

### FULL

_2014-06-09 -> 2026-06-03, 2956 trading days._

| Metric | Baseline (filter OFF) | Dual-mom (filter ON) | Delta |
|---|---:|---:|---:|
| ★ PF (30% disc, HEADLINE) | 2.267 PASS | 2.267 PASS | +0.000 |
| PF (raw) | 3.238 | 3.238 | +0.000 |
| Sharpe | 3.782 PASS | 3.782 PASS | +0.000 |
| |max DD| | 42.30% FAIL | 42.30% FAIL | +0.000% |
| Win rate | 0.555 PASS | 0.555 PASS | +0.000 |
| CAGR | 0.266 | 0.266 | +0.000 |
| n_trades | 299 | 299 | +0 |
| **Gates cleared (disc 30%)** | **3 of 4** | **3 of 4** | +0 |

**Book occupancy (% of trading days):**

| Occupancy | Baseline | Dual-mom | Delta |
|---|---:|---:|---:|
| Fully invested (== 15 positions) | 2.0% | 2.0% | +0.0pp |
| Partial (1 to 14) | 88.4% | 88.4% | +0.0pp |
| **Fully CASH (0 positions)** | **9.6%** | **9.6%** | +0.0pp |
| Mean positions | 10.44 | 10.44 | +0.00 |

**Per-year breakdown:**

| Year | Baseline n | Baseline PF | Baseline PnL | Dual n | Dual PF | Dual PnL |
|---:|---:|---:|---:|---:|---:|---:|
| 2015 | 1 | inf (no losers) | +1,499 | 1 | inf (no losers) | +1,499 |
| 2016 | 18 | 3.269 | +60,231 | 18 | 3.269 | +60,231 |
| 2017 | 24 | 9.186 | +260,895 | 24 | 9.186 | +260,895 |
| 2018 | 31 | 1.414 | +44,622 | 31 | 1.414 | +44,622 |
| 2019 | 32 | 1.731 | +113,710 | 32 | 1.731 | +113,710 |
| 2020 | 31 | 0.727 | -61,428 | 31 | 0.727 | -61,428 |
| 2021 | 26 | 16.407 | +787,248 | 26 | 16.407 | +787,248 |
| 2022 | 27 | 2.371 | +518,632 | 27 | 2.371 | +518,632 |
| 2023 | 30 | 4.194 | +1,591,341 | 30 | 4.194 | +1,591,341 |
| 2024 | 25 | 8.494 | +2,010,794 | 25 | 8.494 | +2,010,794 |
| 2025 | 27 | 2.784 | +1,079,304 | 27 | 2.784 | +1,079,304 |
| 2026 | 27 | 1.791 | +629,620 | 27 | 1.791 | +629,620 |

**Momentum-crash DD diagnostic:**

| Window | Baseline DD | Dual-mom DD | Delta |
|---|---:|---:|---:|
| 2018 vol spike (2018-01-22 -> 2018-10-31) | 20.55% | 20.55% | +0.00pp |
| 2020-03 COVID (2020-02-19 -> 2020-04-30) | 41.60% | 41.60% | +0.00pp |
| 2022 reversal (2022-01-01 -> 2022-07-31) | 26.12% | 26.12% | +0.00pp |
| 2024 election (2024-04-01 -> 2024-07-31) | 15.34% | 15.34% | +0.00pp |

_Deepest DD baseline: 2020-01-20 -> 2020-03-23 (42.30%) | dual-mom: 2020-01-20 -> 2020-03-23 (42.30%)_

## 5. Survivorship caveat

From `data/universe.py`:

> MOMENTUM_UNIVERSE is CURRENT membership, not point-in-time. Names that were in NIFTY 200 a decade ago but have since been delisted / merged out are entirely absent. MOM-3's backtest report MUST apply an explicit survivorship discount (10-30% PF haircut typical for current-membership universes) and label results accordingly. True PIT membership rotation is a separate later upgrade.

Discount applied (per ops MOM brief): **30% conservative HEADLINE**, 25% lighter shown for comparison.

| Window | Baseline PF raw | Baseline disc-30% | Dual-mom PF raw | Dual-mom disc-30% | Dual disc-25% |
|---|---:|---:|---:|---:|---:|
| INSPECT | 6.120 | 4.284 | 6.120 | **4.284** | 4.590 |
| HELD-OUT | 1.986 | 1.390 | 1.986 | **1.390** | 1.490 |
| FULL | 3.238 | 2.267 | 3.238 | **2.267** | 2.429 |

## 6. Significance -- dual-momentum held-out only

- Binomial p (n=105, wins=61): **0.0590**
- Bootstrap PF CI (2000 resamples) 5/50/95: 1.124 / 1.984 / 3.542
- 5th-percentile PF >= 1.0 -- pessimistic tail still positive.

## 7. Proposed follow-up tickets (NOT applied per LAW 4)

- **T-candidate: portfolio DD cap layered on dual-momentum.** Held-out |max DD| = 36.42% > 15% gate even with the filter. Reuse the MR-4 ``dd_cap_pct`` harness param. Note the symmetric re-arm trap MR-4 surfaced.

_End of report._