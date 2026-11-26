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

Siguiente: las [reglas](../DESIGN.md), la [referencia de políticas](../policy-reference.md) y la [referencia de la API](../api.md). El [README en español](https://github.com/jmmana/sqlguardllm/blob/main/README.es.md) tiene más ejemplos.
