"""Runtime settings, read from environment variables and an optional `.env` file."""

from pathlib import Path
from typing import Any, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """All settings are optional so the default (no `.env`) path always works."""

    # an empty value (e.g. `.env.example` copied unchanged) means "not set", so the default applies
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    log_level: LogLevel = "INFO"
    data_dir: Path = Path("data")
    # The owner's own account values: kept only in `.env`, never in code. Needed by the loss Monte Carlo
    # and its gates; every other command works without them.
    stake_usd: float | None = None
    p98_max_usd: float | None = None
    savings_pct: float | None = None
    paper_cash_eur: float | None = None  # starting cash of the paper account
    # [annual probability, loss low, loss high] as a share of the stake, one row per operational event
    op_events: list[tuple[float, float, float]] | None = None
    # Default owners of rows the records store writes on its own: agent runs and mirrored gate-log lines
    run_owner: str | None = None
    gate_owner: str | None = None
    # Records store files, relative to the repo root: the run-manifest folder and the rendered Markdown files
    records_manifest_dir: Path = Path("manifests")
    records_render_files: list[Path] = []


def load_settings() -> Settings:
    return Settings()


def required(name: str) -> Any:
    """One of the owner's own values; refuses with the variable to set instead of guessing a default."""
    value = getattr(load_settings(), name)
    if value is None:
        raise ValueError(f"set {name.upper()} in .env")
    return value
