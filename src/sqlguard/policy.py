"""The policy: what a query may read, for whom, and how much."""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from sqlguard.result import PolicyError

FunctionPolicy = Literal["denylist", "allowlist"]


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

        YAML needs the optional extra: ``pip install "sqlguard[yaml]"``.
        """
        file = Path(path)
        text = file.read_text(encoding="utf-8")
        if file.suffix.lower() in (".yaml", ".yml"):
            try:
                import yaml  # noqa: PLC0415
            except ImportError as exc:  # pragma: no cover - depends on the environment
                raise PolicyError('YAML policies need: pip install "sqlguard[yaml]"') from exc
            data = yaml.safe_load(text)
        else:
            data = json.loads(text)
        if not isinstance(data, Mapping):
            raise PolicyError(f"{file} must contain a mapping at the top level")
        return cls.from_dict(data)
