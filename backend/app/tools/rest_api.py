from typing import Literal
from urllib.parse import urlsplit

import httpx
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..config import Settings
from .common import ToolAccessError, json_result, observed_tool_call, tool_error_result


class RestApiInput(BaseModel):
    url: str = Field(description="Absolute HTTP or HTTPS URL on an allowlisted host.")
    method: Literal["GET", "HEAD"] = Field(
        default="GET", description="Read-only HTTP method."
    )
    query: dict[str, str | int | float | bool] = Field(
        default_factory=dict, description="Optional URL query parameters."
    )


class RestDiagnosticService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _host_allowed(self, hostname: str) -> bool:
        candidate = hostname.lower().rstrip(".")
        for allowed in self.settings.rest_allowed_hosts:
            normalized = allowed.rstrip(".")
            if normalized.startswith("*."):
                suffix = normalized[1:]
                if candidate.endswith(suffix) and candidate != suffix[1:]:
                    return True
            elif candidate == normalized:
                return True
        return False

    def validate_url(self, url: str) -> tuple[str, str]:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            raise ToolAccessError("Only HTTP and HTTPS URLs are allowed.")
        if parsed.username or parsed.password:
            raise ToolAccessError("Credentials in URLs are not allowed.")
        if not parsed.hostname or not self._host_allowed(parsed.hostname):
            raise ToolAccessError(
                f"Host '{parsed.hostname or ''}' is not in DIAGNOSTICS_REST_ALLOWED_HOSTS."
            )
        return parsed.scheme, parsed.hostname

    async def request(
        self,
        url: str,
        method: Literal["GET", "HEAD"] = "GET",
        query: dict[str, str | int | float | bool] | None = None,
    ) -> str:
        scheme, hostname = self.validate_url(url)
        arguments = {
            "url": url,
            "method": method,
            "query": query or {},
            "scheme": scheme,
            "hostname": hostname,
        }

        async def execute() -> str:
            timeout = httpx.Timeout(self.settings.diagnostics_tool_timeout_seconds)
            async with (
                httpx.AsyncClient(
                    headers=self.settings.rest_headers,
                    timeout=timeout,
                    follow_redirects=False,
                ) as client,
                client.stream(method, url, params=query or {}) as response,
            ):
                body = bytearray()
                truncated = False
                if method != "HEAD":
                    async for chunk in response.aiter_bytes():
                        remaining = (
                            self.settings.diagnostics_rest_max_response_bytes - len(body)
                        )
                        if remaining <= 0:
                            truncated = True
                            break
                        body.extend(chunk[:remaining])
                        if len(chunk) > remaining:
                            truncated = True
                            break
                content_type = response.headers.get("content-type", "")
                encoding = response.encoding or "utf-8"
                text = bytes(body).decode(encoding, errors="replace")
                return json_result(
                    ok=response.is_success,
                    status_code=response.status_code,
                    reason=response.reason_phrase,
                    url=str(response.url),
                    content_type=content_type,
                    elapsed_ms=round(response.elapsed.total_seconds() * 1000, 2),
                    redirected=response.is_redirect,
                    location=response.headers.get("location"),
                    body=text,
                    body_bytes=len(body),
                    truncated=truncated,
                )

        return await observed_tool_call(
            "rest_api_read",
            arguments,
            execute,
            include_content=self.settings.log_include_content,
        )

    def as_tool(self) -> StructuredTool:
        return StructuredTool.from_function(
            coroutine=self.request,
            name="rest_api_read",
            description=(
                "Perform a read-only GET or HEAD request against an operator-allowlisted HTTP(S) "
                "endpoint. Use it to inspect health endpoints, API responses, status codes, and "
                "upstream dependencies. Redirects are reported but never followed."
            ),
            args_schema=RestApiInput,
            handle_tool_error=tool_error_result,
        )
