# sqlguardllm execution study

The revised manuscript is for **author review**, not an arXiv submission or an
accepted publication. It includes the original evidence and the expanded
review-driven evaluation, preserving failures as well as successful results.

- [Paper PDF](paper/main.pdf) · [LaTeX](paper/main.tex) · [Source ZIP](sqlguardllm-arxiv-source.zip)
- [Original measurements](results/) · [Expanded local measurements](results-review/) · [Native PostgreSQL server measurements](results-native-service/)
- [Resumen en español](RESUMEN.es.md) · [Submission notes](submission.md)
- [Claude pilot, spending and credential controls](PILOT.md) · [Bilingual demos](../docs/DEMOS.md)

## Measured scope

The core study executes 48 authored templates in two tenant contexts on SQLite
3.53.1, DuckDB 2.0.1, PostgreSQL 19/WASM through PGlite 0.6.0, and native
PostgreSQL 16.2 in a Docker service. Each allowed result is compared with a
physically restricted database containing only the caller's records and public
columns. Bag comparison preserves duplicate rows; capped queries use explicit
ordering. The candidate fetches a ninth row to expose a missing eight-row cap.

Five Python configurations plus RLS/grants on the two PostgreSQL runtimes give
**2,112 core observations**. The full guard has 288 authorized matches and 96
correct blocks across 384 observations. The outermost-WHERE prototype instead
has 14 mismatches per 96 observations, versus 59 without row filters.
Native RLS/grants matches the full guard on the original templates.

The original counts alone missed material utility failures:

| Expanded observation | Count |
|---|---:|
| Explicit review probes across the four core runtimes | 280 |
| Rewritten qualified-column execution errors in those probes | 8 |
| Allowed natural joins rejected in those probes | 8 |
| Generated draws per runtime / distinct SQL strings | 5,000 / 3,434 |
| Generated authorized matches / correct blocks, per runtime | 3,888 / 685 |
| Generated allowed queries rejected / execution errors, per runtime | 233 / 194 |
| Measured generated policy mismatches, per runtime | 0 |
| Selected mutants detected by original templates / combined authored suites | 6 of 7 / 7 of 7 |

The generator is authored, has 23 production families and retains duplicate
draws. It includes issues already found during probe development. It does not
remove author bias. The conditional one-sided bound in the paper concerns only
its fixed with-replacement sampling distribution; runtimes are not pooled to
inflate N. Errors and over-rejections are utility failures.

An additional native single-user consistency run is archived in `results-review`
but excluded from core totals. Single-user native planner observations use one
million synthetic invoices and three indexed query forms; they do not establish
production overhead. The native server workflow also captures this planner
probe. The paper states which captured run supplies its reported timings.

## Reproduce

Use Linux, Python 3.12 and Node.js 24 in a separate research environment.
Research drivers can change optional-dependency assumptions in normal tests.

```bash
uv venv .venv-research
uv pip install --python .venv-research/bin/python -r research/requirements.txt
npm ci --prefix research --ignore-scripts
.venv-research/bin/python research/review_eval.py --out /tmp/sqlguard-review
.venv-research/bin/python research/planner_eval.py --out /tmp/sqlguard-review/planner.json
```

The default semantic run includes local SQLite, DuckDB, PGlite and native
PostgreSQL single-user execution. For native client/server reproduction, start
an isolated PostgreSQL 16.2 service, install `psycopg[binary]==3.2.12` into that
research environment, then use its **synthetic test** DSN:

```bash
export SQLGUARD_RESEARCH_PG_DSN=postgresql://postgres:synthetic-study-only@127.0.0.1:5432/postgres
.venv-research/bin/python research/review_eval.py \
  --engines postgres_service --out /tmp/sqlguard-native
.venv-research/bin/python research/planner_eval.py --postgres-service \
  --out /tmp/sqlguard-native/planner.json
```

The service backend creates and drops isolated study databases and tenant roles;
use a disposable research server. The checked-in workflow
`Research native PostgreSQL` supplies that Docker service and archives results.
Its password protects synthetic CI data only; it is not an application secret.

For a quick smoke run:

```bash
.venv-research/bin/python research/review_eval.py --engines sqlite \
  --generated 50 --mutation-generated 20 --rounds 1 --out /tmp/sqlguard-smoke
```

To rebuild the revised figures and tables from **checked-in** measurements:

```bash
.venv-research/bin/python research/make_review_figures.py
cd research/paper
latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex
cd ../..
.venv-research/bin/python research/verify_artifacts.py
```

The source ZIP includes the ACM class, class source and license. `nonacm` means
that this formatting claims no ACM venue or acceptance. Latin Modern is used
as a local fallback if the class's Libertine font package is unavailable.
`make_figures.py` is retained for the original experiment and writes separate
`original-figures/` diagnostics; it does not overwrite the revised paper.

## Inspect or extend the evidence

- `original.json`: all five configurations, two contexts, 48 templates; PostgreSQL also includes RLS/grants.
- `edges.json`: edge admissions, returned rows and errors. Dangerous probes are admission-only, even under mutations.
- `generated.json.gz`: every generated draw, SQL, context, outcome and result/error.
- `generated-summary.json`: draw/unique counts, outcomes and explicitly conditional bound.
- `mutations.json`, `mutation-detections.json`: survivors and detecting cases, with passing controls.
- `timing.json`: shuffled warm-call samples, CPU affinity, AST size and template-cluster bootstrap results.
- `planner.json`: exact direct/rewritten queries, native JSON plans and paired measurements.
- `metadata.json`: evaluated revision, source fingerprints, versions and timing/generator configuration.
- `results-native-service/ci-provenance.json`: native workflow run, artifact ID and download digest.

Measurements refer to their recorded source revisions. The original `results/`
record and `evaluate.py` remain available; it contains 1,248 observations and a
separate 148-case development regression record over ten parser dialect labels.
That parser corpus is not execution certification of ten database products.

No paid model calls are required to reproduce the semantic, mutation, timing,
planner, fuzzing or red-team re-scoring experiments. The prepared Claude pilot has no results until explicitly
executed; the manuscript makes no LLM accuracy or injection-success claim.
A Zenodo metadata draft is provided, but no DOI has yet been assigned.

## Adversarial evidence: canary fuzzing and LLM red team

Two searches that do not depend on the authored templates (paper section
"Adversarial search"). Every number in the paper is computed from stored files by
`make_adversarial.py`; nothing is typed by hand.

- **Canary fuzzing** (`tests/fuzz/`, statistics in `results/fuzz.json`): 20,000
  examples, seed 20261127, SQLite and DuckDB, 0 invariant failures. Not judged:
  PostgreSQL, MySQL, SQL Server (nightly CI only), `aggregate_only`.
- **LLM red team, run 1** (`redteam/2026-11-27/`): 46 kept queries, USD 0.31, one
  bypass (DuckDB `COLUMNS('.*')`, fixed in 0.5.0a4). Requested model
  `claude-opus-5-5`; the server fallback answered with `claude-opus-4-8`
  (recorded per query). The dataset is part of the artifact and is hashed in
  `artifact-sha256.json`.

```bash
# fuzz statistics (about 3 minutes)
SQLGUARD_FUZZ_PROFILE=deep SQLGUARD_FUZZ_SEED=20261127 SQLGUARD_FUZZ_EXAMPLES=20000 \
SQLGUARD_FUZZ_ENGINES=sqlite,duckdb SQLGUARD_FUZZ_STATS=research/results/fuzz.json \
  uv run pytest tests/fuzz -m fuzz
# re-score the stored red-team dataset with the current guard, no API call
uv run python research/redteam/run.py --rescore research/redteam/2026-11-27
# tables, macros and figure of the paper, then check the artifact hashes
uv run --with matplotlib python research/make_adversarial.py
uv run --with matplotlib python research/verify_artifacts.py
```

The workflows `claude-pilot.yml`, `research.yml` and `redteam.yml` run only on
manual dispatch from `main`; none has a schedule or runs on pull requests.

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

External workloads, third-party adversarial review or other attacker models, a
canary oracle for `aggregate_only`, larger and more varied
schemas, views/UDFs, ambiguous columns, native PostgreSQL and other server
engines, and end-to-end database overhead remain unmeasured here. Masking,
aggregate privacy, inference attacks and cost control require separate studies.
Zero observed failures on this workload does not establish absence of bypasses.
