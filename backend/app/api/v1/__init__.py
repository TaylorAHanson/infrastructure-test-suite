"""API v1 router aggregation."""
from fastapi import APIRouter

from app.api.v1 import (
    admin,
    agents,
    analytics,
    audit,
    authoring,
    health,
    runs,
    schedules,
    suites,
    targets,
    tests,
)

router = APIRouter()
router.include_router(health.router, tags=["health"])
router.include_router(tests.router, prefix="/tests", tags=["tests"])
router.include_router(suites.router, prefix="/suites", tags=["suites"])
router.include_router(targets.router, prefix="/targets", tags=["targets"])
router.include_router(runs.router, prefix="/runs", tags=["runs"])
router.include_router(schedules.router, prefix="/schedules", tags=["schedules"])
router.include_router(audit.router, prefix="/audit", tags=["audit"])
router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
router.include_router(agents.router, prefix="/agents", tags=["agents"])
router.include_router(admin.router, prefix="/admin", tags=["admin"])
router.include_router(authoring.router, prefix="/authoring", tags=["authoring"])
