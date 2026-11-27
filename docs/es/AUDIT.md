# Log de auditoría

*English: [AUDIT.md](../AUDIT.md)*

El guard puede registrar **cada decisión**: quién intentó qué y por qué se permitió o se rechazó. Está **desactivado por defecto**. Sin configuración no se construye, escribe ni mide nada, y ni el comportamiento ni el rendimiento cambian.

La auditoría es configuración del despliegue, no política: vive en `Guard`, la CLI, el servidor HTTP y el servidor MCP. No es una clave de los archivos de política.

## Activarlo

```python
from sqlguardllm import AuditLog, Guard

guard = Guard(policy, audit=AuditLog("audit.jsonl"))   # JSON Lines, se añade al final
guard = Guard(policy, audit=AuditLog("-"))             # JSON Lines por stderr
guard = Guard(policy, audit=AuditLog(logging.getLogger("miapp.audit")))
guard = Guard(policy, audit=lambda event: enviar(event)) # cualquier función que reciba un AuditEvent
guard = Guard(policy, audit=AuditLog.otel())           # pip install "sqlguardllm[otel]"
```

Con un conjunto de políticas, `policies.guard("analyst", audit=...)` añade el rol a cada evento.

Desde la línea de comandos:

```bash
sqlguardllm check --policy policy.yaml --audit-log audit.jsonl "SELECT …"
sqlguardllm serve --policy policy.yaml --audit-log audit.jsonl --audit-sql redacted
sqlguardllm mcp   --policy policy.yaml --db sqlite:///demo.db --audit-log -
```

`--audit-log RUTA|-` escribe en un archivo o, con `-`, en stderr (stdout lleva el resultado o el protocolo MCP). Sin `--audit-log` no se registra nada, diga lo que diga `--audit-sql`.

Los eventos de OpenTelemetry se añaden al span actual como `sqlguardllm.decision`; fuera de un span activo, OpenTelemetry los descarta.

## El evento

| Campo | Significado |
|---|---|
| `timestamp` | hora UTC de la decisión (ISO 8601) |
| `event_id` | id único del evento |
| `version` | versión de sqlguardllm |
| `policy_fingerprint` | sha256 de la política canónica: qué política decidió |
| `role` | rol del conjunto de políticas, o `null` |
| `allowed` | `true` o `false` |
| `violation_codes` | [códigos de violación](../api.md) estables; vacío si se permitió |
| `tables` | tablas que lee la consulta (hasta donde llegó el guard, si se rechazó) |
| `sql_fingerprint` | sha256 del SQL normalizado (literales cambiados por `?`): igual para toda consulta con la misma forma |
| `duration_ms` | lo que tardó la decisión |
| `context_keys` | **nombres** de las claves del contexto, como `user.branches`; los valores nunca se registran |
| `sql` | el SQL, solo según `include_sql` |

## SQL en el evento: `include_sql`

| Valor | Registra |
|---|---|
| `"redacted"` (por defecto con la auditoría activa, `--audit-sql redacted`) | la consulta con cada literal cambiado por `?` y sin comentarios. Si el SQL no se puede interpretar no se puede censurar, así que `sql` se omite |
| `"none"` | nada de SQL, solo la huella |
| `"raw"` | el SQL tal como llegó. **Puede contener datos personales** (correos, ids, nombres escritos por el modelo o el usuario). Actívalo solo si puedes guardarlos |

Los valores del contexto no se registran en ningún modo. Tampoco los filtros de fila inyectados en la consulta.

## Cuando falla el destino

Un destino que falla nunca convierte un rechazo en un permiso.

- Por defecto: el fallo se avisa **una vez** por stderr (sin SQL) y la decisión se mantiene.
- `AuditLog(..., strict=True)`: si la decisión no se puede registrar, una consulta permitida se **rechaza** con el código `audit_failure`. Úsalo cuando una consulta sin registrar sea inaceptable.

## Notas

- Los eventos se escriben cuando decide `check()` (y por tanto `enforce()`, `wrap()`, `/v1/check` del servidor y la herramienta `query` de MCP). `explain()` y `explain_query` no registran.
- El destino de archivo abre el archivo en modo añadir en cada evento y escribe un hilo a la vez; rótalo con tus herramientas habituales.
- El presupuesto de tiempo de [`benchmarks/`](https://github.com/jmmana/sqlguardllm/tree/main/benchmarks) (mediana bajo 2 ms por consulta) se comprueba con la auditoría activa.
