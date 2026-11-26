"""Warnings about policies that are valid but probably unsafe: ``sqlguardllm lint``.

The linter reads a policy or a policy set and never runs SQL. Each warning has a stable code,
an explanation, a fix and a link to the policy field it is about. A policy silences a code it
has reviewed with ``lint_suppress: {code: reason}``; the reason is printed with the report.

Checks that need the columns of each table (tenant-like columns, sensitive columns) use the
policy's ``schema``. Without it they are reported as skipped, never as passed.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

from sqlguardllm._names import TableName, matches, norm, parse_table_entry
from sqlguardllm.data.denied_functions import default_denylist
from sqlguardllm.guard import _compile
from sqlguardllm.messages import LANGUAGES
from sqlguardllm.policy import Policy, read_mapping
from sqlguardllm.policy_set import PolicySet
from sqlguardllm.result import PolicyError
from sqlguardllm.rules.functions import _is_denied

REFERENCE = "https://jmmana.github.io/sqlguardllm/policy-reference/#"
DEFAULT_MAX_ROWS_THRESHOLD = 10_000
MIN_GROUP_SIZE = 5
_MAX_LISTED = 8


class LintCode(str, Enum):
    """Stable codes of ``sqlguardllm lint`` warnings, in report order.

    Codes never change within a major version.
    """

    NO_TABLE_ALLOWLIST = "no-table-allowlist"
    TENANT_WITHOUT_ROW_FILTER = "tenant-without-row-filter"
    SENSITIVE_COLUMN_EXPOSED = "sensitive-column-exposed"
    MAX_ROWS_DISABLED = "max-rows-disabled"
    MAX_ROWS_HIGH = "max-rows-high"
    AGGREGATE_K_LOW = "aggregate-k-low"
    DENIED_FUNCTION_ALLOWED = "denied-function-allowed"


LINT_CODES: frozenset[str] = frozenset(code.value for code in LintCode)
_ORDER = {code: index for index, code in enumerate(LintCode)}
_COLUMN_CHECKS = (LintCode.TENANT_WITHOUT_ROW_FILTER, LintCode.SENSITIVE_COLUMN_EXPOSED)

# Per code: the policy field it is about, then message, explanation and fix in each language.
# Templates are filled with the finding's values; they never include context values.
_TEXTS: dict[LintCode, tuple[str, dict[str, tuple[str, str, str]]]] = {
    LintCode.TENANT_WITHOUT_ROW_FILTER: (
        "row_filters",
        {
            "en": (
                "{table} has the tenant-like column {column} and no row filter",
                "Without a row filter, every user of this policy reads the rows of every "
                "tenant, company or branch in {table}.",
                "Add a row filter filled from the signed-in user, such as row_filters: {{{table}: "
                '"{column} = {{user.{column}}}"}}. If every tenant shares the table on purpose, '
                "suppress the code in lint_suppress with the reason.",
            ),
            "es": (
                "{table} tiene la columna {column}, que parece separar clientes, empresas o "
                "sucursales, y ningún filtro por fila",
                "Sin un filtro por fila, cada usuario de esta política lee las filas de todos "
                "los clientes, empresas o sucursales de {table}.",
                "Agrega un filtro por fila que se rellene con el usuario autenticado, como "
                'row_filters: {{{table}: "{column} = {{user.{column}}}"}}. Si la tabla se '
                "comparte a propósito, suprime el código en lint_suppress con el motivo.",
            ),
        },
    ),
    LintCode.SENSITIVE_COLUMN_EXPOSED: (
        "hidden_columns",
        {
            "en": (
                "{column} looks sensitive and is neither hidden nor masked",
                "The model can read its values, and they can end up in its answers, in logs "
                "or in another prompt.",
                "Add it to hidden_columns, or to masked_columns if the model may see a masked "
                "value. If it is not sensitive, suppress the code in lint_suppress with the "
                "reason.",
            ),
            "es": (
                "{column} parece un dato sensible y no está oculta ni enmascarada",
                "El modelo puede leer sus valores, y pueden terminar en sus respuestas, en "
                "registros o en otro prompt.",
                "Agrégala a hidden_columns, o a masked_columns si el modelo puede ver un valor "
                "enmascarado. Si no es sensible, suprime el código en lint_suppress con el "
                "motivo.",
            ),
        },
    ),
    LintCode.MAX_ROWS_DISABLED: (
        "max_rows",
        {
            "en": (
                "max_rows is null, so queries have no row limit",
                "One query can copy out a whole table, and huge results are slow and costly "
                "for the database and the model.",
                "Set max_rows to the most rows one answer needs, such as 1000 (the default).",
            ),
            "es": (
                "max_rows es null, así que las consultas no tienen límite de filas",
                "Una sola consulta puede copiar una tabla entera, y los resultados enormes son "
                "lentos y costosos para la base de datos y el modelo.",
                "Pon en max_rows el máximo de filas que necesita una respuesta, como 1000 (el "
                "valor por defecto).",
            ),
        },
    ),
    LintCode.MAX_ROWS_HIGH: (
        "max_rows",
        {
            "en": (
                "max_rows is {max_rows}, above the threshold of {threshold}",
                "Each query can return that many rows: enough to copy out large parts of a table.",
                "Lower max_rows to the most rows one answer needs. If the size is intended, "
                "raise the threshold with --max-rows-threshold or suppress the code.",
            ),
            "es": (
                "max_rows es {max_rows}, más que el umbral de {threshold}",
                "Cada consulta puede devolver esa cantidad de filas: suficiente para copiar "
                "gran parte de una tabla.",
                "Baja max_rows al máximo de filas que necesita una respuesta. Si el tamaño es "
                "intencional, sube el umbral con --max-rows-threshold o suprime el código.",
            ),
        },
    ),
    LintCode.AGGREGATE_K_LOW: (
        "aggregate_only",
        {
            "en": (
                "aggregate_only lets {table} return groups of {k} rows, fewer than "
                f"{MIN_GROUP_SIZE}",
                "Small groups single people out: with groups of 2, anyone who knows one "
                "member's value can work out the other's.",
                f"Raise the minimum group size of {{table}} to {MIN_GROUP_SIZE} or more.",
            ),
            "es": (
                "aggregate_only deja que {table} devuelva grupos de {k} filas, menos de "
                f"{MIN_GROUP_SIZE}",
                "Los grupos pequeños identifican personas: con grupos de 2, quien conozca el "
                "valor de un miembro puede deducir el del otro.",
                f"Sube el tamaño mínimo de grupo de {{table}} a {MIN_GROUP_SIZE} o más.",
            ),
        },
    ),
    LintCode.DENIED_FUNCTION_ALLOWED: (
        "denied_functions",
        {
            "en": (
                "{field} lets queries call functions that the default denylist of {dialect} "
                "blocks: {functions}",
                "These functions read files, call the network, sleep, run commands, run SQL "
                "passed as a string (which gets around the table allowlist) or change server "
                "state.",
                "Keep the default denylist and add your own with extra_denied_functions; with "
                "function_policy allowlist, remove them from allowed_functions.",
            ),
            "es": (
                "{field} permite llamar a funciones que la lista de denegación por defecto de "
                "{dialect} bloquea: {functions}",
                "Estas funciones leen archivos, llaman a la red, esperan, ejecutan comandos, "
                "ejecutan SQL pasado como texto (lo que salta la lista de tablas permitidas) o "
                "cambian el estado del servidor.",
                "Conserva la lista por defecto y agrega las tuyas con extra_denied_functions; "
                "con function_policy allowlist, quítalas de allowed_functions.",
            ),
        },
    ),
    LintCode.NO_TABLE_ALLOWLIST: (
        "tables",
        {
            "en": (
                "tables is null, so every table the database user can see is readable",
                "The model can read any table, including tables added later, and "
                "table_not_allowed never fires.",
                "List the tables the model needs in tables.",
            ),
            "es": (
                "tables es null, así que se puede leer cualquier tabla que vea el usuario de "
                "la base de datos",
                "El modelo puede leer cualquier tabla, incluso las que se agreguen después, y "
                "table_not_allowed nunca se activa.",
                "Enumera en tables las tablas que necesita el modelo.",
            ),
        },
    ),
}

_SKIPPED: dict[str, dict[str, str]] = {
    "no_schema": {
        "en": "needs the policy's schema (table → columns), which this policy doesn't have",
        "es": "necesita el schema de la política (tabla → columnas), que esta política no tiene",
    },
    "partial_schema": {
        "en": "columns not checked for allowed tables missing from schema: {tables}",
        "es": "no se revisaron las columnas de las tablas permitidas que faltan en schema: "
        "{tables}",
    },
}

_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "why": "Why",
        "fix": "Fix",
        "see": "See",
        "reason": "Reason",
        "role": "role",
        "roles": "roles",
        "more": "and {count} more",
    },
    "es": {
        "why": "Por qué",
        "fix": "Solución",
        "see": "Referencia",
        "reason": "Motivo",
        "role": "rol",
        "roles": "roles",
        "more": "y {count} más",
    },
}

# Column names are split into words: camelCase, snake_case and kebab-case all work.
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NOT_WORD = re.compile(r"[^0-9a-záéíóúüñ]+")
_TENANT_WORDS = frozenset(
    {
        "tenant",
        "company",
        "org",
        "organization",
        "organisation",
        "branch",
        "workspace",
        "sucursal",
        "empresa",
        "compania",
        "compañia",
        "compañía",
        "organizacion",
        "organización",
        "inquilino",
    }
)
_KEY_WORDS = frozenset(
    {"id", "uuid", "key", "code", "fk", "cod", "codigo", "código", "num", "number", "no"}
)
_SENSITIVE_WORDS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "passphrase",
        "secret",
        "token",
        "apikey",
        "ssn",
        "cedula",
        "cédula",
        "curp",
        "rfc",
        "nit",
        "dni",
        "nif",
        "email",
        "correo",
        "phone",
        "telephone",
        "telefono",
        "teléfono",
        "mobile",
        "celular",
        "movil",
        "móvil",
        "card",
        "cvv",
        "cvc",
        "iban",
        "pin",
        "contrasena",
        "contraseña",
    }
)
# Several consecutive words that name sensitive data together: tax_id, credit_card, api_key.
_SENSITIVE_RUNS = frozenset(
    {
        "apikey",
        "accesskey",
        "secretkey",
        "privatekey",
        "taxid",
        "socialsecurity",
        "creditcard",
        "cardnumber",
        "accountnumber",
        "phonenumber",
        "emailaddress",
    }
)
# Sensitive even inside a longer word, such as userpassword or passwordhash.
_SENSITIVE_INSIDE = ("password", "passwd", "contrasena", "contraseña")
# A last word that describes the data instead of holding it: email_verified, token_count.
_METADATA_WORDS = frozenset(
    {
        "at",
        "on",
        "date",
        "time",
        "count",
        "type",
        "kind",
        "brand",
        "status",
        "verified",
        "confirmed",
        "enabled",
        "consent",
        "expires",
        "expiry",
        "expiration",
        "changed",
        "updated",
        "created",
        "sent",
        "length",
        "version",
        "format",
        "policy",
    }
)


def _words(name: str) -> list[str]:
    return [word for word in _NOT_WORD.split(_CAMEL.sub("_", name).lower()) if word]


def looks_like_tenant(column: str) -> bool:
    """Whether a column name looks like it separates tenants: tenant_id, branchId, sucursal…"""
    words = [word for word in _words(column) if word not in _KEY_WORDS]
    if len(words) != 1:
        return False
    word = words[0]
    if word in _TENANT_WORDS:
        return True
    return any(
        word.endswith(suffix) and word[: -len(suffix)] in _TENANT_WORDS
        for suffix in ("id", "uuid", "code")
    )


def looks_sensitive(column: str) -> bool:
    """Whether a column name looks like personal or secret data: password, email, card…"""
    words = _words(column)
    if len(words) > 1 and words[-1] in _METADATA_WORDS:
        return False
    if any(word in _SENSITIVE_WORDS for word in words):
        return True
    joined = "".join(words)
    if any(part in joined for part in _SENSITIVE_INSIDE):
        return True
    return any(
        "".join(words[start:end]) in _SENSITIVE_RUNS
        for start in range(len(words))
        for end in range(start + 2, len(words) + 1)
    )


@dataclass(frozen=True)
class Finding:
    """One warning, or a suppressed one when ``reason`` is set.

    Attributes:
        code: Stable code of the warning.
        message: What was found, in the report's language.
        explanation: Why it is probably unsafe.
        fix: How to fix it.
        reference: Link to the policy field in the policy reference.
        roles: Policy-set roles it applies to; empty for a single policy.
        reason: The ``lint_suppress`` reason, for suppressed warnings.
    """

    code: LintCode
    message: str
    explanation: str
    fix: str
    reference: str
    roles: tuple[str, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class Skipped:
    """A check that could not run, or ran only on part of the policy, and why."""

    code: LintCode
    reason: str
    roles: tuple[str, ...] = ()


_Item = TypeVar("_Item", Finding, Skipped)


@dataclass(frozen=True)
class LintReport:
    """Everything ``sqlguardllm lint`` found in a policy or a policy set."""

    warnings: tuple[Finding, ...]
    suppressed: tuple[Finding, ...]
    skipped: tuple[Skipped, ...]
    language: str = "en"
    policy_set: bool = False


def _listed(names: Iterable[str], language: str) -> str:
    items = list(names)
    if len(items) <= _MAX_LISTED:
        return ", ".join(items)
    more = _LABELS[language]["more"].format(count=len(items) - _MAX_LISTED)
    return f"{', '.join(items[:_MAX_LISTED])} {more}"


class _Linter:
    """The checks of one policy, in one language."""

    def __init__(self, policy: Policy, language: str, max_rows_threshold: int) -> None:
        self.policy = policy
        self.language = language
        self.threshold = max_rows_threshold
        self.compiled = _compile(policy, language)
        self.found: list[Finding] = []
        self.skipped: list[Skipped] = []

    def add(self, code: LintCode, *, anchor: str | None = None, **values: Any) -> None:
        field, texts = _TEXTS[code]
        message, explanation, fix = texts[self.language]
        self.found.append(
            Finding(
                code=code,
                message=message.format(**values),
                explanation=explanation.format(**values),
                fix=fix.format(**values),
                reference=REFERENCE + (anchor or field),
            )
        )

    def run(self) -> None:
        self.tables()
        self.columns()
        self.max_rows()
        self.aggregates()
        self.functions()
        self.found.sort(key=_by_code)

    def _table(self, entry: str) -> TableName:
        return parse_table_entry(entry, self.compiled.dialect, self.compiled.dialect_name)

    def _matching(self, entries: Iterable[TableName], table: TableName) -> list[TableName]:
        return [entry for entry in entries if matches(entry, table, self.compiled.default_schema)]

    def tables(self) -> None:
        if self.policy.tables is None:
            self.add(LintCode.NO_TABLE_ALLOWLIST)

    def columns(self) -> None:
        compiled = self.compiled
        if self.policy.schema is None:
            reason = _SKIPPED["no_schema"][self.language]
            self.skipped.extend(Skipped(code, reason) for code in _COLUMN_CHECKS)
            return
        described = [self._table(key) for key in self.policy.schema]
        if compiled.tables is not None:
            missing = sorted(
                entry
                for entry in self.policy.tables or ()
                if not any(
                    matches(self._table(entry), table, compiled.default_schema)
                    for table in described
                )
            )
            if missing:
                reason = _SKIPPED["partial_schema"][self.language].format(
                    tables=_listed(missing, self.language)
                )
                self.skipped.extend(Skipped(code, reason) for code in _COLUMN_CHECKS)
        for key, columns in self.policy.schema.items():
            table = self._table(key)
            if compiled.tables is not None and not self._matching(compiled.tables, table):
                continue  # not readable: nothing to protect
            filtered = bool(self._matching(compiled.filters, table))
            protected: set[str] = set()
            for entry in self._matching(compiled.hidden, table):
                protected |= compiled.hidden[entry]
            for entry in self._matching(compiled.masked, table):
                protected |= set(compiled.masked[entry])
            for column in columns:
                if not filtered and looks_like_tenant(column):
                    self.add(LintCode.TENANT_WITHOUT_ROW_FILTER, table=key, column=column)
                if looks_sensitive(column) and norm(column, compiled.dialect) not in protected:
                    self.add(LintCode.SENSITIVE_COLUMN_EXPOSED, column=f"{key}.{column}")

    def max_rows(self) -> None:
        value = self.policy.max_rows
        if value is None:
            self.add(LintCode.MAX_ROWS_DISABLED)
        elif value > self.threshold:
            self.add(LintCode.MAX_ROWS_HIGH, max_rows=value, threshold=self.threshold)

    def aggregates(self) -> None:
        for table, k in sorted(self.policy.aggregate_only.items()):
            if k < MIN_GROUP_SIZE:
                self.add(LintCode.AGGREGATE_K_LOW, table=table, k=k)

    def functions(self) -> None:
        compiled = self.compiled
        default = default_denylist(compiled.dialect_name)
        if compiled.allowed is not None:
            # A bare name also allows qualified calls: sleep allows dbms_lock.sleep.
            names = sorted(
                name
                for name in compiled.allowed
                if _is_denied(name, default) or any(entry.endswith(f".{name}") for entry in default)
            )
            field = "allowed_functions"
        else:
            names = sorted(entry for entry in default if not _is_denied(entry, compiled.denied))
            field = "denied_functions"
        if names:
            self.add(
                LintCode.DENIED_FUNCTION_ALLOWED,
                anchor=field,
                field=field,
                functions=_listed(names, self.language),
                dialect=compiled.dialect_name,
            )


def _by_code(item: Finding | Skipped) -> int:
    return _ORDER[item.code]


def _check(
    policy: Policy, language: str, max_rows_threshold: int
) -> tuple[list[Finding], list[Finding], list[Skipped]]:
    linter = _Linter(policy, language, max_rows_threshold)
    linter.run()
    suppress = policy.lint_suppress
    warnings = [item for item in linter.found if item.code.value not in suppress]
    suppressed = [
        dataclasses.replace(item, reason=suppress[item.code.value])
        for item in linter.found
        if item.code.value in suppress
    ]
    skipped = [item for item in linter.skipped if item.code.value not in suppress]
    return warnings, suppressed, skipped


def _validate(language: str, max_rows_threshold: int) -> None:
    if language not in LANGUAGES:
        raise ValueError(f"language must be one of {', '.join(LANGUAGES)}")
    if isinstance(max_rows_threshold, bool) or max_rows_threshold <= 0:
        raise ValueError("the max_rows threshold must be a positive integer")


def lint_policy(
    policy: Policy,
    *,
    language: str = "en",
    max_rows_threshold: int = DEFAULT_MAX_ROWS_THRESHOLD,
) -> LintReport:
    """Lint one policy. Raises :class:`PolicyError` if the policy itself is invalid."""
    _validate(language, max_rows_threshold)
    warnings, suppressed, skipped = _check(policy, language, max_rows_threshold)
    return LintReport(tuple(warnings), tuple(suppressed), tuple(skipped), language)


def _grouped(items: Iterable[tuple[str, _Item]]) -> tuple[_Item, ...]:
    """Merge what several roles share into one entry that lists the roles."""
    roles: dict[_Item, list[str]] = {}
    for role, item in items:
        roles.setdefault(item, []).append(role)
    merged = [dataclasses.replace(item, roles=tuple(names)) for item, names in roles.items()]
    return tuple(sorted(merged, key=_by_code))


def lint_policy_set(
    policy_set: PolicySet,
    *,
    language: str = "en",
    max_rows_threshold: int = DEFAULT_MAX_ROWS_THRESHOLD,
) -> LintReport:
    """Lint every role of a policy set (the base when it has no roles).

    A warning shared by several roles is reported once, with the list of roles. Each role's
    ``lint_suppress``, inherited and extended like any policy key, decides what it silences.
    """
    _validate(language, max_rows_threshold)
    roles = policy_set.roles
    policies = (
        [(role, policy_set.for_role(role)) for role in roles]
        if roles
        else [("base", policy_set.base)]
    )
    warnings: list[tuple[str, Finding]] = []
    suppressed: list[tuple[str, Finding]] = []
    skipped: list[tuple[str, Skipped]] = []
    for role, policy in policies:
        found, silenced, missing = _check(policy, language, max_rows_threshold)
        warnings += [(role, item) for item in found]
        suppressed += [(role, item) for item in silenced]
        skipped += [(role, item) for item in missing]
    return LintReport(
        _grouped(warnings), _grouped(suppressed), _grouped(skipped), language, policy_set=True
    )


def lint_file(
    path: str | Path,
    *,
    language: str = "en",
    max_rows_threshold: int = DEFAULT_MAX_ROWS_THRESHOLD,
) -> LintReport:
    """Lint a policy or policy-set file (``base`` and ``roles``), ``.json``, ``.yaml`` or ``.yml``.

    Raises:
        OSError: If the file can't be read.
        PolicyError: If the policy or policy set is invalid.
    """
    data: Mapping[str, Any] = read_mapping(path)
    is_set = "base" in data or "roles" in data
    try:
        loaded = PolicySet.from_dict(data) if is_set else Policy.from_dict(data)
    except TypeError as exc:  # a value of the wrong type, such as tables: 5
        raise PolicyError(str(exc)) from exc
    if isinstance(loaded, PolicySet):
        return lint_policy_set(loaded, language=language, max_rows_threshold=max_rows_threshold)
    return lint_policy(loaded, language=language, max_rows_threshold=max_rows_threshold)


def _summary(report: LintReport) -> str:
    warnings, suppressed, skipped = (
        len(report.warnings),
        len(report.suppressed),
        len(report.skipped),
    )
    if report.language == "es":
        return (
            f"{warnings} {'aviso' if warnings == 1 else 'avisos'}, "
            f"{suppressed} {'suprimido' if suppressed == 1 else 'suprimidos'}, "
            f"{skipped} {'comprobación omitida' if skipped == 1 else 'comprobaciones omitidas'}"
        )
    return (
        f"{warnings} {'warning' if warnings == 1 else 'warnings'}, {suppressed} suppressed, "
        f"{skipped} {'check' if skipped == 1 else 'checks'} skipped"
    )


def _roles(roles: tuple[str, ...], language: str) -> str:
    if not roles:
        return ""
    label = _LABELS[language]["role" if len(roles) == 1 else "roles"]
    return f" ({label} {', '.join(roles)})"


def format_report(report: LintReport, *, as_json: bool = False) -> str:
    """The report as text for people or as JSON for programs."""
    if as_json:

        def roles(item: Finding | Skipped) -> list[str] | None:
            return list(item.roles) if report.policy_set else None

        return json.dumps(
            {
                "warnings": [
                    {
                        "code": item.code.value,
                        "roles": roles(item),
                        "message": item.message,
                        "explanation": item.explanation,
                        "fix": item.fix,
                        "reference": item.reference,
                    }
                    for item in report.warnings
                ],
                "suppressed": [
                    {
                        "code": item.code.value,
                        "roles": roles(item),
                        "message": item.message,
                        "reason": item.reason,
                        "reference": item.reference,
                    }
                    for item in report.suppressed
                ],
                "skipped": [
                    {"code": item.code.value, "roles": roles(item), "reason": item.reason}
                    for item in report.skipped
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    labels = _LABELS[report.language]
    lines: list[str] = []
    for item in report.warnings:
        lines += [
            f"WARN {item.code.value}{_roles(item.roles, report.language)}: {item.message}",
            f"  {labels['why']}: {item.explanation}",
            f"  {labels['fix']}: {item.fix}",
            f"  {labels['see']}: {item.reference}",
        ]
    for item in report.suppressed:
        lines += [
            f"SUPPRESSED {item.code.value}{_roles(item.roles, report.language)}: {item.message}",
            f"  {labels['reason']}: {item.reason}",
        ]
    lines += [
        f"SKIP {item.code.value}{_roles(item.roles, report.language)}: {item.reason}"
        for item in report.skipped
    ]
    lines.append(_summary(report))
    return "\n".join(lines)
