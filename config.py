"""Central configuration for the AI Swing Trading system.

Rules-based breakout swing on DAILY bars. NO ML in v1 by design
(see SWING_PROJECT_BOOTSTRAP.md §2a). Secrets are loaded from .env and
never hard-coded here.

Execution convention (the spine of the look-ahead defence):
    decide at day-T CLOSE  →  enter at day-T+1 OPEN.
At the decision moment (after close on day T) day-T's full OHLC has
already resolved, so the decision may use bars up to and including T.
The only day-T+1 value ever touched is open[T+1] (the fill). See
backtesting/replay.py + tests/test_lookahead_regression.py.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

from data.universe import POINT_IN_TIME_NSE25, SECTORS

load_dotenv()

BASE_DIR = Path(__file__).parent

# On a cloud host, set DATA_DIR to a persistent volume mount; locally it
# defaults to the project folder.
_DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR)))
DB_PATH = _DATA_DIR / "market_data.db"
MODELS_DIR = _DATA_DIR / "models" / "saved"      # reserved for a future ML phase
MODELS_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR = _DATA_DIR / "backups"               # timestamped DB/config backups (LAW 7)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# ── Universe ────────────────────────────────────────────────────────────
# Single source of truth: data/universe.py defines the point-in-time NSE-25
# (sector-balanced, retaining names that later fell from NIFTY 50 as the
# survivorship correction). SECTORS is re-exported for the per-sector cap.
DEFAULT_SYMBOLS = list(POINT_IN_TIME_NSE25)

# Macro context symbols (also backfilled in Step D).
REGIME_INDEX = "^NSEI"        # NIFTY 50 — the regime filter benchmark
VIX_SYMBOL = "^INDIAVIX"      # India VIX — optional context

# ── Data settings ───────────────────────────────────────────────────────
DATA_ADAPTER = os.getenv("DATA_ADAPTER", "yfinance")   # 'upstox' for backfill
DAILY_RESOLUTION = "1d"
DEFAULT_YEARS = 10            # ~10 years of daily history

# ── Breakout entry rules (§2b — mandatory craft filters) ────────────────
BREAKOUT_LOOKBACK = 20        # break above the prior N-day high (Donchian upper)
VOLUME_AVG_WINDOW = 20        # window for average-volume baseline
VOLUME_MULT = 1.5            # breakout-day volume must exceed 1.5× the 20-day avg
REGIME_MA = 50               # long only when NIFTY 50 > its own 50-day MA

# ── Mean-reversion entry rules (MR-1 baseline) ──────────────────────────
# "Buy the oversold dip in an uptrend." The market-regime gate (NIFTY >
# 50DMA) is deliberately OMITTED from this baseline — see the module
# docstring in signals/mean_reversion.py for the rationale and the
# correlated knife-catch risk that MR-2's max-DD will measure.
MR_TREND_MA = 200            # only dip-buy names whose close > 200DMA
MR_RSI_PERIOD = 14           # standard RSI window
MR_RSI_OVERSOLD = 30         # trigger oversold at RSI < this
MR_RSI_EXIT = 55             # exit when bounce takes RSI > this
MR_MAX_HOLD_DAYS = 10        # time stop (mean-reversion holds are short)

# ── Risk & exits (§2b, LAW 6 — sacred, change only with sign-off) ───────
ATR_PERIOD = 14
ATR_SL_MULTIPLIER = 2.0       # initial hard stop = entry − 2× ATR (spec: 1.5–2×)
CHANDELIER_ATR_MULT = 3.0     # trailing exit = (highest high since entry) − 3× ATR
MIN_RR = 2.0                  # R:R floor for entry screening (≥ 2:1)
MAX_RISK_PCT = 0.01           # 1% of portfolio risked per trade (spec: 1–2%)
MAX_POSITIONS = 6             # concurrent open positions (spec: 5–8)
MAX_PORTFOLIO_HEAT = 0.08     # cap sum of open risk at 8% (spec: 6–8%)
MAX_PER_SECTOR = 3            # max concurrent positions in one sector
MIN_HOLD_DAYS = 3             # minimum hold before discretionary exit
MAX_HOLD_DAYS = 10            # time-based exit ceiling
EARNINGS_BLACKOUT_DAYS = 2    # no entry within N trading days of known earnings

# ── Execution model (shared by backtest replay AND paper engine) ────────
# decide at day-T close → fill at day-T+1 open. Costs applied on each fill.
BROKERAGE_PCT = 0.0003        # 0.03%
SLIPPAGE_PCT = 0.001          # 0.10%
INITIAL_CAPITAL = 500_000.0

# ── Success gates (bootstrap §5) ────────────────────────────────────────
GATE_PROFIT_FACTOR = 1.3
GATE_SHARPE = 1.0
GATE_MAX_DRAWDOWN = 0.15
GATE_WIN_RATE = 0.45
PERIODS_PER_YEAR = 252        # trading days/year (daily bars)

# ── Alerts (Phase 4 — paper trading; filled from .env) ──────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

ALERT_EMAIL_FROM = os.getenv("ALERT_EMAIL_FROM", "")
ALERT_EMAIL_PASSWORD = os.getenv("ALERT_EMAIL_PASSWORD", "")
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO", "")
ALERT_EMAIL_SMTP_HOST = os.getenv("ALERT_EMAIL_SMTP_HOST", "smtp.gmail.com")
ALERT_EMAIL_SMTP_PORT = int(os.getenv("ALERT_EMAIL_SMTP_PORT", "587"))

# ── Upstox historical API (backfill) ────────────────────────────────────
# The adapter reads UPSTOX_ENV + UPSTOX_{ENV}_ACCESS_TOKEN directly from
# .env on each call; these mirrors exist for the OAuth helper + logging.
UPSTOX_ENV = os.getenv("UPSTOX_ENV", "")
UPSTOX_REDIRECT_URI = os.getenv(
    "UPSTOX_REDIRECT_URI", "http://127.0.0.1:8000/upstox/callback"
)
