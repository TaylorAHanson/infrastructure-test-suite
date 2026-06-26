"""Analytics / failure-analytics endpoints backing the dashboard."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.db.session import get_session
from app.services import analytics

router = APIRouter()


@router.get("/overview")
def overview(session: Session = Depends(get_session), days: int = 30) -> dict:
    return analytics.overview(session, days)


@router.get("/failure-rate-over-time")
def failure_rate_over_time(session: Session = Depends(get_session), days: int = 30) -> list[dict]:
    return analytics.failure_rate_over_time(session, days)


@router.get("/top-failing")
def top_failing(session: Session = Depends(get_session),
                dimension: str = Query("test", pattern="^(test|target)$"),
                limit: int = 10, days: int = 30) -> list[dict]:
    return analytics.top_failing(session, dimension, limit, days)


@router.get("/pass-rate-by")
def pass_rate_by(session: Session = Depends(get_session),
                 dimension: str = Query("category", pattern="^(category|target|suite|test)$"),
                 days: int = 30) -> list[dict]:
    return analytics.pass_rate_by(session, dimension, days)


@router.get("/connectivity-vs-functional")
def connectivity_vs_functional(session: Session = Depends(get_session), days: int = 30) -> dict:
    return analytics.connectivity_vs_functional(session, days)


@router.get("/root-cause-buckets")
def root_cause_buckets(session: Session = Depends(get_session), days: int = 30) -> list[dict]:
    return analytics.root_cause_buckets(session, days)


@router.get("/duration-by-test")
def duration_by_test(session: Session = Depends(get_session), days: int = 30) -> list[dict]:
    return analytics.duration_by_test(session, days)


@router.get("/flaky-tests")
def flaky_tests(session: Session = Depends(get_session), days: int = 30) -> list[dict]:
    return analytics.flaky_tests(session, days)


@router.get("/last-markers")
def last_markers(session: Session = Depends(get_session)) -> list[dict]:
    return analytics.last_markers(session)


@router.get("/regression-after-version")
def regression_after_version(version: str, session: Session = Depends(get_session)) -> list[dict]:
    return analytics.regression_after_version(session, version)


@router.get("/privatelink-failures")
def privatelink_failures(session: Session = Depends(get_session), days: int = 30) -> dict:
    return analytics.privatelink_failures(session, days)
