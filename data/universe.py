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


# ── MOMENTUM_UNIVERSE — broader NSE set for cross-sectional rotation ────
#
# Used by signals/momentum.py (MOM-2). Drawn from current NIFTY 200-ish
# liquid names; SEPARATE from POINT_IN_TIME_NSE25 (it is a SUPERSET — the
# 25 swing names are included so a single backfill plan covers both
# strategies' needs, but the swing strategy still iterates only
# POINT_IN_TIME_NSE25). All tickers in .NS form for yfinance.
#
# IMPORTANT survivorship caveat for MOM (different from swing's caveat):
# this list is CURRENT membership, not point-in-time. Names that were in
# NIFTY 200 a decade ago but have since been delisted / merged out are
# entirely absent. Therefore MOM-3's backtest must apply an explicit
# survivorship discount (10-30% PF haircut typical for current-membership
# universes); see ``MOMENTUM_SURVIVORSHIP_NOTE`` below. A true PIT
# universe is a later upgrade.
MOMENTUM_UNIVERSE: list[str] = sorted({
    # IT (9; LTIM.NS removed — LTI/Mindtree merger left the legacy ticker
    # un-fetchable on yfinance and the corrected LTIMINDTREE.NS also 404s.)
    "TCS.NS", "INFY.NS", "WIPRO.NS", "HCLTECH.NS", "TECHM.NS",
    "PERSISTENT.NS", "COFORGE.NS", "MPHASIS.NS", "KPITTECH.NS",
    # Banks (12)
    "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "AXISBANK.NS", "KOTAKBANK.NS",
    "INDUSINDBK.NS", "BANKBARODA.NS", "PNB.NS", "FEDERALBNK.NS",
    "IDFCFIRSTB.NS", "AUBANK.NS", "YESBANK.NS",
    # Financial services / NBFC / insurance (14)
    "BAJFINANCE.NS", "BAJAJFINSV.NS", "SBILIFE.NS", "HDFCLIFE.NS",
    "ICICIGI.NS", "ICICIPRULI.NS", "SBICARD.NS", "CHOLAFIN.NS",
    "MUTHOOTFIN.NS", "MFSL.NS", "MANAPPURAM.NS", "LICI.NS",
    "RECLTD.NS", "PFC.NS",
    # Energy / oil & gas (10)
    "RELIANCE.NS", "ONGC.NS", "BPCL.NS", "IOC.NS", "HINDPETRO.NS",
    "GAIL.NS", "OIL.NS", "PETRONET.NS", "IGL.NS", "MGL.NS",
    # Auto + ancillaries (11)
    "MARUTI.NS", "TATAMOTORS.NS", "M&M.NS", "BAJAJ-AUTO.NS",
    "HEROMOTOCO.NS", "EICHERMOT.NS", "ASHOKLEY.NS", "TVSMOTOR.NS",
    "BHARATFORG.NS", "BOSCHLTD.NS", "MOTHERSON.NS",
    # FMCG (10)
    "HINDUNILVR.NS", "ITC.NS", "NESTLEIND.NS", "BRITANNIA.NS",
    "DABUR.NS", "MARICO.NS", "COLPAL.NS", "GODREJCP.NS",
    "TATACONSUM.NS", "VBL.NS",
    # Pharma + healthcare services (15)
    "SUNPHARMA.NS", "CIPLA.NS", "DRREDDY.NS", "LUPIN.NS", "AUROPHARMA.NS",
    "BIOCON.NS", "DIVISLAB.NS", "TORNTPHARM.NS", "ZYDUSLIFE.NS",
    "ALKEM.NS", "GLENMARK.NS", "APOLLOHOSP.NS", "FORTIS.NS",
    "MAXHEALTH.NS", "METROPOLIS.NS",
    # Metals (8)
    "TATASTEEL.NS", "HINDALCO.NS", "VEDL.NS", "JSWSTEEL.NS",
    "COALINDIA.NS", "NMDC.NS", "JINDALSTEL.NS", "SAIL.NS",
    # Cement (6)
    "ULTRACEMCO.NS", "SHREECEM.NS", "GRASIM.NS", "ACC.NS",
    "AMBUJACEM.NS", "RAMCOCEM.NS",
    # Telecom (2)
    "BHARTIARTL.NS", "IDEA.NS",
    # Infra / capital goods (10; GMRINFRA.NS replaced by GMRAIRPORT.NS
    # post-2024 demerger — the legacy parent ticker no longer fetches.)
    "LT.NS", "SIEMENS.NS", "ABB.NS", "HAVELLS.NS", "CGPOWER.NS",
    "GMRAIRPORT.NS", "IRB.NS", "ADANIENT.NS", "ADANIPORTS.NS", "BHEL.NS",
    # Power / utilities (8)
    "NTPC.NS", "POWERGRID.NS", "TATAPOWER.NS", "ADANIPOWER.NS",
    "ADANIGREEN.NS", "JSWENERGY.NS", "NHPC.NS", "SJVN.NS",
    # Realty (4)
    "DLF.NS", "GODREJPROP.NS", "OBEROIRLTY.NS", "PRESTIGE.NS",
    # Chemicals / paints (8)
    "PIDILITIND.NS", "UPL.NS", "BERGEPAINT.NS", "ASIANPAINT.NS",
    "AKZOINDIA.NS", "SRF.NS", "TATACHEM.NS", "DEEPAKNTR.NS",
    # Retail / consumer-durables (5)
    "TITAN.NS", "DMART.NS", "BATAINDIA.NS", "PAGEIND.NS", "TRENT.NS",
    # Media (2)
    "ZEEL.NS", "SUNTV.NS",
    # Defense (3)
    "HAL.NS", "BEL.NS", "BHARATFORG.NS",   # BHARATFORG dual-listed in auto+defense
})

# Sector tags for the MOM-only names (extending SECTORS with new entries
# so run_replay's per-sector cap remains meaningful for momentum holdings).
# Names already in SECTORS (the 25 swing set) are NOT re-added.
_MOMENTUM_NEW_SECTORS: dict[str, str] = {
    # IT
    "HCLTECH.NS": "IT", "TECHM.NS": "IT",
    "PERSISTENT.NS": "IT", "COFORGE.NS": "IT", "MPHASIS.NS": "IT",
    "KPITTECH.NS": "IT",
    # Banks
    "KOTAKBANK.NS": "BANK", "INDUSINDBK.NS": "BANK", "BANKBARODA.NS": "BANK",
    "PNB.NS": "BANK", "FEDERALBNK.NS": "BANK", "IDFCFIRSTB.NS": "BANK",
    "AUBANK.NS": "BANK",
    # Financial
    "BAJFINANCE.NS": "FINANCIAL", "BAJAJFINSV.NS": "FINANCIAL",
    "SBILIFE.NS": "FINANCIAL", "HDFCLIFE.NS": "FINANCIAL",
    "ICICIGI.NS": "FINANCIAL", "ICICIPRULI.NS": "FINANCIAL",
    "SBICARD.NS": "FINANCIAL", "CHOLAFIN.NS": "FINANCIAL",
    "MUTHOOTFIN.NS": "FINANCIAL", "MFSL.NS": "FINANCIAL",
    "MANAPPURAM.NS": "FINANCIAL", "LICI.NS": "FINANCIAL",
    "RECLTD.NS": "FINANCIAL", "PFC.NS": "FINANCIAL",
    # Energy
    "IOC.NS": "ENERGY", "HINDPETRO.NS": "ENERGY", "OIL.NS": "ENERGY",
    "PETRONET.NS": "ENERGY", "IGL.NS": "ENERGY", "MGL.NS": "ENERGY",
    # Auto
    "BAJAJ-AUTO.NS": "AUTO", "HEROMOTOCO.NS": "AUTO", "EICHERMOT.NS": "AUTO",
    "ASHOKLEY.NS": "AUTO", "TVSMOTOR.NS": "AUTO", "BHARATFORG.NS": "AUTO",
    "BOSCHLTD.NS": "AUTO", "MOTHERSON.NS": "AUTO",
    # FMCG
    "NESTLEIND.NS": "FMCG", "BRITANNIA.NS": "FMCG", "DABUR.NS": "FMCG",
    "MARICO.NS": "FMCG", "COLPAL.NS": "FMCG", "GODREJCP.NS": "FMCG",
    "TATACONSUM.NS": "FMCG", "VBL.NS": "FMCG",
    # Pharma / healthcare
    "DRREDDY.NS": "PHARMA", "AUROPHARMA.NS": "PHARMA", "BIOCON.NS": "PHARMA",
    "DIVISLAB.NS": "PHARMA", "TORNTPHARM.NS": "PHARMA",
    "ZYDUSLIFE.NS": "PHARMA", "ALKEM.NS": "PHARMA", "GLENMARK.NS": "PHARMA",
    "APOLLOHOSP.NS": "HEALTHCARE", "FORTIS.NS": "HEALTHCARE",
    "MAXHEALTH.NS": "HEALTHCARE", "METROPOLIS.NS": "HEALTHCARE",
    # Metal
    "JSWSTEEL.NS": "METAL", "COALINDIA.NS": "METAL", "NMDC.NS": "METAL",
    "JINDALSTEL.NS": "METAL", "SAIL.NS": "METAL",
    # Cement
    "ULTRACEMCO.NS": "CEMENT", "SHREECEM.NS": "CEMENT", "GRASIM.NS": "CEMENT",
    "ACC.NS": "CEMENT", "AMBUJACEM.NS": "CEMENT", "RAMCOCEM.NS": "CEMENT",
    # Telecom
    "IDEA.NS": "TELECOM",
    # Infra / capital goods
    "SIEMENS.NS": "INFRA", "ABB.NS": "INFRA", "HAVELLS.NS": "INFRA",
    "CGPOWER.NS": "INFRA", "GMRAIRPORT.NS": "INFRA", "IRB.NS": "INFRA",
    "ADANIENT.NS": "INFRA", "ADANIPORTS.NS": "INFRA", "BHEL.NS": "INFRA",
    # Power / utilities
    "NTPC.NS": "POWER", "POWERGRID.NS": "POWER", "TATAPOWER.NS": "POWER",
    "ADANIPOWER.NS": "POWER", "ADANIGREEN.NS": "POWER",
    "JSWENERGY.NS": "POWER", "NHPC.NS": "POWER", "SJVN.NS": "POWER",
    # Realty
    "DLF.NS": "REALTY", "GODREJPROP.NS": "REALTY", "OBEROIRLTY.NS": "REALTY",
    "PRESTIGE.NS": "REALTY",
    # Chemicals / paints
    "PIDILITIND.NS": "CHEMICAL", "UPL.NS": "CHEMICAL",
    "BERGEPAINT.NS": "CHEMICAL", "ASIANPAINT.NS": "CHEMICAL",
    "AKZOINDIA.NS": "CHEMICAL", "SRF.NS": "CHEMICAL",
    "TATACHEM.NS": "CHEMICAL", "DEEPAKNTR.NS": "CHEMICAL",
    # Retail / consumer durables
    "TITAN.NS": "RETAIL", "DMART.NS": "RETAIL", "BATAINDIA.NS": "RETAIL",
    "PAGEIND.NS": "RETAIL", "TRENT.NS": "RETAIL",
    # Media
    "ZEEL.NS": "MEDIA", "SUNTV.NS": "MEDIA",
    # Defense
    "HAL.NS": "DEFENSE", "BEL.NS": "DEFENSE",
}
# Merge into the canonical SECTORS dict so ``get_sector`` returns the
# right bucket for momentum-only names without the swing code seeing
# anything new (swing iterates POINT_IN_TIME_NSE25 explicitly).
SECTORS.update(_MOMENTUM_NEW_SECTORS)

# Names that are in MOMENTUM_UNIVERSE but NOT yet in market_data.db.
# Used by the backfill script to know which symbols to fetch.
# (Computed at import time; the swing 25 + ^NSEI/^INDIAVIX already there
# are excluded so we don't waste yfinance calls re-fetching.)
MOMENTUM_NEW_TO_DB: list[str] = sorted(
    set(MOMENTUM_UNIVERSE) - set(POINT_IN_TIME_NSE25)
)

# Symbols intentionally removed from MOMENTUM_UNIVERSE after the MOM-1
# backfill could not source them on yfinance. Kept here so reviewers can
# reproduce the gap. These are NOT looked up at runtime — pure provenance.
MOMENTUM_DROPPED_DEAD_TICKERS: dict[str, str] = {
    "LTIM.NS": (
        "LTI/Mindtree merger (Nov 2022); the surviving entity LTIMINDTREE.NS "
        "also returns 404 on yfinance as of 2026-06-04. No replacement found."
    ),
    "GMRINFRA.NS": (
        "GMR group 2024 demerger split the parent into GMRP&UI / GMRAIRPORT; "
        "replaced in MOMENTUM_UNIVERSE by GMRAIRPORT.NS (the listed successor "
        "that fetches cleanly)."
    ),
}


MOMENTUM_SURVIVORSHIP_NOTE = (
    "MOMENTUM_UNIVERSE is CURRENT membership, not point-in-time. Names "
    "that were in NIFTY 200 a decade ago but have since been delisted / "
    "merged out are entirely absent. MOM-3's backtest report MUST apply "
    "an explicit survivorship discount (10-30% PF haircut typical for "
    "current-membership universes) and label results accordingly. True "
    "PIT membership rotation is a separate later upgrade."
)


def get_universe() -> list[str]:
    """The tradeable symbols (excludes macro indices, which live in config)."""
    return list(POINT_IN_TIME_NSE25)


def get_momentum_universe() -> list[str]:
    """The broader symbol set used by signals/momentum.py."""
    return list(MOMENTUM_UNIVERSE)


def get_sector(symbol: str) -> str:
    """Sector for a symbol; falls back to the symbol itself if unmapped (so
    an unknown name forms its own sector and never groups spuriously)."""
    return SECTORS.get(symbol, symbol)
