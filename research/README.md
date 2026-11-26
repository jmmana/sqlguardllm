# sqlguardllm systems study

This directory contains a manuscript for **author review**, its reproducible
execution study, and the scripts behind the README demonstrations. It has not
been submitted to arXiv. No arXiv identifier or peer-review status is claimed.

- [Paper (PDF)](paper/main.pdf) · [LaTeX source](paper/main.tex) · [References](paper/references.bib)
- [Raw results and metadata](results/) · [Query templates and fixtures](workload.py)
- [Submission notes](submission.md) · [Demonstrations and regeneration](../docs/DEMOS.md)
- [LaTeX upload package](sqlguardllm-arxiv-source.zip)
- [Resumen en español para el autor](RESUMEN.es.md)

## What was measured

48 authored query templates (24 benign, 12 tenant-isolation stress, 12
forbidden reads) run in two tenant contexts on SQLite 3.53.1, DuckDB 2.0.1,
and PostgreSQL 19.0 compiled to WebAssembly via PGlite 0.6.0. PostgreSQL/WASM
is **not a native PostgreSQL server test**. No SQL Server, MySQL, Oracle or
warehouse server is included in the execution experiment.

An independent reference database per tenant physically contains only that
tenant's rows, omits the hidden column, and omits the forbidden table. Allowed
query results are compared as bags with multiplicity and an eight-row cap.
Queries that can exceed the cap use explicit ordering. Error outcomes remain
errors, rather than being counted as successful protection.

The 1,248 observations include four diagnostic configurations on three
runtimes plus native PostgreSQL RLS/column grants in PGlite. These are
correlated observations over a small, study-authored workload, **not 1,248
independent attacks**. The workload was designed with knowledge of the library.
It is separate from the development corpus, not an external held-out benchmark.

| Full-policy outcome | Observed count |
|---|---:|
| Authorized-query result matches | 216 |
| Forbidden reads correctly rejected | 72 |
| Result-policy mismatches | 0 |
| Allowed queries rejected / execution errors | 0 / 0 |

Native RLS plus column grants has the same outcome on its 96 PGlite
observations. This study makes no claim of superiority over native RLS,
security for arbitrary SQL, or production query-performance overhead.

The separate development regression run passes 148 cases across ten parser
dialect labels. Parser tests are not execution certification of those engines.

## Reproduce the study

Run from the repository root with Python 3.12 and Node.js 24. The measured run
used Python 3.12.14. Use a separate research environment: installing database
drivers into the normal development environment can change optional-dependency
assumptions in existing tests.

```bash
uv venv .venv-research
uv pip install --python .venv-research/bin/python -r research/requirements.txt
npm ci --prefix research --ignore-scripts
.venv-research/bin/python research/evaluate.py
.venv-research/bin/python research/make_figures.py
.venv-research/bin/python research/verify_artifacts.py
```

No credentials, Docker, networked database or model API calls are needed.
Installation downloads the pinned dependencies; the evaluation itself is local.
Dependency versions are pinned in `requirements.txt` and `package-lock.json`;
`results/metadata.json` records measured engine versions and source fingerprints.

For a quick SQLite-only run that preserves the published measurements:

```bash
.venv-research/bin/python research/evaluate.py \
  --engines sqlite --rounds 3 --warmup 1 --out /tmp/sqlguard-study-smoke
```

The default run uses five warm-ups and 50 timed checks per case, context,
runtime and Python configuration. Policies are compiled once. Timing excludes
database execution, compilation, startup, network and LLM generation. Method
order is fixed and the host is shared, so these are descriptive measurements.

## Inspect the evidence

| File | Contents |
|---|---|
| `results/metadata.json` | Versions, timestamp, evaluated base commit, source/workload hashes, timing scope |
| `results/workload.json` | All 48 versioned templates and declared outcomes for tenant 1 |
| `results/execution.json` | Every decision, generated SQL, returned rows or error, and outcome |
| `results/latency.json` | All timed validation samples in nanoseconds and per-case quantiles |
| `results/regression.json` | Development-corpus decisions, violation codes and checks |
| `results/summary.json` | Counts and pooled warm-validation quantiles |
| `results/fuzz.json` | One run of the property-based fuzzer (`tests/fuzz/`): seed, examples, and per engine the queries allowed, rejected by violation code, executed, engine errors and invariant failures. Not covered by `verify_artifacts.py`; reproduce with the `replay` command it records |

Tenant 2 queries are instantiated from the same public templates by
`workload.cases(2)`. No unreported model-generated attack set is used.

The keyword and read-only-shape configurations intentionally omit access
control. They illustrate failure modes. `no_row_filters` is an ablation of
the same library, and native RLS is the established enforcement comparator.

## Build the paper

With a working TeX Live installation:

```bash
cd research/paper
latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex
```

The document embeds its bibliography for a self-contained arXiv source build;
`references.bib` is also provided for reference managers. Figures, measurement
macros and result tables are generated from the checked-in raw results.
`main.pdf` is a review artifact, not an accepted or submitted publication.

## Regenerate the media

See [docs/DEMOS.md](../docs/DEMOS.md). The six-scene, 42-second English and
Spanish videos are captioned visual explanations of captured executions. GIFs
show the first three scenes in 21 seconds. They are not browser recordings.

## Limitations and next evaluations

External workloads, independent adversarial review, larger and more varied
schemas, views/UDFs, ambiguous columns, native PostgreSQL and other server
engines, and end-to-end database overhead remain unmeasured here. Masking,
aggregate privacy, inference attacks and cost control require separate studies.
Zero observed failures on this workload does not establish absence of bypasses.
