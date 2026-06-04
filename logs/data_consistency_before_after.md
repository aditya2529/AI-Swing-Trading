# Data-consistency fix — before / after MOM-3 + MOM-5 comparison

**Branch:** `feature/mom-data-consistency`
**Backup:** `backups/market_data_20260604T145054Z_pre_data_consistency.db` (78.1 MB, LAW 7)

## What changed in the data

For each of the 25 names in `POINT_IN_TIME_NSE25` (originally sourced
from Upstox, which is split-adjusted only), called
`fetch_and_store(sym, years=12, source='yfinance')` to overwrite with
yfinance's split- AND dividend-adjusted (total-return) series. This
brings the entire 136-name universe onto the same TR convention AND
matches the live yfinance feed (closing the bootstrap trap #6 seam:
"backtest data ≠ live data").

| Metric | Pre-fix | Post-fix | Delta |
|---|---:|---:|---:|
| DB total rows (1d) | 379,435 | 391,241 | +11,806 |
| DB size | 78.1 MB | 79.5 MB | +1.4 MB |
| 25 names succeeded | — | **24 / 25** | — |
| 25 names re-fetched per-symbol rows | 2473 | **2957** | +484 (~2 extra years 2014-2016) |
| 25 names date range | 2016-06-03 → 2026-06-02 | 2014-06-09 → 2026-06-03 | extended ~2y |

**Failed:** TATAMOTORS.NS — yfinance returned HTTP 404 (likely related
to the 2024 TML demerger). Three retries by the
retry-with-backoff adapter, all 404. The fetch failed CLEANLY (no
partial DB write), so TATAMOTORS.NS retains its pre-fix Upstox-sourced
series (2473 rows, split-only). This is a 1-of-136 residual
asymmetry — small enough that it should not materially affect the
cross-sectional ranking, but worth flagging.

**Sample evidence the conversion worked** (RELIANCE.NS):
* Pre-fix `AVG(close)` = 928.81 (split-adjusted only)
* Post-fix `AVG(close)` = 785.66 (split + dividend-adjusted)
* Lower because dividends compound BACKWARD as a haircut on
  historical prices — standard yfinance/Bloomberg TR convention.

## HEADLINE — MOM-3 baseline (no vol scaling), HELD-OUT 2023-01-02 → 2026-06-03

| Metric | PRE-FIX | POST-FIX | Delta | Notes |
|---|---:|---:|---:|---|
| PF (raw) | 1.986 | **1.753** | **-0.233** | edge cut ~12% |
| ★ **PF disc-30% (HEADLINE)** | **1.390 PASS** | **1.227 FAIL** | **-0.163** | **falls below 1.3 gate** |
| PF disc-25% (lighter) | 1.490 | 1.314 | -0.176 | still just above 1.3 |
| Sharpe | 2.946 | **3.002** | +0.056 | slightly up |
| \|max DD\| | 36.42% | **34.17%** | -2.25pp | small improvement |
| Win rate | 58.1% | **61.3%** | +3.2pp | up |
| CAGR | 18.6% | 14.7% | -3.9pp | down |
| n_trades | 105 | 106 | +1 | flat |
| Gates cleared (disc 30%) | 3 of 4 | **2 of 4** | -1 | **PF gate falls** |

## HEADLINE — MOM-5 vol-scaled (target=0.12), HELD-OUT

| Metric | PRE-FIX | POST-FIX | Delta | Notes |
|---|---:|---:|---:|---|
| PF (raw) | 2.084 | **1.743** | **-0.341** | edge cut ~16% |
| ★ **PF disc-30% (HEADLINE)** | **1.459 PASS** | **1.220 FAIL** | **-0.239** | **falls below 1.3 gate** |
| Sharpe | 3.067 | **3.236** | +0.169 | improved further |
| \|max DD\| | 33.42% | **33.39%** | -0.03pp | flat |
| CAGR | 19.4% | 16.5% | -2.9pp | down |
| n_trades | 124 | 134 | +10 | up |
| Mean exposure_mult | 0.661 | 0.645 | -0.016 | ~unchanged |
| Gates cleared (disc 30%) | 3 of 4 | **2 of 4** | -1 | **PF gate falls** |

## INSPECT (descriptive only)

| Metric | MOM-3 PRE | MOM-3 POST | MOM-5 PRE | MOM-5 POST |
|---|---:|---:|---:|---:|
| PF (raw) | 6.120 | **4.430** | 3.586 | 3.650 |
| \|max DD\| | 42.88% | **48.69%** | 43.13% | 42.30% |
| n_trades | 193 | 210 | 254 | 257 |

MOM-3 baseline INSPECT got NOISIER post-fix (PF down significantly, DD
slightly worse) — the cross-sectional ranking moved around once the 25
swing names were no longer systematically under-rated, and the 2014-2016
extension introduced data into a different regime. MOM-5 vol-scaled was
more stable across INSPECT (compact change), which is consistent with
the scaler smoothing some of the cross-sectional rank churn.

## Sensitivity (post-fix, HELD-OUT)

| Vol target | PF raw | PF disc-30% | \|max DD\| | Mean expo |
|---:|---:|---:|---:|---:|
| 0.10 | 1.743 | 1.220 | 34.32% | 0.578 |
| **0.12 ★ verdict** | **1.743** | **1.220 FAIL** | **33.39%** | **0.645** |
| 0.15 | 1.857 | 1.300 (borderline) | 32.59% | 0.730 |

Sensitivity result: vol_target=0.15 gives PF disc = 1.300 (exactly at
the gate). Picking 0.15 would be tuning post-fix and is not the verdict.

## How much was real edge vs data artifact?

| Quantity | Pre-fix | Post-fix | Inferred "data artifact" share |
|---|---:|---:|---:|
| Held-out PF raw (baseline) | 1.986 | 1.753 | ~12% of the PF was inflated |
| Held-out PF raw (vol-scaled) | 2.084 | 1.743 | ~16% of the PF was inflated |
| Held-out PF disc-30% (vol-scaled) | 1.459 | 1.220 | the gate-clearing margin (0.16) was largely an artifact |

The 30%-survivorship-discounted PF was BARELY clearing the 1.3 gate
pre-fix (margin = 0.09 on baseline, 0.16 on vol-scaled). The data
consistency fix consumed essentially that entire margin. Honest read:
the apparent gate-clearing was at least partially a data convention
artifact.

## Mechanism — WHY the edge dropped

The 25 Upstox-sourced names were systematically UNDER-RATED in the
cross-sectional 12-1 momentum ranking because their reported returns
omitted dividend reinvestment. The strategy preferred the
yfinance-sourced names (which had TR-adjusted higher returns) more
often than it should have. That over-selection happened to coincide
with names that genuinely momentum-paid out well in held-out — so the
apparent edge was a mixture of (a) real momentum and (b) cherry-picking
TR-adjusted names against split-only-adjusted names. Fix (a) survives
modestly; (b) disappears.

## VERDICT

**MOM is NOT a deploy candidate** at this calibration on this universe.
The data-consistency fix removed enough of the apparent edge to drop
the discounted-PF gate verdict from 3-of-4 (PRE) to 2-of-4 (POST). The
DD gate continues to fail. Sharpe and DD both improved slightly under
the fix (more honest, less mined), but the PF gate failure is the
deploy signal.

The standing rule per the MOM-5 brief was "MOM-5 is the LAST
historical experiment on MOM regardless of outcome -- pivot to paper".
That rule still applies, but the meaning has changed:

* PRE-FIX read: MOM is a borderline edge that paper trading would
  validate or refute.
* POST-FIX read: MOM does not clear deployable-edge thresholds even on
  consistent data; paper trading would need to OVERPERFORM the
  backtest to deploy, which contradicts the standard live-vs-backtest
  degradation expectation (~30-50% PF drop per the bootstrap doc).

## What this rules in / out

|  |  |
|---|---|
| **Confirmed** | The bootstrap trap #6 ("backtest data ≠ live data") was real and material on this project. Closing it changed the verdict. |
| **Confirmed** | Vol scaling is still a real Sharpe + DD improvement (post-fix Sharpe 3.236 vs baseline 3.002). Stays in the harness for future strategies. |
| **Ruled out** | MOM v1 (current strategy + vol-scaled harness) as a deploy candidate without further intervention. |
| **Open** | TATAMOTORS.NS is the only remaining inconsistency (1 of 136); whether to source it via Upstox-only, find an alternate yfinance ticker, or drop it from MOMENTUM_UNIVERSE is a small follow-up. |

## Test-suite fallout (4 failures, all in T1 corp-action layer)

The data fix broke 4 tests in `tests/test_t1_corp_action_adjustments.py`:
* `test_approved_demerger_continuity[VEDL.NS@2026-04-30]`
* `test_approved_demerger_pre_ex_prices_scaled[VEDL.NS@2026-04-30]`
* `test_rejected_event_daily_return_preserved[VEDL.NS@2020-03-23]`
* `test_yesbank_2020_03_06_absolute_prices_unchanged`

These tests asserted that specific Upstox-era ABSOLUTE prices (locked
by the T1.B back-adjustment script run during the swing project's
bootstrap) match the DB. After the yfinance re-fetch the DB no longer
contains those Upstox prices — yfinance returned its own
split-AND-dividend-adjusted (and presumably its own demerger-adjusted)
series for the same dates.

This is EXPECTED fallout from the data fix, not a strategy/harness
regression. The other 91 tests stay GREEN: every strategy test
(breakout / MR / momentum / MOM-4 filter / MOM-5 vol-scaling), the
replay harness contract, the look-ahead regression suite, the
validator suite, and the diagnostics suite are all unaffected.

T1.B back-adjustments were built ON TOP of Upstox split-only data
because Upstox didn't natively handle demergers. yfinance handles
splits, dividends, AND (for most events) demergers natively. So the
T1.B logic and its tests are conceptually superseded by the data
source change — but updating the test fixtures to the new prices is a
separate concern per LAW 4.

## Proposed follow-up (NOT applied per LAW 4)

1. **T1 test layer update** — either (a) recompute Upstox-era price
   constants from the new yfinance series and update assertions; or
   (b) verify yfinance's demerger handling is correct for VEDL on
   2026-04-30 and TATAMOTORS on 2025-10-14, then DELETE the T1.B
   layer entirely (the Upstox-era back-adjustment is no longer
   needed when the source is yfinance). Recommendation: (b) once
   yfinance's demerger handling is spot-checked, since the T1.B
   complexity exists only to compensate for Upstox's gaps.
2. **TATAMOTORS.NS** — try alternate yfinance tickers (`TATAMOTORS.BO`,
   `TATAMTRDVR.NS`) or drop from `MOMENTUM_UNIVERSE` per the same
   convention as LTIM (`MOMENTUM_DROPPED_DEAD_TICKERS`).
3. **Re-fetch the swing-25 universe via yfinance for MR strategies too**
   — same data convention issue applies to MR-2 baseline; would change
   MR-2's reported numbers. Same pattern: it would likely WORSEN the
   apparent edge (less optimistic, more honest).
4. **PIVOT TO PAPER** per the standing rule. Even with the lowered
   deploy bar, this honest result is more useful as input to a paper
   harness than as another historical fit. The paper harness validates
   live execution mechanics; whatever edge survives live trading is
   the real edge, full stop.

_End of report._
