"""Rule 8: bound the rows the outermost query returns."""

from __future__ import annotations

from sqlglot import exp


def _inexact(limit: exp.Expression) -> bool:
    """``PERCENT`` or ``WITH TIES``: the limit is not an upper bound on rows.

    sqlglot keeps both in ``limit_options`` for ``TOP`` (``Limit``) and ``FETCH``.
    """
    options = limit.args.get("limit_options")
    return isinstance(options, exp.Expression) and bool(
        options.args.get("percent") or options.args.get("with_ties")
    )


def _literal_int(node: exp.Expression | None) -> int | None:
    literal = node.unnest() if node is not None else None
    if isinstance(literal, exp.Literal) and literal.is_int:
        return int(literal.name)
    return None


def _limit_tsql_ordered_set(root: exp.SetOperation, max_rows: int) -> exp.SetOperation:
    existing = current_limit(root)
    if existing == 0:
        # FETCH requires a positive count. SQLGlot renders LIMIT 0 as TOP 0 without an offset.
        root.set("offset", None)
        root.set("limit", exp.Limit(expression=exp.Literal.number(0)))
        return root

    count = max_rows if existing is None else min(existing, max_rows)
    if root.args.get("offset") is None:
        root.set("offset", exp.Offset(expression=exp.Literal.number(0)))
    root.set(
        "limit",
        exp.Fetch(
            direction="NEXT",
            count=exp.Literal.number(count),
            limit_options=exp.LimitOptions(rows=True),
        ),
    )
    return root


def apply_limit(
    root: exp.Expression, max_rows: int | None, *, output_dialect: str
) -> exp.Expression:
    """Add, lower or replace the outermost limit."""
    if max_rows is None:
        return root
    if (
        output_dialect == "tsql"
        and isinstance(root, exp.SetOperation)
        and root.args.get("order") is not None
    ):
        # T-SQL needs ORDER BY at the outer level to order the returned rows.
        return _limit_tsql_ordered_set(root, max_rows)
    if not isinstance(root, exp.Select):
        # UNION / INTERSECT / EXCEPT: limit the combined result, portable across dialects.
        wrapped = exp.select("*").from_(
            exp.Subquery(this=root, alias=exp.TableAlias(this=exp.to_identifier("sqlguard_rows")))
        )
        return wrapped.limit(max_rows, copy=False)

    current = root.args.get("limit")
    if current is None:
        return root.limit(max_rows, copy=False)
    if _inexact(current):
        # TOP 50 PERCENT or FETCH … WITH TIES could return more than max_rows rows.
        root.set("limit", None)
        return root.limit(max_rows, copy=False)
    if isinstance(current, exp.Limit):
        value = _literal_int(current.expression)
        if value is None or value > max_rows:
            current.set("expression", exp.Literal.number(max_rows))
        return root
    if isinstance(current, exp.Fetch) and not current.args.get("percent"):
        value = _literal_int(current.args.get("count"))
        if value is None or value > max_rows:
            current.set("count", exp.Literal.number(max_rows))
        return root
    # Anything else (percent limits, unknown forms): replace with a plain limit.
    root.set("limit", None)
    return root.limit(max_rows, copy=False)


def current_limit(root: exp.Expression) -> int | None:
    """The literal row limit of the outermost query, or ``None`` when it has none.

    A percentage (``TOP 50 PERCENT``) is not a row count, so it is reported as ``None``.
    """
    current = root.args.get("limit")
    if isinstance(current, exp.Expression) and _inexact(current):
        return None
    if isinstance(current, exp.Limit):
        return _literal_int(current.expression)
    if isinstance(current, exp.Fetch) and not current.args.get("percent"):
        return _literal_int(current.args.get("count"))
    return None
