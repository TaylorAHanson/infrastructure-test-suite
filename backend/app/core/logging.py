"""Structured JSON logging to stdout/stderr.

Databricks Apps surface stdout/stderr in the Apps UI (and at ``/logz``), but
those logs are **not durable** after app compute shuts down. We therefore log
in structured JSON for live debugging while persisting the durable audit trail
in Lakebase/Unity Catalog (see :mod:`app.services.audit`).
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("run_id", "test_id", "step_kind", "root_cause", "actor"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Quiet noisy libraries.
    for noisy in ("uvicorn.access", "httpx", "databricks.sdk"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
