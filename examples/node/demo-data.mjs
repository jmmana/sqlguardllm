// A tiny sales database shared by the Node recipes in this folder (same data as examples/demo_data.py).
//
// The signed-in user manages branches 1 and 2, so they may see invoices 10 and 11 (350.0 in total)
// but never invoice 12, and never a customer's email.
import { DatabaseSync } from "node:sqlite";

export const POLICY = {
  dialect: "sqlite",
  tables: ["branches", "customers", "invoices"],
  hidden_columns: ["customers.email"],
  // {user.branches} comes from the signed-in user, never from the model.
  row_filters: { invoices: "branch_id IN ({user.branches})" },
  max_rows: 100,
};

// What the application knows about the signed-in user.
export const CONTEXT = { user: { branches: [1, 2] } };

// SQL a model might write: one fine query and two it must never run.
export const ALLOWED_SQL = "SELECT SUM(total) AS revenue FROM invoices";
export const HIDDEN_COLUMN_SQL = "SELECT name, email FROM customers";
export const WRITE_SQL = "DELETE FROM invoices";

export const SCRIPT = `
CREATE TABLE branches (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL);
CREATE TABLE invoices (
    id INTEGER PRIMARY KEY,
    branch_id INTEGER NOT NULL REFERENCES branches (id),
    customer_id INTEGER NOT NULL REFERENCES customers (id),
    total REAL NOT NULL
);
INSERT INTO branches VALUES (1, 'North'), (2, 'South'), (3, 'East');
INSERT INTO customers VALUES (1, 'Ana', 'ana@example.com'), (2, 'Luis', 'luis@example.com');
INSERT INTO invoices VALUES (10, 1, 1, 100.0), (11, 2, 2, 250.0), (12, 3, 1, 900.0);
`;

/** An in-memory demo database. Needs Node 22.5+ (`node:sqlite`). */
export function createDemoDatabase() {
  const db = new DatabaseSync(":memory:");
  db.exec(SCRIPT);
  return db;
}

/** The `run(sql)` the SQLite recipes pass to the guarded tools. */
export function sqliteRunner(db) {
  return (sql) => db.prepare(sql).all().map((row) => ({ ...row }));
}
