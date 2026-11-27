# Revisión del artículo de sqlguardllm

El manuscrito revisado está en `paper/main.pdf`. Es un borrador para revisión
del autor; no se ha enviado a arXiv ni tiene un DOI de Zenodo asignado.
Incluye el nombre completo, correo de contacto y el ORCID facilitado por el
autor: https://orcid.org/0009-0002-5751-0709 . La afiliación institucional se
ha omitido por indicación del autor.

La contribución defendible es una evaluación reproducible que separa admitir
SQL de devolver el resultado autorizado. El oráculo ejecuta la consulta en una
base físicamente restringida, sin usar el rewriting del guard.

La revisión añade:

- PostgreSQL nativo en Docker, además de SQLite, DuckDB y PGlite.
- El baseline de añadir filtros únicamente al WHERE externo: falla en 14 de
  96 observaciones por runtime; el guard completo obtiene 72 coincidencias y
  24 rechazos correctos en las 48 plantillas y dos contextos.
- 5.000 consultas generadas por runtime, con 23 familias y 3.434 SQL distintos.
  No se observan violaciones de la política medida, pero **233 consultas válidas
  se rechazan y 194 fallan al ejecutarse después del rewriting**, por runtime.
- Casos de nombres calificados, alias, catálogos, funciones de archivo,
  ordinales, joins, CTEs recursivas, vistas y stars. Las funciones peligrosas se
  prueban solo como admisión y nunca se ejecutan, tampoco bajo mutaciones.
- Siete mutaciones dirigidas: las plantillas originales detectan seis; las
  plantillas y los casos de revisión juntos detectan las siete. La gramática
  generada detecta cuatro, lo que también demuestra sus límites.
- Medición aleatorizada, afinidad de CPU, parseo SQLGlot y bootstrap por
  plantilla; planes nativos sobre un millón de filas sintéticas.
- Arquitectura con zonas de confianza, ejemplo de rewriting a dos columnas,
  tabla de capacidades, barras apiladas con conteos, heatmap y ECDF/AST.
- Literatura Truman/Non-Truman, Oracle VPD, Oso, Cerbos y Supabase RLS.

La generación sigue siendo obra de los autores y contiene casos problemáticos
ya conocidos al preparar la evaluación. No elimina el sesgo ni permite afirmar
seguridad general. La cota estadística se limita expresamente a la distribución
del generador; no se suman runtimes para inflar el tamaño independiente.

El piloto real con Claude está preparado con 20 preguntas de Spider y dos
variantes por pregunta. Su workflow es manual y exclusivo del propietario, usa
el secreto `ANTHROPIC_API_TOKEN` solo durante la inferencia y limita el gasto
conforme al máximo autorizado de US$10. Aún no tiene resultados de inferencia;
el artículo no inventa cifras de accuracy ni éxito de prompt injection.

## Fuzzing con datos canario y red team con un LLM

Dos búsquedas adversarias que no dependen de las plantillas escritas por el autor.
Todas las cifras salen de archivos guardados con `research/make_adversarial.py`.

- **Fuzzing con canarios** (`tests/fuzz/`, `results/fuzz.json`): 20.000 ejemplos,
  semilla 20261127, SQLite y DuckDB, 0 fallos de invariantes. El oráculo mira solo
  las filas devueltas, en dos mundos que cambian todos los secretos. Con las
  correcciones de dos bypasses históricos revertidas, el oráculo los vuelve a
  encontrar. Hallazgos previos: bypass de mayúsculas en nombres de columna en MySQL
  (0.1.0a1 a 0.5.0a2, corregido en 0.5.0a3), `check()` que lanzaba excepciones en
  lugar de rechazar, y DuckDB 2.0.1 que ignora algunos `LIMIT` (límite del motor).
- **Red team con LLM, ejecución 1** (27/11/2026, `research/redteam/2026-11-27/`):
  46 consultas conservadas, 25 rechazadas, 20 permitidas sin fuga y 1 bypass
  (`SELECT COLUMNS('.*') FROM customers` en DuckDB, corregido en 0.5.0a4), por
  US$0,31. El modelo solicitado fue `claude-opus-5-5`, pero respondió el fallback del
  servidor (`claude-opus-4-8`, registrado en cada consulta). Re-evaluar el dataset sin
  la API (`python research/redteam/run.py --rescore research/redteam/2026-11-27`)
  no muestra bypass y cambia un único veredicto.
- **Límites**: un solo modelo atacante, una ejecución y un prompt; los canarios no
  detectan canales de tiempo, fugas de existencia ni nada que no esté en las filas;
  solo se juzga la política de fuzzing (`aggregate_only` aún no tiene oráculo);
  el fuzzing reportado cubre SQLite y DuckDB. No hay revisión de terceros.
  El dataset y `fuzz.json` forman parte del artefacto (hashes en
  `artifact-sha256.json`).

La API de la librería y los vídeos/GIF existentes continúan documentando casos
permitidos, lecturas restringidas y escrituras rechazadas con salidas reales.
