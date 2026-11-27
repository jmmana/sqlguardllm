# Quickstart

[Leer en español](es/quickstart.md) · [Choose a package](packages.md)

This page is for Python. For **Node.js / TypeScript** or **.NET**, follow the step-by-step guides for [Node.js](guide-node.md) and [.NET](guide-dotnet.md): they install the package, which carries the engine (no Python, no server), and check the same kind of queries; the remote mode with `sqlguardllm serve` is the last section of each. The [Python guide](guide-python.md) does the same with a policy file.

```bash
pip install --pre sqlguardllm
```

```python
from sqlguardllm import Guard, Policy

policy = Policy(
    dialect="postgres",
    tables=["orders", "customers"],
    hidden_columns=["customers.ssn"],
    row_filters={"orders": "tenant_id = {tenant_id}"},
    max_rows=100,
)
guard = Guard(policy)

result = guard.check("SELECT id, total FROM orders", context={"tenant_id": 7})
print(result.ok)  # True
print(result.sql)      # rewritten: row filter and LIMIT applied

blocked = guard.check("DELETE FROM orders")
print(blocked.ok, [v.code for v in blocked.violations])
```

Next: the [rules](DESIGN.md), the [policy reference](policy-reference.md) and the [API reference](api.md).

## Test a policy in CI

Keep a YAML or JSON file of queries your policy should allow or reject. YAML uses the optional extra `pip install --pre "sqlguardllm[yaml]"`.

```yaml
cases:
  - name: branch manager can read invoices
    sql: SELECT id FROM invoices
    context: {user: {branches: [1, 2]}}
    expect: allow
    sql_contains: branch_id IN (1, 2)
  - name: customer emails are private
    sql: SELECT email FROM customers
    expect: reject
    violation: hidden_column
```

Run the [demo cases](../examples/policy-tests.yaml) with the demo policy from the repository root. The [policy-set cases](../examples/roles-tests.yaml) test each role of [`roles.yaml`](../examples/roles.yaml). CI runs both.

```bash
sqlguardllm test examples/policy-tests.yaml --policy examples/policy.yaml
sqlguardllm test examples/roles-tests.yaml --policy examples/roles.yaml
sqlguardllm test examples/policy-tests.yaml --policy examples/policy.yaml --format junit > policy-results.xml
sqlguardllm schema --cases > policy-tests.schema.json
```

Use `role` on every case that tests a policy set. The case can also supply `context` to fill that role's row-filter values. Omit `role` with a single policy. On rejects, `violation` matches a public code. On allows, `sql_contains` matches a literal, case-sensitive substring of the rewritten SQL.

The command returns 0 when all cases pass, 1 when any expectation fails, and 2 for invalid inputs. JUnit reports contain case names and failure reasons. The checks run without a database connection.

## Lint a policy

`sqlguardllm lint` warns about policies that are valid but probably unsafe. It reads a policy or a policy set and never connects to a database.

```bash
sqlguardllm lint --policy examples/roles.yaml
sqlguardllm lint --policy policy.yaml --strict                        # exit 1 on any warning
sqlguardllm lint --policy policy.yaml --format json --language es
```

| Code | Warns when | Field |
|---|---|---|
| `no-table-allowlist` | `tables` is `null`, so every table can be read | [`tables`](policy-reference.md#tables) |
| `tenant-without-row-filter` | a table has a tenant-like column (`tenant_id`, `company_id`, `branch_id`, `org_id`, `sucursal`…) and no row filter | [`row_filters`](policy-reference.md#row_filters) |
| `sensitive-column-exposed` | a column that looks sensitive (`password`, `token`, `secret`, `ssn`, `cedula`, `email`, `phone`, `card`…) is neither hidden nor masked | [`hidden_columns`](policy-reference.md#hidden_columns) |
| `max-rows-disabled` | `max_rows` is `null` | [`max_rows`](policy-reference.md#max_rows) |
| `max-rows-high` | `max_rows` is above 10000, or the value of `--max-rows-threshold` | [`max_rows`](policy-reference.md#max_rows) |
| `aggregate-k-low` | an `aggregate_only` table allows groups of fewer than 5 rows | [`aggregate_only`](policy-reference.md#aggregate_only) |
| `denied-function-allowed` | `denied_functions` or `allowed_functions` lets queries call a function on the default denylist | [`denied_functions`](policy-reference.md#denied_functions) |

The tenant and sensitive-column checks read the columns from the policy's [`schema`](policy-reference.md#schema) (table → columns), which `Policy.from_database` can fill in. Without `schema`, the report lists them as skipped. It also lists allowed tables that are missing from `schema`. Each warning has an explanation, a fix and a link to its field:

```text
WARN tenant-without-row-filter: orders has the tenant-like column tenant_id and no row filter
  Why: Without a row filter, every user of this policy reads the rows of every tenant, company or branch in orders.
  Fix: Add a row filter filled from the signed-in user, such as row_filters: {orders: "tenant_id = {user.tenant_id}"}. If every tenant shares the table on purpose, suppress the code in lint_suppress with the reason.
  See: https://jmmana.github.io/sqlguardllm/policy-reference/#row_filters
1 warning, 0 suppressed, 0 checks skipped
```

When a warning doesn't apply, suppress its code in the policy file with the reason. The report shows suppressed warnings with their reasons. In a policy set, roles inherit suppressions and can extend them under `add` like any other key. Checking SQL ignores [`lint_suppress`](policy-reference.md#lint_suppress).

```yaml
lint_suppress:
  max-rows-high: Monthly exports need 50000 rows; reviewed by the data team.
```

The command returns 0 when it runs, even with warnings, 1 with `--strict` when there are warnings, and 2 for an unreadable or invalid policy. Codes are stable within a major version.
