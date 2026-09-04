"""Relational model for MarketDiff.

Two constraints drive most of the design:

1. A market snapshot is an immutable observation. We never update one; we insert
   a new row. Uniqueness is (stock, source, observed_at), which makes ingestion
   naturally idempotent — replaying the same quote twice is a no-op.

2. "Latest" is a separate, explicitly guarded pointer (``StockState``) rather
   than ``ORDER BY observed_at DESC LIMIT 1``. The pointer only ever moves
   forward in observation time, so an out-of-order or delayed response from a
   provider can never overwrite fresher data.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """Deliberately minimal: an opaque client-generated key, no PII.

    There is no email, name, or password. The key identifies a browser, which is
    all a watchlist actually needs.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    watchlists: Mapped[list["Watchlist"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    preferences: Mapped["UserPreference | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


class UserPreference(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    simple_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    # Minimum attention score before something enters the feed as "changed".
    attention_threshold: Mapped[int] = mapped_column(Integer, default=26)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="preferences")


class Stock(Base):
    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    exchange: Mapped[str] = mapped_column(String(24), default="NSE")
    currency: Mapped[str] = mapped_column(String(8), default="INR")

    state: Mapped["StockState | None"] = relationship(
        back_populates="stock", cascade="all, delete-orphan", uselist=False
    )


class Watchlist(Base):
    __tablename__ = "watchlists"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_watchlist_user_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # --- "last checked" state -------------------------------------------------
    # checkpoint_at is the instant the user last acknowledged the feed. Diffs are
    # computed against the market state as it stood at this moment.
    checkpoint_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Optimistic-concurrency guard. Every checkpoint advance is a conditional
    # UPDATE ... WHERE version = :seen, so two tabs cannot interleave and lose
    # an update, and the timestamp itself is monotonic (never moves backwards).
    checkpoint_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Purely informational: when the feed was last rendered, used for the
    # "you haven't checked in 3 days" copy without affecting the diff.
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    user: Mapped[User] = relationship(back_populates="watchlists")
    entries: Mapped[list["WatchlistStock"]] = relationship(
        back_populates="watchlist", cascade="all, delete-orphan"
    )


class WatchlistStock(Base):
    __tablename__ = "watchlist_stocks"
    __table_args__ = (
        UniqueConstraint("watchlist_id", "stock_id", name="uq_watchlist_stock"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(
        ForeignKey("watchlists.id", ondelete="CASCADE"), index=True
    )
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    watchlist: Mapped[Watchlist] = relationship(back_populates="entries")
    stock: Mapped[Stock] = relationship()


class MarketSnapshot(Base):
    """One immutable observation of one stock from one source."""

    __tablename__ = "market_snapshots"
    __table_args__ = (
        UniqueConstraint("stock_id", "source", "observed_at", name="uq_snapshot_identity"),
        Index("ix_snapshot_stock_observed", "stock_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(32))
    # When the market data was true, per the provider — not when we stored it.
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    price: Mapped[float] = mapped_column(Float)
    previous_close: Mapped[float | None] = mapped_column(Float, default=None)
    day_open: Mapped[float | None] = mapped_column(Float, default=None)
    day_high: Mapped[float | None] = mapped_column(Float, default=None)
    day_low: Mapped[float | None] = mapped_column(Float, default=None)
    volume: Mapped[float | None] = mapped_column(Float, default=None)
    average_volume: Mapped[float | None] = mapped_column(Float, default=None)
    # Stdev of recent daily returns, in percent: this stock's "normal" move.
    sigma_pct: Mapped[float | None] = mapped_column(Float, default=None)
    recent_volatility_pct: Mapped[float | None] = mapped_column(Float, default=None)
    baseline_volatility_pct: Mapped[float | None] = mapped_column(Float, default=None)
    history_points: Mapped[int] = mapped_column(Integer, default=0)
    # Largest relative price disagreement seen across sources, in percent.
    source_disagreement_pct: Mapped[float | None] = mapped_column(Float, default=None)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)

    stock: Mapped[Stock] = relationship()


class StockState(Base):
    """Monotonic pointer to the newest observation we trust for a stock."""

    __tablename__ = "stock_state"

    stock_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), primary_key=True
    )
    latest_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("market_snapshots.id", ondelete="SET NULL"), default=None
    )
    latest_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # Bumped on every accepted advance; used as an optimistic-concurrency token.
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_error: Mapped[str | None] = mapped_column(String(200), default=None)

    stock: Mapped[Stock] = relationship(back_populates="state")
    latest_snapshot: Mapped[MarketSnapshot | None] = relationship(
        foreign_keys=[latest_snapshot_id]
    )


class ChangeEvent(Base):
    """A materialised diff between two snapshots for one watchlist.

    Persisted so the "while you were away" timeline can be rebuilt without
    recomputing the whole history, and so scores shown to the user are stable
    rather than silently re-derived on every request.
    """

    __tablename__ = "change_events"
    __table_args__ = (
        UniqueConstraint(
            "watchlist_id",
            "stock_id",
            "from_snapshot_id",
            "to_snapshot_id",
            name="uq_change_event_identity",
        ),
        Index("ix_change_watchlist_created", "watchlist_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(
        ForeignKey("watchlists.id", ondelete="CASCADE"), index=True
    )
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    from_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("market_snapshots.id", ondelete="SET NULL"), default=None
    )
    to_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("market_snapshots.id", ondelete="SET NULL"), default=None
    )
    checkpoint_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    price_change_pct: Mapped[float | None] = mapped_column(Float, default=None)
    volume_ratio: Mapped[float | None] = mapped_column(Float, default=None)
    volatility_ratio: Mapped[float | None] = mapped_column(Float, default=None)
    attention_score: Mapped[int] = mapped_column(Integer, default=0)
    severity: Mapped[str] = mapped_column(String(16), default="normal")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    # JSON-encoded list of {code, text} — kept as text so the model works
    # identically on SQLite and Postgres.
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    stock: Mapped[Stock] = relationship()
