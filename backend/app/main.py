import json
import logging
import platform
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .config import get_settings
from .diagnostics import DiagnosticAgent
from .graph import AgentWorkflow
from .llm import LMStudioClient, LMStudioError
from .logging_config import (
    configure_logging,
    get_request_id,
    log_event,
    reset_request_id,
    reset_run_id,
    set_request_id,
    set_run_id,
)
from .models import HealthResponse, RunRequest, TaskTemplate, ToolStatus
from .templates import TASK_TEMPLATES, TASKS_BY_ID
from .tools import DiagnosticToolRegistry

logger = logging.getLogger("agentic_flow.api")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_app(workflow: AgentWorkflow | None = None) -> FastAPI:
    settings = get_settings()
    log_path = configure_logging(settings)
    llm = LMStudioClient(settings)
    tool_registry = DiagnosticToolRegistry(settings)
    diagnostic_agent = DiagnosticAgent(settings, llm, tool_registry)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        started = perf_counter()
        app.state.workflow = workflow or AgentWorkflow(
            llm, settings, diagnostic_runner=diagnostic_agent
        )
        app.state.llm = llm
        app.state.tool_registry = tool_registry
        log_event(
            logger,
            logging.INFO,
            "application.started",
            "Local Agent Studio API started",
            log_file=str(log_path),
            lmstudio_base_url=settings.lmstudio_base_url,
            configured_model=settings.lmstudio_model or None,
            cors_origins=settings.allowed_origins,
            log_include_content=settings.log_include_content,
            diagnostic_tools=[
                status.model_dump() for status in tool_registry.statuses
            ],
            runtime={
                "python": platform.python_version(),
                "platform": platform.platform(),
                "packages": {
                    package: version(package)
                    for package in (
                        "asyncssh",
                        "fastapi",
                        "langchain",
                        "langchain-openai",
                        "langgraph",
                        "openai",
                        "oracledb",
                        "psutil",
                        "pydantic",
                        "sqlglot",
                        "uvicorn",
                    )
                },
            },
        )
        try:
            yield
        finally:
            log_event(
                logger,
                logging.INFO,
                "application.stopping",
                "Local Agent Studio API is stopping",
                uptime_ms=round((perf_counter() - started) * 1000, 2),
            )
            await llm.client.close()
            log_event(
                logger,
                logging.INFO,
                "application.stopped",
                "Local Agent Studio API stopped cleanly",
            )

    app = FastAPI(
        title="Local Agent Studio API",
        version="0.1.0",
        description="A streamed LangGraph workflow powered by a local LM Studio model.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = {
            "method": request.method,
            "path": request.url.path,
            "validation_errors": exc.errors(),
        }
        if settings.log_include_content:
            details["request_body"] = exc.body
        log_event(
            logger,
            logging.WARNING,
            "http.request.validation_failed",
            "HTTP request body failed validation",
            **details,
        )
        return JSONResponse(status_code=422, content=jsonable_encoder({"detail": exc.errors()}))

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        supplied_request_id = request.headers.get("x-request-id", "").strip()
        request_id = supplied_request_id[:128] if supplied_request_id else str(uuid4())
        token = set_request_id(request_id)
        started = perf_counter()
        client = request.client
        request_details = {
            "method": request.method,
            "path": request.url.path,
            "query": request.url.query,
            "client_host": client.host if client else None,
            "client_port": client.port if client else None,
            "user_agent": request.headers.get("user-agent"),
            "content_type": request.headers.get("content-type"),
            "content_length": request.headers.get("content-length"),
        }
        log_event(
            logger,
            logging.INFO,
            "http.request.started",
            f"{request.method} {request.url.path} started",
            **request_details,
        )
        try:
            response = await call_next(request)
        except Exception as exc:
            log_event(
                logger,
                logging.ERROR,
                "http.request.failed",
                f"{request.method} {request.url.path} failed",
                exc_info=True,
                **request_details,
                error_type=type(exc).__name__,
                duration_ms=round((perf_counter() - started) * 1000, 2),
            )
            raise
        else:
            response.headers["X-Request-ID"] = request_id
            log_event(
                logger,
                logging.INFO,
                "http.response.headers_ready",
                f"{request.method} {request.url.path} returned {response.status_code}",
                **request_details,
                status_code=response.status_code,
                response_content_type=response.headers.get("content-type"),
                duration_ms=round((perf_counter() - started) * 1000, 2),
                streaming=response.headers.get("content-type", "").startswith(
                    "text/event-stream"
                ),
            )
            return response
        finally:
            reset_request_id(token)

    @app.get("/api/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        status = await request.app.state.llm.status()
        log_event(
            logger,
            logging.DEBUG,
            "api.health.checked",
            "Health endpoint checked application and LM Studio status",
            lmstudio="connected" if status.connected else "unavailable",
            model=status.model,
            detail=status.detail,
        )
        return HealthResponse(
            status="ok",
            lmstudio="connected" if status.connected else "unavailable",
            model=status.model,
            detail=status.detail,
        )

    @app.get("/api/tasks", response_model=list[TaskTemplate])
    async def tasks() -> list[TaskTemplate]:
        log_event(
            logger,
            logging.DEBUG,
            "api.tasks.listed",
            "Task templates returned",
            task_count=len(TASK_TEMPLATES),
            task_ids=[task.id for task in TASK_TEMPLATES],
        )
        return TASK_TEMPLATES

    @app.get("/api/tools", response_model=list[ToolStatus])
    async def tools(request: Request) -> list[ToolStatus]:
        statuses = request.app.state.tool_registry.statuses
        log_event(
            logger,
            logging.DEBUG,
            "api.tools.listed",
            "Diagnostic tool statuses returned",
            tools=[status.model_dump() for status in statuses],
        )
        return statuses

    @app.post("/api/runs")
    async def run_agent(payload: RunRequest, request: Request) -> StreamingResponse:
        if payload.task_id not in TASKS_BY_ID:
            log_event(
                logger,
                logging.WARNING,
                "api.run.unknown_task",
                "Agent run rejected because the task template does not exist",
                task_id=payload.task_id,
            )
            raise HTTPException(status_code=404, detail=f"Unknown task template: {payload.task_id}")

        run_id = str(uuid4())
        request_id = get_request_id()
        request_details = {
            "task_id": payload.task_id,
            "prompt_chars": len(payload.prompt),
            "context_chars": len(payload.context),
            "constraint_count": len(payload.constraints),
        }
        if settings.log_include_content:
            request_details.update(
                {
                    "prompt": payload.prompt,
                    "context": payload.context,
                    "constraints": payload.constraints,
                }
            )
        acceptance_token = set_run_id(run_id)
        try:
            log_event(
                logger,
                logging.INFO,
                "api.run.accepted",
                "Agent run accepted for streaming",
                **request_details,
            )
        finally:
            reset_run_id(acceptance_token)

        async def events() -> AsyncIterator[str]:
            request_token = set_request_id(request_id)
            run_token = set_run_id(run_id)
            started = perf_counter()
            event_count = 0
            outcome = "running"
            try:
                async for item in request.app.state.workflow.stream(payload, run_id):
                    if await request.is_disconnected():
                        outcome = "client_disconnected"
                        log_event(
                            logger,
                            logging.WARNING,
                            "api.stream.client_disconnected",
                            "Client disconnected before the agent stream completed",
                            emitted_events=event_count,
                            duration_ms=round((perf_counter() - started) * 1000, 2),
                        )
                        break
                    event_count += 1
                    event_details = {
                        "stream_event": item["event"],
                        "event_number": event_count,
                    }
                    if settings.log_include_content:
                        event_details["event_data"] = item["data"]
                    log_event(
                        logger,
                        logging.DEBUG,
                        "api.stream.event_emitted",
                        f"SSE event '{item['event']}' emitted",
                        **event_details,
                    )
                    yield _sse(item["event"], item["data"])
                else:
                    outcome = "completed"
            except (LMStudioError, ValueError) as exc:
                outcome = "known_error"
                log_event(
                    logger,
                    logging.ERROR,
                    "api.stream.agent_error",
                    "Agent stream ended with a handled model or validation error",
                    exc_info=True,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    emitted_events=event_count,
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                )
                event_count += 1
                yield _sse("error", {"run_id": run_id, "message": str(exc)})
            except Exception as exc:
                outcome = "unexpected_error"
                log_event(
                    logger,
                    logging.CRITICAL,
                    "api.stream.unexpected_error",
                    "Agent stream ended with an unexpected error",
                    exc_info=True,
                    error_type=type(exc).__name__,
                    emitted_events=event_count,
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                )
                event_count += 1
                yield _sse(
                    "error",
                    {
                        "run_id": run_id,
                        "message": (
                            "The agent workflow failed unexpectedly. Check the backend logs."
                        ),
                    },
                )
            finally:
                log_event(
                    logger,
                    logging.INFO,
                    "api.stream.closed",
                    "Agent SSE stream closed",
                    outcome=outcome,
                    emitted_events=event_count,
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                )
                reset_run_id(run_token)
                reset_request_id(request_token)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    return app


app = create_app()
