import json

import pytest

from app.config import Settings
from app.tools.common import ToolAccessError
from app.tools.oracle import OracleDiagnosticService
from app.tools.registry import DiagnosticToolRegistry
from app.tools.rest_api import RestDiagnosticService
from app.tools.unix import UnixDiagnosticService


@pytest.mark.parametrize(
    "query",
    [
        "select status from v$instance",
        "with recent as (select 1 as value from dual) select * from recent",
    ],
)
def test_oracle_validator_accepts_read_only_queries(query: str):
    OracleDiagnosticService.validate_read_only(query)


@pytest.mark.parametrize(
    "query",
    [
        "update jobs set status = 'DONE'",
        "delete from jobs",
        "select * from dual; select * from v$instance",
        "select 1 into result from dual",
        "begin execute immediate 'drop table jobs'; end;",
    ],
)
def test_oracle_validator_rejects_mutating_or_ambiguous_queries(query: str):
    with pytest.raises(ToolAccessError):
        OracleDiagnosticService.validate_read_only(query)


def test_rest_tool_enforces_scheme_host_and_no_url_credentials():
    service = RestDiagnosticService(
        Settings(diagnostics_rest_allowed_hosts="health.example.test,*.svc.internal")
    )

    assert service.validate_url("https://health.example.test/ready") == (
        "https",
        "health.example.test",
    )
    assert service.validate_url("http://orders.svc.internal/health") == (
        "http",
        "orders.svc.internal",
    )

    for url in (
        "https://example.test/health",
        "file:///etc/passwd",
        "https://user:secret@health.example.test/ready",
        "https://svc.internal/health",
    ):
        with pytest.raises(ToolAccessError):
            service.validate_url(url)


@pytest.mark.asyncio
async def test_local_log_tools_are_confined_to_configured_roots(tmp_path):
    log_file = tmp_path / "orders.log"
    log_file.write_text(
        "startup complete\nrequest=one status=200\nrequest=two status=ERROR\n",
        encoding="utf-8",
    )
    service = UnixDiagnosticService(
        Settings(diagnostics_local_log_roots=str(tmp_path))
    )

    tail = json.loads(await service.tail_log("orders.log", lines=2))
    search = json.loads(await service.search_log(str(log_file), "error"))

    assert "request=two status=ERROR" in tail["content"]
    assert search["match_count"] == 1
    assert search["matches"][0]["text"].endswith("status=ERROR")

    with pytest.raises(ToolAccessError):
        await service.tail_log("/etc/passwd")


@pytest.mark.asyncio
async def test_local_disk_and_process_inspection_are_bounded():
    service = UnixDiagnosticService(Settings())

    disks = json.loads(await service.disk_usage())
    processes = json.loads(await service.processes(sort_by="memory", limit=3))

    assert disks["host"] == "local"
    assert isinstance(disks["filesystems"], list)
    assert processes["sort_by"] == "memory"
    assert len(processes["processes"]) <= 3


@pytest.mark.asyncio
async def test_tool_access_error_is_a_recoverable_agent_observation(tmp_path):
    service = UnixDiagnosticService(
        Settings(diagnostics_local_log_roots=str(tmp_path))
    )
    tail_tool = next(tool for tool in service.tools() if tool.name == "unix_tail_log")

    result = json.loads(
        await tail_tool.ainvoke(
            {"host": "local", "path": "/etc/passwd", "lines": 10}
        )
    )

    assert result["ok"] is False
    assert result["error_type"] == "ToolAccessError"
    assert "outside DIAGNOSTICS_LOCAL_LOG_ROOTS" in result["error"]


def test_registry_exposes_only_read_only_tools_and_gates_oracle():
    registry = DiagnosticToolRegistry(
        Settings(
            diagnostics_rest_allowed_hosts="localhost",
            oracle_dsn="",
            oracle_user="",
            oracle_password="",
        )
    )

    assert "oracle_select" not in {tool.name for tool in registry.tools}
    oracle_status = next(
        status for status in registry.statuses if status.name == "oracle_select"
    )
    assert oracle_status.enabled is False
    assert all(status.access == "read-only" for status in registry.statuses)
