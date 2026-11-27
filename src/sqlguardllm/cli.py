"""Command-line tool: try policies by hand and check SQL in CI.

    sqlguardllm check --policy policy.yaml --context '{"user": {"branches": [1, 2]}}' "SELECT …"
    echo "SELECT …" | sqlguardllm check --policy policy.yaml --format json -
    sqlguardllm schema > schema/policy.schema.json
    sqlguardllm schema --policy-set > schema/policy-set.schema.json
    sqlguardllm test examples/policy-tests.yaml --policy examples/policy.yaml
    sqlguardllm lint --policy examples/roles.yaml --strict
    sqlguardllm serve --policy policy.yaml --port 8787
    sqlguardllm mcp --policy policy.yaml --db sqlite:///demo.db --context @context.json
    sqlguardllm check --policy policy.yaml --audit-log audit.jsonl "SELECT …"   # opt-in audit

Exit codes: 0 allowed, 1 rejected, 2 usage or policy error. ``test`` returns 1 when a case
fails and ``lint --strict`` when there are warnings.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

from sqlguardllm import __version__
from sqlguardllm.audit import AuditLog
from sqlguardllm.guard import Guard
from sqlguardllm.lint import DEFAULT_MAX_ROWS_THRESHOLD, format_report, lint_file
from sqlguardllm.messages import LANGUAGES
from sqlguardllm.policy import Policy
from sqlguardllm.policy_set import PolicySet
from sqlguardllm.result import CheckResult, PolicyError
from sqlguardllm.schema import policy_schema, policy_set_schema, policy_tests_schema

EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_ERROR = 2


class _UsageError(Exception):
    """A problem with the arguments, the policy or the context: exit code 2."""


def _add_audit_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--audit-log",
        default=None,
        metavar="PATH|-",
        help="record every decision as JSON Lines in this file, or on stderr with - "
        "(default: nothing is logged; see docs/AUDIT.md)",
    )
    parser.add_argument(
        "--audit-sql",
        choices=("none", "redacted", "raw"),
        default="redacted",
        help="SQL in audit events: none, redacted (literals replaced by ?, default) or raw "
        "(may contain personal data); ignored without --audit-log",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sqlguardllm",
        description="The SQL firewall for LLM apps: check SQL against a policy.",
    )
    parser.add_argument("--version", action="version", version=f"sqlguardllm {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser(
        "check",
        help="validate SQL and print the rewritten query or the violations",
        description="Exit code 0 when allowed, 1 when rejected, 2 on usage or policy errors.",
    )
    check.add_argument("sql", nargs="?", default="-", help="the SQL, or - to read stdin (default)")
    check.add_argument("--policy", required=True, type=Path, help="policy file (.yaml or .json)")
    check.add_argument(
        "--context",
        default=None,
        help='JSON with the values for row filters, such as \'{"user": {"branches": [1]}}\', '
        "or @file.json",
    )
    check.add_argument("--dialect", default=None, help="override the policy's dialect")
    check.add_argument("--language", choices=LANGUAGES, default="en", help="message language")
    check.add_argument("--format", choices=("text", "json"), default="text", help="output format")
    check.add_argument("--pretty", action="store_true", help="pretty-print the rewritten SQL")
    _add_audit_options(check)

    schema = commands.add_parser("schema", help="print the JSON Schema of policy files")
    schema_kind = schema.add_mutually_exclusive_group()
    schema_kind.add_argument(
        "--cases", action="store_true", help="print the schema of policy test cases instead"
    )
    schema_kind.add_argument(
        "--policy-set",
        action="store_true",
        help="print the schema of policy-set files (base and roles) instead",
    )

    mcp = commands.add_parser(
        "mcp",
        help="run an MCP server over stdio with a database tool that can't do harm",
        description='Needs the optional extra: pip install "sqlguardllm[mcp]". Gives MCP clients '
        "the tools list_tables, describe_table, explain_query and query. Connect with a "
        "read-only database user.",
    )
    mcp.add_argument(
        "--policy",
        required=True,
        type=Path,
        help="policy file (.yaml or .json), or a policy-set file with --role",
    )
    mcp.add_argument(
        "--role", default=None, help="role to use from a policy-set file (base and roles)"
    )
    mcp.add_argument(
        "--db",
        default=None,
        help="SQLAlchemy database URL, such as sqlite:///demo.db or "
        "postgresql+psycopg://reader@host/db (default: the DATABASE_URL environment variable, "
        "which keeps a password out of the command line)",
    )
    mcp.add_argument(
        "--context",
        default=None,
        help="JSON with the values for row filters, or @file.json; set by you, never by the model",
    )
    mcp.add_argument("--language", choices=LANGUAGES, default="en", help="message language")
    mcp.add_argument(
        "--timeout", type=float, default=30.0, help="seconds a query may run (default 30)"
    )
    mcp.add_argument(
        "--max-cost",
        type=float,
        default=None,
        help="refuse queries whose EXPLAIN cost exceeds this (PostgreSQL and MySQL only)",
    )
    mcp.add_argument(
        "--max-plan-rows",
        type=float,
        default=None,
        help="refuse queries whose EXPLAIN row estimate exceeds this (PostgreSQL and MySQL only)",
    )
    _add_audit_options(mcp)

    serve = commands.add_parser(
        "serve",
        help="run an HTTP server (POST /v1/check, /v1/explain) for any language",
        description="Standard library only; see docs/SERVER.md. Binds to loopback by default.",
    )
    serve.add_argument(
        "--policy", required=True, type=Path, help="policy file, or a policy-set file with --role"
    )
    serve.add_argument("--role", default=None, help="role to use from a policy-set file")
    serve.add_argument("--host", default="127.0.0.1", help="address to bind (default 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8787, help="port (default 8787)")
    serve.add_argument("--language", choices=LANGUAGES, default="en", help="message language")
    serve.add_argument(
        "--token-env",
        default=None,
        metavar="NAME",
        help="name of an environment variable holding a bearer token required on every "
        "request except /v1/health (the token is never read from the command line)",
    )
    _add_audit_options(serve)
    test = commands.add_parser("test", help="run declared cases against a policy or policy set")
    test.add_argument("cases", type=Path, help="case file (.yaml or .json)")
    test.add_argument("--policy", required=True, type=Path, help="policy or policy-set file")
    test.add_argument("--format", choices=("text", "junit"), default="text", help="output format")

    lint = commands.add_parser(
        "lint",
        help="warn about policies that are valid but probably unsafe",
        description="Exit code 0 even with warnings, 1 with warnings under --strict, 2 on usage "
        "or policy errors. Silence a reviewed warning with lint_suppress: {code: reason} in the "
        "policy file.",
    )
    lint.add_argument(
        "--policy", required=True, type=Path, help="policy or policy-set file (.yaml or .json)"
    )
    lint.add_argument("--format", choices=("text", "json"), default="text", help="output format")
    lint.add_argument("--language", choices=LANGUAGES, default="en", help="message language")
    lint.add_argument("--strict", action="store_true", help="exit 1 when there are warnings")
    lint.add_argument(
        "--max-rows-threshold",
        type=int,
        default=DEFAULT_MAX_ROWS_THRESHOLD,
        metavar="N",
        help=f"warn when max_rows is above N (default {DEFAULT_MAX_ROWS_THRESHOLD})",
    )
    return parser


def _read_context(raw: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        text = Path(raw[1:]).read_text(encoding="utf-8") if raw.startswith("@") else raw
        value = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise _UsageError(f"--context is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise _UsageError("--context must be a JSON object")
    return value


def _audit_log(args: argparse.Namespace) -> AuditLog | None:
    """The audit log the flags ask for; without ``--audit-log`` there is none."""
    if not args.audit_log:
        return None
    try:
        return AuditLog(args.audit_log, include_sql=args.audit_sql)
    except OSError as exc:
        raise _UsageError(f"cannot open the audit log: {exc}") from exc


def _read_sql(raw: str, stdin: TextIO) -> str:
    sql = stdin.read() if raw == "-" else raw
    if not sql.strip():
        raise _UsageError("no SQL given: pass it as an argument or through stdin")
    return sql


def _load_policy(path: Path, dialect: str | None) -> Policy:
    try:
        policy = Policy.from_file(path)
        return dataclasses.replace(policy, dialect=dialect) if dialect else policy
    except OSError as exc:
        raise _UsageError(f"cannot read the policy: {exc}") from exc
    except (PolicyError, ValueError) as exc:
        raise _UsageError(f"invalid policy {path}: {exc}") from exc


def _load_role(path: Path, role: str) -> Policy:
    try:
        return PolicySet.from_file(path).for_role(role)
    except OSError as exc:
        raise _UsageError(f"cannot read the policy set: {exc}") from exc
    except (PolicyError, ValueError) as exc:
        raise _UsageError(f"invalid policy set {path}: {exc}") from exc


def _test(args: argparse.Namespace, stdout: TextIO) -> int:
    from sqlguardllm.policy_tests import (  # noqa: PLC0415 - used only by the test command
        PolicyTestError,
        format_outcomes,
        run_policy_tests,
    )

    try:
        outcomes = run_policy_tests(args.cases, args.policy)
    except PolicyTestError as exc:
        raise _UsageError(str(exc)) from exc
    print(format_outcomes(outcomes, junit=args.format == "junit"), file=stdout)
    return EXIT_REJECTED if any(outcome.failure is not None for outcome in outcomes) else EXIT_OK


def _lint(args: argparse.Namespace, stdout: TextIO) -> int:
    if args.max_rows_threshold <= 0:
        raise _UsageError("--max-rows-threshold must be a positive number of rows")
    try:
        report = lint_file(
            args.policy, language=args.language, max_rows_threshold=args.max_rows_threshold
        )
    except OSError as exc:
        raise _UsageError(f"cannot read the policy: {exc}") from exc
    except (PolicyError, ValueError) as exc:
        raise _UsageError(f"invalid policy {args.policy}: {exc}") from exc
    print(format_report(report, as_json=args.format == "json"), file=stdout)
    return EXIT_REJECTED if args.strict and report.warnings else EXIT_OK


def _as_json(result: CheckResult) -> str:
    return json.dumps(
        {
            "ok": result.ok,
            "sql": result.sql,
            "tables": sorted(result.tables),
            "applied_filters": list(result.applied_filters),
            "violations": [
                {"code": v.code.value, "message": v.message, "detail": v.detail}
                for v in result.violations
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def _mcp(args: argparse.Namespace) -> int:
    try:
        from sqlguardllm.mcp_server import create_server  # noqa: PLC0415 - optional extra
    except ImportError as exc:
        missing = (exc.name or "unknown").split(".")[0]
        raise _UsageError(
            'the MCP server needs the optional extra: pip install "sqlguardllm[mcp]" '
            f"(missing module {missing})"
        ) from exc
    policy = _load_role(args.policy, args.role) if args.role else _load_policy(args.policy, None)
    context = _read_context(args.context)
    audit = _audit_log(args)
    url = args.db or os.environ.get("DATABASE_URL")
    if not url:
        raise _UsageError("no database: pass --db URL or set DATABASE_URL")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise _UsageError("--timeout must be a positive number of seconds")
    try:
        server = create_server(
            policy,
            url,
            context=context,
            language=args.language,
            timeout=args.timeout,
            max_cost=args.max_cost,
            max_plan_rows=args.max_plan_rows,
            audit=audit,
            role=args.role,
        )
    except PolicyError as exc:
        raise _UsageError(f"invalid policy {args.policy}: {exc}") from None
    except ValueError as exc:  # never includes the URL, which may hold a password
        raise _UsageError(str(exc)) from None
    server.run("stdio")
    return EXIT_OK


def _serve(args: argparse.Namespace, stderr: TextIO) -> int:
    from sqlguardllm.server import make_server  # noqa: PLC0415 - only needed here

    policy = _load_role(args.policy, args.role) if args.role else _load_policy(args.policy, None)
    token = None
    if args.token_env:
        token = os.environ.get(args.token_env)
        if not token:
            raise _UsageError(f"--token-env: the environment variable {args.token_env} is empty")
    if not 0 <= args.port <= 65535:
        raise _UsageError("--port must be between 0 and 65535")
    audit = _audit_log(args)
    try:
        guard = Guard(policy, language=args.language, audit=audit, role=args.role)
        server = make_server(guard, args.host, args.port, token)
    except PolicyError as exc:
        raise _UsageError(f"invalid policy {args.policy}: {exc}") from None
    except OSError as exc:
        raise _UsageError(
            f"cannot listen on {args.host}:{args.port}: {exc.strerror or exc}"
        ) from None
    host, port = server.server_address[:2]
    host = host.decode() if isinstance(host, bytes) else host
    auth = "token required" if token else "no token"
    print(f"sqlguardllm {__version__} listening on http://{host}:{port} ({auth})", file=stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return EXIT_OK


def _check(args: argparse.Namespace, stdin: TextIO, stdout: TextIO) -> int:
    policy = _load_policy(args.policy, args.dialect)
    context = _read_context(args.context)
    sql = _read_sql(args.sql, stdin)
    audit = _audit_log(args)
    try:
        guard = Guard(policy, language=args.language, pretty=args.pretty, audit=audit)
    except PolicyError as exc:
        raise _UsageError(f"invalid policy {args.policy}: {exc}") from exc
    result = guard.check(sql, context)

    if args.format == "json":
        print(_as_json(result), file=stdout)
    elif result.ok:
        print(result.sql, file=stdout)
    else:
        for item in result.violations:
            print(f"{item.code.value}: {item.message}", file=stdout)
    return EXIT_OK if result.ok else EXIT_REJECTED


def main(
    argv: Sequence[str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Entry point of the ``sqlguardllm`` command. Returns the exit code."""
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:  # argparse already printed the usage message
        return exc.code if isinstance(exc.code, int) else EXIT_ERROR
    try:
        if args.command == "schema":
            if args.cases:
                schema = policy_tests_schema()
            else:
                schema = policy_set_schema() if args.policy_set else policy_schema()
            print(json.dumps(schema, indent=2, ensure_ascii=False), file=stdout)
            return EXIT_OK
        if args.command == "test":
            return _test(args, stdout)
        if args.command == "lint":
            return _lint(args, stdout)
        if args.command == "mcp":
            return _mcp(args)
        if args.command == "serve":
            return _serve(args, stderr)
        return _check(args, stdin, stdout)
    except _UsageError as exc:
        print(f"sqlguardllm: error: {exc}", file=stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
