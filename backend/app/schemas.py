from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9&\-\.]{0,23}$")


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


# --- requests ---------------------------------------------------------------


class WatchlistRename(ApiModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Name cannot be blank")
        return cleaned


class WatchlistCreate(WatchlistRename):
    # Optional convenience: seed the list in one round trip instead of N.
    symbols: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("symbols")
    @classmethod
    def clean_symbols(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for raw in value:
            symbol = raw.strip().upper()
            if symbol and SYMBOL_RE.match(symbol) and symbol not in seen:
                seen.append(symbol)
        return seen


class AddStock(ApiModel):
    symbol: str = Field(min_length=1, max_length=24)

    @field_validator("symbol")
    @classmethod
    def clean(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not SYMBOL_RE.match(cleaned):
            raise ValueError("Symbol must be letters, digits, '-', '.' or '&'")
        return cleaned


class CheckpointRequest(ApiModel):
    # The newest observation the client actually rendered. Optional: omitting it
    # means "everything up to now", which is what a plain button press implies.
    seen_through: datetime | None = Field(default=None, alias="seenThrough")


class PreferencesUpdate(ApiModel):
    simple_mode: bool | None = Field(default=None, alias="simpleMode")
    attention_threshold: int | None = Field(default=None, alias="attentionThreshold", ge=1, le=100)


# --- responses ---------------------------------------------------------------


class StockOut(ApiModel):
    symbol: str
    name: str
    exchange: str = "NSE"


class WatchlistOut(ApiModel):
    id: int
    name: str
    stock_count: int = Field(alias="stockCount")
    checkpoint_at: datetime | None = Field(default=None, alias="checkpointAt")
    checkpoint_version: int = Field(alias="checkpointVersion")
    last_opened_at: datetime | None = Field(default=None, alias="lastOpenedAt")
    created_at: datetime = Field(alias="createdAt")


class ReasonOut(ApiModel):
    code: str
    text: str


class FeedItemOut(ApiModel):
    symbol: str
    name: str
    status: str
    price: float | None
    previous_price: float | None = Field(alias="previousPrice")
    price_change_pct: float | None = Field(alias="priceChangePct")
    price_change_abs: float | None = Field(alias="priceChangeAbs")
    attention_score: int = Field(alias="attentionScore")
    severity: str
    confidence: float
    reasons: list[ReasonOut]
    plain_summary: str = Field(alias="plainSummary")
    freshness: str
    freshness_label: str = Field(alias="freshnessLabel")
    age_seconds: int | None = Field(alias="ageSeconds")
    volume_ratio: float | None = Field(alias="volumeRatio")
    typical_move_pct: float | None = Field(alias="typicalMovePct")
    source: str | None
    is_demo: bool = Field(alias="isDemo")
    source_disagreement_pct: float | None = Field(default=None, alias="sourceDisagreementPct")
    severity_capped: bool = Field(default=False, alias="severityCapped")
    observed_at: datetime | None = Field(default=None, alias="observedAt")
    components: dict[str, float] = Field(default_factory=dict)
    baseline_observed_at: datetime | None = Field(default=None, alias="baselineObservedAt")
    sparkline: list[float] = Field(default_factory=list)


class DayMoverOut(ApiModel):
    symbol: str
    price_change_pct: float = Field(alias="priceChangePct")
    attention_score: int = Field(alias="attentionScore")
    severity: str
    headline: str


class DaySummaryOut(ApiModel):
    day: str
    high: int
    meaningful: int
    watch: int
    quiet: int
    changed: int
    has_data: bool = Field(alias="hasData")
    movers: list[DayMoverOut]


class FeedOut(ApiModel):
    watchlist: WatchlistOut
    items: list[FeedItemOut]
    counts: dict[str, int]
    checkpoint_at: datetime | None = Field(default=None, alias="checkpointAt")
    generated_at: datetime = Field(alias="generatedAt")
    away_seconds: int | None = Field(default=None, alias="awaySeconds")
    away_label: str | None = Field(default=None, alias="awayLabel")
    sessions_elapsed: float = Field(alias="sessionsElapsed")
    timeline: list[DaySummaryOut] = Field(default_factory=list)
    data: dict


class CheckpointOut(ApiModel):
    checkpoint_at: datetime | None = Field(default=None, alias="checkpointAt")
    checkpoint_version: int = Field(alias="checkpointVersion")
    advanced: bool
    reason: str


class PreferencesOut(ApiModel):
    simple_mode: bool = Field(alias="simpleMode")
    attention_threshold: int = Field(alias="attentionThreshold")


class SearchHitOut(ApiModel):
    symbol: str
    name: str
    exchange: str = "NSE"
    in_watchlist: bool = Field(default=False, alias="inWatchlist")
