# .NET, paso a paso

[Read in English](../guide-dotnet.md) · [Elige un paquete](packages.md) · [Python](guide-python.md) · [Node.js](guide-node.md)

El paquete de NuGet `SqlGuardLlm` es un cliente pequeño, sin más dependencias que `HttpClient` y `System.Text.Json`. El motor se ejecuta en `sqlguardllm serve`, un servidor HTTP junto a tu aplicación, así que .NET recibe exactamente las mismas respuestas que Python. Necesitas el SDK de .NET 8 o .NET 10, y Python 3.10 o posterior para el servidor.

!!! note "Hoy el cliente necesita un servidor en marcha"
    Un motor embebido, para que el paquete funcione sin Python y sin servidor, está previsto en [#86](https://github.com/jmmana/sqlguardllm/issues/86).

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

Imprime una línea como `sqlguardllm 0.5.0a3 listening on http://127.0.0.1:8787 (no token)` y sigue ejecutándose: deja esa terminal abierta. Solo escucha en tu equipo. Antes de exponerlo a otros equipos, configura un [token](../SERVER.md#token). Todavía no hay una imagen de Docker publicada; puedes construir una con el [Dockerfile](../SERVER.md#docker) del repositorio. `--language es` hace que los mensajes de las violaciones salgan en español.

## 3. Instala el cliente

En la primera terminal, en `guard-demo`:

```bash
dotnet new console -n GuardDemo
cd GuardDemo
dotnet add package SqlGuardLlm --prerelease
```

`--prerelease` hace falta mientras el paquete esté en fase alfa. El paquete está en [NuGet](https://www.nuget.org/packages/SqlGuardLlm) desde la 0.5.0-alpha.2.

## 4. Revisa una consulta permitida y una rechazada

Reemplaza `Program.cs` por:

```csharp
using SqlGuardLlm;

using var guard = new SqlGuardClient("http://localhost:8787");

// El gerente de la sucursal 1. En tu app viene del usuario que inició sesión, nunca del modelo.
var context = new Dictionary<string, object?>
{
    ["user"] = new Dictionary<string, object?> { ["branches"] = new[] { 1 } },
};

var invoices = await guard.CheckAsync("SELECT id, total FROM invoices ORDER BY id", context);
Console.WriteLine($"{invoices.Ok} {invoices.Sql}");

var emails = await guard.CheckAsync("SELECT name, email FROM customers", context);
Console.WriteLine($"{emails.Ok} {string.Join("; ", emails.Violations.Select(v => $"{v.Code}: {v.Message}"))}");
```

## 5. Ejecútalo

```bash
dotnet run
```

Salida esperada:

```text
True SELECT id, total FROM (SELECT * FROM invoices WHERE branch_id IN (1)) AS invoices ORDER BY id LIMIT 100
False hidden_column: customers.email es una columna oculta
```

La primera consulta se permite y se reescribe: solo las facturas de la sucursal 1, con un `LIMIT`. Ejecuta `invoices.Sql`, nunca el texto original, con un usuario de base de datos de solo lectura. La segunda se rechaza porque la política oculta `customers.email`.

Si ves `SqlGuardException: Could not reach the sqlguardllm server.`, el servidor no está en marcha. El cliente **falla cerrado**: lanza una excepción en vez de dejar pasar la consulta, así que nunca la captures para ejecutar el SQL de todos modos.

## 6. Cambia una regla

Para que el modelo lea el email enmascarado, reemplaza la línea de `hidden_columns` de `policy.json` por:

```json
  "masked_columns": {"customers.email": "partial"},
```

Detén el servidor (Ctrl+C), inícialo de nuevo y ejecuta `dotnet run`. La segunda línea pasa a ser `True` sin violaciones: la consulta se permite y `ceo@acme.example` vuelve como `ce***` ([`masked_columns`](../policy-reference.md#masked_columns)). Puedes activar y desactivar estas reglas sin instalar nada en el [playground](https://jmmana.github.io/sqlguardllm/playground/).

## Siguiente

- [README del cliente](https://github.com/jmmana/sqlguardllm/blob/main/clients/dotnet/README.md) (en inglés): `EnforceAsync`, inyección de dependencias con tu propio `HttpClient`, Dapper y ADO.NET, tokens y errores.
- [Servidor HTTP](../SERVER.md): opciones, el contrato `/v1` y notas de seguridad.
- [Referencia de políticas](../policy-reference.md): todos los campos.
