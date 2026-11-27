# Elige un paquete

[Read in English](../packages.md)

Usa el paquete de Python para ejecutar el guard en tu aplicación. Usa un cliente HTTP si tu aplicación está en otro lenguaje o si quieres que varias aplicaciones compartan un motor.

| Lenguaje | Instalación | Modo y entorno |
| --- | --- | --- |
| [Python](guide-python.md) | `pip install --pre sqlguardllm` | Motor local en el mismo proceso o servidor HTTP. Python 3.10+ en Linux, macOS y Windows. |
| [Node.js / TypeScript](guide-node.md) | `npm install sqlguardllm` | Local (el motor viene en el paquete: sin Python ni servidor) o remoto por HTTP. Node.js 18+; motor embebido en linux x64/arm64, macOS x64/arm64 y Windows x64. |
| [.NET](guide-dotnet.md) | `dotnet add package SqlGuardLlm --prerelease` | Local (el motor viene en el paquete: sin Python ni servidor) o remoto por HTTP. .NET 8 y .NET 10; motor embebido en linux-x64, linux-arm64, osx-x64, osx-arm64 y win-x64. |

**Paso a paso**, de la instalación a un programa que revisa una consulta permitida y una rechazada, con su salida exacta: [Python](guide-python.md), [Node.js / TypeScript](guide-node.md), [.NET](guide-dotnet.md).

Los paquetes de Node.js y .NET llevan el motor y lo ejecutan como proceso hijo de tu aplicación (`SqlGuard.local`, `SqlGuard.CreateLocalAsync`), con la misma API que sus clientes remotos. En una plataforma sin motor embebido (Linux musl como Alpine, otras CPU), con Deno, navegadores o entornos edge, o para compartir un motor entre servicios, usa una [instancia de `sqlguardllm serve`](../SERVER.md) activa, que inicias en un entorno de Python o en un contenedor.

Para Python, consulta el [inicio rápido](quickstart.md). Instala `sqlguardllm[yaml]` para cargar políticas YAML, `sqlguardllm[serve]` para cargar YAML en el servidor HTTP o `sqlguardllm[mcp]` para ejecutar el [servidor MCP](../MCP.md). Durante la fase alfa, usa también la opción `--pre` al instalar un extra.

## Versiones de los paquetes y API de Python

Los paquetes están en fase alfa. Antes de la versión 1.0, la API pública puede cambiar entre versiones. Fija las versiones que despliegues y consulta el [historial de cambios](https://github.com/jmmana/sqlguardllm/blob/main/CHANGELOG.md) antes de actualizar.

La [referencia de la API de Python](../api.md) define la interfaz prevista para la versión 1.0. Importa sus tipos desde `sqlguardllm`. Las API marcadas como experimentales pueden cambiar durante su desarrollo. Los códigos de infracción documentados se mantienen estables dentro de una versión mayor. Usa los códigos en la lógica de tu aplicación y muestra los mensajes a los usuarios.

A partir de la versión 1.0, los paquetes siguen el [versionado semántico](https://semver.org/lang/es/). Las versiones mayores pueden introducir cambios incompatibles en la API documentada, las menores añaden funciones compatibles y los parches incluyen correcciones compatibles. Python usa la notación de prelanzamientos de [PEP 440](https://peps.python.org/pep-0440/). Por ejemplo, `0.5.0a1` en PyPI corresponde a `0.5.0-alpha.1` en npm, que es también la notación de NuGet.

## Versión del protocolo HTTP

El prefijo `/v1` identifica el [contrato HTTP](../SERVER.md#contract) con independencia de la versión del paquete. Las ampliaciones compatibles mantienen el prefijo. Los clientes deben ignorar los campos adicionales en las respuestas y admitir nuevos códigos de infracción. Los cambios que obliguen a un cliente existente a modificar sus solicitudes o la lectura de las respuestas usan un prefijo nuevo. Usa `GET /v1/health` para consultar la versión del paquete del servidor.
