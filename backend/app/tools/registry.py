from langchain_core.tools import BaseTool

from ..config import Settings
from ..models import ToolStatus
from .oracle import OracleDiagnosticService
from .rest_api import RestDiagnosticService
from .rest_catalog import load_configured_rest_tools
from .unix import UnixDiagnosticService
from .unix_catalog import load_unix_hosts


class DiagnosticToolRegistry:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._tools: list[BaseTool] = []
        self._statuses: list[ToolStatus] = []
        self._build()

    def _add_tool(self, tool: BaseTool, status: ToolStatus) -> None:
        existing = {registered.name for registered in self._tools}
        if tool.name in existing:
            raise ValueError(f"Duplicate diagnostic tool name '{tool.name}'.")
        if len(self._tools) >= self.settings.diagnostics_max_configured_tools:
            raise ValueError(
                "Configured diagnostic tools exceed DIAGNOSTICS_MAX_CONFIGURED_TOOLS="
                f"{self.settings.diagnostics_max_configured_tools}."
            )
        self._tools.append(tool)
        self._statuses.append(status)

    def _build(self) -> None:
        oracle_enabled = self.settings.diagnostics_enabled and self.settings.oracle_configured
        if oracle_enabled:
            oracle_tool = OracleDiagnosticService(self.settings).as_tool()
            oracle_detail = (
                f"Connected to configured DSN with a {self.settings.oracle_max_rows}-row cap."
            )
        elif not self.settings.diagnostics_enabled:
            oracle_detail = "Disabled because DIAGNOSTICS_ENABLED=false."
        else:
            oracle_detail = "Disabled until ORACLE_DSN, ORACLE_USER, and ORACLE_PASSWORD are set."
        oracle_status = ToolStatus(
            name="oracle_select",
            category="database",
            enabled=oracle_enabled,
            access="read-only",
            detail=oracle_detail,
        )
        if oracle_enabled:
            self._add_tool(oracle_tool, oracle_status)
        else:
            self._statuses.append(oracle_status)

        rest_enabled = self.settings.diagnostics_enabled and bool(
            self.settings.rest_allowed_hosts
        )
        if rest_enabled:
            rest_tool = RestDiagnosticService(self.settings).as_tool()
        rest_status = ToolStatus(
            name="rest_api_read",
            category="http",
            enabled=rest_enabled,
            access="read-only",
            detail=(
                "Allowed hosts: " + ", ".join(self.settings.rest_allowed_hosts)
                if rest_enabled
                else (
                    "Disabled because diagnostics are globally disabled or "
                    "DIAGNOSTICS_REST_ALLOWED_HOSTS is empty."
                )
            ),
        )
        if rest_enabled:
            self._add_tool(rest_tool, rest_status)
        else:
            self._statuses.append(rest_status)

        for configured_rest in load_configured_rest_tools(self.settings):
            self._add_tool(configured_rest.tool, configured_rest.status)

        unix_hosts = load_unix_hosts(self.settings)
        unix_service = UnixDiagnosticService(self.settings, unix_hosts)
        unix_tools = unix_service.tools()
        if self.settings.diagnostics_enabled:
            for tool in unix_tools:
                self._add_tool(
                    tool,
                    ToolStatus(
                        name=tool.name,
                        category="unix",
                        enabled=True,
                        access="read-only",
                        detail=(
                            "Generic local/alias tool. Allowed hosts: "
                            + ", ".join(["local", *sorted(unix_hosts)])
                        ),
                    ),
                )
        for tool in unix_tools:
            if self.settings.diagnostics_enabled:
                continue
            self._statuses.append(
                ToolStatus(
                    name=tool.name,
                    category="unix",
                    enabled=False,
                    access="read-only",
                    detail="Disabled because DIAGNOSTICS_ENABLED=false.",
                )
            )
        if self.settings.diagnostics_enabled:
            for alias in sorted(unix_hosts):
                for bound in unix_service.bound_tools(alias):
                    self._add_tool(bound.tool, bound.status)

    @property
    def tools(self) -> list[BaseTool]:
        return list(self._tools)

    @property
    def statuses(self) -> list[ToolStatus]:
        return list(self._statuses)
