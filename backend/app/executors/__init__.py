"""Executor framework: metadata-driven, plugin-based probes."""
from app.executors.base import (  # noqa: F401
    Executor,
    ProbeContext,
    ProbeError,
    StepResult,
)
from app.executors.registry import get_executor, register, registry  # noqa: F401

# Import built-ins for their registration side effects.
from app.executors import builtin  # noqa: F401,E402

__all__ = [
    "Executor",
    "ProbeContext",
    "ProbeError",
    "StepResult",
    "register",
    "get_executor",
    "registry",
]
