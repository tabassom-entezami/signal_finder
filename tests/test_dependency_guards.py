"""Dependency guards over uv.lock: licence policy and a reviewed-name allowlist.

uv.lock carries no licence data, so licences are read from the installed metadata of the synced env
(License-Expression, License, "License ::" classifiers). `uv lock --check` and pip-audit run as CI steps.
"""

import importlib.metadata as md
import re
import tomllib
from email.message import Message
from pathlib import Path
from typing import cast

import pytest

LOCK = Path(__file__).resolve().parents[1] / "uv.lock"
# GPL / AGPL (copyleft over the whole work), Commons Clause, SSPL and proprietary/BUSL terms are forbidden
# anywhere in the licence text, also next to an allowed classifier.
FORBIDDEN = re.compile(
    r"\bA?GPL|GNU (Affero )?General Public|Commons Clause|SSPL|Server Side Public"
    r"|Proprietary|BUSL|Business Source",
    re.I,
)
# Allow-list: every licence a dependency names must be one of these; anything else fails until reviewed.
# MPL-2.0 (file-level copyleft), PSF-2.0 and Zlib are already in the tree and were reviewed by hand.
ALLOWED_SPDX = {
    "MIT", "MIT-0", "BSD-2-Clause", "BSD-3-Clause", "0BSD", "Apache-2.0", "ISC", "CC0-1.0", "Unlicense",
    "MPL-2.0", "PSF-2.0", "Zlib",
}
# The same licences as written in "License ::" classifiers and the free-text License field.
ALLOWED_NAME = re.compile(
    r"MIT( License)?|MIT-0|BSD( License)?|BSD[- ][23]-Clause( License)?|0BSD"
    r"|Apache( Software License| License,? 2\.0| 2\.0|-2\.0)|ISC( License \(ISCL\))?"
    r"|(The )?Unlicense( \(Unlicense\))?|CC0 1\.0 Universal \(CC0 1\.0\) Public Domain Dedication"
    r"|Mozilla Public License 2\.0 \(MPL 2\.0\)|Python Software Foundation License|zlib/libpng License",
    re.I,
)
SPDX_OPERATORS = {"AND", "OR", "WITH"}
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


def licence_terms(meta: md.PackageMetadata) -> list[str]:
    """SPDX ids of License-Expression, else the classifier names, else the first line of License."""
    expression = meta.get("License-Expression") or ""
    if expression:
        return [t for t in re.findall(r"[A-Za-z0-9.+-]+", expression) if t.upper() not in SPDX_OPERATORS]
    classifiers = [c for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
    names = [c.rsplit("::", 1)[1].strip() for c in classifiers]
    names = [n for n in names if n != "OSI Approved"]  # the bare parent classifier names no licence
    if names:
        return names
    return [line.strip() for line in (meta.get("License") or "").splitlines()[:1] if line.strip()]


def term_allowed(term: str) -> bool:
    return term in ALLOWED_SPDX or bool(ALLOWED_NAME.fullmatch(term))


def fake_meta(**fields: str | list[str]) -> md.PackageMetadata:
    msg = Message()
    for key, value in fields.items():
        for v in value if isinstance(value, list) else [value]:
            msg[key.replace("_", "-")] = v
    return cast(md.PackageMetadata, msg)


@pytest.mark.parametrize(
    ("text", "bad"),
    [
        ("GPL-3.0-or-later", True),
        ("AGPL-3.0-only", True),
        ("License :: OSI Approved :: GNU General Public License v3 (GPLv3)", True),
        ("MIT AND Commons Clause", True),
        ("Proprietary | License :: OSI Approved :: MIT License", True),
        ("BUSL-1.1", True),
        ("MPL-2.0 AND (Apache-2.0 OR MIT)", False),
        ("BSD-3-Clause", False),
    ],
)
def test_licence_policy(text: str, bad: bool) -> None:
    assert forbidden(text) is bad


@pytest.mark.parametrize(
    ("term", "ok"),
    [
        ("MIT", True),
        ("CC0-1.0", True),
        ("Unlicense", True),
        ("0BSD", True),
        ("MIT-0", True),
        ("Apache Software License", True),
        ("Mozilla Public License 2.0 (MPL 2.0)", True),
        ("Python Software Foundation License", True),
        ("zlib/libpng License", True),
        ("Other/Proprietary License", False),
        ("Proprietary", False),
        ("BUSL-1.1", False),
        ("LGPL-2.1-or-later", False),
        ("SSPL-1.0", False),
        ("BSD-4-Clause", False),
        ("BSD-4-Clause-UC", False),
        ("Apache-1.1", False),
        ("Apache License 1.0", False),
        ("MIT AND Proprietary", False),
        ("MITproprietary", False),
        ("CC0 1.0 Universal (CC0 1.0) Public Domain Dedication", True),
    ],
)
def test_licence_allow_list(term: str, ok: bool) -> None:
    assert term_allowed(term) is ok


@pytest.mark.parametrize(
    ("meta", "terms"),
    [
        (fake_meta(License_Expression="MPL-2.0 AND (Apache-2.0 OR MIT)"), ["MPL-2.0", "Apache-2.0", "MIT"]),
        (
            fake_meta(License="Copyright (c) 2001 Someone", Classifier="License :: OSI Approved :: BSD"),
            ["BSD"],
        ),
        (fake_meta(License="BUSL-1.1"), ["BUSL-1.1"]),
        (fake_meta(Classifier="License :: Other/Proprietary License"), ["Other/Proprietary License"]),
        (fake_meta(Classifier="Programming Language :: Python"), []),
    ],
)
def test_licence_terms(meta: md.PackageMetadata, terms: list[str]) -> None:
    assert licence_terms(meta) == terms


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
        terms = licence_terms(meta)
        if forbidden(text) or not all(term_allowed(t) for t in terms):
            bad.append(f"{name}: {text}")
        elif not terms and name not in NO_LICENCE_OK:
            unknown.append(name)
    assert not bad, f"licence not on the allow-list: {bad}"
    assert not unknown, f"no licence metadata, review by hand: {unknown}"
