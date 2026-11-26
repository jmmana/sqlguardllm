# Node.js and TypeScript, step by step

[Leer en español](es/guide-node.md) · [Choose a package](packages.md) · [Python](guide-python.md) · [.NET](guide-dotnet.md)

The npm package `sqlguardllm` is a small client with no dependencies. The engine runs in `sqlguardllm serve`, an HTTP server next to your application, so Node.js gets exactly the same answers as Python. You need Node.js 18 or later, and Python 3.10 or later for the server.

!!! note "Today the client needs a running server"
    An embedded engine, so that `npm install sqlguardllm` works with no Python and no server, is planned in [#85](https://github.com/jmmana/sqlguardllm/issues/85).

## 1. Write a policy

```bash
mkdir guard-demo && cd guard-demo
```

Save this as `policy.json` in that folder. The server reads it; the client never does.

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

## 2. Start the server, in another terminal

From the same folder, with [uv](https://docs.astral.sh/uv/):

```bash
uvx sqlguardllm serve --policy policy.json --port 8787
```

With [pipx](https://pipx.pypa.io/) it is `pipx run sqlguardllm serve --policy policy.json --port 8787`. With plain pip:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install --pre sqlguardllm
sqlguardllm serve --policy policy.json --port 8787
```

It prints a line like `sqlguardllm 0.5.0a2 listening on http://127.0.0.1:8787 (no token)` and keeps running: leave that terminal open. It only listens on your machine. Before exposing it to other hosts, set a [token](SERVER.md#token). There is no published Docker image yet; you can build one from the repository's [Dockerfile](SERVER.md#docker).

## 3. Install the client

In the first terminal, in `guard-demo`:

```bash
npm init -y
npm install sqlguardllm@alpha
```

## 4. Check one allowed and one rejected query

Save this as `check.mjs`:

```js
import { SqlGuard } from "sqlguardllm";

const guard = new SqlGuard({ baseUrl: "http://localhost:8787" });

// The manager of branch 1. In your app this comes from the signed-in user, never from the model.
const context = { user: { branches: [1] } };

const invoices = await guard.check("SELECT id, total FROM invoices ORDER BY id", context);
console.log(invoices.ok, invoices.sql);

const emails = await guard.check("SELECT name, email FROM customers", context);
console.log(emails.ok, emails.violations.map((v) => `${v.code}: ${v.message}`));
```

The package ships its type declarations, so the same code works in TypeScript.

## 5. Run it

```bash
node check.mjs
```

Expected output:

```text
true SELECT id, total FROM (SELECT * FROM invoices WHERE branch_id IN (1)) AS invoices ORDER BY id LIMIT 100
false [ 'hidden_column: customers.email is a hidden column' ]
```

The first query is allowed and rewritten: only branch 1's invoices, with a `LIMIT`. Run `invoices.sql`, never the original text, with a read-only database user. The second is rejected because the policy hides `customers.email`. Start the server with `--language es` for messages in Spanish.

If you see `SqlGuardError: cannot reach the sqlguardllm server`, the server is not running. The client **fails closed**: it throws instead of letting the query through, so never catch that error and run the SQL anyway.

## 6. Change a rule

To let the model read the email masked, replace the `hidden_columns` line of `policy.json` with:

```json
  "masked_columns": {"customers.email": "partial"},
```

Stop the server (Ctrl+C), start it again, and run `node check.mjs`. The second line becomes `true []`: the query is allowed, and `ceo@acme.example` comes back as `ce***` ([`masked_columns`](policy-reference.md#masked_columns)). You can flip these rules without installing anything in the [playground](https://jmmana.github.io/sqlguardllm/playground/).

## Next

- [Client README](../clients/node/README.md): `enforce`, `wrap` for `pg` and `mysql2`, tokens, timeouts and errors.
- [HTTP server](SERVER.md): flags, the `/v1` contract and security notes.
- [Policy reference](policy-reference.md): every field.
