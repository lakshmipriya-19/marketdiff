"""The scoring engine is pure, so it is tested without a database or a server."""

from __future__ import annotations

import pytest

from app.engine.scoring import (
    BAND_HIGH,
    BAND_MEANINGFUL,
    BAND_WATCH,
    ChangeSignals,
    compute_confidence,
    score_change,
    summarise,
)


def signals(**kwargs) -> ChangeSignals:
    base = dict(symbol="TEST", price_change_pct=0.0, baseline_sigma_pct=1.0, history_points=30)
    base.update(kwargs)
    return ChangeSignals(**base)


# --- thresholds --------------------------------------------------------------


def test_ordinary_day_is_not_meaningful():
    result = score_change(signals(price_change_pct=0.4, volume=1_000, average_volume=1_050))
    assert result.severity == "normal"
    assert result.attention_score < BAND_WATCH
    assert not result.is_meaningful


def test_large_move_on_a_quiet_stock_beats_the_same_move_on_a_volatile_one():
    quiet = score_change(signals(price_change_pct=3.0, baseline_sigma_pct=0.5))
    volatile = score_change(signals(price_change_pct=3.0, baseline_sigma_pct=3.0))
    assert quiet.attention_score > volatile.attention_score


def test_move_plus_volume_reaches_high_attention():
    result = score_change(
        signals(
            price_change_pct=4.2,
            baseline_sigma_pct=1.1,
            volume=3_400_000,
            average_volume=1_000_000,
            recent_volatility_pct=2.4,
            baseline_volatility_pct=1.1,
            gap_pct=1.6,
        )
    )
    assert result.attention_score >= BAND_HIGH
    assert result.severity == "high"


def test_score_is_bounded():
    result = score_change(
        signals(
            price_change_pct=42.0,
            baseline_sigma_pct=0.4,
            volume=90_000_000,
            average_volume=1_000_000,
            recent_volatility_pct=20.0,
            baseline_volatility_pct=1.0,
            gap_pct=30.0,
        )
    )
    assert 0 <= result.attention_score <= 100


def test_direction_does_not_change_the_score():
    up = score_change(signals(price_change_pct=3.0))
    down = score_change(signals(price_change_pct=-3.0))
    assert up.attention_score == down.attention_score


def test_score_is_monotonic_in_move_size():
    scores = [score_change(signals(price_change_pct=p)).attention_score for p in (0.5, 1.5, 3.0, 5.0)]
    assert scores == sorted(scores)


def test_engine_is_deterministic():
    payload = signals(price_change_pct=2.7, volume=2_000_000, average_volume=900_000)
    assert score_change(payload).attention_score == score_change(payload).attention_score


def test_severity_bands_line_up_with_the_documented_scale():
    assert BAND_WATCH == 26 and BAND_MEANINGFUL == 51 and BAND_HIGH == 76


# --- signals other than price ------------------------------------------------


def test_volume_alone_can_flag_a_small_move():
    result = score_change(
        signals(price_change_pct=1.4, baseline_sigma_pct=1.0, volume=2_600_000, average_volume=1_000_000)
    )
    assert result.is_meaningful
    assert any(r.code == "unusual_volume" for r in result.reasons)


def test_missing_volume_is_ignored_rather_than_assumed_zero():
    with_volume = score_change(signals(price_change_pct=2.0, volume=1_000, average_volume=1_000))
    without = score_change(signals(price_change_pct=2.0))
    assert with_volume.attention_score == without.attention_score
    assert without.confidence < with_volume.confidence


def test_volatility_regime_shift_contributes():
    calm = score_change(signals(price_change_pct=1.0))
    choppy = score_change(
        signals(price_change_pct=1.0, recent_volatility_pct=2.5, baseline_volatility_pct=1.0)
    )
    assert choppy.attention_score > calm.attention_score


# --- confidence ---------------------------------------------------------------


def test_stale_data_lowers_confidence_and_says_so():
    result = score_change(signals(price_change_pct=3.0, freshness="stale"))
    assert result.confidence < 0.7
    assert any(r.code == "stale_data" for r in result.reasons)


def test_conflicting_sources_lower_confidence_and_are_surfaced():
    clean = score_change(signals(price_change_pct=3.0))
    conflicted = score_change(signals(price_change_pct=3.0, source_disagreement_pct=0.62))
    assert conflicted.confidence < clean.confidence
    assert any(r.code == "source_conflict" for r in conflicted.reasons)


def test_no_history_lowers_confidence_but_still_scores():
    result = score_change(
        ChangeSignals(symbol="NEW", price_change_pct=4.0, baseline_sigma_pct=None, history_points=0)
    )
    assert result.attention_score > 0
    assert result.confidence < 0.7


def test_low_confidence_demotes_but_never_hides():
    result = score_change(
        signals(
            price_change_pct=6.0,
            baseline_sigma_pct=0.8,
            freshness="stale",
            source_disagreement_pct=1.5,
        )
    )
    assert result.confidence < 0.5
    assert result.severity_capped
    # Demotion stops at "watch": a big move on shaky data still reaches the user.
    assert result.severity != "normal"


def test_unavailable_data_scores_zero_and_explains_itself():
    result = score_change(signals(price_change_pct=9.0, freshness="unavailable"))
    assert result.attention_score == 0
    assert result.confidence == 0.0
    assert result.reasons[0].code == "no_data"


@pytest.mark.parametrize("freshness,expected", [("live", 1.0), ("recent", 0.9), ("stale", 0.55)])
def test_confidence_tracks_freshness(freshness, expected):
    value = compute_confidence(
        signals(freshness=freshness, volume=1, average_volume=1, history_points=40)
    )
    assert value == pytest.approx(expected, abs=0.01)


# --- explanations --------------------------------------------------------------


def test_every_reason_is_backed_by_a_number():
    result = score_change(
        signals(price_change_pct=3.2, baseline_sigma_pct=1.0, volume=2_100_000, average_volume=1_000_000)
    )
    assert result.reasons
    for reason in result.reasons:
        if reason.code in {"price_move", "unusual_volume"}:
            assert any(ch.isdigit() for ch in reason.text)


def test_quiet_stock_gets_a_reassuring_explanation():
    result = score_change(signals(price_change_pct=0.1))
    assert result.reasons[0].code == "within_normal"


def test_simple_mode_summary_avoids_jargon():
    result = score_change(
        signals(price_change_pct=1.2, volume=3_000_000, average_volume=1_000_000)
    )
    assert summarise(result) == "Unusual trading volume"
