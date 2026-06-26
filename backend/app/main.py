"""FastAPI application: REST API + served React SPA.

A single long-running FastAPI process serves both the API and the built React
bundle (from ``backend/static``). Node is used only at build time. The process
binds 0.0.0.0 and the Databricks-provided port (see ``run.py``).
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import router as api_router
from app.core.config import settings
from app.core.logging import setup_logging

setup_logging(settings.LOG_LEVEL)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _start_poller_thread() -> None:
    import asyncio

    from app.workers.poller import start_poller

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(start_poller())
        except Exception as exc:  # noqa: BLE001
            logger.error("Poller thread crashed: %s", exc, exc_info=True)
        finally:
            loop.close()

    threading.Thread(target=_run, name="poller", daemon=True).start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s (env=%s)", settings.PROJECT_NAME, settings.APP_VERSION, settings.ENVIRONMENT)

    # Ensure schema exists (Alembic owns prod migrations; this is a safety net).
    try:
        from app.db.session import create_all

        create_all()
    except Exception as exc:  # noqa: BLE001
        logger.error("DB init failed: %s", exc, exc_info=True)

    # Seed the test catalog. Demo data (placeholder targets + synthetic runs) is
    # gated behind SEED_DEMO_DATA so a real workspace gets only the real catalog.
    if settings.SEED_ON_STARTUP:
        try:
            from app.db.session import session_scope
            from app.services.seed import seed_all

            with session_scope() as session:
                seed_all(
                    session,
                    with_sample_runs=settings.SEED_DEMO_DATA,
                    with_demo_targets=settings.SEED_DEMO_DATA,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Seeding skipped: %s", exc)

    # Background poller (skipped under TESTING to keep tests deterministic).
    if settings.POLLER_ENABLED and not os.environ.get("TESTING"):
        _start_poller_thread()

    yield
    logger.info("Shutting down.")


app = FastAPI(title="Databricks Regression Validation Platform", version=settings.APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",")] if settings.CORS_ORIGINS else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.telemetry.otel import setup_telemetry  # noqa: E402

setup_telemetry(app)

app.include_router(api_router, prefix=settings.API_V1_PREFIX)


# --------------------------------------------------------------------------
# Serve the built React SPA (production) with client-side routing fallback.
# --------------------------------------------------------------------------
if STATIC_DIR.exists():
    assets = STATIC_DIR / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        index = STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(index)
        return JSONResponse({"detail": "frontend not built"}, status_code=404)
else:
    @app.get("/")
    async def root():
        return {
            "message": "Databricks Regression Validation Platform API",
            "docs": "/docs",
            "frontend": "run `npm run build` to generate backend/static, or `./dev.sh` for dev",
        }
