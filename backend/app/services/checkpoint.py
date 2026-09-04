"""What "last checked" means, precisely.

Definition: ``watchlist.checkpoint_at`` is the instant of the newest market
observation the user has explicitly acknowledged. It moves only when the client
posts a checkpoint, never as a side effect of loading the page — otherwise the
act of reading the feed would erase the feed, and a stray background refresh in
a forgotten tab could silently mark three days of changes as seen.

Concurrency rules:

* **Monotonic.** The checkpoint never moves backwards. A slow request from an
  old tab carrying an earlier ``seen_through`` cannot rewind it.
* **Bounded.** It can never move past ``now``, so a client with a skewed clock
  cannot acknowledge the future and blind itself to real changes.
* **Optimistic.** The advance is a single conditional UPDATE guarded by a version
  column. Two tabs pressing "Mark as read" simultaneously produce one winner and
  one no-op, not a lost update or a torn value.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..models import Watchlist
from .ingest import as_utc


@dataclass(frozen=True)
class CheckpointResult:
    checkpoint_at: datetime | None
    version: int
    advanced: bool
    reason: str


def advance_checkpoint(
    db: Session,
    watchlist: Watchlist,
    seen_through: datetime | None = None,
    now: datetime | None = None,
) -> CheckpointResult:
    now = now or datetime.now(timezone.utc)
    target = as_utc(seen_through) or now
    if target > now:
        target = now

    current = as_utc(watchlist.checkpoint_at)
    if current is not None and target <= current:
        return CheckpointResult(current, watchlist.checkpoint_version, False, "already_current")

    result = db.execute(
        update(Watchlist)
        .where(
            Watchlist.id == watchlist.id,
            Watchlist.checkpoint_version == watchlist.checkpoint_version,
        )
        .values(
            checkpoint_at=target,
            checkpoint_version=Watchlist.checkpoint_version + 1,
            last_opened_at=now,
        )
    )
    db.commit()
    db.refresh(watchlist)

    if result.rowcount == 0:
        # Another writer advanced it first. Their value is at least as new as
        # ours, so we adopt it rather than retrying and clobbering.
        return CheckpointResult(
            as_utc(watchlist.checkpoint_at),
            watchlist.checkpoint_version,
            False,
            "concurrent_update",
        )

    return CheckpointResult(
        as_utc(watchlist.checkpoint_at), watchlist.checkpoint_version, True, "advanced"
    )


def touch_opened(db: Session, watchlist: Watchlist, now: datetime | None = None) -> None:
    """Record that the feed was viewed. Deliberately does not move the checkpoint."""
    now = now or datetime.now(timezone.utc)
    db.execute(
        update(Watchlist).where(Watchlist.id == watchlist.id).values(last_opened_at=now)
    )
    db.commit()
