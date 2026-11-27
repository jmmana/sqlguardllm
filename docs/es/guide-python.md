# Python, paso a paso

[Read in English](../guide-python.md) · [Elige un paquete](packages.md) · [Node.js](guide-node.md) · [.NET](guide-dotnet.md)

El paquete de Python es el motor mismo: se ejecuta dentro de tu aplicación, sin servidor. Necesitas Python 3.10 o posterior, en Linux, macOS o Windows.

## 1. Instala

```bash
mkdir guard-demo && cd guard-demo
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install --pre sqlguardllm
```

`--pre` hace falta mientras sqlguardllm esté en fase alfa. Para cargar políticas YAML, instala `"sqlguardllm[yaml]"`.

## 2. Escribe una política

Guarda esto como `policy.json` en la misma carpeta:

```json
{
  "dialect": "postgres",
  "tables": ["invoices", "branches", "customers"],
  "hidden_columns": ["customers.email"],
  "row_filters": {
    "invoices": "branch_id IN ({user.branches})",
    "branches": "id IN ({user.branches})"
  },
  "max_rows": 100
}
```

En palabras simples:

- [`tables`](../policy-reference.md#tables): el modelo puede leer `invoices`, `branches` y `customers`, y ninguna otra tabla.
- [`hidden_columns`](../policy-reference.md#hidden_columns): `customers.email` no se puede usar en ningún lado, así que una consulta que la mencione se rechaza. Para que el modelo la vea enmascarada, mira el [paso 5](#5-cambia-una-regla).
- [`row_filters`](../policy-reference.md#row_filters): toda lectura de `invoices` y `branches` se limita a las sucursales del usuario. `{user.branches}` sale del contexto que pasa tu aplicación, nunca del modelo.
- [`max_rows`](../policy-reference.md#max_rows): como máximo 100 filas por consulta.

## 3. Revisa una consulta permitida y una rechazada

Guarda esto como `check.py`:

```python
from sqlguardllm import Guard, Policy

guard = Guard(Policy.from_file("policy.json"), language="es")

# El gerente de la sucursal 1. En tu app viene del usuario que inició sesión, nunca del modelo.
context = {"user": {"branches": [1]}}

invoices = guard.check("SELECT id, total FROM invoices ORDER BY id", context)
print(invoices.ok, invoices.sql)

emails = guard.check("SELECT name, email FROM customers", context)
print(emails.ok, [f"{v.code.value}: {v.message}" for v in emails.violations])
```

## 4. Ejecútalo

```bash
python check.py
```

Salida esperada:

```text
True SELECT id, total FROM (SELECT * FROM invoices WHERE branch_id IN (1)) AS invoices ORDER BY id LIMIT 100
False ['hidden_column: customers.email es una columna oculta']
```

La primera consulta se permite y se reescribe: solo puede ver las facturas de la sucursal 1 y recibió un `LIMIT`. Tu aplicación ejecuta `invoices.sql`, nunca el texto original, con un usuario de base de datos de solo lectura. La segunda se rechaza porque la política oculta `customers.email`. Sin `language="es"` los mensajes salen en inglés.

## 5. Cambia una regla

Para que el modelo lea el email enmascarado, reemplaza la línea de `hidden_columns` de `policy.json` por:

```json
  "masked_columns": {"customers.email": "partial"},
```

Ejecuta `python check.py` otra vez. La segunda línea pasa a ser `True []`: la consulta se permite y `emails.sql` es

```sql
SELECT name, CASE WHEN email IS NULL THEN NULL ELSE SUBSTRING(CAST(email AS VARCHAR(4000)) FROM 1 FOR 2) || '***' END AS email FROM customers LIMIT 100
```

así que `ceo@acme.example` vuelve como `ce***`. Las máscaras son `partial`, `redact`, `null` y `hash` ([`masked_columns`](../policy-reference.md#masked_columns)). Puedes activar y desactivar estas reglas sin instalar nada en el [playground](https://jmmana.github.io/sqlguardllm/playground/).

## Siguiente

- [Inicio rápido](quickstart.md) y [recetas](../RECIPES.md) para DB-API, SQLAlchemy, LangChain, LlamaIndex y Vanna.
- [Referencia de políticas](../policy-reference.md): todos los campos. [Reglas](../DESIGN.md): qué se revisa y cómo se reescriben las consultas.
- [Referencia de la API](../api.md): `Guard.check`, `Guard.enforce`, `Guard.explain`.
