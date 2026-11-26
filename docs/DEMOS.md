# README demonstrations

The README GIFs and videos are generated from actual `Guard.check()` calls
captured by [examples/readme_demo.py](../examples/readme_demo.py). All data is
fictional. The visuals are scripted explanations of those results, not a
recording of a browser product interface.

| Asset | English | Español |
|---|---|---|
| Video, 42 s, 1280 × 720, silent with visible captions | [MP4](assets/demo.en.mp4) | [MP4](assets/demo.es.mp4) |
| GIF, 21 s, first three scenes | [GIF](assets/demo.en.gif) | [GIF](assets/demo.es.gif) |
| Poster | [PNG](assets/demo-poster.en.png) | [PNG](assets/demo-poster.es.png) |
| Optional subtitle track | [WebVTT](assets/demo.en.vtt) | [WebVTT](assets/demo.es.vtt) |
| Machine-readable executions | [JSON](assets/demo-trace.en.json) | [JSON](assets/demo-trace.es.json) |

GitHub displays the GIF inline. The MP4 is linked as a repository asset and
can be opened or downloaded from its file page.

## What the scenes demonstrate

1. An allowed read receives a tenant filter and a row limit.
2. A PostgreSQL write hidden in a CTE is rejected with `write_operation`.
3. Reading `customers.email` is rejected with `hidden_column`.
4. A cross-tenant predicate with `OR 1 = 1` remains inside the injected filter.
5. A harmless string containing `DROP TABLE` remains allowed.
6. Missing authenticated context is rejected with `missing_context`.

The policy validates the PostgreSQL dialect. Only the admitted portable reads
execute on a synthetic SQLite database, which has read-only mode enabled.
Denied statements never reach that executor. This demo is not a PostgreSQL
server test; the [research study](../research/README.md) separately reports
SQLite, DuckDB and PostgreSQL/WebAssembly execution evidence.

## Capture the actual results

From the repository root in the normal development environment:

```bash
uv run python examples/readme_demo.py --language en \
  --out docs/assets/demo-trace.en.json
uv run python examples/readme_demo.py --language es \
  --out docs/assets/demo-trace.es.json
```

The example checks that both invoices belong to tenant 7 and that all three
fixture rows remain intact. Its JSON includes execution status and rows in
addition to the library's decision fields; `CheckResult` itself does not query
a database or contain a `rows` field.

## Render videos and GIFs

Use the isolated research environment described in [research/README.md](../research/README.md),
plus ffmpeg with H.264 support and DejaVu Sans/Mono fonts. The renderer's font
directory is declared near the top of its source for systems with another path.

```bash
.venv-research/bin/python research/render_demos.py
```

It reads the captured JSON, formats SQL for display, renders both language
versions, and writes MP4, GIF, poster, subtitle and contact-sheet files.
The animation changes presentation timing only; SQL, decisions, error messages
and returned rows originate in the captured executions.

## Scientific coverage

The library uses SQLGlot for multi-dialect parsing and generation. The current
study executes SQLite and DuckDB natively, and PostgreSQL in PGlite's WASM
runtime. The development corpus covers ten parser-dialect labels. Those are
different kinds of evidence; neither means every SQLGlot dialect is certified
against its corresponding database server.
