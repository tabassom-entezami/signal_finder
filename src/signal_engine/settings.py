"""Runtime settings, read from environment variables and an optional `.env` file."""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """All settings are optional so the default (no `.env`) path always works."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    log_level: LogLevel = "INFO"
    data_dir: Path = Path("data")


def load_settings() -> Settings:
    return Settings()
