import asyncio
import os
import platform
import posixpath
import re
import shlex
import shutil
import socket
import subprocess
from collections import deque
from pathlib import Path
from time import time
from typing import Any

import asyncssh
import psutil
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..config import BACKEND_DIR, Settings
from .common import ToolAccessError, json_result, observed_tool_call, tool_error_result

SERVICE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]{1,128}$")


class UnixHostInput(BaseModel):
    host: str = Field(
        default="local",
        description="Use 'local' or an operator-configured SSH host alias.",
    )


class UnixLogInput(UnixHostInput):
    path: str = Field(description="Absolute log path or allowed local log path.")
    lines: int = Field(default=200, ge=1, le=500)


class UnixLogSearchInput(UnixHostInput):
    path: str = Field(description="Log file to search.")
    query: str = Field(
        min_length=1,
        max_length=500,
        description="Case-insensitive literal text; regular expressions are not accepted.",
    )
    max_matches: int = Field(default=100, ge=1, le=500)


class UnixServiceInput(UnixHostInput):
    service: str = Field(
        min_length=1,
        max_length=128,
        description="systemd service unit name, such as order-api.service.",
    )
    journal_lines: int = Field(default=100, ge=1, le=500)


class UnixDiagnosticService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _remote_config(self, alias: str) -> dict[str, Any]:
        config = self.settings.unix_hosts.get(alias)
        if not isinstance(config, dict):
            raise ToolAccessError(
                f"Unknown SSH alias '{alias}'. Use 'local' or a configured alias."
            )
        required = ("host", "username", "known_hosts", "log_roots")
        missing = [field for field in required if not config.get(field)]
        if missing:
            raise ToolAccessError(
                f"SSH alias '{alias}' is missing required fields: {', '.join(missing)}."
            )
        return config

    def _local_path(self, value: str) -> Path:
        supplied = Path(value).expanduser()
        roots = [root.resolve() for root in self.settings.local_log_roots]
        candidates = (
            [supplied.resolve()]
            if supplied.is_absolute()
            else [
                (BACKEND_DIR / supplied).resolve(),
                *((root / supplied).resolve() for root in roots),
            ]
        )
        for candidate in candidates:
            for root in roots:
                if candidate == root or root in candidate.parents:
                    return candidate
        raise ToolAccessError(
            f"Path '{value}' is outside DIAGNOSTICS_LOCAL_LOG_ROOTS."
        )

    def _remote_path(self, alias: str, value: str) -> str:
        config = self._remote_config(alias)
        if not value.startswith("/"):
            raise ToolAccessError("Remote log paths must be absolute.")
        candidate = posixpath.normpath(value)
        for configured_root in config["log_roots"]:
            root = posixpath.normpath(str(configured_root))
            if candidate == root or candidate.startswith(root.rstrip("/") + "/"):
                return candidate
        raise ToolAccessError(
            f"Path '{value}' is outside the allowed roots for SSH alias '{alias}'."
        )

    async def _run_remote(self, alias: str, command: str) -> str:
        config = self._remote_config(alias)
        client_keys = [
            str(Path(key).expanduser()) for key in config.get("client_keys", [])
        ] or None

        async def connect_and_run() -> str:
            async with asyncssh.connect(
                config["host"],
                port=int(config.get("port", 22)),
                username=config["username"],
                password=config.get("password") or None,
                client_keys=client_keys,
                known_hosts=str(Path(config["known_hosts"]).expanduser()),
            ) as connection:
                result = await connection.run(command, check=False)
                return json_result(
                    host=alias,
                    exit_status=result.exit_status,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )

        return await asyncio.wait_for(
            connect_and_run(),
            timeout=self.settings.diagnostics_tool_timeout_seconds,
        )

    async def system_snapshot(self, host: str = "local") -> str:
        arguments = {"host": host}

        async def execute() -> str:
            if host != "local":
                command = (
                    "LC_ALL=C uptime; "
                    "printf '\\n--- disk ---\\n'; df -hP; "
                    "printf '\\n--- memory ---\\n'; "
                    "(free -m 2>/dev/null || vm_stat 2>/dev/null || true); "
                    "printf '\\n--- top processes ---\\n'; "
                    "ps -eo pid,ppid,user,%cpu,%mem,etime,comm --sort=-%cpu 2>/dev/null "
                    "| head -n 26"
                )
                return await self._run_remote(host, command)

            def collect() -> str:
                memory = psutil.virtual_memory()
                swap = psutil.swap_memory()
                disk = psutil.disk_usage("/")
                processes: list[dict[str, Any]] = []
                for process in psutil.process_iter(
                    ["pid", "ppid", "username", "name", "memory_percent", "status"]
                ):
                    try:
                        processes.append(process.info)
                    except (psutil.AccessDenied, psutil.NoSuchProcess):
                        continue
                processes.sort(
                    key=lambda item: float(item.get("memory_percent") or 0), reverse=True
                )
                return json_result(
                    host="local",
                    hostname=socket.gethostname(),
                    platform=platform.platform(),
                    boot_time=psutil.boot_time(),
                    uptime_seconds=round(time() - psutil.boot_time(), 2),
                    load_average=os.getloadavg() if hasattr(os, "getloadavg") else None,
                    cpu_percent=psutil.cpu_percent(interval=0.1),
                    cpu_count=psutil.cpu_count(),
                    memory={
                        "total": memory.total,
                        "available": memory.available,
                        "percent": memory.percent,
                    },
                    swap={"total": swap.total, "used": swap.used, "percent": swap.percent},
                    disk_root={
                        "total": disk.total,
                        "used": disk.used,
                        "free": disk.free,
                        "percent": disk.percent,
                    },
                    top_processes=processes[:25],
                )

            return await asyncio.to_thread(collect)

        return await observed_tool_call(
            "unix_system_snapshot",
            arguments,
            execute,
            include_content=self.settings.log_include_content,
        )

    async def tail_log(self, path: str, host: str = "local", lines: int = 200) -> str:
        arguments = {"host": host, "path": path, "lines": lines}

        async def execute() -> str:
            if host != "local":
                validated = self._remote_path(host, path)
                command = f"tail -n {int(lines)} -- {shlex.quote(validated)}"
                return await self._run_remote(host, command)

            validated = self._local_path(path)

            def read_tail() -> str:
                if not validated.is_file():
                    raise ToolAccessError(f"Log file '{path}' does not exist.")
                with validated.open("r", encoding="utf-8", errors="replace") as handle:
                    content = "".join(deque(handle, maxlen=lines))
                return json_result(
                    host="local",
                    path=str(validated),
                    lines=lines,
                    content=content,
                )

            return await asyncio.to_thread(read_tail)

        return await observed_tool_call(
            "unix_tail_log",
            arguments,
            execute,
            include_content=self.settings.log_include_content,
        )

    async def search_log(
        self,
        path: str,
        query: str,
        host: str = "local",
        max_matches: int = 100,
    ) -> str:
        arguments = {
            "host": host,
            "path": path,
            "query": query,
            "max_matches": max_matches,
        }

        async def execute() -> str:
            if host != "local":
                validated = self._remote_path(host, path)
                command = (
                    f"grep -F -i -n -m {int(max_matches)} -- "
                    f"{shlex.quote(query)} {shlex.quote(validated)}"
                )
                return await self._run_remote(host, command)

            validated = self._local_path(path)

            def search() -> str:
                if not validated.is_file():
                    raise ToolAccessError(f"Log file '{path}' does not exist.")
                size = validated.stat().st_size
                offset = max(0, size - self.settings.diagnostics_max_log_bytes)
                matches: list[dict[str, Any]] = []
                with validated.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(offset)
                    if offset:
                        handle.readline()
                    for line_number, line in enumerate(handle, start=1):
                        if query.casefold() in line.casefold():
                            matches.append(
                                {"line_in_window": line_number, "text": line.rstrip()}
                            )
                            if len(matches) >= max_matches:
                                break
                return json_result(
                    host="local",
                    path=str(validated),
                    searched_bytes=size - offset,
                    file_bytes=size,
                    query=query,
                    matches=matches,
                    match_count=len(matches),
                    truncated=offset > 0 or len(matches) >= max_matches,
                )

            return await asyncio.to_thread(search)

        return await observed_tool_call(
            "unix_search_log",
            arguments,
            execute,
            include_content=self.settings.log_include_content,
        )

    async def service_status(
        self,
        service: str,
        host: str = "local",
        journal_lines: int = 100,
    ) -> str:
        if not SERVICE_NAME_PATTERN.fullmatch(service):
            raise ToolAccessError("Service names may contain only letters, digits, . _ @ and -.")
        arguments = {
            "host": host,
            "service": service,
            "journal_lines": journal_lines,
        }

        async def execute() -> str:
            if host != "local":
                quoted = shlex.quote(service)
                command = (
                    f"systemctl status --no-pager --lines={int(journal_lines)} {quoted}; "
                    f"printf '\\n--- journal ---\\n'; "
                    f"journalctl -u {quoted} -n {int(journal_lines)} --no-pager"
                )
                return await self._run_remote(host, command)

            systemctl = shutil.which("systemctl")
            if not systemctl:
                raise ToolAccessError(
                    "Local service inspection requires systemctl; use a configured Linux SSH alias."
                )

            def inspect() -> str:
                status = subprocess.run(
                    [systemctl, "status", "--no-pager", f"--lines={journal_lines}", service],
                    capture_output=True,
                    text=True,
                    timeout=self.settings.diagnostics_tool_timeout_seconds,
                    check=False,
                )
                journalctl = shutil.which("journalctl")
                journal_output = ""
                if journalctl:
                    journal = subprocess.run(
                        [
                            journalctl,
                            "-u",
                            service,
                            "-n",
                            str(journal_lines),
                            "--no-pager",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=self.settings.diagnostics_tool_timeout_seconds,
                        check=False,
                    )
                    journal_output = journal.stdout + journal.stderr
                return json_result(
                    host="local",
                    service=service,
                    exit_status=status.returncode,
                    status=status.stdout + status.stderr,
                    journal=journal_output,
                )

            return await asyncio.to_thread(inspect)

        return await observed_tool_call(
            "unix_service_status",
            arguments,
            execute,
            include_content=self.settings.log_include_content,
        )

    def tools(self) -> list[StructuredTool]:
        return [
            StructuredTool.from_function(
                coroutine=self.system_snapshot,
                name="unix_system_snapshot",
                description=(
                    "Collect a read-only Unix system snapshot: uptime, load, memory, disk, and "
                    "top processes. The host must be 'local' or a configured SSH alias."
                ),
                args_schema=UnixHostInput,
                handle_tool_error=tool_error_result,
            ),
            StructuredTool.from_function(
                coroutine=self.tail_log,
                name="unix_tail_log",
                description=(
                    "Read the last lines of one log file under an operator-allowed local or remote "
                    "log root. This cannot read arbitrary files."
                ),
                args_schema=UnixLogInput,
                handle_tool_error=tool_error_result,
            ),
            StructuredTool.from_function(
                coroutine=self.search_log,
                name="unix_search_log",
                description=(
                    "Search recent content in one allowed log file using case-insensitive literal "
                    "text. Results and bytes read are bounded."
                ),
                args_schema=UnixLogSearchInput,
                handle_tool_error=tool_error_result,
            ),
            StructuredTool.from_function(
                coroutine=self.service_status,
                name="unix_service_status",
                description=(
                    "Inspect systemd service status and recent journal entries on local Linux or "
                    "an operator-configured SSH host. No arbitrary commands are accepted."
                ),
                args_schema=UnixServiceInput,
                handle_tool_error=tool_error_result,
            ),
        ]
