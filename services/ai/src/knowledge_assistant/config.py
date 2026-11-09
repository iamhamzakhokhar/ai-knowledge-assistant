"""Validated service settings; secrets are never returned by the status API."""

from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Environment configuration shared by local development and deployed workers."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    app_env: Literal["development", "test", "production"] = Field(
        default="development", validation_alias="APP_ENV"
    )
    allowed_origins: Annotated[tuple[str, ...], NoDecode] = Field(
        default=("http://localhost:3000",), validation_alias="CORS_ORIGINS"
    )
    database_host: str = Field(default="localhost", min_length=1, validation_alias="DATABASE_HOST")
    database_port: int = Field(default=5432, ge=1, le=65535, validation_alias="DATABASE_PORT")
    database_name: str = Field(
        default="knowledge_assistant", min_length=1, validation_alias="DATABASE_NAME"
    )
    database_user: str = Field(default="aka_app", min_length=1, validation_alias="DATABASE_USER")
    database_password_file: Path | None = Field(
        default=None, validation_alias="DATABASE_PASSWORD_FILE"
    )
    redis_host: str = Field(default="localhost", min_length=1, validation_alias="REDIS_HOST")
    redis_port: int = Field(default=6379, ge=1, le=65535, validation_alias="REDIS_PORT")
    redis_password_file: Path | None = Field(default=None, validation_alias="REDIS_PASSWORD_FILE")

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        """Read a simple comma-separated environment variable without loading .env files."""
        if isinstance(value, str):
            return tuple(origin.strip() for origin in value.split(",") if origin.strip())
        return value

    @field_validator("allowed_origins")
    @classmethod
    def validate_origins(cls, origins: tuple[str, ...]) -> tuple[str, ...]:
        """CORS takes exact origins, never wildcards, paths, or credentials."""
        for origin in origins:
            parsed = urlsplit(origin)
            try:
                port = parsed.port
            except ValueError as exc:
                raise ValueError("Each allowed origin must have a valid port") from exc
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or "*" in origin
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or (port is not None and port == 0)
            ):
                raise ValueError("Each allowed origin must be an exact HTTP(S) origin")
        return tuple(dict.fromkeys(origins))
