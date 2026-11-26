# Inicio rápido

[Read in English](../quickstart.md) · [Elige un paquete](packages.md)

Esta página es para Python. Para **Node.js / TypeScript** o **.NET**, sigue las guías paso a paso de [Node.js](guide-node.md) y [.NET](guide-dotnet.md): instalan el cliente, inician `sqlguardllm serve` y revisan el mismo tipo de consultas. La [guía de Python](guide-python.md) hace lo mismo con un archivo de política.

```bash
pip install --pre sqlguardllm
```

```python
from sqlguardllm import Guard, Policy

policy = Policy(
    dialect="postgres",
    tables=["orders", "customers"],
    hidden_columns=["customers.ssn"],
    row_filters={"orders": "tenant_id = {tenant_id}"},
    max_rows=100,
)
guard = Guard(policy)

result = guard.check("SELECT id, total FROM orders", context={"tenant_id": 7})
print(result.ok)  # True
print(result.sql)      # reescrita: filtro de filas y LIMIT aplicados

blocked = guard.check("DELETE FROM orders")
print(blocked.ok, [v.code for v in blocked.violations])
```

Siguiente: las [reglas](../DESIGN.md), la [referencia de políticas](../policy-reference.md) y la [referencia de la API](../api.md). El [README en español](https://github.com/jmmana/sqlguardllm/blob/main/README.es.md) tiene más ejemplos.
