"""Demo-only affordances.

Kept in one file so the UI can explicitly prepare its deterministic demo
comparison without changing the live-provider configuration.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import Conflict
from ..models import User, WatchlistStock
from ..schemas import ApiModel
from ..seed import backfill_demo_history
from ..services.checkpoint import advance_checkpoint
from .deps import get_user, owned_watchlist

router = APIRouter(prefix="/api/demo", tags=["demo"])


class PrepareRequest(ApiModel):
    watchlist_id: int
    days: int = 6
    # Close enough that the scripted "today" move dominates the comparison
    # instead of being diluted by several sessions of unrelated background
    # noise — see the comment above SCRIPTED_TODAY in providers/demo.py.
    checkpoint_days_ago: int = 2


@router.post("/prepare")
def prepare_demo(
    payload: PrepareRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_user),
):
    """Backfill synthetic history and rewind the checkpoint.

    This is what makes the change feed demonstrable on a fresh database: without
    a prior checkpoint there is, correctly, nothing to diff against.
    """
    watchlist = owned_watchlist(db, user, payload.watchlist_id)
    symbols = [
        e.stock.symbol
        for e in db.query(WatchlistStock)
        .filter(WatchlistStock.watchlist_id == watchlist.id)
        .all()
    ]
    if not symbols:
        raise Conflict("Add some stocks before preparing the demo.", code="empty_watchlist")

    now = datetime.now(timezone.utc)
    written = backfill_demo_history(db, symbols, days=max(payload.days, 2), now=now)

    # Rewind the checkpoint by writing it directly: advance_checkpoint is
    # monotonic by design and will not move a checkpoint backwards.
    rewound_to = now - timedelta(days=max(payload.checkpoint_days_ago, 1))
    watchlist.checkpoint_at = rewound_to
    watchlist.checkpoint_version = watchlist.checkpoint_version + 1
    # Entries must be backdated too, otherwise every stock reads as "added after
    # your last checkpoint" and the feed correctly refuses to diff them.
    for entry in db.query(WatchlistStock).filter(
        WatchlistStock.watchlist_id == watchlist.id
    ):
        entry.added_at = rewound_to - timedelta(days=1)
    db.commit()

    return {
        "snapshotsWritten": written,
        "checkpointAt": rewound_to,
        "symbols": symbols,
    }


@router.post("/reset-checkpoint")
def reset_checkpoint(
    payload: PrepareRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_user),
):
    """Move the checkpoint to now, so the next visit starts from a clean slate."""
    watchlist = owned_watchlist(db, user, payload.watchlist_id)
    result = advance_checkpoint(db, watchlist)
    return {"checkpointAt": result.checkpoint_at, "advanced": result.advanced}
