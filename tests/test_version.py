import re

from signal_engine import __version__


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
