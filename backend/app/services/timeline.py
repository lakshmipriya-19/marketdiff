"""Day-by-day summary for long absences ("Market Replay").

When someone has been away for a week, listing every change would be the same
failure the product exists to fix. Instead we collapse the gap into one row per
trading session, computed from stored snapshots only — no back-filled guesses,
no invented history. Days with no stored data say so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine.scoring import ChangeSignals, score_change
from ..market_calendar import IST, MARKET_CLOSE, expected_volume, is_trading_day
from ..models import MarketSnapshot, WatchlistStock
from .ingest import as_utc

MAX_DAYS = 30


@dataclass
class DayMover:
    symbol: str
    price_change_pct: float
    attention_score: int
    severity: str
    headline: str


@dataclass
class DaySummary:
    day: date
    high: int = 0
    meaningful: int = 0
    watch: int = 0
    quiet: int = 0
    has_data: bool = False
    movers: list[DayMover] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return self.high + self.meaningful + self.watch


def _session_close(day: date) -> datetime:
    return datetime.combine(day, MARKET_CLOSE, tzinfo=IST).astimezone(timezone.utc)


def _snapshot_by_close(db: Session, stock_id: int, day: date) -> MarketSnapshot | None:
    """Last snapshot recorded on or before that session's close."""
    cutoff = _session_close(day)
    floor = cutoff - timedelta(days=6)
    return db.execute(
        select(MarketSnapshot)
        .where(
            MarketSnapshot.stock_id == stock_id,
            MarketSnapshot.observed_at <= cutoff,
            MarketSnapshot.observed_at >= floor,
        )
        .order_by(MarketSnapshot.observed_at.desc(), MarketSnapshot.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def build_timeline(
    db: Session,
    watchlist_id: int,
    since: datetime,
    now: datetime | None = None,
) -> list[DaySummary]:
    now = now or datetime.now(timezone.utc)
    entries = db.query(WatchlistStock).filter(WatchlistStock.watchlist_id == watchlist_id).all()
    if not entries:
        return []

    start = since.astimezone(IST).date()
    end = now.astimezone(IST).date()
    days = [
        d
        for d in (start + timedelta(days=i) for i in range((end - start).days + 1))
        if is_trading_day(d)
    ][-MAX_DAYS:]

    summaries: list[DaySummary] = []
    for day in days:
        summary = DaySummary(day=day)
        previous_day = day - timedelta(days=1)
        while not is_trading_day(previous_day):
            previous_day -= timedelta(days=1)

        for entry in entries:
            current = _snapshot_by_close(db, entry.stock_id, day)
            baseline = _snapshot_by_close(db, entry.stock_id, previous_day)
            if not current or not baseline or baseline.id == current.id or not baseline.price:
                continue
            summary.has_data = True
            observed = as_utc(current.observed_at) or now
            signals = ChangeSignals(
                symbol=entry.stock.symbol,
                price_change_pct=(current.price - baseline.price) / baseline.price * 100.0,
                baseline_sigma_pct=current.sigma_pct,
                volume=current.volume,
                average_volume=expected_volume(current.average_volume, observed),
                recent_volatility_pct=current.recent_volatility_pct,
                baseline_volatility_pct=current.baseline_volatility_pct,
                history_points=current.history_points or 0,
                freshness="live",
            )
            result = score_change(signals)
            if result.severity == "high":
                summary.high += 1
            elif result.severity == "meaningful":
                summary.meaningful += 1
            elif result.severity == "watch":
                summary.watch += 1
            else:
                summary.quiet += 1
                continue
            summary.movers.append(
                DayMover(
                    symbol=entry.stock.symbol,
                    price_change_pct=round(signals.price_change_pct, 2),
                    attention_score=result.attention_score,
                    severity=result.severity,
                    headline=result.reasons[0].text if result.reasons else "",
                )
            )

        summary.movers.sort(key=lambda m: -m.attention_score)
        summary.movers = summary.movers[:3]
        summaries.append(summary)

    return summaries
