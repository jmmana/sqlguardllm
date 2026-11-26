# Quickstart

[Leer en español](es/quickstart.md)

```bash
pip install sqlguardllm
```

```python
from sqlguardllm import Guard, Policy

policy = Policy(
    dialect="postgres",
    tables=["orders", "customers"],
    hidden_columns=["customers.ssn"],
    row_filters={"orders": "tenant_id = {context.tenant_id}"},
    max_rows=100,
)
guard = Guard(policy)

result = guard.check("SELECT id, total FROM orders", context={"tenant_id": 7})
print(result.allowed)  # True
print(result.sql)      # rewritten: row filter and LIMIT applied

blocked = guard.check("DELETE FROM orders")
print(blocked.allowed, [v.code for v in blocked.violations])
```

## Lint a policy

Before deploying, check for valid policies with common safety gaps:

```bash
sqlguardllm lint --policy policy.yaml
sqlguardllm lint --policy policy.yaml --format json
sqlguardllm lint --policy policy.yaml --strict  # exit 1 if warnings remain
```

Warnings include tenant-like columns without row filters, sensitive columns that are neither
hidden nor masked, an unset or unusually high `max_rows` (over 10,000), `aggregate_only` values
below `k: 5`, and functions restored from the built-in denylist. Each warning includes a fix.
Without a policy `schema` (`table: [columns]`), column-based checks are reported as skipped.
Warnings do not fail by default. To document an accepted warning, suppress it by code and give
the reason in the policy:

```yaml
lint_suppressions:
  max_rows_weak: "The trusted export service requires larger result sets."
```

Next: the [rules](DESIGN.md), the [policy reference](policy-reference.md) and the [API reference](api.md).
