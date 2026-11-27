# Node.js y TypeScript, paso a paso

[Read in English](../guide-node.md) · [Elige un paquete](packages.md) · [Python](guide-python.md) · [.NET](guide-dotnet.md)

El paquete de npm `sqlguardllm` es un cliente pequeño y sin dependencias. El motor se ejecuta en `sqlguardllm serve`, un servidor HTTP junto a tu aplicación, así que Node.js recibe exactamente las mismas respuestas que Python. Necesitas Node.js 18 o posterior, y Python 3.10 o posterior para el servidor.

!!! note "Modo local o modo remoto"
    `npm install sqlguardllm` también instala el motor de tu plataforma (linux x64/arm64, macOS x64/arm64, Windows x64), así que `await SqlGuard.local({ policyPath: "policy.json" })` ejecuta el guard sin Python y sin servidor (consulta el [README del paquete](https://github.com/jmmana/sqlguardllm/tree/main/clients/node#local-mode)). Esta guía usa el modo remoto, más abajo. Si tu plataforma no tiene paquete de motor (por ejemplo Alpine/musl), o usas Deno, un navegador o un entorno edge, usa el modo remoto con un servidor en marcha.

## 1. Escribe una política

```bash
mkdir guard-demo && cd guard-demo
```

Guarda esto como `policy.json` en esa carpeta. Lo lee el servidor; el cliente nunca lo lee.

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

El modelo puede leer tres tablas ([`tables`](../policy-reference.md#tables)), nunca `customers.email` ([`hidden_columns`](../policy-reference.md#hidden_columns)), solo las facturas y sucursales de las sucursales del usuario ([`row_filters`](../policy-reference.md#row_filters)) y como máximo 100 filas ([`max_rows`](../policy-reference.md#max_rows)).

## 2. Inicia el servidor, en otra terminal

Desde la misma carpeta, con [uv](https://docs.astral.sh/uv/):

```bash
uvx sqlguardllm serve --policy policy.json --port 8787 --language es
```

Con [pipx](https://pipx.pypa.io/) es `pipx run sqlguardllm serve --policy policy.json --port 8787 --language es`. Con pip:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install --pre sqlguardllm
sqlguardllm serve --policy policy.json --port 8787 --language es
```

Imprime una línea como `sqlguardllm 0.5.0a4 listening on http://127.0.0.1:8787 (no token)` y sigue ejecutándose: deja esa terminal abierta. Solo escucha en tu equipo. Antes de exponerlo a otros equipos, configura un [token](../SERVER.md#token). Todavía no hay una imagen de Docker publicada; puedes construir una con el [Dockerfile](../SERVER.md#docker) del repositorio. `--language es` hace que los mensajes de las violaciones salgan en español.

## 3. Instala el cliente

En la primera terminal, en `guard-demo`:

```bash
npm init -y
npm install sqlguardllm
```

## 4. Revisa una consulta permitida y una rechazada

Guarda esto como `check.mjs`:

```js
import { SqlGuard } from "sqlguardllm";

const guard = new SqlGuard({ baseUrl: "http://localhost:8787" });

// El gerente de la sucursal 1. En tu app viene del usuario que inició sesión, nunca del modelo.
const context = { user: { branches: [1] } };

const invoices = await guard.check("SELECT id, total FROM invoices ORDER BY id", context);
console.log(invoices.ok, invoices.sql);

const emails = await guard.check("SELECT name, email FROM customers", context);
console.log(emails.ok, emails.violations.map((v) => `${v.code}: ${v.message}`));
```

El paquete incluye sus declaraciones de tipos, así que el mismo código funciona en TypeScript.

## 5. Ejecútalo

```bash
node check.mjs
```

Salida esperada:

```text
true SELECT id, total FROM (SELECT * FROM invoices WHERE branch_id IN (1)) AS invoices ORDER BY id LIMIT 100
false [ 'hidden_column: customers.email es una columna oculta' ]
```

La primera consulta se permite y se reescribe: solo las facturas de la sucursal 1, con un `LIMIT`. Ejecuta `invoices.sql`, nunca el texto original, con un usuario de base de datos de solo lectura. La segunda se rechaza porque la política oculta `customers.email`.

Si ves `SqlGuardError: cannot reach the sqlguardllm server`, el servidor no está en marcha. El cliente **falla cerrado**: lanza un error en vez de dejar pasar la consulta, así que nunca captures ese error para ejecutar el SQL de todos modos.

## 6. Cambia una regla

Para que el modelo lea el email enmascarado, reemplaza la línea de `hidden_columns` de `policy.json` por:

```json
  "masked_columns": {"customers.email": "partial"},
```

Detén el servidor (Ctrl+C), inícialo de nuevo y ejecuta `node check.mjs`. La segunda línea pasa a ser `true []`: la consulta se permite y `ceo@acme.example` vuelve como `ce***` ([`masked_columns`](../policy-reference.md#masked_columns)). Puedes activar y desactivar estas reglas sin instalar nada en el [playground](https://jmmana.github.io/sqlguardllm/playground/).

## Siguiente

- [README del cliente](https://github.com/jmmana/sqlguardllm/blob/main/clients/node/README.md) (en inglés): `enforce`, `wrap` para `pg` y `mysql2`, tokens, tiempos de espera y errores.
- [Servidor HTTP](../SERVER.md): opciones, el contrato `/v1` y notas de seguridad.
- [Referencia de políticas](../policy-reference.md): todos los campos.
