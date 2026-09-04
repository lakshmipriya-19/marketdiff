from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import NotFound
from ..models import Stock, User, WatchlistStock
from ..providers.base import ProviderError, Quote
from ..providers.demo import UNIVERSE
from ..providers.registry import get_router
from ..schemas import SearchHitOut
from ..services.freshness import classify
from ..services.ingest import as_utc, latest_snapshot, record_quote
from .deps import get_user

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


async def resolve_stock(db: Session, symbol: str) -> tuple[Stock, Quote | None]:
    """Find or create a stock, verifying it actually exists upstream first.

    We refuse to add a symbol we cannot price. Adding an unresolvable ticker
    would leave a permanent blank row in the feed, which reads as a bug in the
    app rather than a typo by the user.
    """
    symbol = symbol.strip().upper()
    existing = db.query(Stock).filter(Stock.symbol == symbol).one_or_none()

    quote: Quote | None = None
    provider = get_router()
    try:
        outcome = await provider.get_quotes([symbol])
        quote = outcome.quotes.get(symbol)
    except ProviderError:
        quote = None

    if existing is not None:
        return existing, quote

    if quote is None and symbol not in UNIVERSE:
        raise NotFound(
            f"Couldn’t find a stock with the symbol {symbol}.", code="unknown_symbol"
        )

    name = (quote.name if quote else None) or (
        UNIVERSE[symbol].name if symbol in UNIVERSE else symbol
    )
    stock = Stock(
        symbol=symbol,
        name=name,
        exchange="NSE",
        currency=quote.currency if quote else "INR",
    )
    db.add(stock)
    try:
        db.commit()
    except IntegrityError:  # added concurrently
        db.rollback()
        stock = db.query(Stock).filter(Stock.symbol == symbol).one()
    db.refresh(stock)
    return stock, quote


@router.get("/search", response_model=list[SearchHitOut])
async def search_stocks(
    q: str = Query(min_length=1, max_length=40),
    watchlist_id: int | None = Query(default=None, alias="watchlistId"),
    limit: int = Query(default=8, ge=1, le=20),
    db: Session = Depends(get_db),
    user: User = Depends(get_user),
):
    provider = get_router()
    hits, _ = await provider.search(q.strip(), limit=limit)

    on_list: set[str] = set()
    if watchlist_id:
        rows = (
            db.query(Stock.symbol)
            .join(WatchlistStock, WatchlistStock.stock_id == Stock.id)
            .filter(WatchlistStock.watchlist_id == watchlist_id)
            .all()
        )
        on_list = {r[0] for r in rows}

    return [
        SearchHitOut(
            symbol=h.symbol,
            name=h.name,
            exchange=h.exchange,
            inWatchlist=h.symbol in on_list,
        )
        for h in hits
    ]


@router.get("/{symbol}")
async def get_stock(
    symbol: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_user),
):
    stock, quote = await resolve_stock(db, symbol)
    if quote is not None:
        record_quote(db, stock, quote)
        db.commit()

    snapshot = latest_snapshot(db, stock.id)
    fresh = classify(as_utc(snapshot.observed_at) if snapshot else None)
    return {
        "symbol": stock.symbol,
        "name": stock.name,
        "exchange": stock.exchange,
        "currency": stock.currency,
        "price": snapshot.price if snapshot else None,
        "previousClose": snapshot.previous_close if snapshot else None,
        "volume": snapshot.volume if snapshot else None,
        "averageVolume": snapshot.average_volume if snapshot else None,
        "typicalMovePct": snapshot.sigma_pct if snapshot else None,
        "observedAt": as_utc(snapshot.observed_at) if snapshot else None,
        "freshness": fresh.state,
        "freshnessLabel": fresh.label,
        "source": snapshot.source if snapshot else None,
        "isDemo": bool(snapshot.is_demo) if snapshot else False,
    }
