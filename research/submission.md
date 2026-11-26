# arXiv submission package and author review

The manuscript and source package are prepared for the author's review. Nothing
has been submitted to arXiv, and no acceptance, endorsement or identifier exists
for this work at this point.

## Prepared files

- `paper/main.tex`: complete English manuscript and embedded bibliography.
- `paper/main.pdf`: compiled review copy.
- `paper/measurements.tex`, `paper/results-table.tex`: measured values and table.
- `paper/outcomes.pdf`, `paper/latency.pdf`: generated scientific figures.
- `paper/references.bib`: verified primary-source bibliography.
- `sqlguardllm-arxiv-source.zip`: self-contained LaTeX upload package.
- `results/`: public machine-readable evidence in the accompanying repository.

## Suggested metadata

- **Title:** sqlguardllm: Policy-Driven Validation and Rewriting of LLM-Generated SQL
- **Subtitle:** A Reproducible Systems Study
- **Author:** Juan Castillo, following the repository's `CITATION.cff`.
- **Affiliation/email/ORCID:** not invented; confirm any that the author wants to provide.
- **Primary category candidate:** `cs.DB` (Databases), or `cs.CR` if the author
  elects to foreground the security contribution. Classification is subject to moderation.
- **Comments:** Systems study with open-source software, synthetic workload,
  execution results and reproducibility artifacts.
- **License:** to be selected by the author in the submission form; the code's
  Apache-2.0 license does not automatically choose the manuscript's arXiv license.

The abstract is the abstract in `paper/main.tex`; it states the actual study size,
PostgreSQL/WASM scope, measured outcomes and limitations. Do not claim execution
on 32 database engines, a security proof, or independently sampled attacks.

## Review before the author's submission

Confirm author identity and any affiliation; review the scientific contribution,
citations, reported data, scope and AI-assistance disclosure. The current PDF
explicitly says it is for author review. Once the author has reviewed it, update
that status and rebuild the package. There is no fabricated arXiv identifier in
the repository's citation metadata.

arXiv expects authors to self-submit. A new category can require endorsement,
even for an existing author. Moderation is not peer review, and inclusion is
not guaranteed. The package contains the LaTeX source and all included figures;
upload the source ZIP rather than only a PDF generated from LaTeX.

Primary instructions checked on November 26, 2026:

- https://info.arxiv.org/help/submit/index.html
- https://info.arxiv.org/help/moderation/index.html
- https://info.arxiv.org/help/endorsement.html

## Public provenance

The paper names the evaluated engine commit and the results metadata includes
an engine-source SHA-256. Add the eventual arXiv identifier to the README and
`CITATION.cff` only after an identifier is actually assigned.
