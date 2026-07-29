import json

import pytest

from app.config import Settings
from app.graph import AgentWorkflow
from app.models import RunRequest


class ScriptedLLM:
    def __init__(self):
        self.calls = 0

    async def resolve_model(self) -> str:
        return "local-test-model"

    async def complete(self, **kwargs) -> str:
        self.calls += 1
        responses = {
            1: "Create a useful architecture proposal for an engineering audience.",
            2: json.dumps(
                {
                    "steps": [
                        {"title": "Frame", "purpose": "Define the boundaries"},
                        {"title": "Design", "purpose": "Propose the components"},
                        {"title": "Validate", "purpose": "Check the trade-offs"},
                    ]
                }
            ),
            3: "# Architecture\n\nUse a queue.",
            4: json.dumps(
                {
                    "verdict": "pass",
                    "summary": "The answer meets the objective.",
                    "issues": [],
                }
            ),
            5: "# Architecture\n\nUse a durable queue with idempotent consumers.",
        }
        return responses[self.calls]


class ScriptedDiagnosticLLM:
    def __init__(self):
        self.calls = 0

    async def resolve_model(self) -> str:
        return "local-diagnostic-model"

    async def complete(self, **kwargs) -> str:
        self.calls += 1
        responses = {
            1: "Diagnose the reported service issue using read-only evidence.",
            2: json.dumps(
                {
                    "steps": [
                        {"title": "Inspect", "purpose": "Collect runtime evidence"},
                        {"title": "Correlate", "purpose": "Compare logs and health"},
                        {"title": "Report", "purpose": "Rank supported causes"},
                    ]
                }
            ),
            3: json.dumps(
                {
                    "verdict": "pass",
                    "summary": "The diagnosis is evidence-backed.",
                    "issues": [],
                }
            ),
            4: "# Diagnosis\n\nThe service is healthy based on the collected evidence.",
        }
        return responses[self.calls]


class FakeDiagnosticRunner:
    def __init__(self):
        self.requests: list[RunRequest] = []

    async def run(
        self,
        request: RunRequest,
        objective: str,
        plan: list,
    ) -> str:
        self.requests.append(request)
        assert objective
        assert len(plan) == 3
        return (
            "# Evidence\n\nThe service health check passed "
            "[rest_api_read:http://localhost/health]."
        )


@pytest.mark.asyncio
async def test_graph_completes_without_revision():
    llm = ScriptedLLM()
    workflow = AgentWorkflow(llm, Settings(agent_max_revisions=1))
    request = RunRequest(task_id="code", prompt="Design a webhook architecture")

    events = [event async for event in workflow.stream(request, "run-test")]
    result = next(event["data"] for event in events if event["event"] == "result")

    assert result["model"] == "local-test-model"
    assert result["revisions"] == 0
    assert "idempotent consumers" in result["answer"]
    assert [event["event"] for event in events][-1] == "done"


@pytest.mark.asyncio
async def test_diagnostic_task_uses_tool_enabled_runner():
    llm = ScriptedDiagnosticLLM()
    runner = FakeDiagnosticRunner()
    workflow = AgentWorkflow(
        llm,
        Settings(agent_max_revisions=1),
        diagnostic_runner=runner,
    )
    request = RunRequest(
        task_id="diagnose",
        prompt="Inspect http://localhost/health and explain the outage.",
    )

    events = [event async for event in workflow.stream(request, "run-diagnostic")]
    result = next(event["data"] for event in events if event["event"] == "result")
    step_labels = [
        event["data"]["label"] for event in events if event["event"] == "step"
    ]

    assert len(runner.requests) == 1
    assert result["task_id"] == "diagnose"
    assert result["model"] == "local-diagnostic-model"
    assert "service is healthy" in result["answer"]
    assert "Running diagnostic tools" in step_labels
