from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # SQLite by default so the project runs with zero setup. Point DATABASE_URL
    # at Postgres in deployment: the ORM layer is identical either way.
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./marketdiff.db")
    # demo | yahoo | auto  (auto tries yahoo, falls back to demo and says so)
    provider: str = os.getenv("MARKETDIFF_PROVIDER", "auto").strip().lower()
    provider_timeout_s: float = float(os.getenv("MARKETDIFF_PROVIDER_TIMEOUT", "4.0"))
    # How long a fetched quote may be reused before we refetch.
    quote_ttl_s: int = int(os.getenv("MARKETDIFF_QUOTE_TTL", "45"))
    cors_origins: tuple[str, ...] = tuple(
        o.strip()
        for o in os.getenv(
            "MARKETDIFF_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
        ).split(",")
        if o.strip()
    )
    seed_on_start: bool = _bool("MARKETDIFF_SEED", True)
    # Freshness thresholds, in seconds.
    live_max_age_s: int = int(os.getenv("MARKETDIFF_LIVE_MAX_AGE", "180"))
    recent_max_age_s: int = int(os.getenv("MARKETDIFF_RECENT_MAX_AGE", "1800"))
    stale_max_age_s: int = int(os.getenv("MARKETDIFF_STALE_MAX_AGE", "172800"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
