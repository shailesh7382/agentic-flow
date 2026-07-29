import json

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.tools.common import ToolAccessError
from app.tools.rest_catalog import (
    ConfiguredRestService,
    RestRequestTemplate,
    load_configured_rest_tools,
    load_rest_definitions,
)
from app.tools.unix import UnixDiagnosticService
from app.tools.unix_catalog import load_unix_hosts


def _write_rest_catalog(tmp_path, *, method: str = "POST"):
    template_root = tmp_path / "templates"
    template_root.mkdir()
    template = {
        "path": "/api/diagnostics/{{service}}",
        "query": {"verbose": "{{verbose}}"},
        "headers": {"Accept": "application/json"},
        "header_env": {"Authorization": "TEST_DIAGNOSTIC_AUTH"},
        "variables": {
            "service": {
                "type": "string",
                "description": "Service identifier",
                "required": True,
                "pattern": "^[A-Za-z0-9_/-]+$",
            },
            "verbose": {
                "type": "boolean",
                "required": False,
                "default": False,
            },
            "limit": {
                "type": "integer",
                "required": False,
                "default": 25,
                "minimum": 1,
                "maximum": 100,
            },
        },
    }
    if method != "GET":
        template["body"] = {"service": "{{service}}", "limit": "{{limit}}"}
    (template_root / "diagnostics.json").write_text(
        json.dumps(template),
        encoding="utf-8",
    )
    catalog = tmp_path / "rest-tools.csv"
    catalog.write_text(
        "name,description,method,base_url,template_file,timeout_seconds,"
        "max_response_bytes,enabled\n"
        f"query_diagnostics,Query diagnostics,{method},https://api.example.test,"
        "diagnostics.json,12,65536,true\n",
        encoding="utf-8",
    )
    settings = Settings(
        diagnostics_rest_tools_csv=str(catalog),
        diagnostics_rest_template_root=str(template_root),
    )
    return settings, template


def test_csv_rest_operation_becomes_separate_typed_tool(tmp_path):
    settings, _ = _write_rest_catalog(tmp_path)

    configured = load_configured_rest_tools(settings)

    assert len(configured) == 1
    assert configured[0].tool.name == "query_diagnostics"
    assert configured[0].status.access == "operator-templated-write"
    validated = configured[0].tool.args_schema.model_validate(
        {"service": "orders/api"}
    )
    assert validated.limit == 25
    with pytest.raises(ValidationError):
        configured[0].tool.args_schema.model_validate(
            {"service": "orders", "limit": 500}
        )


def test_csv_get_operation_is_read_only(tmp_path):
    settings, _ = _write_rest_catalog(tmp_path, method="GET")

    configured = load_configured_rest_tools(settings)

    assert configured[0].status.access == "read-only"


@pytest.mark.asyncio
async def test_configured_post_sends_only_rendered_template(
    tmp_path,
    monkeypatch,
):
    settings, template_data = _write_rest_catalog(tmp_path)
    monkeypatch.setenv("TEST_DIAGNOSTIC_AUTH", "Bearer test-token")
    definition = load_rest_definitions(settings)[0]
    captured: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"accepted": True})

    service = ConfiguredRestService(
        settings,
        definition,
        RestRequestTemplate.model_validate(template_data),
        transport=httpx.MockTransport(respond),
    )

    result = json.loads(
        await service.invoke(service="orders", verbose=True, limit=10)
    )

    assert result["ok"] is True
    assert len(captured) == 1
    assert captured[0].method == "POST"
    assert captured[0].url == (
        "https://api.example.test/api/diagnostics/orders?verbose=true"
    )
    assert json.loads(captured[0].content) == {"service": "orders", "limit": 10}
    assert captured[0].headers["authorization"] == "Bearer test-token"


def test_rest_template_renders_typed_json_and_environment_header(
    tmp_path,
    monkeypatch,
):
    settings, template_data = _write_rest_catalog(tmp_path)
    monkeypatch.setenv("TEST_DIAGNOSTIC_AUTH", "Bearer test-token")
    definition = load_rest_definitions(settings)[0]
    service = ConfiguredRestService(
        settings,
        definition,
        RestRequestTemplate.model_validate(template_data),
    )

    url, query, body, headers = service.render_request(
        {"service": "orders/api", "verbose": True, "limit": 10}
    )

    assert url == "https://api.example.test/api/diagnostics/orders%2Fapi"
    assert query == {"verbose": True}
    assert body == {"service": "orders/api", "limit": 10}
    assert headers["Authorization"] == "Bearer test-token"


def test_rest_catalog_rejects_arbitrary_methods(tmp_path):
    settings, _ = _write_rest_catalog(tmp_path, method="DELETE")

    with pytest.raises(ValueError, match="unsupported method"):
        load_rest_definitions(settings)


def _write_unix_catalog(tmp_path):
    catalog = tmp_path / "unix-hosts.csv"
    known_hosts = tmp_path / "known_hosts"
    client_key = tmp_path / "diagnostic_key"
    catalog.write_text(
        "name,host,port,username,client_keys,known_hosts,password_env,log_roots,"
        "capabilities,enabled\n"
        f"orders_prod,10.20.0.15,22,diagnostic,{client_key},{known_hosts},,"
        "/var/log/orders,snapshot;disks;processes;tail;search;fetch;service,true\n",
        encoding="utf-8",
    )
    return Settings(
        diagnostics_unix_hosts_json="{}",
        diagnostics_unix_hosts_csv=str(catalog),
    )


def test_unix_csv_host_generates_host_bound_tools(tmp_path):
    settings = _write_unix_catalog(tmp_path)
    hosts = load_unix_hosts(settings)
    service = UnixDiagnosticService(settings, hosts)

    configured = service.bound_tools("orders_prod")
    names = {item.tool.name for item in configured}

    assert names == {
        "orders_prod_system_snapshot",
        "orders_prod_disk_usage",
        "orders_prod_processes",
        "orders_prod_tail_log",
        "orders_prod_search_log",
        "orders_prod_fetch_log",
        "orders_prod_service_status",
    }
    fetch = next(item for item in configured if item.tool.name.endswith("fetch_log"))
    assert fetch.status.access == "local-copy"
    assert (
        service._remote_path("orders_prod", "/var/log/orders/api.log")
        == "/var/log/orders/api.log"
    )
    with pytest.raises(ToolAccessError):
        service._remote_path("orders_prod", "/etc/shadow")
