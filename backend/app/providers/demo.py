"""Deterministic synthetic market data.

Why this exists: a hackathon demo cannot depend on a third-party API being up,
and a change-detection product is worthless to demo if nothing interesting
happened today. But random noise is equally worthless — you cannot rehearse a
demo against a random number generator, and you cannot write tests against one.

So the demo feed is a *pure function of (symbol, timestamp)*. The same instant
always yields the same market. Restarting the server, replaying Tuesday, or
running the test suite all produce byte-identical prices.

Construction:

* Each symbol has a fixed character — base price, typical daily volatility,
  average volume. The price levels are real NSE levels, not round numbers: a
  demo price that is twice the real one produces a fabricated three-digit
  "change" the moment it is compared against a snapshot from the live provider.
* The price path is walked from a fixed ``EPOCH``, never from a rolling window,
  so every call agrees on what a given day closed at. Two readings of the same
  stock are therefore two points on one path, and the percentage between them
  is a real percentage rather than an artefact of two different realisations.
* Each trading day draws a return from a fat-tailed distribution seeded by
  ``md5(symbol + date)``, pulled back toward the base level by a mean-reversion
  term. Most days are quiet; occasionally one is not. That gives Market Replay
  genuine day-to-day variety instead of a canned script, while keeping the
  level anchored to something an Indian large cap actually trades at.
* For the *current* session, six symbols are pinned to specific situations so
  "Demo · All states" always shows the cases the product was built for: a
  large move with heavy volume, a meaningful adverse move, a smaller move worth
  watching, an ordinary day, a stale feed, and a feed with no data at all.

Nothing here decides a score or a severity. The provider only produces prices,
volumes and timestamps; the scoring engine reads them and reaches its own
conclusion, exactly as it does for the live provider.
"""

from __future__ import annotations

import hashlib
import math
import random
from datetime import date, datetime, timedelta, timezone
from threading import Lock

from ..market_calendar import (
    IST,
    MARKET_CLOSE,
    MARKET_OPEN,
    is_trading_day,
    last_trading_day,
    session_fraction,
)
from .base import MarketDataProvider, Quote, SearchHit

EPOCH = date(2024, 1, 1)
HISTORY_DAYS = 60

# Daily pull back toward the symbol's base level, as a fraction of how far the
# walk has strayed. 0.20 gives a half-life of about three sessions, which holds
# the level inside roughly ±10% of base. That makes these demo stocks more
# range-bound than real ones, which trend for months — a deliberate trade: the
# level has to stay near a price the real instrument trades at, because the
# demo is the fallback provider and its readings get compared against the live
# one's. The day-to-day returns are untouched and keep their fat tail, so the
# replay view still has real variety to show.
MEAN_REVERSION = 0.20
# Largest single-session move the walk may produce, in percent.
MAX_DAILY_MOVE_PCT = 8.0


class _Character:
    __slots__ = ("symbol", "name", "base_price", "sigma", "avg_volume", "sector")

    def __init__(
        self, symbol: str, name: str, base_price: float, sigma: float, avg_volume: float, sector: str
    ) -> None:
        self.symbol = symbol
        self.name = name
        self.base_price = base_price
        self.sigma = sigma
        self.avg_volume = avg_volume
        self.sector = sector


# Price levels, typical daily range and average volume for the six scripted
# symbols are taken from the real `yahoo` observations this repository already
# recorded (backend/marketdiff.db, market_snapshots where source='yahoo').
# Getting these wrong is not cosmetic: the demo is the *fallback* provider, so a
# watchlist routinely holds a yahoo baseline and a demo current reading. When
# the two disagree on the price level, the feed reports the disagreement as a
# price move. A demo RELIANCE at 2890 against a real 1322 is where "+129%" came
# from; WIPRO at 528 against 176.40 is "+222%".
UNIVERSE: dict[str, _Character] = {
    c.symbol: c
    for c in [
        _Character("RELIANCE", "Reliance Industries", 1322.0, 0.95, 10_500_000, "Energy"),
        _Character("INFY", "Infosys", 1130.0, 1.40, 7_950_000, "IT Services"),
        _Character("TCS", "Tata Consultancy Services", 2304.0, 1.65, 2_570_000, "IT Services"),
        _Character("HDFCBANK", "HDFC Bank", 712.1, 0.90, 26_700_000, "Banking"),
        _Character("ITC", "ITC", 264.1, 1.55, 14_100_000, "FMCG"),
        _Character("WIPRO", "Wipro", 176.4, 1.25, 7_940_000, "IT Services"),
        _Character("SBIN", "State Bank of India", 812.0, 1.25, 21_400_000, "Banking"),
        _Character("TATAMOTORS", "Tata Motors", 968.0, 1.75, 26_800_000, "Automobile"),
        _Character("BHARTIARTL", "Bharti Airtel", 1495.0, 1.05, 8_700_000, "Telecom"),
        _Character("AXISBANK", "Axis Bank", 1132.0, 1.20, 12_300_000, "Banking"),
        _Character("LT", "Larsen & Toubro", 3585.0, 1.10, 2_400_000, "Infrastructure"),
        _Character("MARUTI", "Maruti Suzuki", 12480.0, 1.15, 640_000, "Automobile"),
        _Character("SUNPHARMA", "Sun Pharmaceutical", 1742.0, 1.00, 3_900_000, "Pharma"),
        _Character("ASIANPAINT", "Asian Paints", 2895.0, 1.10, 2_100_000, "Consumer"),
        _Character("HINDUNILVR", "Hindustan Unilever", 2412.0, 0.75, 2_800_000, "FMCG"),
        _Character("ADANIPORTS", "Adani Ports & SEZ", 1382.0, 1.95, 9_400_000, "Infrastructure"),
        _Character("TITAN", "Titan Company", 3420.0, 1.20, 1_900_000, "Consumer"),
        _Character("KOTAKBANK", "Kotak Mahindra Bank", 1768.0, 1.00, 6_100_000, "Banking"),
        _Character("NTPC", "NTPC", 358.0, 1.05, 24_700_000, "Power"),
        _Character("ONGC", "Oil & Natural Gas Corp", 271.0, 1.40, 19_600_000, "Energy"),
    ]
}
# The fourteen symbols above that are not in the yahoo sample keep their levels:
# they are already plausible NSE prices and this repository holds no observation
# contradicting them. They are estimates, not measurements — if a future sample
# shows one of them is off by a multiple, it belongs in the list above.

# Situations pinned to the current session so "Demo · All states" is always
# legible.  move_pct: today's total move, volume_mult: multiple of average
# volume, gap_pct: open vs previous close.
#
# These are session inputs, not verdicts — the severity each one ends up with
# is whatever the scoring engine makes of it. Two levers keep that reliable:
# the move sizes sit in the range a real NSE large-cap could plausibly print in
# a session, and the volume multiples are pushed to (or past) the engine's
# saturation point — VOLUME_RATIO_SATURATION in engine/scoring.py — so unusual
# participation contributes its full 25 points independent of how much the
# surrounding week's background movement happens to add or cancel out. Demo
# mode also rewinds the checkpoint close enough (see PrepareRequest in
# api/demo.py) that the scripted day dominates the comparison instead of being
# diluted by several sessions of unrelated background noise.
SCRIPTED_TODAY: dict[str, dict[str, float]] = {
    # 1. Needs attention: a large move, on unusually heavy volume, gapping
    #    open before it — the kind of session that should top the feed.
    "RELIANCE": {"move_pct": 5.4, "volume_mult": 4.5, "gap_pct": 2.2},
    # 2. Meaningful change: a clear adverse move on heavy volume — a guidance
    #    or earnings-reaction day, not a top-of-feed emergency.
    "TCS": {"move_pct": -4.0, "volume_mult": 3.2, "gap_pct": -1.9},
    # 3. Worth watching: a smaller move than the two above, but on unusual
    #    volume, which is what keeps it from reading as background noise.
    "INFY": {"move_pct": 1.95, "volume_mult": 3.0, "gap_pct": 0.7},
    # 4. Normal: an ordinary day, nothing for the feed to say about it.
    "HDFCBANK": {"move_pct": 0.3, "volume_mult": 1.05, "gap_pct": 0.05},
    # 5. Stale feed (see STALE_SYMBOLS): a small move, mostly beside the
    #    point, since what matters here is that the price stops updating.
    "ITC": {"move_pct": 0.9, "volume_mult": 1.2, "gap_pct": 0.2},
}
# WIPRO is deliberately absent from every dict above: see UNAVAILABLE_SYMBOLS.
# It demonstrates "no data at all" rather than "nothing happened" — that case
# is already covered by HDFCBANK.

# This feed has not updated in hours. The app must say so rather than showing
# the last price as if it were live.
STALE_SYMBOLS = {"ITC": 5.5}  # symbol -> hours behind
# ...but "hours behind" alone is not enough to stage the state. Run the demo in
# the evening and `moment - 5.5h` lands after the closing bell, where the
# freshness rules correctly read it as the day's closing print rather than a
# broken feed, and the stale row silently becomes a "recent" one. Clamping the
# frozen reading to this point in the session keeps it unambiguously
# mid-session, so it stays stale whenever the demo is run.
STALE_SESSION_POINT = 0.55

# Two upstream sources disagree on the price by this much (percent).
CONFLICT_SYMBOLS = {"INFY": 0.62}

# This stock's feed is down: the provider returns nothing for it at all, ever
# — no current quote, no historical backfill. It exists in UNIVERSE (so its
# name resolves and it can be added to a watchlist) but never gets a
# MarketSnapshot, so the feed correctly renders it as "unavailable" rather than
# a fabricated price. This mirrors a real outage: some feeds go dark
# completely, as opposed to ITC's feed, which is merely stuck on an old price.
UNAVAILABLE_SYMBOLS = {"WIPRO"}


def _seed(*parts: object) -> random.Random:
    digest = hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def _shock(symbol: str, day: date) -> float:
    """Fat-tailed daily shock in percent, deterministic per (symbol, day)."""
    char = UNIVERSE[symbol]
    rng = _seed(symbol, day.isoformat(), "ret")
    # Normal body...
    value = rng.gauss(0.0, char.sigma)
    # ...with an occasional jump, which is what makes some days worth flagging.
    if rng.random() < 0.06:
        value *= rng.uniform(2.2, 3.6)
    # NSE large caps do not move more than this in a session without a circuit
    # halt, so neither does the demo.
    return round(max(-MAX_DAILY_MOVE_PCT, min(MAX_DAILY_MOVE_PCT, value)), 4)


def _raw_daily_return(symbol: str, day: date, price: float) -> float:
    """The day's return, given where the walk currently stands.

    The shock alone is a free random walk: over sixty sessions it compounds to
    anywhere, which is how a ₹968 stock reached ₹679 and a ₹358 one reached
    ₹445. The reversion term pulls the level back toward the instrument's
    character, so the demo stays inside a range the real stock trades in.
    """
    char = UNIVERSE[symbol]
    drift_pct = (price - char.base_price) / char.base_price * 100.0
    return round(_shock(symbol, day) - MEAN_REVERSION * drift_pct, 4)


# One walk per symbol, extended forwards as the demo asks about later days and
# never recomputed. Every window is a slice of that single path, which is what
# makes two readings of the same stock comparable; building it per window
# instead would be quadratic in the age of EPOCH.
_PATH: dict[str, list[tuple[date, float, float, float]]] = {}
_PATH_LOCK = Lock()


def _path(symbol: str, upto: date) -> list[tuple[date, float, float, float]]:
    """The walk from EPOCH to ``upto``: (day, close, volume, return_pct)."""
    char = UNIVERSE[symbol]
    with _PATH_LOCK:
        rows = _PATH.setdefault(symbol, [])
        if rows and rows[-1][0] >= upto:
            return rows

        price = rows[-1][1] if rows else char.base_price
        cursor = rows[-1][0] + timedelta(days=1) if rows else EPOCH
        while cursor <= upto:
            if is_trading_day(cursor):
                ret = _raw_daily_return(symbol, cursor, price)
                price = price * (1.0 + ret / 100.0)
                rng = _seed(symbol, cursor.isoformat(), "vol")
                # Volume rises with the size of the move — real markets behave
                # this way, and it means the volume signal is not independent
                # noise.
                excitement = 1.0 + min(abs(ret) / max(char.sigma, 0.3), 3.0) * 0.35
                volume = char.avg_volume * excitement * rng.uniform(0.72, 1.28)
                rows.append((cursor, price, round(volume), ret))
            cursor += timedelta(days=1)
        return rows


def _series(symbol: str, upto: date) -> tuple[tuple[date, float, float, float], ...]:
    """(day, close, volume, return_pct) for the trailing window, inclusive."""
    rows = _path(symbol, upto)
    end = len(rows)
    while end and rows[end - 1][0] > upto:
        end -= 1
    window = rows[max(end - HISTORY_DAYS, 0) : end]
    return tuple((day, round(price, 2), volume, ret) for day, price, volume, ret in window)


def _session_moment(day: date, fraction: float) -> datetime:
    """The instant ``fraction`` of the way through ``day``'s trading session."""
    open_dt = datetime.combine(day, MARKET_OPEN, tzinfo=IST)
    close_dt = datetime.combine(day, MARKET_CLOSE, tzinfo=IST)
    return (open_dt + (close_dt - open_dt) * fraction).astimezone(timezone.utc)


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


class DemoMarketDataProvider(MarketDataProvider):
    name = "demo"
    is_demo = True

    async def get_quotes(
        self, symbols: list[str], at: datetime | None = None
    ) -> dict[str, Quote]:
        moment = at or datetime.now(timezone.utc)
        return {
            s: self.quote(s, moment)
            for s in symbols
            if s.upper() in UNIVERSE and s.upper() not in UNAVAILABLE_SYMBOLS
        }

    def quote(self, symbol: str, moment: datetime) -> Quote:
        symbol = symbol.upper()
        char = UNIVERSE[symbol]
        session = last_trading_day(moment)
        series = _series(symbol, session)
        history = series[:-1]
        today = series[-1]

        prev_close = history[-1][1] if history else char.base_price
        day_return = today[3]
        day_volume = today[2]
        gap_pct = day_return * 0.35

        # The scripted situations belong to the *current* session only. History
        # replayed by the backfill must come from the unscripted series, or every
        # past day would carry today's staged 4.2% move.
        is_live_session = session == last_trading_day(datetime.now(timezone.utc))
        script = SCRIPTED_TODAY.get(symbol) if is_live_session else None
        if script:
            day_return = script["move_pct"]
            day_volume = char.avg_volume * script["volume_mult"]
            gap_pct = script["gap_pct"]

        fraction = session_fraction(moment)
        # Intraday path: gap at the open, then ease towards the day's close.
        eased = fraction * fraction * (3 - 2 * fraction)  # smoothstep
        realised = gap_pct + (day_return - gap_pct) * eased
        price = prev_close * (1.0 + realised / 100.0)
        volume = day_volume * max(fraction, 0.05)

        returns = [r for _, _, _, r in history]
        volumes = [v for _, _, v, _ in history]
        sigma = _stdev(returns[-20:]) if len(returns) >= 3 else char.sigma
        avg_volume = sum(volumes[-20:]) / len(volumes[-20:]) if volumes else char.avg_volume
        recent_vol = _stdev(returns[-5:]) if len(returns) >= 5 else None
        baseline_vol = _stdev(returns[-30:]) if len(returns) >= 10 else None

        observed_at = moment
        stale_hours = STALE_SYMBOLS.get(symbol)
        if stale_hours and is_live_session:
            # The feed stopped updating: freeze both the price and the timestamp.
            observed_at = min(
                moment - timedelta(hours=stale_hours),
                _session_moment(session, STALE_SESSION_POINT),
            )
            frozen = session_fraction(observed_at)
            eased_frozen = frozen * frozen * (3 - 2 * frozen)
            realised = gap_pct + (day_return - gap_pct) * eased_frozen
            price = prev_close * (1.0 + realised / 100.0)
            volume = day_volume * max(frozen, 0.05)

        high = max(price, prev_close * (1 + max(realised, gap_pct) / 100.0))
        low = min(price, prev_close * (1 + min(realised, gap_pct) / 100.0))

        return Quote(
            symbol=symbol,
            price=round(price, 2),
            observed_at=observed_at,
            source=self.name,
            previous_close=round(prev_close, 2),
            day_open=round(prev_close * (1 + gap_pct / 100.0), 2),
            day_high=round(high, 2),
            day_low=round(low, 2),
            volume=round(volume),
            average_volume=round(avg_volume),
            sigma_pct=round(sigma, 4) or None,
            recent_volatility_pct=round(recent_vol, 4) if recent_vol else None,
            baseline_volatility_pct=round(baseline_vol, 4) if baseline_vol else None,
            history_points=len(history),
            source_disagreement_pct=CONFLICT_SYMBOLS.get(symbol) if is_live_session else None,
            name=char.name,
            is_demo=True,
        )

    async def search(self, query: str, limit: int = 10) -> list[SearchHit]:
        q = query.strip().upper()
        if not q:
            return []
        hits = [
            SearchHit(symbol=c.symbol, name=c.name)
            for c in UNIVERSE.values()
            if q in c.symbol or q in c.name.upper() or q in c.sector.upper()
        ]
        hits.sort(key=lambda h: (not h.symbol.startswith(q), h.symbol))
        return hits[:limit]
