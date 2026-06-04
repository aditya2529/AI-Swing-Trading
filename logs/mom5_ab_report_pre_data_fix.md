# MOM-5 -- Vol-Scaled Sizing A/B (Barroso-Santa-Clara overlay)

**Branch:** `feature/mom5-vol-scaling`
**Strategy:** `signals.momentum.MomentumStrategy` (unchanged from MOM-2).
**Overlay (NEW):** harness-level vol scaling -- `run_replay(..., vol_target_annual, vol_window)`. Default OFF preserves prior behaviour byte-for-byte.
**Replay data:** 136 MOMENTUM_UNIVERSE symbols.
**Initial capital:** Rs 500,000
**Wall-clock:** 57.7s.

## 0. Pre-registered parameters (NOT tuned)

| Param | Value | Notes |
|---|---:|---|
| vol_target_annual | **0.12** | VERDICT runs against this value. |
| vol_window | 63 | ~3 trading months, BSC standard. |
| max_positions | 15 | matched to MOM-3 / MOM-4. |
| max_per_sector | 5 | matched. |
| max_heat | 0.20 | matched. |

Strategy knobs: lookback=252, skip=21, top_n=15. Sensitivity targets (0.10 and 0.15) reported as descriptive -- they are NOT the verdict.

## 1. Windows

| Window | Range | Trading days |
|---|---|---:|
| INSPECT | 2016-01-04 -> 2022-12-30 | 1729 |
| **HELD-OUT (verdict)** | 2023-01-02 -> 2026-06-03 | 843 |
| FULL | 2014-06-09 -> 2026-06-03 | 2956 |

## 2. HELD-OUT verdict (the primary read)

### THE HEADLINE QUESTION (HELD-OUT, 30%-discounted)

- Baseline: |max DD| = 36.42%  |  PF disc = 1.390  |  Sharpe = 2.946  |  CAGR = 0.186  |  n = 105
- Vol-scaled (target=0.12): |max DD| = 33.42%  |  PF disc = 1.459  |  Sharpe = 3.067  |  CAGR = 0.194  |  n = 124

- Held-out |max DD| DROPPED by 3.00pp (36.42% -> 33.42%).
- Vol-scaled |max DD| **FAILS** the 15% gate (observed 33.42%).
- 30%-discounted PF survives the 1.3 gate (= 1.459).
- Sharpe IMPROVED from 2.946 to 3.067 (BSC paper's primary claim reproduces).
- CAGR moved +0.87pp (0.186 -> 0.194) -- less exposure when vol bites, so CAGR is expected to come down somewhat.

**Verdict: PARTIAL WIN -- DD materially lower but still above gate.** The vol-scaling overlay cut the drawdown and preserved the discounted PF, but the remaining DD is still above the 15% gate. The mechanism works as advertised by BSC, just not enough on this universe at this calibration. Further intervention proposed below -- NOT applied here per LAW 4.

_Mined-data caveat: this is the FOURTH walk-forward on this universe (MR-2, MOM-3, MOM-4, MOM-5). Each test reduces the validity of further historical inference. Momentum is also the most survivorship-sensitive strategy in this project; the 30% PF haircut may still flatter the read. Even a clean win = paper-trade candidate, NOT a deploy. Per the standing rule, this is the last historical MOM experiment; we pivot to paper next regardless._

### Side-by-side -- held-out

### HELD-OUT

_2023-01-02 -> 2026-06-03, 843 trading days._

| Metric | Baseline (no vol) | Vol-scaled (target=0.12) | Delta |
|---|---:|---:|---:|
| ★ PF (30% disc, HEADLINE) | 1.390 PASS | 1.459 PASS | +0.069 |
| PF (raw) | 1.986 | 2.084 | +0.098 |
| Sharpe | 2.946 PASS | 3.067 PASS | +0.120 |
| |max DD| | 36.42% FAIL | 33.42% FAIL | -2.997% |
| Win rate | 0.581 PASS | 0.597 PASS | +0.016 |
| CAGR | 0.186 | 0.194 | +0.009 |
| n_trades | 105 | 124 | +19 |
| **Gates cleared (disc 30%)** | **3 of 4** | **3 of 4** | +0 |

**Exposure scaling -- vol-scaled run:**

| Stat | Value |
|---|---:|
| Mean exposure_mult | 0.661 |
| Median exposure_mult | 0.585 |
| % days scaled below 1.0 | 78.2% |
| % days scaled below 0.5 | 38.6% |
| Min exposure_mult | 0.288 (on 2024-06-18) |

**Per-year exposure -- vol-scaled run:**

| Year | n_days | Mean exposure | % days < 1.0 |
|---:|---:|---:|---:|
| 2023 | 245 | 0.802 | 73.9% |
| 2024 | 246 | 0.448 | 100.0% |
| 2025 | 248 | 0.753 | 57.3% |
| 2026 | 104 | 0.611 | 86.5% |

**Per-year PnL -- side-by-side:**

| Year | Base n | Base PF | Base PnL | Vol n | Vol PF | Vol PnL |
|---:|---:|---:|---:|---:|---:|---:|
| 2023 | 17 | 2.383 | +95,052 | 18 | 2.270 | +89,309 |
| 2024 | 33 | 2.557 | +83,589 | 36 | 3.865 | +114,372 |
| 2025 | 30 | 2.166 | +119,923 | 41 | 2.732 | +213,618 |
| 2026 | 25 | 1.324 | +37,370 | 29 | 0.706 | -34,992 |

**Momentum-crash DDs -- side-by-side:**

| Window | Baseline DD | Vol-scaled DD | Delta |
|---|---:|---:|---:|
| 2024 election (2024-04-01 -> 2024-07-31) | 15.72% | 14.46% | -1.26pp |

_Deepest DD baseline: 2024-06-03 -> 2025-02-28 (36.42%) | vol-scaled: 2024-09-03 -> 2025-02-28 (33.42%)_

## 3. INSPECT (descriptive)

### INSPECT

_2016-01-04 -> 2022-12-30, 1729 trading days._

| Metric | Baseline (no vol) | Vol-scaled (target=0.12) | Delta |
|---|---:|---:|---:|
| ★ PF (30% disc, HEADLINE) | 4.284 PASS | 2.510 PASS | -1.774 |
| PF (raw) | 6.120 | 3.586 | -2.534 |
| Sharpe | 3.040 PASS | 3.135 PASS | +0.096 |
| |max DD| | 42.88% FAIL | 43.13% FAIL | +0.249% |
| Win rate | 0.580 PASS | 0.559 PASS | -0.021 |
| CAGR | 0.408 | 0.309 | -0.098 |
| n_trades | 193 | 254 | +61 |
| **Gates cleared (disc 30%)** | **3 of 4** | **3 of 4** | +0 |

**Exposure scaling -- vol-scaled run:**

| Stat | Value |
|---|---:|
| Mean exposure_mult | 0.610 |
| Median exposure_mult | 0.587 |
| % days scaled below 1.0 | 93.4% |
| % days scaled below 0.5 | 25.6% |
| Min exposure_mult | 0.206 (on 2020-05-28) |

**Per-year exposure -- vol-scaled run:**

| Year | n_days | Mean exposure | % days < 1.0 |
|---:|---:|---:|---:|
| 2016 | 245 | 0.734 | 73.9% |
| 2017 | 248 | 0.650 | 100.0% |
| 2018 | 246 | 0.611 | 100.0% |
| 2019 | 244 | 0.688 | 82.0% |
| 2020 | 250 | 0.552 | 97.6% |
| 2021 | 248 | 0.533 | 100.0% |
| 2022 | 248 | 0.508 | 100.0% |

**Per-year PnL -- side-by-side:**

| Year | Base n | Base PF | Base PnL | Vol n | Vol PF | Vol PnL |
|---:|---:|---:|---:|---:|---:|---:|
| 2016 | 17 | 5.069 | +60,776 | 20 | 7.496 | +97,524 |
| 2017 | 27 | 6.861 | +224,842 | 30 | 13.132 | +250,584 |
| 2018 | 29 | 1.800 | +78,298 | 32 | 1.651 | +81,937 |
| 2019 | 34 | 2.227 | +162,668 | 43 | 0.976 | -3,974 |
| 2020 | 34 | 0.646 | -88,590 | 40 | 0.764 | -50,552 |
| 2021 | 23 | 11.267 | +690,266 | 40 | 7.044 | +544,844 |
| 2022 | 29 | 12.174 | +3,596,656 | 49 | 5.318 | +1,758,386 |

**Momentum-crash DDs -- side-by-side:**

| Window | Baseline DD | Vol-scaled DD | Delta |
|---|---:|---:|---:|
| 2018 vol spike (2018-01-22 -> 2018-10-31) | 17.97% | 21.51% | +3.54pp |
| 2020-03 COVID (2020-02-19 -> 2020-04-30) | 42.03% | 43.13% | +1.10pp |
| 2022 reversal (2022-01-01 -> 2022-07-31) | 20.06% | 18.56% | -1.50pp |

_Deepest DD baseline: 2020-01-20 -> 2020-03-23 (42.88%) | vol-scaled: 2020-02-19 -> 2020-03-23 (43.13%)_

## 4. FULL (descriptive)

### FULL

_2014-06-09 -> 2026-06-03, 2956 trading days._

| Metric | Baseline (no vol) | Vol-scaled (target=0.12) | Delta |
|---|---:|---:|---:|
| ★ PF (30% disc, HEADLINE) | 2.267 PASS | 1.713 PASS | -0.553 |
| PF (raw) | 3.238 | 2.448 | -0.790 |
| Sharpe | 3.782 PASS | 3.612 PASS | -0.170 |
| |max DD| | 42.30% FAIL | 42.45% FAIL | +0.154% |
| Win rate | 0.555 PASS | 0.572 PASS | +0.017 |
| CAGR | 0.266 | 0.207 | -0.059 |
| n_trades | 299 | 388 | +89 |
| **Gates cleared (disc 30%)** | **3 of 4** | **3 of 4** | +0 |

**Exposure scaling -- vol-scaled run:**

| Stat | Value |
|---|---:|
| Mean exposure_mult | 0.641 |
| Median exposure_mult | 0.604 |
| % days scaled below 1.0 | 84.1% |
| % days scaled below 0.5 | 29.4% |
| Min exposure_mult | 0.212 (on 2020-05-05) |

**Per-year exposure -- vol-scaled run:**

| Year | n_days | Mean exposure | % days < 1.0 |
|---:|---:|---:|---:|
| 2014 | 137 | 1.000 | 0.0% |
| 2015 | 246 | 0.837 | 34.6% |
| 2016 | 246 | 0.590 | 100.0% |
| 2017 | 248 | 0.624 | 100.0% |
| 2018 | 246 | 0.660 | 100.0% |
| 2019 | 244 | 0.680 | 83.6% |
| 2020 | 250 | 0.529 | 99.2% |
| 2021 | 248 | 0.532 | 100.0% |
| 2022 | 248 | 0.511 | 100.0% |
| 2023 | 245 | 0.656 | 98.0% |
| 2024 | 246 | 0.490 | 100.0% |
| 2025 | 248 | 0.752 | 54.8% |
| 2026 | 104 | 0.610 | 86.5% |

**Per-year PnL -- side-by-side:**

| Year | Base n | Base PF | Base PnL | Vol n | Vol PF | Vol PnL |
|---:|---:|---:|---:|---:|---:|---:|
| 2015 | 1 | inf (no losers) | +1,499 | 1 | inf (no losers) | +1,499 |
| 2016 | 18 | 3.269 | +60,231 | 23 | 1.927 | +26,895 |
| 2017 | 24 | 9.186 | +260,895 | 29 | 11.880 | +254,878 |
| 2018 | 31 | 1.414 | +44,622 | 35 | 1.232 | +23,840 |
| 2019 | 32 | 1.731 | +113,710 | 44 | 1.355 | +48,924 |
| 2020 | 31 | 0.727 | -61,428 | 40 | 0.526 | -102,043 |
| 2021 | 26 | 16.407 | +787,248 | 41 | 7.615 | +508,013 |
| 2022 | 27 | 2.371 | +518,632 | 34 | 2.979 | +516,870 |
| 2023 | 30 | 4.194 | +1,591,341 | 37 | 3.653 | +1,006,044 |
| 2024 | 25 | 8.494 | +2,010,794 | 38 | 5.391 | +918,434 |
| 2025 | 27 | 2.784 | +1,079,304 | 38 | 2.261 | +878,746 |
| 2026 | 27 | 1.791 | +629,620 | 28 | 0.754 | -144,832 |

**Momentum-crash DDs -- side-by-side:**

| Window | Baseline DD | Vol-scaled DD | Delta |
|---|---:|---:|---:|
| 2018 vol spike (2018-01-22 -> 2018-10-31) | 20.55% | 14.67% | -5.88pp |
| 2020-03 COVID (2020-02-19 -> 2020-04-30) | 41.60% | 42.12% | +0.52pp |
| 2022 reversal (2022-01-01 -> 2022-07-31) | 26.12% | 18.27% | -7.85pp |
| 2024 election (2024-04-01 -> 2024-07-31) | 15.34% | 13.73% | -1.61pp |

_Deepest DD baseline: 2020-01-20 -> 2020-03-23 (42.30%) | vol-scaled: 2018-08-31 -> 2020-03-23 (42.45%)_

## 5. Sensitivity (descriptive -- NOT the verdict)

These two targets are quoted purely so reviewers can see how the held-out result moves on either side of the pre-registered 0.12. Selecting any of them as the verdict would be tuning -- the verdict stays at 0.12.

| Target | PF raw | PF disc-30% | |max DD| | Sharpe | CAGR | n_trades | Mean expo |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.10 | 1.881 | 1.317 | 34.03% | 3.260 | 0.175 | 132 | 0.594 |
| 0.12 ★ | 2.084 | 1.459 | 33.42% | 3.067 | 0.194 | 124 | 0.661 |
| 0.15 | 2.221 | 1.555 | 32.69% | 3.292 | 0.206 | 117 | 0.756 |

## 6. Survivorship caveat

From `data/universe.py`:

> MOMENTUM_UNIVERSE is CURRENT membership, not point-in-time. Names that were in NIFTY 200 a decade ago but have since been delisted / merged out are entirely absent. MOM-3's backtest report MUST apply an explicit survivorship discount (10-30% PF haircut typical for current-membership universes) and label results accordingly. True PIT membership rotation is a separate later upgrade.

Discount applied (per ops): **30% conservative HEADLINE**.

## 7. Significance -- vol-scaled held-out only

- Binomial p (n=124, wins=74): **0.0192**
- Bootstrap PF CI (2000 resamples) 5/50/95: 1.210 / 2.072 / 3.756
- 5th-percentile PF >= 1.0 -- pessimistic tail still positive.

## 8. Proposed follow-up tickets (NOT applied per LAW 4)

- **T-candidate: BSC vol scaling + portfolio DD cap (MR-4 `dd_cap_pct`).** Vol scaling addresses smooth-vol regimes; a DD cap catches the abrupt cliff-drops vol scaling can't see in time. Held-out vol-scaled |max DD| = 33.42% > 15%.

_End of report._