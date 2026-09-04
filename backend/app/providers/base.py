from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime


class ProviderError(Exception):
    """Provider could not answer. Carries a machine-readable kind."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        # unavailable | timeout | rate_limited | malformed | unknown_symbol
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class Quote:
    """One provider's view of one stock at one instant.

    ``observed_at`` is the provider's own timestamp for the data, never our
    clock. That distinction is what lets us detect stale feeds instead of
    presenting a five-hour-old price as live.
    """

    symbol: str
    price: float
    observed_at: datetime
    source: str
    previous_close: float | None = None
    day_open: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    volume: float | None = None
    average_volume: float | None = None
    sigma_pct: float | None = None
    recent_volatility_pct: float | None = None
    baseline_volatility_pct: float | None = None
    history_points: int = 0
    source_disagreement_pct: float | None = None
    currency: str = "INR"
    name: str | None = None
    is_demo: bool = False


@dataclass(frozen=True)
class SearchHit:
    symbol: str
    name: str
    exchange: str = "NSE"
    currency: str = "INR"


class MarketDataProvider(abc.ABC):
    """Everything downstream depends on this, not on any vendor."""

    #: Stable identifier stored on every snapshot row.
    name: str = "abstract"
    #: True when the data is synthetic and must be labelled as such in the UI.
    is_demo: bool = False

    @abc.abstractmethod
    async def get_quotes(self, symbols: list[str], at: datetime | None = None) -> dict[str, Quote]:
        """Return quotes keyed by symbol.

        Partial success is normal and expected: symbols the provider could not
        resolve are simply absent from the mapping rather than raising, so one
        bad ticker never blanks an entire watchlist.
        """

    @abc.abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[SearchHit]:
        ...

    async def healthy(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None
