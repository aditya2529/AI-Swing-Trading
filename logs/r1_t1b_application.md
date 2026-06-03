# T1.B — Back-adjustment application log

**Timestamp:** 2026-06-03T15:05:29.666843Z
**Branch:** `feature/t1-corp-action-audit`
**Backup (LAW 7):** `backups\market_data_20260603T150529Z_pre_t1b.db`

## Applied adjustments

| Symbol | Ex-date | Factor | Pre-ex rows updated | prev close before | prev close after | ex close |
|---|---|---:|---:|---:|---:|---:|
| VEDL.NS | 2026-04-30 | 0.351021 | 2451 | 773.6000 | 271.5500 | 271.5500 |
| TATAMOTORS.NS | 2025-10-14 | 0.598487 | 2318 | 660.7500 | 395.4500 | 395.4500 |

**Volume handling:** unchanged. Demergers do not alter the parent ticker's share count or trading volume — the demerged entity becomes a separate ticker with its own volume history. Multiplying volume by the price factor would be wrong.

- **VEDL.NS 2026-04-30** — Vedanta demerger (5-way value distribution). Brief canonical.
- **TATAMOTORS.NS 2025-10-14** — TM CV/PV demerger (NCLT-approved Sept 2025). NIFTY -0.32% same day.

## Skipped / idempotent

_None._

## Post-state verification

- post-state: VEDL.NS continuity holds (return=+0.0000%)
- post-state: TATAMOTORS.NS continuity holds (return=+0.0000%)

## Carry-forward note for T2

Per the T1.A audit's side observation, Upstox historical OHLCV is split-adjusted but NOT dividend-adjusted; yfinance live prices ARE both split- AND dividend-adjusted. T2 strategy code must NOT naively mix yfinance live with Upstox historical without applying an offset correction (or it will detect phantom signals at every large dividend ex-date).

## Rollback

If T1.B needs to be rolled back, the pre-T1.B DB is preserved at
`backups\market_data_20260603T150529Z_pre_t1b.db` — restore with:

```powershell
Copy-Item 'backups\market_data_20260603T150529Z_pre_t1b.db' 'market_data.db' -Force
```