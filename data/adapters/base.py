from abc import ABC, abstractmethod

import pandas as pd


class DataAdapter(ABC):
    """Abstract base for all market-data adapters.

    Concrete subclasses must return a DataFrame with columns:
        time (datetime, tz-naive), open, high, low, close, volume
    using a plain RangeIndex (``time`` is a column, not the index).
    """

    @abstractmethod
    def fetch_ohlcv(self, symbol: str, years: int = 3,
                    resolution: str = "1d") -> pd.DataFrame:
        """Fetch historical OHLCV.

        Args:
            symbol: Ticker in the adapter's native format.
            years:  Years of history to fetch.
            resolution: Bar resolution — '1d' daily (swing default).

        Returns:
            DataFrame [time, open, high, low, close, volume].
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """True if the adapter is configured and reachable."""
        ...
