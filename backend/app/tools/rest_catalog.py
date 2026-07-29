import csv
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from time import perf_counter
from typing import Any, Literal
from urllib.parse import quote, urlsplit

import httpx
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, create_model

from ..config import Settings
from ..models import ToolStatus
from .common import ToolAccessError, json_result, observed_tool_call, tool_error_result

TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{2,63}$")
PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9_]*)\s*\}\}")
VARIABLE_TYPES: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


class TemplateVariable(BaseModel):
    type: Literal["string", "integer", "number", "boolean"] = "string"
    description: str = ""
    required: bool = True
    default: Any = None
    enum: list[str | int | float | bool] = Field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    min_length: int | None = Field(default=None, ge=0)
    max_length: int | None = Field(default=None, ge=1)
    pattern: str | None = None


class RestRequestTemplate(BaseModel):
    path: str
    query: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    header_env: dict[str, str] = Field(
        default_factory=dict,
        description="HTTP header name to environment variable name.",
    )
    body: Any = None
    variables: dict[str, TemplateVariable] = Field(default_factory=dict)


@dataclass(frozen=True)
class ConfiguredRestDefinition:
    name: str
    description: str
    method: Literal["GET", "POST"]
    base_url: str
    template_path: Path
    timeout_seconds: float
    max_response_bytes: int


@dataclass(frozen=True)
class ConfiguredRestTool:
    tool: StructuredTool
    status: ToolStatus


def _parse_bool(value: str, *, field: str, row: int) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"Invalid boolean '{value}' for {field} on REST CSV row {row}.")


def _safe_template_path(root: Path, configured: str, row: int) -> Path:
    resolved_root = root.resolve()
    candidate = (resolved_root / configured).resolve()
    if candidate == resolved_root or resolved_root not in candidate.parents:
        raise ValueError(f"REST template on CSV row {row} escapes the template root.")
    if candidate.suffix.lower() != ".json":
        raise ValueError(f"REST template on CSV row {row} must be a JSON file.")
    return candidate


def _validate_base_url(value: str, row: int) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"REST base_url on CSV row {row} must be an HTTP(S) URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            f"REST base_url on CSV row {row} cannot include credentials, query, or fragment."
        )
    return value.rstrip("/")


def load_rest_definitions(settings: Settings) -> list[ConfiguredRestDefinition]:
    path = settings.rest_tools_csv_path
    if not path.exists():
        return []
    definitions: list[ConfiguredRestDefinition] = []
    names: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"name", "description", "method", "base_url", "template_file", "enabled"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"REST tools CSV is missing columns: {', '.join(sorted(missing))}."
            )
        for row_number, row in enumerate(reader, start=2):
            if not _parse_bool(row.get("enabled", ""), field="enabled", row=row_number):
                continue
            name = row.get("name", "").strip()
            if not TOOL_NAME_PATTERN.fullmatch(name):
                raise ValueError(
                    f"REST tool name '{name}' on row {row_number} must match "
                    "[A-Za-z][A-Za-z0-9_-]{2,63}."
                )
            if name in names:
                raise ValueError(f"Duplicate REST tool name '{name}' on row {row_number}.")
            names.add(name)
            method = row.get("method", "").strip().upper()
            if method not in {"GET", "POST"}:
                raise ValueError(
                    f"REST tool '{name}' uses unsupported method '{method}'; use GET or POST."
                )
            timeout = float(
                row.get("timeout_seconds", "").strip()
                or settings.diagnostics_tool_timeout_seconds
            )
            max_bytes = int(
                row.get("max_response_bytes", "").strip()
                or settings.diagnostics_rest_max_response_bytes
            )
            if not 1 <= timeout <= 300:
                raise ValueError(f"REST tool '{name}' timeout must be between 1 and 300.")
            if not 1024 <= max_bytes <= 10_485_760:
                raise ValueError(
                    f"REST tool '{name}' max_response_bytes must be 1024–10485760."
                )
            definitions.append(
                ConfiguredRestDefinition(
                    name=name,
                    description=row.get("description", "").strip()
                    or f"Call the configured {method} diagnostic API.",
                    method=method,
                    base_url=_validate_base_url(
                        row.get("base_url", "").strip(), row_number
                    ),
                    template_path=_safe_template_path(
                        settings.rest_template_root,
                        row.get("template_file", "").strip(),
                        row_number,
                    ),
                    timeout_seconds=timeout,
                    max_response_bytes=max_bytes,
                )
            )
    return definitions


def _load_template(definition: ConfiguredRestDefinition) -> RestRequestTemplate:
    try:
        raw = json.loads(definition.template_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(
            f"REST tool '{definition.name}' template does not exist: "
            f"{definition.template_path}."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"REST tool '{definition.name}' template is invalid JSON: {exc}."
        ) from exc
    template = RestRequestTemplate.model_validate(raw)
    if definition.method == "GET" and template.body is not None:
        raise ValueError(
            f"REST GET tool '{definition.name}' cannot define a request body."
        )
    for header, environment_name in template.header_env.items():
        if "\r" in header or "\n" in header:
            raise ValueError(
                f"REST tool '{definition.name}' contains an invalid header name."
            )
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", environment_name):
            raise ValueError(
                f"REST tool '{definition.name}' contains invalid environment variable "
                f"'{environment_name}'."
            )
    parsed_path = urlsplit(template.path)
    if (
        parsed_path.scheme
        or parsed_path.netloc
        or parsed_path.query
        or parsed_path.fragment
        or ".." in PurePosixPath(parsed_path.path).parts
    ):
        raise ValueError(
            f"REST tool '{definition.name}' template path must be a relative or absolute "
            "URL path without query, fragment, origin, or '..'."
        )
    referenced = set(
        PLACEHOLDER_PATTERN.findall(
            json.dumps(
                {
                    "path": template.path,
                    "query": template.query,
                    "headers": template.headers,
                    "body": template.body,
                }
            )
        )
    )
    undefined = referenced.difference(template.variables)
    if undefined:
        raise ValueError(
            f"REST tool '{definition.name}' references undefined variables: "
            f"{', '.join(sorted(undefined))}."
        )
    return template


def _input_model(name: str, variables: dict[str, TemplateVariable]) -> type[BaseModel]:
    fields: dict[str, tuple[Any, Any]] = {}
    for variable_name, specification in variables.items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", variable_name):
            raise ValueError(
                f"REST tool '{name}' has invalid variable name '{variable_name}'."
            )
        annotation: Any = VARIABLE_TYPES[specification.type]
        if specification.enum:
            valid_enum = {
                "string": lambda item: isinstance(item, str),
                "integer": lambda item: type(item) is int,
                "number": lambda item: type(item) in {int, float},
                "boolean": lambda item: isinstance(item, bool),
            }[specification.type]
            if not all(valid_enum(item) for item in specification.enum):
                raise ValueError(
                    f"REST tool '{name}' variable '{variable_name}' has enum values "
                    f"incompatible with type '{specification.type}'."
                )
            annotation = Literal.__getitem__(tuple(specification.enum))
        default = ... if specification.required else specification.default
        field_options: dict[str, Any] = {"description": specification.description}
        if specification.minimum is not None:
            field_options["ge"] = specification.minimum
        if specification.maximum is not None:
            field_options["le"] = specification.maximum
        if specification.min_length is not None:
            field_options["min_length"] = specification.min_length
        if specification.max_length is not None:
            field_options["max_length"] = specification.max_length
        if specification.pattern is not None:
            field_options["pattern"] = specification.pattern
        fields[variable_name] = (annotation, Field(default, **field_options))
    return create_model(
        f"{name.title().replace('_', '')}Input",
        __config__=ConfigDict(extra="forbid", validate_default=True),
        **fields,
    )


def _render(value: Any, variables: dict[str, Any], *, url_path: bool = False) -> Any:
    if isinstance(value, str):
        exact = PLACEHOLDER_PATTERN.fullmatch(value)
        if exact:
            replacement = variables[exact.group(1)]
            return quote(str(replacement), safe="") if url_path else replacement

        def replace(match: re.Match[str]) -> str:
            replacement = str(variables[match.group(1)])
            return quote(replacement, safe="") if url_path else replacement

        return PLACEHOLDER_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_render(item, variables, url_path=url_path) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _render(item, variables, url_path=url_path)
            for key, item in value.items()
        }
    return value


class ConfiguredRestService:
    def __init__(
        self,
        settings: Settings,
        definition: ConfiguredRestDefinition,
        template: RestRequestTemplate,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings = settings
        self.definition = definition
        self.template = template
        self.transport = transport

    def render_request(
        self,
        variables: dict[str, Any],
    ) -> tuple[str, dict[str, Any], Any, dict[str, str]]:
        path = _render(self.template.path, variables, url_path=True)
        url = f"{self.definition.base_url}/{str(path).lstrip('/')}"
        query = _render(self.template.query, variables)
        body = _render(self.template.body, variables)
        headers = {
            **self.settings.rest_headers,
            **_render(self.template.headers, variables),
        }
        for header, environment_name in self.template.header_env.items():
            value = os.getenv(environment_name)
            if value is None:
                raise ToolAccessError(
                    f"Required environment variable '{environment_name}' is not set."
                )
            headers[header] = value
        return url, query, body, headers

    async def invoke(self, **variables: Any) -> str:
        url, query, body, headers = self.render_request(variables)
        arguments = {
            "configured_tool": self.definition.name,
            "method": self.definition.method,
            "url": url,
            "variables": variables,
        }

        async def execute() -> str:
            request_started = perf_counter()
            request_options: dict[str, Any] = {
                "params": query,
                "headers": headers,
            }
            if self.definition.method == "POST":
                request_options["json"] = body
            async with (
                httpx.AsyncClient(
                    timeout=httpx.Timeout(self.definition.timeout_seconds),
                    follow_redirects=False,
                    transport=self.transport,
                ) as client,
                client.stream(
                    self.definition.method,
                    url,
                    **request_options,
                ) as response,
            ):
                content = bytearray()
                truncated = False
                async for chunk in response.aiter_bytes():
                    remaining = self.definition.max_response_bytes - len(content)
                    if remaining <= 0:
                        truncated = True
                        break
                    content.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        truncated = True
                        break
                text = bytes(content).decode(
                    response.encoding or "utf-8", errors="replace"
                )
                return json_result(
                    ok=response.is_success,
                    tool=self.definition.name,
                    method=self.definition.method,
                    status_code=response.status_code,
                    reason=response.reason_phrase,
                    url=str(response.url),
                    content_type=response.headers.get("content-type", ""),
                    location=response.headers.get("location"),
                    elapsed_ms=round((perf_counter() - request_started) * 1000, 2),
                    body=text,
                    body_bytes=len(content),
                    truncated=truncated,
                )

        return await observed_tool_call(
            self.definition.name,
            arguments,
            execute,
            include_content=self.settings.log_include_content,
        )

    def as_tool(self) -> ConfiguredRestTool:
        access = (
            "read-only"
            if self.definition.method == "GET"
            else "operator-templated-write"
        )
        tool = StructuredTool.from_function(
            coroutine=self.invoke,
            name=self.definition.name,
            description=(
                f"{self.definition.description} This is an operator-configured "
                f"{self.definition.method} call to {self.definition.base_url}; its path, "
                "query, headers, and JSON body are constrained by a server-side template."
            ),
            args_schema=_input_model(
                self.definition.name,
                self.template.variables,
            ),
            handle_tool_error=tool_error_result,
        )
        return ConfiguredRestTool(
            tool=tool,
            status=ToolStatus(
                name=self.definition.name,
                category="http",
                enabled=True,
                access=access,
                detail=(
                    f"{self.definition.method} {self.definition.base_url}"
                    f"/{self.template.path.lstrip('/')} from "
                    f"{self.definition.template_path.name}."
                ),
            ),
        )


def load_configured_rest_tools(settings: Settings) -> list[ConfiguredRestTool]:
    if not settings.diagnostics_enabled:
        return []
    configured: list[ConfiguredRestTool] = []
    for definition in load_rest_definitions(settings):
        template = _load_template(definition)
        configured.append(
            ConfiguredRestService(settings, definition, template).as_tool()
        )
    return configured
