"""Driver Compliance Watch — FastAPI application entry point.

Registers all routers and manages async lifecycle hooks for:
  - PostgreSQL (SQLAlchemy async engine)
  - Redis (connection pool + pub/sub subscriber)
  - Compliance alert subscriber (background task)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.database import close_db, init_db
from app.core.ops_log import configure_ops_log
from app.core.redis import close_redis, init_redis
from app.domains.dashboard.driver_names import warm_driver_name_cache
from app.domains.dashboard.router import router as dashboard_router
from app.domains.dashboard.ui import ui_router
from app.domains.ingestion.adapters.geotab import GeotabAdapter
from app.domains.ingestion.fleets import sync_fleets_to_db
from app.domains.notifier.subscriber import run_subscriber_loop

logger = logging.getLogger("dcw.main")

_DASHBOARD_STATIC = Path(__file__).resolve().parent / "domains" / "dashboard" / "static"

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description=(
        "Driver Compliance Watch — Deterministic 49 CFR Part 395 HOS "
        "compliance platform. Real-time telematics ingestion, rule-pack "
        "evaluation, and automated Twilio alerting."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ─────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.DEBUG else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static + Routers ──────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=str(_DASHBOARD_STATIC)), name="static")
app.include_router(dashboard_router)
app.include_router(ui_router)

# ── Lifecycle ─────────────────────────────────────────────────────────────

_subscriber_task: asyncio.Task | None = None


@app.on_event("startup")
async def on_startup() -> None:
    """Initialize database, Redis, and start the alert subscriber."""
    global _subscriber_task

    configure_ops_log(process_name="api")
    logger.info("Starting DCW application (environment=%s)", settings.ENVIRONMENT)

    await init_db()
    logger.info("PostgreSQL connection pool ready")

    await sync_fleets_to_db()

    await init_redis()
    logger.info("Redis connection pool ready")

    # Warm driver display names (Redis → optional Geotab User cold-start).
    geotab_api = None
    if settings.GEOTAB_DATABASE and settings.GEOTAB_USERNAME and settings.GEOTAB_PASSWORD:
        try:
            adapter = GeotabAdapter()
            await adapter.connect()
            geotab_api = adapter.api
        except Exception as exc:
            logger.warning("Geotab unavailable for driver-name warm: %s", exc)
    try:
        named = await warm_driver_name_cache(geotab_api=geotab_api)
        logger.info("Driver name cache ready (%d names)", named)
    except Exception as exc:
        logger.warning("Driver name cache warm failed: %s", exc)

    # Start the compliance alert subscriber as a background task
    _subscriber_task = asyncio.create_task(run_subscriber_loop())
    logger.info("Compliance alert subscriber started")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    """Graceful shutdown — cancel subscriber and close connections."""
    global _subscriber_task

    logger.info("Shutting down DCW application…")

    if _subscriber_task and not _subscriber_task.done():
        _subscriber_task.cancel()
        try:
            await _subscriber_task
        except asyncio.CancelledError:
            pass

    await close_redis()
    await close_db()
    logger.info("DCW application shutdown complete")


# ── Root + legacy health ─────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """Send bare app URL to the HTMX dashboard home."""
    return RedirectResponse(url="/ui/home", status_code=302)


@app.get("/health", tags=["system"])
async def health_check_root() -> dict:
    """Minimal root health check (see /api/health for extended check)."""
    return {"status": "healthy", "environment": settings.ENVIRONMENT}
