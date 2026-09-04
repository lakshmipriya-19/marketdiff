"""Fetch-and-ingest orchestration for a watchlist."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Stock, StockState, Watchlist, WatchlistStock
from ..providers.base import ProviderError
from ..providers.registry import get_router
from .ingest import as_utc, record_fetch_error, record_quote

log = logging.getLogger("marketdiff.refresh")


@dataclass
class RefreshMeta:
    source: str = "none"
    is_demo: bool = False
    degraded: bool = False
    error: str | None = None
    error_kind: str | None = None
    fetched: int = 0
    skipped_fresh: int = 0
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "isDemo": self.is_demo,
            "degraded": self.degraded,
            "error": self.error,
            "errorKind": self.error_kind,
            "fetched": self.fetched,
            "skippedFresh": self.skipped_fresh,
            "missing": self.missing,
        }


async def refresh_watchlist(
    db: Session,
    watchlist: Watchlist,
    at: datetime | None = None,
    force: bool = False,
    source: str | None = None,
) -> RefreshMeta:
    settings = get_settings()
    now = datetime.now(timezone.utc)

    entries = (
        db.query(WatchlistStock).filter(WatchlistStock.watchlist_id == watchlist.id).all()
    )
    stocks: list[Stock] = [e.stock for e in entries]
    if not stocks:
        return RefreshMeta(source="none")

    # Rapid refreshes (double click, several tabs) should not each hit the
    # provider. Anything fetched within the TTL is reused.
    due: list[Stock] = []
    skipped = 0
    ttl = timedelta(seconds=settings.quote_ttl_s)
    for stock in stocks:
        state = db.get(StockState, stock.id)
        last_fetch = as_utc(state.last_fetch_at) if state else None
        if not force and last_fetch and now - last_fetch < ttl:
            skipped += 1
        else:
            due.append(stock)

    if not due:
        return RefreshMeta(source="cache", skipped_fresh=skipped)

    router = get_router()
    try:
        outcome = await router.get_quotes([s.symbol for s in due], at=at, preferred=source)
    except ProviderError as exc:
        # Both primary and fallback failed. Record it against every stock and
        # let the feed fall back to last known values, clearly labelled.
        log.error("All providers failed: %s", exc.message)
        for stock in due:
            record_fetch_error(db, stock.id, exc.message)
        db.commit()
        return RefreshMeta(
            source="none",
            degraded=True,
            error=exc.message,
            error_kind=exc.kind,
            skipped_fresh=skipped,
            missing=[s.symbol for s in due],
        )

    meta = RefreshMeta(
        source=outcome.source,
        is_demo=outcome.is_demo,
        degraded=outcome.degraded,
        error=outcome.error,
        error_kind=outcome.error_kind,
        skipped_fresh=skipped,
    )

    for stock in due:
        quote = outcome.quotes.get(stock.symbol.upper())
        if quote is None:
            meta.missing.append(stock.symbol)
            record_fetch_error(db, stock.id, "No quote returned for this symbol")
            continue
        try:
            record_quote(db, stock, quote)
            meta.fetched += 1
        except SQLAlchemyError as exc:  # pragma: no cover - defensive
            log.exception("Failed to persist snapshot for %s", stock.symbol)
            db.rollback()
            record_fetch_error(db, stock.id, str(exc)[:180])

    db.commit()
    return meta
