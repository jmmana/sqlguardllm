-- Demo sales database for the MCP walkthrough in docs/MCP.md (SQLite). Build it with:
--   python -c "import sqlite3; sqlite3.connect('demo.db').executescript(open('examples/mcp/demo.sql').read())"
BEGIN;

CREATE TABLE branches (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL
);

CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    tax_id TEXT
);

CREATE TABLE invoices (
    id INTEGER PRIMARY KEY,
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    issued_on TEXT NOT NULL,
    total NUMERIC NOT NULL
);

CREATE TABLE api_keys (
    id INTEGER PRIMARY KEY,
    service TEXT NOT NULL,
    secret TEXT NOT NULL
);

INSERT INTO branches VALUES
    (1, 'Centro', 'Bogotá'),
    (2, 'Norte', 'Medellín'),
    (3, 'Costa', 'Barranquilla');

INSERT INTO customers VALUES
    (1, 'Ana Ruiz', 'ana@example.com', '+57 300 555 0101', '900123456'),
    (2, 'Luis Gómez', 'luis@example.com', '+57 300 555 0102', '900654321'),
    (3, 'Marta Díaz', 'marta@example.com', '+57 300 555 0103', '900111222');

INSERT INTO invoices VALUES
    (1, 1, 1, '2026-11-02', 120.50),
    (2, 1, 2, '2026-11-03', 80.00),
    (3, 1, 3, '2026-11-10', 42.75),
    (4, 2, 1, '2026-11-04', 300.00),
    (5, 2, 2, '2026-11-12', 15.20),
    (6, 3, 3, '2026-11-15', 99.90);

INSERT INTO api_keys VALUES (1, 'payments', 'sk_live_do_not_leak');

COMMIT;
