# Node.js and TypeScript, step by step

[Leer en español](es/guide-node.md) · [Choose a package](packages.md) · [Python](guide-python.md) · [.NET](guide-dotnet.md)

The npm package `sqlguardllm` runs the engine for you: `npm install sqlguardllm` also installs the engine for your platform (linux x64/arm64, macOS x64/arm64, Windows x64), so the guard runs inside your Node.js process tree with **no Python and no server**. This is the local mode, and it is what this guide uses. You need Node.js 18 or later. If your platform has no engine package (for example Alpine/musl), or you run on Deno, a browser or an edge runtime, use the [remote mode](#remote-mode-a-server-instead-of-the-embedded-engine) with a running server.

## 1. Write a policy

```bash
mkdir guard-demo && cd guard-demo
```

Save this as `policy.json` in that folder.

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

The model may read three tables ([`tables`](policy-reference.md#tables)), never `customers.email` ([`hidden_columns`](policy-reference.md#hidden_columns)), only the invoices and branches of the user's branches ([`row_filters`](policy-reference.md#row_filters)), and at most 100 rows ([`max_rows`](policy-reference.md#max_rows)).

## 2. Install the client

```bash
npm init -y
npm install sqlguardllm
```

## 3. Check one allowed and one rejected query

Save this as `check.mjs`:

```js
import { SqlGuard } from "sqlguardllm";

// Starts the embedded engine (its SHA-256 is verified first) and loads the policy.
const guard = await SqlGuard.local({ policyPath: "policy.json" });

// The manager of branch 1. In your app this comes from the signed-in user, never from the model.
const context = { user: { branches: [1] } };

try {
  const invoices = await guard.check("SELECT id, total FROM invoices ORDER BY id", context);
  console.log(invoices.ok, invoices.sql);

  const emails = await guard.check("SELECT name, email FROM customers", context);
  console.log(emails.ok, emails.violations.map((v) => `${v.code}: ${v.message}`));
} finally {
  await guard.close(); // stops the engine
}
```

The package ships its type declarations, so the same code works in TypeScript (`import { SqlGuard } from "sqlguardllm"`, `const guard: SqlGuard = await SqlGuard.local(...)`).

## 4. Run it

```bash
node check.mjs
```

Expected output:

```text
true SELECT id, total FROM (SELECT * FROM invoices WHERE branch_id IN (1)) AS invoices ORDER BY id LIMIT 100
false [ 'hidden_column: customers.email is a hidden column' ]
```

The first query is allowed and rewritten: only branch 1's invoices, with a `LIMIT`. Run `invoices.sql`, never the original text, with a read-only database user. The second is rejected because the policy hides `customers.email`. Pass `language: "es"` to `SqlGuard.local` for messages in Spanish.

The client **fails closed**: if the engine cannot start, crashes or times out, the call throws a `SqlGuardError` (and the engine is restarted with a backoff) instead of letting the query through, so never catch that error and run the SQL anyway. On an unsupported platform the error is `unsupported_platform` and its message names the remote mode.

## 5. Change a rule

To let the model read the email masked, replace the `hidden_columns` line of `policy.json` with:

```json
  "masked_columns": {"customers.email": "partial"},
```

Run `node check.mjs` again. The second line becomes `true []`: the query is allowed, and `ceo@acme.example` comes back as `ce***` ([`masked_columns`](policy-reference.md#masked_columns)). You can flip these rules without installing anything in the [playground](https://jmmana.github.io/sqlguardllm/playground/).

## Remote mode: a server instead of the embedded engine

Use it when several services share one policy, or on a platform that has no embedded engine. The engine then runs in `sqlguardllm serve`, an HTTP server next to your application (Python 3.10 or later). In another terminal, from the same folder, with [uv](https://docs.astral.sh/uv/):

```bash
uvx sqlguardllm serve --policy policy.json --port 8787
```

With [pipx](https://pipx.pypa.io/) it is `pipx run sqlguardllm serve --policy policy.json --port 8787`. With plain pip: `pip install --pre sqlguardllm`, then `sqlguardllm serve --policy policy.json --port 8787`. It only listens on your machine; before exposing it to other hosts, set a [token](SERVER.md#token). In `check.mjs`, replace the first line of code with:

```js
const guard = new SqlGuard({ baseUrl: "http://localhost:8787" });
```

(and drop the `finally` block: there is no engine to close). The output is the same. If you see `SqlGuardError: cannot reach the sqlguardllm server`, the server is not running; the client fails closed there too. Start the server with `--language es` for messages in Spanish. After editing `policy.json`, restart the server.

## Next

- [Client README](../clients/node/README.md): local and remote mode, `enforce`, `wrap` for `pg` and `mysql2`, tokens, timeouts and errors.
- [Recipes](RECIPES.md#nodejs): LangChain.js, the Vercel AI SDK, `pg`, `mysql2` and Prisma.
- [HTTP server](SERVER.md): flags, the `/v1` contract and security notes.
- [Policy reference](policy-reference.md): every field.
