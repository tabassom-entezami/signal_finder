"""The host list fails closed: a missing or broken list refuses every request before any network call."""

import logging
import urllib.request
from collections.abc import Iterator
from email.message import Message
from pathlib import Path

import pytest

from signal_engine.data import http

ENV = "SIGNAL_ENGINE_HOST_LIST"  # literal on purpose: the tests must not depend on the module's constant
URL = "https://example.org/x"
VALID = "host,list,source\nexample.org,allow,test\ndenied.test,deny,test\n"


@pytest.fixture
def hosts_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "hosts.csv"
    monkeypatch.setattr(http, "HOSTS_FILE", path)
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.setattr(http, "_opt_out_warned", False, raising=False)

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("urlopen called although the host list should have refused the request")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    http._hosts.cache_clear()
    yield path
    http._hosts.cache_clear()


def _refused(url: str = URL) -> None:
    with pytest.raises(PermissionError, match=ENV):
        http.get(url)


def test_missing_file_refuses_every_request(hosts_file: Path) -> None:
    _refused()
    with pytest.raises(PermissionError, match="missing"):
        http._check(URL)


@pytest.mark.parametrize("content", [b"", b"host,list,source\n", b"host,list\n,allow\n"])
def test_empty_or_header_only_file_refuses(hosts_file: Path, content: bytes) -> None:
    hosts_file.write_bytes(content)
    _refused()


def test_directory_in_place_of_the_file_refuses(hosts_file: Path) -> None:
    hosts_file.mkdir()
    _refused()


def test_non_utf8_file_refuses(hosts_file: Path) -> None:
    hosts_file.write_bytes(b"\xff\xfehost,list\n")
    _refused()


def test_header_without_list_column_refuses(hosts_file: Path) -> None:
    hosts_file.write_text("host,source\nexample.org,test\n", encoding="utf-8")
    _refused()


def test_valid_file_keeps_the_deny_rules(hosts_file: Path) -> None:
    hosts_file.write_text(VALID, encoding="utf-8")
    http._check(URL)
    for url in ("https://denied.test/x", "https://a.denied.test/x", "https://denied.test./x"):
        with pytest.raises(PermissionError, match="deny list"):
            http.get(url)


def test_opt_out_covers_a_missing_file_and_warns_once(
    hosts_file: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(ENV, "none")
    with caplog.at_level(logging.WARNING, logger=http.__name__):
        http._check(URL)
        http._check(URL)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and ENV in r.getMessage()]
    assert len(warnings) == 1


@pytest.mark.parametrize("value", ["", "0", "NONE", " none"])
def test_opt_out_needs_the_exact_value(hosts_file: Path, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(ENV, value)
    _refused()


def test_opt_out_is_read_at_call_time(hosts_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, "none")
    http._check(URL)
    monkeypatch.delenv(ENV)
    _refused()


@pytest.mark.parametrize("content", [b"", b"host,list,source\n", b"\xff\xfe"])
def test_opt_out_never_covers_a_broken_file(
    hosts_file: Path, monkeypatch: pytest.MonkeyPatch, content: bytes
) -> None:
    hosts_file.write_bytes(content)
    monkeypatch.setenv(ENV, "none")
    _refused()


def test_repaired_file_works_without_a_restart(hosts_file: Path) -> None:
    _refused()
    hosts_file.write_text(VALID, encoding="utf-8")
    http._check(URL)


def test_redirect_with_a_missing_file_is_refused(hosts_file: Path) -> None:
    request = urllib.request.Request(URL)
    with pytest.raises(PermissionError, match=ENV):
        http._CheckedRedirects().redirect_request(  # type: ignore[no-untyped-call]
            request, None, 302, "Found", Message(), "https://example.org/y"
        )
