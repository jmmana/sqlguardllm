# Node.js y TypeScript, paso a paso

[Read in English](../guide-node.md) · [Elige un paquete](packages.md) · [Python](guide-python.md) · [.NET](guide-dotnet.md)

El paquete de npm `sqlguardllm` ejecuta el motor por ti: `npm install sqlguardllm` también instala el motor de tu plataforma (linux x64/arm64, macOS x64/arm64, Windows x64), así que el guard corre junto a tu proceso de Node.js **sin Python y sin servidor**. Es el modo local, y es el que usa esta guía. Necesitas Node.js 18 o posterior. Si tu plataforma no tiene paquete de motor (por ejemplo Alpine/musl), o usas Deno, un navegador o un entorno edge, usa el [modo remoto](#modo-remoto-un-servidor-en-lugar-del-motor-embebido) con un servidor en marcha.

## 1. Escribe una política

```bash
mkdir guard-demo && cd guard-demo
```

Guarda esto como `policy.json` en esa carpeta.

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

## 2. Instala el cliente

```bash
npm init -y
npm install sqlguardllm
```

## 3. Revisa una consulta permitida y una rechazada

Guarda esto como `check.mjs`:

```js
import { SqlGuard } from "sqlguardllm";

// Inicia el motor embebido (antes verifica su SHA-256) y carga la política.
const guard = await SqlGuard.local({ policyPath: "policy.json", language: "es" });

// El gerente de la sucursal 1. En tu app viene del usuario que inició sesión, nunca del modelo.
const context = { user: { branches: [1] } };

try {
  const invoices = await guard.check("SELECT id, total FROM invoices ORDER BY id", context);
  console.log(invoices.ok, invoices.sql);

  const emails = await guard.check("SELECT name, email FROM customers", context);
  console.log(emails.ok, emails.violations.map((v) => `${v.code}: ${v.message}`));
} finally {
  await guard.close(); // detiene el motor
}
```

`language: "es"` hace que los mensajes de las violaciones salgan en español. El paquete incluye sus declaraciones de tipos, así que el mismo código funciona en TypeScript (`import { SqlGuard } from "sqlguardllm"`, `const guard: SqlGuard = await SqlGuard.local(...)`).

## 4. Ejecútalo

```bash
node check.mjs
```

Salida esperada:

```text
true SELECT id, total FROM (SELECT * FROM invoices WHERE branch_id IN (1)) AS invoices ORDER BY id LIMIT 100
false [ 'hidden_column: customers.email es una columna oculta' ]
```

La primera consulta se permite y se reescribe: solo las facturas de la sucursal 1, con un `LIMIT`. Ejecuta `invoices.sql`, nunca el texto original, con un usuario de base de datos de solo lectura. La segunda se rechaza porque la política oculta `customers.email`.

El cliente **falla cerrado**: si el motor no puede iniciar, se cae o se pasa del tiempo, la llamada lanza un `SqlGuardError` (y el motor se reinicia con espera creciente) en vez de dejar pasar la consulta, así que nunca captures ese error para ejecutar el SQL de todos modos. En una plataforma no soportada el error es `unsupported_platform` y su mensaje nombra el modo remoto.

## 5. Cambia una regla

Para que el modelo lea el email enmascarado, reemplaza la línea de `hidden_columns` de `policy.json` por:

```json
  "masked_columns": {"customers.email": "partial"},
```

Ejecuta `node check.mjs` de nuevo. La segunda línea pasa a ser `true []`: la consulta se permite y `ceo@acme.example` vuelve como `ce***` ([`masked_columns`](../policy-reference.md#masked_columns)). Puedes activar y desactivar estas reglas sin instalar nada en el [playground](https://jmmana.github.io/sqlguardllm/playground/).

## Modo remoto: un servidor en lugar del motor embebido

Úsalo cuando varios servicios comparten una política, o en una plataforma sin motor embebido. El motor corre entonces en `sqlguardllm serve`, un servidor HTTP junto a tu aplicación (Python 3.10 o posterior). En otra terminal, desde la misma carpeta, con [uv](https://docs.astral.sh/uv/):

```bash
uvx sqlguardllm serve --policy policy.json --port 8787 --language es
```

Con [pipx](https://pipx.pypa.io/) es `pipx run sqlguardllm serve --policy policy.json --port 8787 --language es`. Con pip: `pip install --pre sqlguardllm` y luego `sqlguardllm serve --policy policy.json --port 8787 --language es`. Solo escucha en tu equipo; antes de exponerlo a otros equipos, configura un [token](../SERVER.md#token). En `check.mjs`, reemplaza la primera línea de código por:

```js
const guard = new SqlGuard({ baseUrl: "http://localhost:8787" });
```

(y quita el bloque `finally`: no hay motor que cerrar). La salida es la misma. Si ves `SqlGuardError: cannot reach the sqlguardllm server`, el servidor no está en marcha; ahí también el cliente falla cerrado. Tras editar `policy.json`, reinicia el servidor.

## Siguiente

- [README del cliente](https://github.com/jmmana/sqlguardllm/blob/main/clients/node/README.md) (en inglés): modo local y remoto, `enforce`, `wrap` para `pg` y `mysql2`, tokens, tiempos de espera y errores.
- [Recetas](../RECIPES.md#nodejs) (en inglés): LangChain.js, Vercel AI SDK, `pg`, `mysql2` y Prisma.
- [Servidor HTTP](../SERVER.md): opciones, el contrato `/v1` y notas de seguridad.
- [Referencia de políticas](../policy-reference.md): todos los campos.
