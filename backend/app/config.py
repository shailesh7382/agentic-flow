from functools import lru_cache
from pathlib import Path

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
    def log_path(self) -> Path:
        configured = Path(self.log_file).expanduser()
        return configured if configured.is_absolute() else BACKEND_DIR / configured


@lru_cache
def get_settings() -> Settings:
    return Settings()
