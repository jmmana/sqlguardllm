# .NET, paso a paso

[Read in English](../guide-dotnet.md) · [Elige un paquete](packages.md) · [Python](guide-python.md) · [Node.js](guide-node.md)

El paquete NuGet `SqlGuardLlm` incluye el motor para linux-x64, linux-arm64, osx-x64, osx-arm64 y win-x64 (en `runtimes/{rid}/native/`), así que el guard corre como proceso hijo de tu app **sin Python y sin servidor**. Es el modo local, y es el que usa esta guía. Necesitas el SDK de .NET 8 o .NET 10. Si tu plataforma no tiene motor (otro sistema u otra CPU, o Linux musl como Alpine), usa el [modo remoto](#modo-remoto-un-servidor-en-lugar-del-motor-embebido) con un servidor en marcha.

## 1. Crea el proyecto y escribe una política

```bash
dotnet new console -n GuardDemo
cd GuardDemo
dotnet add package SqlGuardLlm --prerelease
```

`--prerelease` hace falta mientras el paquete esté en alfa. El paquete está en [NuGet](https://www.nuget.org/packages/SqlGuardLlm).

Guarda esto como `policy.json` en la carpeta `GuardDemo`:

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

## 2. Revisa una consulta permitida y una rechazada

Reemplaza `Program.cs` por:

```csharp
using SqlGuardLlm;

// Inicia el motor embebido (antes verifica el SHA-256 de sus archivos) y carga la política.
await using var guard = await SqlGuard.CreateLocalAsync(new SqlGuardLocalOptions
{
    PolicyJson = await File.ReadAllTextAsync("policy.json"),
    Language = "es",
});

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

`SqlGuard.CreateLocalAsync` devuelve un `ISqlGuard`, la misma interfaz (`CheckAsync`, `EnforceAsync`, `ExplainAsync`, cada una con un `CancellationToken`) que el `SqlGuardClient` remoto. Es seguro entre hilos: comparte una sola instancia en toda la app. `Language = "es"` hace que los mensajes salgan en español.

## 3. Ejecútalo

```bash
dotnet run
```

Salida esperada:

```text
True SELECT id, total FROM (SELECT * FROM invoices WHERE branch_id IN (1)) AS invoices ORDER BY id LIMIT 100
False hidden_column: customers.email es una columna oculta
```

La primera consulta se permite y se reescribe: solo las facturas de la sucursal 1, con un `LIMIT`. Ejecuta `invoices.Sql`, nunca el texto original, con un usuario de base de datos de solo lectura. La segunda se rechaza porque la política oculta `customers.email`.

El cliente **falla cerrado**: si el motor no puede iniciar, un archivo no coincide con su SHA-256, el motor se cae o una llamada se pasa del tiempo, la llamada lanza una `SqlGuardException` (y el motor se reinicia con espera creciente) en vez de dejar pasar la consulta, así que nunca captures esa excepción para ejecutar el SQL de todos modos. En una plataforma no soportada la excepción tiene el código `engine_unsupported` y su mensaje nombra el modo remoto.

También funciona con `dotnet publish -r <rid>`, publicaciones autocontenidas y de archivo único (la carpeta `sqlguardllm-engine` queda junto al ejecutable), Native AOT e imágenes de contenedor Linux (basadas en glibc, como `mcr.microsoft.com/dotnet/runtime-deps`; no Alpine).

## 4. Cambia una regla

Para que el modelo lea el email enmascarado, reemplaza la línea de `hidden_columns` de `policy.json` por:

```json
  "masked_columns": {"customers.email": "partial"},
```

Ejecuta `dotnet run` de nuevo. La segunda línea pasa a ser `True` sin violaciones: la consulta se permite y `ceo@acme.example` vuelve como `ce***` ([`masked_columns`](../policy-reference.md#masked_columns)). Puedes activar y desactivar estas reglas sin instalar nada en el [playground](https://jmmana.github.io/sqlguardllm/playground/).

## Modo remoto: un servidor en lugar del motor embebido

Úsalo cuando varios servicios comparten una política, o en una plataforma sin motor embebido. El motor corre entonces en `sqlguardllm serve`, un servidor HTTP junto a tu aplicación (Python 3.10 o posterior). En otra terminal, desde la misma carpeta, con [uv](https://docs.astral.sh/uv/):

```bash
uvx sqlguardllm serve --policy policy.json --port 8787 --language es
```

Con [pipx](https://pipx.pypa.io/) es `pipx run sqlguardllm serve --policy policy.json --port 8787 --language es`. Con pip: `pip install --pre sqlguardllm` y luego `sqlguardllm serve --policy policy.json --port 8787 --language es`. Solo escucha en tu equipo; antes de exponerlo a otros equipos, configura un [token](../SERVER.md#token). En `Program.cs`, reemplaza la instrucción `await using var guard = ...` por:

```csharp
using var guard = new SqlGuardClient("http://localhost:8787");
```

La salida es la misma. Si ves `SqlGuardException: Could not reach the sqlguardllm server.`, el servidor no está en marcha; ahí también el cliente falla cerrado. Tras editar `policy.json`, reinicia el servidor.

## Siguiente

- [README del cliente](https://github.com/jmmana/sqlguardllm/blob/main/clients/dotnet/README.md) (en inglés): modo local y remoto, `EnforceAsync`, inyección de dependencias, Dapper y ADO.NET, tokens y errores.
- [Recetas](../RECIPES.md#net) (en inglés): `AddSqlGuard`, EF Core, Dapper, Semantic Kernel y Microsoft.Extensions.AI.
- [Servidor HTTP](../SERVER.md): opciones, el contrato `/v1` y notas de seguridad.
- [Referencia de políticas](../policy-reference.md): todos los campos.
