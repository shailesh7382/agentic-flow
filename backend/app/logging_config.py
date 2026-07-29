import json
import logging
import traceback
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .config import Settings

request_id_context: ContextVar[str] = ContextVar("request_id", default="-")
run_id_context: ContextVar[str] = ContextVar("run_id", default="-")
node_context: ContextVar[str] = ContextVar("agent_node", default="-")

_configured_path: Path | None = None


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_context.get()
        record.run_id = run_id_context.get()
        record.agent_node = node_context.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", "log.message"),
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "run_id": getattr(record, "run_id", "-"),
            "agent_node": getattr(record, "agent_node", "-"),
            "process_id": record.process,
            "thread_name": record.threadName,
            "source": {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            },
        }
        details = getattr(record, "details", None)
        if details:
            payload["details"] = details
        if record.exc_info:
            exception_type = record.exc_info[0]
            exception_value = record.exc_info[1]
            payload["exception"] = {
                "type": exception_type.__name__ if exception_type else "Exception",
                "message": str(exception_value),
                "stack_trace": "".join(traceback.format_exception(*record.exc_info)),
            }
        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", "log.message")
        request_id = getattr(record, "request_id", "-")
        run_id = getattr(record, "run_id", "-")
        return (
            f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<8} "
            f"{event} request_id={request_id} run_id={run_id} — {record.getMessage()}"
        )


def configure_logging(settings: Settings) -> Path:
    global _configured_path

    log_path = settings.log_path.resolve()
    if _configured_path == log_path:
        return log_path

    log_path.parent.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, settings.log_level.upper(), logging.DEBUG)
    correlation_filter = CorrelationFilter()

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(JsonFormatter())
    file_handler.addFilter(correlation_filter)
    file_handler._agentic_flow_handler = True

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(ConsoleFormatter())
    console_handler.addFilter(correlation_filter)
    console_handler._agentic_flow_handler = True

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_agentic_flow_handler", False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_logger.handlers = [file_handler, console_handler]
    uvicorn_logger.setLevel(level)
    uvicorn_logger.propagate = False
    for name in ("uvicorn.error", "uvicorn.access"):
        child = logging.getLogger(name)
        child.handlers.clear()
        child.propagate = True

    # These libraries may expose authorization headers or request bodies at DEBUG.
    # First-party instrumentation records the useful timing/status metadata safely.
    for name in ("httpcore", "httpx", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)

    _configured_path = log_path
    log_event(
        logging.getLogger("agentic_flow.logging"),
        logging.INFO,
        "logging.configured",
        "Structured rotating file logging configured",
        log_file=str(log_path),
        log_level=logging.getLevelName(level),
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
        include_content=settings.log_include_content,
    )
    return log_path


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    message: str,
    *,
    exc_info: bool = False,
    **details: Any,
) -> None:
    logger.log(
        level,
        message,
        extra={"event": event, "details": details},
        exc_info=exc_info,
    )


def set_request_id(value: str) -> Token[str]:
    return request_id_context.set(value)


def get_request_id() -> str:
    return request_id_context.get()


def set_run_id(value: str) -> Token[str]:
    return run_id_context.set(value)


def set_node(value: str) -> Token[str]:
    return node_context.set(value)


def reset_request_id(token: Token[str]) -> None:
    request_id_context.reset(token)


def reset_run_id(token: Token[str]) -> None:
    run_id_context.reset(token)


def reset_node(token: Token[str]) -> None:
    node_context.reset(token)
