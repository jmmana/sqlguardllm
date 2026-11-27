"""The fuzzing schema, its canary data and the oracle that reads query output.

The schema is the demo schema of the corpus (``tests/conftest.py``), with row filters on three
tables and masked columns, so a regression found here replays through ``standard_policy`` with
the overrides in ``FUZZ_POLICY``.

Two worlds hold the data. They are identical for everything any role may see and differ only in
what no role may see: hidden and masked column values, and the rows of branch 4, which no role is
given. Both have the same number of rows in the same physical order, so an engine plans a query
the same way in both. In both worlds:

- every row of a row-filtered table carries ``CANARY_T<branch>_…`` in its text columns, so a row
  of a branch outside the role's context is recognised in any output cell;
- every hidden value contains ``canary_hidden`` and every masked value ``canary_masked``, so any
  of them in an output cell is a leak, whatever the query did to get it there.

World B changes every secret: other hidden values (sorting the other way round), other masked
values (same nullness and first two characters, which the ``partial`` mask shows by design) and
other values, keys included, in branch 4. An allowed query must return the same rows, in the
same order, in both worlds; if it does not, secret data reached the output without being copied
into it (a comparison in ``HAVING``, an ``ORDER BY`` cut by the row limit, a ``SUM`` over another
branch…). Neither check reads the guard's code or its rewritten SQL: they only look at rows.
"""

from __future__ import annotations

import datetime
import math
import re
from collections.abc import Collection, Iterable, Sequence
from decimal import Decimal
from typing import Any

MAX_ROWS = 7

# Table -> ((column, type), ...). Types are "int" or "text"; both map to portable SQL types.
COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "branches": (("id", "int"), ("name", "text"), ("region", "text"), ("tag", "text")),
    "customers": (
        ("id", "int"),
        ("name", "text"),
        ("segment", "text"),
        ("city", "text"),
        ("email", "text"),
        ("tax_id", "text"),
        ("phone", "text"),
    ),
    "products": (
        ("id", "int"),
        ("name", "text"),
        ("category", "text"),
        ("price", "int"),
        ("supplier", "text"),
    ),
    "invoices": (
        ("id", "int"),
        ("branch_id", "int"),
        ("customer_id", "int"),
        ("total", "int"),
        ("status", "text"),
        ("issued_at", "text"),
        ("tag", "text"),
    ),
    "invoice_lines": (
        ("id", "int"),
        ("invoice_id", "int"),
        ("product_id", "int"),
        ("branch_id", "int"),
        ("quantity", "int"),
        ("amount", "int"),
        ("discount_code", "text"),
        ("tag", "text"),
    ),
}
TABLES = tuple(COLUMNS)
HIDDEN = frozenset({"customers.email", "customers.tax_id"})  # as in tests/conftest.py
MASKED = {
    "customers.phone": "redact",
    "products.supplier": "partial",
    "invoice_lines.discount_code": "null",
}
FILTERED = {
    "invoices": "branch_id IN ({user.branches})",
    "invoice_lines": "branch_id IN ({user.branches})",
    "branches": "id IN ({user.branches})",
}
# Foreign keys, used by the generator to write joins that return rows.
JOINS: tuple[tuple[str, str, str, str], ...] = (
    ("invoices", "customer_id", "customers", "id"),
    ("invoices", "branch_id", "branches", "id"),
    ("invoice_lines", "invoice_id", "invoices", "id"),
    ("invoice_lines", "product_id", "products", "id"),
    ("invoice_lines", "branch_id", "branches", "id"),
)

# Overrides of tests/conftest.py ``standard_policy``. Hidden columns stay on an unfiltered table:
# with a hidden column on a row-filtered table the guard rejects its own output (the filter's
# ``SELECT *`` reads the hidden column), which fails closed but breaks invariant 5 by design.
FUZZ_POLICY: dict[str, Any] = {
    "row_filters": dict(FILTERED),
    "masked_columns": dict(MASKED),
    "max_rows": MAX_ROWS,
    "schema": {table: [name for name, _ in columns] for table, columns in COLUMNS.items()},
}

# Roles: the authenticated user's context. Branch 4 belongs to nobody.
ROLES: dict[str, dict[str, Any]] = {
    "north": {"user": {"branches": [1, 2]}},
    "east": {"user": {"branches": [3]}},
}
BRANCHES_A = (1, 2, 3, 4)
SECRET_BRANCHES = frozenset({4})

SEGMENTS = ("retail", "wholesale", "online")
CITIES = ("Lima", "Quito", "Bogota", "Cusco")
STATUSES = ("open", "paid", "void")
CATEGORIES = ("tools", "food", "toys")

FOREIGN_TAG = re.compile(r"canary_t(\d+)_", re.IGNORECASE)
HIDDEN_TAG = re.compile(r"canary_hidden", re.IGNORECASE)
MASKED_TAG = re.compile(r"canary_masked", re.IGNORECASE)

Row = tuple[Any, ...]


def _mix(*values: int) -> int:
    """A small deterministic hash, so data looks varied but never changes between runs."""
    state = 2166136261
    for value in values:
        state = ((state ^ value) * 16777619) % 2**32
    return state


def rows(world: str) -> dict[str, list[Row]]:
    """The rows of every table in world ``"A"`` or ``"B"``.

    Both worlds have the same rows in the same order; only secret values differ.
    """
    b = world == "B"
    data: dict[str, list[Row]] = {table: [] for table in TABLES}

    for branch in BRANCHES_A:
        name = f"CANARY_T{branch}_branch"
        region = "south" if branch % 2 else "north"
        if b and branch in SECRET_BRANCHES:
            name, region = f"CANARY_T{branch}_branch_renamed", "west"
        data["branches"].append((branch, name, region, f"CANARY_T{branch}_branches_{branch}"))

    for cid in range(1, 13):
        # Hidden values sort one way in A and the other way in B, with different lengths.
        email = f"a.canary_hidden.{cid:02d}@example.com"
        tax_id = f"CANARY_HIDDEN_TAX_{cid:03d}"
        phone: str | None = f"CANARY_MASKED_PHONE_{cid}" if cid % 4 else None
        if b:
            email = f"z.canary_hidden.{13 - cid:02d}.other@example.org"
            tax_id = f"canary_hidden_tax_{999 - cid}_b"
            phone = f"CAnary_masked_{cid * 7}_b" if phone is not None else None
        data["customers"].append(
            (
                cid,
                f"cust_{cid:02d}",
                SEGMENTS[cid % 3],
                CITIES[_mix(cid) % 4],
                email,
                tax_id,
                phone,
            )
        )

    for pid in range(1, 11):
        supplier = f"CANARY_MASKED_SUP_{pid}" if not b else f"CAnary_masked_sup_{pid * 3}_b"
        data["products"].append(
            (pid, f"prod_{pid}", CATEGORIES[pid % 3], 5 + _mix(pid, 7) % 95, supplier)
        )

    for branch in BRANCHES_A:
        alter = b and branch in SECRET_BRANCHES  # branch 4 is nobody's: every value changes in B
        for n in range(4):
            iid = branch * 100 + 1 + n
            total = 10 + _mix(iid, branch) % 990
            status = STATUSES[_mix(iid) % 3]
            customer = 1 + _mix(iid, 5) % 12
            issued = f"2026-{1 + n:02d}-{1 + _mix(iid, 3) % 28:02d}"
            if alter:
                total, status, customer, issued = total * 3 + 1, "void", 13 - customer, "2027-12-31"
            tag = f"CANARY_T{branch}_invoices_{iid}"
            data["invoices"].append((iid, branch, customer, total, status, issued, tag))
            for k in range(3):
                lid = iid * 10 + k
                product = 1 + _mix(lid, 17) % 10
                quantity = 1 + _mix(lid) % 9
                amount = 1 + _mix(lid, 11) % 200
                if alter:
                    product, quantity, amount = 11 - product, quantity + 50, amount * 5
                code: str | None = None
                if _mix(lid, 13) % 3:
                    code = f"CANARY_MASKED_DISC_{lid}" if not b else f"CAnary_masked_{lid}_b"
                line_tag = f"CANARY_T{branch}_invoice_lines_{lid}"
                data["invoice_lines"].append(
                    (lid, iid, product, branch, quantity, amount, code, line_tag)
                )
    return data


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def ddl(table: str) -> str:
    """Portable CREATE TABLE for every engine."""
    types = {"int": "INT", "text": "VARCHAR(80)"}
    columns = ", ".join(f"{name} {types[kind]}" for name, kind in COLUMNS[table])
    return f"CREATE TABLE {table} ({columns})"


def inserts(table: str, values: Sequence[Row]) -> list[str]:
    """INSERT statements, a few rows each (every engine accepts multi-row VALUES)."""
    statements = []
    for start in range(0, len(values), 20):
        chunk = values[start : start + 20]
        body = ", ".join("(" + ", ".join(_sql_literal(v) for v in row) + ")" for row in chunk)
        statements.append(f"INSERT INTO {table} VALUES {body}")
    return statements


def normalize(value: Any) -> Any:
    """One comparable form for values from any driver (Decimal, float, bytes, dates…)."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return repr(value)
        if value.is_integer() and abs(value) < 2**53:
            return int(value)
        return round(value, 6)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    return str(value)


def normalize_rows(result: Iterable[Sequence[Any]]) -> list[Row]:
    return [tuple(normalize(v) for v in row) for row in result]


def leaks(result: Sequence[Row], allowed: Collection[int]) -> list[tuple[str, str]]:
    """Canaries in the output, as ``(invariant, cell)``, for a user of the ``allowed`` branches.

    Independent of the guard: it only reads values.
    """
    found: list[tuple[str, str]] = []
    for row in result:
        for cell in row:
            if not isinstance(cell, str):
                continue
            if HIDDEN_TAG.search(cell) or MASKED_TAG.search(cell):
                found.append(("hidden_or_masked", cell))
            for match in FOREIGN_TAG.finditer(cell):
                if int(match.group(1)) not in allowed:
                    found.append(("foreign_rows", cell))
    return found
