# Inicio rápido

[Read in English](../quickstart.md) · [Elige un paquete](packages.md)

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

## Prueba una política en CI

Guarda en YAML o JSON las consultas que tu política debe permitir o rechazar. YAML requiere el extra opcional `pip install --pre "sqlguardllm[yaml]"`.

```yaml
cases:
  - name: la gerente de sucursal puede leer facturas
    sql: SELECT id FROM invoices
    context: {user: {branches: [1, 2]}}
    expect: allow
    sql_contains: branch_id IN (1, 2)
  - name: el correo de clientes es privado
    sql: SELECT email FROM customers
    expect: reject
    violation: hidden_column
```

Desde la raíz del repositorio, ejecuta los [casos de ejemplo](https://github.com/jmmana/sqlguardllm/blob/main/examples/policy-tests.yaml) con su política.

```bash
sqlguardllm test examples/policy-tests.yaml --policy examples/policy.yaml
sqlguardllm test examples/policy-tests.yaml --policy examples/policy.yaml --format junit > resultados-politica.xml
sqlguardllm schema --cases > policy-tests.schema.json
```

Indica `role` en cada caso que pruebe un conjunto de políticas. El caso también puede incluir `context` para rellenar los valores de los filtros de fila de ese rol. Omite `role` si pruebas una sola política. En los rechazos, `violation` comprueba un código público. En las consultas permitidas, `sql_contains` comprueba una subcadena literal del SQL reescrito y distingue mayúsculas de minúsculas.

El comando devuelve 0 si pasan todos los casos, 1 si falla alguna expectativa y 2 si la entrada es inválida. Los informes JUnit incluyen los nombres de los casos y los motivos de fallo. Las comprobaciones se ejecutan sin conexión a la base de datos.
