"""Stdio engine: the embedded engine as a child process of any language package.

    sqlguardllm engine --stdio

Newline-delimited JSON on stdin/stdout, one request per line, one response per line. stdout
carries only protocol messages; diagnostics go to stderr. The engine exits on stdin EOF.
See docs/PROTOCOL.md and schema/protocol/protocol.schema.json. SQL and context are never
logged or echoed in errors.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, TextIO

from sqlglot.dialects.dialect import Dialect

from sqlguardllm import __version__
from sqlguardllm.guard import Guard
from sqlguardllm.messages import LANGUAGES
from sqlguardllm.policy import Policy
from sqlguardllm.policy_set import PolicySet
from sqlguardllm.result import PolicyError
from sqlguardllm.server import result_to_dict

PROTOCOL_VERSION = 1
MAX_MESSAGE = 1024 * 1024
MAX_HANDLES = 256


class _Reject(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def dialects() -> list[str]:
    """The dialect names a policy may use."""
    return sorted(name for name in Dialect.classes if name)


def _error(id_: Any, code: str, message: str) -> dict[str, Any]:
    return {
        "id": id_,
        "type": "error",
        "allowed": False,
        "error": {"code": code, "message": message},
    }


class Engine:
    """Handles protocol requests. Guards are compiled once per handle and reused."""

    def __init__(self) -> None:
        self._guards: dict[str, Guard] = {}
        self._counter = 0
        self.stopped = False

    def handle_line(self, line: str) -> dict[str, Any]:
        """The response to one raw line. Never raises: any failure fails closed."""
        id_: Any = None
        try:
            if len(line.encode("utf-8", "surrogatepass")) > MAX_MESSAGE:
                raise _Reject("message_too_large", "message over 1 MiB")
            try:
                request = json.loads(line)
            except (ValueError, RecursionError):
                raise _Reject("malformed_message", "message is not valid JSON") from None
            if not isinstance(request, dict):
                raise _Reject("malformed_message", "message must be a JSON object")
            raw_id = request.get("id")
            if isinstance(raw_id, (str, int)) and not isinstance(raw_id, bool):
                id_ = raw_id
            else:
                raise _Reject("invalid_request", '"id" must be a string or an integer')
            return self._dispatch(id_, request)
        except _Reject as exc:
            return _error(id_, exc.code, exc.message)
        except Exception:  # noqa: BLE001 - fail closed on any internal error
            return _error(id_, "engine_error", "internal engine error")

    def _dispatch(self, id_: Any, request: dict[str, Any]) -> dict[str, Any]:
        kind = request.get("type")
        if kind == "hello":
            return {
                "id": id_,
                "type": "hello",
                "protocol": PROTOCOL_VERSION,
                "engine_version": __version__,
                "dialects": dialects(),
            }
        if kind == "load_policy":
            return {"id": id_, "type": kind, "handle": self._load(request)}
        if kind in ("check", "explain"):
            guard = self._guard(request)
            sql = request.get("sql")
            if not isinstance(sql, str):
                raise _Reject("invalid_request", '"sql" must be a string')
            context = request.get("context")
            if context is not None and not isinstance(context, dict):
                raise _Reject("invalid_request", '"context" must be an object or null')
            if kind == "check":
                result = result_to_dict(guard.check(sql, context))
            else:
                result = guard.explain(sql, context).to_dict()
            return {"id": id_, "type": kind, "result": result}
        if kind == "shutdown":
            self.stopped = True
            return {"id": id_, "type": kind}
        raise _Reject("unknown_type", "unknown message type")

    def _guard(self, request: Mapping[str, Any]) -> Guard:
        handle = request.get("handle")
        guard = self._guards.get(handle) if isinstance(handle, str) else None
        if guard is None:
            raise _Reject("unknown_handle", "unknown policy handle")
        return guard

    def _load(self, request: Mapping[str, Any]) -> str:
        language = request.get("language", "en")
        if language not in LANGUAGES:
            raise _Reject("invalid_request", f"language must be one of {', '.join(LANGUAGES)}")
        policy_data = request.get("policy")
        set_data = request.get("policy_set")
        role = request.get("role")
        if (policy_data is None) == (set_data is None):
            raise _Reject("invalid_request", 'give exactly one of "policy" or "policy_set"')
        if policy_data is not None and role is not None:
            raise _Reject("invalid_request", '"role" is only for "policy_set"')
        if set_data is not None and not isinstance(role, str):
            raise _Reject("invalid_request", '"policy_set" needs a "role" string')
        if len(self._guards) >= MAX_HANDLES:
            raise _Reject("too_many_handles", f"more than {MAX_HANDLES} policies loaded")
        try:
            if policy_data is not None:
                policy = Policy.from_dict(policy_data)
            else:
                policy = PolicySet.from_dict(set_data).for_role(role)  # type: ignore[arg-type]
            guard = Guard(policy, language=language)
        except (PolicyError, ValueError, TypeError, AttributeError) as exc:
            text = str(exc).strip().splitlines()
            raise _Reject("invalid_policy", (text[0] if text else "invalid policy")[:200]) from exc
        self._counter += 1
        handle = f"p{self._counter}"
        self._guards[handle] = guard
        return handle


def _read_line(stdin: TextIO) -> str | None:
    """One line, or None on EOF. An oversize line is drained and returned as over the limit."""
    line = stdin.readline(MAX_MESSAGE + 1)
    if not line:
        return None
    if len(line) > MAX_MESSAGE and not line.endswith("\n"):
        while True:
            rest = stdin.readline(65536)
            if not rest or rest.endswith("\n"):
                break
    return line


def run(stdin: TextIO, stdout: TextIO, stderr: TextIO) -> int:
    """Serve requests until ``shutdown`` or EOF. Returns the exit code."""
    engine = Engine()
    print(f"sqlguardllm {__version__} engine ready (protocol {PROTOCOL_VERSION})", file=stderr)
    while not engine.stopped:
        line = _read_line(stdin)
        if line is None:
            break
        if not line.strip():
            continue
        response = engine.handle_line(line)
        stdout.write(json.dumps(response, ensure_ascii=True) + "\n")
        stdout.flush()
    return 0
