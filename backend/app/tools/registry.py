from langchain_core.tools import BaseTool

from ..config import Settings
from ..models import ToolStatus
from .oracle import OracleDiagnosticService
from .rest_api import RestDiagnosticService
from .unix import UnixDiagnosticService


class DiagnosticToolRegistry:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._tools: list[BaseTool] = []
        self._statuses: list[ToolStatus] = []
        self._build()

    def _build(self) -> None:
        oracle_enabled = self.settings.diagnostics_enabled and self.settings.oracle_configured
        if oracle_enabled:
            self._tools.append(OracleDiagnosticService(self.settings).as_tool())
            oracle_detail = (
                f"Connected to configured DSN with a {self.settings.oracle_max_rows}-row cap."
            )
        elif not self.settings.diagnostics_enabled:
            oracle_detail = "Disabled because DIAGNOSTICS_ENABLED=false."
        else:
            oracle_detail = "Disabled until ORACLE_DSN, ORACLE_USER, and ORACLE_PASSWORD are set."
        self._statuses.append(
            ToolStatus(
                name="oracle_select",
                category="database",
                enabled=oracle_enabled,
                access="read-only",
                detail=oracle_detail,
            )
        )

        rest_enabled = self.settings.diagnostics_enabled and bool(
            self.settings.rest_allowed_hosts
        )
        if rest_enabled:
            self._tools.append(RestDiagnosticService(self.settings).as_tool())
        self._statuses.append(
            ToolStatus(
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
        )

        unix_service = UnixDiagnosticService(self.settings)
        unix_tools = unix_service.tools()
        if self.settings.diagnostics_enabled:
            self._tools.extend(unix_tools)
        host_aliases = ["local", *sorted(self.settings.unix_hosts)]
        for tool in unix_tools:
            self._statuses.append(
                ToolStatus(
                    name=tool.name,
                    category="unix",
                    enabled=self.settings.diagnostics_enabled,
                    access="read-only",
                    detail=(
                        "Allowed hosts: " + ", ".join(host_aliases)
                        if self.settings.diagnostics_enabled
                        else "Disabled because DIAGNOSTICS_ENABLED=false."
                    ),
                )
            )

    @property
    def tools(self) -> list[BaseTool]:
        return list(self._tools)

    @property
    def statuses(self) -> list[ToolStatus]:
        return list(self._statuses)
