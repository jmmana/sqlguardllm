from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from sqlguardllm import __version__
from sqlguardllm.guard import Guard
from sqlguardllm.policy import Policy
from sqlguardllm.server import GuardServer, make_server

POLICY = Path(__file__).parents[1] / "examples" / "policy.yaml"
CTX = {"user": {"branches": [1, 2]}}


def _start(token: str | None) -> Iterator[GuardServer]:
    server = make_server(Guard(Policy.from_file(POLICY)), "127.0.0.1", 0, token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture(scope="module")
def server() -> Iterator[GuardServer]:
    yield from _start(None)


@pytest.fixture(scope="module")
def secured() -> Iterator[GuardServer]:
    yield from _start("s3cret")


def call(  # noqa: PLR0917
    srv: GuardServer,
    method: str,
    path: str,
    body: Any = None,
    headers: dict[str, str] | None = None,
    raw: bytes | None = None,
) -> tuple[int, str, Any]:
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    conn.request(method, path, body=data, headers=headers or {})
    resp = conn.getresponse()
    payload = json.loads(resp.read())
    ctype = resp.getheader("Content-Type") or ""
    conn.close()
    return resp.status, ctype, payload


def test_health(server: GuardServer) -> None:
    status, ctype, body = call(server, "GET", "/v1/health")
    assert status == 200
    assert ctype == "application/json; charset=utf-8"
    assert body == {"status": "ok", "version": __version__}


def test_check_allowed_and_rejected(server: GuardServer) -> None:
    ok = call(server, "POST", "/v1/check", {"sql": "SELECT id FROM invoices", "context": CTX})[2]
    assert ok["ok"] is True
    assert "branch_id IN (1, 2)" in ok["sql"]
    assert ok["tables"] == ["invoices"]
    assert ok["violations"] == []
    status, _, bad = call(server, "POST", "/v1/check", {"sql": "DROP TABLE invoices"})
    assert status == 200
    assert bad["ok"] is False
    assert bad["sql"] is None
    assert bad["violations"][0].keys() == {"code", "message", "detail"}


def test_explain(server: GuardServer) -> None:
    status, _, body = call(
        server, "POST", "/v1/explain", {"sql": "SELECT id FROM invoices", "context": None}
    )
    assert status == 200
    assert "ok" in body


@pytest.mark.parametrize(
    "raw",
    [b"not json", b"[]", b'{"sql": 1}', b'{"context": {}}', b'{"sql": "x", "context": [1]}'],
)
def test_bad_requests(server: GuardServer, raw: bytes) -> None:
    status, ctype, body = call(server, "POST", "/v1/check", raw=raw)
    assert status == 400
    assert ctype == "application/json; charset=utf-8"
    assert body["error"]["code"] == "bad_request"


def test_error_never_echoes_context(server: GuardServer) -> None:
    raw = b'{"sql": 5, "context": {"user": "TOPSECRET"}}'
    _, _, body = call(server, "POST", "/v1/check", raw=raw)
    assert "TOPSECRET" not in json.dumps(body)


def test_not_found_and_method(server: GuardServer) -> None:
    assert call(server, "GET", "/nope")[0] == 404
    assert call(server, "POST", "/nope", {"sql": "x"})[0] == 404
    assert call(server, "GET", "/v1/check")[0] == 405
    assert call(server, "POST", "/v1/health", {})[0] == 405
    assert call(server, "DELETE", "/v1/check")[0] == 405


def test_body_over_one_mib(server: GuardServer) -> None:
    big = json.dumps({"sql": "SELECT 1 -- " + "a" * (1024 * 1024)}).encode()
    status, _, body = call(server, "POST", "/v1/check", raw=big)
    assert status == 413
    assert "error" in body


def test_auth(secured: GuardServer) -> None:
    body = {"sql": "SELECT id FROM invoices", "context": CTX}
    assert call(secured, "GET", "/v1/health")[0] == 200
    assert call(secured, "POST", "/v1/check", body)[0] == 401
    wrong = {"Authorization": "Bearer nope"}
    assert call(secured, "POST", "/v1/check", body, wrong)[0] == 401
    assert call(secured, "POST", "/v1/explain", body, {"Authorization": "s3cret"})[0] == 401
    status, _, out = call(secured, "POST", "/v1/check", body, {"Authorization": "Bearer s3cret"})
    assert status == 200
    assert out["ok"] is True
    assert call(secured, "GET", "/nope")[0] == 401


def test_concurrent_requests(server: GuardServer) -> None:
    def one(i: int) -> tuple[int, bool]:
        sql = "SELECT id FROM invoices" if i % 2 else "DELETE FROM invoices"
        status, _, body = call(server, "POST", "/v1/check", {"sql": sql, "context": CTX})
        return status, body["ok"]

    with ThreadPoolExecutor(20) as pool:
        results = list(pool.map(one, range(40)))
    assert all(status == 200 for status, _ in results)
    assert [ok for _, ok in results] == [bool(i % 2) for i in range(40)]
