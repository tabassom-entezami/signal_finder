"""Plain HTTPS GET with a timeout and a short retry on transient errors (stdlib only, no key)."""

import csv
import logging
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
def denied_hosts() -> frozenset[str]:
    """Hosts the host registry stops (`list=deny` in ops/network/hosts.csv)."""
    if not HOSTS_FILE.exists():
        return frozenset()
    with HOSTS_FILE.open(encoding="utf-8") as fh:
        return frozenset(r["host"].lower().rstrip(".") for r in csv.DictReader(fh) if r.get("list") == "deny")


def _check(url: str) -> None:
    """https only, and never a denied host or any subdomain of one (also after a redirect)."""
    if not url.startswith("https://"):
        raise ValueError(f"https only: {url}")
    host = (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")
    bare = host.removeprefix("www.")
    for denied in denied_hosts():
        root = denied.removeprefix("www.")
        if host == denied or bare == root or bare.endswith("." + root):
            raise PermissionError(f"host {host} is on the deny list (ops/network/hosts.csv); fetch refused")


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
