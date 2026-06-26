"""ID and timestamp helpers. UTC everywhere, by policy."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone


def utcnow() -> datetime:
    """Naive UTC timestamp.

    We deliberately use *naive* UTC (no tzinfo) so timestamps are consistent
    across SQLite (local) and Lakebase/Postgres, where the columns are not
    declared ``timezone=True``. Everything in the platform is UTC by policy;
    naive-UTC simply avoids aware/naive subtraction errors after a round-trip
    through SQLite.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"
