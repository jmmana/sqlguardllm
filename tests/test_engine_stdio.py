from __future__ import annotations

import http.client
import io
import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from sqlguardllm import __version__
from sqlguardllm.cli import main
from sqlguardllm.engine_stdio import MAX_MESSAGE, Engine, run
from sqlguardllm.guard import Guard
from sqlguardllm.policy import Policy
from sqlguardllm.server import make_server

ROOT = Path(__file__).parents[1]
SCHEMA = json.loads((ROOT / "schema" / "protocol" / "protocol.schema.json").read_text("utf-8"))
POLICY = {
    "dialect": "postgres",
    "tables": ["invoices"],
    "row_filters": {"invoices": "branch_id IN ({user.branches})"},
    "max_rows": 100,
}
CTX = {"user": {"branches": [1, 2]}}


def _validate(instance: Any, name: str) -> None:
    jsonschema.validate(instance, {"$ref": f"#/$defs/{name}", "$defs": SCHEMA["$defs"]})


def _session(*requests: Any) -> list[dict[str, Any]]:
    lines = [r if isinstance(r, str) else json.dumps(r) for r in requests]
    out = io.StringIO()
    run(io.StringIO("\n".join(lines) + "\n"), out, io.StringIO())
    return [json.loads(line) for line in out.getvalue().splitlines()]


def _load(engine: Engine, **extra: Any) -> str:
    body = {"id": 0, "type": "load_policy", **({"policy": POLICY} | extra)}
    return str(engine.handle_line(json.dumps(body))["handle"])


def test_hello_and_responses_match_the_schema() -> None:
    responses = _session(
        {"id": "a", "type": "hello"},
        {"id": 1, "type": "load_policy", "policy": POLICY},
        {"id": 2, "type": "check", "handle": "p1", "sql": "SELECT id FROM invoices", "context": CTX},
        {"id": 3, "type": "explain", "handle": "p1", "sql": "SELECT id FROM invoices", "context": CTX},
        {"id": 4, "type": "shutdown"},
    )
    assert [r["id"] for r in responses] == ["a", 1, 2, 3, 4]
    hello = responses[0]
    assert hello["protocol"] == 1
    assert hello["engine_version"] == __version__
    assert "postgres" in hello["dialects"]
    for response in responses:
        _validate(response, "Response")
    assert responses[2]["result"]["ok"] is True
    _validate(responses[2]["result"], "CheckResult")
    _validate(responses[3]["result"], "ExplainResult")


def test_requests_match_the_schema() -> None:
    for request in (
        {"id": 1, "type": "hello"},
        {"id": 1, "type": "load_policy", "policy": POLICY},
        {"id": 1, "type": "load_policy", "policy_set": {"base": POLICY}, "role": "r"},
        {"id": 1, "type": "check", "handle": "p1", "sql": "SELECT 1", "context": None},
    ):
        _validate(request, "Request")
    with pytest.raises(jsonschema.ValidationError):
        _validate({"id": 1, "type": "load_policy", "policy": POLICY, "role": "r"}, "Request")


def test_check_matches_http_body_and_schema() -> None:
    server = make_server(Guard(Policy.from_dict(POLICY)), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        engine = Engine()
        handle = _load(engine)
        for kind in ("check", "explain"):
            for sql in ("SELECT id FROM invoices", "DROP TABLE invoices", "SELECT * FROM nope"):
                conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
                conn.request("POST", f"/v1/{kind}", json.dumps({"sql": sql, "context": CTX}))
                http_body = json.loads(conn.getresponse().read())
                conn.close()
                message = {"id": 1, "type": kind, "handle": handle, "sql": sql, "context": CTX}
                stdio = engine.handle_line(json.dumps(message))
                assert stdio["result"] == http_body
                _validate(http_body, "CheckResult" if kind == "check" else "ExplainResult")
    finally:
        server.shutdown()
        server.server_close()


def test_policy_is_parsed_once_per_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    original = Policy.from_dict.__func__  # type: ignore[attr-defined]
    monkeypatch.setattr(
        Policy, "from_dict", classmethod(lambda cls, d: calls.append(1) or original(cls, d))
    )
    engine = Engine()
    handle = _load(engine)
    for _ in range(3):
        request = {"id": 1, "type": "check", "handle": handle, "sql": "SELECT id FROM invoices"}
        engine.handle_line(json.dumps(request | {"context": CTX}))
    assert len(calls) == 1


def test_policy_set_with_role() -> None:
    engine = Engine()
    body = {
        "id": 1,
        "type": "load_policy",
        "policy_set": {"base": POLICY, "roles": {"r": {"max_rows": 5}}},
        "role": "r",
    }
    handle = engine.handle_line(json.dumps(body))["handle"]
    request = {"id": 2, "type": "check", "handle": handle, "sql": "SELECT id FROM invoices"}
    result = engine.handle_line(json.dumps(request | {"context": CTX}))["result"]
    assert result["ok"] and "LIMIT 5" in result["sql"]


@pytest.mark.parametrize(
    ("line", "code", "id_"),
    [
        ("{not json", "malformed_message", None),
        ("[1, 2]", "malformed_message", None),
        ('{"type": "hello"}', "invalid_request", None),
        ('{"id": true, "type": "hello"}', "invalid_request", None),
        ('{"id": 1, "type": "nope"}', "unknown_type", 1),
        ('{"id": 2, "type": "check", "handle": "zz", "sql": "SELECT 1"}', "unknown_handle", 2),
        ('{"id": 3, "type": "load_policy"}', "invalid_request", 3),
        ('{"id": 4, "type": "load_policy", "policy": {"dialect": "nope"}}', "invalid_policy", 4),
        ('{"id": 5, "type": "load_policy", "policy": []}', "invalid_policy", 5),
        ('{"id": 6, "type": "load_policy", "policy_set": {"base": {}}, "role": "x"}', "invalid_policy", 6),
        ("[" * 100000, "malformed_message", None),
    ],
)
def test_bad_requests_get_an_error_and_fail_closed(line: str, code: str, id_: Any) -> None:
    response = Engine().handle_line(line)
    assert response["type"] == "error"
    assert response["allowed"] is False
    assert response["error"]["code"] == code
    assert response["id"] == id_
    _validate(response, "Response")


def test_bad_check_arguments() -> None:
    engine = Engine()
    handle = _load(engine)
    for extra in ({"sql": 1}, {"sql": "SELECT 1", "context": [1]}, {}):
        response = engine.handle_line(json.dumps({"id": 1, "type": "check", "handle": handle} | extra))
        assert response["error"]["code"] == "invalid_request"


def test_internal_error_is_engine_error_and_leaks_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = Engine()
    handle = _load(engine)

    def boom(self: Guard, sql: str, context: Any = None) -> Any:
        raise RuntimeError("secret-sql-text")

    monkeypatch.setattr(Guard, "check", boom)
    response = engine.handle_line(
        json.dumps({"id": 7, "type": "check", "handle": handle, "sql": "SELECT 1"})
    )
    assert response["allowed"] is False
    assert response["error"]["code"] == "engine_error"
    assert response["id"] == 7
    assert "secret" not in json.dumps(response)


def test_oversize_message_is_rejected_and_the_engine_keeps_going() -> None:
    big = '{"id": 1, "type": "check", "sql": "' + "x" * (MAX_MESSAGE + 10) + '"}'
    responses = _session(big, {"id": 2, "type": "hello"})
    assert responses[0]["error"]["code"] == "message_too_large"
    assert responses[0]["allowed"] is False
    assert responses[1]["type"] == "hello"


def test_max_size_message_boundary_in_bytes() -> None:
    padding = "é" * (MAX_MESSAGE // 2)  # two bytes each: the line is over 1 MiB in bytes
    line = json.dumps({"id": 1, "type": "hello", "pad": padding}, ensure_ascii=False)
    assert Engine().handle_line(line)["error"]["code"] == "message_too_large"


def test_stdout_is_ascii_json_lines_and_blank_lines_are_ignored() -> None:
    out = io.StringIO()
    request = {"id": 1, "type": "check", "handle": "p1", "sql": "SELECT 'ñ' FROM invoices"}
    stdin = io.StringIO('{"id": 0, "type": "load_policy", "policy": ' + json.dumps(POLICY) + "}\n\n")
    stdin.seek(0, 2)
    stdin.write(json.dumps(request | {"context": CTX}) + "\n")
    stdin.seek(0)
    assert run(stdin, out, io.StringIO()) == 0
    lines = out.getvalue().splitlines()
    assert len(lines) == 2
    assert all(line.isascii() for line in lines)


def test_shutdown_stops_reading() -> None:
    responses = _session({"id": 1, "type": "shutdown"}, {"id": 2, "type": "hello"})
    assert [r["id"] for r in responses] == [1]


def test_cli_engine_requires_stdio(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["engine"]) == 2
    out, err = io.StringIO(), io.StringIO()
    stdin = io.StringIO('{"id": 1, "type": "hello"}\n')
    assert main(["engine", "--stdio"], stdin, out, err) == 0
    assert json.loads(out.getvalue())["protocol"] == 1
    assert "ready" in err.getvalue()


def test_real_process_exits_on_eof_with_clean_stdout() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "sqlguardllm", "engine", "--stdio"],
        input='{"id": 1, "type": "hello"}\nnot json\n',
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0
    lines = proc.stdout.splitlines()
    assert [json.loads(line)["id"] for line in lines] == [1, None]
    assert "ready" in proc.stderr
