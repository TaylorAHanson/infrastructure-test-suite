"""Metadata-driven executor registry.

Executors register themselves by ``executor_key``. A test definition names its
``executor_key``; the run engine resolves the plugin at execution time. Adding
a new test is therefore: (1) add a definition row (config), and optionally
(2) register one small executor class - without editing unrelated files.
"""
from __future__ import annotations

import logging
from typing import Type

from app.executors.base import Executor

logger = logging.getLogger(__name__)

registry: dict[str, Type[Executor]] = {}


def register(executor_key: str):
    """Class decorator registering an executor under ``executor_key``."""

    def _wrap(cls: Type[Executor]) -> Type[Executor]:
        cls.executor_key = executor_key
        if executor_key in registry:
            logger.warning("Executor key '%s' already registered; overriding", executor_key)
        registry[executor_key] = cls
        return cls

    return _wrap


def get_executor(executor_key: str) -> Executor:
    """Instantiate the executor for ``executor_key``.

    Falls back to the capability-gated stub so an unknown/unsupported key
    yields a clean SKIP rather than crashing a run.
    """
    cls = registry.get(executor_key)
    if cls is None:
        from app.executors.builtin.stubs import CapabilityGatedStub

        logger.info("No executor registered for '%s'; using capability-gated stub", executor_key)
        return CapabilityGatedStub()
    return cls()
