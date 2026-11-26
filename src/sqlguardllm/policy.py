"""The policy: what a query may read, for whom, and how much."""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from sqlguardllm.result import PolicyError

FunctionPolicy = Literal["denylist", "allowlist"]
MaskStrategy = Literal["null", "redact", "hash", "partial"]
# SQLAlchemy dialect name -> sqlglot dialect name, where they differ.
_DIALECTS = {"mssql": "tsql"}
MASK_STRATEGIES: tuple[str, ...] = ("null", "redact", "hash", "partial")


def _frozen(value: Collection[str] | None, name: str) -> frozenset[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        raise PolicyError(f"{name} must be a collection of strings, not a single string")
    items = frozenset(value)
    if not all(isinstance(item, str) and item.strip() for item in items):
        raise PolicyError(f"{name} must contain non-empty strings")
    return items


@dataclass(frozen=True)
class Policy:
    """What SQL written by an untrusted source may do.

    Args:
        dialect: Any dialect name sqlglot understands (``"postgres"``, ``"tsql"``, ``"oracle"``…).
        tables: Allowlisted tables, optionally qualified (``"sales.customers"``). ``None`` disables
            the table allowlist, which is not recommended.
        hidden_columns: Columns that may never be referenced, as ``"table.column"``.
        row_filters: Table → boolean SQL expression with ``{context.path}`` placeholders.
        max_rows: Maximum rows a query may return. ``None`` disables the limit.
        default_schema: Schema assumed for unqualified table references.
        denied_functions: Replaces the built-in denylist for the dialect when given.
        extra_denied_functions: Added to the denylist.
        function_policy: ``"denylist"`` (default) or ``"allowlist"``.
        allowed_functions: Functions allowed in ``"allowlist"`` mode.
        max_joins: Maximum joins in the whole query, across every scope. ``None`` = no limit.
        max_subquery_depth: Maximum nesting of subqueries, derived tables and CTEs. ``0`` allows
            none. ``None`` = no limit.
        forbid_cartesian: Reject joins with no ``ON``/``USING`` whose tables aren't linked by an
            equality in ``WHERE``, and every explicit ``CROSS JOIN``.
        required_filters: Table → columns. Every read of the table must be filtered on at least
            one of them in its own ``WHERE`` or ``JOIN … ON``, such as a date range on a large
            fact table.
        output_dialect: Generate the rewritten SQL in this dialect instead of ``dialect``.
        masked_columns: ``"table.column"`` → ``null``, ``redact``, ``hash`` or ``partial``. The
            column may be selected but comes back masked; using it to filter, join, group or
            sort is ``hidden_column``.
        aggregate_only: Table → minimum group size ``k``. The table can only be read through
            aggregates, and groups smaller than ``k`` never come back.
        schema: Table → its columns. Lets ``*`` over a table with masked columns be expanded
            with the masks instead of rejected.
    """

    dialect: str
    tables: Collection[str] | None = None
    hidden_columns: Collection[str] = frozenset()
    row_filters: Mapping[str, str] = field(default_factory=dict)
    max_rows: int | None = 1000
    default_schema: str | None = None
    denied_functions: Collection[str] | None = None
    extra_denied_functions: Collection[str] = frozenset()
    function_policy: FunctionPolicy = "denylist"
    allowed_functions: Collection[str] | None = None
    max_joins: int | None = None
    max_subquery_depth: int | None = None
    forbid_cartesian: bool = False
    required_filters: Mapping[str, Collection[str]] = field(default_factory=dict)
    output_dialect: str | None = None
    masked_columns: Mapping[str, str] = field(default_factory=dict)
    aggregate_only: Mapping[str, int] = field(default_factory=dict)
    schema: Mapping[str, Collection[str]] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.dialect, str) or not self.dialect.strip():
            raise PolicyError("dialect must be a non-empty string")
        object.__setattr__(self, "tables", _frozen(self.tables, "tables"))
        object.__setattr__(self, "hidden_columns", _frozen(self.hidden_columns, "hidden_columns"))
        object.__setattr__(
            self, "denied_functions", _frozen(self.denied_functions, "denied_functions")
        )
        object.__setattr__(
            self,
            "extra_denied_functions",
            _frozen(self.extra_denied_functions, "extra_denied_functions"),
        )
        object.__setattr__(
            self, "allowed_functions", _frozen(self.allowed_functions, "allowed_functions")
        )
        if not isinstance(self.row_filters, Mapping) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in self.row_filters.items()
        ):
            raise PolicyError("row_filters must map table names to SQL condition strings")
        object.__setattr__(self, "row_filters", MappingProxyType(dict(self.row_filters)))
        if self.max_rows is not None and (
            isinstance(self.max_rows, bool)
            or not isinstance(self.max_rows, int)
            or self.max_rows <= 0
        ):
            raise PolicyError("max_rows must be a positive integer or None")
        if self.function_policy not in ("denylist", "allowlist"):
            raise PolicyError('function_policy must be "denylist" or "allowlist"')
        if self.function_policy == "allowlist" and self.allowed_functions is None:
            raise PolicyError('function_policy="allowlist" requires allowed_functions')
        for name in ("max_joins", "max_subquery_depth"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise PolicyError(f"{name} must be a non-negative integer or None")
        if not isinstance(self.forbid_cartesian, bool):
            raise PolicyError("forbid_cartesian must be true or false")
        if not isinstance(self.required_filters, Mapping):
            raise PolicyError("required_filters must map table names to lists of columns")
        required: dict[str, frozenset[str]] = {}
        for table, columns in self.required_filters.items():
            if not isinstance(table, str) or not table.strip():
                raise PolicyError("required_filters keys must be non-empty table names")
            frozen = _frozen(columns, f"required_filters[{table!r}]")
            if not frozen:
                raise PolicyError(f"required_filters[{table!r}] needs at least one column")
            required[table] = frozen
        object.__setattr__(self, "required_filters", MappingProxyType(required))
        if self.output_dialect is not None and (
            not isinstance(self.output_dialect, str) or not self.output_dialect.strip()
        ):
            raise PolicyError("output_dialect must be a non-empty string or None")
        if not isinstance(self.masked_columns, Mapping) or not all(
            isinstance(k, str) and v in MASK_STRATEGIES for k, v in self.masked_columns.items()
        ):
            raise PolicyError(
                f"masked_columns must map table.column to one of {', '.join(MASK_STRATEGIES)}"
            )
        object.__setattr__(self, "masked_columns", MappingProxyType(dict(self.masked_columns)))
        overlap = sorted(set(self.masked_columns) & set(self.hidden_columns))
        if overlap:
            raise PolicyError(f"a column can't be both hidden and masked: {', '.join(overlap)}")
        if not isinstance(self.aggregate_only, Mapping) or not all(
            isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v >= 2
            for k, v in self.aggregate_only.items()
        ):
            raise PolicyError("aggregate_only must map table names to a minimum group size >= 2")
        object.__setattr__(self, "aggregate_only", MappingProxyType(dict(self.aggregate_only)))
        if self.schema is not None:
            if not isinstance(self.schema, Mapping):
                raise PolicyError("schema must map table names to lists of columns")
            frozen_schema: dict[str, tuple[str, ...]] = {}
            for table, columns in self.schema.items():
                if not isinstance(table, str) or not table.strip():
                    raise PolicyError("schema keys must be non-empty table names")
                if isinstance(columns, str) or not columns:
                    raise PolicyError(f"schema[{table!r}] must be a non-empty list of columns")
                items = tuple(columns)
                if not all(isinstance(c, str) and c.strip() for c in items):
                    raise PolicyError(f"schema[{table!r}] must contain non-empty strings")
                frozen_schema[table] = items
            object.__setattr__(self, "schema", MappingProxyType(frozen_schema))

    @classmethod
    def from_database(
        cls,
        url: str,
        *,
        include: Collection[str] | None = None,
        exclude: Collection[str] | None = None,
        schemas: Collection[str] | None = None,
        **options: Any,
    ) -> Policy:
        """Build a policy whose ``tables`` and ``schema`` are read from a live database.

        Needs ``pip install "sqlguardllm[db]"`` and your database driver. Only names are read.
        ``include``/``exclude`` are glob patterns over table names (``"invoice*"``,
        ``"audit_*"``); ``schemas`` limits which schemas are read. ``dialect`` is taken from the
        URL unless you pass it. Every other option (``hidden_columns``, ``row_filters``…) is
        passed through. Check the result: reading a database is a starting point, not a review.

        >>> Policy.from_database("postgresql+psycopg://reader@host/db", exclude=["audit_*"])
        """
        from sqlguardllm.introspect import read_database  # noqa: PLC0415

        found, dialect = read_database(url, include=include, exclude=exclude, schemas=schemas)
        if not found:
            raise PolicyError("no table matched: check the schemas and the include/exclude filters")
        values: dict[str, Any] = {"dialect": _DIALECTS.get(dialect, dialect)}
        values.update(options)
        values["tables"] = sorted(found)
        values["schema"] = found
        return cls(**values)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Policy:
        """Build a policy from a mapping, rejecting unknown keys."""
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise PolicyError(f"unknown policy keys: {', '.join(unknown)}")
        if "dialect" not in data:
            raise PolicyError("the policy needs a dialect")
        return cls(**dict(data))

    @classmethod
    def from_file(cls, path: str | Path) -> Policy:
        """Load a policy from a ``.json``, ``.yaml`` or ``.yml`` file.

        YAML needs the optional extra: ``pip install "sqlguardllm[yaml]"``.
        """
        return cls.from_dict(read_mapping(path))


def read_mapping(path: str | Path) -> Mapping[str, Any]:
    """Read a ``.json``, ``.yaml`` or ``.yml`` file whose top level is a mapping."""
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if file.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise PolicyError('YAML policies need: pip install "sqlguardllm[yaml]"') from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, Mapping):
        raise PolicyError(f"{file} must contain a mapping at the top level")
    return data
