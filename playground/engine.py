import json
import sqlite3

from sqlguardllm import Guard, Policy

_db = sqlite3.connect(":memory:")
_db.executescript("""
CREATE TABLE branches (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, email TEXT);
CREATE TABLE invoices (id INTEGER PRIMARY KEY, branch_id INTEGER, customer_id INTEGER, total INTEGER);
CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, password_hash TEXT);
INSERT INTO branches VALUES (1,'North'),(2,'South');
INSERT INTO customers VALUES (1,'Acme','ceo@acme.example'),(2,'Globex','hi@globex.example'),(3,'Initech','bill@initech.example');
INSERT INTO invoices VALUES (1,1,1,250),(2,2,2,999),(3,1,3,120),(4,2,1,430),(5,1,2,75),(6,2,3,310);
INSERT INTO users VALUES (1,'root','x9f8a7');
""")
_db.execute("PRAGMA query_only = ON")


def run(policy_json, sql, ctx_json, lang):
    try:
        guard = Guard(Policy.from_dict(json.loads(policy_json)), language=lang)
    except Exception as e:  # invalid JSON or invalid policy
        return json.dumps({"error": f"{type(e).__name__}: {e}"})
    r = guard.check(sql, json.loads(ctx_json))
    out = {
        "ok": r.ok,
        "sql": r.sql,
        "applied_filters": list(r.applied_filters),
        "violations": [
            {"code": v.code.value, "message": v.message, "detail": v.detail}
            for v in r.violations
        ],
    }
    if r.ok:
        try:
            cur = _db.execute(r.sql)
            out["columns"] = [d[0] for d in cur.description or []]
            out["rows"] = [list(x) for x in cur.fetchmany(200)]
        except Exception as e:
            out["exec_error"] = str(e)
    return json.dumps(out, default=str)
