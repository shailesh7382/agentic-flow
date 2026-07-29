import csv
import re
from pathlib import Path
from typing import Any

from ..config import Settings

HOST_ALIAS_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,31}$")
ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ALLOWED_CAPABILITIES = {
    "snapshot",
    "disks",
    "processes",
    "tail",
    "search",
    "fetch",
    "service",
}


def _parse_bool(value: str, *, row: int) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"Invalid enabled value '{value}' on Unix hosts CSV row {row}.")


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(";") if item.strip()]


def _resolve_paths(settings: Settings, values: list[str]) -> list[str]:
    return [str(settings.backend_path(value).resolve()) for value in values]


def load_unix_hosts(settings: Settings) -> dict[str, dict[str, Any]]:
    hosts = {name: dict(config) for name, config in settings.unix_hosts.items()}
    path = settings.unix_hosts_csv_path
    if not path.exists():
        return hosts
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "name",
            "host",
            "port",
            "username",
            "client_keys",
            "known_hosts",
            "password_env",
            "log_roots",
            "capabilities",
            "enabled",
        }
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"Unix hosts CSV is missing columns: {', '.join(sorted(missing))}."
            )
        for row_number, row in enumerate(reader, start=2):
            if not _parse_bool(row.get("enabled", ""), row=row_number):
                continue
            name = row.get("name", "").strip()
            if not HOST_ALIAS_PATTERN.fullmatch(name):
                raise ValueError(
                    f"Unix host alias '{name}' on row {row_number} must match "
                    "[A-Za-z][A-Za-z0-9_-]{1,31}."
                )
            if name in hosts:
                raise ValueError(f"Duplicate Unix host alias '{name}' on row {row_number}.")
            hostname = row.get("host", "").strip()
            username = row.get("username", "").strip()
            known_hosts = row.get("known_hosts", "").strip()
            log_roots = _split(row.get("log_roots", ""))
            if not hostname or not username or not known_hosts or not log_roots:
                raise ValueError(
                    f"Unix host '{name}' requires host, username, known_hosts, and log_roots."
                )
            port = int(row.get("port", "").strip() or "22")
            if not 1 <= port <= 65535:
                raise ValueError(f"Unix host '{name}' port must be between 1 and 65535.")
            password_env = row.get("password_env", "").strip()
            if password_env and not ENVIRONMENT_NAME_PATTERN.fullmatch(password_env):
                raise ValueError(
                    f"Unix host '{name}' has invalid password_env '{password_env}'."
                )
            capabilities = set(
                _split(row.get("capabilities", ""))
                or sorted(ALLOWED_CAPABILITIES)
            )
            unknown = capabilities.difference(ALLOWED_CAPABILITIES)
            if unknown:
                raise ValueError(
                    f"Unix host '{name}' has unknown capabilities: "
                    f"{', '.join(sorted(unknown))}."
                )
            hosts[name] = {
                "host": hostname,
                "port": port,
                "username": username,
                "client_keys": _resolve_paths(
                    settings,
                    _split(row.get("client_keys", "")),
                ),
                "known_hosts": str(settings.backend_path(known_hosts).resolve()),
                "password_env": password_env or None,
                "log_roots": log_roots,
                "capabilities": sorted(capabilities),
                "source": str(Path(path).resolve()),
            }
    return hosts
