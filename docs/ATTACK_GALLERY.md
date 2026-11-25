# Attack gallery

Every row is a test case of the adversarial corpus in [`tests/corpus/`](../tests/corpus/), which has 64 cases in 0.1.0a1 and runs on every pull request. The column **sqlglot sees** was verified with **sqlglot 30.35.0** on 2026-11-25; the column **sqlguard** is what 0.1.0a1 does, checked by that corpus.

Policy used in the examples: tables `invoices`, `branches`, `customers`; hidden column `customers.email`; row filter on `invoices`.

## Writes and statement shape

| # | Dialect | Attempt | sqlglot sees | sqlguard |
|---|---|---|---|---|
| 1 | postgres | `DELETE FROM invoices` | root `Delete` | ❌ `not_a_query` |
| 2 | tsql | `SELECT 1; DROP TABLE invoices` | 2 statements: `Select`, `Drop` | ❌ `multiple_statements` |
| 3 | postgres | `sElEcT * fRoM invoices; dElEtE fRoM invoices` | 2 statements: `Select`, `Delete` | ❌ `multiple_statements` |
| 4 | tsql | `SELECT * INTO backup FROM invoices` | root `Select` with an `Into` node | ❌ `write_operation` |
| 5 | postgres | `WITH d AS (DELETE FROM invoices RETURNING *) SELECT * FROM d` | root `Select` with a `Delete` in the CTE | ❌ `write_operation` |
| 6 | postgres | `COPY invoices TO '/tmp/x.csv'` | root `Copy` | ❌ `not_a_query` |
| 7 | mysql | `SELECT * FROM invoices INTO OUTFILE '/tmp/x.csv'` | parse error | ❌ `parse_error` |
| 8 | sqlite | `ATTACH DATABASE '/tmp/x.db' AS x` | root `Attach` | ❌ `not_a_query` |
| 9 | sqlite | `PRAGMA writable_schema = 1` | root `Pragma` | ❌ `not_a_query` |
| 10 | postgres | `LOCK TABLE invoices` | falls back to `Command` | ❌ `not_a_query` |
| 11 | postgres | `SET ROLE admin` | falls back to `Command` | ❌ `not_a_query` |
| 12 | postgres | `CALL refresh_totals()` | falls back to `Command` | ❌ `not_a_query` |
| 13 | tsql | `EXEC xp_cmdshell 'dir'` | root `Execute` | ❌ `not_a_query` |
| 14 | tsql | `WAITFOR DELAY '00:00:10'` | parse error | ❌ `parse_error` |
| 15 | postgres | `SELECT * FROM invoices FOR UPDATE` | root `Select` with a `Lock` | ❌ `row_lock` |

## Functions

| # | Dialect | Attempt | sqlglot sees | sqlguard |
|---|---|---|---|---|
| 16 | postgres | `SELECT pg_sleep(10)` | anonymous function `pg_sleep` | ❌ `denied_function` |
| 17 | postgres | `SELECT pg_read_file('/etc/passwd')` | anonymous function `pg_read_file` | ❌ `denied_function` |
| 18 | mysql | `SELECT LOAD_FILE('/etc/passwd')` | anonymous function `LOAD_FILE` | ❌ `denied_function` |
| 19 | tsql | `SELECT * FROM OPENROWSET('SQLNCLI', 'Server=x;', 'SELECT 1')` | anonymous function `OPENROWSET` | ❌ `table_not_allowed` |
| 20 | oracle | `SELECT DBMS_PIPE.RECEIVE_MESSAGE('a', 10) FROM dual` | `Dot` + anonymous `RECEIVE_MESSAGE`: the guard must rebuild the qualified name | ❌ `denied_function` |

## Tables and columns

| # | Dialect | Attempt | sqlglot sees | sqlguard |
|---|---|---|---|---|
| 21 | postgres | `SELECT * FROM invoices UNION ALL SELECT * FROM (SELECT * FROM users) u` | root `Union`; table `users` | ❌ `table_not_allowed` |
| 22 | postgres | `SELECT name, email FROM customers` | column `email` of `customers` | ❌ `hidden_column` |
| 23 | postgres | `SELECT * FROM customers` | star over a table with hidden columns | ❌ `hidden_column` |
| 24 | postgres | `SELECT name FROM customers WHERE email LIKE '%@acme.com'` | hidden column in a predicate | ❌ `hidden_column` |

## Things that must keep working

False positives matter too: a guard that blocks legitimate queries gets switched off.

| # | Dialect | Query | sqlglot sees | sqlguard |
|---|---|---|---|---|
| 25 | postgres | `SELECT 'x; DROP TABLE invoices' AS note` | one `Select`, a string literal | ✅ allowed |
| 26 | postgres | `SELECT 1 /* ; DROP TABLE invoices */` | one `Select`, a comment | ✅ allowed |
| 27 | postgres | `WITH invoices AS (SELECT 1 AS id) SELECT * FROM invoices` | CTE that shadows the table | ✅ allowed, no row filter injected inside the CTE reference |
| 28 | tsql | `SELECT SUM(total) FROM dbo.invoices` | one `Select` | ✅ rewritten with the row filter and `TOP n` |

## Add a case

Cases live as YAML in `tests/corpus/`, one file per category:

```yaml
- id: pg-cte-delete
  dialect: postgres
  sql: WITH d AS (DELETE FROM invoices RETURNING *) SELECT * FROM d
  expect: reject
  codes: [write_operation]
  note: A write hidden in a CTE, invisible to keyword filters.
```

Adding cases is one of the best first contributions: see [CONTRIBUTING.md](../CONTRIBUTING.md). If you find a case that sqlguard **accepts but shouldn't**, that's a vulnerability: report it privately per [SECURITY.md](../SECURITY.md).
