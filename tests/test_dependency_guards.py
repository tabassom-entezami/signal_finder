"""Dependency guards over uv.lock: licence policy and a reviewed-name allowlist.

uv.lock carries no licence data, so licences are read from the installed metadata of the synced env
(License-Expression, License, "License ::" classifiers). `uv lock --check` and pip-audit run as CI steps.
"""

import importlib.metadata as md
import re
import tomllib
from pathlib import Path

import pytest

LOCK = Path(__file__).resolve().parents[1] / "uv.lock"
# GPL / AGPL (copyleft over the whole work), Commons Clause and SSPL are forbidden; LGPL is not matched.
FORBIDDEN = re.compile(r"\bA?GPL|GNU (Affero )?General Public|Commons Clause|SSPL|Server Side Public", re.I)
NO_LICENCE_OK = {"signal-engine"}  # this project itself
# Every locked name was reviewed by hand (typo-squat guard). A new dependency fails here until it is
# checked (spelling, maintainer, licence) and added.
REVIEWED = {
    "aiohappyeyeballs", "aiohttp", "aiohttp-fast-zlib", "aiosignal", "annotated-types", "ast-serialize",
    "attrs", "ccxt", "certifi", "cffi", "charset-normalizer", "cloudpickle", "coincurve", "colorama",
    "cryptography", "duckdb", "frozenlist", "hypothesis", "idna", "iniconfig", "joblib", "librt", "multidict",
    "mypy", "mypy-extensions", "narwhals", "numpy", "orjson", "packaging", "pandas", "pathspec", "pluggy",
    "propcache", "purgedcv", "pyarrow", "pycparser", "pydantic", "pydantic-core", "pydantic-settings",
    "pygments", "pytest", "python-dateutil", "python-dotenv", "requests", "ruff", "scikit-learn", "scipy",
    "signal-engine", "six", "sortedcontainers", "threadpoolctl", "typing-extensions", "typing-inspection",
    "tzdata", "urllib3", "uvloop", "winloop", "yarl", "zlib-ng",
}


def locked_names() -> list[str]:
    return [str(p["name"]) for p in tomllib.loads(LOCK.read_text())["package"]]


def licence_text(meta: md.PackageMetadata) -> str:
    first_line = (meta.get("License") or "").splitlines()[:1]
    classifiers = [c for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
    return " | ".join(x for x in [meta.get("License-Expression") or "", *first_line, *classifiers] if x)


def forbidden(text: str) -> bool:
    return bool(FORBIDDEN.search(text))


@pytest.mark.parametrize(
    ("text", "bad"),
    [
        ("GPL-3.0-or-later", True),
        ("AGPL-3.0-only", True),
        ("License :: OSI Approved :: GNU General Public License v3 (GPLv3)", True),
        ("MIT AND Commons Clause", True),
        ("LGPL-2.1-or-later", False),
        ("License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)", False),
        ("MPL-2.0 AND (Apache-2.0 OR MIT)", False),
        ("BSD-3-Clause", False),
    ],
)
def test_licence_policy(text: str, bad: bool) -> None:
    assert forbidden(text) is bad


def test_every_locked_name_was_reviewed() -> None:
    new = sorted(set(locked_names()) - REVIEWED)
    assert not new, f"review these new dependencies, then add them to REVIEWED: {new}"


def test_installed_dependencies_have_allowed_licences() -> None:
    bad, unknown = [], []
    for name in locked_names():
        try:
            meta = md.metadata(name)
        except md.PackageNotFoundError:
            continue  # platform-only package (e.g. winloop on Windows): not installed here
        text = licence_text(meta)
        if forbidden(text):
            bad.append(f"{name}: {text}")
        elif not text and name not in NO_LICENCE_OK:
            unknown.append(name)
    assert not bad, f"forbidden licence: {bad}"
    assert not unknown, f"no licence metadata, review by hand: {unknown}"
