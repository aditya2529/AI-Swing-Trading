"""The trading universe — a point-in-time NSE-25.

WHY THIS IS NOT "TODAY'S TOP 25"
--------------------------------
Backtesting on today's 25 most-liquid winners overstates results: a stock
is in that list precisely BECAUSE it trended well over the period you're
testing (survivorship bias — bootstrap §2b, trap #4). To blunt that, this
fixed 25-name set deliberately retains several names that were prominent
around the 2016 start of our daily history but later **fell out of NIFTY
50** (marked in ``REMOVED_FROM_NIFTY50`` below). They underperformed — and
including them is the whole point: it keeps the backtest honest.

THE COMPROMISE (read this before trusting any PF)
-------------------------------------------------
A *true* point-in-time universe rotates membership year by year and
includes fully-delisted names. We can't fully do that here:
  - Fully-delisted tickers (e.g. Bharti Infratel, merged into Indus Towers
    in 2020) have NO fetchable OHLC from Upstox/yfinance — so they cannot
    be in a backtest at all.
  - So this is a FIXED list of names that were significant in 2016 and are
    STILL LISTED (history is fetchable), with some that later fell from the
    index as the survivorship correction.
This reduces survivorship bias but does not eliminate it. Therefore Phase 3
MUST report PF both raw AND with an explicit survivorship haircut, and must
state this list is fixed-membership, not a true point-in-time rotation.
See ``SURVIVORSHIP_NOTE``.

Status: DRAFT pending sign-off (2026-06-02). Swap names by editing the
table below; the Upstox instrument map (data/adapters/upstox_symbol_map.py)
must carry an ISIN for every symbol here before an Upstox backfill.
"""
from __future__ import annotations


# symbol -> sector. Sector tags drive the "max positions per sector" cap.
SECTORS: dict[str, str] = {
    # IT (3)
    "TCS.NS": "IT", "INFY.NS": "IT", "WIPRO.NS": "IT",
    # Banking & Financials (5) — includes YESBANK (⬇ removed from NIFTY 50)
    "HDFCBANK.NS": "BANK", "ICICIBANK.NS": "BANK", "SBIN.NS": "BANK",
    "AXISBANK.NS": "BANK", "YESBANK.NS": "BANK",
    # Energy / Oil & Gas (4) — includes GAIL (⬇)
    "RELIANCE.NS": "ENERGY", "ONGC.NS": "ENERGY", "GAIL.NS": "ENERGY",
    "BPCL.NS": "ENERGY",
    # Auto (3)
    "MARUTI.NS": "AUTO", "M&M.NS": "AUTO", "TATAMOTORS.NS": "AUTO",
    # Pharma (3) — includes LUPIN (⬇)
    "SUNPHARMA.NS": "PHARMA", "CIPLA.NS": "PHARMA", "LUPIN.NS": "PHARMA",
    # FMCG (2)
    "HINDUNILVR.NS": "FMCG", "ITC.NS": "FMCG",
    # Metals (3) — includes VEDL (⬇)
    "TATASTEEL.NS": "METAL", "HINDALCO.NS": "METAL", "VEDL.NS": "METAL",
    # Telecom (1)
    "BHARTIARTL.NS": "TELECOM",
    # Infra / Capital goods (1)
    "LT.NS": "INFRA",
}

# The 25 symbols, in a stable order.
POINT_IN_TIME_NSE25: list[str] = list(SECTORS.keys())

# Names that were in NIFTY 50 in/around 2016 but were later removed — kept
# here ON PURPOSE as the survivorship correction. (Years approximate —
# verify if used in a writeup.) All are still LISTED, so history is fetchable.
REMOVED_FROM_NIFTY50: dict[str, str] = {
    "YESBANK.NS": "removed ~2020 after the bank's crisis/moratorium",
    "GAIL.NS": "removed from NIFTY 50 ~2021",
    "LUPIN.NS": "removed from NIFTY 50 ~2019",
    "VEDL.NS": "removed from NIFTY 50 (Vedanta); 2020 delisting attempt failed, still listed",
}

SURVIVORSHIP_NOTE = (
    "Fixed-membership universe with fallen-from-index names retained. NOT a "
    "true point-in-time rotation and excludes fully-delisted tickers "
    "(unfetchable). Phase 3 must report PF raw AND survivorship-discounted, "
    "and label results accordingly."
)


def get_universe() -> list[str]:
    """The tradeable symbols (excludes macro indices, which live in config)."""
    return list(POINT_IN_TIME_NSE25)


def get_sector(symbol: str) -> str:
    """Sector for a symbol; falls back to the symbol itself if unmapped (so
    an unknown name forms its own sector and never groups spuriously)."""
    return SECTORS.get(symbol, symbol)
