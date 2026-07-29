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
