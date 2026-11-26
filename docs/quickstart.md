# Quickstart

[Leer en español](es/quickstart.md) · [Choose a package](packages.md)

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

Run the [demo cases](../examples/policy-tests.yaml) with the demo policy from the repository root.

```bash
sqlguardllm test examples/policy-tests.yaml --policy examples/policy.yaml
sqlguardllm test examples/policy-tests.yaml --policy examples/policy.yaml --format junit > policy-results.xml
sqlguardllm schema --cases > policy-tests.schema.json
```

Use `role` on every case that tests a policy set. The case can also supply `context` to fill that role's row-filter values. Omit `role` with a single policy. On rejects, `violation` matches a public code. On allows, `sql_contains` matches a literal, case-sensitive substring of the rewritten SQL.

The command returns 0 when all cases pass, 1 when any expectation fails, and 2 for invalid inputs. JUnit reports contain case names and failure reasons. The checks run without a database connection.
