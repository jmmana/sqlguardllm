# Choose a package

[Leer en español](es/packages.md)

Use the Python package to run the guard in your application. Use an HTTP client when your application runs in a different language or when you want several applications to share one engine.

| Language | Install | Mode and runtime |
| --- | --- | --- |
| [Python](guide-python.md) | `pip install --pre sqlguardllm` | Local in-process engine or HTTP server. Python 3.10+ on Linux, macOS and Windows. |
| [Node.js / TypeScript](guide-node.md) | `npm install sqlguardllm` | Remote over HTTP. Node.js 18+ on Linux, macOS and Windows. |
| [.NET](guide-dotnet.md) | `dotnet add package SqlGuardLlm --prerelease` once published | Remote over HTTP. The source client targets .NET 8 and .NET 10 on Linux, macOS and Windows. |
| Java | Maven Central publication is [planned](https://github.com/jmmana/sqlguardllm/issues/83). | The [first Java client](https://github.com/jmmana/sqlguardllm/issues/80) targets Java 17+ and uses HTTP. Java applications can use the HTTP API today. |

**Step by step**, from install to a program that checks one allowed and one rejected query, with its exact output: [Python](guide-python.md), [Node.js / TypeScript](guide-node.md), [.NET](guide-dotnet.md).

The Node.js and .NET clients use a running [`sqlguardllm serve` instance](SERVER.md). Start it in a Python environment or a container. Until the NuGet package is available, use a project reference to the [.NET client source](https://github.com/jmmana/sqlguardllm/tree/main/clients/dotnet) in a local checkout. Embedded engines for [Node.js](https://github.com/jmmana/sqlguardllm/issues/73), [.NET](https://github.com/jmmana/sqlguardllm/issues/77) and [Java](https://github.com/jmmana/sqlguardllm/issues/81) are planned.

For Python, start with the [quickstart](quickstart.md). Install `sqlguardllm[yaml]` to load YAML policies, `sqlguardllm[serve]` to load YAML in the HTTP server, or `sqlguardllm[mcp]` to run the [MCP server](MCP.md). Use the same `--pre` flag when installing an extra during the alpha.

## Package versions and Python API

The packages are in alpha. Before 1.0, the public API may change between releases. Pin the versions you deploy and read the [changelog](https://github.com/jmmana/sqlguardllm/blob/main/CHANGELOG.md) before upgrading.

The [Python API reference](api.md) defines the interface intended for 1.0. Import its types from `sqlguardllm`. APIs explicitly marked experimental may change while they are being developed. The documented machine-readable violation codes are stable within a major version. Use the codes for program logic and display messages to users.

From 1.0, package releases follow [Semantic Versioning](https://semver.org/). Major releases can break the documented API, minor releases add compatible functionality, and patch releases provide compatible fixes. Python uses [PEP 440](https://peps.python.org/pep-0440/) prerelease spelling. For example, PyPI's `0.5.0a1` corresponds to npm's `0.5.0-alpha.1`, the version spelling also used by NuGet.

## HTTP protocol version

The `/v1` prefix identifies the [HTTP contract](SERVER.md#contract) independently of the package version. Compatible additions use the same prefix. Clients should ignore extra response fields and handle new violation codes. Changes that require an existing client to update its requests or its response parsing use a new prefix. Use `GET /v1/health` to read the server's package version.
