# Inicio rápido

[Read in English](../quickstart.md)

```bash
pip install sqlguardllm
```

```python
from sqlguardllm import Guard, Policy

policy = Policy(
    dialect="postgres",
    tables=["orders", "customers"],
    hidden_columns=["customers.ssn"],
    row_filters={"orders": "tenant_id = {context.tenant_id}"},
    max_rows=100,
)
guard = Guard(policy)

result = guard.check("SELECT id, total FROM orders", context={"tenant_id": 7})
print(result.allowed)  # True
print(result.sql)      # reescrita: filtro de filas y LIMIT aplicados

blocked = guard.check("DELETE FROM orders")
print(blocked.allowed, [v.code for v in blocked.violations])
```

## Analizar una política

Antes de desplegar, comprueba si una política válida tiene problemas de seguridad comunes:

```bash
sqlguardllm lint --policy policy.yaml
sqlguardllm lint --policy policy.yaml --format json
sqlguardllm lint --policy policy.yaml --strict  # devuelve código 1 si quedan avisos
```

Los avisos incluyen columnas similares a identificadores de organización sin filtros de filas,
columnas sensibles que no están ocultas ni enmascaradas, un `max_rows` sin definir o demasiado
alto (más de 10.000), valores de `aggregate_only` con `k` menor que 5 y funciones restauradas de
la lista de funciones denegadas por defecto. Cada aviso incluye una solución. Si la política no
incluye `schema` (`tabla: [columnas]`), las comprobaciones que dependen de columnas se indican
como omitidas. Los avisos no provocan un error de forma predeterminada. Para documentar un riesgo
aceptado, suprime el aviso por código e indica el motivo en la política:

```yaml
lint_suppressions:
  max_rows_weak: "El servicio de exportación de confianza necesita resultados más grandes."
```

Siguiente: las [reglas](../DESIGN.md), la [referencia de políticas](../policy-reference.md) y la [referencia de la API](../api.md). El [README en español](https://github.com/jmmana/sqlguardllm/blob/main/README.es.md) tiene más ejemplos.
