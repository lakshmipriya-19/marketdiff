"""Seeding.

Two distinct things live here, deliberately kept apart:

* ``seed_reference_data`` inserts stock *metadata* only — symbols and names, no
  prices. Safe in every mode.
* ``backfill_demo_history`` writes synthetic *price history*. It is only ever
  invoked for demo data and every row it writes is flagged ``is_demo``, so
  synthetic history can never silently become the baseline for a real quote.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .db import SessionLocal
from .market_calendar import IST, MARKET_CLOSE, is_trading_day
from .models import Stock
from .providers.demo import UNAVAILABLE_SYMBOLS, UNIVERSE, DemoMarketDataProvider
from .services.ingest import record_quote


def seed_reference_data() -> None:
    db: Session = SessionLocal()
    try:
        existing = {row[0] for row in db.query(Stock.symbol).all()}
        added = 0
        for char in UNIVERSE.values():
            if char.symbol in existing:
                continue
            db.add(Stock(symbol=char.symbol, name=char.name, exchange="NSE", currency="INR"))
            added += 1
        if added:
            db.commit()
    finally:
        db.close()


def _trading_days_back(count: int, now: datetime) -> list[date]:
    days: list[date] = []
    cursor = now.astimezone(IST).date()
    while len(days) < count:
        if is_trading_day(cursor):
            days.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(days)


def backfill_demo_history(
    db: Session, symbols: list[str], days: int = 8, now: datetime | None = None
) -> int:
    """Write one synthetic close per trading day, oldest first.

    Chronological order matters: the latest-snapshot pointer only moves forward,
    so writing history in order leaves it pointing at the newest observation.
    """
    now = now or datetime.now(timezone.utc)
    provider = DemoMarketDataProvider()
    written = 0

    for symbol in symbols:
        symbol = symbol.upper()
        if symbol not in UNIVERSE:
            continue
        if symbol in UNAVAILABLE_SYMBOLS:
            # This symbol's feed is permanently down in the demo: no historical
            # backfill either, so it renders as "unavailable" rather than a
            # fabricated price history.
            continue
        stock = db.query(Stock).filter(Stock.symbol == symbol.upper()).one_or_none()
        if stock is None:
            char = UNIVERSE[symbol.upper()]
            stock = Stock(symbol=char.symbol, name=char.name)
            db.add(stock)
            db.commit()
            db.refresh(stock)

        for day in _trading_days_back(days, now):
            close_dt = datetime.combine(day, MARKET_CLOSE, tzinfo=IST).astimezone(timezone.utc)
            if close_dt >= now:
                continue
            record_quote(db, stock, provider.quote(stock.symbol, close_dt))
            written += 1
        db.commit()
    return written


if __name__ == "__main__":  # pragma: no cover
    from .db import init_db

    init_db()
    seed_reference_data()
    print(f"Seeded {len(UNIVERSE)} reference stocks.")
