# T1 — Corporate-Action Audit (read-only)

**Sprint:** swing phase 1 — first honest backtest.
**Branch:** `feature/t1-corp-action-audit`.
**Source:** `market_data.db` (27 symbols × ~10 years daily). yfinance `.splits` and `.dividends` as PRIMARY classifier.
**Method:** flag every daily |return| >= 12%; classify >= 20%; yfinance match within +-2 days primary, snapback/volume backstop.

## ADJUSTMENT VERDICT

**Our stored Upstox-historical daily data appears: ADJUSTED.**

Evidence — known yfinance splits, comparing the realised gap in our DB against the gap expected if data were UNADJUSTED:

| Symbol | Ex-date | yf ratio | Expected unadj gap | DB realised gap | Verdict |
|---|---|---:|---:|---:|---|
| INFY.NS | 2018-09-04 | 2.0:1 | -50.0% | +2.8% | ADJUSTED |
| WIPRO.NS | 2024-12-03 | 2.0:1 | -50.0% | -0.2% | ADJUSTED |
| TCS.NS | 2018-05-31 | 2.0:1 | -50.0% | -0.9% | ADJUSTED |

**Implication for T1.B:** no global re-adjustment needed. Apply only event-level fixes for non-split corp actions (demergers, value distributions) and keep real crashes intact.

**Side observation (informational, not for T1.B):** our DB prices are systematically higher than yfinance's `auto_adjust=True` closes across the verdict events. This is consistent with Upstox adjusting splits only while yfinance adjusts splits AND dividends. T2 must not naively mix yfinance live prices with Upstox historical without accounting for this offset.

## Heuristic limitations (read before trusting any row)

The classification logic is best-effort and known to be wrong in edge cases. Ops should not rubber-stamp any row.

- **yfinance corp-action data is incomplete.** It lists splits and dividends but not demergers / value distributions / spinoffs. The VEDL 2026-04-30 Vedanta demerger is NOT in yfinance and must be encoded as a canonical override (it is).
- **High volume does NOT rule out a corp action.** Trader repositioning on demerger days regularly produces 2-5x average volume. The heuristic now treats >=30% no-snapback moves as structural REGARDLESS of volume — the previous version would have mis-classified VEDL 2026-04-30 as `real_event`.
- **Snapback heuristic is direction-asymmetric.** A real crash with no recovery looks identical to a demerger on price shape alone. We default to `demerger_suspected → back-adjust` for extreme negative no-snapback moves; ops must verify with news context before T1.B applies the adjustment.
- **YESBANK is a known minefield.** The March 2020 SBI-led moratorium / rescue produced ~10 days of 20-60% intraday swings (real, not corp actions). The +58% and +45% rebounds on low volume look like our 'no clean fit' bucket and are marked `uncertain` — those need a human read against contemporaneous news, not a heuristic verdict.
- **Confidence labels are coarse.** 'medium' for a high-vol crisis day (SBIN +27.7% on 19.7x vol) and 'medium' for an ambiguous low-vol move both appear as 'medium'. Treat the reasoning text, not the label, as the audit signal.

## Classified events (127 total)

**Class distribution:**

| Class | Count | Default handling |
|---|---:|---|
| dividend_ex_date | 2 | keep-as-is |
| demerger_suspected | 2 | back-adjust |
| real_event | 16 | keep-as-is |
| uncertain | 2 | flag |
| watchlist | 103 | keep-as-is |

### Must-classify events (|return| >= 20%)

| Symbol | Date | Return | Prior→Close | Vol×20d | Next-day open | Class | Conf | Handling |
|---|---|---:|---|---:|---:|---|---|---|
| VEDL.NS | 2026-04-30 | -64.9% | 773.60→271.55 | 3.6× | 277.00 | demerger_known | high | back-adjust |
| YESBANK.NS | 2020-03-17 | +58.1% | 37.10→58.65 | 0.8× | 64.50 | uncertain | low | flag |
| YESBANK.NS | 2020-03-06 | -56.1% | 36.80→16.15 | 7.3× | 17.00 | real_event_known | high | keep-as-is |
| YESBANK.NS | 2020-03-16 | +45.2% | 25.55→37.10 | 0.6× | 40.80 | uncertain | low | flag |
| TATAMOTORS.NS | 2025-10-14 | -40.2% | 660.75→395.45 | 3.3× | 403.00 | demerger_suspected | medium | back-adjust |
| YESBANK.NS | 2020-03-11 | +35.5% | 21.25→28.80 | 1.6× | 28.70 | real_event | medium | keep-as-is |
| YESBANK.NS | 2019-10-03 | +32.8% | 32.00→42.50 | 2.3× | 45.00 | real_event | medium | keep-as-is |
| YESBANK.NS | 2020-03-09 | +31.6% | 16.15→21.25 | 3.2× | 23.35 | real_event | medium | keep-as-is |
| YESBANK.NS | 2019-02-14 | +30.6% | 169.45→221.25 | 4.5× | 226.90 | real_event | medium | keep-as-is |
| YESBANK.NS | 2019-04-30 | -29.2% | 237.20→168.00 | 6.4× | 162.00 | real_event | medium | keep-as-is |
| YESBANK.NS | 2018-09-21 | -29.0% | 319.20→226.50 | 10.6× | 236.50 | real_event | medium | keep-as-is |
| AXISBANK.NS | 2020-03-23 | -27.9% | 428.15→308.65 | 2.2× | 331.95 | real_event | medium | keep-as-is |
| SBIN.NS | 2017-10-25 | +27.7% | 254.45→324.90 | 19.7× | 330.00 | real_event | medium | keep-as-is |
| YESBANK.NS | 2020-03-05 | +25.6% | 29.30→36.80 | 5.3× | 33.15 | real_event | medium | keep-as-is |
| VEDL.NS | 2020-10-12 | -24.9% | 99.85→74.95 | 4.3× | 76.45 | real_event | medium | keep-as-is |
| VEDL.NS | 2020-03-23 | -23.9% | 53.45→40.65 | 0.7× | 45.50 | demerger_suspected | medium | back-adjust |
| YESBANK.NS | 2019-10-31 | +23.9% | 56.80→70.40 | 2.4× | 71.00 | real_event | medium | keep-as-is |
| YESBANK.NS | 2019-10-01 | -22.7% | 41.40→32.00 | 3.6× | 35.20 | real_event | medium | keep-as-is |
| BPCL.NS | 2018-10-05 | -21.2% | 155.10→122.15 | 10.8× | 126.00 | real_event | medium | keep-as-is |
| TATAMOTORS.NS | 2021-10-13 | +20.4% | 420.85→506.90 | 4.2× | 530.00 | real_event | medium | keep-as-is |
| ONGC.NS | 2020-03-20 | +20.4% | 55.30→66.60 | 2.8× | 54.90 | real_event | medium | keep-as-is |
| VEDL.NS | 2019-02-01 | -20.1% | 175.35→140.15 | 10.7× | 140.80 | real_event | medium | keep-as-is |

### Watchlist events (12–20%, light scrutiny)

| Symbol | Date | Return | Vol×20d | Class |
|---|---|---:|---:|---|
| AXISBANK.NS | 2020-03-12 | -12.3% | 3.3× | watchlist |
| AXISBANK.NS | 2020-04-07 | +19.5% | 1.4× | watchlist |
| AXISBANK.NS | 2020-04-17 | +13.3% | 1.1× | watchlist |
| AXISBANK.NS | 2020-05-27 | +13.4% | 1.8× | watchlist |
| BPCL.NS | 2018-10-04 | -13.1% | 2.1× | watchlist |
| BPCL.NS | 2019-09-23 | +12.3% | 3.1× | watchlist |
| BPCL.NS | 2020-03-12 | -15.7% | 2.1× | watchlist |
| BPCL.NS | 2020-03-23 | -16.3% | 0.8× | dividend_ex_date |
| BPCL.NS | 2020-03-31 | +16.6% | 1.4× | watchlist |
| BPCL.NS | 2020-07-17 | +13.4% | 8.9× | watchlist |
| BPCL.NS | 2024-06-04 | -12.9% | 2.6× | watchlist |
| CIPLA.NS | 2020-04-09 | +13.0% | 3.6× | watchlist |
| GAIL.NS | 2019-06-06 | -12.2% | 6.7× | watchlist |
| GAIL.NS | 2020-03-12 | -12.9% | 1.9× | watchlist |
| GAIL.NS | 2020-03-20 | +18.7% | 1.7× | watchlist |
| GAIL.NS | 2024-06-03 | +13.0% | 4.9× | watchlist |
| GAIL.NS | 2024-06-04 | -17.5% | 5.6× | watchlist |
| HDFCBANK.NS | 2020-03-23 | -12.6% | 1.4× | watchlist |
| HINDALCO.NS | 2020-03-12 | -12.8% | 1.6× | watchlist |
| HINDALCO.NS | 2020-03-23 | -16.7% | 1.1× | watchlist |
| HINDALCO.NS | 2020-04-07 | +17.1% | 1.9× | watchlist |
| HINDALCO.NS | 2024-02-13 | -12.4% | 8.0× | watchlist |
| HINDUNILVR.NS | 2020-04-07 | +13.5% | 2.5× | watchlist |
| ICICIBANK.NS | 2017-10-25 | +14.7% | 7.5× | watchlist |
| ICICIBANK.NS | 2020-03-23 | -17.8% | 1.6× | watchlist |
| ICICIBANK.NS | 2020-04-07 | +13.8% | 1.1× | watchlist |
| ICICIBANK.NS | 2021-02-01 | +12.4% | 3.2× | watchlist |
| INFY.NS | 2019-10-22 | -16.2% | 8.1× | watchlist |
| INFY.NS | 2020-03-24 | +12.0% | 1.6× | watchlist |
| ITC.NS | 2017-07-18 | -12.5% | 12.1× | watchlist |
| ITC.NS | 2020-03-23 | -12.1% | 1.5× | watchlist |
| LT.NS | 2020-03-23 | -16.3% | 1.4× | watchlist |
| LT.NS | 2024-06-04 | -12.7% | 2.8× | watchlist |
| LUPIN.NS | 2017-11-07 | -16.9% | 17.9× | watchlist |
| LUPIN.NS | 2020-04-03 | +13.3% | 2.4× | watchlist |
| LUPIN.NS | 2021-05-05 | +13.5% | 11.8× | watchlist |
| M&M.NS | 2020-04-07 | +14.4% | 1.6× | watchlist |
| M&M.NS | 2020-04-09 | +16.9% | 1.5× | watchlist |
| MARUTI.NS | 2020-03-23 | -16.9% | 1.3× | watchlist |
| MARUTI.NS | 2020-04-07 | +13.5% | 1.9× | watchlist |
| MARUTI.NS | 2020-04-09 | +13.4% | 2.4× | watchlist |
| ONGC.NS | 2018-10-05 | -15.2% | 5.0× | watchlist |
| ONGC.NS | 2020-03-09 | -16.9% | 3.7× | watchlist |
| ONGC.NS | 2020-03-12 | -13.9% | 2.7× | watchlist |
| ONGC.NS | 2020-03-18 | +15.0% | 4.8× | watchlist |
| ONGC.NS | 2020-03-23 | -17.9% | 0.7× | dividend_ex_date |
| ONGC.NS | 2020-04-30 | +14.5% | 1.9× | watchlist |
| ONGC.NS | 2022-03-07 | +13.6% | 5.5× | watchlist |
| ONGC.NS | 2022-07-01 | -14.1% | 3.5× | watchlist |
| ONGC.NS | 2024-06-04 | -16.8% | 5.3× | watchlist |
| RELIANCE.NS | 2020-03-09 | -12.3% | 4.1× | watchlist |
| RELIANCE.NS | 2020-03-23 | -13.2% | 1.0× | watchlist |
| RELIANCE.NS | 2020-03-25 | +14.7% | 1.7× | watchlist |
| SBIN.NS | 2020-03-12 | -13.3% | 1.6× | watchlist |
| SBIN.NS | 2020-03-13 | +13.8% | 2.4× | watchlist |
| SBIN.NS | 2020-03-23 | -13.5% | 0.8× | watchlist |
| SBIN.NS | 2024-06-04 | -14.4% | 6.2× | watchlist |
| TATAMOTORS.NS | 2018-10-09 | -13.2% | 10.5× | watchlist |
| TATAMOTORS.NS | 2019-02-08 | -17.6% | 8.2× | watchlist |
| TATAMOTORS.NS | 2019-10-27 | +16.4% | 0.8× | watchlist |
| TATAMOTORS.NS | 2019-10-29 | +16.8% | 3.9× | watchlist |
| TATAMOTORS.NS | 2020-03-23 | -14.4% | 0.4× | watchlist |
| TATAMOTORS.NS | 2020-04-30 | +19.3% | 3.0× | watchlist |
| TATAMOTORS.NS | 2020-06-05 | +12.4% | 2.9× | watchlist |
| TATAMOTORS.NS | 2021-02-02 | +15.2% | 1.6× | watchlist |
| TATAMOTORS.NS | 2021-10-07 | +12.0% | 5.7× | watchlist |
| TATASTEEL.NS | 2020-03-13 | +13.6% | 2.0× | watchlist |
| TATASTEEL.NS | 2022-05-23 | -12.6% | 3.5× | watchlist |
| VEDL.NS | 2020-02-28 | -15.2% | 4.6× | watchlist |
| VEDL.NS | 2020-03-09 | -18.1% | 2.1× | watchlist |
| VEDL.NS | 2020-03-12 | -17.4% | 1.8× | watchlist |
| VEDL.NS | 2020-03-16 | -14.8% | 1.4× | watchlist |
| VEDL.NS | 2020-03-20 | +12.9% | 1.0× | watchlist |
| VEDL.NS | 2020-04-09 | +13.5% | 1.3× | watchlist |
| VEDL.NS | 2020-04-30 | +18.2% | 1.9× | watchlist |
| VEDL.NS | 2020-05-04 | -14.2% | 0.9× | watchlist |
| VEDL.NS | 2020-05-12 | +16.4% | 3.4× | watchlist |
| VEDL.NS | 2020-10-07 | -12.6% | 6.1× | watchlist |
| VEDL.NS | 2021-10-18 | +13.8% | 4.5× | watchlist |
| VEDL.NS | 2022-05-17 | +12.8% | 2.6× | watchlist |
| VEDL.NS | 2022-06-20 | -13.8% | 4.0× | watchlist |
| WIPRO.NS | 2020-07-15 | +16.8% | 15.5× | watchlist |
| YESBANK.NS | 2019-06-13 | -13.1% | 2.8× | watchlist |
| YESBANK.NS | 2019-07-18 | -12.8% | 1.9× | watchlist |
| YESBANK.NS | 2019-08-22 | -13.9% | 2.1× | watchlist |
| YESBANK.NS | 2019-09-11 | +13.5% | 1.7× | watchlist |
| YESBANK.NS | 2019-09-19 | -15.6% | 1.5× | watchlist |
| YESBANK.NS | 2019-09-30 | -15.1% | 1.6× | watchlist |
| YESBANK.NS | 2019-10-17 | +15.5% | 1.3× | watchlist |
| YESBANK.NS | 2019-12-11 | -15.3% | 2.5× | watchlist |
| YESBANK.NS | 2020-03-12 | -13.0% | 1.2× | watchlist |
| YESBANK.NS | 2020-03-20 | -14.9% | 0.4× | watchlist |
| YESBANK.NS | 2020-03-23 | -13.3% | 0.3× | watchlist |
| YESBANK.NS | 2020-03-25 | -15.4% | 0.3× | watchlist |
| YESBANK.NS | 2020-04-20 | +17.3% | 1.3× | watchlist |
| YESBANK.NS | 2020-07-13 | -13.7% | 3.6× | watchlist |
| YESBANK.NS | 2020-07-23 | -19.2% | 14.2× | watchlist |
| YESBANK.NS | 2021-03-30 | +16.0% | 3.7× | watchlist |
| YESBANK.NS | 2021-09-14 | +12.2% | 7.7× | watchlist |
| YESBANK.NS | 2021-09-16 | +15.7% | 6.1× | watchlist |
| YESBANK.NS | 2022-04-06 | +13.1% | 4.3× | watchlist |
| YESBANK.NS | 2022-08-02 | +12.5% | 9.7× | watchlist |
| YESBANK.NS | 2022-12-13 | +13.5% | 6.1× | watchlist |
| YESBANK.NS | 2022-12-26 | +12.3% | 1.2× | watchlist |
| YESBANK.NS | 2024-02-07 | +17.3% | 5.0× | watchlist |

## Reasoning per must-classify event

### VEDL.NS  2026-04-30  (-64.9%)

- **Class:** demerger_known (confidence: high)
- **Recommended handling:** back-adjust
- **Reasoning:** CANONICAL OVERRIDE: Brief states this is the Vedanta demerger (5-way value distribution). yfinance has no record. Heuristic alone would mis-classify on volume; this override is the canonical answer.

### YESBANK.NS  2020-03-17  (+58.1%)

- **Class:** uncertain (confidence: low)
- **Recommended handling:** flag
- **Reasoning:** Move +58.1% >= 20% but neither yfinance corp action matched nor heuristics fit cleanly. Manual review required.

### YESBANK.NS  2020-03-06  (-56.1%)

- **Class:** real_event_known (confidence: high)
- **Recommended handling:** keep-as-is
- **Reasoning:** CANONICAL OVERRIDE: Brief states this is real — SBI-led moratorium / rescue announced. No corp action. The >=30% no-snapback heuristic would otherwise mis-classify it as demerger_suspected; this override prevents that.

### YESBANK.NS  2020-03-16  (+45.2%)

- **Class:** uncertain (confidence: low)
- **Recommended handling:** flag
- **Reasoning:** Move +45.2% >= 20% but neither yfinance corp action matched nor heuristics fit cleanly. Manual review required.

### TATAMOTORS.NS  2025-10-14  (-40.2%)

- **Class:** demerger_suspected (confidence: medium)
- **Recommended handling:** back-adjust
- **Reasoning:** EXTREME structural move (-40.2%), next-day open holds the new level (d_back_to_prior=+39.0%, d_to_close=+1.9%). volume×3.3 is informational only — at this magnitude with no snapback AND non-panic volume, a real crash would normally show partial recovery. yfinance lists no split/dividend. Strongly suggests an unlisted corp action (demerger / value distribution). Confidence medium pending news verification.

### YESBANK.NS  2020-03-11  (+35.5%)

- **Class:** real_event (confidence: medium)
- **Recommended handling:** keep-as-is
- **Reasoning:** Large move (+35.5%) on heavy volume (×1.6 20d-avg). No yfinance corp-action match. Consistent with a real event (news, crash, takeover collapse).

### YESBANK.NS  2019-10-03  (+32.8%)

- **Class:** real_event (confidence: medium)
- **Recommended handling:** keep-as-is
- **Reasoning:** Large move (+32.8%) on heavy volume (×2.3 20d-avg). No yfinance corp-action match. Consistent with a real event (news, crash, takeover collapse).

### YESBANK.NS  2020-03-09  (+31.6%)

- **Class:** real_event (confidence: medium)
- **Recommended handling:** keep-as-is
- **Reasoning:** Large move (+31.6%) on heavy volume (×3.2 20d-avg). No yfinance corp-action match. Consistent with a real event (news, crash, takeover collapse).

### YESBANK.NS  2019-02-14  (+30.6%)

- **Class:** real_event (confidence: medium)
- **Recommended handling:** keep-as-is
- **Reasoning:** Large move (+30.6%) on heavy volume (×4.5 20d-avg). No yfinance corp-action match. Consistent with a real event (news, crash, takeover collapse).

### YESBANK.NS  2019-04-30  (-29.2%)

- **Class:** real_event (confidence: medium)
- **Recommended handling:** keep-as-is
- **Reasoning:** Large move (-29.2%) on heavy volume (×6.4 20d-avg). No yfinance corp-action match. Consistent with a real event (news, crash, takeover collapse).

### YESBANK.NS  2018-09-21  (-29.0%)

- **Class:** real_event (confidence: medium)
- **Recommended handling:** keep-as-is
- **Reasoning:** Large move (-29.0%) on heavy volume (×10.6 20d-avg). No yfinance corp-action match. Consistent with a real event (news, crash, takeover collapse).

### AXISBANK.NS  2020-03-23  (-27.9%)

- **Class:** real_event (confidence: medium)
- **Recommended handling:** keep-as-is
- **Reasoning:** Large move (-27.9%) on heavy volume (×2.2 20d-avg). No yfinance corp-action match. Consistent with a real event (news, crash, takeover collapse).

### SBIN.NS  2017-10-25  (+27.7%)

- **Class:** real_event (confidence: medium)
- **Recommended handling:** keep-as-is
- **Reasoning:** Large move (+27.7%) on heavy volume (×19.7 20d-avg). No yfinance corp-action match. Consistent with a real event (news, crash, takeover collapse).

### YESBANK.NS  2020-03-05  (+25.6%)

- **Class:** real_event (confidence: medium)
- **Recommended handling:** keep-as-is
- **Reasoning:** Large move (+25.6%) on heavy volume (×5.3 20d-avg). No yfinance corp-action match. Consistent with a real event (news, crash, takeover collapse).

### VEDL.NS  2020-10-12  (-24.9%)

- **Class:** real_event (confidence: medium)
- **Recommended handling:** keep-as-is
- **Reasoning:** Large move (-24.9%) on heavy volume (×4.3 20d-avg). No yfinance corp-action match. Consistent with a real event (news, crash, takeover collapse).

### VEDL.NS  2020-03-23  (-23.9%)

- **Class:** demerger_suspected (confidence: medium)
- **Recommended handling:** back-adjust
- **Reasoning:** Large negative move (-23.9%), next-day open holds the new level (d_back_to_prior=+14.9%, d_to_close=+11.9%), volume×0.7 is not panic-shaped. yfinance lists no split/dividend near this date. Likely an unlisted corp action (demerger / value distribution).

### YESBANK.NS  2019-10-31  (+23.9%)

- **Class:** real_event (confidence: medium)
- **Recommended handling:** keep-as-is
- **Reasoning:** Large move (+23.9%) on heavy volume (×2.4 20d-avg). No yfinance corp-action match. Consistent with a real event (news, crash, takeover collapse).

### YESBANK.NS  2019-10-01  (-22.7%)

- **Class:** real_event (confidence: medium)
- **Recommended handling:** keep-as-is
- **Reasoning:** Large move (-22.7%) on heavy volume (×3.6 20d-avg). No yfinance corp-action match. Consistent with a real event (news, crash, takeover collapse).

### BPCL.NS  2018-10-05  (-21.2%)

- **Class:** real_event (confidence: medium)
- **Recommended handling:** keep-as-is
- **Reasoning:** Large move (-21.2%) on heavy volume (×10.8 20d-avg). No yfinance corp-action match. Consistent with a real event (news, crash, takeover collapse).

### TATAMOTORS.NS  2021-10-13  (+20.4%)

- **Class:** real_event (confidence: medium)
- **Recommended handling:** keep-as-is
- **Reasoning:** Large move (+20.4%) on heavy volume (×4.2 20d-avg). No yfinance corp-action match. Consistent with a real event (news, crash, takeover collapse).

### ONGC.NS  2020-03-20  (+20.4%)

- **Class:** real_event (confidence: medium)
- **Recommended handling:** keep-as-is
- **Reasoning:** Large move (+20.4%) on heavy volume (×2.8 20d-avg). No yfinance corp-action match. Consistent with a real event (news, crash, takeover collapse).

### VEDL.NS  2019-02-01  (-20.1%)

- **Class:** real_event (confidence: medium)
- **Recommended handling:** keep-as-is
- **Reasoning:** Large move (-20.1%) on heavy volume (×10.7 20d-avg). No yfinance corp-action match. Consistent with a real event (news, crash, takeover collapse).

## Sign-off (T1.B APPLIED 2026-06-03)

Ops reviewed and signed off with one rejection. T1.B has been applied on this branch.

### Final disposition

| Event | Heuristic class | Ops decision | T1.B action |
|---|---|---|---|
| VEDL.NS 2026-04-30 (-64.9%) | demerger_known | APPROVE | back-adjusted (factor 0.351021, 2451 pre-ex rows) |
| TATAMOTORS.NS 2025-10-14 (-40.2%) | demerger_suspected | APPROVE (verified: NIFTY -0.32%, price held) | back-adjusted (factor 0.598487, 2318 pre-ex rows) |
| VEDL.NS 2020-03-23 (-23.9%) | demerger_suspected | **REJECT** — COVID crash (NIFTY -13.0%, 23/26 universe down >5%, multi-session bounce) | keep-as-is; locked by regression test |
| YESBANK.NS 2020-03-06 (-56.1%) | real_event_known | KEEP (canonical) | keep-as-is; locked by regression test |
| 16 × real_event (YESBANK crisis, AXIS/SBIN/etc.) | real_event | KEEP | no change |
| YESBANK.NS 2020-03-16 +45.2%, 2020-03-17 +58.1% | uncertain | KEEP/flag (rescue rebounds) | no change; remain flagged |
| 105 watchlist (12-20%) | watchlist | no action | no change |

### Artifacts

- **Backup (LAW 7):** `backups/market_data_20260603T150529Z_pre_t1b.db`
- **Application log:** `logs/r1_t1b_application.md`
- **Regression test:** `tests/test_t1_corp_action_adjustments.py` (9 assertions, all GREEN)
- **Total tests:** 25 / 25 pass

### Locked invariants (these will RED any code that tries to "fix" a real crash)

- VEDL.NS daily return on 2020-03-23 == -23.95% (within 1e-4 tolerance) — COVID crash signal preserved through future scalar back-adjustments
- YESBANK.NS daily return on 2020-03-06 == -56.11% (within 1e-4 tolerance) — moratorium signal preserved
- YESBANK.NS 2020-03-06 absolute OHLCV unchanged from captured reference — YESBANK has no approved adjustment, so prices are literal

### T2 carry-forward (required note)

Upstox historical data is split-adjusted ONLY; yfinance live data is split- AND dividend-adjusted. T2 must NOT mix yfinance live with Upstox historical without correcting for the cumulative dividend offset (or it will detect phantom signals at every large ex-dividend date). This note also lives in the T1.B application log.

_End of report._