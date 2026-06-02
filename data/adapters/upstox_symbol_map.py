"""yfinance symbol -> Upstox instrument key (static, 25-symbol universe).

ISINs are stable identifiers (they don't churn unless a company
restructures its legal entity). The entries below were correct as of
2026-05 (ported from the intraday project's vetted map). Re-verify
against the Upstox instrument master before any production use; for
the historical daily backfill an out-of-date key fails loudly with a
clear KeyError, carrying no money risk.

To add a symbol: get its ISIN from NSE and add an ``ALL_MAPPINGS`` entry.
"""
from __future__ import annotations


# yfinance ticker -> Upstox instrument key (NSE_EQ|ISIN)
ALL_MAPPINGS: dict[str, str] = {
    # IT
    "TCS.NS":        "NSE_EQ|INE467B01029",
    "INFY.NS":       "NSE_EQ|INE009A01021",
    "WIPRO.NS":      "NSE_EQ|INE075A01022",
    "HCLTECH.NS":    "NSE_EQ|INE860A01027",
    # Banking
    "HDFCBANK.NS":   "NSE_EQ|INE040A01034",
    "ICICIBANK.NS":  "NSE_EQ|INE090A01021",
    "KOTAKBANK.NS":  "NSE_EQ|INE237A01028",
    "AXISBANK.NS":   "NSE_EQ|INE238A01034",
    "SBIN.NS":       "NSE_EQ|INE062A01020",
    # Energy
    "RELIANCE.NS":   "NSE_EQ|INE002A01018",
    "ONGC.NS":       "NSE_EQ|INE213A01029",
    "BPCL.NS":       "NSE_EQ|INE029A01011",
    # Auto
    "MARUTI.NS":     "NSE_EQ|INE585B01010",
    "M&M.NS":        "NSE_EQ|INE101A01026",
    "BAJAJ-AUTO.NS": "NSE_EQ|INE917I01010",
    # Pharma
    "SUNPHARMA.NS":  "NSE_EQ|INE044A01036",
    "DRREDDY.NS":    "NSE_EQ|INE089A01023",
    "CIPLA.NS":      "NSE_EQ|INE059A01026",
    # FMCG
    "HINDUNILVR.NS": "NSE_EQ|INE030A01027",
    "NESTLEIND.NS":  "NSE_EQ|INE239A01016",
    "BRITANNIA.NS":  "NSE_EQ|INE216A01030",
    # Metals
    "TATASTEEL.NS":  "NSE_EQ|INE081A01020",
    "HINDALCO.NS":   "NSE_EQ|INE038A01020",
    # Infra / Telecom
    "BHARTIARTL.NS": "NSE_EQ|INE397D01024",
    "LT.NS":         "NSE_EQ|INE018A01030",
}


def lookup(symbol: str) -> str:
    """Return the Upstox instrument key for a yfinance symbol.

    Case-sensitive. Raises ``KeyError`` (with the symbol embedded) on an
    unknown/empty input so failures are easy to read in tracebacks.
    """
    if not symbol:
        raise KeyError(f"empty symbol (got {symbol!r})")
    if symbol not in ALL_MAPPINGS:
        raise KeyError(
            f"unknown symbol {symbol!r}; add it to "
            f"data.adapters.upstox_symbol_map.ALL_MAPPINGS"
        )
    return ALL_MAPPINGS[symbol]
