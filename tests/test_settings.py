from pathlib import Path

import pytest
from pydantic import ValidationError

from signal_engine.settings import Settings


def test_defaults_without_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here: the default path must work
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)
    s = Settings()
    assert s.log_level == "INFO"
    assert s.data_dir == Path("data")


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    assert Settings().log_level == "DEBUG"


def test_invalid_log_level_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "LOUD")
    with pytest.raises(ValidationError):
        Settings()
