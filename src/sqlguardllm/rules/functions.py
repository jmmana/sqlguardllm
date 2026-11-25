"""Rule 6: denied functions (default) or only allowlisted functions."""

from __future__ import annotations

from sqlglot import exp

from sqlguardllm._compiled import Compiled
from sqlguardllm.messages import violation
from sqlguardllm.result import Violation, ViolationCode


def function_names(node: exp.Func, dialect_name: str) -> set[str]:
    """Every lowercase name a call may be known by, including qualified forms.

    ``SYS.DBMS_PIPE.RECEIVE_MESSAGE(...)`` yields ``receive_message``,
    ``dbms_pipe.receive_message`` and ``sys.dbms_pipe.receive_message``.
    """
    base = node.name if isinstance(node, exp.Anonymous) else type(node).sql_name()
    base = base.lower()
    names = {base}
    parent = node.parent
    if isinstance(parent, exp.Dot) and parent.expression is node:
        parts = (
            parent.this.sql(dialect=dialect_name)
            .lower()
            .replace('"', "")
            .replace("`", "")
            .split(".")
        )
        for i in range(len(parts)):
            names.add(".".join([*parts[i:], base]))
    return names


def _is_denied(name: str, denied: frozenset[str]) -> bool:
    if name in denied:
        return True
    return any(entry.endswith(".*") and name.startswith(entry[:-1]) for entry in denied)


def check_functions(root: exp.Expression, compiled: Compiled) -> list[Violation]:
    """Reject denied functions, or any function outside the allowlist in allowlist mode."""
    details: list[str] = []
    for node in root.find_all(exp.Func):
        names = function_names(node, compiled.dialect_name)
        display = max(names, key=len)
        if compiled.allowed is not None:
            if not names & compiled.allowed:
                details.append(display)
        elif any(_is_denied(name, compiled.denied) for name in names):
            details.append(display)
    return [
        violation(ViolationCode.DENIED_FUNCTION, detail, compiled.language)
        for detail in dict.fromkeys(details)
    ]
