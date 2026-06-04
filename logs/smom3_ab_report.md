# SMOM-3 -- Small/Mid-Cap Momentum + Low-Vol Tilt A/B

**Branch:** `feature/smom3-smid-backtest`
**Universe:** `SMID_UNIVERSE` (216 of 221 symbols loaded from market_data.db).
**A/B legs:** `MomentumStrategy` (no tilt) vs `SmidMomentumStrategy` (low-vol tilt + liquidity sanity).
**Initial capital:** Rs 500,000
**Wall-clock:** 52.7s.

## 0. Pre-registered parameters (NOT tuned)

| Param | Value | Notes |
|---|---:|---|
| **slippage_pct** | **0.004** | BRUTAL — 40 bps; small-cap bid/ask + impact. |
| brokerage_pct | 0.0003 | config default. |
| max_positions | 15 | matched to MOM-3 / MOM-5. |
| max_per_sector | 5 | matched. |
| max_heat | 0.20 | matched. |
| ★ **SURVIVORSHIP discount (HEADLINE)** | **45%** | 45%, NOT 30% — small-caps have a much fatter bankruptcy/delist tail than MOM. |
| Survivorship discount (lighter, compare only) | 40% | |

SMID knobs (SMOM-2 module defaults): top_n=15, momentum_pool_multiplier=2 (pool=30), vol_window=63, min_median_traded_value=Rs 1 crore.

## 1. Windows

| Window | Range | Trading days |
|---|---|---:|
| INSPECT | 2016-01-04 -> 2022-12-30 | 1727 |
| **HELD-OUT (verdict)** | 2023-01-02 -> 2026-06-03 | 843 |
| FULL | 2014-06-09 -> 2026-06-03 | 2954 |

## 2. HELD-OUT verdict (the primary read)

### THE HEADLINE QUESTION (HELD-OUT, 45%-discounted, brutal costs)

- Momentum-only: PF disc-45% = 1.351  |  |max DD| = 40.54%  |  Sharpe = 3.459  |  n = 114
- SMID (low-vol tilt): PF disc-45% = **1.398**  |  |max DD| = **32.62%**  |  Sharpe = 5.080  |  n = 157

**Verdict: PARTIAL WIN — edge survives 45% haircut + brutal costs but DD gate still fails.** The discounted PF cleared the bar; the |max DD| did not drop below 15%. SMID's small-cap exposure is less violent than large-cap MOM but still above the deploy threshold.

- The low-vol tilt was ~flat vs momentum-only (disc-PF delta = +0.047).
- Tilt REDUCED |max DD| by 7.92pp.

_Mined-data caveat: this is the FIFTH walk-forward on this DB (MR-2, MOM-3, MOM-4, MOM-5, SMOM-3). SMOM is on a DIFFERENT universe than MOM, which softens the marginal mining cost — but it is STILL a mined-data read. Per the standing rule, even a clean win is a paper-trade candidate, NEVER a deploy. The 45% PF haircut may STILL be optimistic given small-cap survivorship dynamics — many of the names in SMID_UNIVERSE today did not exist 10 years ago, and many similar names from that era are entirely absent._

### Side-by-side -- held-out

### HELD-OUT

_2023-01-02 -> 2026-06-03, 843 trading days._

| Metric | Momentum-only | SMID (tilt) | Delta |
|---|---:|---:|---:|
| ★ PF disc-45% (HEADLINE) | 1.351 PASS | 1.398 PASS | +0.047 |
| PF (raw) | 2.457 | 2.542 | +0.085 |
| Sharpe | 3.459 PASS | 5.080 PASS | +1.620 |
| |max DD| | 40.54% FAIL | 32.62% FAIL | -7.917% |
| Win rate | 0.526 PASS | 0.618 PASS | +0.092 |
| CAGR | 0.372 | 0.392 | +0.019 |
| n_trades | 114 | 157 | +43 |
| **Gates cleared (disc 45%)** | **3 of 4** | **3 of 4** | +0 |

**Per-year breakdown:**

| Year | Mom n | Mom PF | Mom PnL | SMID n | SMID PF | SMID PnL |
|---:|---:|---:|---:|---:|---:|---:|
| 2023 | 17 | 5.410 | +133,344 | 36 | 26.287 | +284,462 |
| 2024 | 26 | 4.967 | +573,390 | 49 | 5.999 | +693,816 |
| 2025 | 42 | 0.953 | -17,030 | 47 | 0.877 | -51,187 |
| 2026 | 29 | 3.312 | +251,617 | 25 | 1.922 | +82,442 |

**Momentum-crash DDs:**

| Window | Range | Mom DD | SMID DD |
|---|---|---:|---:|
| 2024 election | 2024-04-01 -> 2024-07-31 | 15.94% | 11.20% |

_Deepest DD momentum: 2024-07-12 -> 2025-03-03 (40.54%) | SMID: 2024-12-19 -> 2025-03-10 (32.62%)_

**Robustness suite (SMID held-out):**

| Question | Value |
|---|---:|
| Raw PF | 2.542 |
| PF with top-contributing symbol removed (MCX.NS, Rs +134,301) | 2.345 |
| PF with best year removed (2024, Rs +693,816) | 1.612 |
| # symbols net-negative | 34 of 98 |
| Top-symbol share of gross PnL | 8.1% |
| Top-year share of gross PnL | 41.7% |

**Significance (SMID held-out):**

- Binomial p (n=157, wins=97): **0.0020**
- Bootstrap PF CI 5/50/95: 1.572 / 2.563 / 4.110
- 5th-percentile PF >= 1.0 — pessimistic tail still positive.

## 3. INSPECT (descriptive)

### INSPECT

_2016-01-04 -> 2022-12-30, 1727 trading days._

| Metric | Momentum-only | SMID (tilt) | Delta |
|---|---:|---:|---:|
| ★ PF disc-45% (HEADLINE) | 2.122 PASS | 1.444 PASS | -0.678 |
| PF (raw) | 3.859 | 2.626 | -1.233 |
| Sharpe | 3.613 PASS | 4.625 PASS | +1.012 |
| |max DD| | 36.31% FAIL | 32.73% FAIL | -3.580% |
| Win rate | 0.549 PASS | 0.562 PASS | +0.013 |
| CAGR | 0.357 | 0.267 | -0.089 |
| n_trades | 226 | 290 | +64 |
| **Gates cleared (disc 45%)** | **3 of 4** | **3 of 4** | +0 |

**Per-year breakdown:**

| Year | Mom n | Mom PF | Mom PnL | SMID n | SMID PF | SMID PnL |
|---:|---:|---:|---:|---:|---:|---:|
| 2016 | 33 | 1.089 | +6,261 | 32 | 2.525 | +78,620 |
| 2017 | 25 | 10.440 | +166,156 | 45 | 1.964 | +95,126 |
| 2018 | 21 | 9.080 | +392,507 | 37 | 2.104 | +161,627 |
| 2019 | 30 | 0.859 | -23,508 | 35 | 0.791 | -28,711 |
| 2020 | 40 | 1.411 | +108,774 | 39 | 2.391 | +184,248 |
| 2021 | 29 | 7.022 | +1,457,870 | 44 | 11.431 | +988,961 |
| 2022 | 48 | 4.340 | +1,434,300 | 58 | 1.940 | +554,753 |

**Momentum-crash DDs:**

| Window | Range | Mom DD | SMID DD |
|---|---|---:|---:|
| 2018 vol spike | 2018-01-22 -> 2018-10-31 | 34.03% | 24.51% |
| 2020-03 COVID | 2020-02-19 -> 2020-04-30 | 36.31% | 32.43% |
| 2022 reversal | 2022-01-01 -> 2022-07-31 | 27.19% | 28.69% |

_Deepest DD momentum: 2020-02-19 -> 2020-03-23 (36.31%) | SMID: 2018-08-28 -> 2020-03-23 (32.73%)_

## 4. FULL (descriptive)

### FULL

_2014-06-09 -> 2026-06-03, 2954 trading days._

| Metric | Momentum-only | SMID (tilt) | Delta |
|---|---:|---:|---:|
| ★ PF disc-45% (HEADLINE) | 1.660 PASS | 1.393 PASS | -0.266 |
| PF (raw) | 3.017 | 2.533 | -0.484 |
| Sharpe | 3.822 PASS | 4.945 PASS | +1.124 |
| |max DD| | 40.96% FAIL | 36.23% FAIL | -4.733% |
| Win rate | 0.523 PASS | 0.570 PASS | +0.047 |
| CAGR | 0.298 | 0.241 | -0.057 |
| n_trades | 348 | 460 | +112 |
| **Gates cleared (disc 45%)** | **3 of 4** | **3 of 4** | +0 |

**Per-year breakdown:**

| Year | Mom n | Mom PF | Mom PnL | SMID n | SMID PF | SMID PnL |
|---:|---:|---:|---:|---:|---:|---:|
| 2015 | 8 | 0.068 | -44,797 | 7 | 0.134 | -19,100 |
| 2016 | 37 | 0.746 | -24,262 | 39 | 1.382 | +35,918 |
| 2017 | 29 | 7.325 | +146,082 | 50 | 2.158 | +93,066 |
| 2018 | 22 | 3.004 | +210,244 | 37 | 2.116 | +145,489 |
| 2019 | 33 | 0.410 | -86,426 | 34 | 0.912 | -8,923 |
| 2020 | 36 | 1.556 | +86,781 | 47 | 1.681 | +90,695 |
| 2021 | 27 | 8.435 | +962,340 | 46 | 10.311 | +695,588 |
| 2022 | 38 | 3.965 | +1,161,767 | 43 | 1.682 | +260,950 |
| 2023 | 28 | 5.228 | +958,297 | 44 | 9.259 | +1,303,370 |
| 2024 | 25 | 5.810 | +3,988,406 | 50 | 5.820 | +2,625,914 |
| 2025 | 37 | 1.176 | +408,488 | 42 | 1.329 | +483,064 |
| 2026 | 28 | 5.270 | +2,380,710 | 21 | 1.131 | +76,289 |

**Momentum-crash DDs:**

| Window | Range | Mom DD | SMID DD |
|---|---|---:|---:|
| 2018 vol spike | 2018-01-22 -> 2018-10-31 | 32.93% | 24.71% |
| 2020-03 COVID | 2020-02-19 -> 2020-04-30 | 35.59% | 34.92% |
| 2022 reversal | 2022-01-01 -> 2022-07-31 | 29.42% | 28.77% |
| 2024 election | 2024-04-01 -> 2024-07-31 | 15.58% | 11.30% |

_Deepest DD momentum: 2024-07-15 -> 2025-03-03 (40.96%) | SMID: 2020-02-07 -> 2020-03-23 (36.23%)_

## 5. Survivorship caveat (LOUD)

From `data/universe.py`:

> ⚠️ SMID_UNIVERSE is CURRENT NIFTY Midcap-150 + Smallcap-250 membership, NOT point-in-time. Survivorship bias is MUCH more severe at this end of the market than for large-caps because the BANKRUPTCY / DELIST tail is much fatter. Names that were liquid 10 years ago and have since delisted, merged out, or gone to zero are entirely absent. SMOM-3's backtest report MUST apply an explicit **45% PF haircut** as the HEADLINE discount (not the 30% used for MOMENTUM_UNIVERSE). State loudly. True PIT membership rotation is a separate later upgrade.

Discount table -- HEADLINE bold:

| Window | Momentum raw | Mom disc-45% | SMID raw | SMID disc-45% (HEADLINE) | SMID disc-40% |
|---|---:|---:|---:|---:|---:|
| INSPECT | 3.859 | 2.122 | 2.626 | **1.444** | 1.576 |
| HELD-OUT | 2.457 | 1.351 | 2.542 | **1.398** | 1.525 |
| FULL | 3.017 | 1.660 | 2.533 | **1.393** | 1.520 |

## 6. Proposed follow-up tickets (NOT applied per LAW 4)

- **T-candidate: SMID + portfolio DD cap (MR-4 mechanism)** or **SMID + vol-scaling overlay (MOM-5)**. Both are orthogonal harness knobs. Held-out |max DD| = 32.62% > 15%.

_End of report._