from pathlib import Path

import pytest

from signal_engine.settings import Settings, required

ROOT = Path(__file__).resolve().parents[1]


def test_env_example_lists_every_setting() -> None:
    keys = {
        line.split("=", 1)[0].strip()
        for line in (ROOT / ".env.example").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert keys == {name.upper() for name in Settings.model_fields}


def test_env_example_copied_unchanged_loads_the_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text((ROOT / ".env.example").read_text())
    monkeypatch.chdir(tmp_path)
    s = Settings()
    assert s.log_level == "INFO" and s.stake_usd is None and s.records_render_files == []
    with pytest.raises(ValueError, match="set STAKE_USD in .env"):
        required("stake_usd")
