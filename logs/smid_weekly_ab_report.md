# SMID-WEEKLY -- Cadence A/B (MONTHLY vs WEEKLY vs TRANCHED-4)

**Branch:** `feature/smid-weekly`
**Universe:** `SMID_UNIVERSE` (216 of 221 symbols loaded).
**Strategy core:** SmidMomentumStrategy (low-vol tilt + liquidity floor); only the rebalance cadence varies.
**Initial capital:** Rs 500,000
**Wall-clock:** 108.1s.

## 0. Pre-registered parameters (NOT tuned)

| Param | Value |
|---|---:|
| slippage_pct | 0.004 (40 bps, BRUTAL) |
| brokerage_pct | 0.0003 |
| max_positions | 15 |
| max_per_sector | 5 |
| max_heat | 0.20 |
| ★ SURVIVORSHIP discount (HEADLINE) | 45% (small-cap) |
| MONTHLY = first trading day each calendar month (SMOM-3 baseline) | |
| WEEKLY = first trading day of each ISO week | |
| TRANCHED-4 = 4 sleeves; ISO-week % 4 picks the active sleeve | |

## 1. HELD-OUT verdict (the primary read)

### THE HEADLINE QUESTIONS (HELD-OUT, 45%-discounted, brutal 40-bps costs)

- **MONTHLY    **: PF disc-45% = 1.398  |  |max DD| = 32.62%  |  Sharpe = 5.080  |  n = 157  |  cost = Rs 132,147
- **WEEKLY     **: PF disc-45% = 0.991  |  |max DD| = 31.45%  |  Sharpe = 3.797  |  n = 364  |  cost = Rs 251,294
- **TRANCHED-4 **: PF disc-45% = 1.026  |  |max DD| = 24.03%  |  Sharpe = 2.460  |  n = 110  |  cost = Rs 91,905

- **WEEKLY** FAILS the 1.3 disc-PF gate (= 0.991). The extra weekly costs ate into the edge.
- **TRANCHED-4** FAILS the disc-PF gate (= 1.026).
- Costs vs MONTHLY: WEEKLY = 1.90x, TRANCHED-4 = 0.70x. (A naive 'weekly = 4x monthly' assumption would be too pessimistic IF the ranks are sticky week-to-week.)

**Verdict: monthly remains the price of the edge.** Neither WEEKLY nor TRANCHED-4 clears the disc-PF gate after the brutal costs. Ops can have a weekly RHYTHM only by accepting an edge degradation that takes us back below the deploy bar — not worth it.

_Mined-data caveat: this is the 6th walk-forward on this DB. The cadence knob is constrained to monthly / weekly / tranched-4 by calendar / Antonacci convention (not tuned), so the marginal mining cost is bounded — but it is not zero. Small-cap 45% haircut may still be optimistic. Even a clean weekly result is paper-trade candidate, NEVER a deploy._

### Side-by-side -- held-out

### HELD-OUT

_2023-01-02 -> 2026-06-03, 843 trading days._

| Metric | MONTHLY | WEEKLY | TRANCHED-4 |
|---|---:|---:|---:|
| ★ PF disc-45% (HEADLINE) | 1.398 PASS | 0.991 FAIL | 1.026 FAIL |
| PF (raw) | 2.542 | 1.802 | 1.866 |
| Sharpe | 5.080 PASS | 3.797 PASS | 2.460 PASS |
| |max DD| | 32.62% FAIL | 31.45% FAIL | 24.03% FAIL |
| Win rate | 0.618 PASS | 0.538 PASS | 0.582 PASS |
| CAGR | 0.392 | 0.318 | 0.196 |
| n_trades | 157 | 364 | 110 |
| **Gates cleared (disc 45%)** | **3 of 4** | **2 of 4** | **2 of 4** |

**Cost drag (Rs paid to slippage + brokerage; approximation):**

| Cost component | MONTHLY | WEEKLY | TRANCHED-4 |
|---|---:|---:|---:|
| Slippage paid | Rs 122,928 | Rs 233,762 | Rs 85,493 |
| Brokerage paid | Rs 9,220 | Rs 17,532 | Rs 6,412 |
| **Total cost drag** | **Rs 132,147** | **Rs 251,294** | **Rs 91,905** |
| Gross turnover (entry+exit value) | Rs 30,731,896 | Rs 58,440,518 | Rs 21,373,358 |
| Cost vs MONTHLY (multiple) | 1.00x | 1.90x | 0.70x |

**Per-year PF:**

| Year | MONTHLY PF | MONTHLY n | WEEKLY PF | WEEKLY n | TRANCHED-4 PF | TRANCHED-4 n |
|---:|---:|---:|---:|---:|---:|---:|
| 2023 | 26.287 | 36 | 12.554 | 93 | 6.782 | 31 |
| 2024 | 5.999 | 49 | 1.956 | 121 | 2.187 | 30 |
| 2025 | 0.877 | 47 | 0.833 | 114 | 0.700 | 32 |
| 2026 | 1.922 | 25 | 1.700 | 36 | 2.155 | 17 |

**Momentum-crash DDs:**

| Crash window | MONTHLY DD | WEEKLY DD | TRANCHED-4 DD |
|---|---:|---:|---:|
| 2024 election | 11.20% | 12.32% | 5.95% |

## 2. Robustness + significance (held-out, per config)

**Robustness (MONTHLY, held-out):**

| Question | Value |
|---|---:|
| Raw PF | 2.542 |
| PF with top symbol removed (MCX.NS, Rs +134,301) | 2.345 |
| PF with best year removed (2024, Rs +693,816) | 1.612 |
| # symbols net-negative | 34 of 98 |
| Top-symbol share of gross PnL | 8.1% |
| Top-year share of gross PnL | 41.7% |

**Significance (MONTHLY, held-out):**

- Binomial p (n=157, wins=97): **0.0020**
- Bootstrap PF CI 5/50/95: 1.572 / 2.563 / 4.110

**Robustness (WEEKLY, held-out):**

| Question | Value |
|---|---:|
| Raw PF | 1.802 |
| PF with top symbol removed (LAURUSLABS.NS, Rs +139,240) | 1.660 |
| PF with best year removed (2023, Rs +401,492) | 1.392 |
| # symbols net-negative | 55 of 124 |
| Top-symbol share of gross PnL | 8.2% |
| Top-year share of gross PnL | 23.6% |

**Significance (WEEKLY, held-out):**

- Binomial p (n=364, wins=196): **0.0785**
- Bootstrap PF CI 5/50/95: 1.306 / 1.795 / 2.495

**Robustness (TRANCHED-4, held-out):**

| Question | Value |
|---|---:|
| Raw PF | 1.866 |
| PF with top symbol removed (SUZLON.NS, Rs +100,207) | 1.672 |
| PF with best year removed (2023, Rs +269,256) | 1.331 |
| # symbols net-negative | 20 of 50 |
| Top-symbol share of gross PnL | 11.3% |
| Top-year share of gross PnL | 30.4% |

**Significance (TRANCHED-4, held-out):**

- Binomial p (n=110, wins=64): **0.0523**
- Bootstrap PF CI 5/50/95: 1.203 / 1.882 / 2.969

## 3. INSPECT (descriptive)

### INSPECT

_2016-01-04 -> 2022-12-30, 1727 trading days._

| Metric | MONTHLY | WEEKLY | TRANCHED-4 |
|---|---:|---:|---:|
| ★ PF disc-45% (HEADLINE) | 1.444 PASS | 1.076 FAIL | 1.286 FAIL |
| PF (raw) | 2.626 | 1.957 | 2.337 |
| Sharpe | 4.625 PASS | 2.715 PASS | 3.874 PASS |
| |max DD| | 32.73% FAIL | 32.70% FAIL | 21.70% FAIL |
| Win rate | 0.562 PASS | 0.516 PASS | 0.621 PASS |
| CAGR | 0.267 | 0.211 | 0.168 |
| n_trades | 290 | 694 | 203 |
| **Gates cleared (disc 45%)** | **3 of 4** | **2 of 4** | **2 of 4** |

**Cost drag (Rs paid to slippage + brokerage; approximation):**

| Cost component | MONTHLY | WEEKLY | TRANCHED-4 |
|---|---:|---:|---:|
| Slippage paid | Rs 229,312 | Rs 334,096 | Rs 155,408 |
| Brokerage paid | Rs 17,198 | Rs 25,057 | Rs 11,656 |
| **Total cost drag** | **Rs 246,511** | **Rs 359,154** | **Rs 167,064** |
| Gross turnover (entry+exit value) | Rs 57,328,026 | Rs 83,524,076 | Rs 38,852,125 |
| Cost vs MONTHLY (multiple) | 1.00x | 1.46x | 0.68x |

**Per-year PF:**

| Year | MONTHLY PF | MONTHLY n | WEEKLY PF | WEEKLY n | TRANCHED-4 PF | TRANCHED-4 n |
|---:|---:|---:|---:|---:|---:|---:|
| 2016 | 2.525 | 32 | 0.767 | 89 | 0.848 | 30 |
| 2017 | 1.964 | 45 | 2.715 | 104 | 3.357 | 32 |
| 2018 | 2.104 | 37 | 0.971 | 81 | 0.830 | 28 |
| 2019 | 0.791 | 35 | 1.008 | 100 | 2.260 | 32 |
| 2020 | 2.391 | 39 | 2.649 | 85 | 2.516 | 31 |
| 2021 | 11.431 | 44 | 3.839 | 113 | 14.165 | 25 |
| 2022 | 1.940 | 58 | 1.749 | 122 | 1.301 | 25 |

**Momentum-crash DDs:**

| Crash window | MONTHLY DD | WEEKLY DD | TRANCHED-4 DD |
|---|---:|---:|---:|
| 2018 vol spike | 24.51% | 19.83% | 21.14% |
| 2020-03 COVID | 32.43% | 32.70% | 18.16% |
| 2022 reversal | 28.69% | 22.89% | 14.66% |

## 4. Survivorship caveat (LOUD)

> ⚠️ SMID_UNIVERSE is CURRENT NIFTY Midcap-150 + Smallcap-250 membership, NOT point-in-time. Survivorship bias is MUCH more severe at this end of the market than for large-caps because the BANKRUPTCY / DELIST tail is much fatter. Names that were liquid 10 years ago and have since delisted, merged out, or gone to zero are entirely absent. SMOM-3's backtest report MUST apply an explicit **45% PF haircut** as the HEADLINE discount (not the 30% used for MOMENTUM_UNIVERSE). State loudly. True PIT membership rotation is a separate later upgrade.

Headline discount = **45%** (small-cap). Discount table:

| Window | Cadence | PF raw | PF disc-45% (HEADLINE) |
|---|---|---:|---:|
| INSPECT | MONTHLY | 2.626 | **1.444** |
| INSPECT | WEEKLY | 1.957 | **1.076** |
| INSPECT | TRANCHED-4 | 2.337 | **1.286** |
| HELD-OUT | MONTHLY | 2.542 | **1.398** |
| HELD-OUT | WEEKLY | 1.802 | **0.991** |
| HELD-OUT | TRANCHED-4 | 1.866 | **1.026** |

_End of report._