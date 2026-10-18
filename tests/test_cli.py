import json

import pytest

from signal_engine import __version__
from signal_engine.__main__ import main


def test_version_logs_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    obj = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert obj["version"] == __version__


def test_unknown_flag_exits_2() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--nope"])
    assert exc.value.code == 2
