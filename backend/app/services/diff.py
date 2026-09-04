"""The diff: what changed since the user's last checkpoint.

For every stock on the watchlist we hold two snapshots — the one that was
current when the user last acknowledged the feed, and the one that is current
now — and ask the scoring engine how much the difference deserves attention.

The one subtlety worth reading: a stock's "normal" range is a *per-session*
figure, so before comparing a multi-day move against it we scale it by the
square root of the number of trading sessions that elapsed. Without that, coming
back after a long weekend makes every holding look like an emergency.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..engine.scoring import ChangeSignals, ScoreResult, score_change, summarise
from ..market_calendar import expected_volume, session_equivalents
from ..models import ChangeEvent, MarketSnapshot, Stock, Watchlist, WatchlistStock
from .freshness import Freshness, classify
from .ingest import as_utc, latest_snapshot, snapshot_at_or_before

# Statuses a feed row can carry.
STATUS_CHANGED = "changed"
STATUS_UNCHANGED = "unchanged"
STATUS_NEW = "new"  # added after the last checkpoint, nothing to compare against
STATUS_NO_DATA = "no_data"


@dataclass
class FeedItem:
    symbol: str
    name: str
    status: str
    price: float | None
    previous_price: float | None
    price_change_pct: float | None
    price_change_abs: float | None
    attention_score: int
    severity: str
    confidence: float
    reasons: list[dict]
    plain_summary: str
    freshness: str
    freshness_label: str
    age_seconds: int | None
    volume_ratio: float | None
    typical_move_pct: float | None
    source: str | None
    is_demo: bool
    source_disagreement_pct: float | None = None
    severity_capped: bool = False
    observed_at: datetime | None = None
    components: dict[str, float] = field(default_factory=dict)
    baseline_observed_at: datetime | None = None
    # Existing observations between the compared endpoints. This is display
    # data only; scoring remains entirely based on the two endpoint snapshots.
    sparkline: list[float] = field(default_factory=list)


@dataclass
class FeedResult:
    items: list[FeedItem]
    checkpoint_at: datetime | None
    now: datetime
    counts: dict[str, int] = field(default_factory=dict)
    sessions_elapsed: float = 0.0

    @property
    def changed(self) -> list[FeedItem]:
        return [i for i in self.items if i.status == STATUS_CHANGED]


SEVERITY_ORDER = {"high": 0, "meaningful": 1, "watch": 2, "normal": 3}


def _scaled_sigma(base_sigma: float | None, sessions: float) -> float | None:
    """Square-root-of-time scaling of a per-session normal range."""
    if base_sigma is None or base_sigma <= 0:
        return None
    return base_sigma * math.sqrt(max(sessions, 0.25))


def _signals_for(
    stock: Stock,
    baseline: MarketSnapshot,
    current: MarketSnapshot,
    fresh: Freshness,
    sessions: float,
) -> ChangeSignals:
    change_pct = (current.price - baseline.price) / baseline.price * 100.0 if baseline.price else 0.0

    observed = as_utc(current.observed_at) or datetime.now(timezone.utc)
    exp_volume = expected_volume(current.average_volume, observed)

    # Only count the opening gap when the comparison actually spans a session
    # boundary; within one session it would double-count the same move.
    gap_pct = None
    if sessions >= 0.9 and current.day_open and current.previous_close:
        gap_pct = (current.day_open - current.previous_close) / current.previous_close * 100.0

    return ChangeSignals(
        symbol=stock.symbol,
        price_change_pct=round(change_pct, 4),
        baseline_sigma_pct=_scaled_sigma(current.sigma_pct, sessions),
        volume=current.volume,
        average_volume=exp_volume,
        recent_volatility_pct=current.recent_volatility_pct,
        baseline_volatility_pct=current.baseline_volatility_pct,
        gap_pct=gap_pct,
        history_points=current.history_points or 0,
        freshness=fresh.state,  # type: ignore[arg-type]
        source_disagreement_pct=current.source_disagreement_pct,
        elapsed_hours=None,
    )


def _item_from(
    db: Session,
    stock: Stock,
    baseline: MarketSnapshot | None,
    current: MarketSnapshot | None,
    fresh: Freshness,
    result: ScoreResult | None,
    signals: ChangeSignals | None,
    status: str,
) -> FeedItem:
    price = current.price if current else None
    prev = baseline.price if baseline else None
    change_pct = signals.price_change_pct if signals else None
    volume_ratio = None
    if signals and signals.volume and signals.average_volume:
        volume_ratio = round(signals.volume / signals.average_volume, 2)

    if result is None:
        result = ScoreResult(0, "normal", 0.0, [], {})

    sparkline = _sparkline_prices(db, stock.id, baseline, current)

    return FeedItem(
        symbol=stock.symbol,
        name=stock.name,
        status=status,
        price=price,
        previous_price=prev,
        price_change_pct=round(change_pct, 2) if change_pct is not None else None,
        price_change_abs=round(price - prev, 2) if price is not None and prev is not None else None,
        attention_score=result.attention_score,
        severity=result.severity,
        confidence=result.confidence,
        reasons=[{"code": r.code, "text": r.text} for r in result.reasons],
        plain_summary=summarise(result) if result.reasons else "Nothing unusual",
        freshness=fresh.state,
        freshness_label=fresh.label,
        age_seconds=fresh.age_seconds,
        volume_ratio=volume_ratio,
        typical_move_pct=round(signals.baseline_sigma_pct, 2)
        if signals and signals.baseline_sigma_pct
        else None,
        source=current.source if current else None,
        is_demo=bool(current.is_demo) if current else False,
        source_disagreement_pct=current.source_disagreement_pct if current else None,
        severity_capped=result.severity_capped,
        observed_at=as_utc(current.observed_at) if current else None,
        components=result.components,
        baseline_observed_at=as_utc(baseline.observed_at) if baseline else None,
        sparkline=sparkline,
    )


def _sparkline_prices(
    db: Session,
    stock_id: int,
    baseline: MarketSnapshot | None,
    current: MarketSnapshot | None,
) -> list[float]:
    """Return stored prices over the exact comparison interval.

    This deliberately performs one bounded database lookup for the complete
    feed, never a provider request.  Keeping a single source avoids drawing a
    false line through mixed live and demo observations. The baseline is kept
    when it came from a prior source so the line still starts at the value the
    user is being compared against.
    """
    if baseline is None or current is None or not baseline.price or not current.price:
        return []

    rows = (
        db.query(MarketSnapshot.price)
        .filter(
            MarketSnapshot.stock_id == stock_id,
            MarketSnapshot.source == current.source,
            MarketSnapshot.observed_at >= baseline.observed_at,
            MarketSnapshot.observed_at <= current.observed_at,
        )
        .order_by(MarketSnapshot.observed_at.asc(), MarketSnapshot.id.asc())
        .limit(24)
        .all()
    )
    prices = [float(row[0]) for row in rows]
    if baseline.source != current.source:
        prices.insert(0, float(baseline.price))
    elif not prices or prices[0] != float(baseline.price):
        prices.insert(0, float(baseline.price))
    if not prices or prices[-1] != float(current.price):
        prices.append(float(current.price))

    # Two points imply a decorative diagonal rather than a useful movement
    # trace. Omit it until the local snapshot history can tell a real story.
    return prices if len(prices) >= 3 else []


def build_feed(
    db: Session,
    watchlist: Watchlist,
    now: datetime | None = None,
    persist_events: bool = True,
) -> FeedResult:
    now = now or datetime.now(timezone.utc)
    checkpoint_at = as_utc(watchlist.checkpoint_at)

    entries = (
        db.query(WatchlistStock)
        .filter(WatchlistStock.watchlist_id == watchlist.id)
        .order_by(WatchlistStock.added_at.asc())
        .all()
    )

    sessions_elapsed = session_equivalents(checkpoint_at, now) if checkpoint_at else 0.0
    items: list[FeedItem] = []

    for entry in entries:
        stock = entry.stock
        current = latest_snapshot(db, stock.id)
        fresh = classify(as_utc(current.observed_at) if current else None, now)

        if current is None:
            items.append(_item_from(db, stock, None, None, fresh, None, None, STATUS_NO_DATA))
            continue

        # The comparison is anchored at the later of the checkpoint and the
        # moment the stock joined the list. Diffing a stock added this morning
        # against Monday's price would report a "change" the user never missed,
        # because they were not watching it on Monday.
        added_at = as_utc(entry.added_at) or now
        anchor = max(checkpoint_at, added_at) if checkpoint_at else None
        baseline = snapshot_at_or_before(db, stock.id, anchor) if anchor else None

        # First ever visit, or no observation from before the anchor: there is no
        # honest comparison to make, so we say so rather than diffing against
        # whatever happened to be lying around.
        if baseline is None:
            items.append(_item_from(db, stock, None, current, fresh, None, None, STATUS_NEW))
            continue

        if baseline.id == current.id:
            result = ScoreResult(
                0,
                "normal",
                1.0,
                [],
                {"move": 0.0, "volume": 0.0, "volatility": 0.0, "gap": 0.0},
            )
            item = _item_from(db, stock, baseline, current, fresh, result, None, STATUS_UNCHANGED)
            item.reasons = [
                {"code": "no_new_data", "text": "No new market data since you last checked."}
            ]
            item.plain_summary = "Nothing new"
            items.append(item)
            continue

        signals = _signals_for(stock, baseline, current, fresh, sessions_elapsed)
        result = score_change(signals)
        status = STATUS_CHANGED if result.is_meaningful else STATUS_UNCHANGED
        item = _item_from(db, stock, baseline, current, fresh, result, signals, status)
        items.append(item)

        if persist_events and result.is_meaningful:
            _persist_event(db, watchlist, stock, baseline, current, signals, result, checkpoint_at)

    items.sort(
        key=lambda i: (
            SEVERITY_ORDER.get(i.severity, 3),
            -i.attention_score,
            i.symbol,
        )
    )

    counts = {
        "high": sum(1 for i in items if i.severity == "high"),
        "meaningful": sum(1 for i in items if i.severity == "meaningful"),
        "watch": sum(1 for i in items if i.severity == "watch"),
        "unchanged": sum(1 for i in items if i.status == STATUS_UNCHANGED),
        "new": sum(1 for i in items if i.status == STATUS_NEW),
        "no_data": sum(1 for i in items if i.status == STATUS_NO_DATA),
        "total": len(items),
    }
    counts["changed"] = counts["high"] + counts["meaningful"] + counts["watch"]

    return FeedResult(
        items=items,
        checkpoint_at=checkpoint_at,
        now=now,
        counts=counts,
        sessions_elapsed=sessions_elapsed,
    )


def _persist_event(
    db: Session,
    watchlist: Watchlist,
    stock: Stock,
    baseline: MarketSnapshot,
    current: MarketSnapshot,
    signals: ChangeSignals,
    result: ScoreResult,
    checkpoint_at: datetime | None,
) -> None:
    """Materialise the diff. Unique constraint makes repeat calls a no-op."""
    volume_ratio = (
        signals.volume / signals.average_volume
        if signals.volume and signals.average_volume
        else None
    )
    volatility_ratio = (
        signals.recent_volatility_pct / signals.baseline_volatility_pct
        if signals.recent_volatility_pct and signals.baseline_volatility_pct
        else None
    )
    event = ChangeEvent(
        watchlist_id=watchlist.id,
        stock_id=stock.id,
        from_snapshot_id=baseline.id,
        to_snapshot_id=current.id,
        checkpoint_at=checkpoint_at,
        price_change_pct=signals.price_change_pct,
        volume_ratio=volume_ratio,
        volatility_ratio=volatility_ratio,
        attention_score=result.attention_score,
        severity=result.severity,
        confidence=result.confidence,
        reasons_json=json.dumps([{"code": r.code, "text": r.text} for r in result.reasons]),
    )
    db.add(event)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()  # already recorded by a concurrent request
