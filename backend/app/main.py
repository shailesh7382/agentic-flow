import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .config import get_settings
from .graph import AgentWorkflow
from .llm import LMStudioClient, LMStudioError
from .models import HealthResponse, RunRequest, TaskTemplate
from .templates import TASK_TEMPLATES, TASKS_BY_ID


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_app(workflow: AgentWorkflow | None = None) -> FastAPI:
    settings = get_settings()
    llm = LMStudioClient(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.workflow = workflow or AgentWorkflow(llm, settings)
        app.state.llm = llm
        yield
        await llm.client.close()

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

    @app.get("/api/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        status = await request.app.state.llm.status()
        return HealthResponse(
            status="ok",
            lmstudio="connected" if status.connected else "unavailable",
            model=status.model,
            detail=status.detail,
        )

    @app.get("/api/tasks", response_model=list[TaskTemplate])
    async def tasks() -> list[TaskTemplate]:
        return TASK_TEMPLATES

    @app.post("/api/runs")
    async def run_agent(payload: RunRequest, request: Request) -> StreamingResponse:
        if payload.task_id not in TASKS_BY_ID:
            raise HTTPException(status_code=404, detail=f"Unknown task template: {payload.task_id}")

        run_id = str(uuid4())

        async def events() -> AsyncIterator[str]:
            try:
                async for item in request.app.state.workflow.stream(payload, run_id):
                    if await request.is_disconnected():
                        break
                    yield _sse(item["event"], item["data"])
            except (LMStudioError, ValueError) as exc:
                yield _sse("error", {"run_id": run_id, "message": str(exc)})
            except Exception:
                yield _sse(
                    "error",
                    {
                        "run_id": run_id,
                        "message": (
                            "The agent workflow failed unexpectedly. Check the backend logs."
                        ),
                    },
                )

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
