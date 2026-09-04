"""Freshness classification.

The product rule is simple and absolute: never render old data as if it were
current. Every price the UI shows carries one of four states and the age that
produced it, so "₹2,890" is always accompanied by how much that number can be
trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..config import get_settings
from ..market_calendar import last_session_close, market_is_open


@dataclass(frozen=True)
class Freshness:
    state: str  # live | recent | stale | unavailable
    age_seconds: int | None
    label: str

    @property
    def is_trustworthy(self) -> bool:
        return self.state in {"live", "recent"}


def _humanise(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = seconds // 86400
    return f"{days} day{'s' if days != 1 else ''} ago"


def classify(observed_at: datetime | None, now: datetime | None = None) -> Freshness:
    now = now or datetime.now(timezone.utc)
    if observed_at is None:
        return Freshness("unavailable", None, "No data received")

    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    age = max(int((now - observed_at).total_seconds()), 0)
    settings = get_settings()

    if age <= settings.live_max_age_s:
        return Freshness("live", age, f"Updated {_humanise(age)}")
    if age <= settings.recent_max_age_s:
        return Freshness("recent", age, f"Updated {_humanise(age)}")
    # Outside market hours an hours-old price is expected rather than broken —
    # but only if it is actually the closing print. A feed that stopped updating
    # at lunchtime is stale no matter what time you look at it.
    if not market_is_open(now) and age <= 86400:
        close = last_session_close(now)
        if observed_at >= close - timedelta(minutes=20):
            return Freshness("recent", age, f"At the closing bell, {_humanise(age)}")

    return Freshness("stale", age, f"Last reliable update {_humanise(age)}")
