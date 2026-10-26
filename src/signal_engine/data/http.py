"""Plain HTTPS GET with a timeout and a short retry on transient errors (stdlib only, no key)."""

import csv
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import cache
from pathlib import Path
from typing import Protocol

HOSTS_FILE = Path(__file__).resolve().parents[3] / "ops/network/hosts.csv"  # one list for sandbox and jobs

TIMEOUT_S = 30.0
MAX_RETRIES = 3  # retries after the first attempt, on network errors and HTTP 5xx only
BACKOFF_S = 1.0  # sleeps 1 s, 2 s, 4 s
USER_AGENT = "signal-engine/0.1 (research; public data)"

log = logging.getLogger(__name__)
_sleep = time.sleep  # patched in tests


class HttpGet(Protocol):
    def __call__(self, url: str) -> bytes: ...


@cache
def _hosts(kind: str) -> frozenset[str]:
    if not HOSTS_FILE.exists():
        return frozenset()
    with HOSTS_FILE.open(encoding="utf-8") as fh:
        return frozenset(r["host"].lower().rstrip(".") for r in csv.DictReader(fh) if r.get("list") == kind)


def denied_hosts() -> frozenset[str]:
    """Hosts the host registry stops (`list=deny` in ops/network/hosts.csv)."""
    return _hosts("deny")


def research_hosts() -> frozenset[str]:
    """Hosts open for reading public data only (`list=research`): no account, order or login paths."""
    return _hosts("research")


# account and money paths stay closed on research hosts (opinions/bitpin-research-access-2026-10-26.md)
RESEARCH_PATH_BLOCK = re.compile(
    r"order(?!book)|account|withdraw|deposit|wallet|user|auth|login|transfer", re.IGNORECASE
)


def _matches(host: str, listed: frozenset[str]) -> bool:
    bare = host.removeprefix("www.")
    for entry in listed:
        root = entry.removeprefix("*.").removeprefix("www.")
        if host == entry or bare == root or bare.endswith("." + root):
            return True
    return False


def _check(url: str) -> None:
    """https only, and never a denied host or any subdomain of one (also after a redirect)."""
    if not url.startswith("https://"):
        raise ValueError(f"https only: {url}")
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if _matches(host, denied_hosts()):
        raise PermissionError(f"host {host} is on the deny list (ops/network/hosts.csv); fetch refused")
    target = urllib.parse.unquote(urllib.parse.unquote(f"{parts.path}?{parts.query}"))  # %6Frders is /orders
    if _matches(host, research_hosts()) and RESEARCH_PATH_BLOCK.search(target):
        raise PermissionError(
            f"host {host} is research-only (ops/network/hosts.csv); account or order path refused"
        )


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        _check(newurl)  # a public host must not bounce us to a denied one (doi.org -> ssrn)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


urllib.request.install_opener(urllib.request.build_opener(_CheckedRedirects))  # every urlopen in the process


def get(url: str) -> bytes:
    """Body of `url`; an HTTP 4xx is raised at once, network errors and 5xx are retried, then raised."""
    _check(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310 (scheme checked)
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
                body: bytes = response.read()
                return body
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == MAX_RETRIES:
                raise
            error = repr(exc)
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == MAX_RETRIES:
                raise
            error = repr(exc)
        delay = BACKOFF_S * 2**attempt
        log.warning(
            "http error, retrying",
            extra={"url": url, "attempt": attempt + 1, "delay_s": delay, "error": error},
        )
        _sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover
