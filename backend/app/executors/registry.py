"""Handler registry + open-SDK operation validation (ADR-0017).

Handlers register by ``handler_key``; a Check names the key of the handler that
performs its operation on the spine. There is **no operation allowlist**: under
open-SDK authoring a Check may express any Databricks SDK/API call, and the only
gate at author/registration time is the **blocklist** — the global R3-never
floor (:func:`validate_operation`). The ground truth for what may be expressed
is therefore "the SDK/OpenAPI surface minus the blocklist", not a curated
manifest.
"""
from __future__ import annotations

import logging
from typing import Optional, Type

from app.core.operations import Operation, is_blocklisted
from app.executors.base import Handler

logger = logging.getLogger(__name__)

registry: dict[str, Type[Handler]] = {}


def register(handler_key: str):
    """Class decorator registering a handler under ``handler_key``."""

    def _wrap(cls: Type[Handler]) -> Type[Handler]:
        cls.handler_key = handler_key
        if handler_key in registry:
            logger.warning("Handler key '%s' already registered; overriding", handler_key)
        registry[handler_key] = cls
        return cls

    return _wrap


def get_handler(handler_key: str) -> Handler:
    """Instantiate the handler for ``handler_key`` (falls back to a clean stub)."""
    cls = registry.get(handler_key)
    if cls is None:
        from app.executors.builtin.generic import UnsupportedHandler

        logger.info("No handler registered for '%s'; using unsupported stub", handler_key)
        return UnsupportedHandler()
    return cls()


class OperationBlocked(ValueError):
    """Raised when an operation is refused by the blocklist (R3-never floor)."""


def validate_operation(operation) -> None:
    """Refuse a blocklisted operation. Open SDK otherwise — no allowlist.

    Raises :class:`OperationBlocked` for anything on the blocklist; returns
    ``None`` for every other operation (the SDK surface minus the blocklist).
    """
    op = operation if isinstance(operation, Operation) else Operation.from_dict(operation)
    blocked, reason = is_blocklisted(op)
    if blocked:
        raise OperationBlocked(f"Operation '{op.qualified}' is blocklisted: {reason}")


def is_operation_allowed(operation) -> tuple[bool, Optional[str]]:
    """Non-raising variant of :func:`validate_operation`."""
    try:
        validate_operation(operation)
        return True, None
    except OperationBlocked as exc:
        return False, str(exc)
