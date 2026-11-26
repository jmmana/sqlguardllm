# Python, step by step

[Leer en español](es/guide-python.md) · [Choose a package](packages.md) · [Node.js](guide-node.md) · [.NET](guide-dotnet.md)

The Python package is the engine itself: it runs inside your application, with no server. You need Python 3.10 or later, on Linux, macOS or Windows.

## 1. Install

```bash
mkdir guard-demo && cd guard-demo
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install --pre sqlguardllm
```

`--pre` is needed while sqlguardllm is in alpha. To load YAML policies, install `"sqlguardllm[yaml]"` instead.

## 2. Write a policy

Save this as `policy.json` in the same folder:

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

In plain words:

- [`tables`](policy-reference.md#tables): the model may read `invoices`, `branches` and `customers`, and no other table.
- [`hidden_columns`](policy-reference.md#hidden_columns): `customers.email` can't be used anywhere, so a query that mentions it is rejected. To let the model see it masked instead, see [step 5](#5-change-a-rule).
- [`row_filters`](policy-reference.md#row_filters): every read of `invoices` and `branches` is limited to the user's branches. `{user.branches}` is filled in from the context your application passes, never from the model.
- [`max_rows`](policy-reference.md#max_rows): at most 100 rows per query.

## 3. Check one allowed and one rejected query

Save this as `check.py`:

```python
from sqlguardllm import Guard, Policy

guard = Guard(Policy.from_file("policy.json"))

# The manager of branch 1. In your app this comes from the signed-in user, never from the model.
context = {"user": {"branches": [1]}}

invoices = guard.check("SELECT id, total FROM invoices ORDER BY id", context)
print(invoices.ok, invoices.sql)

emails = guard.check("SELECT name, email FROM customers", context)
print(emails.ok, [f"{v.code.value}: {v.message}" for v in emails.violations])
```

## 4. Run it

```bash
python check.py
```

Expected output:

```text
True SELECT id, total FROM (SELECT * FROM invoices WHERE branch_id IN (1)) AS invoices ORDER BY id LIMIT 100
False ['hidden_column: customers.email is a hidden column']
```

The first query is allowed and rewritten: it can only see branch 1's invoices, and it got a `LIMIT`. Your application runs `invoices.sql`, never the original text, with a read-only database user. The second query is rejected because the policy hides `customers.email`. `Guard(..., language="es")` gives the messages in Spanish.

## 5. Change a rule

To let the model read the email masked, replace the `hidden_columns` line of `policy.json` with:

```json
  "masked_columns": {"customers.email": "partial"},
```

Run `python check.py` again. The second line becomes `True []`: the query is allowed, and `emails.sql` is

```sql
SELECT name, CASE WHEN email IS NULL THEN NULL ELSE SUBSTRING(CAST(email AS VARCHAR(4000)) FROM 1 FOR 2) || '***' END AS email FROM customers LIMIT 100
```

so `ceo@acme.example` comes back as `ce***`. Masks are `partial`, `redact`, `null` and `hash` ([`masked_columns`](policy-reference.md#masked_columns)). You can flip these rules without installing anything in the [playground](https://jmmana.github.io/sqlguardllm/playground/).

## Next

- [Quickstart](quickstart.md) and [recipes](RECIPES.md) for DB-API, SQLAlchemy, LangChain, LlamaIndex and Vanna.
- [Policy reference](policy-reference.md): every field. [Rules](DESIGN.md): what is checked and how queries are rewritten.
- [API reference](api.md): `Guard.check`, `Guard.enforce`, `Guard.explain`.
