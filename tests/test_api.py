from __future__ import annotations

import pytest

import sqlguardllm
from conftest import CONTEXT, standard_policy
from sqlguardllm import Guard, GuardViolation, PolicyError, ViolationCode
from sqlguardllm.messages import _TEMPLATES


def test_version_is_exposed() -> None:
    assert sqlguardllm.__version__


def test_enforce_returns_safe_sql(guard: Guard) -> None:
    assert guard.enforce("SELECT name FROM branches") == "SELECT name FROM branches LIMIT 1000"


def test_enforce_raises_with_violations(guard: Guard) -> None:
    with pytest.raises(GuardViolation) as info:
        guard.enforce("DELETE FROM invoices")
    assert info.value.violations[0].code is ViolationCode.NOT_A_QUERY
    assert "SELECT" in str(info.value)


def test_wrap_only_passes_safe_sql() -> None:
    guard = Guard(standard_policy())
    seen: list[str] = []

    def execute(sql: str, tag: str) -> str:
        seen.append(sql)
        return tag

    safe_execute = guard.wrap(execute, context_fn=lambda: CONTEXT)
    assert safe_execute("SELECT id FROM invoices", "ok") == "ok"
    assert "branch_id IN (1, 2)" in seen[0]
    with pytest.raises(GuardViolation):
        safe_execute("DROP TABLE invoices", "never")
    assert len(seen) == 1


def test_every_code_has_both_languages() -> None:
    assert set(_TEMPLATES) == set(ViolationCode)
    for code, templates in _TEMPLATES.items():
        assert templates.keys() >= {"en", "es"}, code


def test_spanish_messages() -> None:
    guard = Guard(standard_policy(), language="es")
    result = guard.check("SELECT email FROM customers")
    assert result.messages == ["customers.email es una columna oculta"]


def test_unknown_language() -> None:
    with pytest.raises(PolicyError, match="language"):
        Guard(standard_policy(), language="fr")


def test_non_string_sql(guard: Guard) -> None:
    with pytest.raises(TypeError):
        guard.check(123)  # type: ignore[arg-type]
