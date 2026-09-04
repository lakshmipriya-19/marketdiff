from __future__ import annotations

import os
import tempfile

os.environ.setdefault("MARKETDIFF_PROVIDER", "demo")
os.environ.setdefault("MARKETDIFF_SEED", "0")
_TMP = tempfile.mkdtemp(prefix="marketdiff-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Stock  # noqa: E402
from app.providers.demo import UNIVERSE  # noqa: E402
from app.providers.registry import set_router  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    set_router(None)
    yield
    set_router(None)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def stock(db):
    row = Stock(symbol="RELIANCE", name=UNIVERSE["RELIANCE"].name)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def headers():
    return {"X-User-Key": "test-user-key-000001"}
