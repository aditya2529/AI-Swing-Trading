# MR-3 — Regime-Gated Mean-Reversion A/B Report

**Branch:** `feature/mr3-regime-gate`
**Single change vs MR-1 baseline:** `use_regime_gate=True` — block new entries unless ^NSEI close > its 50-DMA. All other parameters frozen at MR-1 values. No per-year tuning, no calibration.
**Replay data:** 25 universe equities + ^NSEI. MeanReversionStrategy skips '^'-prefixed symbols from trading regardless of the toggle, so ^NSEI is read for the regime check but never traded.
**Initial capital:** Rs 500,000

## 1. Per-year side-by-side (the decisive view)

Each year is its own honest walk-forward because the rules are parameter-free — no parameter was fit on any year to make another year look better.

| Year | Base n | Base PF | Base PnL (Rs) | Gated n | Gated PF | Gated PnL (Rs) | Δ n | Δ PnL (Rs) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2017 | 13 | 1.773 | +12,713 | 8 | 1.051 | +815 | -5 | -11,898 |
| 2018 | 5 | 0.219 | -14,725 | 2 | 0.000 | -12,544 | -3 | +2,180 |
| 2019 | 5 | 0.066 | -20,621 | 1 | 0.000 | -5,826 | -4 | +14,795 |
| 2020 | 7 | 1.456 | +5,575 | 4 | 1.291 | +1,621 | -3 | -3,953 |
| 2021 | 9 | 0.960 | -608 | 8 | 1.328 | +3,614 | -1 | +4,222 |
| 2022 | 5 | 0.124 | -19,232 | 2 | 0.000 | -16,653 | -3 | +2,579 |
| 2023 | 8 | 3.568 | +18,600 | 0 | n/a | +0 | -8 | -18,600 |
| 2024 | 18 | 1.405 | +9,381 | 8 | 0.972 | -143 | -10 | -9,524 |
| 2025 | 6 | 8.710 | +12,378 | 2 | inf | +9,903 | -4 | -2,475 |
| 2026 | 6 | 3.352 | +11,484 | 0 | n/a | +0 | -6 | -11,484 |

## 2. The three key questions (plain-English)

**Headline change:** baseline full-cycle PF 1.104, gated PF 0.736. Baseline n=82, gated n=35. Baseline |maxDD| 0.175, gated |maxDD| 0.103.

**Q1 — Did the bad years improve?** ([2018, 2019, 2022])

- **2018**: baseline PnL Rs -14,725 on n=5 → gated Rs -12,544 on n=2 (ΔPnL Rs +2,180, Δn -3) — **improved**.
- **2019**: baseline PnL Rs -20,621 on n=5 → gated Rs -5,826 on n=1 (ΔPnL Rs +14,795, Δn -4) — **improved**.
- **2022**: baseline PnL Rs -19,232 on n=5 → gated Rs -16,653 on n=2 (ΔPnL Rs +2,579, Δn -3) — **improved**.

**Summary:** 3 of 3 flagged bad years improved under the gate.

**Q2 — Did 2020 (the COVID-bounce year) get hurt?**

- **Yes** — 2020 baseline made Rs +5,575 on n=7, gated made Rs +1,621 on n=4 (ΔPnL Rs -3,953, Δn -3). The gate blocked the COVID-bounce wins, as the brief warned.

**Q3 — Net effect: better, worse, or a wash?**

- Total PnL: baseline Rs +14,945, gated Rs -19,213 (Δ Rs -34,158).
- PF: 1.104 → 0.736 (not better).
- |maxDD|: 0.175 → 0.103 (better).
- n_trades: 82 → 35 (fewer).

**Net verdict: max-DD improved but PF did not.** The gate trades good-year gains for bad-year safety. Whether that's a net win depends on the user's risk preference; this report does NOT call it a clear improvement.

## 3. Gated variant — full-cycle gate verdict

- **Profit Factor:** 0.736
- **Sharpe ratio:** -0.798
- **Max drawdown:** 0.1028 (= 10.28%)
- **Win rate:** 0.514 (18 of 35)
- **CAGR:** -0.004
- **n_trades:** 35

| Gate | Observed | Required | Threshold | Result |
|---|---|---|---:|:---:|
| Profit Factor | 0.736 | > | 1.3 | FAIL |
| Sharpe ratio | -0.798 | > | 1.0 | FAIL |
| Max drawdown (mag) | 0.1028 | < | 0.15 | PASS |
| Win rate | 0.514 | > | 0.45 | PASS |

**2 of 4 gates cleared** on the gated full-cycle replay.

## 4. Robustness suite (gated variant)

| Question | Value |
|---|---:|
| Raw PF | 0.736 |
| PF with top-contributing symbol removed (TATASTEEL.NS, Rs +17,273) | 0.479 |
| PF with best year removed (2025, Rs +9,903) | 0.600 |
| # symbols with net-negative PnL | 12 |
| Top-symbol share of gross-positive PnL | 32.2% |
| Top-year share of gross-positive PnL | 18.5% |

## 5. Significance (gated variant)

**Binomial test** (null: no edge → win rate 50%)

- Observed: 18 wins in 35 trades.
- P(X ≥ 18 | n=35, p=0.5) = **0.5000**
- p ≥ 0.10 — NOT statistically distinguishable from chance.

**Bootstrap CI on PF** (2000 resamples)

- 5th / 50th / 95th percentile: 0.343 / 0.721 / 1.533
- 90% CI **spans 1.0** — bootstrap cannot rule out break-even.

## 6. Survivorship caveat — gated raw vs discounted

From `data/universe.py`:

> Fixed-membership universe with fallen-from-index names retained. NOT a true point-in-time rotation and excludes fully-delisted tickers (unfetchable). Phase 3 must report PF raw AND survivorship-discounted, and label results accordingly.

| Quantity | Value |
|---|---:|
| Raw gated PF | 0.736 |
| Survivorship-discounted gated PF (× 0.85) | 0.626 |

Same 15% discount T3/MR-2 used; rationale there. Live PF typically runs 30-50% below backtest on top of this, so the practical live expectation is roughly the discounted number × 0.55-0.75.

## 7. Mined-data caveat (MANDATORY)

This historical data has now been looked at across **three lenses**: T3 (breakout), MR-2 (mean-reversion baseline + held-out split), and now MR-3 (the regime gate). Each look mines the same underlying price history. Even if THIS report's headline looks good, that's not a deploy signal — it's a CANDIDATE to be paper-traded forward on fresh data.

Concretely, the MR-2 held-out window (2023-2026) is now BURNED — it's been used to test the MR-1 baseline and (because MR-3 reads the full cycle) the gated variant too. The only honest forward test from this point is **live paper-trading on bars that don't yet exist in `market_data.db`**.

Per LAW 3: minimum **30 trades OR 4 weeks**, whichever is longer, before any real capital. Per the bootstrap doc: aim for held-out PF ≥ 1.5 as the safe-deploy bar.

_End of report._