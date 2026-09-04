"""Session clock for NSE trading hours.

Small but load-bearing: cumulative intraday volume is meaningless unless you
know how much of the session has elapsed. Comparing 10:00 AM volume against a
full-day average would make every morning look like a ghost town, and would make
the volume signal fire only after lunch.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)

# Never divide by a fraction smaller than this: in the first minutes of trading
# the ratio is too noisy to be evidence of anything.
MIN_SESSION_FRACTION = 0.12


def is_trading_day(day: date) -> bool:
    """Weekday check. Exchange holidays are not modelled — see README trade-offs."""
    return day.weekday() < 5


def last_trading_day(moment: datetime) -> date:
    day = moment.astimezone(IST).date()
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day


def session_fraction(moment: datetime) -> float:
    """How much of the trading session had elapsed at ``moment`` (0..1)."""
    local = moment.astimezone(IST)
    if not is_trading_day(local.date()):
        return 1.0
    open_dt = datetime.combine(local.date(), MARKET_OPEN, tzinfo=IST)
    close_dt = datetime.combine(local.date(), MARKET_CLOSE, tzinfo=IST)
    if local <= open_dt:
        return 0.0
    if local >= close_dt:
        return 1.0
    return (local - open_dt).total_seconds() / (close_dt - open_dt).total_seconds()


def market_is_open(moment: datetime) -> bool:
    local = moment.astimezone(IST)
    return is_trading_day(local.date()) and MARKET_OPEN <= local.time() <= MARKET_CLOSE


def expected_volume(average_volume: float | None, moment: datetime) -> float | None:
    """The volume a typical session would have printed by ``moment``."""
    if not average_volume or average_volume <= 0:
        return None
    return average_volume * max(session_fraction(moment), MIN_SESSION_FRACTION)


def session_equivalents(start: datetime, end: datetime) -> float:
    """Trading time between two instants, measured in whole sessions.

    Used to scale a stock's normal daily range when the user's last checkpoint
    is days old: a three-session move should be compared against a three-session
    yardstick, not a one-day one. Overnight and weekend hours contribute nothing,
    which is why wall-clock elapsed time is the wrong unit here.
    """
    if end <= start:
        return 0.0
    start_local = start.astimezone(IST)
    end_local = end.astimezone(IST)
    total = 0.0
    day = start_local.date()
    while day <= end_local.date():
        if is_trading_day(day):
            day_open = datetime.combine(day, MARKET_OPEN, tzinfo=IST)
            day_close = datetime.combine(day, MARKET_CLOSE, tzinfo=IST)
            lo = max(start_local, day_open)
            hi = min(end_local, day_close)
            if hi > lo:
                total += (hi - lo).total_seconds() / (day_close - day_open).total_seconds()
        day += timedelta(days=1)
    return round(total, 4)


def last_session_close(moment: datetime) -> datetime:
    """The most recent session close at or before ``moment`` (UTC)."""
    local = moment.astimezone(IST)
    day = local.date()
    while True:
        if is_trading_day(day):
            close_dt = datetime.combine(day, MARKET_CLOSE, tzinfo=IST)
            if close_dt <= local:
                return close_dt.astimezone(timezone.utc)
        day -= timedelta(days=1)
