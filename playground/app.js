// sqlguardllm playground. Everything runs in the browser; nothing is sent anywhere.
const PYODIDE = "0.29.5";
const PYODIDE_JS = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE}/full/pyodide.js`;
const PYODIDE_SRI = "sha256-f4MqNQJAJj2ZRqnDyHf3vNq2w31txl9yzTvlkF3KYt0=";

const DEFAULT_POLICY = {
  dialect: "sqlite",
  tables: ["invoices", "branches", "customers"],
  hidden_columns: ["customers.email"],
  row_filters: { invoices: "branch_id IN ({user.branches})", branches: "id IN ({user.branches})" },
  max_rows: 50,
};
const ROLES = { north: { branches: [1] }, south: { branches: [2] } };

// [id, dialect, sql, expected]  (docs/ATTACK_GALLERY.md)
const G = {
  writes: [
    [1, "postgres", "DELETE FROM invoices", "not_a_query"],
    [2, "tsql", "SELECT 1; DROP TABLE invoices", "multiple_statements"],
    [3, "postgres", "sElEcT * fRoM invoices; dElEtE fRoM invoices", "multiple_statements"],
    [4, "tsql", "SELECT * INTO backup FROM invoices", "write_operation"],
    [5, "postgres", "WITH d AS (DELETE FROM invoices RETURNING *) SELECT * FROM d", "write_operation"],
    [6, "postgres", "COPY invoices TO '/tmp/x.csv'", "not_a_query"],
    [7, "mysql", "SELECT * FROM invoices INTO OUTFILE '/tmp/x.csv'", "parse_error"],
    [8, "sqlite", "ATTACH DATABASE '/tmp/x.db' AS x", "not_a_query"],
    [9, "sqlite", "PRAGMA writable_schema = 1", "not_a_query"],
    [10, "postgres", "LOCK TABLE invoices", "not_a_query"],
    [11, "postgres", "SET ROLE admin", "not_a_query"],
    [12, "postgres", "CALL refresh_totals()", "not_a_query"],
    [13, "tsql", "EXEC xp_cmdshell 'dir'", "not_a_query"],
    [14, "tsql", "WAITFOR DELAY '00:00:10'", "parse_error"],
    [15, "postgres", "SELECT * FROM invoices FOR UPDATE", "row_lock"],
  ],
  functions: [
    [16, "postgres", "SELECT pg_sleep(10)", "denied_function"],
    [17, "postgres", "SELECT pg_read_file('/etc/passwd')", "denied_function"],
    [18, "mysql", "SELECT LOAD_FILE('/etc/passwd')", "denied_function"],
    [19, "tsql", "SELECT * FROM OPENROWSET('SQLNCLI', 'Server=x;', 'SELECT 1')", "table_not_allowed"],
    [20, "oracle", "SELECT DBMS_PIPE.RECEIVE_MESSAGE('a', 10) FROM dual", "denied_function"],
  ],
  tables: [
    [21, "postgres", "SELECT * FROM invoices UNION ALL SELECT * FROM (SELECT * FROM users) u", "table_not_allowed"],
    [22, "postgres", "SELECT name, email FROM customers", "hidden_column"],
    [23, "postgres", "SELECT * FROM customers", "hidden_column"],
    [24, "postgres", "SELECT name FROM customers WHERE email LIKE '%@acme.com'", "hidden_column"],
  ],
  keep: [
    [25, "postgres", "SELECT 'x; DROP TABLE invoices' AS note", "allowed"],
    [26, "postgres", "SELECT 1 /* ; DROP TABLE invoices */", "allowed"],
    [27, "postgres", "WITH invoices AS (SELECT 1 AS id) SELECT * FROM invoices", "allowed"],
    [28, "tsql", "SELECT SUM(total) FROM dbo.invoices", "allowed"],
    [29, "sqlite", "SELECT id, total FROM invoices WHERE branch_id = 2 OR 1 = 1 ORDER BY id", "allowed"],
  ],
};

const T = {
  en: {
    title: "Try to break it",
    intro: "Write SQL the way a misbehaving model would. sqlguardllm checks it and rewrites it with your role's row filter, all inside your browser.",
    h_in: "Query", role: "Role", sql: "SQL", run: "Check and run", share: "Copy share link", copied: "Link copied", copyFail: "Copy failed: the link is in the address bar",
    presets: "Attack presets (docs/ATTACK_GALLERY.md)", h_pol: "Policy (JSON, editable)", reset: "Reset policy",
    demodb: "Demo SQLite tables: invoices(id, branch_id, customer_id, total), branches(id, name), customers(id, name, email), users(id, name, password_hash).",
    h_out: "Result", empty: "Run a query to see the decision.",
    roleN: "Manager North (branches [1])", roleS: "Manager South (branches [2])", ctx: "Context given by the app: ",
    g_writes: "Writes and statement shape", g_functions: "Functions", g_tables: "Tables and columns", g_keep: "Must keep working",
    loading: "Loading Python in your browser (first visit only, then cached)...", ready: "Ready. Runs locally, nothing is uploaded.", failed: "Could not load the engine: ",
    allowed: "ALLOWED", rejected: "REJECTED", rewritten: "Rewritten SQL (what would run)", filters: "Applied filters", rows: "Rows returned", norows: "No rows.", execErr: "Allowed by the guard but not runnable on the SQLite demo (different dialect): ",
    violations: "Violations", polErr: "Invalid policy: ", ms: "Checked in",
    privacy: "No tracking, no analytics. The share link keeps policy, SQL and role in the URL fragment, which browsers never send to a server.",
  },
  es: {
    title: "Intenta romperlo",
    intro: "Escribe SQL como lo haría un modelo que se porta mal. sqlguardllm lo revisa y lo reescribe con el filtro de filas de tu rol, todo dentro de tu navegador.",
    h_in: "Consulta", role: "Rol", sql: "SQL", run: "Revisar y ejecutar", share: "Copiar enlace", copied: "Enlace copiado", copyFail: "No se pudo copiar: el enlace está en la barra de direcciones",
    presets: "Ataques de ejemplo (docs/ATTACK_GALLERY.md)", h_pol: "Política (JSON, editable)", reset: "Restablecer política",
    demodb: "Tablas SQLite de demostración: invoices(id, branch_id, customer_id, total), branches(id, name), customers(id, name, email), users(id, name, password_hash).",
    h_out: "Resultado", empty: "Ejecuta una consulta para ver la decisión.",
    roleN: "Gerente Norte (sucursales [1])", roleS: "Gerente Sur (sucursales [2])", ctx: "Contexto que da la aplicación: ",
    g_writes: "Escrituras y forma de la sentencia", g_functions: "Funciones", g_tables: "Tablas y columnas", g_keep: "Deben seguir funcionando",
    loading: "Cargando Python en tu navegador (solo la primera visita, luego en caché)...", ready: "Listo. Se ejecuta localmente, no se sube nada.", failed: "No se pudo cargar el motor: ",
    allowed: "PERMITIDA", rejected: "RECHAZADA", rewritten: "SQL reescrito (lo que se ejecutaría)", filters: "Filtros aplicados", rows: "Filas devueltas", norows: "Sin filas.", execErr: "Permitida por el guardia pero no ejecutable en la demo SQLite (otro dialecto): ",
    violations: "Violaciones", polErr: "Política inválida: ", ms: "Revisada en",
    privacy: "Sin rastreo ni analítica. El enlace guarda política, SQL y rol en el fragmento de la URL, que los navegadores nunca envían a un servidor.",
  },
};

const $ = (id) => document.getElementById(id);
let lang = (navigator.language || "en").toLowerCase().startsWith("es") ? "es" : "en";
let py = null, runPy = null, wantRun = false;
const t = (k) => T[lang][k];

const b64e = (o) => { const b = new TextEncoder().encode(JSON.stringify(o)); let s = ""; b.forEach((c) => (s += String.fromCharCode(c))); return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); };
const b64d = (s) => { s = s.replace(/-/g, "+").replace(/_/g, "/"); const bin = atob(s + "=".repeat((4 - (s.length % 4)) % 4)); return JSON.parse(new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)))); };
const pretty = (p) => JSON.stringify(p, null, 2);

function el(tag, text, cls) { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; }

function applyLang() {
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i]").forEach((n) => (n.textContent = t(n.dataset.i)));
  $("lang").textContent = lang === "en" ? "ES" : "EN";
  $("role").options[0].textContent = t("roleN"); $("role").options[1].textContent = t("roleS");
  ctxShow();
  const box = $("presets"); box.textContent = "";
  for (const [grp, items] of Object.entries(G)) {
    box.append(el("h3", t("g_" + grp)));
    box.lastChild.style.cssText = "font-size:.9rem;margin:8px 0 4px";
    const d = el("div", null, "presets");
    for (const [n, dia, sql, exp] of items) {
      const b = el("button", `#${n} ${exp}`); b.type = "button"; b.title = `${dia}: ${sql}`;
      b.setAttribute("aria-label", `#${n}: ${sql}`);
      b.addEventListener("click", () => preset(dia, sql));
      d.append(b);
    }
    box.append(d);
  }
  setStatus(py ? t("ready") : t("loading"), !py);
}
function ctxShow() { $("ctxShow").textContent = t("ctx") + JSON.stringify({ user: ROLES[$("role").value] }); }
function setStatus(msg, busy) { $("statusText").textContent = msg; $("prog").hidden = !busy; }

function preset(dialect, sql) {
  try { const p = JSON.parse($("policy").value); p.dialect = dialect; $("policy").value = pretty(p); } catch { $("policy").value = pretty({ ...DEFAULT_POLICY, dialect }); }
  $("sql").value = sql; run();
}

async function loadEngine() {
  try {
    await new Promise((ok, no) => {
      const s = document.createElement("script");
      s.src = PYODIDE_JS; s.integrity = PYODIDE_SRI; s.crossOrigin = "anonymous"; s.onload = ok; s.onerror = () => no(new Error("pyodide.js"));
      document.head.append(s);
    });
    py = await self.loadPyodide({ indexURL: `https://cdn.jsdelivr.net/pyodide/v${PYODIDE}/full/` });
    await py.loadPackage("sqlite3");
    const names = await (await fetch("wheels/manifest.json")).json();
    for (const n of names) py.unpackArchive(await (await fetch("wheels/" + n)).arrayBuffer(), "wheel");
    py.runPython(await (await fetch("engine.py")).text());
    runPy = py.globals.get("run");
    $("run").disabled = false;
    self.__ready = true;
    setStatus(t("ready"), false);
    if (wantRun) run();
  } catch (e) { setStatus(t("failed") + e.message, false); $("prog").hidden = true; }
}

function run() {
  if (!runPy) { wantRun = true; return; }
  const t0 = performance.now();
  const raw = runPy($("policy").value, $("sql").value, JSON.stringify({ user: ROLES[$("role").value] }), lang);
  const r = JSON.parse(raw); const ms = Math.round(performance.now() - t0);
  const out = $("out"); out.textContent = "";
  if (r.error) { out.append(el("p", t("polErr") + r.error, "err")); return; }
  out.dataset.ok = r.ok;
  out.append(el("span", r.ok ? t("allowed") : t("rejected"), "verdict " + (r.ok ? "ok" : "bad")), el("small", `  ${t("ms")} ${ms} ms`));
  if (r.violations.length) {
    out.append(el("h3", t("violations")));
    const ul = el("ul");
    for (const v of r.violations) { const li = el("li"); li.append(el("code", v.code), document.createTextNode(": " + v.message)); ul.append(li); }
    out.append(ul);
  }
  if (r.ok) {
    out.append(el("h3", t("rewritten")), el("pre", r.sql));
    if (r.applied_filters.length) out.append(el("p", t("filters") + ": " + r.applied_filters.join("; ")));
    if (r.exec_error) out.append(el("p", t("execErr") + r.exec_error, "err"));
    else {
      out.append(el("h3", t("rows")));
      if (!r.rows.length) out.append(el("p", t("norows")));
      else {
        const tb = el("table"), hd = el("tr"); r.columns.forEach((c) => hd.append(el("th", c))); tb.append(hd);
        r.rows.forEach((row) => { const tr = el("tr"); row.forEach((c) => tr.append(el("td", c === null ? "NULL" : String(c)))); tb.append(tr); });
        const w = el("div", null, "tw"); w.append(tb); out.append(w);
      }
    }
  }
}

function link() {
  const h = "#" + b64e({ p: $("policy").value, s: $("sql").value, r: $("role").value, l: lang });
  history.replaceState(null, "", h);
  return location.href;
}

function fromHash() {
  if (location.hash.length < 2) return false;
  try {
    const d = b64d(location.hash.slice(1));
    $("policy").value = d.p; $("sql").value = d.s;
    if (ROLES[d.r]) $("role").value = d.r;
    if (d.l === "en" || d.l === "es") lang = d.l;
    return true;
  } catch { return false; }
}

$("policy").value = pretty(DEFAULT_POLICY);
$("sql").value = "SELECT id, total FROM invoices ORDER BY id";
const shared = fromHash();
applyLang();
$("lang").addEventListener("click", () => { lang = lang === "en" ? "es" : "en"; applyLang(); if (runPy && $("out").dataset.ok) run(); });
$("role").addEventListener("change", () => { ctxShow(); });
$("run").addEventListener("click", run);
$("reset").addEventListener("click", () => ($("policy").value = pretty(DEFAULT_POLICY)));
$("sql").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) run(); });
$("share").addEventListener("click", async () => {
  const u = link();
  try { await navigator.clipboard.writeText(u); $("shareMsg").textContent = t("copied"); } catch { $("shareMsg").textContent = t("copyFail"); }
});
if (shared) wantRun = true;
// Lazy-load the engine after first paint.
requestAnimationFrame(() => setTimeout(loadEngine, 0));
