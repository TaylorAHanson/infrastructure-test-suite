"""Handler framework: metadata-driven, spine-based, plugin handlers."""
from app.executors.base import (  # noqa: F401
    Handler,
    HandlerContext,
    HandlerError,
    StageResult,
)
from app.executors.registry import (  # noqa: F401
    OperationBlocked,
    get_handler,
    is_operation_allowed,
    register,
    registry,
    validate_operation,
)

# Import built-ins for their registration side effects.
from app.executors import builtin  # noqa: F401,E402

__all__ = [
    "Handler",
    "HandlerContext",
    "HandlerError",
    "StageResult",
    "register",
    "get_handler",
    "registry",
    "validate_operation",
    "is_operation_allowed",
    "OperationBlocked",
]
