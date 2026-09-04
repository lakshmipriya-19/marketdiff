from __future__ import annotations

import re

from fastapi import Depends, Header
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import BadRequest, NotFound
from ..models import User, UserPreference, Watchlist

USER_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def get_user(
    db: Session = Depends(get_db),
    x_user_key: str | None = Header(default=None, alias="X-User-Key"),
) -> User:
    """Identity is an opaque browser-generated key. No PII is collected.

    Sufficient for a personal watchlist and nothing more: there is no account to
    take over, no password to leak, and no email to lose.
    """
    if not x_user_key or not USER_KEY_RE.match(x_user_key):
        raise BadRequest("Missing or malformed X-User-Key header", code="invalid_user_key")

    user = db.query(User).filter(User.user_key == x_user_key).one_or_none()
    if user is None:
        user = User(user_key=x_user_key)
        db.add(user)
        try:
            db.commit()
        except IntegrityError:  # created by a concurrent request
            db.rollback()
            user = db.query(User).filter(User.user_key == x_user_key).one()
    return user


def get_preferences(db: Session, user: User) -> UserPreference:
    prefs = db.get(UserPreference, user.id)
    if prefs is None:
        prefs = UserPreference(user_id=user.id)
        db.add(prefs)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            prefs = db.get(UserPreference, user.id)
    return prefs


def owned_watchlist(db: Session, user: User, watchlist_id: int) -> Watchlist:
    """Never trust a client-supplied id: scope every lookup by owner.

    A watchlist belonging to someone else returns 404, not 403, so the API does
    not confirm that an id exists to someone who cannot see it.
    """
    watchlist = (
        db.query(Watchlist)
        .filter(Watchlist.id == watchlist_id, Watchlist.user_id == user.id)
        .one_or_none()
    )
    if watchlist is None:
        raise NotFound("That watchlist does not exist", code="watchlist_not_found")
    return watchlist
