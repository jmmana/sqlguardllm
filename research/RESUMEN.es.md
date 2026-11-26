# Revisión del artículo de sqlguardllm

El manuscrito revisado está en `paper/main.pdf`. Es un borrador para revisión
del autor; no se ha enviado a arXiv ni tiene un DOI de Zenodo asignado.
Incluye el nombre completo, correo de contacto, afiliación WarlockCode S.A.S.
y el ORCID facilitado por el autor: https://orcid.org/0009-0002-5751-0709 .
La afiliación puede ajustarse antes de la publicación externa.

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

La API de la librería y los vídeos/GIF existentes continúan documentando casos
permitidos, lecturas restringidas y escrituras rechazadas con salidas reales.
