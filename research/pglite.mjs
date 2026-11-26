// JSON-lines worker. PostgreSQL runs as WebAssembly inside this process.
// This is not a native PostgreSQL server or a database network benchmark.
import { PGlite } from "@electric-sql/pglite";
import readline from "node:readline";

const databases = new Map();
const lines = readline.createInterface({ input: process.stdin });
for await (const line of lines) {
  try {
    const request = JSON.parse(line);
    if (request.op === "init") {
      for (const [key, schema] of Object.entries(request.fixtures)) {
        const db = await PGlite.create();
        await db.exec(schema);
        databases.set(key, db);
      }
      const db = databases.get("full");
      for (const tenant of [1, 2]) {
        await db.exec(`CREATE ROLE study_tenant_${tenant} NOLOGIN;
          GRANT SELECT ON invoices, branches TO study_tenant_${tenant};
          GRANT SELECT (id, tenant_id, name) ON customers TO study_tenant_${tenant};`);
      }
      for (const table of ["invoices", "customers", "branches"]) {
        await db.exec(`ALTER TABLE ${table} ENABLE ROW LEVEL SECURITY;`);
        for (const tenant of [1, 2]) {
          await db.exec(`CREATE POLICY tenant_${tenant} ON ${table}
            FOR SELECT TO study_tenant_${tenant} USING (tenant_id = ${tenant});`);
        }
      }
      const result = await db.query("SELECT version() AS version");
      console.log(JSON.stringify({ version: result.rows[0].version }));
    } else if (request.op === "query") {
      const db = databases.get(request.key);
      await db.exec("BEGIN READ ONLY");
      try {
        if (request.role) {
          if (!/^study_tenant_[12]$/.test(request.role)) throw Error("invalid role");
          await db.exec(`SET LOCAL ROLE ${request.role}`);
        }
        const result = await db.query(request.sql, [], { rowMode: "array" });
        console.log(JSON.stringify({ rows: result.rows }, (_, v) => typeof v === "bigint" ? v.toString() : v));
      } finally {
        await db.exec("ROLLBACK");
      }
    } else if (request.op === "close") {
      for (const db of databases.values()) await db.close();
      console.log(JSON.stringify({ closed: true }));
      process.exit(0);
    } else {
      throw Error("unknown operation");
    }
  } catch (error) {
    console.log(JSON.stringify({ error: error.message }));
  }
}
