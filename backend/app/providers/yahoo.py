"""Real market data, no API key required.

Uses Yahoo Finance's public chart endpoint. One request per symbol, issued
concurrently with a bounded semaphore so a twenty-stock watchlist does not look
like an attack. Every response is validated before use: a 200 with malformed
JSON is treated exactly like a 500, because a provider that returns nonsense is
not more trustworthy than one that returns nothing.

Derived statistics (sigma, average volume, volatility regime) are computed here
from the daily history the same endpoint returns, so the scoring engine gets
identical inputs regardless of which provider is active.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone

import httpx

from ..config import get_settings
from .base import MarketDataProvider, ProviderError, Quote, SearchHit

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
USER_AGENT = "Mozilla/5.0 (compatible; MarketDiff/1.0)"
MAX_CONCURRENCY = 6


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def _to_yahoo(symbol: str) -> str:
    """NSE tickers need an exchange suffix; anything already suffixed passes."""
    if "." in symbol or "^" in symbol:
        return symbol
    return f"{symbol.upper()}.NS"


def _from_yahoo(symbol: str) -> str:
    return symbol.split(".")[0].upper()


class YahooMarketDataProvider(MarketDataProvider):
    name = "yahoo"
    is_demo = False

    def __init__(self, timeout: float | None = None) -> None:
        settings = get_settings()
        self._timeout = timeout or settings.provider_timeout_s
        self._client: httpx.AsyncClient | None = None
        self._sem = asyncio.Semaphore(MAX_CONCURRENCY)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                follow_redirects=True,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _fetch_one(self, symbol: str) -> Quote | None:
        client = self._get_client()
        params = {"range": "3mo", "interval": "1d", "includePrePost": "false"}
        try:
            async with self._sem:
                response = await client.get(CHART_URL.format(symbol=_to_yahoo(symbol)), params=params)
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", f"{symbol}: request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("unavailable", f"{symbol}: {exc}") from exc

        if response.status_code == 429:
            raise ProviderError("rate_limited", "Upstream rate limit reached")
        if response.status_code == 404:
            return None
        if response.status_code >= 500:
            raise ProviderError("unavailable", f"Upstream returned {response.status_code}")
        if response.status_code != 200:
            raise ProviderError("unavailable", f"Upstream returned {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("malformed", f"{symbol}: response was not JSON") from exc

        return self._parse(symbol, payload)

    def _parse(self, symbol: str, payload: dict) -> Quote | None:
        """Defensive parse. Any missing field means we return None, not zeros."""
        try:
            result = (payload.get("chart") or {}).get("result") or []
            if not result:
                return None
            node = result[0]
            meta = node.get("meta") or {}
            price = meta.get("regularMarketPrice")
            if price is None or not isinstance(price, (int, float)) or price <= 0:
                return None

            ts = meta.get("regularMarketTime")
            observed_at = (
                datetime.fromtimestamp(int(ts), tz=timezone.utc)
                if isinstance(ts, (int, float))
                else datetime.now(timezone.utc)
            )

            quotes = ((node.get("indicators") or {}).get("quote") or [{}])[0]
            closes = [c for c in (quotes.get("close") or []) if isinstance(c, (int, float))]
            volumes = [v for v in (quotes.get("volume") or []) if isinstance(v, (int, float))]

            returns = [
                (closes[i] - closes[i - 1]) / closes[i - 1] * 100.0
                for i in range(1, len(closes))
                if closes[i - 1]
            ]
            sigma = _stdev(returns[-20:]) if len(returns) >= 3 else None
            recent_vol = _stdev(returns[-5:]) if len(returns) >= 5 else None
            baseline_vol = _stdev(returns[-30:]) if len(returns) >= 10 else None
            avg_volume = (
                sum(volumes[-20:]) / len(volumes[-20:])
                if len(volumes) >= 3
                else meta.get("averageDailyVolume3Month")
            )

            return Quote(
                symbol=_from_yahoo(symbol),
                price=float(price),
                observed_at=observed_at,
                source=self.name,
                previous_close=_num(meta.get("chartPreviousClose") or meta.get("previousClose")),
                day_open=_num(closes[-1] if closes else None),
                day_high=_num(meta.get("regularMarketDayHigh")),
                day_low=_num(meta.get("regularMarketDayLow")),
                volume=_num(meta.get("regularMarketVolume")),
                average_volume=_num(avg_volume),
                sigma_pct=round(sigma, 4) if sigma else None,
                recent_volatility_pct=round(recent_vol, 4) if recent_vol else None,
                baseline_volatility_pct=round(baseline_vol, 4) if baseline_vol else None,
                history_points=len(returns),
                currency=meta.get("currency") or "INR",
                name=meta.get("longName") or meta.get("shortName"),
                is_demo=False,
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("malformed", f"{symbol}: unexpected response shape ({exc})") from exc

    async def get_quotes(self, symbols: list[str], at: datetime | None = None) -> dict[str, Quote]:
        # ``at`` is meaningless for a live provider; historical replay is a
        # demo-mode capability and is not faked here.
        results = await asyncio.gather(
            *(self._fetch_one(s) for s in symbols), return_exceptions=True
        )
        quotes: dict[str, Quote] = {}
        errors: list[ProviderError] = []
        for symbol, outcome in zip(symbols, results):
            if isinstance(outcome, ProviderError):
                errors.append(outcome)
            elif isinstance(outcome, BaseException):
                errors.append(ProviderError("unavailable", f"{symbol}: {outcome}"))
            elif outcome is not None:
                quotes[symbol.upper()] = outcome
        # Total failure is an error; partial failure is just partial data.
        if not quotes and errors:
            raise errors[0]
        return quotes

    async def search(self, query: str, limit: int = 10) -> list[SearchHit]:
        client = self._get_client()
        try:
            response = await client.get(
                SEARCH_URL, params={"q": query, "quotesCount": limit, "newsCount": 0}
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("unavailable", f"search failed: {exc}") from exc

        hits: list[SearchHit] = []
        for item in payload.get("quotes", [])[:limit]:
            symbol = item.get("symbol")
            if not symbol:
                continue
            hits.append(
                SearchHit(
                    symbol=_from_yahoo(symbol),
                    name=item.get("shortname") or item.get("longname") or symbol,
                    exchange=item.get("exchange") or "NSE",
                )
            )
        return hits

    async def healthy(self) -> bool:
        try:
            quotes = await self.get_quotes(["RELIANCE"])
            return bool(quotes)
        except ProviderError:
            return False


def _num(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None
