"""Opt-in audit log: one event per decision of the guard, written only when you turn it on.

    from sqlguardllm import AuditLog, Guard

    guard = Guard(policy, audit=AuditLog("audit.jsonl"))   # JSON Lines to a file
    guard = Guard(policy, audit=print)                       # any callable taking the event

Without ``audit`` nothing is built, written or timed. Audit is deployment configuration, not
policy: it lives on the Guard, the CLI, the server and the MCP server. Context values are
never logged, and SQL appears only as ``include_sql`` says. See docs/AUDIT.md.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
import logging
import os
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TextIO

import sqlglot
from sqlglot import exp

from sqlguardllm.policy import Policy

IncludeSql = Literal["none", "redacted", "raw"]
INCLUDE_SQL: tuple[str, ...] = ("none", "redacted", "raw")
LOGGER_NAME = "sqlguardllm.audit"

_LITERALS: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, name)
    for name in ("Literal", "HexString", "BitString", "ByteString", "RawString", "National")
    if hasattr(exp, name)
)
_MAX_CONTEXT_DEPTH = 5


@dataclass(frozen=True)
class AuditEvent:
    """One decision of the guard.

    Attributes:
        timestamp: UTC time of the decision, ISO 8601.
        event_id: Unique id of this event.
        version: sqlguardllm version that decided.
        policy_fingerprint: sha256 of the canonical policy, to tell which policy decided.
        role: Role of the policy set, when the Guard came from one.
        allowed: True when the query may run, False when it was rejected.
        violation_codes: Stable codes of every violation (empty when allowed).
        tables: Physical tables the query reads, as far as the guard got.
        sql_fingerprint: sha256 of the normalised SQL (literals replaced by ``?``), the same
            for every query of the same shape.
        duration_ms: Time the decision took, in milliseconds.
        context_keys: Names, never values, of the context keys (``user.branches``).
        sql: The SQL as ``include_sql`` allows: ``None``, redacted or raw.
    """

    timestamp: str
    event_id: str
    version: str
    policy_fingerprint: str
    role: str | None
    allowed: bool
    violation_codes: tuple[str, ...]
    tables: tuple[str, ...]
    sql_fingerprint: str
    duration_ms: float
    context_keys: tuple[str, ...]
    sql: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape of the event. ``sql`` is present only when it is logged."""
        data: dict[str, Any] = {
            "timestamp": self.timestamp,
            "event_id": self.event_id,
            "version": self.version,
            "policy_fingerprint": self.policy_fingerprint,
            "role": self.role,
            "allowed": self.allowed,
            "violation_codes": list(self.violation_codes),
            "tables": list(self.tables),
            "sql_fingerprint": self.sql_fingerprint,
            "duration_ms": self.duration_ms,
            "context_keys": list(self.context_keys),
        }
        if self.sql is not None:
            data["sql"] = self.sql
        return data


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items(), key=_key)}
    if isinstance(value, set | frozenset):
        return sorted((_canonical(item) for item in value), key=_dump)
    if isinstance(value, list | tuple):
        return [_canonical(item) for item in value]
    return value


def _key(item: tuple[Any, Any]) -> str:
    return str(item[0])


def _dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def policy_fingerprint(policy: Policy) -> str:
    """sha256 of the canonical policy: the same policy gives the same fingerprint everywhere."""
    fields = {f.name: _canonical(getattr(policy, f.name)) for f in dataclasses.fields(policy)}
    return hashlib.sha256(_dump(fields).encode("utf-8")).hexdigest()


def redact_sql(sql: str, dialect: str) -> str | None:
    """``sql`` with every literal replaced by ``?`` and comments dropped.

    Returns ``None`` when the SQL can't be parsed: it can't be redacted safely.
    """
    try:
        parts = []
        for statement in sqlglot.parse(sql, read=dialect):
            if statement is None:
                continue
            for node in list(statement.find_all(*_LITERALS)):
                node.replace(exp.Var(this="?"))
            parts.append(statement.sql(dialect=dialect, comments=False))
        return "; ".join(parts)
    except Exception:  # noqa: BLE001 - untrusted SQL never makes the audit raise
        return None


def context_key_names(context: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Dotted names of the context's keys, never its values: ``("user.branches",)``."""
    names: set[str] = set()

    def walk(mapping: Mapping[Any, Any], prefix: str, depth: int) -> None:
        for key, value in mapping.items():
            name = f"{prefix}{key}"
            if isinstance(value, Mapping) and value and depth < _MAX_CONTEXT_DEPTH:
                walk(value, f"{name}.", depth + 1)
            else:
                names.add(name)

    if isinstance(context, Mapping):
        walk(context, "", 0)
    return tuple(sorted(names))


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()
    return (text[0] if text else type(exc).__name__)[:200]


class AuditLog:
    """Where decisions go, and how much SQL they carry.

    Args:
        sink: A path (JSON Lines, appended; ``"-"`` is stderr), an open text stream, a
            :class:`logging.Logger`, or a callable taking the :class:`AuditEvent`. See
            :meth:`otel` for OpenTelemetry.
        include_sql: ``"redacted"`` (default: literals replaced by ``?``), ``"none"`` or
            ``"raw"``. Raw SQL may contain personal data: turn it on only if you can store it.
        strict: When True, a sink failure rejects the query instead of being reported once on
            stderr. A failing sink never turns a rejection into an allowance.
        level: Logging level when ``sink`` is a logger.
    """

    def __init__(
        self,
        sink: Callable[[AuditEvent], None] | str | os.PathLike[str] | TextIO | logging.Logger,
        *,
        include_sql: IncludeSql = "redacted",
        strict: bool = False,
        level: int = logging.INFO,
    ) -> None:
        if include_sql not in INCLUDE_SQL:
            raise ValueError(f"include_sql must be one of {', '.join(INCLUDE_SQL)}")
        self.include_sql = include_sql
        self.strict = bool(strict)
        self._lock = threading.Lock()
        self._reported = False
        self._sink = self._make_sink(sink, level)

    @classmethod
    def stderr(cls, **options: Any) -> AuditLog:
        """JSON Lines to standard error."""
        return cls("-", **options)

    @classmethod
    def otel(cls, **options: Any) -> AuditLog:
        """Decisions as ``sqlguardllm.decision`` events on the current OpenTelemetry span.

        Needs the optional extra: ``pip install "sqlguardllm[otel]"``. Outside a recording
        span the events are dropped by OpenTelemetry itself.
        """
        try:
            trace = importlib.import_module("opentelemetry.trace")
        except ImportError as exc:
            raise ImportError(
                'OpenTelemetry audit needs the optional extra: pip install "sqlguardllm[otel]"'
            ) from exc

        def sink(event: AuditEvent) -> None:
            attributes = {
                key: list(value) if isinstance(value, tuple) else value
                for key, value in event.to_dict().items()
                if value is not None
            }
            trace.get_current_span().add_event("sqlguardllm.decision", attributes=attributes)

        return cls(sink, **options)

    def _make_sink(self, sink: Any, level: int) -> Callable[[AuditEvent], None]:
        if isinstance(sink, logging.Logger):

            def to_logger(event: AuditEvent) -> None:
                data = event.to_dict()
                sink.log(level, "%s", _dump(data), extra={"sqlguardllm_event": data})

            return to_logger
        if isinstance(sink, str | os.PathLike):
            if os.fspath(sink) == "-":
                return self._lines(lambda line: print(line, file=sys.stderr, flush=True))
            path = Path(os.fspath(sink))
            with path.open("a", encoding="utf-8"):  # fail now, not on the first decision
                pass

            def to_file(line: str) -> None:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")

            return self._lines(to_file)
        if hasattr(sink, "write"):

            def to_stream(line: str) -> None:
                sink.write(line + "\n")
                if hasattr(sink, "flush"):
                    sink.flush()

            return self._lines(to_stream)
        if callable(sink):
            return sink  # type: ignore[no-any-return]
        raise TypeError("sink must be a path, a text stream, a logger or a callable")

    def _lines(self, write: Callable[[str], None]) -> Callable[[AuditEvent], None]:
        def sink(event: AuditEvent) -> None:
            line = _dump(event.to_dict())
            with self._lock:  # one whole line at a time, even from many threads
                write(line)

        return sink

    def record(
        self,
        *,
        sql: str,
        dialect: str,
        policy_fingerprint: str,
        role: str | None,
        allowed: bool,
        violation_codes: Iterable[str],
        tables: Iterable[str],
        duration_ms: float,
        context: Mapping[str, Any] | None,
    ) -> bool:
        """Build and deliver the event. Never raises; returns False when it was not delivered."""
        try:
            from sqlguardllm import __version__  # noqa: PLC0415 - circular at import time

            redacted = redact_sql(sql, dialect)
            fingerprint_source = redacted if redacted is not None else " ".join(sql.split())
            shown: str | None = None
            if self.include_sql == "raw":
                shown = sql
            elif self.include_sql == "redacted":
                shown = redacted
            event = AuditEvent(
                timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                event_id=uuid.uuid4().hex,
                version=__version__,
                policy_fingerprint=policy_fingerprint,
                role=role,
                allowed=allowed,
                violation_codes=tuple(sorted(set(violation_codes))),
                tables=tuple(sorted(set(tables))),
                sql_fingerprint=hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest(),
                duration_ms=round(duration_ms, 3),
                context_keys=context_key_names(context),
                sql=shown,
            )
            self._sink(event)
        except Exception as exc:  # noqa: BLE001 - any sink may fail; the guard must not
            self._report(exc)
            return False
        return True

    def _report(self, exc: Exception) -> None:
        with self._lock:
            if self._reported or self.strict:
                return
            self._reported = True
        try:
            print(
                f"sqlguardllm: audit log failed ({type(exc).__name__}: {_first_line(exc)}); "
                "decisions are not being recorded",
                file=sys.stderr,
            )
        except Exception:  # noqa: BLE001, S110 - stderr itself may be closed
            pass


def monotonic_ms() -> float:
    """A clock in milliseconds for measuring durations."""
    return time.perf_counter() * 1000.0


def as_audit_log(audit: Any) -> AuditLog | None:
    """``None`` stays ``None``, an :class:`AuditLog` stays as is, a callable becomes one."""
    if audit is None or isinstance(audit, AuditLog):
        return audit
    if callable(audit):
        return AuditLog(audit)
    raise TypeError("audit must be an AuditLog, a callable taking the event, or None")
