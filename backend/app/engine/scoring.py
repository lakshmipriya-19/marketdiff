"""Attention scoring engine.

Pure functions only: no database, no network, no clock. Everything the engine
needs arrives in ``ChangeSignals`` so the whole thing is trivially testable and
reproducible.

The score answers one question: *how much does this deserve a human's attention
right now?* It is deliberately NOT a prediction, a rating, or advice.

Design notes
------------
Four weighted components, 100 points total:

    move        45   how large is the move relative to this stock's own normal
    volume      25   is participation unusual
    volatility  15   has the stock's recent volatility regime shifted
    gap         15   did it open away from the previous close

Each component is normalised into 0..1 and multiplied by its weight, so the
formula fits on one line and can be explained on a whiteboard:

    attention = 45*move + 25*volume + 15*vol_regime + 15*gap

Everything is measured *relative to the instrument's own recent behaviour*.
A 2% move in a stock that normally moves 0.4% is a bigger event than a 4% move
in one that swings 5% a day. That relativity is the point: a flat percentage
threshold would flag the same noisy names every single day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["normal", "watch", "meaningful", "high"]
Freshness = Literal["live", "recent", "stale", "unavailable"]

# --- Component weights (sum to 100) -----------------------------------------
W_MOVE = 45.0
W_VOLUME = 25.0
W_VOLATILITY = 15.0
W_GAP = 15.0

# --- Normalisation constants -------------------------------------------------
# A move of 3 standard deviations saturates the move component.
MOVE_SIGMA_SATURATION = 3.0
# ...but a very large absolute move still matters even for a volatile name.
MOVE_ABS_SATURATION_PCT = 6.0
# Volume at 3x its average saturates the volume component.
VOLUME_RATIO_SATURATION = 3.0
# Recent volatility at 2.5x its baseline saturates the volatility component.
VOLATILITY_RATIO_SATURATION = 2.5
# An opening gap of 2.5 sigma saturates the gap component.
GAP_SIGMA_SATURATION = 2.5
# Floor on sigma so that an unnaturally quiet history cannot make every
# rounding error look like a 40-sigma event.
MIN_SIGMA_PCT = 0.35

# --- Severity bands ----------------------------------------------------------
BAND_WATCH = 26
BAND_MEANINGFUL = 51
BAND_HIGH = 76

# Below this confidence we refuse to shout: severity is capped one band lower.
LOW_CONFIDENCE = 0.5

# Minimum contribution before a component earns a sentence in the explanation.
REASON_MIN_POINTS = 4.0


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class ChangeSignals:
    """Everything the engine needs to score one stock's change.

    All percentages are expressed in percent units (1.5 == 1.5%), not fractions.
    Optional fields are genuinely optional: the engine degrades gracefully and
    lowers confidence rather than inventing values.
    """

    symbol: str
    price_change_pct: float
    # Typical single-session move for this stock (stdev of recent daily returns).
    baseline_sigma_pct: float | None = None
    volume: float | None = None
    # Volume a typical session would have printed by now (pace-adjusted, so an
    # intraday reading is compared like-for-like rather than against a full day).
    average_volume: float | None = None
    # Realised volatility of the most recent window vs the longer baseline.
    recent_volatility_pct: float | None = None
    baseline_volatility_pct: float | None = None
    # Open vs previous close, i.e. the overnight gap.
    gap_pct: float | None = None
    # How many historical observations the baselines were computed from.
    history_points: int = 0
    freshness: Freshness = "live"
    # Largest relative disagreement between sources, in percent (None == agreed).
    source_disagreement_pct: float | None = None
    # Time elapsed between the two snapshots being compared, in hours.
    elapsed_hours: float | None = None


@dataclass(frozen=True)
class Reason:
    """One human-readable, number-backed justification."""

    code: str
    text: str
    points: float


@dataclass(frozen=True)
class ScoreResult:
    attention_score: int
    severity: Severity
    confidence: float
    reasons: list[Reason]
    components: dict[str, float] = field(default_factory=dict)
    # Populated when the raw score was demoted because we do not trust the data.
    severity_capped: bool = False

    @property
    def is_meaningful(self) -> bool:
        return self.severity != "normal"


def effective_sigma(signals: ChangeSignals) -> float:
    """Baseline daily move, floored so quiet history cannot manufacture drama."""
    sigma = signals.baseline_sigma_pct
    if sigma is None or sigma <= 0:
        # No usable history: fall back to a broad-market assumption. Confidence
        # is reduced separately so the UI can say we are guessing at "normal".
        return 1.2
    return max(sigma, MIN_SIGMA_PCT)


def _move_component(signals: ChangeSignals) -> tuple[float, float, Reason | None]:
    sigma = effective_sigma(signals)
    move = abs(signals.price_change_pct)
    z = move / sigma
    relative = z / MOVE_SIGMA_SATURATION
    absolute = move / MOVE_ABS_SATURATION_PCT
    normalised = _clamp(max(relative, absolute))
    points = W_MOVE * normalised

    reason = None
    if points >= REASON_MIN_POINTS:
        direction = "up" if signals.price_change_pct >= 0 else "down"
        if z >= 2.0:
            shape = "far beyond its usual daily range"
        elif z >= 1.2:
            shape = "wider than its usual daily range"
        else:
            shape = "within its usual daily range, but large in absolute terms"
        reason = Reason(
            code="price_move",
            text=(
                f"Price moved {direction} {move:.1f}%, about {z:.1f}x its typical "
                f"daily move of {sigma:.1f}% — {shape}."
            ),
            points=points,
        )
    return points, z, reason


def _volume_component(signals: ChangeSignals) -> tuple[float, float | None, Reason | None]:
    if not signals.volume or not signals.average_volume or signals.average_volume <= 0:
        return 0.0, None, None
    ratio = signals.volume / signals.average_volume
    normalised = _clamp((ratio - 1.0) / (VOLUME_RATIO_SATURATION - 1.0))
    points = W_VOLUME * normalised
    reason = None
    if points >= REASON_MIN_POINTS:
        reason = Reason(
            code="unusual_volume",
            text=(
                f"Trading volume is {ratio:.1f}x the normal pace for this point "
                "in the session."
            ),
            points=points,
        )
    return points, ratio, reason


def _volatility_component(signals: ChangeSignals) -> tuple[float, float | None, Reason | None]:
    recent = signals.recent_volatility_pct
    baseline = signals.baseline_volatility_pct
    if not recent or not baseline or baseline <= 0:
        return 0.0, None, None
    ratio = recent / baseline
    normalised = _clamp((ratio - 1.0) / (VOLATILITY_RATIO_SATURATION - 1.0))
    points = W_VOLATILITY * normalised
    reason = None
    if points >= REASON_MIN_POINTS:
        reason = Reason(
            code="volatility_shift",
            text=(
                f"Recent price swings are {ratio:.1f}x calmer periods — this stock "
                "has become more volatile than usual."
            ),
            points=points,
        )
    return points, ratio, reason


def _gap_component(signals: ChangeSignals) -> tuple[float, float | None, Reason | None]:
    if signals.gap_pct is None:
        return 0.0, None, None
    sigma = effective_sigma(signals)
    gap = abs(signals.gap_pct)
    z = gap / sigma
    normalised = _clamp(z / GAP_SIGMA_SATURATION)
    points = W_GAP * normalised
    reason = None
    if points >= REASON_MIN_POINTS:
        direction = "above" if signals.gap_pct >= 0 else "below"
        reason = Reason(
            code="opening_gap",
            text=f"It opened {gap:.1f}% {direction} the previous close.",
            points=points,
        )
    return points, z, reason


def compute_confidence(signals: ChangeSignals) -> float:
    """How much we trust the inputs, in 0..1.

    Confidence is about *data quality*, never about market direction. It is
    reported separately from the score so a big number backed by thin data is
    never mistaken for a big number backed by good data.
    """
    confidence = 1.0

    if signals.freshness == "unavailable":
        return 0.0
    if signals.freshness == "stale":
        confidence *= 0.55
    elif signals.freshness == "recent":
        confidence *= 0.9

    if signals.baseline_sigma_pct is None:
        confidence *= 0.6
    elif signals.history_points < 10:
        confidence *= 0.75
    elif signals.history_points < 20:
        confidence *= 0.9

    if signals.volume is None or signals.average_volume is None:
        confidence *= 0.85

    if signals.source_disagreement_pct:
        # Feeds disagreeing by 2% would halve our confidence; smaller gaps scale
        # linearly. Capped, because a price disagreement is a reason to caveat a
        # number, not a reason to pretend we know nothing.
        penalty = _clamp(signals.source_disagreement_pct / 2.0, 0.0, 0.45)
        confidence *= 1.0 - penalty

    return round(_clamp(confidence), 3)


def _severity_for(score: int) -> Severity:
    if score >= BAND_HIGH:
        return "high"
    if score >= BAND_MEANINGFUL:
        return "meaningful"
    if score >= BAND_WATCH:
        return "watch"
    return "normal"


# Low confidence lowers the volume of a claim; it never silences it. Demotion
# stops at "watch" so a large move on shaky data still reaches the user, flagged
# for what it is, instead of vanishing from the feed.
_DEMOTE: dict[Severity, Severity] = {
    "high": "meaningful",
    "meaningful": "watch",
    "watch": "watch",
    "normal": "normal",
}


def score_change(signals: ChangeSignals) -> ScoreResult:
    """Score one stock's change since the user's last checkpoint."""
    if signals.freshness == "unavailable":
        return ScoreResult(
            attention_score=0,
            severity="normal",
            confidence=0.0,
            reasons=[
                Reason(
                    code="no_data",
                    text="No market data available for this stock right now.",
                    points=0.0,
                )
            ],
            components={"move": 0.0, "volume": 0.0, "volatility": 0.0, "gap": 0.0},
        )

    move_pts, move_z, move_reason = _move_component(signals)
    vol_pts, vol_ratio, vol_reason = _volume_component(signals)
    vola_pts, vola_ratio, vola_reason = _volatility_component(signals)
    gap_pts, gap_z, gap_reason = _gap_component(signals)

    total = move_pts + vol_pts + vola_pts + gap_pts
    score = int(round(_clamp(total, 0.0, 100.0)))

    confidence = compute_confidence(signals)
    severity = _severity_for(score)
    capped = False
    if confidence < LOW_CONFIDENCE and severity != "normal":
        severity = _DEMOTE[severity]
        capped = True

    reasons = [r for r in (move_reason, vol_reason, vola_reason, gap_reason) if r]
    reasons.sort(key=lambda r: r.points, reverse=True)

    if signals.source_disagreement_pct and signals.source_disagreement_pct > 0.25:
        reasons.append(
            Reason(
                code="source_conflict",
                text=(
                    f"Data sources disagree by {signals.source_disagreement_pct:.2f}% "
                    "on the current price, so treat this as indicative."
                ),
                points=0.0,
            )
        )
    if signals.freshness == "stale":
        reasons.append(
            Reason(
                code="stale_data",
                text="The latest reliable update for this stock is old, so this "
                "comparison may already be out of date.",
                points=0.0,
            )
        )
    if not reasons:
        reasons.append(
            Reason(
                code="within_normal",
                text="Movement and volume are both within this stock's normal range.",
                points=0.0,
            )
        )

    return ScoreResult(
        attention_score=score,
        severity=severity,
        confidence=confidence,
        reasons=reasons,
        components={
            "move": round(move_pts, 2),
            "volume": round(vol_pts, 2),
            "volatility": round(vola_pts, 2),
            "gap": round(gap_pts, 2),
        },
        severity_capped=capped,
    )


def summarise(result: ScoreResult) -> str:
    """One plain-language line for Simple Mode. No jargon, no numbers soup."""
    mapping = {
        "unusual_volume": "Unusual trading volume",
        "price_move": "Bigger move than normal",
        "volatility_shift": "Choppier than usual",
        "opening_gap": "Opened away from yesterday's close",
        "stale_data": "Data may be out of date",
        "source_conflict": "Sources disagree on the price",
        "no_data": "No data available",
        "within_normal": "Nothing unusual",
    }
    for reason in result.reasons:
        if reason.code in mapping:
            return mapping[reason.code]
    return "Nothing unusual"
