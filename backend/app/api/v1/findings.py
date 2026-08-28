"""The findings feed API (#33, ADR-0009) — the "What's Interesting" read path.

``GET /findings`` returns a ranked feed of **findings, not raw runs**, split into
the **Recent-deployments** and **Overnight** lanes, with non-actionable
(nominal/recovered) series collapsed into each lane's **"Everything else"**
count. This is a strictly read-only view over retained history; it never mutates
a run. Provenance threads + flaky advisory ride on top of it (#34); the frontend
renders it (#35).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.db.session import get_session
from app.schemas.schemas import FindingsFeedOut
from app.services import findings as findings_service

router = APIRouter()


@router.get("", response_model=FindingsFeedOut)
def get_findings(
    session: Session = Depends(get_session),
    window_hours: Optional[int] = Query(
        default=None, ge=1,
        description="Override the Recent-deployments rolling window (hours); "
                    "defaults to the configured FINDINGS_RECENT_WINDOW_HOURS."),
    limit: int = Query(default=200, ge=1, le=1000,
                       description="Max findings returned per lane, newest-first."),
) -> dict:
    return findings_service.build_feed(session, window_hours=window_hours, limit=limit)
