from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import User
from ..providers.registry import get_router
from ..schemas import PreferencesOut, PreferencesUpdate
from .deps import get_preferences, get_user

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    settings = get_settings()
    provider = get_router()
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # pragma: no cover - surfaced to the client, not raised
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "unavailable",
        "provider": {
            "configured": settings.provider,
            "mode": provider.mode,
            "circuitOpen": provider.circuit_open,
        },
    }


@router.get("/preferences", response_model=PreferencesOut)
def read_preferences(db: Session = Depends(get_db), user: User = Depends(get_user)):
    prefs = get_preferences(db, user)
    return PreferencesOut(simpleMode=prefs.simple_mode, attentionThreshold=prefs.attention_threshold)


@router.patch("/preferences", response_model=PreferencesOut)
def update_preferences(
    payload: PreferencesUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_user),
):
    prefs = get_preferences(db, user)
    if payload.simple_mode is not None:
        prefs.simple_mode = payload.simple_mode
    if payload.attention_threshold is not None:
        prefs.attention_threshold = payload.attention_threshold
    db.commit()
    return PreferencesOut(simpleMode=prefs.simple_mode, attentionThreshold=prefs.attention_threshold)
