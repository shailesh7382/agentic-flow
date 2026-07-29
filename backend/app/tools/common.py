import json
import logging
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any, TypeVar

from langchain_core.tools import ToolException

from ..logging_config import log_event

logger = logging.getLogger("agentic_flow.tools")
T = TypeVar("T")


class DiagnosticToolError(ToolException):
    """Raised when an operational failure should be returned to the diagnostic agent."""


class ToolAccessError(DiagnosticToolError):
    """Raised when a diagnostic tool request violates an access boundary."""


def json_result(**values: Any) -> str:
    return json.dumps(values, ensure_ascii=False, default=str)


def tool_error_result(error: ToolException) -> str:
    """Return recoverable tool failures to the agent as structured observations."""
    return json_result(
        ok=False,
        error_type=type(error).__name__,
        error=str(error),
    )


async def observed_tool_call(
    name: str,
    arguments: dict[str, Any],
    function: Callable[[], Awaitable[T]],
    *,
    include_content: bool = True,
) -> T:
    started = perf_counter()
    log_event(
        logger,
        logging.INFO,
        "diagnostic.tool.started",
        f"Diagnostic tool '{name}' started",
        tool=name,
        arguments=arguments if include_content else {"keys": sorted(arguments)},
    )
    try:
        result = await function()
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "diagnostic.tool.failed",
            f"Diagnostic tool '{name}' failed",
            exc_info=True,
            tool=name,
            arguments=arguments if include_content else {"keys": sorted(arguments)},
            error_type=type(exc).__name__,
            duration_ms=round((perf_counter() - started) * 1000, 2),
        )
        if isinstance(exc, ToolException):
            raise
        raise DiagnosticToolError(f"{type(exc).__name__}: {exc}") from exc
    log_event(
        logger,
        logging.INFO,
        "diagnostic.tool.completed",
        f"Diagnostic tool '{name}' completed",
        tool=name,
        arguments=arguments if include_content else {"keys": sorted(arguments)},
        duration_ms=round((perf_counter() - started) * 1000, 2),
        result=result if include_content else None,
        result_chars=len(result) if isinstance(result, str) else None,
    )
    return result
