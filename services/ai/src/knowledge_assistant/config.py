"""Validated service settings; secrets are never returned by the status API."""

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
