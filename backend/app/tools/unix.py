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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from time import time
from typing import Any, Literal

import asyncssh
import psutil
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..config import BACKEND_DIR, Settings
from ..models import ToolStatus
from .common import ToolAccessError, json_result, observed_tool_call, tool_error_result

SERVICE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]{1,128}$")
PROCESS_SORT_FIELDS = {
    "cpu": "%cpu",
    "memory": "%mem",
}


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


class UnixProcessInput(UnixHostInput):
    sort_by: Literal["cpu", "memory"] = "cpu"
    limit: int = Field(default=25, ge=1, le=100)


class BoundLogInput(BaseModel):
    path: str = Field(description="Absolute path under this host's configured log roots.")
    lines: int = Field(default=200, ge=1, le=500)


class BoundLogSearchInput(BaseModel):
    path: str = Field(description="Absolute path under this host's configured log roots.")
    query: str = Field(
        min_length=1,
        max_length=500,
        description="Case-insensitive literal text; regular expressions are not accepted.",
    )
    max_matches: int = Field(default=100, ge=1, le=500)


class BoundServiceInput(BaseModel):
    service: str = Field(min_length=1, max_length=128)
    journal_lines: int = Field(default=100, ge=1, le=500)


class BoundProcessInput(BaseModel):
    sort_by: Literal["cpu", "memory"] = "cpu"
    limit: int = Field(default=25, ge=1, le=100)


class BoundFetchLogInput(BaseModel):
    path: str = Field(
        description="Absolute remote log path to copy into the configured local collection root."
    )


class NoToolInput(BaseModel):
    pass


@dataclass(frozen=True)
class BoundUnixTool:
    tool: StructuredTool
    status: ToolStatus


class UnixDiagnosticService:
    def __init__(
        self,
        settings: Settings,
        hosts: dict[str, dict[str, Any]] | None = None,
    ):
        self.settings = settings
        self.hosts = hosts if hosts is not None else settings.unix_hosts

    def _remote_config(self, alias: str) -> dict[str, Any]:
        config = self.hosts.get(alias)
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

        password = config.get("password") or None
        password_env = config.get("password_env")
        if password_env:
            password = os.getenv(str(password_env))
            if password is None:
                raise ToolAccessError(
                    f"Required SSH password environment variable '{password_env}' is not set."
                )

        async def connect_and_run() -> str:
            async with asyncssh.connect(
                config["host"],
                port=int(config.get("port", 22)),
                username=config["username"],
                password=password,
                client_keys=client_keys,
                known_hosts=str(Path(config["known_hosts"]).expanduser()),
            ) as connection:
                result = await connection.run(command, check=False)
                output_limit = self.settings.diagnostics_max_log_bytes
                stdout = result.stdout
                stderr = result.stderr
                stdout_truncated = len(stdout.encode("utf-8")) > output_limit
                stderr_truncated = len(stderr.encode("utf-8")) > output_limit
                return json_result(
                    host=alias,
                    exit_status=result.exit_status,
                    stdout=stdout[-output_limit:],
                    stderr=stderr[-output_limit:],
                    stdout_truncated=stdout_truncated,
                    stderr_truncated=stderr_truncated,
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

    async def disk_usage(self, host: str = "local") -> str:
        arguments = {"host": host}

        async def execute() -> str:
            if host != "local":
                return await self._run_remote(
                    host,
                    "LC_ALL=C df -hP -x tmpfs -x devtmpfs 2>/dev/null || "
                    "LC_ALL=C df -hP",
                )

            def collect() -> str:
                disks: list[dict[str, Any]] = []
                seen: set[str] = set()
                for partition in psutil.disk_partitions(all=False):
                    if partition.mountpoint in seen:
                        continue
                    seen.add(partition.mountpoint)
                    try:
                        usage = psutil.disk_usage(partition.mountpoint)
                    except (OSError, PermissionError):
                        continue
                    disks.append(
                        {
                            "device": partition.device,
                            "mountpoint": partition.mountpoint,
                            "filesystem": partition.fstype,
                            "total": usage.total,
                            "used": usage.used,
                            "free": usage.free,
                            "percent": usage.percent,
                        }
                    )
                return json_result(host="local", filesystems=disks)

            return await asyncio.to_thread(collect)

        return await observed_tool_call(
            "unix_disk_usage",
            arguments,
            execute,
            include_content=self.settings.log_include_content,
        )

    async def processes(
        self,
        host: str = "local",
        sort_by: Literal["cpu", "memory"] = "cpu",
        limit: int = 25,
    ) -> str:
        arguments = {"host": host, "sort_by": sort_by, "limit": limit}

        async def execute() -> str:
            if host != "local":
                sort_field = PROCESS_SORT_FIELDS[sort_by]
                command = (
                    "LC_ALL=C ps -eo pid,ppid,user,%cpu,%mem,etime,state,comm "
                    f"--sort=-{sort_field} 2>/dev/null | head -n {int(limit) + 1}"
                )
                return await self._run_remote(host, command)

            def collect() -> str:
                rows: list[dict[str, Any]] = []
                for process in psutil.process_iter(
                    [
                        "pid",
                        "ppid",
                        "username",
                        "name",
                        "cpu_percent",
                        "memory_percent",
                        "status",
                        "create_time",
                    ]
                ):
                    try:
                        rows.append(process.info)
                    except (psutil.AccessDenied, psutil.NoSuchProcess):
                        continue
                key = "cpu_percent" if sort_by == "cpu" else "memory_percent"
                rows.sort(
                    key=lambda item: float(item.get(key) or 0),
                    reverse=True,
                )
                return json_result(
                    host="local",
                    sort_by=sort_by,
                    processes=rows[:limit],
                )

            return await asyncio.to_thread(collect)

        return await observed_tool_call(
            "unix_processes",
            arguments,
            execute,
            include_content=self.settings.log_include_content,
        )

    async def fetch_log(self, path: str, host: str) -> str:
        if host == "local":
            raise ToolAccessError("SCP log collection requires a configured remote host.")
        validated = self._remote_path(host, path)
        arguments = {"host": host, "path": path}

        async def execute() -> str:
            config = self._remote_config(host)
            client_keys = [
                str(Path(key).expanduser()) for key in config.get("client_keys", [])
            ] or None
            password = config.get("password") or None
            password_env = config.get("password_env")
            if password_env:
                password = os.getenv(str(password_env))
                if password is None:
                    raise ToolAccessError(
                        f"Required SSH password environment variable '{password_env}' is not set."
                    )

            async def transfer() -> str:
                async with asyncssh.connect(
                    config["host"],
                    port=int(config.get("port", 22)),
                    username=config["username"],
                    password=password,
                    client_keys=client_keys,
                    known_hosts=str(Path(config["known_hosts"]).expanduser()),
                ) as connection:
                    sftp = await connection.start_sftp_client()
                    attributes = await sftp.stat(validated)
                    remote_size = int(attributes.size or 0)
                    max_bytes = self.settings.diagnostics_scp_max_bytes
                    offset = max(0, remote_size - max_bytes)
                    async with sftp.open(validated, "rb") as remote_file:
                        if offset:
                            await remote_file.seek(offset)
                        content = await remote_file.read(max_bytes)

                collected_root = self.settings.log_download_dir.resolve()
                target_directory = (collected_root / host).resolve()
                if (
                    target_directory != collected_root
                    and collected_root not in target_directory.parents
                ):
                    raise ToolAccessError("Computed log collection path escaped its root.")
                timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
                safe_name = re.sub(
                    r"[^A-Za-z0-9_.-]",
                    "_",
                    PurePosixPath(validated).name,
                )
                target = target_directory / f"{timestamp}-{safe_name}"

                def save() -> None:
                    target_directory.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)

                await asyncio.to_thread(save)
                return json_result(
                    host=host,
                    remote_path=validated,
                    local_path=str(target),
                    remote_bytes=remote_size,
                    copied_bytes=len(content),
                    truncated=offset > 0,
                )

            return await asyncio.wait_for(
                transfer(),
                timeout=self.settings.diagnostics_tool_timeout_seconds,
            )

        return await observed_tool_call(
            "unix_fetch_log",
            arguments,
            execute,
            include_content=self.settings.log_include_content,
        )

    async def tail_log(self, path: str, host: str = "local", lines: int = 200) -> str:
        arguments = {"host": host, "path": path, "lines": lines}

        async def execute() -> str:
            if host != "local":
                validated = self._remote_path(host, path)
                command = (
                    f"tail -c {self.settings.diagnostics_max_log_bytes} -- "
                    f"{shlex.quote(validated)} | tail -n {int(lines)}"
                )
                return await self._run_remote(host, command)

            validated = self._local_path(path)

            def read_tail() -> str:
                if not validated.is_file():
                    raise ToolAccessError(f"Log file '{path}' does not exist.")
                file_bytes = validated.stat().st_size
                offset = max(0, file_bytes - self.settings.diagnostics_max_log_bytes)
                with validated.open("rb") as handle:
                    handle.seek(offset)
                    raw = handle.read(self.settings.diagnostics_max_log_bytes)
                text = raw.decode("utf-8", errors="replace")
                if offset and "\n" in text:
                    text = text.split("\n", 1)[1]
                content = "".join(deque(text.splitlines(keepends=True), maxlen=lines))
                return json_result(
                    host="local",
                    path=str(validated),
                    lines=lines,
                    content=content,
                    file_bytes=file_bytes,
                    read_bytes=len(raw),
                    truncated=offset > 0,
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
                    f"tail -c {self.settings.diagnostics_max_log_bytes} -- "
                    f"{shlex.quote(validated)} | grep -F -i -n -m {int(max_matches)} "
                    f"-- {shlex.quote(query)}"
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
                coroutine=self.disk_usage,
                name="unix_disk_usage",
                description=(
                    "Inspect mounted filesystems and disk utilization on local or configured "
                    "Unix hosts using a fixed read-only operation."
                ),
                args_schema=UnixHostInput,
                handle_tool_error=tool_error_result,
            ),
            StructuredTool.from_function(
                coroutine=self.processes,
                name="unix_processes",
                description=(
                    "List a bounded number of processes sorted by CPU or memory on local or "
                    "configured Unix hosts. No arbitrary process command is accepted."
                ),
                args_schema=UnixProcessInput,
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

    def bound_tools(self, alias: str) -> list[BoundUnixTool]:
        config = self._remote_config(alias)
        prefix = re.sub(r"[^A-Za-z0-9_]", "_", alias)
        configured_capabilities = config.get(
            "capabilities",
            ["snapshot", "disks", "processes", "tail", "search", "fetch", "service"],
        )
        capabilities = {str(value) for value in configured_capabilities}
        tools: list[BoundUnixTool] = []

        def add(
            *,
            capability: str,
            suffix: str,
            description: str,
            coroutine: Any,
            args_schema: type[BaseModel],
            access: Literal["read-only", "local-copy"] = "read-only",
        ) -> None:
            if capability not in capabilities:
                return
            name = f"{prefix}_{suffix}"
            tool = StructuredTool.from_function(
                coroutine=coroutine,
                name=name,
                description=f"{description} The target is fixed to SSH host alias '{alias}'.",
                args_schema=args_schema,
                handle_tool_error=tool_error_result,
            )
            tools.append(
                BoundUnixTool(
                    tool=tool,
                    status=ToolStatus(
                        name=name,
                        category="unix",
                        enabled=True,
                        access=access,
                        detail=f"SSH host alias: {alias}. Capability: {capability}.",
                    ),
                )
            )

        async def snapshot() -> str:
            return await self.system_snapshot(host=alias)

        async def disks() -> str:
            return await self.disk_usage(host=alias)

        async def process_list(
            sort_by: Literal["cpu", "memory"] = "cpu",
            limit: int = 25,
        ) -> str:
            return await self.processes(host=alias, sort_by=sort_by, limit=limit)

        async def tail(path: str, lines: int = 200) -> str:
            return await self.tail_log(path=path, host=alias, lines=lines)

        async def search(
            path: str,
            query: str,
            max_matches: int = 100,
        ) -> str:
            return await self.search_log(
                path=path,
                query=query,
                host=alias,
                max_matches=max_matches,
            )

        async def fetch(path: str) -> str:
            return await self.fetch_log(path=path, host=alias)

        async def service(
            service: str,
            journal_lines: int = 100,
        ) -> str:
            return await self.service_status(
                service=service,
                host=alias,
                journal_lines=journal_lines,
            )

        add(
            capability="snapshot",
            suffix="system_snapshot",
            description="Collect uptime, load, memory, disk, and top-process evidence.",
            coroutine=snapshot,
            args_schema=NoToolInput,
        )
        add(
            capability="disks",
            suffix="disk_usage",
            description="Inspect all mounted filesystem utilization.",
            coroutine=disks,
            args_schema=NoToolInput,
        )
        add(
            capability="processes",
            suffix="processes",
            description="List a bounded set of processes sorted by CPU or memory.",
            coroutine=process_list,
            args_schema=BoundProcessInput,
        )
        add(
            capability="tail",
            suffix="tail_log",
            description="Read a bounded tail from an allowed remote log path.",
            coroutine=tail,
            args_schema=BoundLogInput,
        )
        add(
            capability="search",
            suffix="search_log",
            description="Search a bounded log window for literal text.",
            coroutine=search,
            args_schema=BoundLogSearchInput,
        )
        add(
            capability="fetch",
            suffix="fetch_log",
            description=(
                "Copy the bounded trailing portion of an allowed remote log into the "
                "server-side collection directory."
            ),
            coroutine=fetch,
            args_schema=BoundFetchLogInput,
            access="local-copy",
        )
        add(
            capability="service",
            suffix="service_status",
            description="Inspect systemd status and recent journal entries.",
            coroutine=service,
            args_schema=BoundServiceInput,
        )
        return tools
