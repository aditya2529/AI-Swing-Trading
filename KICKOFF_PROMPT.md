# Swing Project — Kickoff Prompt
**Paste the block below into a fresh agent session opened in D:\Projects\AI Swing Trading**

---

```
You are building an AI swing trading system from scratch in this folder
(D:\Projects\AI Swing Trading).

FIRST: read SWING_PROJECT_BOOTSTRAP.md in this directory cover to cover,
then SWING_STRATEGY_OPTIONS.md. The bootstrap carries every hard-won
lesson from my previous intraday project (D:\Projects\AI Stock Market
Analyzer), which was undermined by look-ahead bias (backtest PF 8.1 vs
live 0.76). You will need read access to that intraday folder to port
reusable code — confirm you can read it before planning the port.

NON-NEGOTIABLE: follow all 8 Engineering Laws in the bootstrap.

=== THE LOOK-AHEAD RULE (read carefully — do NOT blindly copy the
intraday fix) ===
The principle is: a decision at time T may use ONLY data that had
FULLY RESOLVED at T. The intraday project used `index < clock` because
its 5-min bars were OPEN-stamped, so the current bar's close was 5 min
in the future. For DAILY swing bars the convention may differ — a
decision made AFTER market close on day T can legitimately use day T's
complete OHLC.

Therefore, BEFORE writing any slicing logic:
1. Verify the exact bar-stamping convention in the daily data (query
   the DB: does bar[T].close == bar[T+1].open, or is the bar stamped at
   close?).
2. Define the exact decision moment (e.g. "decide at day-T close, enter
   at day-T+1 open").
3. Derive the correct slice from those two facts — do NOT assume `<` or
   `<=`. Document the reasoning in a comment + the regression test.

CLARIFICATION on labels: forward-looking LABELS in TRAINING (e.g. the
N-day-ahead return used to label a BUY) are CORRECT and expected — that
is supervised learning, not leakage. Leakage is when a FEATURE consumed
at INFERENCE time uses data from at/after the decision moment. Do not
"fix" the training labels; only guard the inference-time feature path.

=== STRATEGY: SIMPLE RULES FIRST, NOT ML ===
Per bootstrap §2a: build a pure RULES-BASED breakout strategy first
(no XGBoost/LSTM/training pipeline). It must include the mandatory
craft filters from §2b: volume confirmation (>1.5x 20-day avg), regime
filter (only long when NIFTY 50 > its 50-DMA), and a trailing-stop exit
(Chandelier: highest-high-since-entry minus 3x ATR). ML may be evaluated
LATER only if it provably beats the simple version on the honest harness.

=== MANDATORY REGRESSION TEST (Phase 0 gate) ===
Write a synthetic-ascending-price test: feed a monotonically rising
series through the FULL inference + decision path. If the strategy
predicts the rise with implausible accuracy, look-ahead is present and
the test must FAIL. No strategy logic may be written until this test
passes.

=== WALK-FORWARD DISCIPLINE ===
- Train and test windows must never overlap.
- Any scaler/normalizer must be FIT ON TRAIN ONLY, then applied to test.
- The backtest must be an engine-replay running the real decision code,
  never a parallel shortcut. The deploy gate is the replay PF.
- Treat any published/third-party PF as inflated; plan for live ~30-50%
  below backtest.

START WITH PHASE 0, in this order:
1. Repo structure (git init, folders, requirements). Confirm with me
   whether to create a new GitHub repo.
2. Set up .env — Upstox historical API creds are needed for the daily
   backfill. List exactly which env vars you need; I'll provide them
   securely (do NOT ask me to paste secrets in chat).
3. Port ONLY the files listed in bootstrap Section 3 from the intraday
   repo, in dependency order. State the list before copying. Copy
   files in (do not link/share across projects).
4. Port the engine-replay harness, applying the VERIFIED look-ahead
   slice (per the rule above) + the synthetic-ascending-price test.
5. Backfill 10 years of DAILY bars for the 25-symbol NSE universe
   (point-in-time membership if available).

DO NOT write any strategy/model logic until the look-ahead regression
test passes. That test is the gate for everything after.

Constraints:
- Python 3.11+. Long-only swing: daily bars, 3-10 day holds, ATR stops
  (1.5-2x), trailing-stop exit, 1-2% risk/trade, 5-8 max positions,
  cap total portfolio heat ~6-8%, max 2-3 positions per sector.
- Paper money only. Real money is months away by design.
- Commit frequently with timestamped backups; rollback < 5 min.
- ASK before any state-changing action.

FIRST RESPONSE EXPECTED:
1. Confirm you read the bootstrap + can access the intraday folder.
2. In your OWN words, explain the look-ahead rule and why blindly using
   `index < clock` from the intraday project could be WRONG for daily
   bars. (This proves you understood, not just copied.)
3. Propose the Phase 0 repo structure + exact port list + the env vars
   you need.
DO NOT write code until I approve the plan.
```
