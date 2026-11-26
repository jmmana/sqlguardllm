from __future__ import annotations

import json
from typing import Any

import pytest

from conftest import CONTEXT, standard_policy
from sqlguardllm import Explanation, Guard, Policy, ViolationCode
from test_corpus import CASES


def explain(sql: str, context: Any = CONTEXT, **overrides: Any) -> Explanation:
    return Guard(standard_policy(**overrides)).explain(sql, context)


def columns(explanation: Explanation) -> dict[str, set[str]]:
    return {table: set(names) for table, names in explanation.columns.items()}


def test_join() -> None:
    e = explain(
        "SELECT b.name, SUM(i.total) FROM invoices i JOIN branches b ON b.id = i.branch_id "
        "GROUP BY b.name"
    )
    assert e.ok and e.sql is not None
    assert e.tables == {"invoices", "branches"}
    assert columns(e) == {"branches": {"id", "name"}, "invoices": {"branch_id", "total"}}
    assert dict(e.row_filters) == {"invoices": "branch_id IN (1, 2)"}
    assert e.limit == 1000
    assert e.violations == ()
    assert e.text == (
        "Reads branches.id, branches.name, invoices.branch_id and invoices.total, "
        "filtered by branch_id IN (1, 2) on invoices, up to 1000 rows."
    )


def test_matches_check() -> None:
    guard = Guard(standard_policy())
    sql = "SELECT a.id FROM invoices a JOIN invoices b ON a.id = b.id"
    result, explanation = guard.check(sql, CONTEXT), guard.explain(sql, CONTEXT)
    assert (explanation.ok, explanation.sql) == (result.ok, result.sql)
    assert dict(explanation.row_filters) == {"invoices": "branch_id IN (1, 2)"}


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_same_decision_as_check_on_the_corpus(case: dict[str, Any]) -> None:
    guard = Guard(standard_policy(case["dialect"], **case.get("policy", {})))
    context = case.get("context", CONTEXT)
    result, explanation = guard.check(case["sql"], context), guard.explain(case["sql"], context)
    assert (explanation.ok, explanation.sql) == (result.ok, result.sql)
    assert explanation.violations == result.violations
    assert explanation.text


def test_correlated_subquery_columns_belong_to_their_own_select() -> None:
    e = explain(
        "SELECT name FROM customers c "
        "WHERE id IN (SELECT customer_id FROM invoices i WHERE i.total > c.credit)"
    )
    assert e.ok
    assert columns(e) == {
        "customers": {"name", "id", "credit"},
        "invoices": {"customer_id", "total"},
    }


def test_scalar_subquery_without_from_reads_the_outer_table() -> None:
    e = explain("SELECT (SELECT MAX(total)) AS top FROM invoices")
    assert columns(e) == {"invoices": {"total"}}


def test_cte_reads_are_attributed_to_the_real_table() -> None:
    e = explain(
        "WITH recent AS (SELECT * FROM invoices WHERE issued_on >= '2026-11-01') "
        "SELECT COUNT(*) FROM recent"
    )
    assert e.ok
    assert e.tables == {"invoices"}
    assert columns(e) == {"invoices": {"*", "issued_on"}}
    assert e.text.startswith("Reads invoices.* and invoices.issued_on, filtered by")


def test_cte_shadowing_a_table_reads_nothing() -> None:
    e = explain("WITH invoices AS (SELECT 1 AS id) SELECT id FROM invoices")
    assert e.ok
    assert e.tables == frozenset()
    assert dict(e.columns) == {}
    assert dict(e.row_filters) == {}
    assert e.text == "Reads no table, up to 1000 rows."


def test_derived_table_and_union() -> None:
    e = explain(
        "SELECT d.n FROM (SELECT branch_id AS n FROM invoices) d "
        "UNION SELECT id FROM branches WHERE name LIKE 'N%'"
    )
    assert e.ok
    assert columns(e) == {"invoices": {"branch_id"}, "branches": {"id", "name"}}


def test_qualified_star_and_count_star() -> None:
    assert columns(explain("SELECT i.* FROM invoices i")) == {"invoices": {"*"}}
    e = explain("SELECT COUNT(*) FROM invoices")
    assert columns(e) == {"invoices": set()}
    assert e.text.startswith("Reads invoices, filtered by")


def test_unqualified_columns_with_several_sources_are_unresolved() -> None:
    e = explain("SELECT name, total FROM branches b JOIN invoices i ON i.branch_id = b.id")
    assert e.ok
    assert columns(e) == {"branches": {"id"}, "invoices": {"branch_id"}}
    assert e.unresolved_columns == {"name", "total"}
    assert "name (table not resolved)" in e.text


def test_output_aliases_are_not_columns() -> None:
    e = explain(
        "SELECT branch_id AS b, COUNT(*) AS n FROM invoices GROUP BY b HAVING n > 1 ORDER BY n"
    )
    assert columns(e) == {"invoices": {"branch_id"}}
    assert e.unresolved_columns == frozenset()


def test_schema_resolves_unqualified_columns() -> None:
    schema = {"branches": ["id", "name"], "invoices": ["id", "branch_id", "total"]}
    e = explain(
        "SELECT name, total, id FROM branches b JOIN invoices i ON i.branch_id = b.id",
        schema=schema,
    )
    assert columns(e) == {"branches": {"id", "name"}, "invoices": {"branch_id", "total"}}
    assert e.unresolved_columns == {"id"}  # in both tables
    partial = explain(
        "SELECT name, total FROM branches b JOIN invoices i ON i.branch_id = b.id",
        schema={"branches": ["id", "name"]},
    )
    assert partial.unresolved_columns == {"name", "total"}  # invoices is not in the schema


MASKED = {"customers.email": "hash", "customers.phone": "partial"}
CUSTOMERS = ["id", "name", "email", "phone"]


def test_masked_columns() -> None:
    e = explain("SELECT name, email FROM customers", hidden_columns=set(), masked_columns=MASKED)
    assert e.ok
    assert columns(e) == {"customers": {"name", "email"}}
    assert dict(e.masked_columns) == {"customers.email": "hash"}
    assert e.text == (
        "Reads customers.email and customers.name, masking customers.email (hash), up to 1000 rows."
    )
    assert e.to_text("es") == (
        "Lee customers.email y customers.name, enmascarando customers.email (hash), "
        "hasta 1000 filas."
    )


def test_star_over_masked_columns_with_schema() -> None:
    e = explain(
        "SELECT * FROM customers",
        hidden_columns=set(),
        masked_columns=MASKED,
        schema={"customers": CUSTOMERS},
    )
    assert e.ok
    assert dict(e.masked_columns) == MASKED
    assert "masking customers.email (hash) and customers.phone (partial)" in e.text


def test_masked_column_outside_the_select_list_is_rejected() -> None:
    e = explain(
        "SELECT id FROM customers WHERE email LIKE 'a%'",
        hidden_columns=set(),
        masked_columns=MASKED,
    )
    assert not e.ok
    assert e.codes == {ViolationCode.HIDDEN_COLUMN}
    assert dict(e.masked_columns) == {}
    assert columns(e) == {"customers": {"id", "email"}}


def test_aggregate_only() -> None:
    e = explain(
        "SELECT branch_id, SUM(total) FROM invoices GROUP BY branch_id",
        aggregate_only={"invoices": 5},
    )
    assert e.ok and e.sql is not None and "HAVING COUNT(*) >= 5" in e.sql
    assert dict(e.aggregate_only) == {"invoices": 5}
    assert e.text == (
        "Reads invoices.branch_id and invoices.total, filtered by branch_id IN (1, 2) on "
        "invoices, only groups of at least 5 rows from invoices, up to 1000 rows."
    )
    assert "solo grupos de al menos 5 filas de invoices" in e.to_text("es")
    raw = explain("SELECT total FROM invoices", aggregate_only={"invoices": 5})
    assert raw.codes == {ViolationCode.AGGREGATE_REQUIRED}
    assert dict(raw.aggregate_only) == {}


def test_rejected_query_still_lists_what_it_reads() -> None:
    e = explain("SELECT name, email FROM customers")
    assert not e.ok and e.sql is None
    assert e.codes == {ViolationCode.HIDDEN_COLUMN}
    assert columns(e) == {"customers": {"name", "email"}}
    assert dict(e.row_filters) == {}
    assert e.limit is None
    assert e.text == (
        "Rejected: customers.email is a hidden column. "
        "It would read customers.email and customers.name."
    )


def test_rejected_table_is_reported_as_a_violation_not_a_read() -> None:
    e = explain("SELECT s.x, i.id FROM secrets s JOIN invoices i ON i.id = s.id")
    assert not e.ok
    assert e.tables == {"invoices"}
    assert e.codes == {ViolationCode.TABLE_NOT_ALLOWED}
    assert e.text.startswith("Rejected: secrets is not an allowed table.")


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("DELETE FROM invoices", ViolationCode.NOT_A_QUERY),
        ("SELEC id FROM", ViolationCode.PARSE_ERROR),
        ("", ViolationCode.EMPTY_STATEMENT),
    ],
)
def test_rejected_before_reading_anything(sql: str, code: ViolationCode) -> None:
    e = explain(sql)
    assert not e.ok
    assert code in e.codes
    assert e.tables == frozenset()
    assert e.text.startswith("Rejected: ")
    assert "would read" not in e.text


def test_missing_context_is_explained() -> None:
    e = explain("SELECT id FROM invoices", context=None)
    assert not e.ok
    assert e.codes == {ViolationCode.MISSING_CONTEXT}
    assert columns(e) == {"invoices": {"id"}}
    assert dict(e.row_filters) == {}
    assert e.text == (
        "Rejected: A row filter needs {user.branches}, which was not provided, so nothing ran. "
        "It would read invoices.id."
    )


def test_spanish() -> None:
    guard = Guard(standard_policy(), language="es")
    e = guard.explain(
        "SELECT b.name, i.total FROM invoices i JOIN branches b ON b.id = i.branch_id", CONTEXT
    )
    assert e.language == "es"
    assert e.text == (
        "Lee branches.id, branches.name, invoices.branch_id y invoices.total, "
        "filtrado por branch_id IN (1, 2) en invoices, hasta 1000 filas."
    )
    rejected = guard.explain("SELECT email FROM customers")
    assert rejected.text == (
        "Rechazada: customers.email es una columna oculta. Leería customers.email."
    )
    assert rejected.to_text("en") == (
        "Rejected: customers.email is a hidden column. It would read customers.email."
    )


def test_unknown_text_language() -> None:
    with pytest.raises(ValueError, match="language"):
        explain("SELECT 1").to_text("fr")


@pytest.mark.parametrize(
    ("sql", "max_rows", "limit", "words"),
    [
        ("SELECT id FROM products", 1000, 1000, "up to 1000 rows"),
        ("SELECT id FROM products LIMIT 5", 1000, 5, "up to 5 rows"),
        ("SELECT id FROM products LIMIT 1", 1000, 1, "up to 1 row"),
        ("SELECT id FROM products LIMIT 5000", 1000, 1000, "up to 1000 rows"),
        ("SELECT id FROM products", None, None, "with no row limit"),
        ("SELECT id FROM products LIMIT 7", None, 7, "up to 7 rows"),
        ("SELECT id FROM products UNION SELECT id FROM branches", 50, 50, "up to 50 rows"),
    ],
)
def test_limit(sql: str, max_rows: int | None, limit: int | None, words: str) -> None:
    e = explain(sql, max_rows=max_rows)
    assert e.limit == limit
    assert e.text.endswith(f"{words}.")


@pytest.mark.parametrize(
    ("dialect", "sql"),
    [
        ("tsql", "SELECT TOP 10 id FROM products"),
        ("oracle", "SELECT id FROM products FETCH FIRST 10 ROWS ONLY"),
    ],
)
def test_limit_in_other_dialects(dialect: str, sql: str) -> None:
    assert explain(sql, dialect=dialect).limit == 10


def test_percent_limit_is_not_a_row_count() -> None:
    e = Guard(Policy(dialect="tsql", tables={"products"}, max_rows=None)).explain(
        "SELECT TOP 50 PERCENT id FROM products"
    )
    assert e.ok
    assert e.limit is None


def test_to_dict_is_json_ready() -> None:
    e = explain("SELECT b.name FROM branches b")
    data = json.loads(json.dumps(e.to_dict()))
    assert data == {
        "ok": True,
        "sql": e.sql,
        "tables": ["branches"],
        "columns": {"branches": ["name"]},
        "unresolved_columns": [],
        "masked_columns": {},
        "aggregate_only": {},
        "row_filters": {},
        "limit": 1000,
        "violations": [],
        "text": "Reads branches.name, up to 1000 rows.",
    }
    rejected = explain("SELECT email FROM customers").to_dict()
    assert rejected["violations"] == [
        {
            "code": "hidden_column",
            "message": "customers.email is a hidden column",
            "detail": "customers.email",
        }
    ]


def test_explanation_is_immutable() -> None:
    e = explain("SELECT id FROM invoices")
    with pytest.raises(TypeError):
        e.columns["invoices"] = frozenset()  # type: ignore[index]
    with pytest.raises(TypeError):
        e.row_filters["invoices"] = "true"  # type: ignore[index]


def test_explain_does_not_change_the_guard() -> None:
    guard = Guard(standard_policy())
    sql = "SELECT id FROM invoices"
    before = guard.check(sql, CONTEXT)
    guard.explain(sql, CONTEXT)
    assert guard.check(sql, CONTEXT) == before


def test_non_string_sql() -> None:
    with pytest.raises(TypeError):
        Guard(standard_policy()).explain(123)  # type: ignore[arg-type]
