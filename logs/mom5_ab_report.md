# MOM-5 -- Vol-Scaled Sizing A/B (Barroso-Santa-Clara overlay)

**Branch:** `feature/mom5-vol-scaling`
**Strategy:** `signals.momentum.MomentumStrategy` (unchanged from MOM-2).
**Overlay (NEW):** harness-level vol scaling -- `run_replay(..., vol_target_annual, vol_window)`. Default OFF preserves prior behaviour byte-for-byte.
**Replay data:** 136 MOMENTUM_UNIVERSE symbols.
**Initial capital:** Rs 500,000
**Wall-clock:** 56.6s.

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

- Baseline: |max DD| = 34.17%  |  PF disc = 1.227  |  Sharpe = 3.002  |  CAGR = 0.147  |  n = 106
- Vol-scaled (target=0.12): |max DD| = 33.39%  |  PF disc = 1.220  |  Sharpe = 3.236  |  CAGR = 0.165  |  n = 134

- Held-out |max DD| DROPPED by 0.78pp (34.17% -> 33.39%).
- Vol-scaled |max DD| **FAILS** the 15% gate (observed 33.39%).
- 30%-discounted PF **fails** the 1.3 gate (= 1.220).
- Sharpe IMPROVED from 3.002 to 3.236 (BSC paper's primary claim reproduces).
- CAGR moved +1.85pp (0.147 -> 0.165) -- less exposure when vol bites, so CAGR is expected to come down somewhat.

**Verdict: mixed.** See gate-pass columns above. Not a deploy.

_Mined-data caveat: this is the FOURTH walk-forward on this universe (MR-2, MOM-3, MOM-4, MOM-5). Each test reduces the validity of further historical inference. Momentum is also the most survivorship-sensitive strategy in this project; the 30% PF haircut may still flatter the read. Even a clean win = paper-trade candidate, NOT a deploy. Per the standing rule, this is the last historical MOM experiment; we pivot to paper next regardless._

### Side-by-side -- held-out

### HELD-OUT

_2023-01-02 -> 2026-06-03, 843 trading days._

| Metric | Baseline (no vol) | Vol-scaled (target=0.12) | Delta |
|---|---:|---:|---:|
| ★ PF (30% disc, HEADLINE) | 1.227 FAIL | 1.220 FAIL | -0.006 |
| PF (raw) | 1.753 | 1.743 | -0.009 |
| Sharpe | 3.002 PASS | 3.236 PASS | +0.234 |
| |max DD| | 34.17% FAIL | 33.39% FAIL | -0.782% |
| Win rate | 0.613 PASS | 0.619 PASS | +0.006 |
| CAGR | 0.147 | 0.165 | +0.019 |
| n_trades | 106 | 134 | +28 |
| **Gates cleared (disc 30%)** | **2 of 4** | **2 of 4** | +0 |

**Exposure scaling -- vol-scaled run:**

| Stat | Value |
|---|---:|
| Mean exposure_mult | 0.645 |
| Median exposure_mult | 0.557 |
| % days scaled below 1.0 | 78.9% |
| % days scaled below 0.5 | 40.8% |
| Min exposure_mult | 0.271 (on 2024-06-07) |

**Per-year exposure -- vol-scaled run:**

| Year | n_days | Mean exposure | % days < 1.0 |
|---:|---:|---:|---:|
| 2023 | 245 | 0.781 | 73.9% |
| 2024 | 246 | 0.433 | 100.0% |
| 2025 | 248 | 0.743 | 59.7% |
| 2026 | 104 | 0.597 | 86.5% |

**Per-year PnL -- side-by-side:**

| Year | Base n | Base PF | Base PnL | Vol n | Vol PF | Vol PnL |
|---:|---:|---:|---:|---:|---:|---:|
| 2023 | 17 | 1.961 | +68,311 | 22 | 1.951 | +78,842 |
| 2024 | 32 | 2.591 | +81,603 | 38 | 3.818 | +110,334 |
| 2025 | 28 | 3.799 | +209,894 | 40 | 2.618 | +238,005 |
| 2026 | 29 | 0.633 | -69,242 | 34 | 0.484 | -93,070 |

**Momentum-crash DDs -- side-by-side:**

| Window | Baseline DD | Vol-scaled DD | Delta |
|---|---:|---:|---:|
| 2024 election (2024-04-01 -> 2024-07-31) | 16.79% | 15.29% | -1.50pp |

_Deepest DD baseline: 2024-06-03 -> 2025-02-28 (34.17%) | vol-scaled: 2024-09-03 -> 2025-02-28 (33.39%)_

## 3. INSPECT (descriptive)

### INSPECT

_2016-01-04 -> 2022-12-30, 1729 trading days._

| Metric | Baseline (no vol) | Vol-scaled (target=0.12) | Delta |
|---|---:|---:|---:|
| ★ PF (30% disc, HEADLINE) | 3.101 PASS | 2.555 PASS | -0.546 |
| PF (raw) | 4.430 | 3.650 | -0.780 |
| Sharpe | 2.956 PASS | 3.022 PASS | +0.066 |
| |max DD| | 48.69% FAIL | 42.30% FAIL | -6.389% |
| Win rate | 0.567 PASS | 0.560 PASS | -0.006 |
| CAGR | 0.328 | 0.307 | -0.020 |
| n_trades | 210 | 257 | +47 |
| **Gates cleared (disc 30%)** | **3 of 4** | **3 of 4** | +0 |

**Exposure scaling -- vol-scaled run:**

| Stat | Value |
|---|---:|
| Mean exposure_mult | 0.593 |
| Median exposure_mult | 0.575 |
| % days scaled below 1.0 | 95.3% |
| % days scaled below 0.5 | 26.7% |
| Min exposure_mult | 0.207 (on 2020-05-28) |

**Per-year exposure -- vol-scaled run:**

| Year | n_days | Mean exposure | % days < 1.0 |
|---:|---:|---:|---:|
| 2016 | 245 | 0.722 | 73.9% |
| 2017 | 248 | 0.604 | 100.0% |
| 2018 | 246 | 0.602 | 100.0% |
| 2019 | 244 | 0.641 | 100.0% |
| 2020 | 250 | 0.542 | 92.8% |
| 2021 | 248 | 0.543 | 100.0% |
| 2022 | 248 | 0.500 | 100.0% |

**Per-year PnL -- side-by-side:**

| Year | Base n | Base PF | Base PnL | Vol n | Vol PF | Vol PnL |
|---:|---:|---:|---:|---:|---:|---:|
| 2016 | 20 | 3.513 | +69,059 | 24 | 5.435 | +80,131 |
| 2017 | 31 | 11.848 | +301,220 | 32 | 10.704 | +274,675 |
| 2018 | 30 | 1.367 | +31,211 | 36 | 1.117 | +15,393 |
| 2019 | 39 | 1.046 | +7,893 | 42 | 1.249 | +33,355 |
| 2020 | 32 | 0.497 | -135,474 | 39 | 0.690 | -61,307 |
| 2021 | 28 | 7.462 | +292,912 | 36 | 8.794 | +592,506 |
| 2022 | 30 | 10.788 | +2,429,445 | 48 | 5.149 | +1,708,407 |

**Momentum-crash DDs -- side-by-side:**

| Window | Baseline DD | Vol-scaled DD | Delta |
|---|---:|---:|---:|
| 2018 vol spike (2018-01-22 -> 2018-10-31) | 21.62% | 21.49% | -0.12pp |
| 2020-03 COVID (2020-02-19 -> 2020-04-30) | 46.41% | 42.30% | -4.11pp |
| 2022 reversal (2022-01-01 -> 2022-07-31) | 23.37% | 21.77% | -1.60pp |

_Deepest DD baseline: 2018-01-23 -> 2020-03-23 (48.69%) | vol-scaled: 2020-02-19 -> 2020-03-23 (42.30%)_

## 4. FULL (descriptive)

### FULL

_2014-06-09 -> 2026-06-03, 2956 trading days._

| Metric | Baseline (no vol) | Vol-scaled (target=0.12) | Delta |
|---|---:|---:|---:|
| ★ PF (30% disc, HEADLINE) | 1.820 PASS | 1.718 PASS | -0.102 |
| PF (raw) | 2.599 | 2.454 | -0.146 |
| Sharpe | 3.876 PASS | 3.554 PASS | -0.322 |
| |max DD| | 42.58% FAIL | 41.36% FAIL | -1.222% |
| Win rate | 0.595 PASS | 0.586 PASS | -0.009 |
| CAGR | 0.248 | 0.229 | -0.019 |
| n_trades | 304 | 418 | +114 |
| **Gates cleared (disc 30%)** | **3 of 4** | **3 of 4** | +0 |

**Exposure scaling -- vol-scaled run:**

| Stat | Value |
|---|---:|
| Mean exposure_mult | 0.627 |
| Median exposure_mult | 0.581 |
| % days scaled below 1.0 | 85.4% |
| % days scaled below 0.5 | 32.9% |
| Min exposure_mult | 0.212 (on 2020-05-06) |

**Per-year exposure -- vol-scaled run:**

| Year | n_days | Mean exposure | % days < 1.0 |
|---:|---:|---:|---:|
| 2014 | 137 | 1.000 | 0.0% |
| 2015 | 246 | 0.837 | 34.6% |
| 2016 | 246 | 0.585 | 100.0% |
| 2017 | 248 | 0.622 | 100.0% |
| 2018 | 246 | 0.609 | 100.0% |
| 2019 | 244 | 0.663 | 98.4% |
| 2020 | 250 | 0.549 | 97.2% |
| 2021 | 248 | 0.544 | 100.0% |
| 2022 | 248 | 0.497 | 100.0% |
| 2023 | 245 | 0.621 | 98.4% |
| 2024 | 246 | 0.433 | 100.0% |
| 2025 | 248 | 0.744 | 57.7% |
| 2026 | 104 | 0.597 | 86.5% |

**Per-year PnL -- side-by-side:**

| Year | Base n | Base PF | Base PnL | Vol n | Vol PF | Vol PnL |
|---:|---:|---:|---:|---:|---:|---:|
| 2015 | 1 | inf (no losers) | +1,499 | 1 | inf (no losers) | +1,499 |
| 2016 | 20 | 1.877 | +30,866 | 33 | 2.786 | +62,007 |
| 2017 | 29 | 31.595 | +306,072 | 39 | 9.961 | +260,426 |
| 2018 | 31 | 1.062 | +8,727 | 39 | 1.933 | +83,640 |
| 2019 | 32 | 1.322 | +41,299 | 43 | 1.521 | +66,085 |
| 2020 | 33 | 0.495 | -114,804 | 40 | 0.647 | -77,156 |
| 2021 | 27 | 12.915 | +642,553 | 39 | 8.508 | +593,237 |
| 2022 | 20 | 2.804 | +480,096 | 35 | 2.229 | +426,404 |
| 2023 | 26 | 19.372 | +2,210,098 | 36 | 5.301 | +1,359,460 |
| 2024 | 27 | 7.683 | +1,925,159 | 36 | 6.145 | +1,056,537 |
| 2025 | 30 | 2.581 | +1,506,629 | 41 | 3.195 | +1,912,483 |
| 2026 | 28 | 0.519 | -804,918 | 36 | 0.495 | -612,205 |

**Momentum-crash DDs -- side-by-side:**

| Window | Baseline DD | Vol-scaled DD | Delta |
|---|---:|---:|---:|
| 2018 vol spike (2018-01-22 -> 2018-10-31) | 25.08% | 15.35% | -9.73pp |
| 2020-03 COVID (2020-02-19 -> 2020-04-30) | 42.30% | 41.36% | -0.94pp |
| 2022 reversal (2022-01-01 -> 2022-07-31) | 22.03% | 22.66% | +0.63pp |
| 2024 election (2024-04-01 -> 2024-07-31) | 18.51% | 15.89% | -2.62pp |

_Deepest DD baseline: 2020-01-24 -> 2020-03-23 (42.58%) | vol-scaled: 2020-02-19 -> 2020-03-23 (41.36%)_

## 5. Sensitivity (descriptive -- NOT the verdict)

These two targets are quoted purely so reviewers can see how the held-out result moves on either side of the pre-registered 0.12. Selecting any of them as the verdict would be tuning -- the verdict stays at 0.12.

| Target | PF raw | PF disc-30% | |max DD| | Sharpe | CAGR | n_trades | Mean expo |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.10 | 1.743 | 1.220 | 33.76% | 3.097 | 0.159 | 140 | 0.578 |
| 0.12 ★ | 1.743 | 1.220 | 33.39% | 3.236 | 0.165 | 134 | 0.645 |
| 0.15 | 1.857 | 1.300 | 32.59% | 2.856 | 0.178 | 118 | 0.730 |

## 6. Survivorship caveat

From `data/universe.py`:

> MOMENTUM_UNIVERSE is CURRENT membership, not point-in-time. Names that were in NIFTY 200 a decade ago but have since been delisted / merged out are entirely absent. MOM-3's backtest report MUST apply an explicit survivorship discount (10-30% PF haircut typical for current-membership universes) and label results accordingly. True PIT membership rotation is a separate later upgrade.

Discount applied (per ops): **30% conservative HEADLINE**.

## 7. Significance -- vol-scaled held-out only

- Binomial p (n=134, wins=83): **0.0036**
- Bootstrap PF CI (2000 resamples) 5/50/95: 0.981 / 1.744 / 3.240
- 90% CI **spans 1.0** -- bootstrap cannot rule out break-even.

## 8. Proposed follow-up tickets (NOT applied per LAW 4)

- **T-candidate: BSC vol scaling + portfolio DD cap (MR-4 `dd_cap_pct`).** Vol scaling addresses smooth-vol regimes; a DD cap catches the abrupt cliff-drops vol scaling can't see in time. Held-out vol-scaled |max DD| = 33.39% > 15%.
- **T-candidate: tighter sector / lower top_n.** If the discounted PF fell below the gate under scaling, the exposure cut may have removed too much of the concentrated edge that came from leverage-by-default sizing.

_End of report._