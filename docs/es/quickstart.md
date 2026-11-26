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

Desde la raíz del repositorio, ejecuta los [casos de ejemplo](https://github.com/jmmana/sqlguardllm/blob/main/examples/policy-tests.yaml) con su política. Los [casos del conjunto de políticas](https://github.com/jmmana/sqlguardllm/blob/main/examples/roles-tests.yaml) prueban cada rol de [`roles.yaml`](https://github.com/jmmana/sqlguardllm/blob/main/examples/roles.yaml). CI ejecuta ambos.

```bash
sqlguardllm test examples/policy-tests.yaml --policy examples/policy.yaml
sqlguardllm test examples/roles-tests.yaml --policy examples/roles.yaml
sqlguardllm test examples/policy-tests.yaml --policy examples/policy.yaml --format junit > resultados-politica.xml
sqlguardllm schema --cases > policy-tests.schema.json
```

Indica `role` en cada caso que pruebe un conjunto de políticas. El caso también puede incluir `context` para rellenar los valores de los filtros de fila de ese rol. Omite `role` si pruebas una sola política. En los rechazos, `violation` comprueba un código público. En las consultas permitidas, `sql_contains` comprueba una subcadena literal del SQL reescrito y distingue mayúsculas de minúsculas.

El comando devuelve 0 si pasan todos los casos, 1 si falla alguna expectativa y 2 si la entrada es inválida. Los informes JUnit incluyen los nombres de los casos y los motivos de fallo. Las comprobaciones se ejecutan sin conexión a la base de datos.

## Revisa una política con lint

`sqlguardllm lint` avisa de políticas válidas pero probablemente inseguras. Lee una política o un conjunto de políticas y nunca se conecta a una base de datos.

```bash
sqlguardllm lint --policy examples/roles.yaml
sqlguardllm lint --policy policy.yaml --strict                        # sale con 1 si hay avisos
sqlguardllm lint --policy policy.yaml --format json --language es
```

| Código | Avisa cuando | Campo |
|---|---|---|
| `no-table-allowlist` | `tables` es `null`, así que se puede leer cualquier tabla | [`tables`](../policy-reference.md#tables) |
| `tenant-without-row-filter` | una tabla tiene una columna que separa clientes, empresas o sucursales (`tenant_id`, `company_id`, `branch_id`, `org_id`, `sucursal`…) y ningún filtro por fila | [`row_filters`](../policy-reference.md#row_filters) |
| `sensitive-column-exposed` | una columna que parece sensible (`password`, `token`, `secret`, `ssn`, `cedula`, `email`, `phone`, `card`…) no está oculta ni enmascarada | [`hidden_columns`](../policy-reference.md#hidden_columns) |
| `max-rows-disabled` | `max_rows` es `null` | [`max_rows`](../policy-reference.md#max_rows) |
| `max-rows-high` | `max_rows` pasa de 10000, o del valor de `--max-rows-threshold` | [`max_rows`](../policy-reference.md#max_rows) |
| `aggregate-k-low` | una tabla de `aggregate_only` permite grupos de menos de 5 filas | [`aggregate_only`](../policy-reference.md#aggregate_only) |
| `denied-function-allowed` | `denied_functions` o `allowed_functions` permite llamar a una función de la lista de denegación por defecto | [`denied_functions`](../policy-reference.md#denied_functions) |

Las comprobaciones de columnas de tenant y sensibles leen las columnas del [`schema`](../policy-reference.md#schema) de la política (tabla → columnas), que `Policy.from_database` puede rellenar. Sin `schema`, el informe las muestra como omitidas. También muestra las tablas permitidas que faltan en `schema`. Cada aviso trae una explicación, una solución y un enlace a su campo:

```text
WARN tenant-without-row-filter: orders tiene la columna tenant_id, que parece separar clientes, empresas o sucursales, y ningún filtro por fila
  Por qué: Sin un filtro por fila, cada usuario de esta política lee las filas de todos los clientes, empresas o sucursales de orders.
  Solución: Agrega un filtro por fila que se rellene con el usuario autenticado, como row_filters: {orders: "tenant_id = {user.tenant_id}"}. Si la tabla se comparte a propósito, suprime el código en lint_suppress con el motivo.
  Referencia: https://jmmana.github.io/sqlguardllm/policy-reference/#row_filters
1 aviso, 0 suprimidos, 0 comprobaciones omitidas
```

Si un aviso no aplica, suprime su código en el archivo de la política con el motivo. El informe muestra los avisos suprimidos con sus motivos. En un conjunto de políticas, los roles heredan las supresiones y pueden ampliarlas bajo `add` como cualquier otra clave. La revisión de SQL ignora [`lint_suppress`](../policy-reference.md#lint_suppress).

```yaml
lint_suppress:
  max-rows-high: Las exportaciones mensuales necesitan 50000 filas; lo revisó el equipo de datos.
```

El comando devuelve 0 cuando se ejecuta, aunque haya avisos, 1 con `--strict` si hay avisos y 2 si la política no se puede leer o es inválida. Los códigos son estables dentro de una versión mayor.
