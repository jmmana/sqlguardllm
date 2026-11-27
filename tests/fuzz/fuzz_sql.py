"""A hypothesis strategy that writes SQL over the fuzzing schema, as sqlglot syntax trees.

It writes what a model, or an attacker steering one, could write: joins, ``USING`` and
``NATURAL`` joins, derived tables, CTEs (recursive too), set operations, ``GROUP BY``/``HAVING``,
``QUALIFY``, window functions, ``DISTINCT ON``, ``ORDER BY`` over aliases and expressions,
``LIMIT``/``FETCH``/``TOP`` with ``PERCENT`` and ``WITH TIES``, correlated subqueries, quoting and
case variants of names, keyword case, whitespace and comments. Hidden and masked columns are
picked like any other column, qualified or not, so the past bypasses (an unqualified hidden
column in ``HAVING``/``QUALIFY``, an alias in ``ORDER BY``, ``TOP 50 PERCENT``) are reachable.

Choices are ordered simplest first, so hypothesis shrinks a failure to a short query.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from fuzz_schema import COLUMNS, JOINS, ROLES
from hypothesis import strategies as st
from sqlglot import exp
from sqlglot.dialects.dialect import Dialect
from sqlglot.errors import SqlglotError
from sqlglot.tokens import TokenType

INT_LITERALS = (1, 0, 2, 3, 5, 7, 10, 100, 1000)
TEXT_LITERALS = ("a", "m", "z", "open", "paid", "retail", "Lima", "%a%", "CANARY", "O'x", "")
ALIASES = ("x", "n", "total", "email", "name", "id", "phone", "tax_id", "v", "supplier")
TABLE_ALIASES = ("t", "c", "i", "l", "p", "b", "d", "s")
COMMENTS = (
    "/* c */",
    "-- c\n",
    "/* DROP TABLE invoices; */",
    "/*! , email */",
    "/*+ hint */",
    "/* a /* b */",
    "-- */ , email\n",
    "\n",
    "\t",
)


@dataclass(frozen=True)
class Col:
    name: str
    kind: str  # "int" or "text"


@dataclass
class Source:
    """Something in a FROM clause: a table, a derived table or a CTE, and its columns."""

    name: str  # table or CTE name ("" for a derived table)
    alias: str | None
    columns: tuple[Col, ...]
    physical: bool

    @property
    def ref(self) -> str:
        return self.alias or self.name


@dataclass
class Ctx:
    sources: list[Source]
    outer: list[Source] = field(default_factory=list)
    ctes: list[Source] = field(default_factory=list)
    depth: int = 0


@dataclass(frozen=True)
class FuzzCase:
    """One generated example: a syntax tree, the role that runs it and how to mangle its text."""

    tree: exp.Expression
    role: str
    mangle: int  # 0 = none, 1 = keyword case and spaces, 2 = also comments
    mangle_seed: int

    def __repr__(self) -> str:
        try:
            text = self.tree.sql(dialect="duckdb")
        except SqlglotError:
            text = repr(self.tree)
        return f"FuzzCase({text!r}, role={self.role!r}, mangle={self.mangle}/{self.mangle_seed})"


class Gen:
    def __init__(self, draw: Any) -> None:
        self.draw = draw
        self.counter = 0

    # -- choices (index 0 is the simplest; hypothesis shrinks towards it) -----------------------
    def pick(self, items: Any) -> Any:
        items = tuple(items)
        return items[self.draw(st.integers(0, len(items) - 1))]

    def chance(self, percent: int) -> bool:
        return self.draw(st.integers(0, 99)) >= 100 - percent

    def weighted(self, *weights: int) -> int:
        roll = self.draw(st.integers(0, sum(weights) - 1))
        for index, weight in enumerate(weights):
            if roll < weight:
                return index
            roll -= weight
        return len(weights) - 1

    # -- sources -------------------------------------------------------------------------------
    def table_source(self, used: set[str]) -> Source:
        table = self.pick(("customers", "invoices", "invoice_lines", "products", "branches"))
        alias: str | None = None
        if self.chance(55) or table in used:
            alias = self.pick(tuple(a for a in TABLE_ALIASES if a not in used) or ("z9",))
        if alias is None and self.chance(10):
            alias = table  # FROM customers AS customers
        columns = tuple(Col(name, kind) for name, kind in COLUMNS[table])
        return Source(table, alias, columns, physical=True)

    def from_item(self, ctx: Ctx, used: set[str]) -> tuple[exp.Expression, Source]:
        kind = self.weighted(70, 15 if ctx.depth < 2 else 0, 15 if ctx.ctes else 0)
        if kind == 1:
            select, columns = self.select(Ctx([], ctes=ctx.ctes, depth=ctx.depth + 1), named=True)
            alias = self.pick(tuple(a for a in ("d", "s", "q", "sub") if a not in used) or ("d9",))
            node = exp.Subquery(this=select, alias=exp.TableAlias(this=exp.to_identifier(alias)))
            return node, Source("", alias, tuple(columns), physical=False)
        if kind == 2:
            cte = self.pick(ctx.ctes)
            alias = None
            if self.chance(40):
                alias = self.pick(tuple(a for a in TABLE_ALIASES if a not in used) or ("z8",))
            source = Source(cte.name, alias, cte.columns, physical=False)
            return self.table_node(source), source
        source = self.table_source(used)
        return self.table_node(source), source

    @staticmethod
    def table_node(source: Source) -> exp.Table:
        table = exp.Table(this=exp.to_identifier(source.name))
        if source.alias is not None:
            table.set("alias", exp.TableAlias(this=exp.to_identifier(source.alias)))
        return table

    # -- columns and expressions ---------------------------------------------------------------
    def column(self, ctx: Ctx, kind: str | None, *, outer: bool = False) -> exp.Expression | None:
        pool = ctx.outer if outer else ctx.sources
        candidates = [
            (source, col)
            for source in pool
            for col in source.columns
            if kind is None or col.kind == kind
        ]
        if not candidates:
            return None
        source, col = self.pick(candidates)
        shared = sum(1 for s in ctx.sources if any(c.name == col.name for c in s.columns))
        qualify = outer or shared > 1 or len(ctx.sources) > 1
        if self.chance(25):
            qualify = not qualify  # unqualified where it is ambiguous, qualified where it isn't
        table = source.ref if qualify else None
        return exp.column(col.name, table=table)

    def literal(self, kind: str) -> exp.Expression:
        if kind == "int":
            return exp.Literal.number(self.pick(INT_LITERALS))
        return exp.Literal.string(self.pick(TEXT_LITERALS))

    def expr(self, ctx: Ctx, kind: str, level: int = 0, *, agg: bool = False) -> exp.Expression:
        """A non-aggregate expression of ``kind`` (aggregates come from :meth:`aggregate`)."""
        if agg and self.chance(50):
            return self.aggregate(ctx, kind)
        choice = self.weighted(55, 15, 20 if level < 2 else 0, 6 if ctx.depth < 2 else 0)
        if choice == 0:
            column = self.column(ctx, kind)
            if column is not None:
                return column
            return self.literal(kind)
        if choice == 1:
            return self.literal(kind)
        if choice == 3:
            return self.scalar_subquery(ctx, kind)
        inner = level + 1
        if kind == "int":
            op = self.weighted(3, 3, 2, 2, 2, 2)
            if op == 0:
                return exp.Add(this=self.expr(ctx, "int", inner), expression=self.literal("int"))
            if op == 1:
                return exp.Length(this=self.expr(ctx, "text", inner))
            if op == 2:
                return exp.Sub(
                    this=self.expr(ctx, "int", inner), expression=self.expr(ctx, "int", inner)
                )
            if op == 3:
                return exp.Abs(this=self.expr(ctx, "int", inner))
            if op == 4:
                return exp.Coalesce(
                    this=self.expr(ctx, "int", inner), expressions=[self.literal("int")]
                )
            return self.case(ctx, "int", inner)
        op = self.weighted(3, 3, 2, 2, 2, 2)
        if op == 0:
            return exp.Upper(this=self.expr(ctx, "text", inner))
        if op == 1:
            return exp.Lower(this=self.expr(ctx, "text", inner))
        if op == 2:
            return exp.Substring(
                this=self.expr(ctx, "text", inner),
                start=exp.Literal.number(1),
                length=exp.Literal.number(self.pick((3, 1, 20))),
            )
        if op == 3:
            return exp.Coalesce(
                this=self.expr(ctx, "text", inner), expressions=[self.literal("text")]
            )
        if op == 4:
            return exp.Concat(
                expressions=[self.expr(ctx, "text", inner), self.expr(ctx, "text", inner)]
            )
        return self.case(ctx, "text", inner)

    def case(self, ctx: Ctx, kind: str, level: int) -> exp.Expression:
        return exp.Case(
            ifs=[exp.If(this=self.predicate(ctx, level + 1), true=self.expr(ctx, kind, level))],
            default=self.expr(ctx, kind, level),
        )

    def aggregate(self, ctx: Ctx, kind: str) -> exp.Expression:
        if kind == "int":
            op = self.weighted(4, 3, 3, 2, 2, 1)
            if op == 0:
                return exp.Count(this=exp.Star())
            if op == 1:
                return exp.Sum(this=self.expr(ctx, "int", 1))
            if op == 2:
                return exp.Count(this=self.expr(ctx, self.pick(("int", "text")), 1))
            if op == 3:
                return exp.Max(this=self.expr(ctx, "int", 1))
            if op == 4:
                return exp.Count(this=exp.Distinct(expressions=[self.expr(ctx, "text", 1)]))
            return exp.Length(this=exp.Max(this=self.expr(ctx, "text", 1)))
        op = self.weighted(1, 1)
        node = exp.Max if op == 0 else exp.Min
        return node(this=self.expr(ctx, "text", 1))

    def window(self, ctx: Ctx) -> exp.Expression:
        func: exp.Expression = self.pick(
            (
                exp.RowNumber(),
                exp.Anonymous(this="RANK", expressions=[]),
                exp.Sum(this=self.expr(ctx, "int", 2)),
                exp.Count(this=exp.Star()),
            )
        )
        partition = [self.expr(ctx, self.pick(("int", "text")), 2)] if self.chance(50) else []
        order = exp.Order(
            expressions=[exp.Ordered(this=self.expr(ctx, self.pick(("int", "text")), 2))]
        )
        return exp.Window(this=func, partition_by=partition, order=order)

    def scalar_subquery(self, ctx: Ctx, kind: str) -> exp.Expression:
        inner = Ctx([], outer=ctx.sources + ctx.outer, ctes=ctx.ctes, depth=ctx.depth + 1)
        node, source = self.from_item(inner, set())
        inner.sources = [source]
        select = exp.select(self.aggregate(inner, kind)).from_(node)
        if self.chance(40):
            select = select.where(self.correlation(inner) or self.predicate(inner, 1))
        return exp.Subquery(this=select)

    def correlation(self, inner: Ctx) -> exp.Expression | None:
        """``inner.col = outer.col`` with matching types, when there is an outer scope."""
        if not inner.outer:
            return None
        kind = self.pick(("int", "text"))
        left = self.column(inner, kind)
        right = self.column(inner, kind, outer=True)
        if left is None or right is None:
            return None
        return exp.EQ(this=left, expression=right)

    def predicate(self, ctx: Ctx, level: int = 0, *, agg: bool = False) -> exp.Expression:
        choice = self.weighted(
            30,
            12,
            10,
            8,
            6,
            12 if level < 2 else 0,
            8 if ctx.depth < 2 else 0,
            4 if level < 2 else 0,
        )
        if choice == 0:
            kind = self.pick(("int", "text"))
            op = self.pick((exp.EQ, exp.GT, exp.LT, exp.NEQ, exp.GTE, exp.LTE))
            right = self.literal(kind) if self.chance(60) else self.expr(ctx, kind, level + 1)
            return op(this=self.expr(ctx, kind, level + 1, agg=agg), expression=right)
        if choice == 1:
            return exp.Like(
                this=self.expr(ctx, "text", level + 1, agg=agg), expression=self.literal("text")
            )
        if choice == 2:
            kind = self.pick(("int", "text"))
            values = [self.literal(kind) for _ in range(self.pick((2, 1, 3)))]
            return exp.In(this=self.expr(ctx, kind, level + 1, agg=agg), expressions=values)
        if choice == 3:
            node = exp.Is(this=self.expr(ctx, "text", level + 1, agg=agg), expression=exp.Null())
            return exp.Not(this=node) if self.chance(50) else node
        if choice == 4:
            return exp.Between(
                this=self.expr(ctx, "int", level + 1, agg=agg),
                low=self.literal("int"),
                high=self.literal("int"),
            )
        if choice == 5:
            op = self.pick((exp.And, exp.Or))
            return op(
                this=self.predicate(ctx, level + 1, agg=agg),
                expression=self.predicate(ctx, level + 1, agg=agg),
            )
        if choice == 6:
            return self.subquery_predicate(ctx)
        return exp.Not(this=exp.Paren(this=self.predicate(ctx, level + 1, agg=agg)))

    def subquery_predicate(self, ctx: Ctx) -> exp.Expression:
        inner = Ctx([], outer=ctx.sources + ctx.outer, ctes=ctx.ctes, depth=ctx.depth + 1)
        node, source = self.from_item(inner, set())
        inner.sources = [source]
        where = self.correlation(inner) if self.chance(60) else None
        if where is None and self.chance(50):
            where = self.predicate(inner, 1)
        if self.chance(50):
            select = exp.select(exp.Literal.number(1)).from_(node)
            if where is not None:
                select = select.where(where)
            return exp.Exists(this=select)
        kind = self.pick(("int", "text"))
        select = exp.select(self.expr(inner, kind, 1)).from_(node)
        if where is not None:
            select = select.where(where)
        return exp.In(this=self.expr(ctx, kind, 1), query=exp.Subquery(this=select))

    # -- SELECT --------------------------------------------------------------------------------
    def join_condition(self, left: list[Source], right: Source) -> exp.Expression | None:
        pairs = []
        for source in left:
            for a_table, a_col, b_table, b_col in JOINS:
                if source.name == a_table and right.name == b_table:
                    pairs.append((source, a_col, right, b_col))
                elif source.name == b_table and right.name == a_table:
                    pairs.append((source, b_col, right, a_col))
        if pairs and self.chance(65):
            a, a_col, b, b_col = self.pick(pairs)
            return exp.EQ(
                this=exp.column(a_col, table=a.ref), expression=exp.column(b_col, table=b.ref)
            )
        ctx = Ctx([*left, right])
        return self.predicate(ctx, 1)

    def select(
        self, ctx: Ctx, *, named: bool = False, signature: list[str] | None = None
    ) -> tuple[exp.Select, list[Col]]:
        used: set[str] = set()
        node, first = self.from_item(ctx, used)
        used.add(first.ref)
        select = exp.Select().from_(node)
        sources = [first]
        for _ in range(self.weighted(45, 38, 17 if ctx.depth == 0 else 0)):
            right_node, right = self.from_item(ctx, used)
            used.add(right.ref)
            kind = self.weighted(42, 16, 8, 14, 13, 7)
            join: exp.Join
            if kind == 4:  # comma join
                join = exp.Join(this=right_node)
            elif kind == 5:
                join = exp.Join(this=right_node, method="NATURAL")
            elif kind == 3:
                join = exp.Join(this=right_node, kind="CROSS")
            elif kind == 2:
                shared = sorted(
                    {c.name for c in right.columns} & {c.name for s in sources for c in s.columns}
                )
                if shared:
                    names = [exp.to_identifier(self.pick(shared))]
                    join = exp.Join(this=right_node, using=names)
                else:
                    join = exp.Join(this=right_node, kind="CROSS")
            else:
                on = self.join_condition(sources, right)
                join = exp.Join(this=right_node, on=on, side="LEFT" if kind == 1 else None)
            select.append("joins", join)
            sources.append(right)
        here = Ctx(sources, outer=ctx.outer, ctes=ctx.ctes, depth=ctx.depth)

        aggregate = self.weighted(65, 35) == 1
        columns: list[Col] = []
        projections: list[exp.Expression] = []
        kinds = signature or [self.pick(("int", "text")) for _ in range(self.pick((1, 2, 3, 4)))]
        if aggregate:
            keys = [
                key
                for key in (self.column(here, None) for _ in range(self.pick((0, 1, 2))))
                if key is not None
            ]
            if keys:
                select.set("group", exp.Group(expressions=[k.copy() for k in keys]))
            key_kinds = {
                k.name: c.kind for s in sources for c in s.columns for k in keys if c.name == k.name
            }
            for index, kind in enumerate(kinds):
                matching = [k for k in keys if key_kinds.get(k.name) == kind]
                if matching and self.chance(50):
                    projections.append(self.pick(matching).copy())
                else:
                    projections.append(self.aggregate(here, kind))
                columns.append(Col(f"c{index}", kind))
            if self.chance(45):
                select.set("having", exp.Having(this=self.predicate(here, 1, agg=True)))
        elif signature is None and not named and self.chance(8):
            star: exp.Expression = exp.Star()
            if len(sources) > 1 or self.chance(30):
                star = exp.Column(this=exp.Star(), table=exp.to_identifier(sources[0].ref))
            projections.append(star)
            columns.extend(sources[0].columns)
        else:
            for index, wanted in enumerate(kinds):
                kind = wanted
                if ctx.depth == 0 and signature is None and self.chance(10):
                    projections.append(self.window(here))
                    kind = "int"
                else:
                    projections.append(self.expr(here, kind))
                columns.append(Col(f"c{index}", kind))

        # Aliases: forced in derived tables and CTEs (their columns must have names), else random.
        for index, projection in enumerate(list(projections)):
            if isinstance(projection, exp.Star) or (
                isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star)
            ):
                continue
            alias = None
            if named:
                alias = f"c{index}"
                if self.chance(30):
                    alias = self.pick(ALIASES)
                    if any(c.name == alias for c in columns[:index]):
                        alias = f"c{index}"
            elif self.chance(35):
                alias = self.pick(ALIASES)
            if alias is not None:
                projections[index] = exp.alias_(projection, alias)
                columns[index] = Col(alias, columns[index].kind)
            elif isinstance(projection, exp.Column):
                columns[index] = Col(projection.name, columns[index].kind)
        select.set("expressions", projections)

        if self.chance(55):
            select.set("where", exp.Where(this=self.predicate(here)))
        if not aggregate and ctx.depth == 0 and self.chance(5):
            select.set(
                "qualify",
                exp.Qualify(this=exp.LTE(this=self.window(here), expression=self.literal("int"))),
            )
        if self.chance(12):
            if not aggregate and self.chance(30):
                on = [self.expr(here, self.pick(("int", "text")), 1)]
                select.set("distinct", exp.Distinct(on=exp.Tuple(expressions=on)))
            else:
                select.set("distinct", exp.Distinct())
        if ctx.depth == 0 and signature is None and self.chance(45):
            select.set("order", self.order_by(here, projections, aggregate))
        return select, columns

    def order_by(self, ctx: Ctx, projections: list[exp.Expression], aggregate: bool) -> exp.Order:
        items: list[exp.Expression] = []
        for _ in range(self.pick((1, 2))):
            choice = self.weighted(30, 15, 25, 15, 15)
            aliases = [p.alias for p in projections if isinstance(p, exp.Alias)]
            if choice == 0 and aliases:
                item: exp.Expression = exp.column(self.pick(aliases))  # bare alias
            elif choice == 1:
                item = exp.Literal.number(self.pick(range(1, len(projections) + 1)))
            elif choice == 3 and aliases:
                # An expression over a name that is both an alias and a column (`LOWER(email)`).
                item = exp.Lower(this=exp.column(self.pick(aliases)))
            elif choice == 4 and not aggregate:
                item = self.window(ctx) if self.chance(30) else self.expr(ctx, "text", 1)
            elif aggregate:
                item = self.aggregate(ctx, self.pick(("int", "text")))
            else:
                item = self.expr(ctx, self.pick(("int", "text")), 1)
            items.append(exp.Ordered(this=item, desc=self.chance(30)))
        return exp.Order(expressions=items)

    def limit(self, query: exp.Expression) -> None:
        choice = self.weighted(36, 22, 8, 12, 6, 7, 4, 3)
        count = exp.Literal.number(self.pick((7, 1, 3, 8, 50, 1000, 0)))
        if choice == 3:  # PERCENT: a large percentage of a large result
            count = exp.Literal.number(self.pick((50, 100, 1000, 7)))
        if choice == 0:
            return
        if choice == 1:
            query.set("limit", exp.Limit(expression=count))
        elif choice == 2:
            query.set("limit", exp.Limit(expression=count))
            query.set("offset", exp.Offset(expression=exp.Literal.number(self.pick((1, 2)))))
        elif choice == 3:
            options = exp.LimitOptions(percent=True)
            query.set("limit", exp.Limit(expression=count, limit_options=options))
        elif choice == 4:
            options = exp.LimitOptions(with_ties=True)
            query.set("limit", exp.Limit(expression=count, limit_options=options))
        elif choice == 5:
            options = exp.LimitOptions(rows=True, with_ties=self.chance(50))
            query.set("limit", exp.Fetch(direction="FIRST", count=count, limit_options=options))
        elif choice == 6:
            query.set("limit", exp.Limit(expression=exp.Neg(this=exp.Literal.number(1))))
        else:
            sub = exp.select(exp.Count(this=exp.Star())).from_("invoices")
            query.set("limit", exp.Limit(expression=exp.Subquery(this=sub)))

    # -- whole query ---------------------------------------------------------------------------
    def recursive_cte(self) -> tuple[exp.CTE, Source]:
        anchor: exp.Select
        text_col: exp.Expression
        if self.chance(50):
            table = self.pick(("customers", "products", "branches"))
            name = self.pick([c for c, k in COLUMNS[table] if k == "text"])
            text_col = exp.column(name)
            anchor = exp.select(exp.Literal.number(1), text_col).from_(table)
            if self.chance(50):
                anchor = anchor.where(
                    exp.EQ(this=exp.column("id"), expression=exp.Literal.number(1))
                )
        else:
            anchor = exp.select(exp.Literal.number(1), exp.Literal.string("seed"))
        step = (
            exp.select(
                exp.Add(this=exp.column("n"), expression=exp.Literal.number(1)), exp.column("v")
            )
            .from_("r")
            .where(exp.LT(this=exp.column("n"), expression=exp.Literal.number(3)))
        )
        body = exp.union(anchor, step, distinct=False)
        alias = exp.TableAlias(
            this=exp.to_identifier("r"),
            columns=[exp.to_identifier("n"), exp.to_identifier("v")],
        )
        source = Source("r", None, (Col("n", "int"), Col("v", "text")), physical=False)
        return exp.CTE(this=body, alias=alias), source

    def query(self) -> exp.Expression:
        ctes: list[exp.CTE] = []
        sources: list[Source] = []
        recursive = False
        for index in range(self.weighted(75, 20, 5)):
            if self.chance(25) and not recursive:
                cte, source = self.recursive_cte()
                recursive = True
            else:
                body, columns = self.select(Ctx([], ctes=list(sources), depth=1), named=True)
                name = self.pick(("w", "cte", "invoices", "customers")) if index == 0 else "w2"
                if any(s.name == name for s in sources):
                    name = f"w{index}"
                cte = exp.CTE(this=body, alias=exp.TableAlias(this=exp.to_identifier(name)))
                source = Source(name, None, tuple(columns), physical=False)
            ctes.append(cte)
            sources.append(source)

        top, columns = self.select(Ctx([], ctes=sources))
        query: exp.Expression = top
        if self.chance(15):
            right, _ = self.select(Ctx([], ctes=sources), signature=[c.kind for c in columns])
            top.set("order", None)
            op = self.weighted(5, 3, 1, 1)
            if op <= 1:
                query = exp.union(top, right, distinct=op == 1)
            elif op == 2:
                query = exp.intersect(top, right, distinct=True)
            else:
                query = exp.except_(top, right, distinct=True)
            if self.chance(30):
                query.set(
                    "order",
                    exp.Order(expressions=[exp.Ordered(this=exp.Literal.number(1))]),
                )
        self.limit(query)
        if ctes:
            query.set("with_", exp.With(expressions=ctes, recursive=recursive))
        return query

    def mangle_identifiers(self, tree: exp.Expression) -> None:
        mode = self.weighted(70, 12, 10, 8)  # none, case, quoting, both
        if mode == 0:
            return
        for ident in list(tree.find_all(exp.Identifier)):
            if not self.chance(50):
                continue
            if mode in (1, 3):
                name = ident.this
                ident.set("this", self.pick((name.upper(), name.title(), name.swapcase())))
            if mode in (2, 3):
                ident.set("quoted", True)


@st.composite
def fuzz_cases(draw: Any) -> FuzzCase:
    gen = Gen(draw)
    tree = gen.query()
    gen.mangle_identifiers(tree)
    role = gen.pick(tuple(ROLES))
    mangle = gen.weighted(60, 25, 15)
    seed = draw(st.integers(0, 2**16))
    return FuzzCase(tree=tree, role=role, mangle=mangle, mangle_seed=seed)


_KEEP_CASE = {TokenType.VAR, TokenType.IDENTIFIER, TokenType.STRING, TokenType.NUMBER}


def render(case: FuzzCase, dialect: str) -> str | None:
    """The SQL text "the model wrote" for ``dialect``, or None if sqlglot can't write it."""
    try:
        sql = case.tree.sql(dialect=dialect)
    except (SqlglotError, ValueError, KeyError, TypeError):
        return None
    if case.mangle == 0:
        return sql
    return mangle_text(sql, dialect, case.mangle, case.mangle_seed)


def mangle_text(sql: str, dialect: str, level: int, seed: int) -> str:
    """Random keyword case, whitespace and (level 2) comments between tokens.

    Falls back to the input if the result doesn't tokenize to the same tokens.
    """
    rng = random.Random(seed)
    tokenizer = Dialect.get_or_raise(dialect)
    try:
        tokens = tokenizer.tokenize(sql)
    except SqlglotError:
        return sql
    parts: list[str] = []
    for index, token in enumerate(tokens):
        raw = sql[token.start : token.end + 1]
        if token.token_type not in _KEEP_CASE and raw.replace(" ", "").isalpha():
            raw = "".join(ch.upper() if rng.random() < 0.5 else ch.lower() for ch in raw)
        if index:
            gap = sql[tokens[index - 1].end + 1 : token.start]
            roll = rng.random()
            if level >= 2 and roll < 0.12:
                gap = f" {rng.choice(COMMENTS)} "
            elif gap and roll < 0.3:
                gap = rng.choice(("  ", "\n", "\t", "\n  "))
            parts.append(gap)
        parts.append(raw)
    text = "".join(parts)
    try:
        again = tokenizer.tokenize(text)
    except SqlglotError:
        return text  # an unterminated comment: the guard must reject it, so keep it
    same = [(t.token_type, t.text.upper()) for t in again] == [
        (t.token_type, t.text.upper()) for t in tokens
    ]
    return text if same or level >= 2 else sql
