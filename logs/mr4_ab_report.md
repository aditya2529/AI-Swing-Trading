# MR-4 — Portfolio DD-Cap A/B Report

**Branch:** `feature/mr4-dd-cap`
**Single change vs MR-1 baseline:** `dd_cap_pct=0.10` on `run_replay`. Strategy code untouched. Regime gate OFF in both arms (it's dead — see MR-3 report).
**Pre-registered threshold:** `dd_cap_pct = 0.10` (chosen on principle as a standard 10% portfolio-drawdown circuit-breaker, NOT fit to results). 0.15 and 0.20 reported as descriptive sensitivity ONLY.
**Replay data:** 25 universe equities + ^NSEI (identical to MR-3 for clean comparison; ^NSEI never traded by the strategy's '^'-skip).
**Initial capital:** Rs 500,000

## 1. Per-year A/B (baseline vs DD-cap@10%)

Per-year is the honest walk-forward because the rules are parameter-free.

| Year | Base n | Base PF | Base PnL (Rs) | Cap n | Cap PF | Cap PnL (Rs) | Δ n | Δ PnL (Rs) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2017 | 13 | 1.773 | +12,713 | 13 | 1.773 | +12,713 | +0 | +0 |
| 2018 | 5 | 0.219 | -14,725 | 5 | 0.219 | -14,725 | +0 | +0 |
| 2019 | 5 | 0.066 | -20,621 | 5 | 0.066 | -20,621 | +0 | +0 |
| 2020 | 7 | 1.456 | +5,575 | 7 | 1.456 | +5,575 | +0 | +0 |
| 2021 | 9 | 0.960 | -608 | 9 | 0.960 | -608 | +0 | +0 |
| 2022 | 5 | 0.124 | -19,232 | 5 | 0.124 | -19,232 | +0 | +0 |
| 2023 | 8 | 3.568 | +18,600 | 0 | n/a | +0 | -8 | -18,600 |
| 2024 | 18 | 1.405 | +9,381 | 0 | n/a | +0 | -18 | -9,381 |
| 2025 | 6 | 8.710 | +12,378 | 0 | n/a | +0 | -6 | -12,378 |
| 2026 | 6 | 3.352 | +11,484 | 0 | n/a | +0 | -6 | -11,484 |

## 2. The three key questions (plain-English)

**Headline change (cap = 10%):** baseline PF 1.104 -> capped PF 0.654. Baseline n=82, capped n=44. |maxDD|: 0.175 -> 0.175.

**Q1 — Did the bad years bleed LESS?** ([2018, 2019, 2022])

- **2018**: baseline Rs -14,725 on n=5 -> capped Rs -14,725 on n=5 (Δ Rs +0) — **did NOT improve**.
- **2019**: baseline Rs -20,621 on n=5 -> capped Rs -20,621 on n=5 (Δ Rs +0) — **did NOT improve**.
- **2022**: baseline Rs -19,232 on n=5 -> capped Rs -19,232 on n=5 (Δ Rs +0) — **did NOT improve**.

**Summary:** 0 of 3 flagged bad years bled less.

**Q2 — Did the good years stay essentially intact?**

- **2017**: baseline Rs +12,713 -> capped Rs +12,713 (retained 100.0%) — **intact**.
- **2020**: baseline Rs +5,575 -> capped Rs +5,575 (retained 100.0%) — **intact**.
- **2023**: baseline Rs +18,600 -> capped Rs +0 (retained 0.0%) — **hit**.
- **2024**: baseline Rs +9,381 -> capped Rs +0 (retained 0.0%) — **hit**.
- **2025**: baseline Rs +12,378 -> capped Rs +0 (retained 0.0%) — **hit**.
- **2026**: baseline Rs +11,484 -> capped Rs +0 (retained 0.0%) — **hit**.

**Summary:** 2 of 6 good years essentially intact (≥95% retained).

**Q3 — Net effect: better, worse, or a wash?**

- Total PnL: baseline Rs +14,945 -> capped Rs -36,898 (Δ Rs -51,843).
- PF: 1.104 -> 0.654 (not better).
- |maxDD|: 0.175 -> 0.175 (not better).
- n_trades: 82 -> 44 (fewer).

**Net verdict: cap did not help.** The DD cap is a keeper for the harness anyway (good risk infra for any future strategy), but it did not improve MR's headline.

## 3. Capped variant — full-cycle headline (10%)

- **Profit Factor:** 0.654
- **Sharpe ratio:** -1.362
- **Max drawdown:** 0.1753 (= 17.53%)
- **Win rate:** 0.500 (22 of 44)
- **CAGR:** -0.008
- **n_trades:** 44

| Gate | Observed | Required | Threshold | Result |
|---|---|---|---:|:---:|
| Profit Factor | 0.654 | > | 1.3 | FAIL |
| Sharpe ratio | -1.362 | > | 1.0 | FAIL |
| Max drawdown (mag) | 0.1753 | < | 0.15 | FAIL |
| Win rate | 0.500 | > | 0.45 | PASS |

**1 of 4 gates cleared.**

## 4. Robustness suite (capped@10%)

| Question | Value |
|---|---:|
| Raw PF | 0.654 |
| PF with top-contributing symbol removed (TCS.NS, Rs +8,524) | 0.575 |
| PF with best year removed (2017, Rs +12,713) | 0.451 |
| # symbols with net-negative PnL | 14 |
| Top-symbol share of gross-positive PnL | 12.2% |
| Top-year share of gross-positive PnL | 18.2% |

## 5. Significance (capped@10%)

- Observed: 22 wins in 44 trades (win rate 0.500).
- Binomial P(X ≥ 22 | n=44, p=0.5) = **0.5598**.
- p ≥ 0.10 — NOT statistically distinguishable from chance.
- Bootstrap PF 5/50/95%: 0.350 / 0.650 / 1.184.
- 90% CI spans 1.0 — bootstrap cannot rule out break-even.

## 6. Sensitivity (descriptive ONLY — 0.15 / 0.20)

These rows are NOT the verdict. They exist so a reader can tell whether the 0.10 result is on a smooth curve or sitting on a knife's edge.

| dd_cap_pct | PF | Sharpe | \|maxDD\| | Win rate | n_trades |
|---:|---:|---:|---:|---:|---:|
| 0.00 (baseline) | 1.104 | 0.107 | 0.1753 | 0.585 | 82 |
| **0.10 (verdict)** | **0.654** | **-1.362** | **0.1753** | **0.500** | **44** |
| 0.15 | 1.104 | 0.107 | 0.1753 | 0.585 | 82 |
| 0.20 | 1.104 | 0.107 | 0.1753 | 0.585 | 82 |

## 7. Survivorship caveat — capped raw vs discounted

> Fixed-membership universe with fallen-from-index names retained. NOT a true point-in-time rotation and excludes fully-delisted tickers (unfetchable). Phase 3 must report PF raw AND survivorship-discounted, and label results accordingly.

| Quantity | Capped@10% value |
|---|---:|
| Raw PF | 0.654 |
| Survivorship-discounted PF (× 0.85) | 0.556 |

Same 15% discount T3/MR-2/MR-3 used. Live PF runs 30-50% below backtest on top of this.

## 8. Mined-data caveat (MANDATORY)

The historical data has now been mined **four times** (T3 breakout, MR-2 baseline + held-out split, MR-3 regime gate, this MR-4 DD cap). The MR-2 held-out window (2023-2026) is BURNED. Even a clean MR-4 result is a CANDIDATE for forward paper-trading, not a deploy. The one honest test left is live paper-trading on bars that don't yet exist in `market_data.db`.

Per LAW 3: minimum 30 trades OR 4 weeks live paper before any real capital. Per the bootstrap doc: live safe-deploy bar is PF ≥ 1.5.

Note: the DD cap is a KEEPER for the harness regardless of MR's verdict — it's general-purpose risk infra that every future strategy benefits from.

_End of report._