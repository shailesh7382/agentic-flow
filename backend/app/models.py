from typing import Any, Literal

from pydantic import BaseModel, Field


class TaskTemplate(BaseModel):
    id: str
    title: str
    description: str
    icon: str
    prompt_label: str
    placeholder: str
    starter: str
    output_hint: str


class RunRequest(BaseModel):
    task_id: str = Field(min_length=2, max_length=64)
    prompt: str = Field(min_length=3, max_length=20_000)
    context: str = Field(default="", max_length=30_000)
    constraints: list[str] = Field(default_factory=list, max_length=12)


class PlanStep(BaseModel):
    title: str
    purpose: str


class Critique(BaseModel):
    verdict: Literal["pass", "revise"]
    summary: str
    issues: list[str] = Field(default_factory=list)


class RunResult(BaseModel):
    run_id: str
    task_id: str
    model: str
    objective: str
    plan: list[PlanStep]
    critique: Critique
    answer: str
    revisions: int


class HealthResponse(BaseModel):
    status: Literal["ok"]
    lmstudio: Literal["connected", "unavailable"]
    model: str | None = None
    detail: str | None = None


class ToolStatus(BaseModel):
    name: str
    category: Literal["database", "http", "unix"]
    enabled: bool
    access: Literal["read-only", "operator-templated-write", "local-copy"]
    detail: str


class StreamEvent(BaseModel):
    event: Literal["run", "step", "result", "error", "done"]
    data: dict[str, Any]
