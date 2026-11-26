# Resumen del artículo para revisión del autor

**Título:** sqlguardllm: Policy-Driven Validation and Rewriting of LLM-Generated SQL.

El artículo describe cómo la librería valida el árbol sintáctico del SQL y
aplica políticas de tablas, columnas, filas y límites. Su pregunta principal
es si la consulta ejecutada devuelve lo mismo que una base que contiene
únicamente los datos autorizados para ese usuario.

## Evidencia obtenida

Se diseñaron 48 plantillas SQL: 24 consultas legítimas, 12 casos de aislamiento
entre empresas y 12 lecturas prohibidas. Se probaron con dos empresas sobre
SQLite y DuckDB nativos y PostgreSQL compilado a WebAssembly mediante PGlite.
También se comparó el guard con filtros de palabras, validación de forma,
una versión sin filtros por fila y las políticas nativas de PostgreSQL.

| Con la política completa | Resultado observado |
|---|---:|
| Consultas permitidas con resultado igual a la referencia autorizada | 216 |
| Lecturas prohibidas rechazadas | 72 |
| Diferencias respecto a la referencia | 0 |
| Consultas permitidas rechazadas / errores de ejecución | 0 / 0 |

Al desactivar los filtros por usuario aparecieron 59 diferencias por cada
96 observaciones en cada entorno. Las políticas nativas de PostgreSQL
coincidieron con el guard en los casos probados. La mediana de validación
fue de aproximadamente 0,50 a 0,54 milisegundos, sin incluir la ejecución
de la base de datos. Hay 1.248 observaciones contando las configuraciones
comparadas; no son 1.248 ataques independientes.

## Qué significan estos resultados

La evidencia muestra la utilidad de aplicar los permisos por fila además
de validar que el SQL sea de solo lectura, dentro de la muestra estudiada.
El artículo no afirma que inventamos el análisis AST ni la seguridad por
reescritura, que superamos a las políticas nativas de la base o que la
librería sea invulnerable.

Las consultas fueron diseñadas para este estudio con conocimiento de la
implementación. Los datos son pequeños y ficticios. Hace falta evaluación
externa para estimar su comportamiento ante ataques no vistos y cargas
reales. El experimento del artículo no ejecutó un servidor PostgreSQL nativo,
SQL Server, MySQL u Oracle; la suite de motores incorporada al repositorio
es una fuente de evidencia separada.

## Archivos para revisar

- [Artículo completo en inglés, 9 páginas](paper/main.pdf).
- [Código y datos de los experimentos](README.md).
- [Paquete LaTeX para arXiv](sqlguardllm-arxiv-source.zip).
- [Instrucciones y metadatos del envío](submission.md).
- [Videos, GIF y sus ejecuciones reales](../docs/DEMOS.md).

El manuscrito identifica a **Juan Castillo**, siguiendo `CITATION.cff`.
No se inventó afiliación, correo ni ORCID. Incluye la declaración de asistencia
con IA. Está preparado para revisión del autor y todavía no se ha enviado
a arXiv ni recibido un identificador o evaluación por pares.
