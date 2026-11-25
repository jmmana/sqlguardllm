<p align="center">
  <img alt="sqlguardllm" src="https://raw.githubusercontent.com/jmmana/sqlguardllm/main/docs/assets/logo-light.svg" width="340">
</p>

# sqlguardllm — the SQL firewall for LLM apps

Your model writes SQL. **sqlguardllm** makes sure it can only read what it's allowed to read: one read-only statement, allowlisted tables and columns, row-level filters per user, and a row limit, in any dialect [sqlglot](https://github.com/tobymao/sqlglot) understands.

```bash
pip install sqlguardllm
```

```python
from sqlguardllm import Guard, Policy

guard = Guard(
    Policy(
        dialect="postgres",
        tables={"invoices", "branches", "customers"},
        hidden_columns={"customers.email"},
        row_filters={"invoices": "branch_id IN ({user.branches})"},
        max_rows=1000,
    )
)

result = guard.check(sql_from_llm, context={"user": {"branches": [1, 2]}})
if result.ok:
    rows = db.execute(result.sql)  # read-only, row-filtered, limited
else:
    print(result.messages)  # ["The query tries to change data or structure (DELETE)"]
```

## What it guarantees

- **One statement** that is a query. Nothing stacked after a `;`.
- **Read-only across the whole syntax tree**: no `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `SELECT … INTO`, DDL, `COPY`, `ATTACH`, `PRAGMA`, `EXEC` or row locks, at any depth. A `DELETE` hidden in a CTE is caught.
- **Allowlisted tables and hidden columns**, CTE-aware; `SELECT *`, whole-row references and predicates on hidden columns are rejected.
- **Denied functions** per dialect: file readers, sleeps, shells, network calls and functions that run SQL from a string (`pg_read_file`, `query_to_xml`, `LOAD_FILE`, `OPENROWSET`, `DBMS_PIPE.*`, DuckDB `read_csv`…).
- **Row-level filters**: every occurrence of a table becomes a filtered derived table, with the user's values bound as escaped literals.
- **Row limit** in the dialect's own syntax (`LIMIT`, `TOP`, `FETCH FIRST`).
- **Fails closed**: what can't be understood is rejected with a reason.

Pure Python, one dependency (sqlglot), no network, no LLM. Status: **alpha** — the API may still change before 1.0; violation codes are stable.

## Learn more

- Source, design and attack gallery: <https://github.com/jmmana/sqlguardllm>
- Threat model: <https://github.com/jmmana/sqlguardllm/blob/main/docs/THREAT_MODEL.md>
- Report a bypass privately: <https://github.com/jmmana/sqlguardllm/security/advisories/new>

Licensed under Apache-2.0.
