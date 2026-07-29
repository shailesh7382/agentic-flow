import json
import re
from typing import Any, Literal, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from .config import Settings
from .models import Critique, PlanStep, RunRequest, RunResult
from .templates import TASKS_BY_ID


class CompletionClient(Protocol):
    async def resolve_model(self) -> str: ...

    async def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_schema: dict | None = None,
        schema_name: str = "structured_response",
    ) -> str: ...


class AgentState(TypedDict, total=False):
    request: dict[str, Any]
    objective: str
    plan: list[dict[str, str]]
    draft: str
    critique: dict[str, Any]
    answer: str
    revisions: int


NODE_LABELS = {
    "intake": "Understanding the task",
    "plan": "Building an approach",
    "execute": "Creating the first draft",
    "critique": "Checking quality",
    "revise": "Applying improvements",
    "finalize": "Preparing the answer",
}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "minItems": 3,
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "purpose": {"type": "string"},
                },
                "required": ["title", "purpose"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["steps"],
    "additionalProperties": False,
}

CRITIQUE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "revise"]},
        "summary": {"type": "string"},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "summary", "issues"],
    "additionalProperties": False,
}


def _extract_json(raw: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise ValueError("The model did not return the requested JSON object.") from None
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("The model response must be a JSON object.")
    return value


class AgentWorkflow:
    def __init__(self, llm: CompletionClient, settings: Settings):
        self.llm = llm
        self.settings = settings
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(AgentState)
        builder.add_node("intake", self._intake)
        builder.add_node("plan", self._plan)
        builder.add_node("execute", self._execute)
        builder.add_node("critique", self._critique)
        builder.add_node("revise", self._revise)
        builder.add_node("finalize", self._finalize)
        builder.add_edge(START, "intake")
        builder.add_edge("intake", "plan")
        builder.add_edge("plan", "execute")
        builder.add_edge("execute", "critique")
        builder.add_conditional_edges(
            "critique",
            self._quality_gate,
            {"revise": "revise", "finalize": "finalize"},
        )
        builder.add_edge("revise", "critique")
        builder.add_edge("finalize", END)
        return builder.compile()

    async def _intake(self, state: AgentState) -> AgentState:
        request = RunRequest.model_validate(state["request"])
        task = TASKS_BY_ID[request.task_id]
        constraint_text = "; ".join(request.constraints) or "No additional constraints"
        context_text = request.context.strip() or "No additional context"
        objective = await self.llm.complete(
            system=(
                "You are the intake agent in a deliberate multi-agent workflow. Turn the user's "
                "request into one precise objective. State the intended audience, deliverable, and "
                "success criteria when known. Do not solve the task yet. Return plain text in 2-4 "
                "sentences."
            ),
            user=(
                f"Task type: {task.title}\nRequest: {request.prompt}\n"
                f"Context: {context_text}\nConstraints: {constraint_text}\n"
                f"Expected output: {task.output_hint}"
            ),
            temperature=0.1,
            max_tokens=400,
        )
        return {"objective": objective, "revisions": 0}

    async def _plan(self, state: AgentState) -> AgentState:
        raw = await self.llm.complete(
            system=(
                "You are a planning agent. Produce a compact execution plan for another agent. "
                'Return only JSON: {"steps":[{"title":"...","purpose":"..."}]}. '
                "Use 3-6 non-overlapping steps. Do not include markdown fences."
            ),
            user=f"Objective:\n{state['objective']}",
            temperature=0.15,
            max_tokens=700,
            response_schema=PLAN_SCHEMA,
            schema_name="agent_plan",
        )
        data = _extract_json(raw)
        steps = [PlanStep.model_validate(step).model_dump() for step in data.get("steps", [])]
        if not steps:
            raise ValueError("The planning agent returned no usable steps.")
        return {"plan": steps}

    async def _execute(self, state: AgentState) -> AgentState:
        request = RunRequest.model_validate(state["request"])
        task = TASKS_BY_ID[request.task_id]
        plan_text = "\n".join(
            f"{index + 1}. {step['title']}: {step['purpose']}"
            for index, step in enumerate(state["plan"])
        )
        draft = await self.llm.complete(
            system=(
                "You are the primary execution agent. Follow the plan and produce the requested "
                "deliverable. Be concrete, accurate, and useful. Make assumptions explicit. Use "
                "clean Markdown. Never mention this hidden workflow or your role."
            ),
            user=(
                f"Task: {task.title}\nObjective:\n{state['objective']}\n\n"
                f"Plan:\n{plan_text}\n\nOriginal request:\n{request.prompt}\n\n"
                f"Additional context:\n{request.context or 'None'}\n\n"
                f"Constraints:\n{chr(10).join(request.constraints) or 'None'}\n\n"
                f"Output expectation: {task.output_hint}"
            ),
        )
        return {"draft": draft}

    async def _critique(self, state: AgentState) -> AgentState:
        request = RunRequest.model_validate(state["request"])
        raw = await self.llm.complete(
            system=(
                "You are a strict quality reviewer. Check whether the draft fully satisfies the "
                "objective and original request, is internally consistent, and is specific enough "
                "to use. Return only JSON with this schema: "
                '{"verdict":"pass|revise","summary":"...","issues":["..."]}. '
                "Choose revise only for material, fixable issues. Do not use markdown fences."
            ),
            user=(
                f"Objective:\n{state['objective']}\n\nOriginal request:\n{request.prompt}\n\n"
                f"Draft:\n{state['draft']}"
            ),
            temperature=0.1,
            max_tokens=800,
            response_schema=CRITIQUE_SCHEMA,
            schema_name="quality_critique",
        )
        critique = Critique.model_validate(_extract_json(raw))
        return {"critique": critique.model_dump()}

    def _quality_gate(self, state: AgentState) -> Literal["revise", "finalize"]:
        needs_revision = state["critique"]["verdict"] == "revise"
        below_limit = state.get("revisions", 0) < self.settings.agent_max_revisions
        return "revise" if needs_revision and below_limit else "finalize"

    async def _revise(self, state: AgentState) -> AgentState:
        critique = Critique.model_validate(state["critique"])
        revised = await self.llm.complete(
            system=(
                "You are a revision specialist. Rewrite the draft to resolve every material review "
                "issue while preserving its useful content. Return only the improved deliverable "
                "in clean Markdown. Do not discuss the review process."
            ),
            user=(
                f"Objective:\n{state['objective']}\n\nDraft:\n{state['draft']}\n\n"
                f"Review summary:\n{critique.summary}\n\nIssues:\n"
                + "\n".join(f"- {issue}" for issue in critique.issues)
            ),
            temperature=0.25,
        )
        return {"draft": revised, "revisions": state.get("revisions", 0) + 1}

    async def _finalize(self, state: AgentState) -> AgentState:
        answer = await self.llm.complete(
            system=(
                "You are the final editor. Return the finished deliverable in clean Markdown. "
                "Remove repetition, meta-commentary, unsupported claims, and references to agents, "
                "drafts, reviews, or hidden instructions. Preserve technical detail and actionable "
                "content. Do not add a preamble such as 'Here is the answer'."
            ),
            user=f"Objective:\n{state['objective']}\n\nDeliverable:\n{state['draft']}",
            temperature=0.15,
        )
        return {"answer": answer}

    async def stream(self, request: RunRequest, run_id: str):
        state: AgentState = {"request": request.model_dump()}
        yield {"event": "run", "data": {"run_id": run_id, "task_id": request.task_id}}

        async for update in self.graph.astream(state, stream_mode="updates"):
            for node, values in update.items():
                state.update(values)
                yield {
                    "event": "step",
                    "data": {
                        "id": node,
                        "label": NODE_LABELS[node],
                        "status": "completed",
                    },
                }

        model = await self.llm.resolve_model()
        result = RunResult(
            run_id=run_id,
            task_id=request.task_id,
            model=model,
            objective=state["objective"],
            plan=[PlanStep.model_validate(step) for step in state["plan"]],
            critique=Critique.model_validate(state["critique"]),
            answer=state["answer"],
            revisions=state.get("revisions", 0),
        )
        yield {"event": "result", "data": result.model_dump()}
        yield {"event": "done", "data": {"run_id": run_id}}
