# Oracle Cloud VM — paper-trading deploy instructions

End-to-end checklist for standing up the paper-trading runner on a
fresh Oracle Cloud Infrastructure (OCI) compute VM. Follows the
DEPLOY-HARDENING brief baked-in scars from the intraday project.

---

## 0. VM provisioning (one-time)

- Shape: `VM.Standard.E2.1.Micro` (Always Free) is sufficient — the
  runner is single-threaded pandas + numpy, ~50 MB RAM at peak, ~150 MB
  paper-ledger after 6 months.
- OS image: Oracle Linux 8 or Ubuntu 22.04 LTS.
- Block volume: 50 GB is plenty (DB + logs).
- Network: outbound only — no inbound ports required. The runner
  reaches yfinance (HTTPS) and Telegram (HTTPS). No broker API ever.

## 1. OS prep

```bash
sudo dnf install -y python3.11 python3.11-pip git tzdata logrotate
# Or on Ubuntu:
# sudo apt install -y python3.11 python3.11-venv python3-pip git tzdata logrotate

# Confirm Asia/Kolkata is available.
ls /usr/share/zoneinfo/Asia/Kolkata

# Optional: set the VM's wall clock to IST (the cron has CRON_TZ
# explicitly so this is cosmetic, but logs are easier to read).
sudo timedatectl set-timezone Asia/Kolkata
```

## 2. Service user + directories

```bash
sudo useradd -m -s /bin/bash swing
sudo mkdir -p /opt/swing /opt/swing/logs /opt/swing/logs/archive /opt/swing/backups
sudo chown -R swing:swing /opt/swing
sudo -u swing -i
```

All steps below run as the `swing` user.

## 3. Clone + venv

```bash
cd /opt/swing
git clone https://github.com/aditya2529/AI-Swing-Trading.git app
cd app
python3.11 -m venv ../.venv
source ../.venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 4. Backfill market_data.db

The runner reads from `market_data.db` (already weekday-filtered).
The simplest path is to **rsync the dev box's DB** to the VM:

```bash
# from the dev box:
scp market_data.db swing@<vm>:/opt/swing/app/market_data.db
```

Alternatively, run the SMOM-1 backfill on the VM directly:

```bash
cd /opt/swing/app
PAPER_MODE=1 python -m scripts.smom1_pre_backfill_backup
PAPER_MODE=1 python -m scripts.smom1_backfill_universe
```

Allow ~5 minutes for the ~140-symbol yfinance fetch.

## 5. Configuration (`.env`)

The runner reads `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from
`.env`. Create the file but **never commit it** (it is gitignored):

```bash
cat > /opt/swing/app/.env <<'EOF'
PAPER_MODE=1
TELEGRAM_BOT_TOKEN=123456789:replace-me
TELEGRAM_CHAT_ID=-1001234567890
DATA_ADAPTER=yfinance
EOF
chmod 0600 /opt/swing/app/.env
```

To get the bot token: BotFather (Telegram) → `/newbot` → copy the
token. To get the chat ID: add the bot to your target group, send
any message, then visit
`https://api.telegram.org/bot<TOKEN>/getUpdates`.

## 6. Initialize the paper-ledger

A one-time init that creates the schema and seeds cash from
`config.INITIAL_CAPITAL`:

```bash
cd /opt/swing/app
PAPER_MODE=1 python -c "
from live.paper_ledger import init_ledger
from config import INITIAL_CAPITAL
init_ledger('paper_ledger.db', initial_capital=INITIAL_CAPITAL)
print('paper_ledger.db initialised, cash seeded.')
"
ls -l paper_ledger.db
```

## 7. Smoke test — dry-run one EOD cycle

This validates the entire pipeline (paper guard → backup → finality
check → strategy decide → equity write → Telegram ping) without
trusting the cron yet. Run from the `swing` user:

```bash
cd /opt/swing/app
PAPER_MODE=1 python -m live.eod_runner
```

Expected:
- `=== EOD runner start | run_date=YYYY-MM-DD | HH:MM IST`
- `safety guard: PAPER_MODE=1 confirmed.`
- `bar-finality: N/N symbols have a YYYY-MM-DD bar (fraction=1.00)`
  (or a clean "BAR FINALITY FAILED" if today is pre-15:30 or a NSE holiday)
- `strategy decided N orders (next-open intent): [...]`
- `equity row written: equity Rs ... (cash ... + mtm ...)`
- `=== EOD runner OK`
- ONE Telegram message: `OK YYYY-MM-DD HH:MM IST | equity Rs ... | orders N`

Verify the ledger:

```bash
sqlite3 paper_ledger.db 'SELECT * FROM runs;'
sqlite3 paper_ledger.db 'SELECT COUNT(*) FROM equity_curve;'
```

## 8. Smoke test — decision == backtest

Sanity-check that the runner's decision for today matches what the
backtest harness would produce for the same date. From the dev box
(easier — has all the diagnostics):

```bash
# Pull yesterday's paper_ledger.db from the VM
scp swing@<vm>:/opt/swing/app/paper_ledger.db /tmp/paper_yesterday.db

# Run the backtest harness with the same strategy + cutoff
python -m scripts.smom3_run_backtest --end YYYY-MM-DD
# Compare the decisions logs / trade tape near YYYY-MM-DD.
```

If the runner's "decided orders" don't match what the backtest
would have produced, **investigate before enabling the cron**. The
runner is just the backtest harness in production clothes; the
strategy is the SAME object.

## 9. Install the crontab

```bash
crontab /opt/swing/app/deploy/crontab.sample
crontab -l
```

Cron entries:
- 15:45 IST Mon-Fri — EOD runner
- 16:30 IST Mon-Fri — health check
- 15:30 IST Mon-Fri — paper_ledger.db backup
- 00:00 IST daily   — logrotate

## 10. Verify the first live cron firing

After the next 15:45 IST window:

```bash
# Check today's run record
sqlite3 /opt/swing/app/paper_ledger.db \
    "SELECT * FROM runs WHERE run_date = strftime('%Y-%m-%d','now');"

# Confirm the Telegram ping landed.

# Check the runner's own log
tail -50 /opt/swing/logs/eod_runner_$(date +%Y-%m-%d).log
```

If anything looks off, the FIRST thing to check is the timezone:
`date` (OS wall clock) and the `CRON_TZ` line in the crontab.

---

## Rollback (< 5 min)

If the paper-ledger gets corrupted or the strategy emits something
wrong:

```bash
# Stop the cron temporarily
crontab -r

# Restore the most recent good backup
ls -lt /opt/swing/backups/paper_ledger_*.db | head -1
cp /opt/swing/backups/paper_ledger_<TIMESTAMP>.db /opt/swing/app/paper_ledger.db

# Optionally restore the runs table state so today doesn't re-run
# (or LEAVE it so today re-runs with the restored ledger)

# Re-install the cron
crontab /opt/swing/app/deploy/crontab.sample
```

---

## Operational invariants the deploy enforces

| Invariant | Where enforced |
|---|---|
| Cron fires on IST schedule, not UTC | `CRON_TZ=Asia/Kolkata` (crontab.sample) |
| Run only after NSE close | 15:45 IST cron entry + bar-finality check |
| No silent death | crash-safe wrapper sends error Telegram on ANY exception |
| Health alert if cron skipped | separate 16:30 IST health-check cron |
| yfinance flakes don't crash | retry-with-backoff adapter; finality check aborts on stale day |
| No double trade | `runs` table date-PK idempotency lock |
| LAW 7 backup before mutation | pre-run backup inside `run_eod()` + daily cron backup |
| No live order possible | `safety_guard.assert_paper_mode()` at process start |
| No ML / no threading | rules-based design; pure pandas + numpy |
| Log disk doesn't fill | nightly logrotate cron |

---

_End of deploy guide. Ping ops once the cron has fired cleanly 2-3 days in a row before drawing any conclusions about the paper-trading edge._
