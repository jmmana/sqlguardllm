"""HTTP server mode: the same engine as a sidecar for any language. Standard library only.

    sqlguardllm serve --policy policy.yaml --port 8787

See docs/SERVER.md for the contract. SQL and context are never logged or echoed in errors.
"""

from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from sqlguardllm import __version__
from sqlguardllm.guard import Guard
from sqlguardllm.result import CheckResult

MAX_BODY = 1024 * 1024
_JSON = "application/json; charset=utf-8"


def result_to_dict(result: CheckResult) -> dict[str, Any]:
    """The JSON shape of a check, identical to ``sqlguardllm check --format json``."""
    return {
        "ok": result.ok,
        "sql": result.sql,
        "tables": sorted(result.tables),
        "applied_filters": list(result.applied_filters),
        "violations": [
            {"code": v.code.value, "message": v.message, "detail": v.detail}
            for v in result.violations
        ],
    }


class _BadRequest(Exception):
    pass


class GuardServer(ThreadingHTTPServer):
    """A threaded HTTP server holding one Guard (immutable, shared by all requests)."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], guard: Guard, token: str | None = None) -> None:
        self.guard = guard
        self.token = token
        super().__init__(address, _Handler)


class _Handler(BaseHTTPRequestHandler):
    server: GuardServer
    protocol_version = "HTTP/1.1"
    server_version = "sqlguardllm"

    def log_message(self, format: str, *args: Any) -> None:
        """Silence the default access log: request lines could carry sensitive data."""

    def _send(self, status: int, body: dict[str, Any], *, close: bool = False) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", _JSON)
        self.send_header("Content-Length", str(len(data)))
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, code: str, message: str, *, close: bool = False) -> None:
        self._send(status, {"error": {"code": code, "message": message}}, close=close)

    def _authorized(self) -> bool:
        token = self.server.token
        if token is None:
            return True
        header = self.headers.get("Authorization", "")
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer":
            return False
        return hmac.compare_digest(value.strip().encode("utf-8"), token.encode("utf-8"))

    def _path(self) -> str:
        return self.path.split("?", 1)[0]

    def do_GET(self) -> None:
        path = self._path()
        if path == "/v1/health":
            self._send(200, {"status": "ok", "version": __version__})
        elif not self._authorized():
            self._unauthorized()
        elif path in ("/v1/check", "/v1/explain"):
            self._method_not_allowed("POST")
        else:
            self._error(404, "not_found", "unknown path")

    def do_POST(self) -> None:
        path = self._path()
        if path == "/v1/health":
            self._method_not_allowed("GET")
            return
        if not self._authorized():
            self._unauthorized()
            return
        if path not in ("/v1/check", "/v1/explain"):
            self._error(404, "not_found", "unknown path", close=True)
            return
        try:
            sql, context = self._read_body()
        except _BadRequest as exc:
            self._error(400, "bad_request", str(exc), close=True)
            return
        except OverflowError:
            self._error(413, "payload_too_large", "body over 1 MiB", close=True)
            return
        guard = self.server.guard
        if path == "/v1/check":
            self._send(200, result_to_dict(guard.check(sql, context)))
        else:
            self._send(200, guard.explain(sql, context).to_dict())

    def do_PUT(self) -> None:
        self._other()

    do_DELETE = do_PATCH = do_PUT  # noqa: N815

    def _other(self) -> None:
        if not self._authorized():
            self._unauthorized()
        elif self._path() in ("/v1/health", "/v1/check", "/v1/explain"):
            self._method_not_allowed("GET" if self._path() == "/v1/health" else "POST")
        else:
            self._error(404, "not_found", "unknown path", close=True)

    def _unauthorized(self) -> None:
        self._error(401, "unauthorized", "missing or invalid bearer token", close=True)

    def _method_not_allowed(self, allow: str) -> None:
        data = json.dumps(
            {"error": {"code": "method_not_allowed", "message": f"use {allow}"}}
        ).encode()
        self.send_response(405)
        self.send_header("Allow", allow)
        self.send_header("Content-Type", _JSON)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> tuple[str, dict[str, Any] | None]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None or not raw_length.strip().isdigit():
            raise _BadRequest("Content-Length is required")
        length = int(raw_length)
        if length > MAX_BODY:
            # Drain a modest overshoot so the client can read the 413 instead of a reset.
            remaining = min(length, 8 * MAX_BODY)
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
            raise OverflowError
        try:
            payload = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            raise _BadRequest("body is not valid JSON") from None
        if not isinstance(payload, dict):
            raise _BadRequest("body must be a JSON object")
        sql = payload.get("sql")
        if not isinstance(sql, str):
            raise _BadRequest('"sql" must be a string')
        context = payload.get("context")
        if context is not None and not isinstance(context, dict):
            raise _BadRequest('"context" must be an object or null')
        return sql, context


def make_server(
    guard: Guard, host: str = "127.0.0.1", port: int = 8787, token: str | None = None
) -> GuardServer:
    """Build the server (port 0 picks a free one); call ``serve_forever()`` to run it."""
    return GuardServer((host, port), guard, token)
