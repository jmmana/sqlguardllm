"""Query cost estimates read from ``EXPLAIN`` output. Pure functions, no database access."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

__all__ = ["CostEstimate", "explain_statement", "parse_plan_cost"]


@dataclass(frozen=True)
class CostEstimate:
    """What the planner expects, in the engine's own units (not comparable across engines)."""

    cost: float
    rows: float | None = None


def explain_statement(engine: str, sql: str) -> str | None:
    """The ``EXPLAIN`` statement for ``engine`` (never ``ANALYZE``, which runs the query)."""
    if engine == "postgresql":
        return f"EXPLAIN (FORMAT JSON) {sql}"
    if engine == "mysql":
        return f"EXPLAIN FORMAT=JSON {sql}"
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _mysql_rows(node: Any) -> float | None:
    """The largest row estimate of any table in a MySQL plan (a pessimistic bound)."""
    best: float | None = None
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key in ("rows_examined_per_scan", "rows_produced_per_join"):
                found = _number(current.get(key))
                if found is not None and (best is None or found > best):
                    best = found
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return best


def parse_plan_cost(engine: str, json_text: str) -> CostEstimate:
    """Read the estimated cost and rows from the JSON ``EXPLAIN`` output of ``engine``.

    ``engine`` is the SQLAlchemy backend name (``postgresql`` or ``mysql``).

    Raises:
        ValueError: If the engine is unsupported or the output has no usable cost.
    """
    try:
        data = json.loads(json_text)
    except (TypeError, ValueError):
        raise ValueError("the plan is not valid JSON") from None
    if engine == "postgresql":
        try:
            plan = data[0]["Plan"]
            cost = _number(plan["Total Cost"])
        except (KeyError, IndexError, TypeError):
            raise ValueError("the plan has no Total Cost") from None
        if cost is None:
            raise ValueError("the plan has no Total Cost")
        return CostEstimate(cost, _number(plan.get("Plan Rows")))
    if engine == "mysql":
        try:
            block = data["query_block"]
            cost = _number(block["cost_info"]["query_cost"])
        except (KeyError, TypeError):
            # Plans without cost_info (e.g. "no tables used") cost nothing to read.
            if isinstance(data, dict) and isinstance(data.get("query_block"), dict):
                return CostEstimate(0.0, 0.0)
            raise ValueError("the plan has no query_cost") from None
        if cost is None:
            raise ValueError("the plan has no query_cost")
        return CostEstimate(cost, _mysql_rows(block))
    raise ValueError(f"cost estimates are not supported for {engine}")
