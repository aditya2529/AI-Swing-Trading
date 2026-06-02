# AI Swing Trading System — Project Bootstrap & Context
**Paste this entire file into the first session of the new project. It carries every hard-won lesson from the intraday project so they are never repeated.**

Drafted: June 2, 2026 — after the intraday project hit a confirmed look-ahead bias that invalidated months of backtests.

---

## 0. READ THIS FIRST — Why this project exists

The predecessor project (AI Stock Market Analyzer, intraday) worked for months but was ultimately undermined by a **look-ahead bias bug** in the backtest harness: the replay sliced bars with `index <= clock` instead of `index < clock`, leaking the in-progress 5-min candle's close (which resolves 5 minutes in the future). This inflated backtest Profit Factor to 8.1 while live trading delivered 0.76 — a 10× gap.

**The single most important lesson:** A backtest that disagrees with live by a large margin is LYING until proven otherwise. Never trust a backtest number you have not reconciled against live behavior.

Swing trading is chosen as the next battle because:
- Daily bars carry real signal; 5-min bars are mostly noise.
- Multi-day holds dwarf brokerage + slippage costs (the killer of intraday edge).
- No competition with HFT firms on speed.
- ~70% of the existing codebase is reusable.
- The user needs a winnable fight after months of intraday grind.

Intraday is **paused, not abandoned.** It will be revisited later with the lessons below.

---

## 1. NON-NEGOTIABLE ENGINEERING LAWS (the anti-mistake guardrails)

These exist because each one was learned the hard way. Violating any of them is how the intraday project lost months.

### LAW 1 — No look-ahead, ever. Prove it.
- At decision time T, the model may use ONLY data with timestamp STRICTLY LESS THAN T (`index < T`, never `<=`).
- Bars are stamped at OPEN time. The bar at index T contains OHLC for the interval [T, T+1period) — its CLOSE only exists at T+1period. Using it at T is look-ahead.
- **Every backtest must include a synthetic-ascending-price regression test**: feed a monotonically rising series; if the strategy "predicts" the rise perfectly, look-ahead is present.
- Rolling-window features (moving averages, RSI, etc.) must be computed so the value at T excludes T's own unfinished bar.

### LAW 2 — Backtest must equal live, or the gap must be explained.
- Build the backtest as an **engine-replay** that runs the EXACT live decision code over historical bars — not a parallel shortcut implementation. (The intraday project's "shortcut harness" claimed PF 2.39 vs replay PF 0.02 — a 77× lie from missing engine gates.)
- After ANY backtest, compute the same metrics on live paper trades. If they diverge >2×, STOP and investigate before trusting either number.
- The deploy gate is the REPLAY PF, never a shortcut PF.

### LAW 3 — Paper-trade before real money. Always. No exceptions.
- A strategy must paper-trade live for a minimum of **30 trades or 4 weeks**, whichever is longer, and match its backtest within reason, before a single rupee of real money.

### LAW 4 — One change at a time.
- Never bundle a model retrain + a parameter change + a feature change in one experiment. When results shift, you must know WHICH change caused it.

### LAW 5 — Verify, don't assume.
- "Tests pass" ≠ "it works." Run the actual thing and observe real output.
- Don't trust a metric without checking the trade-by-trade tape behind it. (The intraday "88% win rate" was inflated by look-ahead — the trade tape revealed confidence was just measuring future-knowledge.)

### LAW 6 — Risk controls are sacred.
- Position sizing (e.g. 1-2% risk per trade), max concurrent positions, daily/weekly loss limits, and mandatory stop-losses are NOT to be changed casually or removed. Each change requires explicit sign-off + a worst-case loss calculation.

### LAW 7 — Reproducibility & backups.
- Every model artifact, config, and DB state that affects a decision must be committed or backed up with a timestamp before any change. Rollback must always be < 5 minutes.

### LAW 8 — Small sample ≠ signal.
- Do not draw conclusions from <30 trades. A binomial sanity check (could this win-rate happen by chance?) is mandatory before declaring success or failure.

---

## 2. WHAT SWING TRADING IS (the strategy definition)

| Parameter | Value |
|---|---|
| Bar timeframe | Daily (1d) |
| Hold period | 3–10 trading days (let winners run via trailing stop) |
| Universe | NSE stocks (25-symbol set, expandable) — use POINT-IN-TIME membership (see §2b) |
| Direction | Long-only (per prior PRD constraint; revisit later) |
| Entry signal | **Breakout Swing — simple RULES-BASED first, NOT ML (see §2a)** |
| Stop-loss | ATR-based (1.5–2× ATR of daily bars) — mandatory |
| Target / exit | **Trailing stop** (let winners run) — see §2b. Hard R:R floor ≥ 2:1 for entry screening |
| Position size | 1–2% portfolio risk per trade |
| Max positions | 5–8 concurrent (cap portfolio heat — see §2b) |
| Review cadence | Once daily after market close (NOT minute-by-minute) |
| Rebalance/exit check | Daily at close |

**Why daily bars win where 5-min lost:** multi-day trends are real and persistent; intraday 5-min moves are dominated by random noise and microstructure that retail cannot predict.

**IMPORTANT — published PF numbers are optimistic.** `SWING_STRATEGY_OPTIONS.md` quotes published breakout PF of 1.6–2.2. After the intraday look-ahead lesson, treat ALL third-party backtest numbers as inflated (look-ahead, survivorship, curve-fitting affect everyone's). Plan for real live PF roughly **30–50% below** any published figure — so target ~1.2–1.5 live, and be pleased if it's better.

---

## 2a. SIMPLE FIRST — rules-based, NOT ML (resolves the conflict)

`SWING_STRATEGY_OPTIONS.md` recommends a SIMPLE rules-based breakout and argues AGAINST ML-swing (it inherits the same C-extension threading fragility that crashed the intraday engine, for little added edge on daily bars). **That recommendation wins. Build the simple version first.**

- **Phases 1–3: build a pure rules-based breakout strategy.** No XGBoost, no LSTM, no training pipeline. Deterministic entry/exit rules only — testable, debuggable, you understand every trade, and it cannot crash on a thread-unsafe library.
- **Only AFTER** the rules-based version is backtested honestly AND paper-traded: optionally test whether an ML filter on top *provably beats* it on the honest harness. If it doesn't beat it by a clear margin, do NOT add ML.
- Where this section disagrees with any "reuse the ML ensemble" wording elsewhere in this doc, **this section wins: simple first.**

---

## 2b. SWING TRADING CRAFT — the non-negotiable trade filters

A breakout strategy without these is just a noise generator. Each is mandatory, learned from real swing trading, not just code.

### Volume confirmation (the #1 fakeout filter)
- A breakout on LOW volume is almost always a fakeout that reverses.
- Rule: only take a breakout when the day's volume > ~1.5× the 20-day average volume. Skip clean-looking breakouts that lack volume.

### Regime filter (this alone can double real-world PF)
- Breakouts nearly all WIN in trending/bull markets and nearly all FAIL in choppy/sideways markets — they cluster by regime.
- Rule: only take long breakouts when NIFTY 50 is above its own 50-day moving average. When NIFTY is below its 50-DMA, stand aside.

### Trailing-stop mechanics (breakout swing LIVES on this)
- The whole edge is "cut losers fast, let winners run." A fixed target caps winners and kills the edge.
- Specify concretely. Recommended: **Chandelier Exit** = (highest high since entry) − (3 × ATR). Exit on a close below it.
- Keep the initial hard stop (1.5–2× ATR below entry) until the rising trail overtakes it, then the trail governs.

### Earnings / gap risk (overnight holds are exposed)
- Overnight + weekend holds face gap risk from news/earnings.
- Rule: check the earnings calendar. Don't enter within ~2 trading days of a known earnings date; consider exiting/halving an open position into earnings. Accept that gaps can occasionally blow through a stop for >1× ATR — size for it.

### Portfolio correlation / heat
- 5–8 breakout positions can be highly correlated (same hot sector) → they fail together in a turn, far exceeding per-trade risk.
- Rule: cap total open risk (sum of all positions' risk) at ~6–8%. Max 2–3 positions per sector.

### Point-in-time universe (avoid survivorship bias)
- Backtesting on today's 25 liquid winners overstates results — they're in the list BECAUSE they trended.
- Use historical (point-in-time) universe membership including later-delisted names. If unavailable, explicitly discount the backtest PF for survivorship.

---

## 3. ARCHITECTURE — reuse vs rebuild

### Reuse from the intraday project (~70%)
- Data pipeline (`data/`) — DB schema, ingestion, validator. Daily bars already supported.
- Data adapters — yfinance (live), Upstox historical (10 years of daily data available).
- Feature engineering core (`features/engineer.py`) — BUT audit every feature for look-ahead on daily bars.
- Risk management (`signals/risk.py`) — position sizing, SL/target calc.
- Paper trading engine (`paper_trading/`) — adapt the tick loop to a daily-close loop.
- Dashboard (`dashboard/`, `api/`) — reuse, adjust for swing cadence.
- Alerts (`alerts/`) — Telegram/email, reuse as-is.
- **The engine-replay backtest harness** — the crown jewel. Reuse it, but DO NOT blindly copy the intraday `index < clock` fix. The correct slice depends on the daily bar-stamping convention + the exact decision moment (see LAW 1). Verify the convention first, then derive the right slice and lock it with the synthetic-ascending-price test.

### Rebuild / new
- Model training on DAILY labels (label = N-day-ahead return, not 5-min).
- Daily-close decision loop (runs once/day, not every 5 min).
- Swing-specific exit logic (time-based exit after max hold, trailing stop optional).
- Backtest must be daily-bar engine-replay with the look-ahead fix.

### Tech stack (proven, keep)
Python 3.11+, XGBoost + LSTM + HMM + LR ensemble (or simplify — evaluate if the full ensemble is worth it on daily bars), SQLite (TimescaleDB later), SHAP for explainability.

---

## 4. THE BUILD PLAN (phased, one thing at a time per LAW 4)

### Phase 0 — Foundation & honest harness
- Set up the new repo. Port the reusable code.
- **First commit priority:** the engine-replay backtest harness WITH the `index < clock` look-ahead fix AND the synthetic-ascending-price regression test (LAW 1).
- Backfill 10 years of daily bars for the universe (Upstox + yfinance).
- Gate: the look-ahead regression test must pass before any strategy work.

### Phase 1 — Label & feature definition
- Define swing labels: e.g. BUY if forward N-day return > +X%, SELL if < -X%, scaled to daily volatility.
- Audit EVERY feature for look-ahead on daily bars (LAW 1).
- Gate: a feature-causality test proving no feature at T uses data ≥ T.

### Phase 2 — Train v1 swing model
- Train ensemble on daily bars, walk-forward split (train past → test future, never overlapping).
- Gate: walk-forward holdout that is genuinely out-of-sample.

### Phase 3 — Backtest on the honest harness
- Run engine-replay over a multi-year holdout.
- Report PF, Sharpe, win rate, max DD, trade count, avg win/loss.
- Mandatory: binomial sanity check (LAW 8) + trade-tape inspection (LAW 5).
- Gate: PF > 1.3 on TRUSTWORTHY (replay, no look-ahead) numbers.

### Phase 4 — Paper trade live
- Deploy to paper engine. Daily-close decisions.
- Watch for 30 trades / 4 weeks (LAW 3).
- Reconcile live PF vs backtest PF (LAW 2). If they diverge >2×, STOP.

### Phase 5 — Verdict
- Live matches backtest AND PF > 1.3 → plan scaled capital, then eventually real money (LAW 3).
- Live diverges → investigate (look-ahead? data? slippage?) before anything else.

---

## 5. SUCCESS GATES (what "it works" means)

| Gate | Threshold |
|---|---|
| Look-ahead regression test | MUST pass (Phase 0) |
| Walk-forward holdout PF | > 1.3 |
| Sharpe | > 1.0 |
| Max drawdown | < 15% |
| Win rate | > 45% (swing wins on R:R, not frequency) |
| Live-vs-backtest reconciliation | within 2× |
| Live sample before real money | ≥ 30 trades / 4 weeks |

A strategy that clears the backtest but fails live reconciliation has NOT passed. Live is the only truth.

---

## 6. OPERATIONAL DISCIPLINE (workflow that worked)

- **Paper money only** until every gate above is cleared. Real money is months away and that is correct.
- **Commit/push frequently**, timestamped backups before any state change (LAW 7).
- **ASK before any state-changing action** (DB writes, config changes, deploys, engine restarts).
- **Watch-and-observe over fix** during market hours — never restart a live engine mid-session.
- **Keep the laptop plugged in** during any overnight retrain (learned the hard way — battery death killed a 5-hour retrain).
- **A watchdog** that auto-restarts a crashed engine and alerts via Telegram is mandatory before going live.
- Plain-English logging of every decision + the reason (so a human can audit WHY a trade happened).

---

## 7. THINGS THAT WILL TRY TO FOOL YOU (the trap list)

1. **Look-ahead bias** — the #1 killer. `<=` vs `<`. Future-candle close. Rolling windows including the current bar. ALWAYS run the synthetic-rising-price test.
2. **Shortcut backtests** — a backtest that doesn't run the real engine gates over-reports activity (intraday: 615 fake trades vs 8 real).
3. **Small-sample euphoria** — 8 trades winning means nothing. Wait for 30+.
4. **Survivorship bias** — make sure the universe includes delisted/changed symbols, not just today's winners.
5. **Inflated metrics from one lucky stock** — always check per-symbol breakdown.
6. **Data-source mismatch** — backtest data (Upstox) ≠ live data (yfinance) can produce different signals. Reconcile.
7. **Slippage denial** — modeled fills are optimistic. Live fills are worse. Budget for it.
8. **Curve-fitting** — a model tuned to look perfect on history usually fails forward. Walk-forward only.

---

## 8. WHAT TO HAND THE NEW PROJECT'S FIRST AGENT

Tell it, verbatim:
> "Build an AI swing trading system per SWING_PROJECT_BOOTSTRAP.md. Start with Phase 0: port the reusable code from the intraday project and stand up the engine-replay backtest harness with the `index < clock` look-ahead fix and the synthetic-ascending-price regression test. Do NOT write any strategy logic until that regression test passes. Follow the 8 Engineering Laws without exception. Paper money only. Ask before any state-changing action."

---

## 9. REFERENCE — carry these over
- `SWING_STRATEGY_OPTIONS.md` — 5 swing strategies analyzed, Breakout Swing recommended (from intraday repo).
- The intraday repo's `models/engine_replay_backtest.py` — the harness to port (apply the `index < clock` fix).
- `tests/test_r11_engine_replay.py` — the replay contract tests to port + extend.
- 10 years of daily OHLCV already in `market_data.db`.
- `PENDING_AUDIT_FIXES.md` — P49 (backtest-vs-live gap) and the R18 look-ahead finding are the founding lessons.

---

## 10. THE MINDSET

Not "succeed at any cost" — that mindset is what loses real money on a lying backtest. The new mantra:

**"Honest numbers, one change at a time, paper before real, verify everything."**

You are not starting over. You are starting SMARTER, carrying the single most valuable thing you earned in the intraday project: the ability to catch the trap that fools professionals.

Build the winnable fight first. Intraday will wait — and you'll crush it later with these laws in hand.

— End of bootstrap —
