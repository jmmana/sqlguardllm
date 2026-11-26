"""Hypothesis strategies that build SQLGlot query trees for the fuzz engines."""

from __future__ import annotations

from typing import Any

import sqlglot
from hypothesis import strategies as st
from sqlglot import exp

TABLES = ("invoices", "invoice_lines", "branches", "customers", "products")
COLUMNS = ("id", "branch_id", "related_id", "label", "payload", "hidden_value", "masked_value")
SHAPES = (
    "simple",
    "join",
    "subquery",
    "cte",
    "recursive",
    "set",
    "having",
    "window",
    "order_alias",
    "distinct_on",
    "qualify",
    "fetch",
)


def _identifier(name: str, quoted: bool) -> str:
    return f'"{name}"' if quoted else name


@st.composite
def query_asts(draw: Any, dialect: str) -> exp.Expression:
    """Build bounded, engine-compatible SQLGlot ASTs using the fuzz schema."""
    shape = draw(st.sampled_from(SHAPES))
    table = draw(st.sampled_from(TABLES))
    other_table = draw(st.sampled_from(tuple(name for name in TABLES if name != table)))
    column = draw(st.sampled_from(COLUMNS))
    alias = draw(st.sampled_from(("t", "T")))
    quoted = draw(st.booleans())
    table_case = table if quoted else draw(st.sampled_from((table, table.upper())))
    source = f"{_identifier(table_case, quoted)} AS {_identifier(alias, quoted)}"
    column_ref = f"{_identifier(alias, quoted)}.{_identifier(column, quoted)}"

    projection = column_ref
    if shape not in {"subquery", "cte", "set"} and draw(st.booleans()):
        projection += " AS result"
    base = sqlglot.parse_one(
        f"SELECT {projection} FROM {source}",
        read=dialect,
    )

    if shape == "join":
        base = base.join(
            f"{_identifier(other_table, quoted)} AS u",
            on=f"{_identifier(alias, quoted)}.branch_id = u.branch_id",
        )
    elif shape == "subquery":
        inner = base.sql(dialect=dialect)
        base = sqlglot.parse_one(
            f"SELECT q.{_identifier(column, quoted)} FROM ({inner}) AS q",
            read=dialect,
        )
    elif shape == "cte":
        inner = base.sql(dialect=dialect)
        base = sqlglot.parse_one(
            f"WITH q AS ({inner}) SELECT q.{_identifier(column, quoted)} FROM q",
            read=dialect,
        )
    elif shape == "recursive":
        base = sqlglot.parse_one(
            "WITH RECURSIVE n(x) AS "
            "(SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 2) SELECT x FROM n",
            read=dialect,
        )
    elif shape == "set":
        duplicate = sqlglot.parse_one(
            f"SELECT {_identifier(alias, quoted)}.{_identifier(column, quoted)} "
            f"FROM {_identifier(table_case, quoted)} AS {_identifier(alias, quoted)}",
            read=dialect,
        )
        base = exp.Union(this=base, expression=duplicate, distinct=False)
    elif shape == "having":
        hidden_predicate = draw(st.booleans())
        condition = (
            f"MAX({_identifier(alias, quoted)}.hidden_value) IS NOT NULL"
            if hidden_predicate
            else "COUNT(*) > 0"
        )
        base = sqlglot.parse_one(
            f"SELECT COUNT(*) AS n FROM {source} "
            f"GROUP BY {_identifier(alias, quoted)}.branch_id HAVING {condition}",
            read=dialect,
        )
    elif shape == "window":
        base = sqlglot.parse_one(
            f"SELECT {_identifier(alias, quoted)}.{_identifier(column, quoted)}, "
            f"ROW_NUMBER() OVER (ORDER BY {_identifier(alias, quoted)}.label) AS rn "
            f"FROM {_identifier(table_case, quoted)} AS {_identifier(alias, quoted)}",
            read=dialect,
        )
    elif shape == "order_alias":
        base = sqlglot.parse_one(
            f"SELECT {column_ref} AS result FROM {source} ORDER BY result",
            read=dialect,
        )
    elif shape == "distinct_on" and dialect in {"postgres", "duckdb"}:
        base = sqlglot.parse_one(
            f"SELECT DISTINCT ON ({_identifier(alias, quoted)}.label) "
            f"{_identifier(alias, quoted)}.{_identifier(column, quoted)} "
            f"FROM {_identifier(table_case, quoted)} AS {_identifier(alias, quoted)} "
            f"ORDER BY {_identifier(alias, quoted)}.label",
            read=dialect,
        )
    elif shape == "qualify" and dialect == "duckdb":
        base = sqlglot.parse_one(
            f"SELECT {_identifier(alias, quoted)}.{_identifier(column, quoted)}, "
            f"ROW_NUMBER() OVER (ORDER BY {_identifier(alias, quoted)}.label) AS rn "
            f"FROM {source} QUALIFY rn = 1",
            read=dialect,
        )
    elif shape == "fetch" and dialect == "postgres":
        base = sqlglot.parse_one(
            f"SELECT {column_ref} FROM {source} ORDER BY label FETCH FIRST 10 ROWS WITH TIES",
            read=dialect,
        )
    elif shape == "fetch" and dialect == "tsql":
        base = sqlglot.parse_one(
            f"SELECT TOP (100) PERCENT WITH TIES {column_ref} FROM {source} ORDER BY label",
            read=dialect,
        )
    else:
        base = base.limit(draw(st.integers(min_value=1, max_value=20)))

    if draw(st.booleans()):
        base.add_comments([" fuzz"])
    return base
