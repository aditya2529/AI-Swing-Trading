# Weekly-Strategy Deep Research — Findings (2026-06-06)

**Question:** Which weekly-cadence systematic swing-trading strategies have documented,
robust, real-world edges for RETAIL equity traders (esp. India/NSE) that survive realistic
transaction costs AND survivorship bias?

**Method:** Multi-agent deep-research harness — 5 search angles, 21 sources fetched,
96 claims extracted, 25 adversarially verified (3-vote), 24 confirmed / 1 refuted. 103 agents.

---

## Bottom line
**No weekly long-only equity edge survives costs + survivorship for a retail trader.**
The only momentum that survives is **monthly and liquid**; the best non-equity option is
**monthly futures trend-following**. Weekly fails on costs, illiquidity, and anomaly decay.
This independently confirms our own 8 failed weekly experiments.

## Confirmed findings (high confidence, multiple primary peer-reviewed sources)
1. No weekly long-only edge clears costs for retail.
2. Surviving momentum is **monthly and liquid** (matches our deployed SMID monthly).
3. **Reversal and PEAD are the most cost-damaged** anomalies.
4. PEAD and Indian short-term reversals exist **only in illiquid stocks** you can't realistically trade.
5. **Monthly futures trend-following** (managed futures / CTA) is the credible non-equity diversifier.
6. Published anomalies **decay ~35% post-publication** (McLean–Pontiff).
7. Overfitting is effectively **guaranteed**; a weekly strategy needs **~20 years** of data to validate (Bailey/López de Prado; Harvey).
8. *(Medium confidence — single preprint)* Indian survivorship bias ≈ **4.94 percentage points per year** of inflated backtest returns.

## Refuted (killed by adversarial vote)
- Claim that Indian equity momentum exists *only* at intermediate/long horizons and not short — vote 1–2, not robust.

## India reality check
- **SEBI: 91% of retail F&O (derivatives) traders LOST money in FY25.** The "weekly options for daily action" path is where retail is statistically wiped out.

## Caveats
- Indian survivorship figure rests on one preprint.
- Trend-following evidence is from **futures, not NSE equities**.
- One claim was refuted.

## Key sources (primary unless noted)
- Journal of Financial Economics — trading-cost erosion of anomalies (S0304405X19301618)
- Financial Analysts Journal — anomaly persistence (faj.v65.n4.3)
- Gatev/Goetzmann/Rouwenhorst — Pairs Trading (Wharton)
- AQR — "A Century of Evidence on Trend-Following" (Hurst et al.) + "Demystifying Managed Futures"
- McLean & Pontiff — anomaly decay post-publication (HEC)
- Harvey & Liu — backtesting / multiple-testing (Duke)
- Bailey & López de Prado — probability of backtest overfitting
- SEBI / Business Standard — 91% retail F&O loss stat FY25
- backtestindia.com — India quality-momentum / factor backtests (blog)
- QuantInsti — cointegrated pairs trading on Indian equities (blog)
- arXiv preprint 2603.19380 — Indian survivorship bias

## Implication for us
Our **monthly SMID momentum** paper-deploy is the correct horse. "More frequent action"
does NOT translate to more profit; for honest retail money the edge lives at **monthly**
cadence. If diversification is wanted later, **futures trend-following** is the evidence-backed
add — not weekly equity churn, and emphatically not retail weekly options.
