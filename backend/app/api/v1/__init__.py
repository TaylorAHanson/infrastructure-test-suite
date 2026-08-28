"""API v1 router aggregation."""
from fastapi import APIRouter

from app.api.v1 import (
    admin,
    assistant,
    checks,
    findings,
    health,
    runs,
    suites,
    targets,
    tests,
)

router = APIRouter()
router.include_router(health.router, tags=["health"])
router.include_router(checks.router, prefix="/checks", tags=["checks"])
router.include_router(tests.router, prefix="/tests", tags=["tests"])
router.include_router(suites.router, prefix="/suites", tags=["suites"])
router.include_router(targets.router, prefix="/targets", tags=["targets"])
router.include_router(runs.router, prefix="/runs", tags=["runs"])
router.include_router(findings.router, prefix="/findings", tags=["findings"])
router.include_router(assistant.router, prefix="/assistant", tags=["assistant"])
router.include_router(admin.router, prefix="/admin", tags=["admin"])
