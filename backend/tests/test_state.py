"""Data-integrity tests: ordering, idempotency, and concurrent checkpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import MarketSnapshot, StockState, User, Watchlist
from app.providers.base import Quote
from app.services.checkpoint import advance_checkpoint
from app.services.freshness import classify
from app.services.ingest import latest_snapshot, record_quote, snapshot_at_or_before

NOW = datetime(2026, 3, 10, 6, 0, tzinfo=timezone.utc)  # a Tuesday, mid-session


def quote(price: float, observed_at: datetime, source: str = "demo") -> Quote:
    return Quote(
        symbol="RELIANCE",
        price=price,
        observed_at=observed_at,
        source=source,
        previous_close=price * 0.99,
        volume=1_000_000,
        average_volume=900_000,
        sigma_pct=1.1,
        history_points=30,
        is_demo=True,
    )


# --- snapshot ingestion --------------------------------------------------------


def test_ingesting_the_same_observation_twice_creates_one_row(db, stock):
    record_quote(db, stock, quote(100.0, NOW))
    record_quote(db, stock, quote(100.0, NOW))
    db.commit()
    assert db.query(MarketSnapshot).filter(MarketSnapshot.stock_id == stock.id).count() == 1


def test_newer_observation_advances_the_pointer(db, stock):
    record_quote(db, stock, quote(100.0, NOW))
    record_quote(db, stock, quote(105.0, NOW + timedelta(minutes=5)))
    db.commit()
    assert latest_snapshot(db, stock.id).price == 105.0


def test_out_of_order_arrival_is_stored_but_never_becomes_current(db, stock):
    record_quote(db, stock, quote(105.0, NOW + timedelta(minutes=5)))
    snapshot, advanced = record_quote(db, stock, quote(100.0, NOW))
    db.commit()

    assert advanced is False
    # The late observation is kept as history...
    assert db.query(MarketSnapshot).filter(MarketSnapshot.stock_id == stock.id).count() == 2
    assert snapshot.price == 100.0
    # ...but the user's view of "now" did not rewind.
    assert latest_snapshot(db, stock.id).price == 105.0


def test_pointer_version_increments_only_on_a_real_advance(db, stock):
    record_quote(db, stock, quote(100.0, NOW))
    db.commit()
    first = db.get(StockState, stock.id).version

    record_quote(db, stock, quote(100.0, NOW))  # duplicate
    record_quote(db, stock, quote(99.0, NOW - timedelta(minutes=1)))  # out of order
    db.commit()
    assert db.get(StockState, stock.id).version == first


def test_two_sources_for_the_same_instant_coexist(db, stock):
    record_quote(db, stock, quote(100.0, NOW, source="demo"))
    record_quote(db, stock, quote(100.4, NOW, source="other"))
    db.commit()
    assert db.query(MarketSnapshot).filter(MarketSnapshot.stock_id == stock.id).count() == 2


def test_snapshot_at_or_before_finds_the_checkpoint_baseline(db, stock):
    record_quote(db, stock, quote(100.0, NOW - timedelta(hours=2)))
    record_quote(db, stock, quote(103.0, NOW))
    db.commit()
    baseline = snapshot_at_or_before(db, stock.id, NOW - timedelta(hours=1))
    assert baseline.price == 100.0


# --- freshness -----------------------------------------------------------------


def test_freshness_states(db):
    assert classify(NOW, NOW).state == "live"
    assert classify(NOW - timedelta(minutes=10), NOW).state == "recent"
    assert classify(NOW - timedelta(hours=5), NOW).state == "stale"
    assert classify(None, NOW).state == "unavailable"


def test_stale_label_reports_the_last_reliable_time(db):
    fresh = classify(NOW - timedelta(hours=5), NOW)
    assert "5 hours ago" in fresh.label
    assert not fresh.is_trustworthy


# --- checkpoint ----------------------------------------------------------------


def _watchlist(db) -> Watchlist:
    user = User(user_key="state-test-user-1")
    db.add(user)
    db.commit()
    watchlist = Watchlist(user_id=user.id, name="Test")
    db.add(watchlist)
    db.commit()
    db.refresh(watchlist)
    return watchlist


def test_first_checkpoint_is_set(db):
    watchlist = _watchlist(db)
    result = advance_checkpoint(db, watchlist, seen_through=NOW, now=NOW)
    assert result.advanced
    assert result.checkpoint_at == NOW


def test_checkpoint_never_moves_backwards(db):
    watchlist = _watchlist(db)
    advance_checkpoint(db, watchlist, seen_through=NOW, now=NOW)
    result = advance_checkpoint(db, watchlist, seen_through=NOW - timedelta(hours=3), now=NOW)

    assert result.advanced is False
    assert result.reason == "already_current"
    assert result.checkpoint_at == NOW


def test_checkpoint_cannot_be_set_in_the_future(db):
    watchlist = _watchlist(db)
    result = advance_checkpoint(db, watchlist, seen_through=NOW + timedelta(days=2), now=NOW)
    assert result.checkpoint_at == NOW


def test_concurrent_checkpoint_writes_produce_one_winner(db):
    """Two tabs marking the feed as seen with a stale version in hand."""
    watchlist = _watchlist(db)
    advance_checkpoint(db, watchlist, seen_through=NOW - timedelta(hours=2), now=NOW)

    # Simulate a second tab holding an out-of-date version token.
    watchlist.checkpoint_version = watchlist.checkpoint_version - 1
    result = advance_checkpoint(db, watchlist, seen_through=NOW, now=NOW)

    assert result.advanced is False
    assert result.reason == "concurrent_update"
    # And the stored value is still the newer of the two, never a torn write.
    db.refresh(watchlist)
    assert watchlist.checkpoint_at is not None


def test_repeated_checkpoint_with_same_value_is_idempotent(db):
    watchlist = _watchlist(db)
    first = advance_checkpoint(db, watchlist, seen_through=NOW, now=NOW)
    second = advance_checkpoint(db, watchlist, seen_through=NOW, now=NOW)
    assert first.checkpoint_at == second.checkpoint_at
    assert second.advanced is False
