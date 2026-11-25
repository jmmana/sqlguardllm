"""Rule 8: bound the rows the outermost query returns."""

from __future__ import annotations

from sqlglot import exp


def _literal_int(node: exp.Expression | None) -> int | None:
    if isinstance(node, exp.Literal) and node.is_int:
        return int(node.name)
    return None


def apply_limit(root: exp.Expression, max_rows: int | None) -> exp.Expression:
    """Add, lower or replace the outermost limit. Set operations are wrapped first."""
    if max_rows is None:
        return root
    if not isinstance(root, exp.Select):
        # UNION / INTERSECT / EXCEPT: limit the combined result, portable across dialects.
        wrapped = exp.select("*").from_(
            exp.Subquery(this=root, alias=exp.TableAlias(this=exp.to_identifier("sqlguard_rows")))
        )
        return wrapped.limit(max_rows, copy=False)

    current = root.args.get("limit")
    if current is None:
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
