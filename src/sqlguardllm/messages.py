"""Human-readable violation messages in English and Spanish.

Messages never include values from the caller's context: only SQL fragments and names that
already appear in the query or the policy.
"""

from __future__ import annotations

from sqlguardllm.result import Violation, ViolationCode

LANGUAGES = ("en", "es")

_TEMPLATES: dict[ViolationCode, dict[str, str]] = {
    ViolationCode.PARSE_ERROR: {
        "en": "The SQL could not be understood, so it was rejected: {detail}",
        "es": "No se pudo entender el SQL, así que se rechazó: {detail}",
    },
    ViolationCode.EMPTY_STATEMENT: {
        "en": "There is no SQL statement to run",
        "es": "No hay ninguna sentencia SQL para ejecutar",
    },
    ViolationCode.MULTIPLE_STATEMENTS: {
        "en": "Only one statement is allowed, found {detail}",
        "es": "Solo se permite una sentencia y hay {detail}",
    },
    ViolationCode.NOT_A_QUERY: {
        "en": "Only SELECT queries are allowed, not {detail}",
        "es": "Solo se permiten consultas SELECT, no {detail}",
    },
    ViolationCode.WRITE_OPERATION: {
        "en": "The query tries to change data or structure ({detail})",
        "es": "La consulta intenta modificar datos o estructura ({detail})",
    },
    ViolationCode.ROW_LOCK: {
        "en": "Locking rows is not allowed ({detail})",
        "es": "No se permite bloquear filas ({detail})",
    },
    ViolationCode.TABLE_NOT_ALLOWED: {
        "en": "{detail} is not an allowed table",
        "es": "{detail} no es una tabla permitida",
    },
    ViolationCode.HIDDEN_COLUMN: {
        "en": "{detail} is a hidden column",
        "es": "{detail} es una columna oculta",
    },
    ViolationCode.DENIED_FUNCTION: {
        "en": "The function {detail} is not allowed",
        "es": "La función {detail} no está permitida",
    },
    ViolationCode.MISSING_CONTEXT: {
        "en": "A row filter needs {detail}, which was not provided, so nothing ran",
        "es": "Un filtro por fila necesita {detail}, que no se recibió, así que no se ejecutó nada",
    },
    ViolationCode.INVALID_CONTEXT: {
        "en": "The context value {detail} can't be used safely in a row filter",
        "es": "El valor de contexto {detail} no se puede usar con seguridad en un filtro por fila",
    },
    ViolationCode.TOO_COMPLEX: {
        "en": "The query is too complex for the policy: {detail}",
        "es": "La consulta es demasiado compleja para la política: {detail}",
    },
    ViolationCode.MISSING_REQUIRED_FILTER: {
        "en": "Reading {detail} requires a filter on one of those columns",
        "es": "Leer {detail} exige un filtro sobre una de esas columnas",
    },
}


def violation(code: ViolationCode, detail: str = "", language: str = "en") -> Violation:
    """Build a :class:`Violation` with its message in ``language``."""
    template = _TEMPLATES[code].get(language, _TEMPLATES[code]["en"])
    return Violation(code=code, message=template.format(detail=detail), detail=detail)
