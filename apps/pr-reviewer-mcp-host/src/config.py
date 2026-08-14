from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from loguru import logger
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


APP_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = APP_ROOT / ".env"
DEFAULT_DELIVERY_DB = APP_ROOT / "webhook_deliveries.sqlite3"


class Settings(BaseSettings):
    """Runtime configuration for the automated pull-request reviewer."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    OPENAI_API_KEY: str = Field(description="OpenAI Platform API key.")
    OPENAI_MODEL: str = Field(
        default="gpt-5.6",
        description="Responses API model used for pull-request reviews.",
    )
    OPENAI_TIMEOUT_SECONDS: float = Field(default=180.0, gt=0)

    TOOL_REGISTRY_URL: str = Field(
        description="Public HTTPS URL of the remote MCP tool registry."
    )
    MCP_AUTHORIZATION: str = Field(
        default="",
        description="Optional OAuth access token for the remote MCP registry.",
    )
    SLACK_CHANNEL_ID: str = Field(
        description="Slack channel ID that receives pull-request reviews."
    )

    GITHUB_WEBHOOK_SECRET: str = Field(
        description="Secret configured on the GitHub repository webhook."
    )
    WEBHOOK_DELIVERY_DB: str = Field(default=str(DEFAULT_DELIVERY_DB))
    WEBHOOK_MAX_ATTEMPTS: int = Field(default=3, ge=1, le=10)
    WEBHOOK_RETRY_BASE_SECONDS: float = Field(default=2.0, ge=0, le=60)
    WEBHOOK_PROCESSING_STALE_SECONDS: int = Field(default=900, ge=60)

    @field_validator(
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "TOOL_REGISTRY_URL",
        "SLACK_CHANNEL_ID",
        "GITHUB_WEBHOOK_SECRET",
    )
    @classmethod
    def check_not_empty(cls, value: str, info) -> str:
        if not value or not value.strip():
            logger.error("{} cannot be empty.", info.field_name)
            raise ValueError(f"{info.field_name} cannot be empty.")
        return value.strip()

    @field_validator("TOOL_REGISTRY_URL")
    @classmethod
    def check_registry_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("TOOL_REGISTRY_URL must be an absolute HTTP(S) URL.")
        if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError(
                "TOOL_REGISTRY_URL must be reachable by OpenAI; localhost URLs are not supported."
            )
        return value.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
