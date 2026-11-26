# Quickstart

[Leer en español](es/quickstart.md) · [Choose a package](packages.md)

This page is for Python. For **Node.js / TypeScript** or **.NET**, follow the step-by-step guides for [Node.js](guide-node.md) and [.NET](guide-dotnet.md): they install the client, start `sqlguardllm serve` and check the same kind of queries. The [Python guide](guide-python.md) does the same with a policy file.

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
