from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import BadRequest, Conflict, NotFound
from ..models import User, Watchlist, WatchlistStock
from ..providers.registry import get_router
from ..schemas import (
    AddStock,
    CheckpointOut,
    CheckpointRequest,
    DayMoverOut,
    DaySummaryOut,
    FeedItemOut,
    FeedOut,
    WatchlistCreate,
    WatchlistOut,
    WatchlistRename,
)
from ..services.checkpoint import advance_checkpoint, touch_opened
from ..services.diff import build_feed
from ..services.ingest import as_utc, record_quote
from ..services.refresh import refresh_watchlist
from ..services.timeline import build_timeline
from .deps import get_user, owned_watchlist
from .stocks import resolve_stock

router = APIRouter(prefix="/api/watchlists", tags=["watchlists"])

MAX_WATCHLISTS = 20
MAX_STOCKS_PER_LIST = 50
# Below this, a plain "last checked X ago" line is enough; above it, the user
# has been away long enough to want the day-by-day view.
TIMELINE_THRESHOLD_SECONDS = 12 * 3600


def _serialise(watchlist: Watchlist, stock_count: int) -> WatchlistOut:
    return WatchlistOut(
        id=watchlist.id,
        name=watchlist.name,
        stockCount=stock_count,
        checkpointAt=as_utc(watchlist.checkpoint_at),
        checkpointVersion=watchlist.checkpoint_version,
        lastOpenedAt=as_utc(watchlist.last_opened_at),
        createdAt=as_utc(watchlist.created_at),
    )


def _dominant_source(items) -> str:
    sources = [i.source for i in items if i.source]
    return max(set(sources), key=sources.count) if sources else "none"


def _count(db: Session, watchlist_id: int) -> int:
    return db.query(WatchlistStock).filter(WatchlistStock.watchlist_id == watchlist_id).count()


def _away_label(seconds: int) -> str:
    if seconds < 3600:
        return f"{max(seconds // 60, 1)} minutes"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = seconds // 86400
    return f"{days} day{'s' if days != 1 else ''}"


@router.get("", response_model=list[WatchlistOut])
def list_watchlists(db: Session = Depends(get_db), user: User = Depends(get_user)):
    watchlists = (
        db.query(Watchlist)
        .filter(Watchlist.user_id == user.id)
        .order_by(Watchlist.created_at.asc())
        .all()
    )
    return [_serialise(w, _count(db, w.id)) for w in watchlists]


@router.post("", response_model=WatchlistOut, status_code=201)
async def create_watchlist(
    payload: WatchlistCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_user),
):
    if db.query(Watchlist).filter(Watchlist.user_id == user.id).count() >= MAX_WATCHLISTS:
        raise Conflict(
            f"You can keep up to {MAX_WATCHLISTS} watchlists.", code="watchlist_limit"
        )
    watchlist = Watchlist(user_id=user.id, name=payload.name)
    db.add(watchlist)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise Conflict(f"You already have a watchlist called “{payload.name}”.", code="duplicate_name")
    db.refresh(watchlist)

    # Optional symbols are best-effort: a watchlist that was created should not
    # be rolled back because one ticker could not be resolved.
    added = 0
    for symbol in payload.symbols:
        try:
            stock, quote = await resolve_stock(db, symbol)
        except NotFound:
            continue
        db.add(WatchlistStock(watchlist_id=watchlist.id, stock_id=stock.id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        if quote is not None:
            record_quote(db, stock, quote)
            db.commit()
        added += 1

    return _serialise(watchlist, added)


@router.patch("/{watchlist_id}", response_model=WatchlistOut)
def rename_watchlist(
    watchlist_id: int,
    payload: WatchlistRename,
    db: Session = Depends(get_db),
    user: User = Depends(get_user),
):
    watchlist = owned_watchlist(db, user, watchlist_id)
    watchlist.name = payload.name
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise Conflict(f"You already have a watchlist called “{payload.name}”.", code="duplicate_name")
    db.refresh(watchlist)
    return _serialise(watchlist, _count(db, watchlist.id))


@router.delete("/{watchlist_id}", status_code=204)
def delete_watchlist(
    watchlist_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_user),
):
    watchlist = owned_watchlist(db, user, watchlist_id)
    db.delete(watchlist)
    db.commit()
    return Response(status_code=204)


@router.post("/{watchlist_id}/stocks", status_code=201)
async def add_stock(
    watchlist_id: int,
    payload: AddStock,
    db: Session = Depends(get_db),
    user: User = Depends(get_user),
):
    watchlist = owned_watchlist(db, user, watchlist_id)
    if _count(db, watchlist.id) >= MAX_STOCKS_PER_LIST:
        raise Conflict(
            f"A watchlist can hold up to {MAX_STOCKS_PER_LIST} stocks.", code="stock_limit"
        )

    stock, quote = await resolve_stock(db, payload.symbol)

    existing = (
        db.query(WatchlistStock)
        .filter(
            WatchlistStock.watchlist_id == watchlist.id,
            WatchlistStock.stock_id == stock.id,
        )
        .one_or_none()
    )
    if existing:
        raise Conflict(f"{stock.symbol} is already on this watchlist.", code="duplicate_stock")

    db.add(WatchlistStock(watchlist_id=watchlist.id, stock_id=stock.id))
    try:
        db.commit()
    except IntegrityError:
        # Two tabs added the same symbol at once. The end state is correct, so
        # report it as the duplicate it now is rather than a server error.
        db.rollback()
        raise Conflict(f"{stock.symbol} is already on this watchlist.", code="duplicate_stock")

    if quote is not None:
        record_quote(db, stock, quote)
        db.commit()

    return {"symbol": stock.symbol, "name": stock.name, "exchange": stock.exchange}


@router.delete("/{watchlist_id}/stocks/{symbol}", status_code=204)
def remove_stock(
    watchlist_id: int,
    symbol: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_user),
):
    watchlist = owned_watchlist(db, user, watchlist_id)
    entry = (
        db.query(WatchlistStock)
        .join(WatchlistStock.stock)
        .filter(WatchlistStock.watchlist_id == watchlist.id)
        .filter(WatchlistStock.stock.has(symbol=symbol.strip().upper()))
        .one_or_none()
    )
    if entry is None:
        raise NotFound(f"{symbol.upper()} is not on this watchlist.", code="stock_not_on_list")
    db.delete(entry)
    db.commit()
    return Response(status_code=204)


@router.get("/{watchlist_id}/feed", response_model=FeedOut)
async def get_feed(
    watchlist_id: int,
    refresh: bool = Query(default=True),
    force: bool = Query(default=False),
    source: str | None = Query(default=None, pattern="^(yahoo|demo)$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_user),
):
    """The change feed. Reading it never advances the checkpoint."""
    watchlist = owned_watchlist(db, user, watchlist_id)
    now = datetime.now(timezone.utc)

    meta = None
    if refresh:
        # This selection belongs to the request, not to checkpoint state or
        # global provider configuration.
        meta = await refresh_watchlist(db, watchlist, force=force or source is not None, source=source)

    feed = build_feed(db, watchlist, now=now)
    db.commit()
    touch_opened(db, watchlist, now)

    checkpoint_at = feed.checkpoint_at
    away_seconds = int((now - checkpoint_at).total_seconds()) if checkpoint_at else None

    timeline: list[DaySummaryOut] = []
    if checkpoint_at and away_seconds and away_seconds >= TIMELINE_THRESHOLD_SECONDS:
        timeline = [
            DaySummaryOut(
                day=day.day.isoformat(),
                high=day.high,
                meaningful=day.meaningful,
                watch=day.watch,
                quiet=day.quiet,
                changed=day.changed,
                hasData=day.has_data,
                movers=[
                    DayMoverOut(
                        symbol=m.symbol,
                        priceChangePct=m.price_change_pct,
                        attentionScore=m.attention_score,
                        severity=m.severity,
                        headline=m.headline,
                    )
                    for m in day.movers
                ],
            )
            for day in build_timeline(db, watchlist.id, checkpoint_at, now)
        ]

    provider = get_router()
    data = {
        "mode": provider.mode,
        "source": meta.source if meta and meta.fetched else _dominant_source(feed.items),
        "isDemo": bool(meta.is_demo) if meta and meta.fetched else any(i.is_demo for i in feed.items),
        "degraded": bool(meta.degraded) if meta else False,
        "error": meta.error if meta else None,
        "errorKind": meta.error_kind if meta else None,
        "missing": meta.missing if meta else [],
        "fetched": meta.fetched if meta else 0,
        "skippedFresh": meta.skipped_fresh if meta else 0,
    }

    return FeedOut(
        watchlist=_serialise(watchlist, len(feed.items)),
        items=[FeedItemOut(**vars(item)) for item in feed.items],
        counts=feed.counts,
        checkpointAt=checkpoint_at,
        generatedAt=now,
        awaySeconds=away_seconds,
        awayLabel=_away_label(away_seconds) if away_seconds else None,
        sessionsElapsed=feed.sessions_elapsed,
        timeline=timeline,
        data=data,
    )


@router.post("/{watchlist_id}/checkpoint", response_model=CheckpointOut)
def set_checkpoint(
    watchlist_id: int,
    payload: CheckpointRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_user),
):
    watchlist = owned_watchlist(db, user, watchlist_id)
    seen_through = payload.seen_through if payload else None
    if seen_through and seen_through.tzinfo is None:
        seen_through = seen_through.replace(tzinfo=timezone.utc)
    result = advance_checkpoint(db, watchlist, seen_through=seen_through)
    return CheckpointOut(
        checkpointAt=result.checkpoint_at,
        checkpointVersion=result.version,
        advanced=result.advanced,
        reason=result.reason,
    )


@router.get("/{watchlist_id}/timeline", response_model=list[DaySummaryOut])
def get_timeline(
    watchlist_id: int,
    days: int = Query(default=7, ge=1, le=30),
    db: Session = Depends(get_db),
    user: User = Depends(get_user),
):
    from datetime import timedelta

    watchlist = owned_watchlist(db, user, watchlist_id)
    now = datetime.now(timezone.utc)
    if days < 1:
        raise BadRequest("days must be at least 1")
    summaries = build_timeline(db, watchlist.id, now - timedelta(days=days), now)
    return [
        DaySummaryOut(
            day=s.day.isoformat(),
            high=s.high,
            meaningful=s.meaningful,
            watch=s.watch,
            quiet=s.quiet,
            changed=s.changed,
            hasData=s.has_data,
            movers=[
                DayMoverOut(
                    symbol=m.symbol,
                    priceChangePct=m.price_change_pct,
                    attentionScore=m.attention_score,
                    severity=m.severity,
                    headline=m.headline,
                )
                for m in s.movers
            ],
        )
        for s in summaries
    ]
