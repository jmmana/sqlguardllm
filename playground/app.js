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


// What the rules panel edits. The policy JSON stays the single source of truth.
const ROW_FILTERS = DEFAULT_POLICY.row_filters;
const EMAIL = "customers.email", PWD = "users.password_hash";
const MASKS = ["partial", "redact", "null", "hash"];
const REF = "https://jmmana.github.io/sqlguardllm/policy-reference/#";
const RULES = "https://jmmana.github.io/sqlguardllm/DESIGN/#";

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
    h_rules: "Policy rules",
    rules_intro: "Plain-language switches for the policy JSON below. Changing one rewrites the JSON and checks the last query again; editing the JSON moves the switches.",
    r_email: "Email column (customers.email)",
    r_email_visible: "Visible: the model can read real addresses.",
    r_email_hidden: "Hidden: any query that uses it is rejected (hidden_columns).",
    r_email_masked: "Masked: the query runs, but the value comes back masked (masked_columns).",
    r_mask: "Mask", m_partial: "partial (ce***)", m_redact: "redact (***)", m_null: "null (NULL)", m_hash: "hash: not on SQLite",
    r_rows: "Rows",
    r_filter: "Row filter per branch: each manager sees only their own branch in invoices and branches (row_filters). Off: everyone sees every branch, so the role no longer matters.",
    r_max: "Maximum rows per query (max_rows)",
    r_max_help: "Adds or lowers the LIMIT; it never rejects a query. Empty: no limit.",
    r_tables: "Tables (tables, the allowlist)",
    r_users: "Allow the users table. Off: any query on users is rejected.",
    r_pwd: "Keep users.password_hash hidden (recommended).",
    rulesErr: "The JSON below is not valid, so these switches are paused until you fix it.",
    roleOff: "The row filter is off: both roles see every branch.",
    h_help: "What do these rules mean?",
    help_intro: "Each field links to its entry in the policy reference. Always on, whatever the policy: exactly one statement, read-only at every depth, no row locks, and the dialect's function denylist unless you replace it.",
    help_more: "Every field, with its type: the policy reference",
    whyTitle: "Why it was rejected, and how to allow it",
    k_rule: "Rule: ", k_why: "Why: ", k_how: "How to allow it: ", k_docs: "Docs: ",
    maskedNote: "Masked by the policy: %cols%. This is what the model would see.",
    fx_visible: "Make the email visible", fx_mask: "Mask the email", fx_users: "Allow the users table", fx_nofilter: "Turn the row filter off",
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
    h_rules: "Reglas de la política",
    rules_intro: "Interruptores en lenguaje simple para el JSON de la política de abajo. Al cambiar uno se reescribe el JSON y se vuelve a revisar la última consulta; si editas el JSON, los interruptores se actualizan.",
    r_email: "Columna email (customers.email)",
    r_email_visible: "Visible: el modelo puede leer los correos reales.",
    r_email_hidden: "Oculta: se rechaza cualquier consulta que la use (hidden_columns).",
    r_email_masked: "Enmascarada: la consulta se ejecuta, pero el valor vuelve enmascarado (masked_columns).",
    r_mask: "Máscara", m_partial: "partial (ce***)", m_redact: "redact (***)", m_null: "null (NULL)", m_hash: "hash: no en SQLite",
    r_rows: "Filas",
    r_filter: "Filtro por sucursal: cada gerente ve solo su sucursal en invoices y branches (row_filters). Apagado: todos ven todas las sucursales, así que el rol deja de importar.",
    r_max: "Máximo de filas por consulta (max_rows)",
    r_max_help: "Agrega o baja el LIMIT; nunca rechaza una consulta. Vacío: sin límite.",
    r_tables: "Tablas (tables, la lista blanca)",
    r_users: "Permitir la tabla users. Apagado: se rechaza cualquier consulta sobre users.",
    r_pwd: "Mantener oculta users.password_hash (recomendado).",
    rulesErr: "El JSON de abajo no es válido; estos interruptores quedan en pausa hasta que lo corrijas.",
    roleOff: "El filtro por sucursal está apagado: los dos roles ven todas las sucursales.",
    h_help: "¿Qué significan estas reglas?",
    help_intro: "Cada campo enlaza a su entrada en la referencia de políticas (en inglés). Siempre activo, sea cual sea la política: una sola sentencia, solo lectura a cualquier profundidad, sin bloqueos de filas y la lista de funciones prohibidas del dialecto, salvo que la reemplaces.",
    help_more: "Todos los campos, con su tipo: la referencia de políticas",
    whyTitle: "Por qué se rechazó y cómo permitirlo",
    k_rule: "Regla: ", k_why: "Por qué: ", k_how: "Cómo permitirlo: ", k_docs: "Documentación: ",
    maskedNote: "Enmascarado por la política: %cols%. Esto es lo que vería el modelo.",
    fx_visible: "Hacer visible el email", fx_mask: "Enmascarar el email", fx_users: "Permitir la tabla users", fx_nofilter: "Apagar el filtro por sucursal",
  },
};

// One explanation per violation code the engine can emit (src/sqlguardllm/result.py), plus
// variants for the demo's own rules. The engine's message is shown as is; this adds the why
// and the how. %dialect% and %hidden% are filled in from the policy that was checked.
const W = {
  en: {
    parse_error: ["The SQL has to parse in the policy's dialect (dialect: %dialect%).", "What the guard can't understand it can't check, so it says no. Some attacks are syntax only one database accepts, like MySQL's INTO OUTFILE.", "Fix the SQL, or set dialect in the JSON to the database the SQL was written for (the presets do this for you)."],
    empty_statement: ["There has to be one SQL statement.", "Empty input, or only comments, has nothing to check.", "Write a SELECT query."],
    multiple_statements: ["Only one statement per query. This rule is always on.", "Stacking a second statement after a ; is the classic way to slip a DROP or DELETE behind an innocent read.", "It can't be switched off: send one query at a time."],
    not_a_query: ["Only queries (SELECT, UNION, WITH … SELECT) are allowed. Always on.", "Commands like PRAGMA, ATTACH, SET, CALL or EXEC change the database or the session, or run code.", "It can't be switched off: the guard only lets reads through. Ask for the data with a SELECT."],
    write_operation: ["Nothing in the query may write, at any depth. Always on.", "A write can hide inside a CTE (WITH d AS (DELETE …)) or behind SELECT … INTO, so the guard checks every node, not the first word.", "It can't be switched off. In a real app, also use a read-only database user."],
    row_lock: ["Row locks (FOR UPDATE, FOR SHARE) are not allowed. Always on.", "A lock taken by a model's query can block your application's real writes.", "It can't be switched off: remove the locking clause."],
    table_not_allowed: ["tables lists the only tables the model may read, and this source is not one of them.", "The model should only reach what the app needs. Unknown tables, and table functions that read files or other servers (OPENROWSET, read_csv, dblink), are refused.", "If it is a real table your app needs, add it to tables in the JSON. Table functions can't be allowed this way."],
    table_users: ["users is not in tables, the allowlist (“Allow the users table” is off).", "users holds password hashes, and a text-to-SQL feature rarely needs it.", "Turn on “Allow the users table” in Policy rules. users.password_hash stays hidden unless you also untick “Keep users.password_hash hidden”."],
    hidden_email: ["hidden_columns lists customers.email (“Email column: Hidden” in Policy rules).", "A hidden column can't appear anywhere: not in the select list, not in WHERE (email LIKE 'a%' would leak it letter by letter), not through SELECT *.", "Set “Email column” to “Visible” if the model may read addresses, or to “Masked”: the query runs and the value comes back masked (ce***)."],
    hidden_masked: ["customers.email is masked (masked_columns), and a masked column may only appear in the select list.", "Filtering, joining, grouping or sorting by a masked column would reveal the real value: WHERE email = 'ceo@acme.example' confirms an address.", "Remove it from WHERE, JOIN, GROUP BY and ORDER BY, or set “Email column” to “Visible”."],
    hidden_star_masked: ["SELECT * over a table with masked columns needs the table's columns listed in schema.", "Without the column list the guard can't put the mask inside *, so it refuses rather than return the real value.", "Name the columns (SELECT id, name, email FROM customers), or add \"schema\": {\"customers\": [\"id\", \"name\", \"email\"]} to the JSON."],
    hidden_pwd: ["hidden_columns lists users.password_hash (“Keep users.password_hash hidden”).", "Password hashes can be cracked offline, and the model never needs them. SELECT * over users is rejected for the same reason.", "Name the columns you need (SELECT id, name FROM users). Unticking the switch works, but don't do that in a real app."],
    hidden_column: ["hidden_columns: a hidden column can't be used anywhere in the query. Hidden in this policy: %hidden%.", "* or t.* over a table with hidden columns is rejected too, so the model has to name the columns it wants.", "Name only the columns you need, or remove the column from hidden_columns (for the email, use “Email column” in Policy rules)."],
    denied_function: ["The function is on the denylist for %dialect% (denied_functions, extra_denied_functions).", "These functions read files, call the network, sleep, run shell commands or change server state: one call can leak or damage data.", "On purpose, there is no switch for this. denied_functions in the JSON replaces the built-in list; only do that if you know the function is harmless."],
    missing_context: ["A row filter (row_filters) needs a value the context doesn't have.", "Without the value the filter can't be applied, and running the query unfiltered would show every row, so nothing runs.", "Here the context comes from the role and only has {user.branches}: use that in the filter, or turn off “Row filter per branch”."],
    invalid_context: ["A context value used by a row filter has a type that can't be bound safely.", "Values are bound as escaped literals (numbers, text, dates, or lists of them); anything else could change what the filter means.", "In your app, pass plain values from the signed-in user. The playground's roles always send valid ones."],
    too_complex: ["A complexity limit fired (max_joins, max_subquery_depth or forbid_cartesian).", "Queries far bigger than the question can exhaust the database: a join with no condition multiplies rows.", "Simplify the query, or raise or remove that limit in the JSON."],
    missing_required_filter: ["required_filters: every read of this table must filter on one of the listed columns.", "It prevents full scans of big tables, like reading every invoice with no date range.", "Add a WHERE on one of those columns, or remove the table from required_filters in the JSON."],
    aggregate_required: ["aggregate_only: this table can only be read through aggregates, over groups of at least k rows.", "Single rows (one person's salary) never come back, only totals over groups too big to single anyone out.", "Use COUNT, SUM or AVG with GROUP BY, or remove the table from aggregate_only in the JSON."],
    unknown: ["", "", "See the policy reference."],
  },
  es: {
    parse_error: ["El SQL tiene que poder analizarse en el dialecto de la política (dialect: %dialect%).", "Lo que el guardia no entiende no lo puede revisar, así que dice que no. Algunos ataques son sintaxis que solo acepta una base de datos, como INTO OUTFILE de MySQL.", "Corrige el SQL, o cambia dialect en el JSON a la base de datos para la que se escribió (los ejemplos lo hacen por ti)."],
    empty_statement: ["Tiene que haber una sentencia SQL.", "Una entrada vacía, o solo comentarios, no tiene nada que revisar.", "Escribe una consulta SELECT."],
    multiple_statements: ["Solo una sentencia por consulta. Esta regla siempre está activa.", "Encadenar una segunda sentencia después de un ; es la forma clásica de colar un DROP o un DELETE detrás de una lectura inocente.", "No se puede desactivar: envía una consulta a la vez."],
    not_a_query: ["Solo se permiten consultas (SELECT, UNION, WITH … SELECT). Siempre activa.", "Comandos como PRAGMA, ATTACH, SET, CALL o EXEC cambian la base de datos o la sesión, o ejecutan código.", "No se puede desactivar: el guardia solo deja pasar lecturas. Pide los datos con un SELECT."],
    write_operation: ["Nada en la consulta puede escribir, a ninguna profundidad. Siempre activa.", "Una escritura puede esconderse dentro de un CTE (WITH d AS (DELETE …)) o detrás de SELECT … INTO, así que el guardia revisa cada nodo, no la primera palabra.", "No se puede desactivar. En una app real, usa además un usuario de base de datos de solo lectura."],
    row_lock: ["No se permiten bloqueos de filas (FOR UPDATE, FOR SHARE). Siempre activa.", "Un bloqueo tomado por una consulta del modelo puede frenar las escrituras reales de tu aplicación.", "No se puede desactivar: quita la cláusula de bloqueo."],
    table_not_allowed: ["tables lista las únicas tablas que el modelo puede leer, y esta fuente no es una de ellas.", "El modelo solo debe llegar a lo que la app necesita. Se rechazan las tablas desconocidas y las funciones de tabla que leen archivos u otros servidores (OPENROWSET, read_csv, dblink).", "Si es una tabla real que tu app necesita, agrégala a tables en el JSON. Las funciones de tabla no se pueden permitir así."],
    table_users: ["users no está en tables, la lista blanca («Permitir la tabla users» está apagado).", "users guarda hashes de contraseñas, y una función de texto a SQL rara vez la necesita.", "Activa «Permitir la tabla users» en Reglas de la política. users.password_hash sigue oculta salvo que también desmarques «Mantener oculta users.password_hash»."],
    hidden_email: ["hidden_columns incluye customers.email («Columna email: Oculta» en Reglas de la política).", "Una columna oculta no puede aparecer en ningún lado: ni en el SELECT, ni en el WHERE (email LIKE 'a%' la filtraría letra por letra), ni con SELECT *.", "Cambia «Columna email» a «Visible» si el modelo puede leer correos, o a «Enmascarada»: la consulta se ejecuta y el valor vuelve enmascarado (ce***)."],
    hidden_masked: ["customers.email está enmascarada (masked_columns), y una columna enmascarada solo puede aparecer en la lista del SELECT.", "Filtrar, unir, agrupar u ordenar por una columna enmascarada revelaría el valor real: WHERE email = 'ceo@acme.example' confirma un correo.", "Quítala del WHERE, JOIN, GROUP BY y ORDER BY, o cambia «Columna email» a «Visible»."],
    hidden_star_masked: ["SELECT * sobre una tabla con columnas enmascaradas necesita las columnas de la tabla en schema.", "Sin la lista de columnas el guardia no puede poner la máscara dentro del *, así que lo rechaza en vez de devolver el valor real.", "Nombra las columnas (SELECT id, name, email FROM customers), o agrega \"schema\": {\"customers\": [\"id\", \"name\", \"email\"]} al JSON."],
    hidden_pwd: ["hidden_columns incluye users.password_hash («Mantener oculta users.password_hash»).", "Los hashes de contraseñas se pueden romper sin conexión, y el modelo nunca los necesita. SELECT * sobre users se rechaza por lo mismo.", "Nombra las columnas que necesitas (SELECT id, name FROM users). Desmarcar el interruptor funciona, pero no lo hagas en una app real."],
    hidden_column: ["hidden_columns: una columna oculta no se puede usar en ninguna parte de la consulta. Ocultas en esta política: %hidden%.", "También se rechaza * o t.* sobre una tabla con columnas ocultas, así que el modelo tiene que nombrar las columnas que quiere.", "Nombra solo las columnas que necesitas, o quita la columna de hidden_columns (para el email, usa «Columna email» en Reglas de la política)."],
    denied_function: ["La función está en la lista de funciones prohibidas de %dialect% (denied_functions, extra_denied_functions).", "Estas funciones leen archivos, llaman a la red, esperan, ejecutan comandos o cambian el estado del servidor: una sola llamada puede filtrar o dañar datos.", "A propósito, no hay interruptor para esto. denied_functions en el JSON reemplaza la lista incluida; hazlo solo si sabes que la función es inofensiva."],
    missing_context: ["Un filtro por fila (row_filters) necesita un valor que el contexto no tiene.", "Sin el valor no se puede aplicar el filtro, y ejecutar la consulta sin filtrar mostraría todas las filas, así que no se ejecuta nada.", "Aquí el contexto viene del rol y solo tiene {user.branches}: usa eso en el filtro, o apaga «Filtro por sucursal»."],
    invalid_context: ["Un valor del contexto que usa un filtro por fila tiene un tipo que no se puede insertar con seguridad.", "Los valores se insertan como literales escapados (números, texto, fechas o listas de ellos); cualquier otra cosa podría cambiar lo que significa el filtro.", "En tu app, pasa valores simples del usuario con sesión iniciada. Los roles del playground siempre envían valores válidos."],
    too_complex: ["Saltó un límite de complejidad (max_joins, max_subquery_depth o forbid_cartesian).", "Consultas mucho más grandes que la pregunta pueden agotar la base de datos: un join sin condición multiplica las filas.", "Simplifica la consulta, o sube o quita ese límite en el JSON."],
    missing_required_filter: ["required_filters: toda lectura de esta tabla tiene que filtrar por una de las columnas indicadas.", "Evita recorrer completas las tablas grandes, como leer todas las facturas sin un rango de fechas.", "Agrega un WHERE sobre una de esas columnas, o quita la tabla de required_filters en el JSON."],
    aggregate_required: ["aggregate_only: esta tabla solo se puede leer con agregados, en grupos de al menos k filas.", "Nunca vuelven filas sueltas (el sueldo de una persona), solo totales de grupos demasiado grandes para señalar a alguien.", "Usa COUNT, SUM o AVG con GROUP BY, o quita la tabla de aggregate_only en el JSON."],
    unknown: ["", "", "Consulta la referencia de políticas."],
  },
};

// "What do these rules mean?": [fields, English, Spanish]
const HELP = [
  [["dialect"], "The SQL dialect the model writes: sqlite here, or postgres, tsql, mysql… The presets change it. SQL that doesn't parse in this dialect is rejected (parse_error).",
    "El dialecto SQL que escribe el modelo: aquí sqlite, o postgres, tsql, mysql… Los ejemplos lo cambian. Si el SQL no se puede analizar en este dialecto, se rechaza (parse_error)."],
  [["tables"], "The allowlist: the only tables the model may read. Any other table, or a table function that reads files or other servers, is rejected (table_not_allowed). null turns the allowlist off, which is not recommended.",
    "La lista blanca: las únicas tablas que el modelo puede leer. Cualquier otra tabla, o una función de tabla que lee archivos u otros servidores, se rechaza (table_not_allowed). null apaga la lista blanca, y no es recomendable."],
  [["hidden_columns"], "Columns the model may never use, anywhere: select list, WHERE, ORDER BY or SELECT *. A query that mentions one is rejected (hidden_column). That is why SELECT name, email FROM customers is REJECTED with the default policy.",
    "Columnas que el modelo nunca puede usar, en ningún lado: SELECT, WHERE, ORDER BY o SELECT *. Una consulta que mencione una se rechaza (hidden_column). Por eso SELECT name, email FROM customers sale RECHAZADA con la política por defecto."],
  [["masked_columns"], "Columns the model may select, but the value comes back masked: partial (ce***), redact (***), null, or hash (SHA-256, not on SQLite). Using them in WHERE, JOIN, GROUP BY or ORDER BY is still rejected, because that would reveal the real value.",
    "Columnas que el modelo puede seleccionar, pero el valor vuelve enmascarado: partial (ce***), redact (***), null o hash (SHA-256, no en SQLite). Usarlas en WHERE, JOIN, GROUP BY u ORDER BY se sigue rechazando, porque revelaría el valor real."],
  [["row_filters"], "A condition added to every read of a table, with values from the signed-in user: here {user.branches} comes from the role. The model can't remove it; WHERE … OR 1 = 1 stays inside the filtered rows.",
    "Una condición que se agrega a cada lectura de una tabla, con valores del usuario con sesión iniciada: aquí {user.branches} viene del rol. El modelo no puede quitarla; un WHERE … OR 1 = 1 queda dentro de las filas filtradas."],
  [["max_rows"], "The most rows one query may return. A LIMIT is added or lowered; it never rejects a query. null means no limit.",
    "El máximo de filas que puede devolver una consulta. Se agrega o se baja un LIMIT; nunca rechaza una consulta. null significa sin límite."],
  [["schema"], "The columns of each table. Only needed so that SELECT * over a table with masked columns can be expanded with the masks.",
    "Las columnas de cada tabla. Solo hace falta para que SELECT * sobre una tabla con columnas enmascaradas se pueda expandir con las máscaras."],
  [["denied_functions", "extra_denied_functions", "function_policy", "allowed_functions"], "Functions that read files, sleep, call the network or run commands are denied per dialect (denied_function). extra_denied_functions adds to the list, denied_functions replaces it, and function_policy \"allowlist\" with allowed_functions allows only the ones you name.",
    "Las funciones que leen archivos, esperan, llaman a la red o ejecutan comandos están prohibidas por dialecto (denied_function). extra_denied_functions agrega a la lista, denied_functions la reemplaza, y function_policy \"allowlist\" con allowed_functions permite solo las que nombres."],
  [["max_joins", "max_subquery_depth", "forbid_cartesian"], "Optional limits for expensive queries: how many joins, how deep subqueries nest, and whether joins without a linking condition are allowed (too_complex).",
    "Límites opcionales para consultas caras: cuántos joins, qué tan anidadas las subconsultas y si se permiten joins sin una condición que los una (too_complex)."],
  [["required_filters"], "Every read of a table must filter on one of the listed columns, for example a date range on a big table (missing_required_filter).",
    "Toda lectura de una tabla tiene que filtrar por una de las columnas indicadas, por ejemplo un rango de fechas en una tabla grande (missing_required_filter)."],
  [["aggregate_only"], "The table can only be read through aggregates over groups of at least k rows, so no single person's row comes back (aggregate_required).",
    "La tabla solo se puede leer con agregados en grupos de al menos k filas, así que nunca vuelve la fila de una sola persona (aggregate_required)."],
  [["output_dialect", "default_schema"], "output_dialect writes the rewritten SQL for another database; default_schema is the schema assumed for table names that have none.",
    "output_dialect genera el SQL reescrito para otra base de datos; default_schema es el esquema que se supone para las tablas que no lo indican."],
];

const $ = (id) => document.getElementById(id);
let lang = (navigator.language || "en").toLowerCase().startsWith("es") ? "es" : "en";
let py = null, runPy = null, wantRun = false, ran = false;
const t = (k) => T[lang][k];

const b64e = (o) => { const b = new TextEncoder().encode(JSON.stringify(o)); let s = ""; b.forEach((c) => (s += String.fromCharCode(c))); return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); };
const b64d = (s) => { s = s.replace(/-/g, "+").replace(/_/g, "/"); const bin = atob(s + "=".repeat((4 - (s.length % 4)) % 4)); return JSON.parse(new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)))); };
const pretty = (p) => JSON.stringify(p, null, 2);

function el(tag, text, cls) { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; }
function link(href, text) { const a = el("a", text); a.href = href; a.target = "_blank"; a.rel = "noopener"; return a; }

// ---- Policy helpers (names compared case-insensitively, like unquoted SQL identifiers) ----
const lc = (s) => String(s).toLowerCase();
const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const has = (arr, x) => Array.isArray(arr) && arr.some((a) => lc(a) === x);
const without = (arr, x) => (Array.isArray(arr) ? arr.filter((a) => lc(a) !== x) : []);
const maskKey = (p) => (isObj(p.masked_columns) ? Object.keys(p.masked_columns).find((k) => lc(k) === EMAIL) : undefined);
const emailState = (p) => (has(p.hidden_columns, EMAIL) ? "hidden" : maskKey(p) ? "masked" : "visible");
const rowFilterOn = (p) => isObj(p.row_filters) && Object.keys(p.row_filters).some((k) => k in ROW_FILTERS || lc(k) in ROW_FILTERS);
const usersAllowed = (p) => p.tables == null || has(p.tables, "users");

function readPolicy() {
  try { const p = JSON.parse($("policy").value); return isObj(p) ? p : null; } catch { return null; }
}

function setEmail(p, state, strategy) {
  p.hidden_columns = without(p.hidden_columns, EMAIL);
  if (isObj(p.masked_columns)) {
    for (const k of Object.keys(p.masked_columns)) if (lc(k) === EMAIL) delete p.masked_columns[k];
    if (!Object.keys(p.masked_columns).length) delete p.masked_columns;
  }
  if (state === "hidden") p.hidden_columns.push(EMAIL);
  if (state === "masked") p.masked_columns = { ...(p.masked_columns || {}), [EMAIL]: strategy || "partial" };
}
function setRowFilter(p, on) {
  const rf = isObj(p.row_filters) ? { ...p.row_filters } : {};
  for (const k of Object.keys(rf)) if (lc(k) in ROW_FILTERS) delete rf[k];
  if (on) Object.assign(rf, ROW_FILTERS);
  p.row_filters = rf;
}
function setUsers(p, on) {
  // null (no allowlist) becomes the demo's allowlist, so the switch always means something.
  p.tables = without(Array.isArray(p.tables) ? p.tables : DEFAULT_POLICY.tables, "users");
  p.hidden_columns = without(p.hidden_columns, PWD);
  if (on) { p.tables.push("users"); p.hidden_columns.push(PWD); }
}
function setPwdHidden(p, on) {
  p.hidden_columns = without(p.hidden_columns, PWD);
  if (on) p.hidden_columns.push(PWD);
}

// Apply a change from the rules panel to the JSON, then check the last query again.
function editPolicy(fn) {
  const p = readPolicy();
  if (!p) return;
  fn(p);
  $("policy").value = pretty(p);
  syncRules();
  if (runPy && ran) run();
}

// JSON -> switches.
function syncRules() {
  const p = readPolicy();
  const box = $("rules");
  box.querySelectorAll("input,select").forEach((i) => (i.disabled = !p));
  $("rulesErr").hidden = !!p;
  $("rulesErr").textContent = t("rulesErr");
  if (!p) { $("roleNote").hidden = true; return; }
  const st = emailState(p);
  box.querySelectorAll('input[name="email"]').forEach((r) => (r.checked = r.value === st));
  const k = maskKey(p);
  $("maskStrategy").value = k && MASKS.includes(p.masked_columns[k]) ? p.masked_columns[k] : "partial";
  $("maskStrategy").disabled = st !== "masked";
  const filterOn = rowFilterOn(p);
  $("rowFilter").checked = filterOn;
  if (document.activeElement !== $("maxRows")) $("maxRows").value = p.max_rows == null ? "" : String(p.max_rows);
  const users = usersAllowed(p);
  $("allowUsers").checked = users;
  $("hidePwd").checked = has(p.hidden_columns, PWD);
  $("hidePwd").disabled = !users;
  $("roleNote").hidden = filterOn;
  $("roleNote").textContent = t("roleOff");
}

function renderHelp() {
  const box = $("help"); box.textContent = "";
  box.append(el("p", t("help_intro")));
  const dl = el("dl");
  for (const [fields, en, es] of HELP) {
    const dt = el("dt");
    fields.forEach((f, i) => { if (i) dt.append(", "); const a = link(REF + f, null); a.append(el("code", f)); dt.append(a); });
    dl.append(dt, el("dd", lang === "es" ? es : en));
  }
  box.append(dl);
  const more = el("p"); more.append(link(REF.slice(0, -1), t("help_more"))); box.append(more);
}

function applyLang() {
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i]").forEach((n) => (n.textContent = t(n.dataset.i)));
  $("lang").textContent = lang === "en" ? "ES" : "EN";
  $("role").options[0].textContent = t("roleN"); $("role").options[1].textContent = t("roleS");
  for (const o of $("maskStrategy").options) o.textContent = t("m_" + o.value);
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
  renderHelp();
  syncRules();
  setStatus(py ? t("ready") : t("loading"), !py);
}
function ctxShow() { $("ctxShow").textContent = t("ctx") + JSON.stringify({ user: ROLES[$("role").value] }); }
function setStatus(msg, busy) { $("statusText").textContent = msg; $("prog").hidden = !busy; }

function preset(dialect, sql) {
  try { const p = JSON.parse($("policy").value); p.dialect = dialect; $("policy").value = pretty(p); } catch { $("policy").value = pretty({ ...DEFAULT_POLICY, dialect }); }
  syncRules();
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

// Why a violation fired and how to allow it, for the policy that was checked.
function explain(v, p) {
  const d = lc(v.detail || ""), email = emailState(p), fixes = [];
  const emailFixes = () => fixes.push(["fx_visible", (q) => setEmail(q, "visible")], ["fx_mask", (q) => setEmail(q, "masked", "partial")]);
  let key = v.code, field = null, rules = null;
  switch (v.code) {
    case "parse_error": field = "dialect"; break;
    case "empty_statement": case "multiple_statements": case "not_a_query": rules = ["2-statement-shape", "2. Statement shape"]; break;
    case "write_operation": case "row_lock": rules = ["3-read-only-walk", "3. Read-only walk"]; break;
    case "table_not_allowed":
      field = "tables";
      if (d === "users" || d.endsWith(".users")) { key = "table_users"; fixes.push(["fx_users", (q) => setUsers(q, true)]); }
      break;
    case "hidden_column":
      field = "hidden_columns";
      if (d.includes("schema")) { key = "hidden_star_masked"; field = "schema"; }
      else if (d.includes("password_hash") || /\busers\b/.test(d)) key = "hidden_pwd";
      else if (email === "masked" && /customers|email/.test(d)) { key = "hidden_masked"; field = "masked_columns"; fixes.push(["fx_visible", (q) => setEmail(q, "visible")]); }
      else if (email === "hidden" && /customers|email/.test(d)) { key = "hidden_email"; emailFixes(); }
      else if (email === "hidden") emailFixes();
      break;
    case "denied_function": field = "denied_functions"; break;
    case "missing_context": case "invalid_context":
      field = "row_filters";
      if (v.code === "missing_context" && rowFilterOn(p)) fixes.push(["fx_nofilter", (q) => setRowFilter(q, false)]);
      break;
    case "too_complex": field = ["max_subquery_depth", "forbid_cartesian"].find((f) => d.startsWith(f)) || "max_joins"; break;
    case "missing_required_filter": field = "required_filters"; break;
    case "aggregate_required": field = "aggregate_only"; break;
    default: field = null;
  }
  const hidden = Array.isArray(p.hidden_columns) && p.hidden_columns.length ? p.hidden_columns.join(", ") : "-";
  const fill = (s) => s.replace("%dialect%", p.dialect || "?").replace("%hidden%", hidden);
  const [rule, why, how] = (W[lang][key] || W[lang].unknown).map(fill);
  const href = field ? [REF + field, field] : rules ? [RULES + rules[0], rules[1]] : [REF.slice(0, -1), "policy reference"];
  return { rule, why, how, href, fixes };
}

function renderWhy(out, violations, p) {
  if (!p) return;
  out.append(el("h3", t("whyTitle")));
  const ul = el("ul", null, "why");
  for (const v of violations) {
    const x = explain(v, p), li = el("li");
    li.dataset.code = v.code;
    li.append(el("code", v.code));
    for (const [k, text] of [["k_rule", x.rule], ["k_why", x.why], ["k_how", x.how]]) {
      if (!text) continue;
      const para = el("p"); para.append(el("b", t(k)), document.createTextNode(text)); li.append(para);
    }
    const docs = el("p"); docs.append(el("b", t("k_docs")), link(x.href[0], x.href[1])); li.append(docs);
    if (x.fixes.length) {
      const row = el("div", null, "row");
      for (const [label, fn] of x.fixes) {
        const b = el("button", t(label)); b.type = "button"; b.dataset.fix = label;
        b.addEventListener("click", () => editPolicy(fn));
        row.append(b);
      }
      li.append(row);
    }
    ul.append(li);
  }
  out.append(ul);
}

function run() {
  if (!runPy) { wantRun = true; return; }
  ran = true;
  const t0 = performance.now();
  const raw = runPy($("policy").value, $("sql").value, JSON.stringify({ user: ROLES[$("role").value] }), lang);
  const r = JSON.parse(raw); const ms = Math.round(performance.now() - t0);
  const p = readPolicy();
  const out = $("out"); out.textContent = "";
  if (r.error) { delete out.dataset.ok; out.append(el("p", t("polErr") + r.error, "err")); return; }
  out.dataset.ok = r.ok;
  out.append(el("span", r.ok ? t("allowed") : t("rejected"), "verdict " + (r.ok ? "ok" : "bad")), el("small", `  ${t("ms")} ${ms} ms`));
  if (r.violations.length) {
    out.append(el("h3", t("violations")));
    const ul = el("ul");
    for (const v of r.violations) { const li = el("li"); li.append(el("code", v.code), document.createTextNode(": " + v.message)); ul.append(li); }
    out.append(ul);
    renderWhy(out, r.violations, p);
  }
  if (r.ok) {
    out.append(el("h3", t("rewritten")), el("pre", r.sql));
    if (r.applied_filters.length) out.append(el("p", t("filters") + ": " + r.applied_filters.join("; ")));
    if (r.exec_error) out.append(el("p", t("execErr") + r.exec_error, "err"));
    else {
      out.append(el("h3", t("rows")));
      const masked = p && isObj(p.masked_columns)
        ? Object.entries(p.masked_columns).filter(([k]) => r.columns.includes(k.split(".").pop())).map(([k, s]) => `${k} (${s})`) : [];
      if (masked.length) out.append(el("p", t("maskedNote").replace("%cols%", masked.join(", ")), "note"));
      if (!r.rows.length) out.append(el("p", t("norows")));
      else {
        const tb = el("table"), hd = el("tr"); r.columns.forEach((c) => hd.append(el("th", c))); tb.append(hd);
        r.rows.forEach((row) => { const tr = el("tr"); row.forEach((c) => tr.append(el("td", c === null ? "NULL" : String(c)))); tb.append(tr); });
        const w = el("div", null, "tw"); w.append(tb); out.append(w);
      }
    }
  }
}

function shareLink() {
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
$("reset").addEventListener("click", () => { $("policy").value = pretty(DEFAULT_POLICY); syncRules(); if (runPy && ran) run(); });
$("policy").addEventListener("input", syncRules);
document.querySelectorAll('input[name="email"]').forEach((r) =>
  r.addEventListener("change", () => editPolicy((p) => setEmail(p, r.value, $("maskStrategy").value))));
$("maskStrategy").addEventListener("change", () => editPolicy((p) => setEmail(p, "masked", $("maskStrategy").value)));
$("rowFilter").addEventListener("change", (e) => editPolicy((p) => setRowFilter(p, e.target.checked)));
$("maxRows").addEventListener("input", () => {
  const v = $("maxRows").value.trim(), n = Number(v);
  if (v === "") editPolicy((p) => (p.max_rows = null));
  else if (Number.isInteger(n) && n >= 1) editPolicy((p) => (p.max_rows = n));
});
$("allowUsers").addEventListener("change", (e) => editPolicy((p) => setUsers(p, e.target.checked)));
$("hidePwd").addEventListener("change", (e) => editPolicy((p) => setPwdHidden(p, e.target.checked)));
$("sql").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) run(); });
$("share").addEventListener("click", async () => {
  const u = shareLink();
  try { await navigator.clipboard.writeText(u); $("shareMsg").textContent = t("copied"); } catch { $("shareMsg").textContent = t("copyFail"); }
});
if (shared) wantRun = true;
// Lazy-load the engine after first paint.
requestAnimationFrame(() => setTimeout(loadEngine, 0));
