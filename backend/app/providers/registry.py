"""Provider selection and failure policy.

The router is the only place that knows a real provider can fail. Two rules keep
it honest:

1. Real and synthetic data are never mixed inside one refresh. If the live
   provider fails, the whole refresh falls back to demo data and the response is
   labelled ``demo``. A feed that is half real and half invented is worse than
   one that is clearly invented.

2. A failing provider is not retried on every request. After repeated failures
   the circuit opens for a cooling-off period, so a provider outage degrades the
   app once rather than adding four seconds of timeout to every page load.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..config import get_settings
from .base import MarketDataProvider, ProviderError, Quote, SearchHit
from .demo import DemoMarketDataProvider
from .yahoo import YahooMarketDataProvider

log = logging.getLogger("marketdiff.providers")

FAILURE_THRESHOLD = 2
COOLDOWN = timedelta(seconds=90)


@dataclass
class FetchOutcome:
    quotes: dict[str, Quote]
    source: str
    is_demo: bool
    degraded: bool = False
    error: str | None = None
    error_kind: str | None = None


class ProviderRouter:
    def __init__(self, primary: MarketDataProvider | None, fallback: MarketDataProvider) -> None:
        self.primary = primary
        self.fallback = fallback
        self._failures = 0
        self._open_until: datetime | None = None
        self._last_error: str | None = None

    @property
    def circuit_open(self) -> bool:
        return bool(self._open_until and datetime.now(timezone.utc) < self._open_until)

    @property
    def mode(self) -> str:
        if self.primary is None:
            return "demo"
        return "degraded" if self.circuit_open else "live"

    def _record_failure(self, exc: ProviderError) -> None:
        self._failures += 1
        self._last_error = exc.message
        if self._failures >= FAILURE_THRESHOLD:
            self._open_until = datetime.now(timezone.utc) + COOLDOWN
            log.warning("Provider circuit opened until %s (%s)", self._open_until, exc.message)

    def _record_success(self) -> None:
        self._failures = 0
        self._open_until = None
        self._last_error = None

    async def get_quotes(
        self, symbols: list[str], at: datetime | None = None, preferred: str | None = None
    ) -> FetchOutcome:
        """Fetch a chosen source without changing the server-wide configuration."""
        if not symbols:
            return FetchOutcome({}, self.fallback.name if self.primary is None else "none", False)

        # Demo is an explicit, deterministic presentation mode. It must not
        # probe the live provider first or accidentally fall back to it.
        if preferred == "demo":
            quotes = await self.fallback.get_quotes(symbols, at=at)
            return FetchOutcome(quotes, self.fallback.name, True)

        if self.primary is None:
            quotes = await self.fallback.get_quotes(symbols, at=at)
            return FetchOutcome(quotes, self.fallback.name, True)

        if self.circuit_open:
            quotes = await self.fallback.get_quotes(symbols, at=at)
            return FetchOutcome(
                quotes,
                self.fallback.name,
                True,
                degraded=True,
                error=self._last_error or "Live market data is unavailable",
                error_kind="circuit_open",
            )

        try:
            quotes = await self.primary.get_quotes(symbols, at=at)
            self._record_success()
            return FetchOutcome(quotes, self.primary.name, False)
        except ProviderError as exc:
            self._record_failure(exc)
            log.warning("Primary provider failed (%s): %s", exc.kind, exc.message)
            quotes = await self.fallback.get_quotes(symbols, at=at)
            return FetchOutcome(
                quotes,
                self.fallback.name,
                True,
                degraded=True,
                error=exc.message,
                error_kind=exc.kind,
            )

    async def search(self, query: str, limit: int = 10) -> tuple[list[SearchHit], bool]:
        """Returns (hits, used_fallback)."""
        if self.primary is not None and not self.circuit_open:
            try:
                hits = await self.primary.search(query, limit=limit)
                if hits:
                    return hits, False
            except ProviderError as exc:
                log.info("Search fell back to local catalogue: %s", exc.message)
        return await self.fallback.search(query, limit=limit), True

    async def aclose(self) -> None:
        if self.primary:
            await self.primary.aclose()
        await self.fallback.aclose()


_router: ProviderRouter | None = None


def build_router() -> ProviderRouter:
    settings = get_settings()
    demo = DemoMarketDataProvider()
    if settings.provider == "demo":
        return ProviderRouter(primary=None, fallback=demo)
    return ProviderRouter(primary=YahooMarketDataProvider(), fallback=demo)


def get_router() -> ProviderRouter:
    global _router
    if _router is None:
        _router = build_router()
    return _router


def set_router(router: ProviderRouter | None) -> None:
    """Test seam: inject a router, or pass None to reset."""
    global _router
    _router = router
