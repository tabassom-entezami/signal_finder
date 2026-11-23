"""Every fetcher goes through the guarded client, and exchange clients never get credentials.

Plain HTTP must use `signal_engine.data.http` (host check, honest user agent, retries). Exchange data goes
through ccxt, built for public endpoints only: no API key, secret or password in any client's options.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "signal_engine"
GUARDED_CLIENT = SRC / "data" / "http.py"
RAW_HTTP_MODULES = {"urllib.request", "http.client", "requests", "httpx", "aiohttp", "urllib3", "pycurl"}
CREDENTIAL_KEYS = {"apikey", "secret", "password", "uid", "privatekey", "walletaddress", "token"}


def _modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "private" not in p.relative_to(SRC).parts)


def _imported(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names |= {f"{node.module}.{a.name}" for a in node.names}
    return names


def raw_http_users(modules: list[Path]) -> list[str]:
    """Modules other than the guarded client that import a raw HTTP library."""
    out = []
    for path in modules:
        if path == GUARDED_CLIENT:
            continue
        hit = _imported(ast.parse(path.read_text(encoding="utf-8"))) & RAW_HTTP_MODULES
        if hit:
            out.append(f"{path.relative_to(SRC)}: {sorted(hit)}")
    return out


def credentialed_ccxt_clients(modules: list[Path]) -> list[str]:
    """Calls like `ccxt.<exchange>({...})` whose options hold a credential key."""
    out = []
    for path in modules:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "ccxt"):
                continue
            for arg in node.args:
                if isinstance(arg, ast.Dict):
                    consts = [k for k in arg.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
                    keys = {str(k.value).lower() for k in consts}
                    if keys & CREDENTIAL_KEYS:
                        out.append(f"{path.relative_to(SRC)}:{node.lineno}: {sorted(keys & CREDENTIAL_KEYS)}")
            if any(kw.arg and kw.arg.lower() in CREDENTIAL_KEYS for kw in node.keywords):
                out.append(f"{path.relative_to(SRC)}:{node.lineno}: keyword credential")
    return out


def test_only_the_guarded_client_imports_a_raw_http_library() -> None:
    assert raw_http_users(_modules()) == []


def test_no_exchange_client_is_built_with_credentials() -> None:
    assert credentialed_ccxt_clients(_modules()) == []


def test_the_checks_catch_a_raw_import_and_a_credential(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text(
        "import urllib.request\nimport ccxt\n"
        "x = ccxt.someexchange({'apiKey': 'k', 'enableRateLimit': True})\n",
        encoding="utf-8",
    )
    global SRC
    saved, SRC = SRC, tmp_path
    try:
        assert raw_http_users([bad]) == ["bad.py: ['urllib.request']"]
        assert credentialed_ccxt_clients([bad]) == ["bad.py:3: ['apikey']"]
    finally:
        SRC = saved
