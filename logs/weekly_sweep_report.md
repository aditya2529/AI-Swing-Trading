# WEEKLY-SWEEP — 6 weekly-cadence strategies x 2 windows

**Branch:** `feature/weekly-sweep`
**Universe:** MOMENTUM_UNIVERSE (136 loaded), weekday-filtered, total-return adjusted.
**Brutal costs:** slippage = 0.004 (40 bps), brokerage = 0.0003.
**Survivorship discount (HEADLINE):** 30% (MOM universe).
**Wall-clock:** 385.4s.

## 0. Multiple-comparisons guard

Six candidates are being tested at a single 1.3 disc-PF gate. With 6 independent tries, the chance that AT LEAST ONE clears by coincidence is meaningfully above what a single-strategy gate would allow. To control for that, a candidate is declared a real winner ONLY IF it meets ALL of:

1. disc-30% PF > 1.3 in **BOTH** INSPECT and HELD-OUT.
2. |max DD| < 15% in HELD-OUT.
3. PF with top-contributing symbol removed > 1.3 in HELD-OUT (edge not concentrated in one name).
4. Bootstrap 5th-percentile PF > 1.0 in HELD-OUT (pessimistic tail not break-even).
5. n_trades >= 30 in HELD-OUT (LAW 8 sample size).

Anything that wins one window only — even spectacularly — is NOT a candidate.

## 1. Master comparison table (the answer at a glance)

### Master comparison table (HELD-OUT primary)

| Strategy | Window | PF disc-30% | PF raw | Sharpe | |max DD| | Win | n_tr | trades/yr | Cost (Rs) | Gates |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| RSI2-MeanRev | INSPECT | 0.443 | 0.633 | -1.954 | 69.42% | 0.534 | 1548 | 225.6 | Rs 511,954 | 1/4 |
| RSI2-MeanRev | HELD-OUT | 0.444 | 0.634 | -2.105 | 48.51% | 0.517 | 755 | 225.7 | Rs 376,989 | 1/4 |
| ShortMomentum63 | INSPECT | 1.051 | 1.501 | 1.529 | 46.14% | 0.428 | 872 | 127.1 | Rs 472,768 | 1/4 |
| ShortMomentum63 | HELD-OUT | 1.130 | 1.614 | 2.307 | 22.15% | 0.439 | 387 | 115.7 | Rs 190,873 | 1/4 |
| WeeklyDonchian | INSPECT | 0.681 | 0.973 | 0.337 | 34.06% | 0.474 | 906 | 132.0 | Rs 408,466 | 1/4 |
| WeeklyDonchian | HELD-OUT | 0.540 | 0.772 | -0.900 | 41.99% | 0.408 | 441 | 131.8 | Rs 204,224 | 0/4 |
| GapReversal | INSPECT | 0.531 | 0.759 | -1.680 | 6.94% | 0.508 | 63 | 9.2 | Rs 23,134 | 2/4 |
| GapReversal | HELD-OUT | 0.365 | 0.521 | -1.807 | 3.13% | 0.414 | 29 | 8.7 | Rs 14,955 | 1/4 |
| SectorRotation | INSPECT | 0.879 | 1.255 | 1.281 | 47.90% | 0.453 | 954 | 139.0 | Rs 527,307 | 2/4 |
| SectorRotation | HELD-OUT | 0.800 | 1.142 | 1.526 | 33.09% | 0.432 | 387 | 115.7 | Rs 195,821 | 1/4 |
| Pullback52W | INSPECT | inf (no losers) | inf (no losers) | 0.000 | 0.12% | 1.000 | 1 | 0.1 | Rs 913 | 3/4 |
| Pullback52W | HELD-OUT | inf (no losers) | inf (no losers) | 0.000 | 0.00% | 0.000 | 0 | 0.0 | Rs 0 | 2/4 |

## 2. Pre-registered parameters (NOT tuned)

| Param | Value | Source |
|---|---:|---|
| RSI2 (trend_ma, rsi_period, oversold, exit, hold) | 200, 2, 10, 60, 5 | Connors RSI2 |
| ShortMomentum lookback | 63 | Carhart 97; ~1Q |
| WeeklyDonchian (lookback, vol_mult, chand, hold) | 20, 1.5, 3.0, 10 | Project breakout config |
| GapReversal (gap_th, trend_ma, hold) | 5%, 200, 3 | classic gap-down reversal |
| SectorRotation (lookback, top_sec, top_n) | 63, 3, 15 | GTAA sector momentum |
| Pullback52W (high, near%, rsi_os, rsi_x, hold) | 252, 5%, 40, 55, 10 | Minervini-style pullback |
| slippage / brokerage | 0.004 / config | BRUTAL impact |
| max_positions / max_sec / max_heat | 15 / 5 / 0.20 | matched to SMOM-3 |

## 3. Why each non-winner failed

- **RSI2-MeanRev** -- INSPECT disc-PF 0.443 <= 1.3; HELD-OUT disc-PF 0.444 <= 1.3; HELD-OUT |max DD| 48.51% >= 15%; HELD-OUT PF-with-top-symbol-removed 0.610 <= 1.3 (edge concentrated in one name); HELD-OUT bootstrap 5%-tile PF 0.532 <= 1.0 (pessimistic tail breaks even or worse)
- **ShortMomentum63** -- INSPECT disc-PF 1.051 <= 1.3; HELD-OUT disc-PF 1.130 <= 1.3; HELD-OUT |max DD| 22.15% >= 15%
- **WeeklyDonchian** -- INSPECT disc-PF 0.681 <= 1.3; HELD-OUT disc-PF 0.540 <= 1.3; HELD-OUT |max DD| 41.99% >= 15%; HELD-OUT PF-with-top-symbol-removed 0.726 <= 1.3 (edge concentrated in one name); HELD-OUT bootstrap 5%-tile PF 0.610 <= 1.0 (pessimistic tail breaks even or worse)
- **GapReversal** -- INSPECT disc-PF 0.531 <= 1.3; HELD-OUT disc-PF 0.365 <= 1.3; HELD-OUT PF-with-top-symbol-removed 0.441 <= 1.3 (edge concentrated in one name); HELD-OUT bootstrap 5%-tile PF 0.209 <= 1.0 (pessimistic tail breaks even or worse); HELD-OUT n_trades = 29 < 30 (LAW 8: too few trades for significance)
- **SectorRotation** -- INSPECT disc-PF 0.879 <= 1.3; HELD-OUT disc-PF 0.800 <= 1.3; HELD-OUT |max DD| 33.09% >= 15%; HELD-OUT PF-with-top-symbol-removed 1.084 <= 1.3 (edge concentrated in one name); HELD-OUT bootstrap 5%-tile PF 0.844 <= 1.0 (pessimistic tail breaks even or worse)
- **Pullback52W** -- HELD-OUT n_trades = 0 < 30 (LAW 8: too few trades for significance)

## 4. Robustness suite

**No candidates met all winner conditions.** The honest answer is: at this brutal cost regime and 30% survivorship haircut, NONE of the six weekly-cadence strategies cleared the deploy bar on BOTH inspect AND held-out windows with the robustness guard in place. Per-strategy reasons for failure are in §3.

## 5. Survivorship caveat

> MOMENTUM_UNIVERSE is CURRENT membership, not point-in-time. Names that were in NIFTY 200 a decade ago but have since been delisted / merged out are entirely absent. MOM-3's backtest report MUST apply an explicit survivorship discount (10-30% PF haircut typical for current-membership universes) and label results accordingly. True PIT membership rotation is a separate later upgrade.

Discount applied to PF (HEADLINE): **30%** (MOM universe).

## 6. Verdict

**No weekly-cadence winner.** At brutal 40bps costs and the 30% MOM survivorship discount, none of the six candidates cleared the multiple-comparisons guard. The honest answer to ops' question — 'is there a weekly-cadence edge on this universe?' — is **NO at this cost regime and at this universe scale**. The MONTHLY SMOM candidate from SMOM-3 remains the strongest signal in this project.

_Mined-data caveat: this is the 7th walk-forward on this DB. All six strategies use literature defaults — no project-specific tuning — so the marginal mining cost per strategy is small, but the comparison itself is the source of multiple-testing inflation. The guard above is how we control for that. Even a clean winner is paper-trade candidate, NEVER a deploy._

_End of report._