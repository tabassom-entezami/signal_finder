from pathlib import Path

from signal_engine.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


def test_env_example_lists_every_setting() -> None:
    keys = {
        line.split("=", 1)[0].strip()
        for line in (ROOT / ".env.example").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert keys == {name.upper() for name in Settings.model_fields}
