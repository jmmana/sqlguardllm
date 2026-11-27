# Submission package and remaining external steps

The revised manuscript and LaTeX source package are prepared for the author's
review. This is not an arXiv submission, acceptance or assigned identifier.
ACM `sigconf,nonacm` formatting does not claim an ACM venue or acceptance.

## Prepared

- `paper/main.pdf`: revised English review copy.
- `paper/main.tex`, generated tables/macros, four vector figures and bibliography.
- `paper/acmart.cls` (LPPL) and `paper/ACM-LICENSE.txt` for a self-contained build. The
  documented source (`acmart.dtx`/`.ins`) is not vendored; get it from CTAN if needed.
- `redteam/2026-11-27/` and `results/fuzz.json`: LLM red-team dataset and fuzz statistics,
  hashed in `artifact-sha256.json` and checked by `verify_artifacts.py`.
- `sqlguardllm-arxiv-source.zip`: all required LaTeX sources and figures.
- `results/`, `results-review/`, `results-native-service/`: original evidence,
  generated cases, utility failures, mutations, timings and native Docker observations.
- `.zenodo.json`: draft software-artifact metadata with ORCID and licensing notes.
- `PILOT.md`: bounded manual Claude experiment, credential controls and status.

## Author metadata

- Title: sqlguardllm: Policy-Driven Validation and Rewriting of LLM-Generated SQL.
- Subtitle: An Execution-Oriented Systems Study.
- Author: Juan Manuel Castillo Pinto.
- ORCID: https://orcid.org/0009-0002-5751-0709 .
- Email: jmmana@gmail.com .
- Institutional affiliation: omitted at the author's request.
- Category candidate: cs.DB; final classification is subject to arXiv moderation.
- Keywords: SQL authorization, text-to-SQL, row-level security, query rewriting,
  reproducible evaluation.

The abstract is the one in `paper/main.tex`. Core results include native
PostgreSQL server execution and preserve known utility failures. The prepared
Claude pilot is explicitly unmeasured until it runs; do not turn its protocol
into accuracy or prompt-injection results. No security proof, third-party
review or ten-engine execution claim is made. Adversarial evidence is limited to
canary fuzzing (one run, SQLite and DuckDB, 20,000 examples) and one LLM red-team
run (46 queries, one attacker model, answered through a server-side fallback).

## External publication steps

1. Review author details, scientific claims, licensing and AI-assistance disclosure.
2. Execute the manual Claude pilot if its integration results should appear in
   this version; archive completed/partial outcomes honestly and regenerate text.
3. Deposit an immutable artifact in the author's Zenodo account. Use the metadata
   draft as a starting point; add a reserved/registered DOI only after Zenodo
   actually supplies it, and distinguish draft reservation from publication.
4. Rebuild the PDF/source ZIP if author details, measurements or DOI change.
5. The author selects the manuscript license and submits the source ZIP through
   arXiv. The software's Apache-2.0 license does not automatically select the
   manuscript's arXiv submission license. Category endorsement may be required.
6. Add the actual arXiv identifier to README/CITATION metadata only after assignment.

A successful GitHub check or merged PR does not mean arXiv submission. Moderation
is not peer review and acceptance is not guaranteed. No Zenodo DOI or arXiv ID
has been fabricated in the artifact.

Primary instructions checked November 26, 2026:

- https://info.arxiv.org/help/submit/index.html
- https://info.arxiv.org/help/moderation/index.html
- https://info.arxiv.org/help/endorsement.html
- https://developers.zenodo.org/
