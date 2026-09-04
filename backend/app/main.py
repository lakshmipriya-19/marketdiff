from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import demo, meta, stocks, watchlists
from .config import get_settings
from .db import init_db
from .errors import install_error_handlers
from .providers.registry import get_router

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    settings = get_settings()
    if settings.seed_on_start:
        from .seed import seed_reference_data

        seed_reference_data()
    yield
    await get_router().aclose()


app = FastAPI(
    title="MarketDiff API",
    version="1.0.0",
    description="What meaningfully changed since you last looked.",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-User-Key"],
)

install_error_handlers(app)
app.include_router(meta.router)
app.include_router(watchlists.router)
app.include_router(stocks.router)
app.include_router(demo.router)
