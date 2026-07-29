import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    lmstudio_base_url: str = "http://127.0.0.1:1234/v1"
    lmstudio_api_key: str = "lm-studio"
    lmstudio_model: str = ""
    agent_temperature: float = Field(default=0.35, ge=0, le=2)
    agent_max_tokens: int = Field(default=1800, ge=128, le=32768)
    agent_max_revisions: int = Field(default=1, ge=0, le=3)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    diagnostics_enabled: bool = True
    diagnostics_max_iterations: int = Field(default=8, ge=1, le=20)
    diagnostics_max_configured_tools: int = Field(default=64, ge=1, le=256)
    diagnostics_tool_timeout_seconds: float = Field(default=30, ge=1, le=300)
    diagnostics_rest_allowed_hosts: str = "localhost,127.0.0.1"
    diagnostics_rest_max_response_bytes: int = Field(
        default=1_048_576, ge=1024, le=10_485_760
    )
    diagnostics_rest_headers_json: str = "{}"
    diagnostics_rest_tools_csv: str = "config/rest-tools.csv"
    diagnostics_rest_template_root: str = "config/rest-templates"
    oracle_dsn: str = ""
    oracle_user: str = ""
    oracle_password: str = ""
    oracle_max_rows: int = Field(default=200, ge=1, le=5000)
    diagnostics_local_log_roots: str = "logs,/var/log"
    diagnostics_max_log_bytes: int = Field(default=2_097_152, ge=4096, le=20_971_520)
    diagnostics_unix_hosts_json: str = "{}"
    diagnostics_unix_hosts_csv: str = "config/unix-hosts.csv"
    diagnostics_log_download_dir: str = "logs/collected"
    diagnostics_scp_max_bytes: int = Field(
        default=10_485_760, ge=4096, le=104_857_600
    )
    log_level: str = "DEBUG"
    log_file: str = "logs/agentic-flow.log"
    log_max_bytes: int = Field(default=10_485_760, ge=1024)
    log_backup_count: int = Field(default=5, ge=1, le=100)
    log_include_content: bool = True

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def rest_allowed_hosts(self) -> list[str]:
        return [
            host.strip().lower()
            for host in self.diagnostics_rest_allowed_hosts.split(",")
            if host.strip()
        ]

    @property
    def rest_headers(self) -> dict[str, str]:
        parsed = json.loads(self.diagnostics_rest_headers_json)
        if not isinstance(parsed, dict):
            raise ValueError("DIAGNOSTICS_REST_HEADERS_JSON must contain a JSON object.")
        return {str(key): str(value) for key, value in parsed.items()}

    @property
    def local_log_roots(self) -> list[Path]:
        roots: list[Path] = []
        for value in self.diagnostics_local_log_roots.split(","):
            if not value.strip():
                continue
            path = Path(value.strip()).expanduser()
            roots.append(path if path.is_absolute() else BACKEND_DIR / path)
        return roots

    @property
    def unix_hosts(self) -> dict[str, dict[str, Any]]:
        parsed = json.loads(self.diagnostics_unix_hosts_json)
        if not isinstance(parsed, dict):
            raise ValueError("DIAGNOSTICS_UNIX_HOSTS_JSON must contain a JSON object.")
        return parsed

    @property
    def oracle_configured(self) -> bool:
        return all((self.oracle_dsn, self.oracle_user, self.oracle_password))

    def backend_path(self, value: str) -> Path:
        configured = Path(value).expanduser()
        return configured if configured.is_absolute() else BACKEND_DIR / configured

    @property
    def rest_tools_csv_path(self) -> Path:
        return self.backend_path(self.diagnostics_rest_tools_csv)

    @property
    def rest_template_root(self) -> Path:
        return self.backend_path(self.diagnostics_rest_template_root)

    @property
    def unix_hosts_csv_path(self) -> Path:
        return self.backend_path(self.diagnostics_unix_hosts_csv)

    @property
    def log_download_dir(self) -> Path:
        return self.backend_path(self.diagnostics_log_download_dir)

    @property
    def log_path(self) -> Path:
        return self.backend_path(self.log_file)


@lru_cache
def get_settings() -> Settings:
    return Settings()
