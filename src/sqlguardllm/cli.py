"""Command-line tool: try policies by hand and check SQL in CI.

    sqlguardllm check --policy policy.yaml --context '{"user": {"branches": [1, 2]}}' "SELECT …"
    echo "SELECT …" | sqlguardllm check --policy policy.yaml --format json -
    sqlguardllm schema > schema/policy.schema.json
    sqlguardllm schema --policy-set > schema/policy-set.schema.json
    sqlguardllm mcp --policy policy.yaml --db sqlite:///demo.db --context @context.json

Exit codes: 0 allowed, 1 rejected, 2 usage or policy error.
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
from sqlguardllm.guard import Guard
from sqlguardllm.messages import LANGUAGES
from sqlguardllm.policy import Policy
from sqlguardllm.policy_set import PolicySet
from sqlguardllm.result import CheckResult, PolicyError
from sqlguardllm.schema import policy_schema, policy_set_schema

EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_ERROR = 2


class _UsageError(Exception):
    """A problem with the arguments, the policy or the context: exit code 2."""


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

    schema = commands.add_parser("schema", help="print the JSON Schema of policy files")
    schema.add_argument(
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
    url = args.db or os.environ.get("DATABASE_URL")
    if not url:
        raise _UsageError("no database: pass --db URL or set DATABASE_URL")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise _UsageError("--timeout must be a positive number of seconds")
    try:
        server = create_server(
            policy, url, context=context, language=args.language, timeout=args.timeout
        )
    except PolicyError as exc:
        raise _UsageError(f"invalid policy {args.policy}: {exc}") from None
    except ValueError as exc:  # never includes the URL, which may hold a password
        raise _UsageError(str(exc)) from None
    server.run("stdio")
    return EXIT_OK


def _check(args: argparse.Namespace, stdin: TextIO, stdout: TextIO) -> int:
    policy = _load_policy(args.policy, args.dialect)
    context = _read_context(args.context)
    sql = _read_sql(args.sql, stdin)
    try:
        guard = Guard(policy, language=args.language, pretty=args.pretty)
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
            schema = policy_set_schema() if args.policy_set else policy_schema()
            print(json.dumps(schema, indent=2, ensure_ascii=False), file=stdout)
            return EXIT_OK
        if args.command == "mcp":
            return _mcp(args)
        return _check(args, stdin, stdout)
    except _UsageError as exc:
        print(f"sqlguardllm: error: {exc}", file=stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
