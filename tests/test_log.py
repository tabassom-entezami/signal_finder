import json
import logging
import sys

from signal_engine.log import JsonFormatter


def _record(**extra: object) -> logging.LogRecord:
    rec = logging.makeLogRecord({"name": "t", "levelname": "INFO", "msg": "hello %s", "args": ("x",)})
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def test_line_is_json_with_core_fields() -> None:
    line = JsonFormatter().format(_record(version="0.1.0"))
    obj = json.loads(line)
    assert obj["msg"] == "hello x" and obj["level"] == "INFO" and obj["version"] == "0.1.0"
    assert obj["ts"].endswith("+00:00")


def test_non_serialisable_extra_stays_valid_json() -> None:
    line = JsonFormatter().format(_record(obj=object()))
    assert "obj" in json.loads(line)


def test_exception_is_included() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        rec = _record()
        rec.exc_info = sys.exc_info()
    obj = json.loads(JsonFormatter().format(rec))
    assert "RuntimeError: boom" in obj["exc"]
