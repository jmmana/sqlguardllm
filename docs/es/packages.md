# Elige un paquete

[Read in English](../packages.md)

Usa el paquete de Python para ejecutar el guard en tu aplicación. Usa un cliente HTTP si tu aplicación está en otro lenguaje o si quieres que varias aplicaciones compartan un motor.

| Lenguaje | Instalación | Modo y entorno |
| --- | --- | --- |
| Python | `pip install --pre sqlguardllm` | Motor local en el mismo proceso o servidor HTTP. Python 3.10+ en Linux, macOS y Windows. |
| Node.js / TypeScript | `npm install sqlguardllm@alpha` | Remoto por HTTP. Node.js 18+ en Linux, macOS y Windows. |
| .NET | `dotnet add package SqlGuardLlm --prerelease` cuando se publique | Remoto por HTTP. El cliente en el repositorio es compatible con .NET 8 y .NET 10 en Linux, macOS y Windows. |
| Java | La publicación en Maven Central [está prevista](https://github.com/jmmana/sqlguardllm/issues/83). | El [primer cliente Java](https://github.com/jmmana/sqlguardllm/issues/80) está previsto para Java 17+ y usa HTTP. Las aplicaciones Java ya pueden usar la API HTTP. |

Los clientes de Node.js y .NET necesitan una [instancia de `sqlguardllm serve`](../SERVER.md) activa. Iníciala en un entorno de Python o en un contenedor. Hasta que el paquete esté disponible en NuGet, puedes agregar una referencia al [proyecto del cliente .NET](https://github.com/jmmana/sqlguardllm/tree/main/clients/dotnet) desde una copia local del repositorio. Los motores embebidos para [Node.js](https://github.com/jmmana/sqlguardllm/issues/73), [.NET](https://github.com/jmmana/sqlguardllm/issues/77) y [Java](https://github.com/jmmana/sqlguardllm/issues/81) están previstos.

Para Python, consulta el [inicio rápido](quickstart.md). Instala `sqlguardllm[yaml]` para cargar políticas YAML, `sqlguardllm[serve]` para cargar YAML en el servidor HTTP o `sqlguardllm[mcp]` para ejecutar el [servidor MCP](../MCP.md). Durante la fase alfa, usa también la opción `--pre` al instalar un extra.

## Versiones de los paquetes y API de Python

Los paquetes están en fase alfa. Antes de la versión 1.0, la API pública puede cambiar entre versiones. Fija las versiones que despliegues y consulta el [historial de cambios](https://github.com/jmmana/sqlguardllm/blob/main/CHANGELOG.md) antes de actualizar.

La [referencia de la API de Python](../api.md) define la interfaz prevista para la versión 1.0. Importa sus tipos desde `sqlguardllm`. Las API marcadas como experimentales pueden cambiar durante su desarrollo. Los códigos de infracción documentados se mantienen estables dentro de una versión mayor. Usa los códigos en la lógica de tu aplicación y muestra los mensajes a los usuarios.

A partir de la versión 1.0, los paquetes siguen el [versionado semántico](https://semver.org/lang/es/). Las versiones mayores pueden introducir cambios incompatibles en la API documentada, las menores añaden funciones compatibles y los parches incluyen correcciones compatibles. Python usa la notación de prelanzamientos de [PEP 440](https://peps.python.org/pep-0440/). Por ejemplo, `0.5.0a1` en PyPI corresponde a `0.5.0-alpha.1` en npm, que es también la notación de NuGet.

## Versión del protocolo HTTP

El prefijo `/v1` identifica el [contrato HTTP](../SERVER.md#contract) con independencia de la versión del paquete. Las ampliaciones compatibles mantienen el prefijo. Los clientes deben ignorar los campos adicionales en las respuestas y admitir nuevos códigos de infracción. Los cambios que obliguen a un cliente existente a modificar sus solicitudes o la lectura de las respuestas usan un prefijo nuevo. Usa `GET /v1/health` para consultar la versión del paquete del servidor.
