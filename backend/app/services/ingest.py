"""Turning provider quotes into durable, ordered snapshots.

Two invariants this module exists to protect:

*Idempotency* — the same observation ingested twice produces one row. The
uniqueness key is (stock, source, observed_at), so a double-clicked refresh, a
retried request, or two browser tabs refreshing together converge on the same
state instead of inflating history.

*Monotonicity* — ``StockState.latest_*`` only ever moves forward in observation
time. Providers do return late or out-of-order responses, and without this guard
a slow request carrying a 10:02 price could land after a fast one carrying 10:05
and quietly rewind the user's view of the market.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import MarketSnapshot, Stock, StockState
from ..providers.base import Quote

log = logging.getLogger("marketdiff.ingest")


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; normalise before any comparison."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_or_create_state(db: Session, stock_id: int) -> StockState:
    state = db.get(StockState, stock_id)
    if state is None:
        state = StockState(stock_id=stock_id)
        db.add(state)
        try:
            db.flush()
        except IntegrityError:  # created concurrently
            db.rollback()
            state = db.get(StockState, stock_id)
            if state is None:  # pragma: no cover - only on a torn transaction
                raise
    return state


def record_quote(db: Session, stock: Stock, quote: Quote) -> tuple[MarketSnapshot, bool]:
    """Persist one quote. Returns (snapshot, advanced_latest_pointer)."""
    observed_at = as_utc(quote.observed_at) or datetime.now(timezone.utc)

    existing = db.execute(
        select(MarketSnapshot).where(
            MarketSnapshot.stock_id == stock.id,
            MarketSnapshot.source == quote.source,
            MarketSnapshot.observed_at == observed_at,
        )
    ).scalar_one_or_none()

    if existing is not None:
        snapshot = existing
    else:
        snapshot = MarketSnapshot(
            stock_id=stock.id,
            source=quote.source,
            observed_at=observed_at,
            price=quote.price,
            previous_close=quote.previous_close,
            day_open=quote.day_open,
            day_high=quote.day_high,
            day_low=quote.day_low,
            volume=quote.volume,
            average_volume=quote.average_volume,
            sigma_pct=quote.sigma_pct,
            recent_volatility_pct=quote.recent_volatility_pct,
            baseline_volatility_pct=quote.baseline_volatility_pct,
            history_points=quote.history_points,
            source_disagreement_pct=quote.source_disagreement_pct,
            is_demo=quote.is_demo,
        )
        db.add(snapshot)
        try:
            db.flush()
        except IntegrityError:
            # Another request inserted the identical observation first. That is
            # a success, not an error: adopt their row.
            db.rollback()
            snapshot = db.execute(
                select(MarketSnapshot).where(
                    MarketSnapshot.stock_id == stock.id,
                    MarketSnapshot.source == quote.source,
                    MarketSnapshot.observed_at == observed_at,
                )
            ).scalar_one()

    advanced = _advance_latest(db, stock.id, snapshot)
    return snapshot, advanced


def _advance_latest(db: Session, stock_id: int, snapshot: MarketSnapshot) -> bool:
    state = get_or_create_state(db, stock_id)
    current_latest = as_utc(state.latest_observed_at)
    observed_at = as_utc(snapshot.observed_at)

    if current_latest is not None and observed_at <= current_latest:
        # Out-of-order or duplicate arrival: the snapshot is kept as history but
        # must not become "current".
        log.debug("Ignoring stale arrival for stock %s (%s <= %s)", stock_id, observed_at, current_latest)
        state.last_fetch_at = datetime.now(timezone.utc)
        return False

    # Conditional update: only wins if nobody else advanced the pointer since we
    # read it. If rowcount is 0 another writer got there first with data at least
    # as new, and we accept their result.
    result = db.execute(
        update(StockState)
        .where(StockState.stock_id == stock_id, StockState.version == state.version)
        .values(
            latest_snapshot_id=snapshot.id,
            latest_observed_at=observed_at,
            version=state.version + 1,
            last_fetch_at=datetime.now(timezone.utc),
            last_error=None,
        )
    )
    if result.rowcount == 0:
        db.expire(state)
        return False
    db.expire(state)
    return True


def record_fetch_error(db: Session, stock_id: int, message: str) -> None:
    """Remember why a stock has no fresh data, without touching its last price."""
    state = get_or_create_state(db, stock_id)
    state.last_fetch_at = datetime.now(timezone.utc)
    state.last_error = message[:200]


def latest_snapshot(db: Session, stock_id: int) -> MarketSnapshot | None:
    state = db.get(StockState, stock_id)
    if state and state.latest_snapshot_id:
        return db.get(MarketSnapshot, state.latest_snapshot_id)
    # Fall back to a scan for stocks ingested before the pointer existed.
    return db.execute(
        select(MarketSnapshot)
        .where(MarketSnapshot.stock_id == stock_id)
        .order_by(MarketSnapshot.observed_at.desc(), MarketSnapshot.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def snapshot_at_or_before(db: Session, stock_id: int, moment: datetime) -> MarketSnapshot | None:
    return db.execute(
        select(MarketSnapshot)
        .where(MarketSnapshot.stock_id == stock_id, MarketSnapshot.observed_at <= moment)
        .order_by(MarketSnapshot.observed_at.desc(), MarketSnapshot.id.desc())
        .limit(1)
    ).scalar_one_or_none()
