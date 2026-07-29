from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import RunRequest


class FakeLLM:
    async def status(self):
        class Status:
            connected = True
            model = "test-model"
            detail = None

        return Status()

    async def close(self):
        return None


class FakeOpenAIClient:
    def __init__(self):
        self.client = FakeLLM()

    async def status(self):
        return await self.client.status()


class FakeWorkflow:
    async def stream(
        self, request: RunRequest, run_id: str
    ) -> AsyncIterator[dict[str, object]]:
        yield {"event": "run", "data": {"run_id": run_id, "task_id": request.task_id}}
        yield {
            "event": "step",
            "data": {"id": "intake", "label": "Understanding the task", "status": "completed"},
        }
        yield {
            "event": "result",
            "data": {
                "run_id": run_id,
                "task_id": request.task_id,
                "model": "test-model",
                "objective": "Test objective",
                "plan": [{"title": "Test", "purpose": "Verify the stream"}],
                "critique": {"verdict": "pass", "summary": "Good", "issues": []},
                "answer": "# Result\n\nIt works.",
                "revisions": 0,
            },
        }
        yield {"event": "done", "data": {"run_id": run_id}}


def test_tasks_are_available():
    app = create_app(FakeWorkflow())
    with TestClient(app) as client:
        response = client.get("/api/tasks")
    assert response.status_code == 200
    assert {task["id"] for task in response.json()} == {
        "diagnose",
        "write",
        "analyze",
        "plan",
        "code",
        "brainstorm",
        "summarize",
    }


def test_diagnostic_tool_statuses_are_available():
    app = create_app(FakeWorkflow())
    with TestClient(app) as client:
        response = client.get("/api/tools")
    assert response.status_code == 200
    assert {tool["name"] for tool in response.json()} == {
        "oracle_select",
        "rest_api_read",
        "unix_system_snapshot",
        "unix_disk_usage",
        "unix_processes",
        "unix_tail_log",
        "unix_search_log",
        "unix_service_status",
    }
    assert all(tool["access"] == "read-only" for tool in response.json())


def test_unknown_task_is_rejected():
    app = create_app(FakeWorkflow())
    with TestClient(app) as client:
        response = client.post("/api/runs", json={"task_id": "missing", "prompt": "Do a thing"})
    assert response.status_code == 404


def test_run_stream_contains_result():
    app = create_app(FakeWorkflow())
    with TestClient(app) as client, client.stream(
        "POST",
        "/api/runs",
        json={"task_id": "write", "prompt": "Write a useful announcement"},
    ) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200
    assert "event: step" in body
    assert "event: result" in body
    assert "It works." in body
